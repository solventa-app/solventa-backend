"""Persistencia del perfil de riesgo en MongoDB (T-W01-6/7, HU-W01, Bloques 2, 3 y 8).

Capa simple (sin puertos y adaptadores formales — regla de arquitectura #3:
hexagonal es solo para `acl-worker`). RISK es el **único escritor** del
perfil de riesgo (regla de arquitectura #1): este módulo es el único lugar
del sistema que escribe en la colección `perfiles`.

Escribe con el *write concern* por defecto que ya fija `mongo-init`
(`{w: 1}`, deliberadamente NO `majority` — ver `dev/mongo/init-replica-set.js`
y `docs/arquitectura-backend.md`: forzar `majority` aquí ocultaría el lag de
replicación que el experimento D-02 necesita medir). Usa una `ClientSession`
explícita para poder leer `session.operation_time` después de escribir — es
el valor que D-02/HA-LAT-003 exige devolver para que RATING arme su sesión
causal (`afterClusterTime`).

Cifrado de campo (SEG-002/D-04/CA-W01-10/EC022, Bloque 3): antes de insertar
en MongoDB, el diccionario `datos` de cada fuente se cifra con AES-256-GCM en
`datos_cifrados` y `datos` se persiste vacío (`{}`), para que en MongoDB Atlas
M0 nunca quede en disco un solo campo personal o financiero en claro."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import Timestamp
from pymongo import MongoClient, ReturnDocument

from app.application.calculo_score import VERSION_REGLAS, calcular_resultado_score
from app.application.cifrado import CifradorAesGcm, CifradorCampos

NOMBRE_BASE = "risk"
COLECCION_PERFILES = "perfiles"
COLECCION_CONTADORES = "contadores_version"

# W01-A4: un perfil anterior al cambio no sabe cuándo vence su consentimiento. El consentimiento
# dura 30 días, así que se toma ese plazo desde que se creó el perfil.
DIAS_PERFIL_SIN_VENCIMIENTO = 30


def condiciones_vencido(ahora: datetime) -> list[dict[str, Any]]:
    """Cuándo un perfil ya no tiene consentimiento vigente. La comparten la purga y el batch, sin
    preguntar a AUTH perfil por perfil. `None` en Mongo también casa con el campo ausente."""
    return [
        {"consentimiento_vence_en": {"$lte": ahora}},
        {
            "consentimiento_vence_en": None,
            "creado_en": {"$lte": ahora - timedelta(days=DIAS_PERFIL_SIN_VENCIMIENTO)},
        },
    ]


def cifrar_fuentes_perfil(
    fuentes: list[dict[str, Any]], cifrador: CifradorCampos | None
) -> list[dict[str, Any]]:
    """Cifra el diccionario `datos` de cada fuente en `datos_cifrados` y deja
    `datos: {}` en la copia destinada a MongoDB."""
    if cifrador is None:
        return [dict(f) for f in fuentes]

    resultado: list[dict[str, Any]] = []
    for fuente in fuentes:
        copia = dict(fuente)
        datos_en_claro = copia.get("datos") or {}
        copia["datos_cifrados"] = cifrador.cifrar(
            json.dumps(datos_en_claro, ensure_ascii=False, sort_keys=True)
        )
        copia["datos"] = {}
        resultado.append(copia)
    return resultado


class RepositorioPerfilesMongo:
    def __init__(
        self,
        cliente: MongoClient,
        cifrador: CifradorCampos | None = None,
        *,
        cifrar_campos: bool = True,
    ) -> None:
        self._cliente = cliente
        base = cliente[NOMBRE_BASE]
        self._perfiles = base[COLECCION_PERFILES]
        self._contadores = base[COLECCION_CONTADORES]
        if not cifrar_campos:
            self._cifrador: CifradorCampos | None = None
        else:
            self._cifrador = cifrador or CifradorAesGcm.desde_entorno()

    def guardar(
        self,
        cliente_id: str,
        fuentes: list[dict[str, Any]],
        evaluacion: dict[str, Any] | None = None,
        *,
        score_total: int | None = None,
        nivel_riesgo: str | None = None,
        fecha_vigencia: str | None = None,
        version_reglas: str | None = None,
        creado_en: datetime | None = None,
        consentimiento_id: str | None = None,
        consentimiento_vence_en: datetime | None = None,
    ) -> tuple[int, Timestamp]:
        ahora = creado_en or datetime.now(UTC)
        if (
            score_total is None
            or nivel_riesgo is None
            or fecha_vigencia is None
            or version_reglas is None
        ):
            calculado = calcular_resultado_score(fuentes, creado_en=ahora)
            score_total = score_total if score_total is not None else calculado["score_total"]
            nivel_riesgo = nivel_riesgo if nivel_riesgo is not None else calculado["nivel_riesgo"]
            fecha_vigencia = (
                fecha_vigencia if fecha_vigencia is not None else calculado["fecha_vigencia"]
            )
            version_reglas = version_reglas if version_reglas is not None else VERSION_REGLAS

        fuentes_persistidas = cifrar_fuentes_perfil(fuentes, self._cifrador)

        with self._cliente.start_session() as session:
            contador = self._contadores.find_one_and_update(
                {"_id": cliente_id},
                {"$inc": {"secuencia": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            perfil_version = contador["secuencia"]

            documento: dict[str, Any] = {
                "cliente_id": cliente_id,
                "perfil_version": perfil_version,
                "fuentes": fuentes_persistidas,
                "score_total": score_total,
                "nivel_riesgo": nivel_riesgo,
                "fecha_vigencia": fecha_vigencia,
                "version_reglas": version_reglas,
                "creado_en": ahora,
            }
            if consentimiento_id is not None:
                documento["consentimiento_id"] = consentimiento_id
            if consentimiento_vence_en is not None:
                documento["consentimiento_vence_en"] = consentimiento_vence_en
            if evaluacion is not None:
                documento["evaluacion"] = evaluacion
                documento["score"] = evaluacion.get("score")
                documento["clasificacion"] = evaluacion.get("clasificacion")

            self._perfiles.insert_one(documento, session=session)

            operation_time = session.operation_time

        return perfil_version, operation_time

    def borrar_de_consentimiento(
        self, cliente_id: str, consentimiento_id: str
    ) -> list[dict[str, Any]]:
        """W01-A3: borra los perfiles de ese consentimiento y los anteriores al cambio, que no
        dicen de cuál vienen. Un perfil de otro consentimiento vigente del titular no se toca."""
        return self._borrar(
            {
                "cliente_id": cliente_id,
                "$or": [{"consentimiento_id": consentimiento_id}, {"consentimiento_id": None}],
            }
        )

    def borrar_vencidos(self, ahora: datetime) -> list[dict[str, Any]]:
        """W01-A4: borra los perfiles sin consentimiento vigente."""
        return self._borrar({"$or": condiciones_vencido(ahora)})

    def _borrar(self, filtro: dict[str, Any]) -> list[dict[str, Any]]:
        """Devuelve lo borrado para limpiar la caché. `contadores_version` se conserva: la
        versión de un titular no retrocede y solo guarda su id opaco."""
        borrados = list(
            self._perfiles.find(filtro, {"cliente_id": 1, "perfil_version": 1, "version_reglas": 1})
        )
        if borrados:
            self._perfiles.delete_many({"_id": {"$in": [p["_id"] for p in borrados]}})
        return borrados
