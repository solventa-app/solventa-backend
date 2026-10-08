"""Pruebas del cache-aside del score (HA-LAT-001) — sin Redis real."""

from app.application.cache_score import CacheScore


class AlmacenFalso:
    def __init__(self) -> None:
        self.valores: dict[str, str] = {}
        self.ttl_recibido: int | None = None

    def get(self, nombre: str) -> str | None:
        return self.valores.get(nombre)

    def set(self, nombre: str, valor: str, ex: int | None = None) -> None:
        self.ttl_recibido = ex
        self.valores[nombre] = valor


class AlmacenQueFalla:
    def get(self, nombre: str) -> None:
        raise ConnectionError("redis caido")

    def set(self, nombre: str, valor: str, ex: int | None = None) -> None:
        raise ConnectionError("redis caido")


def test_guardar_y_obtener_hacen_round_trip() -> None:
    almacen = AlmacenFalso()
    cache = CacheScore(almacen, ttl_segundos=300)

    cache.guardar("oferta:hash:1:v1", {"score_total": 82})

    assert cache.obtener("oferta:hash:1:v1") == {"score_total": 82}
    assert almacen.ttl_recibido == 300


def test_obtener_sin_dato_cacheado_devuelve_none() -> None:
    cache = CacheScore(AlmacenFalso(), ttl_segundos=300)
    assert cache.obtener("oferta:hash:sin-cache:v1") is None


def test_un_fallo_de_redis_nunca_se_propaga() -> None:
    cache = CacheScore(AlmacenQueFalla(), ttl_segundos=300)

    cache.guardar("oferta:hash:1:v1", {"score_total": 82})  # no debe lanzar
    assert cache.obtener("oferta:hash:1:v1") is None  # no debe lanzar
