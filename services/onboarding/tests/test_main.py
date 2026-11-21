"""Endpoints de ONBOARDING con dobles (con los reales se verifica en vivo con compose)."""

import time

from fakes import Pila
from fastapi.testclient import TestClient

from app.application.configuracion import Configuracion
from app.application.planificador import PlanificadorLocal, VerificacionPendiente
from app.main import create_app


def _cliente(pila: Pila) -> TestClient:
    return TestClient(create_app(config=Configuracion(""), servicio=pila.servicio))


def test_recorrido_completo_por_http() -> None:
    pila = Pila()
    with _cliente(pila) as cliente:
        r = cliente.post("/onboarding", json={"jurisdiccion": "co", "tipo_documento": "cc"})
        assert r.status_code == 200
        sesion_id, token = r.json()["onboarding_id"], r.json()["token"]
        h = {"X-Onboarding-Token": token}

        r = cliente.post(
            f"/onboarding/{sesion_id}/subidas",
            json={"paso": "ANVERSO", "tipo_contenido": "image/jpeg", "tamano_bytes": 10},
            headers=h,
        )
        assert (r.status_code, r.json()["detail"]) == (409, "SIN_CONSENTIMIENTO")
        r = cliente.post(
            f"/onboarding/{sesion_id}/consentimiento", json={"consentimiento_id": "c-1"}, headers=h
        )
        assert r.status_code == 200

        for paso, contenido in (
            ("anverso", "image/jpeg"),
            ("reverso", "image/jpeg"),
            ("rostro", "image/jpeg"),
            ("video", "video/mp4"),
        ):
            r = cliente.post(
                f"/onboarding/{sesion_id}/subidas",
                json={"paso": paso, "tipo_contenido": contenido, "tamano_bytes": 10},
                headers=h,
            )
            assert r.status_code == 200, r.text
            assert r.json()["metodo"] == "PUT"
            archivo_id = r.json()["archivo_id"]
            r = cliente.post(f"/onboarding/{sesion_id}/subidas/{archivo_id}/confirmar", headers=h)
            assert r.json()["estado"] == "DISPONIBLE"

        r = cliente.post(f"/onboarding/{sesion_id}/cuenta", json={"usuario_id": "u-9"}, headers=h)
        assert r.status_code == 200
        r = cliente.post(f"/onboarding/{sesion_id}/enviar", headers=h)
        assert r.json()["estado"] == "EN_VERIFICACION"

        r = cliente.post(f"/tareas/verificar/{sesion_id}")
        assert r.json()["estado"] == "VERIFICADA"
        r = cliente.get(f"/onboarding/{sesion_id}", headers=h)
        assert r.json() == {
            "id": sesion_id,
            "estado": "VERIFICADA",
            "motivo": None,
            "intento": 1,
            "intentos_restantes": 2,
            "tiene_cuenta": True,
        }
        assert cliente.get("/usuarios/u-9/onboarding").json()["estado"] == "VERIFICADA"


def test_sin_token_es_401_y_tarea_con_acl_caido_es_503() -> None:
    pila = Pila()
    sesion, _ = pila.sesion_lista()
    pila.servicio.enviar(sesion)
    pila.identidad.caido = True
    with _cliente(pila) as cliente:
        assert cliente.get(f"/onboarding/{sesion['id']}").status_code == 401
        r = cliente.post(f"/tareas/verificar/{sesion['id']}")
        assert (r.status_code, r.json()["detail"]) == (503, "VERIFICACION_PENDIENTE")
        assert cliente.get("/usuarios/nadie/onboarding").status_code == 404


def test_la_tarea_responde_503_y_en_el_ultimo_intento_de_cloud_tasks_cierra() -> None:
    """KAN-131: Cloud Tasks reintenta con 503; en el último intento de la cola (encabezado
    `X-CloudTasks-TaskRetryCount` = `TAREAS_MAX_INTENTOS` - 1) cierra en RECHAZADA técnico."""
    pila = Pila({"estado": "pendiente", "motivo": "circuito_abierto"})
    sesion, _ = pila.sesion_lista()
    pila.servicio.enviar(sesion)
    with _cliente(pila) as cliente:
        r = cliente.post(
            f"/tareas/verificar/{sesion['id']}", headers={"X-CloudTasks-TaskRetryCount": "3"}
        )
        assert (r.status_code, r.json()["detail"]) == (503, "VERIFICACION_PENDIENTE")
        r = cliente.post(
            f"/tareas/verificar/{sesion['id']}", headers={"X-CloudTasks-TaskRetryCount": "4"}
        )
        assert r.status_code == 200
        assert (r.json()["estado"], r.json()["motivo"]) == ("RECHAZADA", "PROVEEDOR_NO_DISPONIBLE")
        assert r.json()["intentos_restantes"] == 3


def test_el_planificador_local_avisa_el_ultimo_intento() -> None:
    llamadas: list[bool] = []

    def verificar(sesion_id: str, ultimo_intento: bool = False) -> None:
        llamadas.append(ultimo_intento)
        if not ultimo_intento:
            raise VerificacionPendiente("acl")

    planificador = PlanificadorLocal(demora_reintento_s=0, max_reintentos=3)
    planificador.conectar(verificar)
    planificador._correr("s-1", 0)
    for _ in range(100):
        if len(llamadas) == 3:
            break
        time.sleep(0.01)
    assert llamadas == [False, False, True]
