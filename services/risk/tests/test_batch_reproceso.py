"""Pruebas del batch nocturno `risk-reproceso` (Bloque 9, HA-ESC-004, EC017)."""

import json
from datetime import UTC, datetime
from typing import Any

from fakes import AlmacenClaveValorFalso

from app.application.cache_score import CacheScore, clave_oferta, clave_score
from app.application.cifrado import CIFRADO_CLAVE_LOCAL, CifradorAesGcm
from app.application.repositorio_perfiles import COLECCION_PERFILES, cifrar_fuentes_perfil
from app.batch_reproceso import ejecutar_reproceso


class CursorFalso:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)

    def sort(self, claves: list[tuple[str, int]]) -> "CursorFalso":
        campo, direccion = claves[0]
        self._docs.sort(key=lambda d: str(d.get(campo, "")), reverse=direccion < 0)
        return self

    def limit(self, n: int) -> "CursorFalso":
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class ColeccionPerfilesBatchFalsa:
    def __init__(self, documentos: list[dict[str, Any]]) -> None:
        self.documentos = [dict(d) for d in documentos]

    def count_documents(self, filtro: dict[str, Any]) -> int:
        objetivo = filtro.get("version_reglas")
        return sum(1 for d in self.documentos if d.get("version_reglas") == objetivo)

    def find(self, filtro: dict[str, Any]) -> CursorFalso:
        condicion = filtro.get("version_reglas", {})
        if isinstance(condicion, dict) and "$ne" in condicion:
            objetivo = condicion["$ne"]
            filtrados = [
                d for d in self.documentos if d.get("version_reglas") != objetivo
            ]
        else:
            filtrados = list(self.documentos)
        return CursorFalso(filtrados)

    def update_one(
        self, filtro: dict[str, Any], actualizacion: dict[str, Any]
    ) -> None:
        for doc in self.documentos:
            if doc.get("_id") == filtro.get("_id"):
                doc.update(actualizacion.get("$set", {}))


class DbBatchFalsa:
    def __init__(self, documentos: list[dict[str, Any]]) -> None:
        self.coleccion = ColeccionPerfilesBatchFalsa(documentos)

    def __getitem__(self, nombre: str) -> ColeccionPerfilesBatchFalsa:
        assert nombre == COLECCION_PERFILES
        return self.coleccion


def _crear_documento_perfil(
    idx: int,
    *,
    version_reglas: str = "v0",
    cifrador: CifradorAesGcm | None = None,
) -> dict[str, Any]:
    fuentes = [
        {
            "fuente": "historial-crediticio",
            "datos": {"score_crediticio": 750},
            "capturado_en": "2026-10-18T00:00:00+00:00",
            "de_cache": False,
            "degradado": False,
        },
        {
            "fuente": "antecedentes-judiciales",
            "datos": {"antecedentes_vigentes": False},
            "capturado_en": "2026-10-18T00:00:00+00:00",
            "de_cache": False,
            "degradado": False,
        },
    ]
    return {
        "_id": f"id-{idx:04d}",
        "cliente_id": f"cliente-{idx}",
        "perfil_version": 1,
        "fuentes": cifrar_fuentes_perfil(fuentes, cifrador),
        "version_reglas": version_reglas,
        "creado_en": datetime(2026, 10, 18, 0, 0, tzinfo=UTC),
    }


def test_reproceso_por_lotes_actualiza_mongo_y_cache_redis() -> None:
    cifrador = CifradorAesGcm.desde_base64(CIFRADO_CLAVE_LOCAL)
    docs = [
        _crear_documento_perfil(i, version_reglas="v0", cifrador=cifrador)
        for i in range(1, 6)
    ]
    db = DbBatchFalsa(docs)
    almacen_redis = AlmacenClaveValorFalso()
    cache = CacheScore(almacen_redis, ttl_segundos=300)

    metricas = ejecutar_reproceso(
        db,
        cache_score=cache,
        cifrador=cifrador,
        version_objetivo="v1",
        tamano_lote=2,
    )

    assert metricas["procesados"] == 5
    assert metricas["omitidos"] == 0
    assert metricas["lotes"] == 3
    assert metricas["version_reglas"] == "v1"
    assert metricas["duracion_s"] > 0
    assert metricas["perfiles_por_segundo"] > 0

    for doc in db[COLECCION_PERFILES].documentos:
        assert doc["version_reglas"] == "v1"
        assert doc["score_total"] > 0
        assert doc["nivel_riesgo"] in ("BAJO", "MEDIO", "ALTO")
        assert doc["fecha_vigencia"].startswith("2026-11-17")
        # Sigue cifrado en reposo en MongoDB
        assert doc["fuentes"][0]["datos"] == {}
        assert "datos_cifrados" in doc["fuentes"][0]

    # También quedó actualizado en la caché de Redis
    en_cache = json.loads(almacen_redis.valores[clave_score("cliente-1", "v1")])
    assert en_cache["score_total"] == db[COLECCION_PERFILES].documentos[0]["score_total"]
    assert clave_oferta("cliente-1", 1, "v1") in almacen_redis.valores


def test_reproceso_es_idempotente_al_interrumpir_y_reanudar() -> None:
    """HA-ESC-004 / EC017: si el batch se interrumpe tras el primer lote y
    vuelve a correr, retoma desde donde iba sin reprocesar los ya actualizados."""
    cifrador = CifradorAesGcm.desde_base64(CIFRADO_CLAVE_LOCAL)
    docs = [
        _crear_documento_perfil(i, version_reglas="v0", cifrador=cifrador)
        for i in range(1, 6)
    ]
    db = DbBatchFalsa(docs)

    # 1) Se interrumpe después de 1 lote de tamaño 2
    parcial = ejecutar_reproceso(
        db,
        cifrador=cifrador,
        version_objetivo="v2",
        tamano_lote=2,
        max_lotes=1,
    )
    assert parcial["procesados"] == 2
    assert parcial["omitidos"] == 0
    assert parcial["lotes"] == 1

    # 2) Al reanudar, omite los 2 ya migrados a v2 y procesa solo los 3 restantes
    reanudado = ejecutar_reproceso(
        db,
        cifrador=cifrador,
        version_objetivo="v2",
        tamano_lote=2,
    )
    assert reanudado["procesados"] == 3
    assert reanudado["omitidos"] == 2
    assert reanudado["lotes"] == 2

    # 3) Una tercera corrida no reprocesa ninguno
    final = ejecutar_reproceso(
        db,
        cifrador=cifrador,
        version_objetivo="v2",
        tamano_lote=2,
    )
    assert final["procesados"] == 0
    assert final["omitidos"] == 5
    assert final["lotes"] == 0
