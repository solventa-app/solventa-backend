"""La firma local produce URLs V4 válidas contra el emulador sin tocar la red."""

from urllib.parse import parse_qs, urlparse

import pytest

from app.application.almacen_objetos import CUENTA_LOCAL, AlmacenGcs


def _almacen() -> AlmacenGcs:
    return AlmacenGcs("archivos-local", "local", "http://localhost:4443")


def test_url_de_subida_firmada_con_los_encabezados_obligatorios() -> None:
    url, encabezados = _almacen().url_subida("tipo/2026/10/abc", "image/jpeg", 5000, 600)

    partes = urlparse(url)
    consulta = parse_qs(partes.query)
    assert f"{partes.scheme}://{partes.netloc}" == "http://localhost:4443"
    assert partes.path == "/archivos-local/tipo/2026/10/abc"
    assert consulta["X-Goog-Algorithm"] == ["GOOG4-RSA-SHA256"]
    assert consulta["X-Goog-Expires"] == ["600"]
    assert CUENTA_LOCAL in consulta["X-Goog-Credential"][0]
    firmados = consulta["X-Goog-SignedHeaders"][0].split(";")
    assert {"content-type", "x-goog-content-length-range", "x-goog-if-generation-match"} <= set(
        firmados
    )
    assert encabezados == {
        "Content-Type": "image/jpeg",
        "x-goog-content-length-range": "0,5000",
        "x-goog-if-generation-match": "0",
    }


def test_url_de_descarga() -> None:
    url = _almacen().url_descarga("tipo/2026/10/abc", 300)
    assert "X-Goog-Signature=" in url and "X-Goog-Expires=300" in url


def test_firmante_desconocido() -> None:
    with pytest.raises(ValueError):
        AlmacenGcs("b", "llave-en-el-repo")
