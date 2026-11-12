import pytest

from app.plantillas import PlantillaDesconocida, Plantillas

CATALOGO = {
    "indemnizacion-liquidada": {
        "ttlSegundos": 86400,
        "silenciosa": False,
        "es": {
            "titulo": "Tu indemnización ya está lista",
            "cuerpo": "Se liquidaron {monto} {moneda}.",
        },
        "en": {"titulo": "Your payout is ready", "cuerpo": "{monto} {moneda} was paid out."},
    },
    "sesion-cerrada": {
        "ttlSegundos": 300,
        "silenciosa": True,
        "es": {"titulo": "", "cuerpo": ""},
        "en": {"titulo": "", "cuerpo": ""},
    },
}


def test_resuelve_con_variables() -> None:
    plantillas = Plantillas(CATALOGO)
    mensaje = plantillas.resolver("indemnizacion-liquidada", {"monto": "500000", "moneda": "COP"})
    assert mensaje["titulo"] == "Tu indemnización ya está lista"
    assert mensaje["cuerpo"] == "Se liquidaron 500000 COP."
    assert mensaje["ttl_segundos"] == 86400
    assert mensaje["silenciosa"] is False


def test_variable_ausente_deja_el_texto_literal() -> None:
    plantillas = Plantillas(CATALOGO)
    mensaje = plantillas.resolver("indemnizacion-liquidada", {"monto": "500000"})
    assert mensaje["cuerpo"] == "Se liquidaron {monto} {moneda}."


def test_sin_variables() -> None:
    plantillas = Plantillas(CATALOGO)
    mensaje = plantillas.resolver("sesion-cerrada", None)
    assert mensaje["silenciosa"] is True


def test_idioma_no_disponible_cae_a_es() -> None:
    plantillas = Plantillas(CATALOGO)
    variables = {"monto": "1", "moneda": "COP"}
    mensaje = plantillas.resolver("indemnizacion-liquidada", variables, idioma="fr")
    assert mensaje["titulo"] == "Tu indemnización ya está lista"


def test_idioma_disponible_se_usa() -> None:
    plantillas = Plantillas(CATALOGO)
    variables = {"monto": "1", "moneda": "USD"}
    mensaje = plantillas.resolver("indemnizacion-liquidada", variables, idioma="en")
    assert mensaje["titulo"] == "Your payout is ready"


def test_plantilla_desconocida() -> None:
    plantillas = Plantillas(CATALOGO)
    with pytest.raises(PlantillaDesconocida):
        plantillas.resolver("no-existe", None)


def test_carga_el_catalogo_real_del_contrato() -> None:
    plantillas = Plantillas()
    mensaje = plantillas.resolver("indemnizacion-liquidada", {"monto": "1", "moneda": "COP"})
    assert mensaje["titulo"]
