import pytest

from app.canales import canales_de


@pytest.mark.parametrize(
    ("ticket", "esperado"),
    [
        ({"rol": "ASESOR", "usuarioId": "u-1"}, ["avisos:asesor:u-1"]),
        ({"rol": "CLIENTE", "usuarioId": "c-1", "titularId": "hmac"}, ["avisos:titular:hmac"]),
        ({"rol": "BACKOFFICE", "usuarioId": "b-1"}, ["avisos:todos", "avisos:cobros"]),
        (
            {"rol": "SOCIO", "usuarioId": "s-1", "entorno": "PRUEBAS"},
            ["avisos:socio:s-1:PRUEBAS"],
        ),
        ({"rol": "SOCIO", "usuarioId": "s-1"}, []),
        ({"rol": "SOCIO", "usuarioId": "s-1", "entorno": "OTRO"}, []),
        ({"rol": "CLIENTE", "usuarioId": "c-1", "titularId": None}, []),
        ({"rol": "ASESOR"}, []),
        ({"rol": "OTRO", "usuarioId": "x"}, []),
        ({}, []),
    ],
)
def test_canales_por_rol(ticket, esperado) -> None:
    assert canales_de(ticket) == esperado
