"""Quién dispara la emisión: hilo local o Cloud Tasks (decisión W03-6)."""

import threading

from app.application.planificador import (
    EmisionPendiente,
    PlanificadorCloudTasks,
    PlanificadorLocal,
)


class RespuestaFalsa:
    def __init__(self, codigo: int) -> None:
        self.status_code = codigo


class SesionFalsa:
    def __init__(self, codigo: int = 200, error: Exception | None = None) -> None:
        self.codigo = codigo
        self.error = error
        self.llamadas: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict, timeout: float) -> RespuestaFalsa:
        self.llamadas.append((url, json))
        if self.error is not None:
            raise self.error
        return RespuestaFalsa(self.codigo)


def _cloud(sesion: SesionFalsa) -> PlanificadorCloudTasks:
    return PlanificadorCloudTasks(
        "proyecto", "us-central1", "emision-polizas", "https://policy.run.app/",
        "rt-policy@proyecto.iam", sesion=sesion, reloj=lambda: 1_000_000.0,
    )  # fmt: skip


def test_cloud_tasks_apunta_a_la_emision_de_esa_propuesta_con_oidc() -> None:
    sesion = SesionFalsa()

    _cloud(sesion).encolar("prop-1", 10)

    url, cuerpo = sesion.llamadas[0]
    assert url == (
        "https://cloudtasks.googleapis.com/v2/projects/proyecto/locations/us-central1/"
        "queues/emision-polizas/tasks"
    )
    tarea = cuerpo["task"]
    assert tarea["httpRequest"]["url"] == "https://policy.run.app/propuestas/prop-1/_emitir"
    assert tarea["httpRequest"]["oidcToken"] == {
        "serviceAccountEmail": "rt-policy@proyecto.iam",
        "audience": "https://policy.run.app",
    }
    assert tarea["scheduleTime"] == "1970-01-12T13:46:50Z"  # reloj + 10 s


def test_encolar_la_misma_propuesta_en_la_misma_ventana_repite_el_nombre() -> None:
    sesion = SesionFalsa()
    planificador = _cloud(sesion)

    planificador.encolar("prop-1")
    planificador.encolar("prop-1")

    nombres = {llamada[1]["task"]["name"] for llamada in sesion.llamadas}
    assert len(nombres) == 1  # Cloud Tasks responde 409 al repetir: una sola tarea


def test_un_409_de_cloud_tasks_no_es_un_error(caplog) -> None:
    caplog.set_level("WARNING")

    _cloud(SesionFalsa(409)).encolar("prop-1")

    assert caplog.text == ""


def test_encolar_nunca_lanza_aunque_cloud_tasks_falle(caplog) -> None:
    caplog.set_level("WARNING")

    _cloud(SesionFalsa(500)).encolar("prop-1")
    _cloud(SesionFalsa(error=ConnectionError("sin red"))).encolar("prop-1")

    assert "no se pudo programar la emisión" in caplog.text
    assert "prop-1" not in caplog.text  # solo el id opaco


def test_el_planificador_local_ejecuta_la_emision() -> None:
    hecho = threading.Event()
    planificador = PlanificadorLocal()
    planificador.conectar(lambda propuesta_id: hecho.set())

    planificador.encolar("prop-1")

    assert hecho.wait(2)


def test_el_planificador_local_reintenta_si_la_emision_queda_pendiente() -> None:
    llamadas: list[str] = []
    hecho = threading.Event()

    def emitir(propuesta_id: str) -> None:
        llamadas.append(propuesta_id)
        if len(llamadas) < 3:
            raise EmisionPendiente("UNDER no disponible")
        hecho.set()

    planificador = PlanificadorLocal(demora_reintento_s=0.01)
    planificador.conectar(emitir)
    planificador.encolar("prop-1")

    assert hecho.wait(2)
    assert len(llamadas) == 3


def test_el_planificador_local_se_rinde_tras_el_maximo_de_reintentos() -> None:
    llamadas: list[str] = []

    def emitir(propuesta_id: str) -> None:
        llamadas.append(propuesta_id)
        raise EmisionPendiente("PAYMENTS no disponible")

    planificador = PlanificadorLocal(demora_reintento_s=0.01, max_reintentos=3)
    planificador.conectar(emitir)
    planificador._correr("prop-1", 0)
    planificador._correr("prop-1", 2)  # el último intento permitido ya no reprograma

    assert len(llamadas) == 2


def test_un_error_inesperado_en_el_hilo_no_lo_mata(caplog) -> None:
    caplog.set_level("WARNING")
    planificador = PlanificadorLocal()
    planificador.conectar(lambda propuesta_id: 1 / 0)

    planificador._correr("prop-1", 0)  # no lanza

    assert "falló la emisión" in caplog.text
