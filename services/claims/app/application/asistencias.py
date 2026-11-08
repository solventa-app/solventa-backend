"""Asistencia en sitio (HU-M06, KAN-265 y KAN-266): solicitar y cancelar.

A diferencia del catálogo de prestadores (dato maestro que lee la app), esto es transaccional: cada
solicitud se escribe en la **primaria** (escritor único: CLAIMS) y se lee de ella al responder.

- Idempotente por `id_solicitud` (lo genera la app): reenviar (M06-03b) no crea una segunda
  asistencia. Si la primera quedó NO_CONFIRMADA, reenviar vuelve a intentar confirmarla.
- La ubicación del cliente (adónde debe llegar el prestador) y su descripción son datos personales:
  se guardan cifradas por campo con el id de la asistencia como dueño y nunca van a los logs.
- Mientras no haya prestadores reales, el desenlace se simula (`ASISTENCIA_SIMULACION`): con
  `primero_falla` (por defecto) el primer intento queda NO_CONFIRMADA y el reintento se confirma,
  para que la demo recorra ambos caminos del prototipo (M06-04b y M06-04a).
"""

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pymongo import MongoClient

from app.application.cifrado import CifradorCampos

# Mismas reglas que la app (lib/features/assistance/domain/cercania.dart): 20 km/h + 2 min.
VELOCIDAD_KMH = 20
ALISTAMIENTO_MIN = 2
RADIO_TIERRA_KM = 6371
MAX_DESCRIPCION = 500
MAX_DIRECCION = 200

SIMULACION_PRIMERO_FALLA = "primero_falla"
SIMULACION_CONFIRMA = "confirma"
SIMULACIONES = {SIMULACION_PRIMERO_FALLA, SIMULACION_CONFIRMA}

MENSAJE_PRESTADOR_NO_DISPONIBLE = (
    "Este prestador ya no está disponible. Elige otro prestador cercano."
)
MENSAJE_TIPO_NO_ATENDIDO = "Este prestador no atiende ese tipo de asistencia. Elige otro prestador."
MENSAJE_DESCRIPCION_LARGA = f"La descripción no puede pasar de {MAX_DESCRIPCION} caracteres."
MENSAJE_DIRECCION_LARGA = f"La dirección no puede pasar de {MAX_DIRECCION} caracteres."
MENSAJE_SOLICITUD_AJENA = "Esta solicitud ya existe. Vuelve a intentarlo desde el mapa."
MENSAJE_NO_ENCONTRADA = "No encontramos esa asistencia."


class ErrorAsistencia(Exception):
    """Error de negocio con un código estable para la app y un mensaje para la persona."""

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en línea recta (Haversine)."""
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * RADIO_TIERRA_KM * math.asin(math.sqrt(h))


def minutos_estimados(km: float) -> int:
    return math.ceil(km / VELOCIDAD_KMH * 60) + ALISTAMIENTO_MIN


class RepositorioAsistencias(Protocol):
    def prestador(self, prestador_id: str) -> dict | None: ...

    def obtener(self, asistencia_id: str) -> dict | None: ...

    def crear(self, asistencia: dict) -> None: ...

    def actualizar(self, asistencia_id: str, cambios: dict) -> None: ...


class RepositorioAsistenciasMongo:
    """Colección `asistencias` en la primaria (lectura de lo propio recién escrito)."""

    def __init__(self, cliente: MongoClient, cifrador: CifradorCampos, nombre_db: str = "claims"):
        db = cliente[nombre_db]
        self._asistencias = db["asistencias"]
        self._prestadores = db["prestadores"]
        self._cifrador = cifrador

    def prestador(self, prestador_id: str) -> dict | None:
        doc = self._prestadores.find_one({"_id": prestador_id})
        if doc is None:
            return None
        return {
            "id": doc["_id"],
            "nombre": doc["nombre"],
            "tipo": doc["tipo"],
            "ciudad": doc["ciudad"],
            "latitud": float(doc["latitud"]),
            "longitud": float(doc["longitud"]),
            "activo": doc["activo"],
        }

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
    }


class ServicioAsistencias:
    def __init__(
        self,
        repositorio: RepositorioAsistencias,
        simulacion: str = SIMULACION_PRIMERO_FALLA,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if simulacion not in SIMULACIONES:
            raise ValueError(f"ASISTENCIA_SIMULACION inválida: {simulacion}")
        self._repo = repositorio
        self._simulacion = simulacion
        self._reloj = reloj

    def _desenlace(self, asistencia: dict, intento: int) -> dict:
        """Simulado hasta que existan prestadores reales: CONFIRMADA con el tiempo de llegada
        desde la ubicación del cliente, o NO_CONFIRMADA porque el prestador no respondió."""
        if self._simulacion == SIMULACION_PRIMERO_FALLA and intento == 1:
            return {"estado": "NO_CONFIRMADA", "motivo": "PRESTADOR_SIN_RESPUESTA"}
        p, u = asistencia["prestador"], asistencia["ubicacion"]
        km = distancia_km(u["latitud"], u["longitud"], p["latitud"], p["longitud"])
        return {"estado": "CONFIRMADA", "motivo": None, "minutos_estimados": minutos_estimados(km)}

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
                "latitud": solicitud["latitud"],
                "longitud": solicitud["longitud"],
                "direccion": direccion,
            },
            "intentos": 1,
            "solicitada_en": ahora,
            "actualizada_en": ahora,
        }
        asistencia.update(self._desenlace(asistencia, 1))
        self._repo.crear(asistencia)
        return _publica(asistencia)

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
