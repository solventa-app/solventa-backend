"""Presupuestos por proveedor y techo de la ruta síncrona del usuario (CA-W03-05).

Nada de esto está fijo en el código: todo sale del entorno, y un proveedor nuevo no obliga a tocar
`Configuracion`."""

import pytest

from app.application.configuracion import Configuracion


def _entorno_limpio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quita lo que pueda venir del entorno de quien corre las pruebas."""
    import os

    for nombre in list(os.environ):
        if nombre.startswith("PRESUPUESTO_MS_") or nombre in (
            "TIMEOUT_MS",
            "PLAZO_RUTA_SINCRONA_MS",
            "RESERVA_SALTOS_MS",
            "PROVEEDORES_RUTA_SINCRONA",
        ):
            monkeypatch.delenv(nombre, raising=False)


def test_un_proveedor_nuevo_se_configura_solo_con_su_variable(monkeypatch) -> None:
    _entorno_limpio(monkeypatch)
    monkeypatch.setenv("PRESUPUESTO_MS_PROVEEDOR_NUEVO", "200")

    cfg = Configuracion.desde_entorno()

    assert cfg.presupuesto_proveedor_s("proveedor-nuevo") == 0.2
    assert cfg.presupuesto_proveedor_s("PROVEEDOR_NUEVO") == 0.2


def test_sin_variable_el_presupuesto_es_el_timeout(monkeypatch) -> None:
    _entorno_limpio(monkeypatch)

    cfg = Configuracion.desde_entorno()

    assert cfg.presupuesto_proveedor_s("kyc") == 0.7
    assert cfg.presupuesto_proveedor_s("proveedor-desconocido") == 0.7


def test_el_presupuesto_nunca_supera_el_timeout_duro(monkeypatch) -> None:
    _entorno_limpio(monkeypatch)
    monkeypatch.setenv("PRESUPUESTO_MS_KYC", "5000")

    assert Configuracion.desde_entorno().presupuesto_proveedor_s("kyc") == 0.7


def test_la_firma_cabe_en_el_plazo_del_gateway_menos_la_reserva(monkeypatch) -> None:
    # CA-W03-05: el gateway corta a 700 ms; con 250 ms de reserva para BFF y POLICY, la firma
    # (ruta síncrona del usuario) tiene 450 ms aunque su presupuesto no diga nada.
    _entorno_limpio(monkeypatch)

    cfg = Configuracion.desde_entorno()

    assert cfg.techo_ruta_sincrona_s == 0.45
    assert cfg.presupuesto_proveedor_s("firma") == 0.45


def test_un_presupuesto_de_firma_mas_chico_que_el_techo_se_respeta(monkeypatch) -> None:
    _entorno_limpio(monkeypatch)
    monkeypatch.setenv("PRESUPUESTO_MS_FIRMA", "300")

    assert Configuracion.desde_entorno().presupuesto_proveedor_s("firma") == 0.3


def test_un_presupuesto_de_firma_mayor_que_el_techo_se_acota(monkeypatch) -> None:
    _entorno_limpio(monkeypatch)
    monkeypatch.setenv("PRESUPUESTO_MS_FIRMA", "700")

    assert Configuracion.desde_entorno().presupuesto_proveedor_s("firma") == 0.45


def test_los_proveedores_en_segundo_plano_no_se_acotan(monkeypatch) -> None:
    # El cobro, el KYC y el aviso corren tras responderle al usuario: conservan su presupuesto.
    _entorno_limpio(monkeypatch)

    cfg = Configuracion.desde_entorno()

    assert cfg.presupuesto_proveedor_s("pasarela") == 0.7
    assert cfg.presupuesto_proveedor_s("kyc") == 0.7
    assert cfg.presupuesto_proveedor_s("notificaciones") == 0.7


def test_el_techo_se_mueve_con_el_plazo_y_la_reserva(monkeypatch) -> None:
    _entorno_limpio(monkeypatch)
    monkeypatch.setenv("PLAZO_RUTA_SINCRONA_MS", "1000")
    monkeypatch.setenv("RESERVA_SALTOS_MS", "300")
    monkeypatch.setenv("TIMEOUT_MS", "900")

    assert Configuracion.desde_entorno().presupuesto_proveedor_s("firma") == 0.7


def test_quien_se_declara_en_la_ruta_sincrona_queda_acotado(monkeypatch) -> None:
    _entorno_limpio(monkeypatch)
    monkeypatch.setenv("PROVEEDORES_RUTA_SINCRONA", "firma, kyc")

    cfg = Configuracion.desde_entorno()

    assert cfg.presupuesto_proveedor_s("kyc") == 0.45
    assert cfg.presupuesto_proveedor_s("pasarela") == 0.7


def test_sacar_a_la_firma_de_la_ruta_sincrona_le_devuelve_su_presupuesto(monkeypatch) -> None:
    _entorno_limpio(monkeypatch)
    monkeypatch.setenv("PROVEEDORES_RUTA_SINCRONA", "")

    assert Configuracion.desde_entorno().presupuesto_proveedor_s("firma") == 0.7


@pytest.mark.parametrize(
    ("variable", "valor"),
    [
        ("PRESUPUESTO_MS_FIRMA", "mucho"),
        ("PRESUPUESTO_MS_FIRMA", "0"),
        ("PRESUPUESTO_MS_FIRMA", "-5"),
        ("RESERVA_SALTOS_MS", "700"),
        ("RESERVA_SALTOS_MS", "-1"),
    ],
)
def test_valores_invalidos_se_rechazan_al_arrancar(monkeypatch, variable, valor) -> None:
    _entorno_limpio(monkeypatch)
    monkeypatch.setenv(variable, valor)

    with pytest.raises(ValueError):
        Configuracion.desde_entorno()
