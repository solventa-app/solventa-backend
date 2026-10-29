"""Endpoints de ALMACENAMIENTO con dobles (sin PostgreSQL ni Cloud Storage reales: la verificación
con los reales se hace en vivo con `docker compose` y fake-gcs-server)."""

import base64
import json

from fakes import JPEG, AlmacenFalso, BusFalso, RepositorioFalso, catalogo_de_prueba
from fastapi.testclient import TestClient

from app.application.configuracion import Configuracion
from app.application.servicio_archivos import ServicioArchivos
from app.main import create_app

CLIENTE = {"X-Actor-Rol": "CLIENTE", "X-Actor-Id": "u-1"}
OTRO = {"X-Actor-Rol": "CLIENTE", "X-Actor-Id": "u-2"}


class Pila:
    def __init__(self) -> None:
        self.repo = RepositorioFalso()
        self.almacen = AlmacenFalso()
        self.bus = BusFalso()
        servicio = ServicioArchivos(self.repo, self.almacen, catalogo_de_prueba(), self.bus)
        self.cliente = TestClient(
            create_app(config=Configuracion(""), servicio=servicio, bucket="bucket-prueba")
        )

    def solicitar(self, tamano: int | None = None, encabezados=CLIENTE):
        return self.cliente.post(
            "/archivos/subidas",
            json={
                "tipo": "foto-cliente",
                "tipo_contenido": "image/jpeg",
                "tamano_bytes": tamano or len(JPEG),
                "propietario_id": "u-1",
            },
            headers=encabezados,
        )


def _push(evento: str, objeto: str, bucket: str = "bucket-prueba") -> dict:
    datos = base64.b64encode(json.dumps({"name": objeto}).encode()).decode()
    return {
        "message": {
            "attributes": {"eventType": evento, "objectId": objeto, "bucketId": bucket},
            "data": datos,
            "messageId": "1",
        },
        "subscription": "projects/p/subscriptions/almacenamiento-gcs",
    }


def test_health() -> None:
    with Pila().cliente as cliente:
        assert cliente.get("/health").json() == {"servicio": "almacenamiento", "estado": "ok"}


def test_recorrido_completo_por_http() -> None:
    pila = Pila()
    with pila.cliente as cliente:
        respuesta = pila.solicitar()
        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["metodo"] == "PUT"
        assert cuerpo["encabezados"]["Content-Type"] == "image/jpeg"
        archivo_id = cuerpo["archivo"]["id"]

        # Antes de subir: el objeto no está.
        assert cliente.post(f"/archivos/{archivo_id}/confirmar", headers=CLIENTE).json() == {
            "detail": "OBJETO_NO_SUBIDO"
        }
        pila.almacen.subir(pila.repo.archivos[archivo_id]["objeto"], JPEG, "image/jpeg")
        confirmado = cliente.post(f"/archivos/{archivo_id}/confirmar", headers=CLIENTE)
        assert confirmado.status_code == 200 and confirmado.json()["estado"] == "DISPONIBLE"

        assert (
            cliente.get(f"/archivos/{archivo_id}", headers=CLIENTE).json()["estado"] == "DISPONIBLE"
        )
        assert cliente.get(f"/archivos/{archivo_id}", headers=OTRO).status_code == 404
        descarga = cliente.get(f"/archivos/{archivo_id}/descarga", headers=CLIENTE)
        assert descarga.status_code == 200 and "firma=descarga" in descarga.json()["url"]
        lista = cliente.get(
            "/archivos",
            params={"propietario_tipo": "usuario", "propietario_id": "u-1"},
            headers=CLIENTE,
        )
        assert [a["id"] for a in lista.json()] == [archivo_id]


def test_errores_del_catalogo_y_del_actor() -> None:
    pila = Pila()
    with pila.cliente:
        assert pila.solicitar(tamano=5000).json() == {"detail": "TAMANO_NO_ADMITIDO"}
        assert pila.solicitar(tamano=5000).status_code == 422
        backoffice = {"X-Actor-Rol": "BACKOFFICE", "X-Actor-Id": "b-1"}
        assert pila.solicitar(encabezados=backoffice).status_code == 403
        assert pila.solicitar(encabezados={}).json() == {"detail": "ACTOR_INVALIDO"}
        assert pila.solicitar(encabezados={"X-Actor-Rol": "SERVICIO"}).status_code == 400


def test_push_de_pubsub_confirma_y_siempre_responde_204() -> None:
    pila = Pila()
    with pila.cliente as cliente:
        archivo_id = pila.solicitar().json()["archivo"]["id"]
        objeto = pila.repo.archivos[archivo_id]["objeto"]
        pila.almacen.subir(objeto, JPEG, "image/jpeg")

        assert (
            cliente.post("/eventos/gcs", json=_push("OBJECT_FINALIZE", objeto, "otro")).status_code
            == 204
        )
        assert pila.repo.archivos[archivo_id]["estado"] == "PENDIENTE"  # bucket ajeno: se ignora

        assert (
            cliente.post("/eventos/gcs", json=_push("OBJECT_FINALIZE", objeto)).status_code == 204
        )
        assert pila.repo.archivos[archivo_id]["estado"] == "DISPONIBLE"
        assert cliente.post("/eventos/gcs", json={"basura": True}).status_code == 204
        assert cliente.post("/eventos/gcs", content=b"no es json").status_code == 204


def test_retencion_y_purga_por_http() -> None:
    pila = Pila()
    with pila.cliente as cliente:
        archivo_id = pila.solicitar().json()["archivo"]["id"]
        pila.almacen.subir(pila.repo.archivos[archivo_id]["objeto"], JPEG, "image/jpeg")
        cliente.post(f"/archivos/{archivo_id}/confirmar", headers=CLIENTE)
        backoffice = {"X-Actor-Rol": "BACKOFFICE", "X-Actor-Id": "b-1"}
        respuesta = cliente.post(f"/archivos/{archivo_id}/retencion", headers=backoffice)
        assert respuesta.json() == {"detail": "SIN_RETENCION"}
        assert cliente.post("/archivos/_purgar-vencidos").json() == {"eliminados": 0}


def test_sin_bucket_configurado_responde_503_pero_esta_sano() -> None:
    with TestClient(create_app(config=Configuracion(""))) as cliente:
        assert cliente.get("/health").status_code == 200
        respuesta = cliente.post(
            "/archivos/subidas",
            json={
                "tipo": "foto-cliente",
                "tipo_contenido": "image/jpeg",
                "tamano_bytes": 10,
                "propietario_id": "u-1",
            },
            headers=CLIENTE,
        )
        assert respuesta.status_code == 503
        assert respuesta.json() == {"detail": "ALMACEN_NO_CONFIGURADO"}
