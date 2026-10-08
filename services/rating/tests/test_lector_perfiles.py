"""Prueba el mecanismo de fallback de D-02 (secundaria -> primaria) aislado
como función pura, sin necesitar un MongoDB real (ver lector_perfiles.py)."""

import pytest
from pymongo.errors import (
    ExecutionTimeout,
    NetworkTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from app.application.lector_perfiles import leer_con_fallback_a_primaria


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
