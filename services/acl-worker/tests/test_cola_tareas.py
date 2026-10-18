"""Reconciliación diferida por Cloud Tasks (decisión W05-7): mismo contrato que la cola `rq`."""

import base64
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_main import CONFIG_SIN_PROVEEDORES

from app.application.cola_tareas import ColaReconciliacionCloudTasks
from app.application.configuracion import Configuracion
from app.application.reconciliacion import ProductorReconciliacion, TrabajoReconciliacion
from app.main import create_app

TRABAJO = TrabajoReconciliacion("historial-crediticio", "cliente-1", "consent-1", 1_000.0)


class SesionFalsa:
    def __init__(self, status: int = 200) -> None:
        self.llamadas: list[tuple[str, dict]] = []
        self._status = status

    def post(self, url: str, json: dict, timeout: float) -> SimpleNamespace:
        self.llamadas.append((url, json))
        return SimpleNamespace(status_code=self._status)


def _cola(sesion: SesionFalsa) -> ColaReconciliacionCloudTasks:
    return ColaReconciliacionCloudTasks(
        "solventa-511815",
        "us-central1",
        "reconciliacion-fuentes",
        "https://consolidador-fuentes-123.us-central1.run.app/",
        "rt-acl-worker@solventa-511815.iam.gserviceaccount.com",
        sesion=sesion,
    )


def test_encola_una_tarea_http_hacia_el_consolidador_con_el_trabajo_en_el_cuerpo() -> None:
    sesion = SesionFalsa()

    _cola(sesion).encolar(TRABAJO)

    url, cuerpo = sesion.llamadas[0]
    assert url.endswith("/queues/reconciliacion-fuentes/tasks")
    http = cuerpo["task"]["httpRequest"]
    assert http["url"] == "https://consolidador-fuentes-123.us-central1.run.app/reconciliar"
    assert http["oidcToken"]["audience"] == "https://consolidador-fuentes-123.us-central1.run.app"
    assert json.loads(base64.b64decode(http["body"])) == TRABAJO.a_dict()


def test_si_cloud_tasks_no_acepta_la_tarea_lanza_y_el_productor_no_rompe_la_respuesta() -> None:
    cola = _cola(SesionFalsa(status=500))
    with pytest.raises(RuntimeError):
        cola.encolar(TRABAJO)

    class DedupSiempre:
        def marcar_si_ausente(self, fuente: str, cliente_id: str) -> bool:
            return True

    # fire-and-forget: el fallo se registra y no se propaga (la respuesta ya está resuelta)
    ProductorReconciliacion(cola, DedupSiempre()).encolar_si_corresponde("f", "c", "k")


def test_con_disparador_externo_la_configuracion_exige_cloud_tasks(monkeypatch) -> None:
    for nombre in ("GCP_PROYECTO", "GCP_REGION", "TAREAS_CUENTA", "CONSOLIDADOR_URL"):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setenv("DISPARADOR", "externo")

    with pytest.raises(ValueError, match="GCP_PROYECTO"):
        Configuracion.desde_entorno()

    monkeypatch.setenv("GCP_PROYECTO", "p")
    monkeypatch.setenv("GCP_REGION", "r")
    monkeypatch.setenv("TAREAS_CUENTA", "sa@p.iam.gserviceaccount.com")
    monkeypatch.setenv("CONSOLIDADOR_URL", "https://consolidador-1.r.run.app")
    assert Configuracion.desde_entorno().consolidador_url == "https://consolidador-1.r.run.app"


def test_el_servicio_arranca_con_la_cola_de_cloud_tasks() -> None:
    config = Configuracion(
        **{
            **CONFIG_SIN_PROVEEDORES.__dict__,
            "disparador": "externo",
            "tareas_proyecto": "p",
            "tareas_region": "r",
            "tareas_cuenta": "sa@p.iam.gserviceaccount.com",
            "consolidador_url": "https://consolidador-1.r.run.app",
        }
    )
    with TestClient(create_app(config)) as client:
        assert client.get("/health").status_code == 200
