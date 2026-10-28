"""Dobles de prueba de ALMACENAMIENTO: repositorio en memoria, bucket en memoria y bus que guarda.

El repositorio imita las garantías de `RepositorioArchivosPostgres` que importan aquí: los cambios
de estado son condicionales y `accesos_archivo` solo crece. La verificación contra Postgres y
fake-gcs-server reales se hace en vivo con `docker compose`."""

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.catalogo import Catalogo

AHORA = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 56
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 52
PDF = b"%PDF-1.7\n" + b"\x00" * 55


def catalogo_de_prueba() -> Catalogo:
    """Un tipo por cada forma de uso: de un cliente, de un servicio con retención, e inactivo."""
    return Catalogo.desde_json(
        {
            "version": 1,
            "tipos": [
                {
                    "tipo": "foto-cliente",
                    "descripcion": "prueba",
                    "activo": True,
                    "tipos_contenido": ["image/jpeg", "image/png"],
                    "tamano_max_bytes": 1000,
                    "quien_sube": ["CLIENTE", "ASESOR"],
                    "quien_lee": ["CLIENTE", "ASESOR", "BACKOFFICE"],
                    "propietario": "usuario",
                    "requiere_consentimiento": False,
                    "retencion_dias": None,
                },
                {
                    "tipo": "video-onboarding",
                    "descripcion": "prueba",
                    "activo": True,
                    "tipos_contenido": ["video/mp4"],
                    "tamano_max_bytes": 5000,
                    "quien_sube": ["servicio:onboarding"],
                    "quien_lee": ["servicio:onboarding"],
                    "propietario": "onboarding",
                    "requiere_consentimiento": True,
                    "retencion_dias": 30,
                },
                {
                    "tipo": "pdf-viejo",
                    "descripcion": "prueba",
                    "activo": False,
                    "tipos_contenido": ["application/pdf"],
                    "tamano_max_bytes": 1000,
                    "quien_sube": ["BACKOFFICE"],
                    "quien_lee": ["BACKOFFICE"],
                    "propietario": "poliza",
                    "requiere_consentimiento": False,
                    "retencion_dias": None,
                },
            ],
        }
    )


class RepositorioFalso:
    def __init__(self) -> None:
        self.archivos: dict[str, dict[str, Any]] = {}
        self.accesos: list[dict[str, Any]] = []
        # Para simular que otro proceso resolvió el archivo justo antes (carrera).
        self.perder_proxima_carrera = False

    def crear(self, archivo: dict[str, Any]) -> dict[str, Any]:
        fila = {
            **archivo,
            "estado": "PENDIENTE",
            "tamano_bytes": None,
            "crc32c": None,
            "motivo": None,
            "retener_desde": None,
            "creado_en": AHORA,
            "disponible_en": None,
            "eliminado_en": None,
            "actualizado_en": AHORA,
        }
        self.archivos[archivo["id"]] = fila
        return copy.deepcopy(fila)

    def obtener(self, archivo_id: str) -> dict[str, Any] | None:
        fila = self.archivos.get(archivo_id)
        return copy.deepcopy(fila) if fila else None

    def obtener_por_objeto(self, objeto: str) -> dict[str, Any] | None:
        for fila in self.archivos.values():
            if fila["objeto"] == objeto:
                return copy.deepcopy(fila)
        return None

    def listar_por_propietario(self, propietario_tipo: str, propietario_id: str, limite: int = 200):
        return [
            copy.deepcopy(f)
            for f in self.archivos.values()
            if f["propietario_tipo"] == propietario_tipo and f["propietario_id"] == propietario_id
        ][:limite]

    def _cambiar(self, archivo_id: str, desde: str, cambios: dict[str, Any]):
        fila = self.archivos.get(archivo_id)
        if self.perder_proxima_carrera and fila is not None:
            self.perder_proxima_carrera = False
            fila["estado"] = "DISPONIBLE"
            return None
        if fila is None or fila["estado"] != desde:
            return None
        fila.update(cambios)
        return copy.deepcopy(fila)

    def marcar_disponible(self, archivo_id: str, tamano_bytes: int, crc32c: str | None):
        return self._cambiar(
            archivo_id,
            "PENDIENTE",
            {
                "estado": "DISPONIBLE",
                "tamano_bytes": tamano_bytes,
                "crc32c": crc32c,
                "disponible_en": AHORA,
            },
        )

    def marcar_rechazado(self, archivo_id: str, motivo: str):
        return self._cambiar(archivo_id, "PENDIENTE", {"estado": "RECHAZADO", "motivo": motivo})

    def marcar_eliminado(self, archivo_id: str, desde: str = "DISPONIBLE"):
        return self._cambiar(archivo_id, desde, {"estado": "ELIMINADO", "eliminado_en": AHORA})

    def fijar_retencion(self, archivo_id: str, desde: datetime):
        fila = self.archivos.get(archivo_id)
        if fila is None or fila["estado"] != "DISPONIBLE":
            return None
        fila["retener_desde"] = fila["retener_desde"] or desde
        return copy.deepcopy(fila)

    def vencidos(self, dias_por_tipo: dict[str, int], ahora: datetime, limite: int):
        return [
            copy.deepcopy(f)
            for f in self.archivos.values()
            if f["estado"] == "DISPONIBLE"
            and f["retener_desde"] is not None
            and f["tipo"] in dias_por_tipo
            and f["retener_desde"] + timedelta(days=dias_por_tipo[f["tipo"]]) <= ahora
        ][:limite]

    def pendientes_antes_de(self, antes: datetime, limite: int):
        return [
            copy.deepcopy(f)
            for f in self.archivos.values()
            if f["estado"] == "PENDIENTE" and f["creado_en"] < antes
        ][: max(0, limite)]

    def registrar_acceso(self, archivo_id, accion, actor, actor_id, vence_en) -> None:
        self.accesos.append(
            {
                "archivo_id": archivo_id,
                "accion": accion,
                "actor": actor,
                "actor_id": actor_id,
                "vence_en": vence_en,
            }
        )


class AlmacenFalso:
    """Un bucket en memoria: `subir` hace lo que haría el PUT del cliente."""

    def __init__(self) -> None:
        self.objetos: dict[str, dict[str, Any]] = {}
        self.borrados: list[str] = []
        self.custom_time: dict[str, datetime] = {}

    def subir(self, objeto: str, contenido: bytes, tipo_contenido: str) -> None:
        self.objetos[objeto] = {"contenido": contenido, "tipo_contenido": tipo_contenido}

    def url_subida(self, objeto, tipo_contenido, tamano_max, vigencia_s):
        encabezados = {
            "Content-Type": tipo_contenido,
            "x-goog-content-length-range": f"0,{tamano_max}",
            "x-goog-if-generation-match": "0",
        }
        return f"https://almacen.prueba/{objeto}?firma=subida&vigencia={vigencia_s}", encabezados

    def url_descarga(self, objeto, vigencia_s):
        return f"https://almacen.prueba/{objeto}?firma=descarga&vigencia={vigencia_s}"

    def metadatos(self, objeto):
        guardado = self.objetos.get(objeto)
        if guardado is None:
            return None
        return {
            "tamano_bytes": len(guardado["contenido"]),
            "tipo_contenido": guardado["tipo_contenido"],
            "crc32c": "crc==",
            "generacion": "1",
        }

    def primeros_bytes(self, objeto, cantidad):
        return self.objetos[objeto]["contenido"][:cantidad]

    def borrar(self, objeto):
        self.objetos.pop(objeto, None)
        self.borrados.append(objeto)

    def fijar_custom_time(self, objeto, momento):
        self.custom_time[objeto] = momento


class BusFalso:
    def __init__(self) -> None:
        self.eventos: list[dict[str, Any]] = []

    def publicar(self, evento: dict[str, Any]) -> None:
        self.eventos.append(evento)
