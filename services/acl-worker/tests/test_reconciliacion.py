"""Pruebas del productor de reconciliación diferida (ampliación de alcance,
ver `docs/sprint-1.md` y `docs/arquitectura-backend.md`): dedup, encolado y
que ningún fallo de Redis/cola se propague (fire-and-forget)."""

from app.application.reconciliacion import (
    ColaReconciliacionRQ,
    MarcaDedupRedis,
    ProductorReconciliacion,
    TrabajoReconciliacion,
)


class AlmacenNXEnMemoria:
    """Fake mínimo de un cliente Redis para `SET ... NX EX` (sin TTL real:
    las pruebas no necesitan esperar 30 s, solo verifican la semántica de
    'ya existe')."""

    def __init__(self) -> None:
        self._claves: set[str] = set()
        self.llamadas: list[tuple[str, str, int | None, bool]] = []

    def set(self, nombre: str, valor: str, ex: int | None = None, nx: bool = False) -> bool | None:
        self.llamadas.append((nombre, valor, ex, nx))
        if nx and nombre in self._claves:
            return None  # ya existía: no se crea de nuevo (semántica real de redis-py)
        self._claves.add(nombre)
        return True

    def expirar_todo(self) -> None:
        self._claves.clear()


class AlmacenNXQueFalla:
    def set(self, nombre: str, valor: str, ex: int | None = None, nx: bool = False) -> None:
        raise ConnectionError("redis caído")


class ColaFalsa:
    def __init__(self) -> None:
        self.trabajos: list[TrabajoReconciliacion] = []

    def encolar(self, trabajo: TrabajoReconciliacion) -> None:
        self.trabajos.append(trabajo)


class ColaQueFalla:
    def encolar(self, trabajo: TrabajoReconciliacion) -> None:
        raise ConnectionError("cola caída")


def test_marca_dedup_primera_vez_permite_encolar() -> None:
    dedup = MarcaDedupRedis(AlmacenNXEnMemoria())
    assert dedup.marcar_si_ausente("cuentas-bancarias", "cliente-1") is True


def test_marca_dedup_segunda_vez_en_la_misma_ventana_no_permite() -> None:
    dedup = MarcaDedupRedis(AlmacenNXEnMemoria())
    assert dedup.marcar_si_ausente("cuentas-bancarias", "cliente-1") is True
    assert dedup.marcar_si_ausente("cuentas-bancarias", "cliente-1") is False


def test_marca_dedup_distingue_por_fuente_y_por_cliente() -> None:
    dedup = MarcaDedupRedis(AlmacenNXEnMemoria())
    assert dedup.marcar_si_ausente("cuentas-bancarias", "cliente-1") is True
    assert dedup.marcar_si_ausente("historial-crediticio", "cliente-1") is True
    assert dedup.marcar_si_ausente("cuentas-bancarias", "cliente-2") is True


def test_marca_dedup_nunca_usa_el_cliente_id_en_crudo_como_clave() -> None:
    almacen = AlmacenNXEnMemoria()
    dedup = MarcaDedupRedis(almacen)
    dedup.marcar_si_ausente("cuentas-bancarias", "cliente-secreto-123")
    clave = almacen.llamadas[0][0]
    assert "cliente-secreto-123" not in clave


def test_marca_dedup_si_redis_falla_prefiere_encolar_de_mas() -> None:
    dedup = MarcaDedupRedis(AlmacenNXQueFalla())
    assert dedup.marcar_si_ausente("cuentas-bancarias", "cliente-1") is True


def test_cola_rq_encola_por_ruta_de_import_con_los_datos_del_trabajo() -> None:
    class ColaRQFalsa:
        def __init__(self) -> None:
            self.llamadas: list[tuple] = []

        def enqueue(self, ruta, **kwargs):
            self.llamadas.append((ruta, kwargs))

    cola_rq = ColaRQFalsa()
    reintentos = object()
    cola = ColaReconciliacionRQ(cola_rq, reintentos)
    trabajo = TrabajoReconciliacion(
        fuente="cuentas-bancarias",
        cliente_id="cliente-1",
        consentimiento_id="consent-1",
        encolado_en=123.0,
    )

    cola.encolar(trabajo)

    ruta, kwargs = cola_rq.llamadas[0]
    assert ruta == "app.tareas.reconciliar_fuente"
    assert kwargs["fuente"] == "cuentas-bancarias"
    assert kwargs["cliente_id"] == "cliente-1"
    assert kwargs["consentimiento_id"] == "consent-1"
    assert kwargs["encolado_en"] == 123.0
    assert kwargs["retry"] is reintentos


def test_productor_encola_en_la_primera_falla_y_deduplica_la_segunda() -> None:
    cola = ColaFalsa()
    dedup = MarcaDedupRedis(AlmacenNXEnMemoria())
    productor = ProductorReconciliacion(cola, dedup)

    productor.encolar_si_corresponde("cuentas-bancarias", "cliente-1", "consent-1")
    productor.encolar_si_corresponde("cuentas-bancarias", "cliente-1", "consent-1")

    assert len(cola.trabajos) == 1
    assert cola.trabajos[0].fuente == "cuentas-bancarias"
    assert cola.trabajos[0].cliente_id == "cliente-1"
    assert cola.trabajos[0].consentimiento_id == "consent-1"


def test_productor_nunca_propaga_un_fallo_de_la_cola() -> None:
    productor = ProductorReconciliacion(ColaQueFalla(), MarcaDedupRedis(AlmacenNXEnMemoria()))
    # no debe lanzar:
    productor.encolar_si_corresponde("cuentas-bancarias", "cliente-1", "consent-1")
