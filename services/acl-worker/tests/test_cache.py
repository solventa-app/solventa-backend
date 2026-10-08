from datetime import UTC, datetime

from app.application.cache import CacheFuentes
from app.domain.puertos import DatosFuente


class AlmacenFalso:
    """Fake en memoria que cumple el Protocol `PuertoAlmacenClaveValor` sin Redis."""

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
        raise ConnectionError("redis caído")

    def set(self, nombre: str, valor: str, ex: int | None = None) -> None:
        raise ConnectionError("redis caído")


def test_guardar_y_obtener_hacen_round_trip() -> None:
    almacen = AlmacenFalso()
    cache = CacheFuentes(almacen, ttl_segundos=300)
    datos = DatosFuente(
        fuente="cuentas-bancarias", datos={"saldo": 100}, capturado_en=datetime.now(UTC)
    )

    cache.guardar("cuentas-bancarias", "cliente-123", datos)
    recuperado = cache.obtener("cuentas-bancarias", "cliente-123")

    assert recuperado is not None
    assert recuperado.datos == {"saldo": 100}
    assert almacen.ttl_recibido == 300


def test_la_clave_no_contiene_el_cliente_id_en_crudo() -> None:
    almacen = AlmacenFalso()
    cache = CacheFuentes(almacen, ttl_segundos=300)
    datos = DatosFuente(fuente="cuentas-bancarias", datos={}, capturado_en=datetime.now(UTC))

    cache.guardar("cuentas-bancarias", "cliente-123-muy-personal", datos)

    assert all("cliente-123-muy-personal" not in clave for clave in almacen.valores)


def test_obtener_sin_dato_cacheado_devuelve_none() -> None:
    cache = CacheFuentes(AlmacenFalso(), ttl_segundos=300)
    assert cache.obtener("cuentas-bancarias", "cliente-sin-cache") is None


def test_un_fallo_de_redis_nunca_se_propaga() -> None:
    cache = CacheFuentes(AlmacenQueFalla(), ttl_segundos=300)
    datos = DatosFuente(fuente="cuentas-bancarias", datos={}, capturado_en=datetime.now(UTC))

    cache.guardar("cuentas-bancarias", "cliente-1", datos)  # no debe lanzar
    assert cache.obtener("cuentas-bancarias", "cliente-1") is None  # no debe lanzar
