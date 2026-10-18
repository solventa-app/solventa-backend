"""Job de reprocesamiento masivo por lotes de perfiles de riesgo (Bloque 9,
HA-ESC-004, EC017).

Ejecutable como `python -m app.batch_reproceso`.
Lee perfiles en lotes (`BATCH_TAMANO_LOTE`, por defecto 500) cuya
`version_reglas` sea distinta de `VERSION_REGLAS` actual (o de
`BATCH_VERSION_REGLAS`), recalcula `score_total`, `nivel_riesgo` y
`fecha_vigencia` (descifrando `datos_cifrados` en memoria si están cifrados) y
actualiza cada documento con el mismo escritor único (RISK) y en la caché de
Redis (`CacheScore`).

Reparto entre tareas: un Cloud Run Job con `task_count = N` lanza N copias con
`CLOUD_RUN_TASK_INDEX` (0..N-1) y `CLOUD_RUN_TASK_COUNT`. Cada tarea toma solo los perfiles cuyo
hash de `cliente_id` cae en su partición (`construir_filtro`): las N tareas no se pisan ni repiten
trabajo y el caudal crece casi lineal con N (es lo que usa la proyección de EC017).

Idempotente: si el job se interrumpe a mitad de camino y vuelve a correr, la
consulta `{"version_reglas": {"$ne": version_objetivo}}` omite los perfiles que
ya quedaron actualizados en `version_objetivo` y retoma desde donde iba."""

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any

from pymongo import MongoClient

from app.application.cache_score import CacheScore
from app.application.calculo_score import VERSION_REGLAS, calcular_resultado_score
from app.application.cifrado import CifradorAesGcm, CifradorCampos
from app.application.configuracion import Configuracion
from app.application.repositorio_perfiles import COLECCION_PERFILES, NOMBRE_BASE

try:
    import redis  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    redis = None  # type: ignore[assignment]

logger = logging.getLogger("risk.batch_reproceso")


def _descifrar_fuentes_en_memoria(
    fuentes: list[dict[str, Any]], cifrador: CifradorCampos | None
) -> list[dict[str, Any]]:
    if cifrador is None:
        return fuentes
    resultado: list[dict[str, Any]] = []
    for fuente in fuentes:
        copia = dict(fuente)
        if not copia.get("datos") and copia.get("datos_cifrados"):
            texto = cifrador.descifrar(copia["datos_cifrados"])
            copia["datos"] = json.loads(texto)
        resultado.append(copia)
    return resultado


def construir_filtro(objetivo: str, particion: tuple[int, int] | None = None) -> dict[str, Any]:
    """Perfiles pendientes de `objetivo`; con `particion=(indice, total)`, solo los de esa tarea.

    La partición se calcula en MongoDB con el hash del `cliente_id` (`$toHashedIndexKey`, MongoDB
    4.4+): las N particiones son disjuntas y cubren todos los perfiles."""
    filtro: dict[str, Any] = {"version_reglas": {"$ne": objetivo}}
    if particion is not None and particion[1] > 1:
        indice, total = particion
        if not 0 <= indice < total:
            raise ValueError("el índice de la partición debe estar entre 0 y total - 1")
        filtro["$expr"] = {
            "$eq": [{"$mod": [{"$abs": {"$toHashedIndexKey": "$cliente_id"}}, total]}, indice]
        }
    return filtro


def particion_desde_entorno() -> tuple[int, int] | None:
    """La partición de esta tarea según las variables que define Cloud Run Jobs (o `None`)."""
    total = int(os.getenv("CLOUD_RUN_TASK_COUNT", "1") or 1)
    if total <= 1:
        return None
    return int(os.getenv("CLOUD_RUN_TASK_INDEX", "0") or 0), total


def ejecutar_reproceso(
    db: Any,
    *,
    cache_score: CacheScore | None = None,
    cifrador: CifradorCampos | None = None,
    version_objetivo: str | None = None,
    tamano_lote: int = 500,
    max_lotes: int | None = None,
    particion: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Reprocesa por lotes los perfiles con `version_reglas != version_objetivo`
    y devuelve las métricas de la corrida."""
    if tamano_lote <= 0:
        raise ValueError("tamano_lote debe ser mayor a 0")

    objetivo = version_objetivo or os.getenv("BATCH_VERSION_REGLAS", "").strip() or VERSION_REGLAS
    cifrador_activo = cifrador or CifradorAesGcm.desde_entorno()
    coleccion = db[COLECCION_PERFILES]

    inicio = time.perf_counter()
    procesados = 0
    omitidos = 0
    lotes = 0

    if hasattr(coleccion, "count_documents"):
        try:
            omitidos = int(coleccion.count_documents({"version_reglas": objetivo}))
        except Exception:
            omitidos = 0

    vistos_omitidos: set[Any] = set()

    while True:
        if max_lotes is not None and lotes >= max_lotes:
            break

        cursor = coleccion.find(construir_filtro(objetivo, particion))
        if hasattr(cursor, "sort"):
            cursor = cursor.sort([("_id", 1)])
        if hasattr(cursor, "limit"):
            cursor = cursor.limit(tamano_lote)

        candidatos = list(cursor)
        pendientes: list[dict[str, Any]] = []
        for doc in candidatos:
            doc_id = doc.get("_id")
            if doc.get("version_reglas") == objetivo:
                if doc_id not in vistos_omitidos:
                    vistos_omitidos.add(doc_id)
                    if not hasattr(coleccion, "count_documents"):
                        omitidos += 1
                continue
            pendientes.append(doc)
            if len(pendientes) >= tamano_lote:
                break

        if not pendientes:
            break

        lotes += 1
        ahora = datetime.now(UTC)
        for doc in pendientes:
            fuentes_claras = _descifrar_fuentes_en_memoria(
                doc.get("fuentes") or [], cifrador_activo
            )
            creado_en = doc.get("creado_en")
            if isinstance(creado_en, str):
                try:
                    creado_en = datetime.fromisoformat(creado_en)
                except ValueError:
                    creado_en = ahora

            resultado = calcular_resultado_score(
                fuentes_claras,
                creado_en=creado_en if isinstance(creado_en, datetime) else ahora,
                version_reglas=objetivo,
            )
            coleccion.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "score_total": resultado["score_total"],
                        "nivel_riesgo": resultado["nivel_riesgo"],
                        "fecha_vigencia": resultado["fecha_vigencia"],
                        "version_reglas": objetivo,
                        "reprocesado_en": ahora,
                    }
                },
            )
            if cache_score is not None:
                cache_score.guardar_score_cliente(
                    doc["cliente_id"],
                    int(doc["perfil_version"]),
                    resultado,
                    version_reglas=objetivo,
                )
            procesados += 1

    duracion_s = max(time.perf_counter() - inicio, 1e-6)
    perfiles_por_segundo = round(procesados / duracion_s, 2)
    metricas = {
        "procesados": procesados,
        "omitidos": omitidos,
        "lotes": lotes,
        "version_reglas": objetivo,
        "duracion_s": round(duracion_s, 6),
        "perfiles_por_segundo": perfiles_por_segundo,
    }
    logger.info(
        "batch_reproceso completado procesados=%d omitidos=%d lotes=%d "
        "version_reglas=%s duracion_s=%.4f perfiles_por_segundo=%.2f",
        procesados,
        omitidos,
        lotes,
        objetivo,
        metricas["duracion_s"],
        perfiles_por_segundo,
    )
    return metricas


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = Configuracion.desde_entorno()
    nombre_db = os.getenv("MONGO_DB", NOMBRE_BASE)
    cliente_mongo = MongoClient(config.mongo_uri)
    cliente_redis = None
    try:
        cifrador = CifradorAesGcm.desde_entorno(
            cifrado_clave=config.cifrado_clave,
            kms_key_name=config.kms_key_name,
            cifrado_clave_envuelta=config.cifrado_clave_envuelta,
        )
        if redis is not None and config.redis_url:
            try:
                cliente_redis = redis.Redis.from_url(config.redis_url)
            except Exception:
                cliente_redis = None
        cache = CacheScore(cliente_redis, config.score_cache_ttl_segundos)
        ejecutar_reproceso(
            cliente_mongo[nombre_db],
            cache_score=cache,
            cifrador=cifrador,
            tamano_lote=config.batch_tamano_lote,
            particion=particion_desde_entorno(),
        )
        return 0
    finally:
        cliente_mongo.close()
        if cliente_redis is not None:
            try:
                cliente_redis.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
