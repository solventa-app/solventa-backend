"""El catálogo del contrato carga, su copia en la imagen es idéntica y los errores se detectan."""

import copy
import json
from pathlib import Path

import pytest

from app.application.catalogo import RUTA_POR_DEFECTO, Catalogo, CatalogoInvalido

CONTRATO = Path(__file__).resolve().parents[3] / "contracts" / "tipos-archivo.json"


def _base() -> dict:
    return json.loads(CONTRATO.read_text(encoding="utf-8"))


def test_la_copia_de_la_imagen_es_identica_al_contrato() -> None:
    assert RUTA_POR_DEFECTO.read_bytes() == CONTRATO.read_bytes()


def test_el_catalogo_del_contrato_es_valido() -> None:
    catalogo = Catalogo.desde_archivo()
    video = catalogo.obtener("onboarding-video")
    assert video is not None and video.retencion_dias == 30
    assert catalogo.obtener("poliza-pdf") is None  # inactivo: no se puede subir
    assert catalogo.definicion("poliza-pdf") is not None  # pero lo existente se sigue leyendo
    assert {t.tipo for t in catalogo.con_retencion()} == {
        "onboarding-documento",
        "onboarding-rostro",
        "onboarding-video",
    }


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("tipo", "Con/Barra"),
        ("tipos_contenido", ["application/x-msdownload"]),
        ("tamano_max_bytes", 0),
        ("quien_sube", ["ADMIN"]),
        ("quien_lee", []),
        ("propietario", "cualquiera"),
        ("retencion_dias", 0),
        ("retencion_dias", True),
    ],
)
def test_detecta_filas_invalidas(campo: str, valor) -> None:
    contenido = _base()
    contenido["tipos"][0][campo] = valor
    with pytest.raises(CatalogoInvalido):
        Catalogo.desde_json(contenido)


def test_detecta_campos_faltantes_tipos_repetidos_y_version() -> None:
    sin_campo = _base()
    del sin_campo["tipos"][0]["quien_lee"]
    with pytest.raises(CatalogoInvalido):
        Catalogo.desde_json(sin_campo)

    repetido = _base()
    repetido["tipos"].append(copy.deepcopy(repetido["tipos"][0]))
    with pytest.raises(CatalogoInvalido):
        Catalogo.desde_json(repetido)

    with pytest.raises(CatalogoInvalido):
        Catalogo.desde_json({**_base(), "version": 2})
