"""El documento a firmar y su hash (HU-W03, CA-W03-02 y CA-W03-03)."""

import pytest

from app.application.documento_firma import (
    DocumentoNoGenerable,
    construir_documento,
    hash_documento,
    serializar,
)

PROPUESTA = {
    "id": "p-1",
    "prima": {"valor": "185000", "moneda": "COP"},
    "cobertura": {"valor": "200000000", "moneda": "COP"},
    "plazo_meses": 60,
}


def test_el_documento_declara_el_monto_los_meses_y_la_forma_de_cobro() -> None:
    documento = construir_documento(PROPUESTA)

    assert documento["monto_mensual"] == {"valor": "185000", "moneda": "COP"}
    assert documento["meses"] == 60
    assert documento["forma_cobro"] == "con la cuota de tu crédito"
    assert "185000 COP al mes durante 60 meses" in documento["resumen"]
    assert "autorizas ese cobro recurrente" in documento["resumen"]


def test_el_resumen_de_vida_deudor_no_cambia_con_el_producto_explicito() -> None:
    # Los documentos de vida ya firmados se verifican por su hash: su texto no puede moverse.
    sin_producto = construir_documento(PROPUESTA)
    con_producto = construir_documento({**PROPUESTA, "producto": "VIDA_DEUDOR"})

    assert sin_producto == con_producto
    assert sin_producto["resumen"].startswith(
        "Seguro de vida para tu crédito hipotecario: prima de"
    )


def test_el_documento_de_auto_habla_de_auto_y_de_cobro_con_tarjeta() -> None:
    documento = construir_documento({**PROPUESTA, "producto": "AUTO"})

    assert documento["resumen"].startswith("Seguro de automóviles todo riesgo: prima de")
    assert "vida" not in documento["resumen"].lower()
    assert "crédito" not in documento["resumen"].lower()
    assert documento["forma_cobro"] == "con tu tarjeta, de forma recurrente"
    assert "185000 COP al mes durante 60 meses" in documento["resumen"]


def test_el_mismo_documento_da_siempre_el_mismo_hash() -> None:
    primero = hash_documento(serializar(construir_documento(PROPUESTA)))
    segundo = hash_documento(serializar(construir_documento(dict(reversed(PROPUESTA.items())))))

    assert primero == segundo
    assert len(primero) == 64


def test_cambiar_la_prima_cambia_el_hash() -> None:
    otra = {**PROPUESTA, "prima": {"valor": "190000", "moneda": "COP"}}

    assert hash_documento(serializar(construir_documento(PROPUESTA))) != hash_documento(
        serializar(construir_documento(otra))
    )


def test_sin_plazo_no_se_puede_armar_el_documento() -> None:
    with pytest.raises(DocumentoNoGenerable):
        construir_documento({**PROPUESTA, "plazo_meses": None})
