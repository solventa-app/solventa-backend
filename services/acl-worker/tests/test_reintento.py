import pytest

from app.application.reintento import con_reintento


def test_no_reintenta_si_el_primer_intento_tiene_exito() -> None:
    llamadas = []

    def accion(presupuesto: float) -> str:
        llamadas.append(presupuesto)
        return "ok"

    assert con_reintento(accion, presupuesto_s=0.7) == "ok"
    assert llamadas == [0.7]


def test_reintenta_una_vez_si_queda_presupuesto() -> None:
    llamadas = []

    def accion(presupuesto: float) -> str:
        llamadas.append(presupuesto)
        if len(llamadas) == 1:
            raise ConnectionError("rechazada")
        return "ok"

    resultado = con_reintento(accion, presupuesto_s=0.7, margen_minimo_s=0.05)
    assert resultado == "ok"
    assert len(llamadas) == 2
    assert llamadas[1] < llamadas[0]  # el segundo intento usa el presupuesto restante


def test_no_reintenta_si_no_queda_presupuesto_suficiente() -> None:
    llamadas = []

    def accion(presupuesto: float) -> str:
        llamadas.append(presupuesto)
        raise TimeoutError("agotado")

    with pytest.raises(TimeoutError):
        con_reintento(accion, presupuesto_s=0.01, margen_minimo_s=0.05)
    assert len(llamadas) == 1  # un timeout real ya consumió todo el presupuesto


def test_relanza_el_error_original_si_el_reintento_tambien_falla() -> None:
    def accion(presupuesto: float) -> str:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        con_reintento(accion, presupuesto_s=0.7)
