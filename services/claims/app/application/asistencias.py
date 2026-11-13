"""Asistencia en sitio (HU-M06, KAN-265 y KAN-266): solicitar y cancelar.

A diferencia del catálogo de prestadores (dato maestro que lee la app), esto es transaccional: cada
solicitud se escribe en la **primaria** (escritor único: CLAIMS) y se lee de ella al responder.

- Idempotente por `id_solicitud` (lo genera la app): reenviar (M06-03b) no crea una segunda
  asistencia. Si la primera quedó NO_CONFIRMADA, reenviar vuelve a intentar confirmarla.
- La ubicación del cliente (adónde debe llegar el prestador) y su descripción son datos personales:
  se guardan cifradas por campo con el id de la asistencia como dueño y nunca van a los logs.
- El desenlace lo decide la red de prestadores **detrás del ACL** (regla 2, contrato v0.49, B7 del
  plan de HU-M06): CLAIMS ya no simula. Mientras no haya prestadores reales, `stubs/prestadores`
  confirma o no (modo `primero_falla`: el primer intento queda NO_CONFIRMADA y el reintento se
  confirma) y calcula los minutos. Si el ACL no responde o su circuito está abierto, la asistencia
  queda NO_CONFIRMADA (regla 4): la app ya sabe reintentar con el mismo `idSolicitud`.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pymongo import MongoClient

from app.application.catalogo_prestadores import prestador_desde_documento
from app.application.cifrado import CifradorCampos

logger = logging.getLogger("claims.asistencias")

MAX_DESCRIPCION = 500
MAX_DIRECCION = 200

MOTIVO_SIN_RESPUESTA = "PRESTADOR_SIN_RESPUESTA"
DIAS_MIS_ASISTENCIAS = 30
MAX_MIS_ASISTENCIAS = 50

MENSAJE_PRESTADOR_NO_DISPONIBLE = (
    "Este prestador ya no está disponible. Elige otro prestador cercano."
)
MENSAJE_TIPO_NO_ATENDIDO = "Este prestador no atiende ese tipo de asistencia. Elige otro prestador."
MENSAJE_DESCRIPCION_LARGA = f"La descripción no puede pasar de {MAX_DESCRIPCION} caracteres."
MENSAJE_DIRECCION_LARGA = f"La dirección no puede pasar de {MAX_DIRECCION} caracteres."
MENSAJE_DIRECCION_REQUERIDA = "Escribe la dirección a la que debe llegar el prestador."
MENSAJE_SOLICITUD_AJENA = "Esta solicitud ya existe. Vuelve a intentarlo desde el mapa."
MENSAJE_NO_ENCONTRADA = "No encontramos esa asistencia."


class ErrorAsistencia(Exception):
    """Error de negocio con un código estable para la app y un mensaje para la persona."""

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


class RedPrestadores(Protocol):
    """La red de prestadores, vista desde CLAIMS: el ACL Worker (`POST /prestadores/asistencias`).
    Devuelve `estado` (`CONFIRMADA` o `NO_CONFIRMADA`), `minutos_estimados` y `motivo`. Lanza si
    no responde."""

    def solicitar(self, pedido: dict) -> dict: ...


class RepositorioAsistencias(Protocol):
    def prestador(self, prestador_id: str) -> dict | None: ...

    def obtener(self, asistencia_id: str) -> dict | None: ...

    def crear(self, asistencia: dict) -> None: ...

    def actualizar(self, asistencia_id: str, cambios: dict) -> None: ...

    def de_cliente(self, cliente_id: str, desde: datetime) -> list[dict]: ...


class RepositorioAsistenciasMongo:
    """Colección `asistencias` en la primaria (lectura de lo propio recién escrito)."""

    def __init__(self, cliente: MongoClient, cifrador: CifradorCampos, nombre_db: str = "claims"):
        db = cliente[nombre_db]
        self._asistencias = db["asistencias"]
        self._prestadores = db["prestadores"]
        self._cifrador = cifrador

    def prestador(self, prestador_id: str) -> dict | None:
        doc = self._prestadores.find_one({"_id": prestador_id})
        return None if doc is None else prestador_desde_documento(doc)

    def obtener(self, asistencia_id: str) -> dict | None:
        doc = self._asistencias.find_one({"_id": asistencia_id})
        if doc is None:
            return None
        aid = doc.pop("_id")
        ubicacion = self._cifrador.descifrar(doc.pop("ubicacion_cifrada"), f"ubicacion:{aid}")
        descripcion = doc.pop("descripcion_cifrada", None)
        return {
            **doc,
            "id": aid,
            "ubicacion": json.loads(ubicacion),
            "descripcion": (
                self._cifrador.descifrar(descripcion, f"descripcion:{aid}") if descripcion else None
            ),
        }

    def crear(self, asistencia: dict) -> None:
        aid = asistencia["id"]
        doc = {k: v for k, v in asistencia.items() if k not in ("id", "ubicacion", "descripcion")}
        doc["_id"] = aid
        doc["ubicacion_cifrada"] = self._cifrador.cifrar(
            json.dumps(asistencia["ubicacion"], ensure_ascii=False), f"ubicacion:{aid}"
        )
        if asistencia.get("descripcion"):
            doc["descripcion_cifrada"] = self._cifrador.cifrar(
                asistencia["descripcion"], f"descripcion:{aid}"
            )
        self._asistencias.insert_one(doc)

    def actualizar(self, asistencia_id: str, cambios: dict) -> None:
        self._asistencias.update_one({"_id": asistencia_id}, {"$set": cambios})

    def de_cliente(self, cliente_id: str, desde: datetime) -> list[dict]:
        """v0.52 (B6): las del cliente desde [desde], de la más reciente a la más vieja (índice
        `ix_cliente_solicitada`, migración 0009)."""
        cursor = (
            self._asistencias.find(
                {"cliente_id": cliente_id, "solicitada_en": {"$gte": desde}}, {"_id": 1}
            )
            .sort("solicitada_en", -1)
            .limit(MAX_MIS_ASISTENCIAS)
        )
        return [a for doc in cursor if (a := self.obtener(doc["_id"])) is not None]


def _publica(asistencia: dict) -> dict:
    """Lo que vuelve al BFF: sin la ubicación ni la descripción (las mandó el propio cliente y no
    hace falta devolverlas) ni el id del cliente."""
    return {
        "id": asistencia["id"],
        "estado": asistencia["estado"],
        "tipo_asistencia": asistencia["tipo_asistencia"],
        "prestador": asistencia["prestador"],
        "minutos_estimados": asistencia.get("minutos_estimados"),
        "motivo": asistencia.get("motivo"),
        "solicitada_en": asistencia["solicitada_en"],
        "cancelada_en": asistencia.get("cancelada_en"),
        "confirmada_en": asistencia.get("confirmada_en"),
        "llegada_estimada": asistencia.get("llegada_estimada"),
    }


def _al(fecha: datetime | None) -> datetime | None:
    """Mongo devuelve fechas sin zona en algunos clientes: se tratan como UTC."""
    if fecha is None:
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


def estado_al_leer(asistencia: dict, ahora: datetime) -> str:
    """v0.52 (B5): una CONFIRMADA cuya llegada ya pasó se lee como LLEGO. Se deriva al leer, sin
    un job: app y backend dicen lo mismo. Con prestadores reales, el prestador marcará la
    llegada."""
    llegada = _al(asistencia.get("llegada_estimada"))
    if asistencia["estado"] == "CONFIRMADA" and llegada is not None and llegada <= ahora:
        return "LLEGO"
    return asistencia["estado"]


def _detalle(asistencia: dict, ahora: datetime) -> dict:
    """Para «Mis asistencias» y su detalle (v0.52, B6): solo al cliente dueño, con lo que pidió
    (descripción y adónde) para el detalle y el aviso «Te alejaste» (4.4b)."""
    u = asistencia.get("ubicacion") or {}
    return {
        **_publica(asistencia),
        "estado": estado_al_leer(asistencia, ahora),
        "descripcion": asistencia.get("descripcion"),
        "ubicacion": {
            "latitud": u.get("latitud"),
            "longitud": u.get("longitud"),
            "direccion": u.get("direccion"),
            "origen": u.get("origen") or ("GPS" if u.get("latitud") is not None else "DIRECCION"),
        },
    }


class ServicioAsistencias:
    def __init__(
        self,
        repositorio: RepositorioAsistencias,
        red: RedPrestadores,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repositorio
        self._red = red
        self._reloj = reloj

    def _desenlace(self, asistencia: dict, intento: int) -> dict:
        """Lo decide la red de prestadores por el ACL. Si no responde, NO_CONFIRMADA (regla 4).
        La ubicación viaja al prestador (es adónde debe llegar), nunca a los logs."""
        p, u = asistencia["prestador"], asistencia["ubicacion"]
        pedido = {
            "asistencia_id": asistencia["id"],
            "intento": intento,
            "tipo_asistencia": asistencia["tipo_asistencia"],
            "prestador_id": p["id"],
            "prestador_latitud": p["latitud"],
            "prestador_longitud": p["longitud"],
            "latitud": u["latitud"],
            "longitud": u["longitud"],
        }
        try:
            respuesta = self._red.solicitar(pedido)
        except Exception as error:
            logger.warning("la red de prestadores no respondió error=%s", type(error).__name__)
            return {"estado": "NO_CONFIRMADA", "motivo": MOTIVO_SIN_RESPUESTA}
        if respuesta.get("estado") != "CONFIRMADA":
            return {
                "estado": "NO_CONFIRMADA",
                "motivo": respuesta.get("motivo") or MOTIVO_SIN_RESPUESTA,
            }
        minutos = respuesta.get("minutos_estimados")
        ahora = self._reloj()
        return {
            "estado": "CONFIRMADA",
            "motivo": None,
            "minutos_estimados": minutos,
            # v0.52 (B5): la llegada que ve el cliente en «Mis asistencias».
            "confirmada_en": ahora,
            "llegada_estimada": ahora + timedelta(minutes=minutos) if minutos else None,
        }

    def solicitar(self, solicitud: dict) -> dict:
        aid = solicitud["id_solicitud"]
        cliente = solicitud["cliente_id"]
        existente = self._repo.obtener(aid)
        if existente is not None:
            if existente["cliente_id"] != cliente:
                raise ErrorAsistencia("DATOS_INVALIDOS", MENSAJE_SOLICITUD_AJENA)
            if existente["estado"] != "NO_CONFIRMADA":
                return _publica(existente)  # reenvío de algo ya resuelto: sin cambios
            intento = existente.get("intentos", 1) + 1
            cambios = {
                **self._desenlace(existente, intento),
                "intentos": intento,
                "actualizada_en": self._reloj(),
            }
            self._repo.actualizar(aid, cambios)
            return _publica({**existente, **cambios})

        descripcion = (solicitud.get("descripcion") or "").strip() or None
        if descripcion and len(descripcion) > MAX_DESCRIPCION:
            raise ErrorAsistencia("DATOS_INVALIDOS", MENSAJE_DESCRIPCION_LARGA)
        direccion = (solicitud.get("direccion") or "").strip() or None
        if direccion and len(direccion) > MAX_DIRECCION:
            raise ErrorAsistencia("DATOS_INVALIDOS", MENSAJE_DIRECCION_LARGA)
        latitud, longitud = solicitud.get("latitud"), solicitud.get("longitud")
        sin_coordenadas = latitud is None or longitud is None
        # v0.50 (B4): de dónde salió la ubicación; se guarda con la asistencia (y en la auditoría).
        origen = solicitud.get("origen_ubicacion") or ("DIRECCION" if sin_coordenadas else "GPS")
        if (sin_coordenadas or origen == "DIRECCION") and not direccion:
            raise ErrorAsistencia("DATOS_INVALIDOS", MENSAJE_DIRECCION_REQUERIDA)
        prestador = self._repo.prestador(solicitud["prestador_id"])
        if prestador is None or not prestador["activo"]:
            raise ErrorAsistencia("PRESTADOR_NO_DISPONIBLE", MENSAJE_PRESTADOR_NO_DISPONIBLE)
        if prestador["tipo"] != solicitud["tipo_asistencia"]:
            raise ErrorAsistencia("TIPO_NO_ATENDIDO", MENSAJE_TIPO_NO_ATENDIDO)

        ahora = self._reloj()
        asistencia = {
            "id": aid,
            "cliente_id": cliente,
            "prestador": prestador,
            "tipo_asistencia": solicitud["tipo_asistencia"],
            "descripcion": descripcion,
            "ubicacion": {
                "latitud": latitud,
                "longitud": longitud,
                "direccion": direccion,
                "origen": origen,
            },
            "intentos": 1,
            "solicitada_en": ahora,
            "actualizada_en": ahora,
        }
        asistencia.update(self._desenlace(asistencia, 1))
        self._repo.crear(asistencia)
        return _publica(asistencia)

    def mis(self, cliente_id: str) -> list[dict]:
        """v0.52 (B6): las del cliente de los últimos 30 días, más recientes primero."""
        ahora = self._reloj()
        desde = ahora - timedelta(days=DIAS_MIS_ASISTENCIAS)
        return [_detalle(a, ahora) for a in self._repo.de_cliente(cliente_id, desde)]

    def detalle(self, asistencia_id: str, cliente_id: str) -> dict:
        """v0.52 (B6): una del cliente (al tocar la notificación). Ajena = no existe."""
        asistencia = self._repo.obtener(asistencia_id)
        if asistencia is None or asistencia["cliente_id"] != cliente_id:
            raise ErrorAsistencia("NO_ENCONTRADA", MENSAJE_NO_ENCONTRADA)
        return _detalle(asistencia, self._reloj())

    def cancelar(self, asistencia_id: str, cliente_id: str) -> dict:
        """Cancelar dos veces devuelve la misma asistencia cancelada. Una asistencia de otro
        cliente es como si no existiera."""
        asistencia = self._repo.obtener(asistencia_id)
        if asistencia is None or asistencia["cliente_id"] != cliente_id:
            raise ErrorAsistencia("NO_ENCONTRADA", MENSAJE_NO_ENCONTRADA)
        if asistencia["estado"] == "CANCELADA":
            return _publica(asistencia)
        ahora = self._reloj()
        cambios = {"estado": "CANCELADA", "cancelada_en": ahora, "actualizada_en": ahora}
        self._repo.actualizar(asistencia_id, cambios)
        return _publica({**asistencia, **cambios})
