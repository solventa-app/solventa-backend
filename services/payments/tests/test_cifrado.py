"""Cifrado por campo del `token_medio_pago` (HA-SEG-002, EC022)."""

import base64

import pytest

from app.application.cifrado import (
    CIFRADO_CLAVE_LOCAL,
    PREFIJO,
    VALOR_MARCADOR,
    CifradorAesGcm,
    ErrorDeCifrado,
    cifrador_desde_entorno,
)
from app.application.repositorio_cobros import RepositorioCobrosPostgres

LLAVE = bytes(range(32))
OTRA_LLAVE = bytes(range(1, 33))


def test_cifra_y_descifra_con_el_mismo_dueno() -> None:
    cifrador = CifradorAesGcm(LLAVE)
    cifrado = cifrador.cifrar("tok_visa_0001", "cobro-1")

    assert cifrado.startswith(PREFIJO)
    assert "tok_visa_0001" not in cifrado
    assert cifrador.descifrar(cifrado, "cobro-1") == "tok_visa_0001"


def test_el_mismo_texto_cifra_distinto_cada_vez() -> None:
    cifrador = CifradorAesGcm(LLAVE)

    primero = cifrador.cifrar("tok", "cobro-1")
    segundo = cifrador.cifrar("tok", "cobro-1")

    assert primero != segundo


def test_copiar_el_cifrado_a_otro_cobro_falla() -> None:
    cifrador = CifradorAesGcm(LLAVE)
    cifrado = cifrador.cifrar("tok_visa_0001", "cobro-1")

    with pytest.raises(ErrorDeCifrado):
        cifrador.descifrar(cifrado, "cobro-2")


def test_sin_la_llave_correcta_no_se_lee() -> None:
    cifrado = CifradorAesGcm(LLAVE).cifrar("tok_visa_0001", "cobro-1")

    with pytest.raises(ErrorDeCifrado):
        CifradorAesGcm(OTRA_LLAVE).descifrar(cifrado, "cobro-1")


def test_una_fila_anterior_en_claro_se_devuelve_tal_cual() -> None:
    assert CifradorAesGcm(LLAVE).descifrar("tok_visa_0001", "cobro-1") == "tok_visa_0001"


def test_cifrar_dos_veces_no_cifra_encima() -> None:
    cifrador = CifradorAesGcm(LLAVE)
    cifrado = cifrador.cifrar("tok", "cobro-1")

    assert cifrador.cifrar(cifrado, "cobro-1") == cifrado


def test_la_llave_debe_tener_32_bytes() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        CifradorAesGcm(b"corta")
    with pytest.raises(ValueError, match="CIFRADO_CLAVE"):
        CifradorAesGcm.desde_base64("no es base64 de 32 bytes!")


def test_en_cloud_run_la_llave_local_o_el_marcador_no_valen(monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "payments")
    for valor in (CIFRADO_CLAVE_LOCAL, VALOR_MARCADOR):
        monkeypatch.setenv("CIFRADO_CLAVE", valor)
        with pytest.raises(ValueError, match="Secret Manager"):
            cifrador_desde_entorno()

    monkeypatch.setenv("CIFRADO_CLAVE", base64.urlsafe_b64encode(LLAVE).decode())
    assert (
        cifrador_desde_entorno().descifrar(CifradorAesGcm(LLAVE).cifrar("tok", "c"), "c") == "tok"
    )


def test_en_local_se_usa_la_llave_de_pruebas(monkeypatch) -> None:
    for nombre in ("K_SERVICE", "CLOUD_RUN_JOB", "CIFRADO_CLAVE"):
        monkeypatch.delenv(nombre, raising=False)

    assert cifrador_desde_entorno() is not None


# --- El repositorio guarda cifrado y devuelve en claro -------------------------------------------


class _Resultado:
    def __init__(self, fila: dict | None) -> None:
        self._fila = fila

    def fetchone(self) -> dict | None:
        return self._fila


class _Conexion:
    def __init__(self, pool: "_PoolFalso") -> None:
        self._pool = pool

    def __enter__(self) -> "_Conexion":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, _sql: str, parametros: dict) -> _Resultado:
        self._pool.parametros.append(parametros)
        return _Resultado(self._pool.fila_guardada(parametros))


class _PoolFalso:
    """Imita el INSERT ... RETURNING: devuelve la fila tal como quedaría guardada."""

    def __init__(self) -> None:
        self.parametros: list[dict] = []

    def connection(self) -> _Conexion:
        return _Conexion(self)

    @staticmethod
    def fila_guardada(p: dict) -> dict:
        return {
            "id": p["id"],
            "id_intento_cobro": p["id_intento_cobro"],
            "poliza_id": p["poliza_id"],
            "cliente_id": None,
            "monto": p["monto"],
            "moneda": p["moneda"],
            "token_medio_pago": p["token_medio_pago"],
            "estado": "cobrando",
        }


def test_el_repositorio_guarda_el_token_cifrado_y_lo_devuelve_en_claro() -> None:
    pool = _PoolFalso()
    repositorio = RepositorioCobrosPostgres(pool, CifradorAesGcm(LLAVE))  # type: ignore[arg-type]
    orden = {
        "id_intento_cobro": "intento-1",
        "poliza_id": "poliza-1",
        "monto": "10.00",
        "moneda": "COP",
        "token_medio_pago": "tok_visa_0001",
    }

    cobro, creado = repositorio.crear_o_obtener("cobro-1", orden)

    assert creado is True
    guardado = pool.parametros[0]["token_medio_pago"]
    assert guardado.startswith(PREFIJO) and "tok_visa_0001" not in guardado
    assert cobro["token_medio_pago"] == "tok_visa_0001"
