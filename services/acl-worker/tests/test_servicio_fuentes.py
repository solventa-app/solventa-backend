from datetime import UTC, datetime

import pytest

from app.application.cache import CacheFuentes
from app.application.circuitos import NOMBRE_CIRCUITO_OPEN_FINANCE, construir_fabrica
from app.application.servicio_fuentes import ServicioConsultaFuentes
from app.domain.puertos import ConsultaFuente, DatosFuente

CONSULTA = ConsultaFuente(
    cliente_id="cliente-1", consentimiento_id="consent-1", fuente="cuentas-bancarias"
)


class AdaptadorFalso:
    def __init__(
        self, resultado: DatosFuente | None = None, error: Exception | None = None
    ) -> None:
        self.resultado = resultado
        self.error = error
        self.llamadas = 0

    def consultar(self, consulta: ConsultaFuente, timeout_s: float | None = None) -> DatosFuente:
        self.llamadas += 1
        if self.error is not None:
            raise self.error
        return self.resultado


class AlmacenEnMemoria:
    def __init__(self) -> None:
        self._valores: dict[str, str] = {}

    def get(self, nombre: str) -> str | None:
        return self._valores.get(nombre)

    def set(self, nombre: str, valor: str, ex: int | None = None) -> None:
        self._valores[nombre] = valor


class ProductorReconciliacionFalso:
    """Fake del productor de reconciliación diferida, para verificar cuándo
    `ServicioConsultaFuentes` dispara el encolado (sin levantar Redis/RQ)."""

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, str, str]] = []

    def encolar_si_corresponde(self, fuente: str, cliente_id: str, consentimiento_id: str) -> None:
        self.llamadas.append((fuente, cliente_id, consentimiento_id))


def _servicio(
    adaptador,
    umbral_fallos: int = 3,
    ttl_segundos: float = 10,
    productor_reconciliacion=None,
) -> ServicioConsultaFuentes:
    fabrica = construir_fabrica(umbral_fallos, ttl_segundos)
    cache = CacheFuentes(AlmacenEnMemoria(), ttl_segundos=300)
    return ServicioConsultaFuentes(
        adaptador,
        fabrica,
        NOMBRE_CIRCUITO_OPEN_FINANCE,
        cache,
        presupuesto_s=0.7,
        productor_reconciliacion=productor_reconciliacion,
    )


def test_consulta_exitosa_no_esta_degradada_y_se_cachea() -> None:
    resultado = DatosFuente(
        fuente="cuentas-bancarias", datos={"saldo": 1}, capturado_en=datetime.now(UTC)
    )
    adaptador = AdaptadorFalso(resultado=resultado)
    servicio = _servicio(adaptador, umbral_fallos=10)

    datos = servicio.consultar(CONSULTA)

    assert datos.de_cache is False
    assert datos.degradado is False
    assert datos.datos == {"saldo": 1}

    # El proveedor empieza a fallar: la siguiente consulta debe degradar a la
    # caché que la llamada exitosa anterior ya dejó escrita (mismo servicio,
    # mismo adaptador, solo cambia su comportamiento).
    adaptador.resultado = None
    adaptador.error = ConnectionError("caido")

    degradado = servicio.consultar(CONSULTA)
    assert degradado.de_cache is True
    assert degradado.datos == {"saldo": 1}


def test_fallo_sin_cache_devuelve_valor_minimo_degradado() -> None:
    adaptador = AdaptadorFalso(error=ConnectionError("caido"))
    servicio = _servicio(adaptador)

    datos = servicio.consultar(CONSULTA)

    assert datos.degradado is True
    assert datos.de_cache is False
    assert datos.datos == {}


def test_nunca_propaga_una_excepcion_cruda() -> None:
    adaptador = AdaptadorFalso(error=RuntimeError("lo que sea"))
    servicio = _servicio(adaptador)

    try:
        servicio.consultar(CONSULTA)
    except Exception as error:  # noqa: BLE001 - justo lo que esta prueba verifica
        pytest.fail(f"no debería propagar una excepción cruda: {error}")


def test_circuito_abierto_hace_fail_fast_sin_llamar_al_adaptador() -> None:
    adaptador = AdaptadorFalso(error=ConnectionError("caido"))
    servicio = _servicio(adaptador, umbral_fallos=1, ttl_segundos=10)

    servicio.consultar(CONSULTA)  # 1er fallo (con su reintento interno): abre el circuito
    llamadas_tras_primer_fallo = adaptador.llamadas
    assert llamadas_tras_primer_fallo >= 1

    servicio.consultar(CONSULTA)  # circuito abierto: no debe tocar el adaptador otra vez
    assert adaptador.llamadas == llamadas_tras_primer_fallo


def test_consulta_exitosa_no_encola_reconciliacion() -> None:
    resultado = DatosFuente(
        fuente="cuentas-bancarias", datos={"saldo": 1}, capturado_en=datetime.now(UTC)
    )
    productor = ProductorReconciliacionFalso()
    servicio = _servicio(
        AdaptadorFalso(resultado=resultado), umbral_fallos=10, productor_reconciliacion=productor
    )

    servicio.consultar(CONSULTA)

    assert productor.llamadas == []


def test_fallo_del_adaptador_encola_reconciliacion_con_los_datos_de_la_consulta() -> None:
    productor = ProductorReconciliacionFalso()
    servicio = _servicio(
        AdaptadorFalso(error=ConnectionError("caido")),
        umbral_fallos=10,
        productor_reconciliacion=productor,
    )

    servicio.consultar(CONSULTA)

    assert productor.llamadas == [
        (CONSULTA.fuente, CONSULTA.cliente_id, CONSULTA.consentimiento_id)
    ]


def test_circuito_abierto_tambien_encola_reconciliacion() -> None:
    productor = ProductorReconciliacionFalso()
    servicio = _servicio(
        AdaptadorFalso(error=ConnectionError("caido")),
        umbral_fallos=1,
        ttl_segundos=10,
        productor_reconciliacion=productor,
    )

    servicio.consultar(CONSULTA)  # abre el circuito, encola 1
    servicio.consultar(CONSULTA)  # fail-fast por circuito abierto, encola otra vez

    assert len(productor.llamadas) == 2


def test_consulta_degradada_a_cache_tambien_encola_reconciliacion() -> None:
    resultado = DatosFuente(
        fuente="cuentas-bancarias", datos={"saldo": 1}, capturado_en=datetime.now(UTC)
    )
    adaptador = AdaptadorFalso(resultado=resultado)
    servicio = _servicio(adaptador, umbral_fallos=10)
    servicio.consultar(CONSULTA)  # deja algo bueno en caché

    productor = ProductorReconciliacionFalso()
    servicio._productor_reconciliacion = productor
    adaptador.resultado = None
    adaptador.error = ConnectionError("caido")

    degradado = servicio.consultar(CONSULTA)

    assert degradado.de_cache is True
    assert productor.llamadas == [
        (CONSULTA.fuente, CONSULTA.cliente_id, CONSULTA.consentimiento_id)
    ]
