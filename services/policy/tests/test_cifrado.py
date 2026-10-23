import pytest

from app.application.cifrado import CifradorAesGcm, ErrorDeCifrado

LLAVE = bytes(range(32))


def test_cifra_y_descifra() -> None:
    cifrador = CifradorAesGcm(LLAVE)

    cifrado = cifrador.cifrar('{"prima": "185000"}', "propuesta-1")

    assert "185000" not in cifrado
    assert cifrador.descifrar(cifrado, "propuesta-1") == '{"prima": "185000"}'


def test_copiar_el_texto_cifrado_a_otra_propuesta_falla() -> None:
    cifrador = CifradorAesGcm(LLAVE)
    cifrado = cifrador.cifrar("dato", "propuesta-1")

    with pytest.raises(ErrorDeCifrado):
        cifrador.descifrar(cifrado, "propuesta-2")


def test_un_valor_en_claro_no_se_acepta_como_cifrado() -> None:
    with pytest.raises(ErrorDeCifrado):
        CifradorAesGcm(LLAVE).descifrar("dato en claro", "propuesta-1")
