"""Prueba el mecanismo de fallback de D-02 (secundaria -> primaria) aislado
como función pura, sin necesitar un MongoDB real (ver lector_perfiles.py)."""

import base64
import json
import os

import pytest
from pymongo.errors import (
    ExecutionTimeout,
    NetworkTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from app.application.cifrado import (
    CIFRADO_CLAVE_LOCAL,
    CifradorAesGcm,
    ErrorDeCifrado,
)
from app.application.lector_perfiles import (
    descifrar_fuentes_perfil,
    leer_con_fallback_a_primaria,
)


def test_usa_la_secundaria_cuando_responde_a_tiempo() -> None:
    resultado, de_secundaria = leer_con_fallback_a_primaria(
        leer_de_secundaria=lambda: {"origen": "secundaria"},
        leer_de_primaria=lambda: pytest.fail("no debió llamar a la primaria"),
    )

    assert resultado == {"origen": "secundaria"}
    assert de_secundaria is True


def test_cae_a_la_primaria_si_la_secundaria_agota_maxtimems() -> None:
    resultado, de_secundaria = leer_con_fallback_a_primaria(
        leer_de_secundaria=lambda: (_ for _ in ()).throw(ExecutionTimeout("maxTimeMS agotado")),
        leer_de_primaria=lambda: {"origen": "primaria"},
    )

    assert resultado == {"origen": "primaria"}
    assert de_secundaria is False


def test_cae_a_la_primaria_tambien_con_operation_failure() -> None:
    resultado, de_secundaria = leer_con_fallback_a_primaria(
        leer_de_secundaria=lambda: (_ for _ in ()).throw(OperationFailure("fallo")),
        leer_de_primaria=lambda: {"origen": "primaria"},
    )

    assert resultado == {"origen": "primaria"}
    assert de_secundaria is False


def test_cae_a_la_primaria_si_la_secundaria_es_inalcanzable() -> None:
    """Secundaria INALCANZABLE (no solo atrasada): `ServerSelectionTimeoutError`
    también debe disparar el fallback (hueco cerrado tras la verificación en
    vivo con `docker pause` sobre `mongo2`, ver README.md)."""
    resultado, de_secundaria = leer_con_fallback_a_primaria(
        leer_de_secundaria=lambda: (_ for _ in ()).throw(
            ServerSelectionTimeoutError("sin secundaria disponible")
        ),
        leer_de_primaria=lambda: {"origen": "primaria"},
    )

    assert resultado == {"origen": "primaria"}
    assert de_secundaria is False


def test_cae_a_la_primaria_si_el_socket_a_la_secundaria_se_cuelga() -> None:
    """Secundaria que la topología creía sana pero el socket se cuelga al
    usarla (`docker pause`, no un cierre limpio): pymongo lanza
    `NetworkTimeout` (subclase de `AutoReconnect`), confirmado en vivo."""
    resultado, de_secundaria = leer_con_fallback_a_primaria(
        leer_de_secundaria=lambda: (_ for _ in ()).throw(
            NetworkTimeout("mongo2:27017: timed out")
        ),
        leer_de_primaria=lambda: {"origen": "primaria"},
    )

    assert resultado == {"origen": "primaria"}
    assert de_secundaria is False


def test_no_atrapa_errores_que_no_son_de_timeout() -> None:
    with pytest.raises(ValueError):
        leer_con_fallback_a_primaria(
            leer_de_secundaria=lambda: (_ for _ in ()).throw(ValueError("otra cosa")),
            leer_de_primaria=lambda: pytest.fail("no debió llamar a la primaria"),
        )


def test_ec022_descifrar_fuentes_perfil_restaura_datos_en_memoria() -> None:
    cifrador = CifradorAesGcm.desde_base64(CIFRADO_CLAVE_LOCAL)
    fuentes_en_disco = [
        {
            "fuente": "historial-crediticio",
            "datos": {},
            "datos_cifrados": cifrador.cifrar(
                json.dumps({"score_crediticio": 712, "obligaciones_vigentes": 1})
            ),
            "capturado_en": "2026-10-18T12:00:00+00:00",
            "de_cache": False,
            "degradado": False,
        }
    ]

    descifradas = descifrar_fuentes_perfil(fuentes_en_disco, cifrador)

    assert len(descifradas) == 1
    assert "datos_cifrados" not in descifradas[0]
    assert descifradas[0]["datos"] == {
        "score_crediticio": 712,
        "obligaciones_vigentes": 1,
    }


def test_ec022_descifrar_fuentes_perfil_falla_con_llave_incorrecta_o_kms_deshabilitado() -> None:
    cifrador_risk = CifradorAesGcm(os.urandom(32))
    cifrador_incorrecto = CifradorAesGcm(os.urandom(32))
    fuentes_en_disco = [
        {
            "fuente": "historial-crediticio",
            "datos": {},
            "datos_cifrados": cifrador_risk.cifrar('{"score_crediticio": 712}'),
            "capturado_en": "2026-10-18T12:00:00+00:00",
            "de_cache": False,
            "degradado": False,
        }
    ]

    with pytest.raises(ErrorDeCifrado):
        descifrar_fuentes_perfil(fuentes_en_disco, cifrador_incorrecto)

    class KmsDeshabilitado:
        def decrypt(self, request: dict) -> dict:
            raise RuntimeError("CryptoKeyVersion is DISABLED")

    with pytest.raises(ErrorDeCifrado):
        CifradorAesGcm.desde_kms(
            "projects/solventa/locations/us-central1/keyRings/kr/cryptoKeys/campos",
            base64.urlsafe_b64encode(b"sobre").decode("ascii"),
            cliente_kms=KmsDeshabilitado(),
        )

