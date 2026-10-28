"""Sesiones de pago en la página alojada de la pasarela (HU-W03, W03-28 a W03-30; W05-A1 a A4), con
dobles en memoria: idempotencia por intento, reabrir una vencida, rechazo, tres fallos seguidos,
pago sin respuesta conciliado, nunca dos cobros y ningún token en los logs."""

import copy
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import BusEventosFalso, RepositorioCobrosFalso
from fastapi.testclient import TestClient

from app.application.cliente_acl import (
    ConsultaSesionAcl,
    ResultadoSesionAcl,
    SesionPasarela,
    SolicitudSesion,
)
from app.application.configuracion import Configuracion
from app.application.servicio_cobros import ServicioCobros
from app.application.servicio_sesiones import (
    ServicioSesionesPago,
    SolicitudSesionInvalida,
    id_intento_sesion,
)
from app.main import create_app

TOKEN = "tok_secreto_de_prueba"
MEDIO = {"token": TOKEN, "marca": "VISA", "tipo": "CREDITO"}
SOLICITUD = {
    "propuesta_id": "prop-1",
    "cliente_id": "titular-hmac",
    "monto": "185000",
    "moneda": "COP",
    "descripcion": "Seguro de desempleo",
    "url_retorno": "http://localhost:5173/pago/retorno",
}


class AclFalso:
    """La pasarela vista por el ACL: guarda sesiones por referencia y deja simular fallas."""

    def __init__(self) -> None:
        self.sesiones: dict[str, SesionPasarela] = {}
        self.por_referencia: dict[str, str] = {}
        self.caida = False
        self.rechazar_solicitud = False
        self.creadas: list[SolicitudSesion] = []

    def crear_sesion(self, solicitud: SolicitudSesion) -> ResultadoSesionAcl:
        if self.caida:
            return ResultadoSesionAcl(resultado="no_disponible", motivo="pasarela_no_disponible")
        if self.rechazar_solicitud:
            return ResultadoSesionAcl(
                resultado="creada",
                sesion=SesionPasarela(estado="rechazada", motivo="url_retorno_no_permitida"),
            )
        self.creadas.append(solicitud)
        existente = self.por_referencia.get(solicitud.id_intento_cobro)
        if existente is None:
            existente = f"ses_{uuid.uuid4().hex[:8]}"
            self.por_referencia[solicitud.id_intento_cobro] = existente
            self.sesiones[existente] = SesionPasarela(
                estado="abierta",
                sesion_id=existente,
                url_pago=f"http://localhost:8103/checkout/{existente}",
                vence_en=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            )
        return ResultadoSesionAcl(resultado="creada", sesion=copy.copy(self.sesiones[existente]))

    def consultar_sesion(self, proveedor_sesion_id: str) -> ConsultaSesionAcl:
        if self.caida:
            return ConsultaSesionAcl(resultado="desconocido")
        sesion = self.sesiones.get(proveedor_sesion_id)
        if sesion is None:
            return ConsultaSesionAcl(resultado="no_encontrado")
        return ConsultaSesionAcl(resultado="encontrado", sesion=copy.copy(sesion))

    # Lo que pasa en la página de la pasarela, fuera de Solventa.
    def pagar(self, sesion_id: str, estado: str = "cobrada", motivo: str | None = None) -> None:
        sesion = self.sesiones[sesion_id]
        sesion.estado = estado
        sesion.motivo = motivo
        if estado == "cobrada":
            sesion.medio = dict(MEDIO)
            sesion.referencia = f"pg-{uuid.uuid4().hex[:8]}"

    # No lo usa el caso de uso de sesiones, pero `ServicioCobros` lo pide.
    def cobrar(self, orden):  # pragma: no cover
        raise AssertionError("las sesiones no cobran con token")

    def consultar(self, id_intento_cobro):  # pragma: no cover
        raise AssertionError("las sesiones no concilian por referencia")


class RepositorioSesionesFalso:
    def __init__(self, cobros: RepositorioCobrosFalso) -> None:
        self.filas: dict[str, dict] = {}
        self.medios_guardados: dict[str, dict] = {}
        self._cobros = cobros
        self.pagos_registrados = 0

    def ultima(self, propuesta_id: str) -> dict | None:
        propias = [f for f in self.filas.values() if f["propuesta_id"] == propuesta_id]
        return dict(max(propias, key=lambda f: f["intento"])) if propias else None

    def por_proveedor(self, proveedor_sesion_id: str) -> dict | None:
        for fila in self.filas.values():
            if fila["proveedor_sesion_id"] == proveedor_sesion_id:
                return dict(fila)
        return None

    def crear(self, sesion: dict) -> dict:
        for fila in self.filas.values():
            if (fila["propuesta_id"], fila["intento"]) == (
                sesion["propuesta_id"],
                sesion["intento"],
            ):
                return dict(fila)
        fila = {**sesion, "cobro_id": None, "creado_en": datetime.now(UTC)}
        self.filas[sesion["id"]] = fila
        return dict(fila)

    def actualizar(self, sesion_id, estado, motivo, fallos_seguidos) -> dict:
        self.filas[sesion_id].update(estado=estado, motivo=motivo, fallos_seguidos=fallos_seguidos)
        return dict(self.filas[sesion_id])

    def registrar_pago(self, sesion, medio, referencia):
        self.pagos_registrados += 1
        medio_id = str(uuid.uuid4())
        self.medios_guardados[medio_id] = dict(medio)
        cobro, _ = self._cobros.crear_o_obtener(
            str(uuid.uuid4()),
            {
                "id_intento_cobro": sesion["id_intento_cobro"],
                "poliza_id": sesion["propuesta_id"],
                "cliente_id": sesion["cliente_id"],
                "monto": sesion["monto"],
                "moneda": sesion["moneda"],
                "token_medio_pago": medio["token"],
                "origen": "INTERACTIVO",
            },
        )
        cobro = self._cobros.actualizar_estado(cobro["id"], "cobrado", referencia, None)
        cobro["medio_pago_id"] = medio_id
        self._cobros._por_id[cobro["id"]]["medio_pago_id"] = medio_id
        self.filas[sesion["id"]].update(
            estado="cobrada", motivo=None, fallos_seguidos=0, cobro_id=cobro["id"]
        )
        return dict(self.filas[sesion["id"]]), cobro

    def medios(self, ids):
        return {
            i: {
                "marca": self.medios_guardados[i]["marca"],
                "tipo": self.medios_guardados[i]["tipo"],
            }
            for i in ids
            if i in self.medios_guardados
        }


class Entorno:
    def __init__(self) -> None:
        self.acl = AclFalso()
        self.cobros = RepositorioCobrosFalso()
        self.repo = RepositorioSesionesFalso(self.cobros)
        self.bus = BusEventosFalso()
        self.servicio = ServicioSesionesPago(self.repo, self.acl, self.bus, self.cobros.obtener)

    def abrir(self, **cambios) -> dict:
        return self.servicio.abrir({**SOLICITUD, **cambios})

    def confirmar(self, sesion: dict, propuesta_id: str = "prop-1") -> dict | None:
        return self.servicio.confirmar(sesion["proveedor_sesion_id"], propuesta_id)


@pytest.fixture
def entorno() -> Entorno:
    return Entorno()


# ---------- abrir


def test_abrir_crea_el_intento_1_y_repetirlo_devuelve_la_misma(entorno) -> None:
    primera = entorno.abrir()
    segunda = entorno.abrir()

    assert primera["estado"] == "abierta" and primera["intento"] == 1
    assert primera["id_intento_cobro"] == id_intento_sesion("prop-1", 1)
    assert primera["url_pago"].endswith(primera["proveedor_sesion_id"])
    assert segunda["id"] == primera["id"]
    assert len(entorno.repo.filas) == 1


def test_una_solicitud_que_la_pasarela_no_acepta_no_cuenta_como_fallo(entorno) -> None:
    entorno.acl.rechazar_solicitud = True

    with pytest.raises(SolicitudSesionInvalida, match="url_retorno_no_permitida"):
        entorno.abrir()
    assert entorno.repo.filas == {}


# ---------- confirmar


def test_pago_aprobado_crea_el_cobro_interactivo_con_el_medio_y_avisa(entorno) -> None:
    sesion = entorno.abrir()
    entorno.acl.pagar(sesion["proveedor_sesion_id"])

    confirmada = entorno.confirmar(sesion)

    assert confirmada["estado"] == "cobrada" and confirmada["fallos_seguidos"] == 0
    cobro = confirmada["cobro"]
    assert cobro["estado"] == "cobrado" and cobro["origen"] == "INTERACTIVO"
    assert cobro["id_intento_cobro"] == sesion["id_intento_cobro"]
    assert cobro["poliza_id"] == "prop-1" and cobro["monto"] == "185000"
    medio = entorno.repo.medios([cobro["medio_pago_id"]])[cobro["medio_pago_id"]]
    assert medio == {"marca": "VISA", "tipo": "CREDITO"}
    assert entorno.bus.eventos[-1]["datos"]["estado"] == "cobrado"


def test_confirmar_dos_veces_no_cobra_dos_veces(entorno) -> None:
    sesion = entorno.abrir()
    entorno.acl.pagar(sesion["proveedor_sesion_id"])

    primera = entorno.confirmar(sesion)
    segunda = entorno.confirmar(sesion)
    tras_abrir = entorno.abrir()  # «Pagar» otra vez tras cobrar: no abre otra sesión

    assert segunda["cobro"]["id"] == primera["cobro"]["id"]
    assert tras_abrir["estado"] == "cobrada" and tras_abrir["cobro"]["id"] == primera["cobro"]["id"]
    assert entorno.repo.pagos_registrados == 1
    assert len(entorno.repo.filas) == 1


def test_volver_sin_pagar_deja_la_sesion_abierta(entorno) -> None:
    sesion = entorno.abrir()

    assert entorno.confirmar(sesion)["estado"] == "abierta"


def test_una_sesion_de_otra_propuesta_responde_como_inexistente(entorno) -> None:
    sesion = entorno.abrir()

    assert entorno.confirmar(sesion, propuesta_id="prop-ajena") is None
    assert entorno.servicio.confirmar("ses_nada", "prop-1") is None


# ---------- rechazo, vencimiento y fallas


def test_un_rechazo_pide_otra_tarjeta_en_un_intento_nuevo(entorno) -> None:
    sesion = entorno.abrir()
    entorno.acl.pagar(sesion["proveedor_sesion_id"], "rechazada", "fondos_insuficientes")

    rechazada = entorno.confirmar(sesion)
    nueva = entorno.abrir()

    assert (rechazada["estado"], rechazada["motivo"]) == ("rechazada", "fondos_insuficientes")
    assert rechazada["cobro"] is None
    assert nueva["intento"] == 2 and nueva["fallos_seguidos"] == 0
    assert nueva["id_intento_cobro"] != sesion["id_intento_cobro"]
    assert nueva["proveedor_sesion_id"] != sesion["proveedor_sesion_id"]


def test_una_sesion_vencida_se_reabre_con_otra_referencia(entorno) -> None:
    sesion = entorno.abrir()
    entorno.acl.pagar(sesion["proveedor_sesion_id"], "vencida")

    nueva = entorno.abrir()

    assert entorno.repo.filas[sesion["id"]]["estado"] == "vencida"
    assert nueva["estado"] == "abierta" and nueva["intento"] == 2
    assert entorno.acl.creadas[-1].id_intento_cobro == id_intento_sesion("prop-1", 2)


def test_una_sesion_que_la_pasarela_olvido_cuenta_como_vencida(entorno) -> None:
    sesion = entorno.abrir()
    entorno.acl.sesiones.clear()  # la pasarela de prueba se reinició

    assert entorno.confirmar(sesion)["estado"] == "vencida"
    assert entorno.abrir()["intento"] == 2


def test_al_tercer_fallo_seguido_se_pide_otra_tarjeta(entorno) -> None:
    entorno.acl.caida = True
    motivos = [entorno.abrir() for _ in range(3)]

    assert [m["estado"] for m in motivos] == ["no_disponible"] * 3
    assert [m["fallos_seguidos"] for m in motivos] == [1, 2, 3]
    assert [m["motivo"] for m in motivos] == [
        "pasarela_no_disponible",
        "pasarela_no_disponible",
        "requiere_otra_tarjeta",
    ]
    entorno.acl.caida = False
    nueva = entorno.abrir()
    assert nueva["estado"] == "abierta" and nueva["fallos_seguidos"] == 0  # otra tarjeta


def test_una_sesion_fallida_en_la_pasarela_suma_un_fallo(entorno) -> None:
    sesion = entorno.abrir()
    entorno.acl.pagar(sesion["proveedor_sesion_id"], "fallida", "error_procesamiento")

    fallida = entorno.confirmar(sesion)
    nueva = entorno.abrir()

    assert (fallida["estado"], fallida["motivo"], fallida["fallos_seguidos"]) == (
        "fallida",
        "pasarela_no_disponible",
        1,
    )
    assert nueva["fallos_seguidos"] == 1  # la racha sigue con la sesión nueva


def test_un_pago_sin_respuesta_se_concilia_y_nunca_abre_otra_sesion(entorno) -> None:
    sesion = entorno.abrir()
    entorno.acl.pagar(sesion["proveedor_sesion_id"])  # se cobró, pero Solventa no lo sabe
    entorno.acl.caida = True

    sin_respuesta = entorno.confirmar(sesion)
    reintento = entorno.abrir()  # «Reintentar» con la pasarela todavía caída

    assert (sin_respuesta["estado"], sin_respuesta["motivo"]) == (
        "abierta",
        "pasarela_no_disponible",
    )
    assert reintento["id"] == sesion["id"]  # no se abre otra: la anterior pudo cobrarse
    assert len(entorno.repo.filas) == 1

    entorno.acl.caida = False
    conciliada = entorno.abrir()  # «Reintentar» concilia antes de cobrar (W03-30)
    assert conciliada["estado"] == "cobrada" and conciliada["cobro"]["estado"] == "cobrado"
    assert entorno.repo.pagos_registrados == 1


def test_el_reintento_de_fondo_no_toca_los_cobros_interactivos() -> None:
    cobros = RepositorioCobrosFalso()
    interactivo, _ = cobros.crear_o_obtener(
        "c-1",
        {
            "id_intento_cobro": "i-1",
            "poliza_id": "p",
            "monto": "1",
            "moneda": "COP",
            "token_medio_pago": TOKEN,
            "origen": "INTERACTIVO",
        },
    )
    recurrente, _ = cobros.crear_o_obtener(
        "c-2",
        {
            "id_intento_cobro": "i-2",
            "poliza_id": "p",
            "monto": "1",
            "moneda": "COP",
            "token_medio_pago": TOKEN,
        },
    )
    for cobro in (interactivo, recurrente):
        cobros.actualizar_estado(cobro["id"], "pendiente", None, "pasarela_no_disponible")

    assert [c["id"] for c in cobros.listar_pendientes(5)] == ["c-2"]


def test_el_token_no_sale_en_los_logs(entorno, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    sesion = entorno.abrir()
    entorno.acl.pagar(sesion["proveedor_sesion_id"])
    entorno.confirmar(sesion)

    assert TOKEN not in caplog.text and "titular-hmac" not in caplog.text


# ---------- HTTP


def _http(entorno: Entorno) -> TestClient:
    servicio_cobros = ServicioCobros(entorno.cobros, entorno.acl, entorno.bus)
    app = create_app(
        config=Configuracion.desde_entorno(),
        servicio_cobros=servicio_cobros,
        servicio_sesiones=entorno.servicio,
        medios=entorno.repo.medios,
    )
    return TestClient(app)


def test_http_abrir_confirmar_y_consultar(entorno) -> None:
    with _http(entorno) as client:
        abierta = client.post("/sesiones-pago", json=SOLICITUD)
        sesion_id = abierta.json()["sesion_id"]
        entorno.acl.pagar(sesion_id, "cobrada")
        confirmada = client.post(
            f"/sesiones-pago/{sesion_id}/confirmar", json={"propuesta_id": "prop-1"}
        )
        ultima = client.get("/propuestas/prop-1/sesion-pago")
        ajena = client.post(
            f"/sesiones-pago/{sesion_id}/confirmar", json={"propuesta_id": "prop-ajena"}
        )
        sin_sesion = client.get("/propuestas/prop-nada/sesion-pago")

    assert abierta.status_code == 200 and abierta.json()["estado"] == "abierta"
    cuerpo = confirmada.json()
    assert cuerpo["estado"] == "cobrada"
    assert cuerpo["cobro"]["origen"] == "INTERACTIVO"
    assert cuerpo["cobro"]["medio"] == {"marca": "VISA", "tipo": "CREDITO"}
    assert TOKEN not in confirmada.text  # el token nunca sale de PAYMENTS
    assert ultima.json()["estado"] == "cobrada"
    assert ajena.status_code == 404 and sin_sesion.status_code == 404


def test_http_la_pasarela_caida_nunca_da_5xx(entorno) -> None:
    entorno.acl.caida = True
    with _http(entorno) as client:
        r = client.post("/sesiones-pago", json=SOLICITUD)

    assert r.status_code == 200
    assert (r.json()["estado"], r.json()["motivo"]) == ("no_disponible", "pasarela_no_disponible")


def test_http_una_solicitud_que_la_pasarela_no_acepta_da_422(entorno) -> None:
    entorno.acl.rechazar_solicitud = True
    with _http(entorno) as client:
        r = client.post("/sesiones-pago", json=SOLICITUD)

    assert r.status_code == 422
