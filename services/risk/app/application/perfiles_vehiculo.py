"""Perfil de riesgo del vehículo para la póliza de auto (KAN-290, decisión A-6 de
`docs/plan-poliza-auto.md`).

RISK es el único escritor del riesgo (regla 1): aquí se guarda lo que dijeron RUNT, la guía
Fasecolda y la siniestralidad de una placa, y el resumen con el que RATING tarifa. Igual que en el
perfil financiero, el BFF consulta las fuentes por el ACL Worker y RISK recibe sus respuestas.

- **Llave:** `vehiculo_id`, el HMAC de la placa que calcula el BFF con `CLIENTE_ID_CLAVE`. La placa
  nunca llega a RISK.
- **Cifrado (regla 6):** las respuestas de las fuentes y el resumen van en campos cifrados con el
  mismo `CifradorAesGcm` del perfil financiero; en claro solo quedan los ids opacos, la versión, las
  fechas y si el perfil está completo.
- **Consentimiento (regla 7):** el perfil guarda su consentimiento y se borra al revocarlo o al
  vencer, como el perfil financiero (W01-A3, W01-A4).

Capa simple, sin puertos y adaptadores (regla 3).
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from pymongo import MongoClient, ReturnDocument

from app.application.cifrado import CifradorCampos
from app.application.identificadores import id_opaco
from app.application.repositorio_perfiles import (
    COLECCION_CONTADORES,
    NOMBRE_BASE,
    cifrar_fuentes_perfil,
    condiciones_vencido,
)
from app.application.tiempo_causal import serializar_operation_time

logger = logging.getLogger("risk.perfiles_vehiculo")

COLECCION = "perfiles_vehiculo"
FUENTE_RUNT = "runt-vehiculo"
FUENTE_FASECOLDA = "fasecolda-valor"
FUENTE_SINIESTRALIDAD = "siniestralidad-placa"
# Sin estas dos no hay valor comercial ni datos del vehículo: la oferta solo puede ser preliminar.
OBLIGATORIAS = (FUENTE_RUNT, FUENTE_FASECOLDA)


def estado_fuente(fuente: dict[str, Any]) -> str:
    """El mismo vocabulario que `Oferta.fuentes` (OK, CACHE, FALLO)."""
    if fuente.get("degradado"):
        return "FALLO"
    return "CACHE" if fuente.get("de_cache") else "OK"


def resumir(fuentes: list[dict[str, Any]]) -> dict[str, Any]:
    """Lo que RATING necesita para tarifar, a partir de las respuestas del ACL Worker.

    `encontrado` es False solo si RUNT respondió que la placa no existe (no si falló). `completo`
    es True si RUNT y Fasecolda respondieron (en vivo o desde caché); si no, la oferta es
    preliminar (regla 4). La siniestralidad que falla se toma como sin dato (`None`)."""
    por_nombre = {f["fuente"]: f for f in fuentes}
    datos = {nombre: (f.get("datos") or {}) for nombre, f in por_nombre.items()}
    estados = {nombre: estado_fuente(f) for nombre, f in por_nombre.items()}
    runt = datos.get(FUENTE_RUNT, {})
    fasecolda = datos.get(FUENTE_FASECOLDA, {})
    siniestralidad = datos.get(FUENTE_SINIESTRALIDAD, {})

    resumen: dict[str, Any] = {
        "encontrado": runt.get("encontrada") is not False,
        "completo": all(estados.get(n) in ("OK", "CACHE") for n in OBLIGATORIAS)
        and runt.get("encontrada") is not False,
        "fuentes": estados,
        "marca": runt.get("marca"),
        "linea": runt.get("linea"),
        "modelo": runt.get("modelo"),
        "clase": runt.get("clase"),
        "servicio": runt.get("servicio"),
        "estado": runt.get("estado"),
        "odometro_km": runt.get("odometro_km"),
        "valor_comercial": fasecolda.get("valor_comercial"),
        "indice_hurto": fasecolda.get("indice_hurto"),
        "siniestros_3_anios": siniestralidad.get("siniestros_3_anios"),
    }
    return resumen


class RepositorioPerfilesVehiculoMongo:
    def __init__(self, cliente: MongoClient, cifrador: CifradorCampos | None) -> None:
        self._cliente = cliente
        base = cliente[NOMBRE_BASE]
        self._perfiles = base[COLECCION]
        self._contadores = base[COLECCION_CONTADORES]
        self._cifrador = cifrador

    def _cifrar(self, valor: dict[str, Any]) -> str | dict[str, Any]:
        texto = json.dumps(valor, ensure_ascii=False, sort_keys=True)
        return self._cifrador.cifrar(texto) if self._cifrador is not None else valor

    def guardar(self, perfil: dict[str, Any]) -> tuple[int, Any]:
        """Inserta una versión nueva del perfil y devuelve la versión y el `operation_time` para la
        sesión causal de RATING (D-02), como el perfil financiero."""
        with self._cliente.start_session() as session:
            contador = self._contadores.find_one_and_update(
                {"_id": f"vehiculo:{perfil['vehiculo_id']}"},
                {"$inc": {"secuencia": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            version = contador["secuencia"]
            documento = {
                "vehiculo_id": perfil["vehiculo_id"],
                "titular_id": perfil["titular_id"],
                "perfil_version": version,
                "completo": perfil["resumen"]["completo"],
                "resumen_cifrado": self._cifrar(perfil["resumen"]),
                "fuentes": cifrar_fuentes_perfil(perfil["fuentes"], self._cifrador),
                "consentimiento_id": perfil["consentimiento_id"],
                "consentimiento_vence_en": perfil.get("consentimiento_vence_en"),
                "creado_en": perfil["creado_en"],
            }
            self._perfiles.insert_one(documento, session=session)
            return version, session.operation_time

    def borrar_de_consentimiento(self, titular_id: str, consentimiento_id: str) -> int:
        resultado = self._perfiles.delete_many(
            {"titular_id": titular_id, "consentimiento_id": consentimiento_id}
        )
        return resultado.deleted_count

    def borrar_vencidos(self, ahora: datetime) -> int:
        return self._perfiles.delete_many({"$or": condiciones_vencido(ahora)}).deleted_count


class ServicioPerfilesVehiculo:
    def __init__(self, repositorio: RepositorioPerfilesVehiculoMongo) -> None:
        self._repositorio = repositorio

    def registrar(
        self,
        vehiculo_id: str,
        titular_id: str,
        consentimiento_id: str,
        fuentes: list[dict[str, Any]],
        consentimiento_vence_en: datetime | None = None,
    ) -> dict[str, Any]:
        resumen = resumir(fuentes)
        version, operation_time = self._repositorio.guardar(
            {
                "vehiculo_id": vehiculo_id,
                "titular_id": titular_id,
                "consentimiento_id": consentimiento_id,
                "consentimiento_vence_en": consentimiento_vence_en,
                "fuentes": fuentes,
                "resumen": resumen,
                "creado_en": datetime.now(UTC),
            }
        )
        logger.info(
            "perfil de vehículo guardado vehiculo=%s version=%d completo=%s",
            id_opaco(vehiculo_id),
            version,
            resumen["completo"],
        )
        return {
            "vehiculo_id": vehiculo_id,
            "perfil_version": version,
            "operation_time": serializar_operation_time(operation_time),
            "completo": resumen["completo"],
            "encontrado": resumen["encontrado"],
        }

    def consentimiento_revocado(self, titular_id: str, consentimiento_id: str) -> int:
        return self._repositorio.borrar_de_consentimiento(titular_id, consentimiento_id)

    def purgar_vencidos(self, ahora: datetime | None = None) -> int:
        return self._repositorio.borrar_vencidos(ahora or datetime.now(UTC))
