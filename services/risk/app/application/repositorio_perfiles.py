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
from datetime import UTC, datetime
from typing import Any

from bson import Timestamp
from pymongo import MongoClient, ReturnDocument

from app.application.calculo_score import VERSION_REGLAS, calcular_resultado_score
from app.application.cifrado import CifradorAesGcm, CifradorCampos

NOMBRE_BASE = "risk"
COLECCION_PERFILES = "perfiles"
COLECCION_CONTADORES = "contadores_version"


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
            if evaluacion is not None:
                documento["evaluacion"] = evaluacion
                documento["score"] = evaluacion.get("score")
                documento["clasificacion"] = evaluacion.get("clasificacion")

            self._perfiles.insert_one(documento, session=session)

            operation_time = session.operation_time

        return perfil_version, operation_time
