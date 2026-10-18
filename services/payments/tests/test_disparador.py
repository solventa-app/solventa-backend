"""Disparador externo del reintento de cobros (Cloud Tasks, decisión W05-7)."""

from types import SimpleNamespace

import pytest
from fakes import BusEventosFalso, ClienteAclFalso, RepositorioCobrosFalso
from fastapi.testclient import TestClient

from app.application.bus_eventos import construir_evento_cobro_estado_cambiado
from app.application.cliente_acl import ResultadoCobro
from app.application.configuracion import Configuracion
from app.application.disparador import BusConReintento, PlanificadorCloudTasks
from app.application.reintento import ReintentoCobrosPendientes
from app.application.servicio_cobros import ServicioCobros
from app.main import create_app

ORDEN = {
    "id_intento_cobro": "clave-tareas-1",
    "poliza_id": "poliza-tareas",
    "monto": "50.00",
    "moneda": "COP",
    "token_medio_pago": "tok_visa_0001",
}


class SesionFalsa:
    def __init__(self, status: int = 200, falla: bool = False) -> None:
        self.llamadas: list[tuple[str, dict]] = []
        self._status = status
        self._falla = falla

    def post(self, url: str, json: dict, timeout: float) -> SimpleNamespace:
        if self._falla:
            raise ConnectionError("sin red")
        self.llamadas.append((url, json))
        return SimpleNamespace(status_code=self._status)


class PlanificadorFalso:
    def __init__(self) -> None:
        self.demoras: list[float] = []

    def programar(self, demora_segundos: float) -> None:
        self.demoras.append(demora_segundos)


def _planificador(sesion: SesionFalsa) -> PlanificadorCloudTasks:
    return PlanificadorCloudTasks(
        "solventa-511815",
        "us-central1",
        "reintento-cobros",
        "https://payments-123.us-central1.run.app/",
        "rt-payments@solventa-511815.iam.gserviceaccount.com",
        sesion=sesion,
        reloj=lambda: 1_000_000.0,
    )


def test_programa_una_tarea_http_con_token_oidc_hacia_el_endpoint_de_reintento() -> None:
    sesion = SesionFalsa()

    _planificador(sesion).programar(30)

    url, cuerpo = sesion.llamadas[0]
    assert url == (
        "https://cloudtasks.googleapis.com/v2/projects/solventa-511815/locations/us-central1"
        "/queues/reintento-cobros/tasks"
    )
    tarea = cuerpo["task"]
    assert (
        tarea["httpRequest"]["url"] == "https://payments-123.us-central1.run.app/cobros/_reintentar"
    )
    assert tarea["httpRequest"]["httpMethod"] == "POST"
    assert tarea["httpRequest"]["oidcToken"] == {
        "serviceAccountEmail": "rt-payments@solventa-511815.iam.gserviceaccount.com",
        "audience": "https://payments-123.us-central1.run.app",
    }
    assert tarea["scheduleTime"] == "1970-01-12T13:47:10Z"  # 1_000_030 s (RFC 3339)
    assert tarea["name"].endswith("/tasks/reintento-cobros-33334")  # ventana de 30 s


def test_dos_ordenes_en_la_misma_ventana_comparten_el_nombre_de_la_tarea() -> None:
    sesion = SesionFalsa()
    planificador = _planificador(sesion)

    planificador.programar(30)
    planificador.programar(30)

    assert sesion.llamadas[0][1]["task"]["name"] == sesion.llamadas[1][1]["task"]["name"]


def test_una_tarea_ya_programada_no_es_un_error() -> None:
    _planificador(SesionFalsa(status=409)).programar(30)  # no lanza


@pytest.mark.parametrize("sesion", [SesionFalsa(status=500), SesionFalsa(falla=True)])
def test_si_cloud_tasks_falla_no_se_tumba_el_cobro(sesion: SesionFalsa) -> None:
    _planificador(sesion).programar(30)  # no lanza


def test_el_bus_programa_el_reintento_solo_cuando_el_cobro_queda_pendiente() -> None:
    bus, planificador = BusEventosFalso(), PlanificadorFalso()
    con_reintento = BusConReintento(bus, planificador, 30)

    for estado in ("cobrando", "cobrado", "rechazado"):
        con_reintento.publicar(construir_evento_cobro_estado_cambiado("c", "p", estado, "i"))
    assert planificador.demoras == []

    con_reintento.publicar(construir_evento_cobro_estado_cambiado("c", "p", "pendiente", "i"))
    assert planificador.demoras == [30]
    assert len(bus.eventos) == 4  # el evento sigue llegando al bus de verdad


def _con_cobro_pendiente(planificador: PlanificadorFalso, acl: ClienteAclFalso):
    repositorio = RepositorioCobrosFalso()
    bus = BusEventosFalso()
    ServicioCobros(
        repositorio,
        ClienteAclFalso([ResultadoCobro(estado="pendiente", motivo="pasarela_no_disponible")]),
        bus,
    ).crear_cobro(dict(ORDEN))
    reintento = ReintentoCobrosPendientes(repositorio, acl, bus, 30, 10, planificador=planificador)
    return repositorio, reintento


def test_la_pasada_externa_reintenta_y_no_programa_otra_si_ya_no_queda_nada() -> None:
    planificador = PlanificadorFalso()
    repositorio, reintento = _con_cobro_pendiente(
        planificador, ClienteAclFalso([ResultadoCobro(estado="cobrado", referencia="r1")])
    )

    resultado = reintento.una_pasada_externa()

    assert resultado == {"procesados": 1, "por_reintentar": 0}
    assert repositorio.balance_poliza(ORDEN["poliza_id"]) == "50.00"
    assert planificador.demoras == []


def test_la_pasada_externa_programa_la_siguiente_mientras_la_pasarela_siga_caida() -> None:
    planificador = PlanificadorFalso()
    _, reintento = _con_cobro_pendiente(
        planificador, ClienteAclFalso([ResultadoCobro(estado="pendiente", motivo="sigue_caida")])
    )

    resultado = reintento.una_pasada_externa()

    assert resultado == {"procesados": 1, "por_reintentar": 1}
    assert planificador.demoras == [30]


def test_el_endpoint_de_reintento_ejecuta_una_pasada() -> None:
    planificador = PlanificadorFalso()
    repositorio, reintento = _con_cobro_pendiente(
        planificador, ClienteAclFalso([ResultadoCobro(estado="cobrado", referencia="r1")])
    )
    servicio = ServicioCobros(repositorio, ClienteAclFalso(), BusEventosFalso())

    with TestClient(create_app(servicio_cobros=servicio, reintento=reintento)) as client:
        r = client.post("/cobros/_reintentar")

    assert r.status_code == 200
    assert r.json() == {"procesados": 1, "por_reintentar": 0}


def test_el_endpoint_de_reintento_no_existe_si_no_hay_reintento() -> None:
    servicio = ServicioCobros(RepositorioCobrosFalso(), ClienteAclFalso(), BusEventosFalso())

    with TestClient(create_app(servicio_cobros=servicio, reintento=None)) as client:
        assert client.post("/cobros/_reintentar").status_code == 404


def test_con_disparador_externo_no_se_arranca_el_bucle() -> None:
    iniciado: list[bool] = []

    class ReintentoEspia:
        def iniciar(self) -> None:
            iniciado.append(True)

        async def detener(self) -> None:
            return None

    servicio = ServicioCobros(RepositorioCobrosFalso(), ClienteAclFalso(), BusEventosFalso())
    config = Configuracion.desde_entorno()
    for disparador, esperado in (("bucle", [True]), ("externo", [])):
        iniciado.clear()
        configuracion = Configuracion(**{**config.__dict__, "disparador": disparador})
        with TestClient(
            create_app(configuracion, servicio_cobros=servicio, reintento=ReintentoEspia())
        ):
            pass
        assert iniciado == esperado


def test_disparador_externo_exige_la_configuracion_de_cloud_tasks(monkeypatch) -> None:
    for nombre in ("GCP_PROYECTO", "GCP_REGION", "TAREAS_CUENTA", "URL_PROPIA"):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setenv("DISPARADOR", "externo")

    with pytest.raises(ValueError, match="GCP_PROYECTO"):
        Configuracion.desde_entorno()

    monkeypatch.setenv("GCP_PROYECTO", "p")
    monkeypatch.setenv("GCP_REGION", "r")
    monkeypatch.setenv("TAREAS_CUENTA", "sa@p.iam.gserviceaccount.com")
    monkeypatch.setenv("URL_PROPIA", "https://payments-1.r.run.app")
    assert Configuracion.desde_entorno().disparador == "externo"


def test_disparador_desconocido_falla_claro(monkeypatch) -> None:
    monkeypatch.setenv("DISPARADOR", "magia")

    with pytest.raises(ValueError, match="DISPARADOR"):
        Configuracion.desde_entorno()
