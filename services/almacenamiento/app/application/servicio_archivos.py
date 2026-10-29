"""Casos de uso del storage (KAN-250): pedir una subida, confirmarla, leer y retener archivos.

El ciclo de un archivo:

    solicitar_subida ─► PENDIENTE ─(PUT directo al bucket)─► OBJECT_FINALIZE o POST /confirmar
                                                               │
                                    tamaño, tipo y firma OK ───┼─► DISPONIBLE ─retención─► ELIMINADO
                                                               └─► RECHAZADO (borra el objeto)

    PENDIENTE sin confirmar por más de `DIAS_SUBIDA_ABANDONADA` ─(purga)─► ELIMINADO

Quién llama: solo servicios internos (BFF, `onboarding`...), autenticados por IAM de Cloud Run.
Cada llamada declara **a nombre de quién actúa** (`Actor`): el usuario del token, que el BFF ya
validó, o el propio servicio. El catálogo decide qué rol puede subir y leer cada tipo; un CLIENTE,
además, solo ve lo que subió él o lo que es suyo.

Consentimiento (regla 7): el storage no lo consulta. Para los tipos que lo exigen, quien pide la
subida debe traer el `consentimiento_id` que ya validó; queda en la fila."""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.almacen_objetos import PuertoAlmacen
from app.application.bus_eventos import PuertoBusEventos, construir_evento
from app.application.catalogo import ROLES, Catalogo, TipoArchivo
from app.application.identificadores import id_opaco
from app.application.repositorio_archivos import RepositorioArchivosPostgres

logger = logging.getLogger("almacenamiento.archivos")

# Una subida nunca confirmada se borra pasado este plazo. Lo hace la purga y no una regla `age` del
# bucket, porque esa regla no distingue estados: borraría también lo DISPONIBLE sin retención (la
# firma de una póliza emitida, HU-W03).
DIAS_SUBIDA_ABANDONADA = 90

# Primeros bytes de cada tipo de contenido admitido (MP4: `ftyp` en el byte 4).
_BYTES_FIRMA = 16


def _firma_coincide(tipo_contenido: str, inicio: bytes) -> bool:
    if tipo_contenido == "image/jpeg":
        return inicio.startswith(b"\xff\xd8\xff")
    if tipo_contenido == "image/png":
        return inicio.startswith(b"\x89PNG\r\n\x1a\n")
    if tipo_contenido == "application/pdf":
        return inicio.startswith(b"%PDF-")
    if tipo_contenido == "video/mp4":
        return inicio[4:8] == b"ftyp"
    return False


class ErrorArchivo(Exception):
    """Rechazo con un código del contrato y el estado HTTP que le corresponde."""

    def __init__(self, codigo: str, estado_http: int) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.estado_http = estado_http


def _rechazo(codigo: str) -> ErrorArchivo:
    estado = {
        "NO_AUTORIZADO": 403,
        "ARCHIVO_NO_ENCONTRADO": 404,
        "ARCHIVO_NO_DISPONIBLE": 409,
        "OBJETO_NO_SUBIDO": 409,
    }.get(codigo, 422)
    return ErrorArchivo(codigo, estado)


@dataclass(frozen=True)
class Actor:
    """A nombre de quién se hace la operación. `rol` es un rol de usuario o `SERVICIO`."""

    rol: str
    id: str | None = None

    def __post_init__(self) -> None:
        if self.rol not in (*ROLES, "SERVICIO"):
            raise ValueError("rol de actor desconocido")
        if self.rol == "SERVICIO" and not self.id:
            raise ValueError("un actor SERVICIO debe decir qué servicio es")
        if self.rol != "SERVICIO" and not self.id:
            raise ValueError("un actor usuario debe traer su id")

    @property
    def nombre(self) -> str:
        """Como aparece en el catálogo: el rol, o `servicio:<id>`."""
        return f"servicio:{self.id}" if self.rol == "SERVICIO" else self.rol


@dataclass(frozen=True)
class Vigencias:
    subida_s: int = 600
    descarga_s: int = 300


class ServicioArchivos:
    def __init__(
        self,
        repositorio: RepositorioArchivosPostgres,
        almacen: PuertoAlmacen,
        catalogo: Catalogo,
        bus: PuertoBusEventos,
        vigencias: Vigencias | None = None,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repositorio
        self._almacen = almacen
        self._catalogo = catalogo
        self._bus = bus
        self._vigencias = vigencias or Vigencias()
        self._reloj = reloj

    # ------------------------------------------------------------------ subir

    def solicitar_subida(
        self,
        actor: Actor,
        tipo: str,
        tipo_contenido: str,
        tamano_bytes: int,
        propietario_id: str,
        consentimiento_id: str | None = None,
        derivado_de: str | None = None,
    ) -> dict[str, Any]:
        definido = self._catalogo.obtener(tipo)
        if definido is None:
            raise _rechazo("TIPO_NO_ADMITIDO")
        if actor.nombre not in definido.quien_sube:
            raise _rechazo("NO_AUTORIZADO")
        if tipo_contenido not in definido.tipos_contenido:
            raise _rechazo("TIPO_CONTENIDO_NO_ADMITIDO")
        if not 0 < tamano_bytes <= definido.tamano_max_bytes:
            raise _rechazo("TAMANO_NO_ADMITIDO")
        if definido.requiere_consentimiento and not consentimiento_id:
            raise _rechazo("CONSENTIMIENTO_REQUERIDO")
        if not propietario_id or (
            actor.rol == "CLIENTE"
            and definido.propietario == "usuario"
            and propietario_id != actor.id
        ):
            raise _rechazo("PROPIETARIO_INVALIDO")
        if derivado_de is not None:
            origen = self._repo.obtener(derivado_de)
            if origen is None or origen["estado"] != "DISPONIBLE":
                raise _rechazo("ORIGEN_NO_ENCONTRADO")

        ahora = self._reloj()
        archivo_id = str(uuid.uuid4())
        objeto = f"{tipo}/{ahora:%Y}/{ahora:%m}/{archivo_id}"
        archivo = self._repo.crear(
            {
                "id": archivo_id,
                "tipo": tipo,
                "propietario_tipo": definido.propietario,
                "propietario_id": propietario_id,
                "subido_por": actor.nombre,
                "subido_por_id": actor.id,
                "consentimiento_id": consentimiento_id,
                "tipo_contenido": tipo_contenido,
                "tamano_declarado": tamano_bytes,
                "objeto": objeto,
                "derivado_de": derivado_de,
            }
        )
        url, encabezados = self._almacen.url_subida(
            objeto, tipo_contenido, definido.tamano_max_bytes, self._vigencias.subida_s
        )
        vence_en = ahora + timedelta(seconds=self._vigencias.subida_s)
        self._repo.registrar_acceso(archivo_id, "SUBIDA", actor.nombre, actor.id, vence_en)
        logger.info("subida solicitada archivo=%s tipo=%s", id_opaco(archivo_id), tipo)
        return {
            "archivo": archivo,
            "url": url,
            "metodo": "PUT",
            "encabezados": encabezados,
            "vence_en": vence_en,
        }

    def confirmar(self, archivo_id: str, actor: Actor | None = None) -> dict[str, Any]:
        """Valida el objeto subido. Idempotente: un archivo ya resuelto se devuelve tal cual.
        Con `actor` (la confirmación por API) solo puede confirmar quien pidió la subida; sin él es
        la notificación del bucket."""
        archivo = self._repo.obtener(archivo_id)
        if archivo is None or (
            actor is not None
            and (archivo["subido_por"] != actor.nombre or archivo["subido_por_id"] != actor.id)
        ):
            raise _rechazo("ARCHIVO_NO_ENCONTRADO")
        if archivo["estado"] != "PENDIENTE":
            return archivo
        metadatos = self._almacen.metadatos(archivo["objeto"])
        if metadatos is None:
            raise _rechazo("OBJETO_NO_SUBIDO")

        definido = self._catalogo.definicion(archivo["tipo"])
        motivo = self._motivo_rechazo(archivo, metadatos, definido)
        if motivo is None:
            resuelto = self._repo.marcar_disponible(
                archivo_id, metadatos["tamano_bytes"], metadatos["crc32c"]
            )
            evento = "archivo.disponible"
        else:
            resuelto = self._repo.marcar_rechazado(archivo_id, motivo)
            evento = "archivo.rechazado"
        if resuelto is None:  # otro (la notificación o el cliente) lo resolvió primero
            return self._repo.obtener(archivo_id) or archivo
        if motivo is not None:
            self._almacen.borrar(archivo["objeto"])
            logger.info("archivo rechazado archivo=%s motivo=%s", id_opaco(archivo_id), motivo)
        self._bus.publicar(construir_evento(evento, resuelto))
        return resuelto

    def _motivo_rechazo(
        self, archivo: dict[str, Any], metadatos: dict[str, Any], definido: TipoArchivo | None
    ) -> str | None:
        if definido is None:
            return "TIPO_NO_ADMITIDO"
        if metadatos["tamano_bytes"] > definido.tamano_max_bytes:
            return "TAMANO_EXCEDIDO"
        if metadatos["tamano_bytes"] != archivo["tamano_declarado"]:
            return "TAMANO_DISTINTO"
        if metadatos["tipo_contenido"] != archivo["tipo_contenido"]:
            return "TIPO_CONTENIDO_DISTINTO"
        inicio = self._almacen.primeros_bytes(archivo["objeto"], _BYTES_FIRMA)
        if not _firma_coincide(archivo["tipo_contenido"], inicio):
            return "CONTENIDO_NO_COINCIDE"
        return None

    # ------------------------------------------------------------------ notificaciones del bucket

    def notificacion_objeto(self, tipo_evento: str, objeto: str) -> str:
        """Notificación de Cloud Storage (vía Pub/Sub push). Devuelve lo que se hizo, para el log.
        Nunca falla por un objeto desconocido: Pub/Sub reintentaría sin fin."""
        archivo = self._repo.obtener_por_objeto(objeto)
        if archivo is None:
            return "ignorado"
        if tipo_evento == "OBJECT_FINALIZE" and archivo["estado"] == "PENDIENTE":
            try:
                return self.confirmar(archivo["id"])["estado"]
            except ErrorArchivo as error:
                return error.codigo
        if tipo_evento == "OBJECT_DELETE" and archivo["estado"] == "DISPONIBLE":
            self._repo.marcar_eliminado(archivo["id"])
            return "ELIMINADO"
        return "sin cambios"

    # ------------------------------------------------------------------ leer

    def _puede_leer(self, actor: Actor, archivo: dict[str, Any]) -> bool:
        definido = self._catalogo.definicion(archivo["tipo"])
        if definido is None or actor.nombre not in definido.quien_lee:
            return False
        if actor.rol == "CLIENTE":
            return archivo["subido_por_id"] == actor.id or (
                archivo["propietario_tipo"] == "usuario" and archivo["propietario_id"] == actor.id
            )
        return True

    def obtener(self, actor: Actor, archivo_id: str) -> dict[str, Any]:
        archivo = self._repo.obtener(archivo_id)
        # Sin permiso se responde igual que si no existiera: no se revela qué ids hay.
        if archivo is None or not self._puede_leer(actor, archivo):
            raise _rechazo("ARCHIVO_NO_ENCONTRADO")
        return archivo

    def descarga(self, actor: Actor, archivo_id: str) -> dict[str, Any]:
        archivo = self.obtener(actor, archivo_id)
        if archivo["estado"] != "DISPONIBLE":
            raise _rechazo("ARCHIVO_NO_DISPONIBLE")
        url = self._almacen.url_descarga(archivo["objeto"], self._vigencias.descarga_s)
        vence_en = self._reloj() + timedelta(seconds=self._vigencias.descarga_s)
        self._repo.registrar_acceso(archivo_id, "DESCARGA", actor.nombre, actor.id, vence_en)
        return {"url": url, "vence_en": vence_en}

    def listar(self, actor: Actor, propietario_tipo: str, propietario_id: str) -> list[dict]:
        filas = self._repo.listar_por_propietario(propietario_tipo, propietario_id)
        return [fila for fila in filas if self._puede_leer(actor, fila)]

    # ------------------------------------------------------------------ retención

    def fijar_retencion(self, actor: Actor, archivo_id: str) -> dict[str, Any]:
        """Empieza a correr la retención del tipo (p. ej. M01: 30 días tras cerrar la verificación).
        Un CLIENTE no puede pedirla."""
        archivo = self.obtener(actor, archivo_id)
        if actor.rol == "CLIENTE":
            raise _rechazo("NO_AUTORIZADO")
        definido = self._catalogo.definicion(archivo["tipo"])
        if definido is None or definido.retencion_dias is None:
            raise _rechazo("SIN_RETENCION")
        if archivo["estado"] != "DISPONIBLE":
            raise _rechazo("ARCHIVO_NO_DISPONIBLE")
        if archivo["retener_desde"] is not None:
            return archivo
        desde = self._reloj()
        self._almacen.fijar_custom_time(archivo["objeto"], desde)
        return self._repo.fijar_retencion(archivo_id, desde) or archivo

    def purgar_vencidos(self, limite: int = 100) -> int:
        """Borra los objetos cuya retención venció y las subidas que nunca se confirmaron.

        La retención la aplica en la nube la regla de ciclo de vida del bucket (y la notificación
        marca ELIMINADO); para ella esto es el respaldo y lo que corre en local, donde el emulador
        no tiene ciclo de vida. Las subidas abandonadas (PENDIENTE de más de
        `DIAS_SUBIDA_ABANDONADA` días) solo las borra esta pasada, que Cloud Scheduler dispara a
        diario (`infra/almacenamiento.tf`)."""
        ahora = self._reloj()
        dias = {t.tipo: t.retencion_dias for t in self._catalogo.con_retencion()}
        borrados = 0
        for archivo in self._repo.vencidos(dias, ahora, limite):
            self._almacen.borrar(archivo["objeto"])
            if self._repo.marcar_eliminado(archivo["id"]) is not None:
                borrados += 1
        abandonadas = 0
        antes = ahora - timedelta(days=DIAS_SUBIDA_ABANDONADA)
        for archivo in self._repo.pendientes_antes_de(antes, max(0, limite - borrados)):
            self._almacen.borrar(archivo["objeto"])
            if self._repo.marcar_eliminado(archivo["id"], desde="PENDIENTE") is not None:
                abandonadas += 1
        if borrados or abandonadas:
            logger.info(
                "purga: %s archivos vencidos y %s subidas abandonadas eliminados",
                borrados,
                abandonadas,
            )
        return borrados + abandonadas
