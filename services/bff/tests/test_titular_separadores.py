"""QA 2026-11-10: la póliza que vende el asesor y la cuenta que se crea en el móvil (o al revés) se
asocian por el `titular_id`, el HMAC del documento. Debe ser el mismo aunque el número llegue con
puntos, espacios o guiones (la verificación de identidad lo lee como está impreso). La misma prueba
vive en AUTH, el BFF y onboarding: cada uno tiene su copia del algoritmo."""

import pytest

from app.identificadores import cliente_id_desde_documento, valor_titular

CLAVE = "solo-local-no-secreta"


ESCRITOS = ["1.090.000.001", "1 090 000 001", " 1090000001 ", "1090-000-001"]


@pytest.mark.parametrize("escrito", ESCRITOS)
def test_el_titular_no_depende_de_los_separadores(escrito):
    titular = cliente_id_desde_documento
    assert titular(escrito, CLAVE) == titular("1090000001", CLAVE)


def test_los_titulares_que_ya_existen_no_cambian():
    # Cédula colombiana: solo el número, como antes de T20; otro tipo: PAIS:TIPO:NUMERO.
    assert valor_titular("1090000001") == "1090000001"
    assert valor_titular("1090000001", "CO", "CC") == "1090000001"
    assert valor_titular("ab-123.456", "MX", "PAS") == "MX:PAS:AB123456"
