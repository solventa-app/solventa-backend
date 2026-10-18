"""Medios de pago de los clientes (KAN-208): registro, cifrado del token y cobro con ellos."""

import logging

import pytest
from fakes import (
    BusEventosFalso,
    ClienteAclFalso,
    RepositorioCobrosFalso,
    RepositorioMediosPagoFalso,
)
from fastapi.testclient import TestClient

from app.application.cifrado import CifradorAesGcm, ErrorDeCifrado
from app.application.configuracion import CIFRADO_CLAVE_LOCAL, Configuracion
from app.application.medios_pago import (
    MedioPagoInvalido,
    MedioPagoNoUtilizable,
    ServicioMediosPago,
)
from app.application.servicio_cobros import IntentoCobroEnConflicto, ServicioCobros
from app.main import create_app

CIFRADOR = CifradorAesGcm(b"0123456789abcdef0123456789abcdef")
TOKEN = "tok_visa_4242_secreto"
MEDIO = {
    "cliente_id": "u-ana",
    "token": TOKEN,
    "marca": "Visa",
    "ultimos4": "4242",
    "vencimiento_mes": 12,
    "vencimiento_anio": 2030,
}
ORDEN = {
    "id_intento_cobro": "intento-mp-1",
    "poliza_id": "POL-MP-1",
    "cliente_id": "u-ana",
    "monto": "90000.00",
    "moneda": "COP",
}


def _servicios(acl: ClienteAclFalso | None = None):
    repositorio = RepositorioMediosPagoFalso()
    medios = ServicioMediosPago(repositorio, CIFRADOR)
    acl = acl or ClienteAclFalso()
    cobros = ServicioCobros(RepositorioCobrosFalso(), acl, BusEventosFalso(), medios_pago=medios)
    return medios, cobros, acl, repositorio


def _registrar(medios: ServicioMediosPago, **cambios) -> dict:
    return medios.registrar(**{**MEDIO, **cambios})


# ---------- registro


def test_registrar_devuelve_solo_lo_mostrable() -> None:
    medios, *_ = _servicios()

    medio = _registrar(medios)

    assert medio["marca"] == "visa" and medio["ultimos4"] == "4242"
    assert "token" not in medio and "token_cifrado" not in medio and "token_huella" not in medio


def test_el_token_se_guarda_cifrado() -> None:
    medios, _, _, repositorio = _servicios()

    medio = _registrar(medios)

    fila = repositorio.filas[medio["id"]]
    assert fila["token_cifrado"] != TOKEN and TOKEN not in fila["token_cifrado"]
    assert CIFRADOR.descifrar(fila["token_cifrado"], medio["id"]) == TOKEN


def test_registrar_dos_veces_la_misma_tarjeta_es_idempotente() -> None:
    medios, *_ = _servicios()

    primero = _registrar(medios)
    segundo = _registrar(medios)

    assert primero["id"] == segundo["id"]
    assert len(medios.listar_de_cliente("u-ana")) == 1


def test_la_misma_tarjeta_en_otro_cliente_es_otro_medio() -> None:
    medios, *_ = _servicios()

    assert _registrar(medios)["id"] != _registrar(medios, cliente_id="u-beto")["id"]


@pytest.mark.parametrize(
    "cambios",
    [
        {"ultimos4": "42"},
        {"ultimos4": "abcd"},
        {"marca": " "},
        {"marca": "x" * 21},
        {"vencimiento_mes": 13},
        {"vencimiento_anio": 1999},
    ],
)
def test_datos_invalidos(cambios: dict) -> None:
    medios, *_ = _servicios()

    with pytest.raises(MedioPagoInvalido):
        _registrar(medios, **cambios)


def test_listar_los_medios_de_un_cliente() -> None:
    medios, *_ = _servicios()
    _registrar(medios)
    _registrar(medios, token="tok_otra", ultimos4="4444", marca="mastercard")
    _registrar(medios, cliente_id="u-beto")

    assert [m["ultimos4"] for m in medios.listar_de_cliente("u-ana")] == ["4242", "4444"]


# ---------- cobrar con un medio de pago


def test_cobrar_con_medio_pago_usa_su_token_y_lo_vincula() -> None:
    medios, cobros, acl, _ = _servicios()
    medio = _registrar(medios)

    cobro = cobros.crear_cobro({**ORDEN, "medio_pago_id": medio["id"]})

    assert acl.ordenes[0].token_medio_pago == TOKEN
    assert cobro["medio_pago_id"] == medio["id"] and cobro["estado"] == "cobrado"


def test_reenviar_el_mismo_intento_con_el_mismo_medio_no_cobra_dos_veces() -> None:
    medios, cobros, acl, _ = _servicios()
    medio = _registrar(medios)
    orden = {**ORDEN, "medio_pago_id": medio["id"]}

    primero = cobros.crear_cobro(dict(orden))
    segundo = cobros.crear_cobro(dict(orden))

    assert primero["id"] == segundo["id"] and acl.llamadas == 1


def test_el_mismo_intento_con_otro_medio_es_conflicto() -> None:
    medios, cobros, *_ = _servicios()
    uno = _registrar(medios)
    otro = _registrar(medios, token="tok_otra", ultimos4="4444")
    cobros.crear_cobro({**ORDEN, "medio_pago_id": uno["id"]})

    with pytest.raises(IntentoCobroEnConflicto):
        cobros.crear_cobro({**ORDEN, "medio_pago_id": otro["id"]})


def test_no_se_puede_cobrar_con_el_medio_de_otro_cliente() -> None:
    medios, cobros, acl, _ = _servicios()
    medio_de_beto = _registrar(medios, cliente_id="u-beto")

    with pytest.raises(MedioPagoNoUtilizable, match="no existe o no es de este cliente"):
        cobros.crear_cobro({**ORDEN, "medio_pago_id": medio_de_beto["id"]})
    assert acl.llamadas == 0


def test_un_medio_inexistente_da_el_mismo_mensaje_que_uno_ajeno() -> None:
    medios, cobros, *_ = _servicios()
    ajeno = _registrar(medios, cliente_id="u-beto")

    with pytest.raises(MedioPagoNoUtilizable) as ajeno_error:
        cobros.crear_cobro({**ORDEN, "medio_pago_id": ajeno["id"]})
    with pytest.raises(MedioPagoNoUtilizable) as inexistente_error:
        cobros.crear_cobro({**ORDEN, "medio_pago_id": "no-existe"})

    assert str(ajeno_error.value) == str(inexistente_error.value)


def test_un_medio_de_pago_exige_indicar_el_cliente() -> None:
    medios, cobros, *_ = _servicios()
    medio = _registrar(medios)
    sin_cliente = {k: v for k, v in ORDEN.items() if k != "cliente_id"}

    with pytest.raises(MedioPagoNoUtilizable, match="indicar el cliente"):
        cobros.crear_cobro({**sin_cliente, "medio_pago_id": medio["id"]})


@pytest.mark.parametrize("extra", [{}, {"token_medio_pago": "tok_x", "medio_pago_id": "m-1"}])
def test_hay_que_indicar_el_token_o_el_medio_pero_no_ambos_ni_ninguno(extra: dict) -> None:
    _, cobros, *_ = _servicios()

    with pytest.raises(MedioPagoNoUtilizable, match="no ambos ni ninguno"):
        cobros.crear_cobro({**ORDEN, **extra})


def test_el_token_suelto_sigue_funcionando_como_antes() -> None:
    _, cobros, acl, _ = _servicios()

    cobro = cobros.crear_cobro({**ORDEN, "token_medio_pago": "tok_suelto"})

    assert acl.ordenes[0].token_medio_pago == "tok_suelto" and cobro["medio_pago_id"] is None


def test_un_cifrado_copiado_de_otra_fila_no_sirve() -> None:
    medios, cobros, acl, repositorio = _servicios()
    uno = _registrar(medios)
    otro = _registrar(medios, token="tok_otra", ultimos4="4444")
    repositorio.filas[otro["id"]]["token_cifrado"] = repositorio.filas[uno["id"]]["token_cifrado"]

    with pytest.raises(MedioPagoNoUtilizable, match="No se pudo usar"):
        cobros.crear_cobro({**ORDEN, "medio_pago_id": otro["id"]})
    assert acl.llamadas == 0


# ---------- HTTP


def _http() -> tuple[TestClient, ClienteAclFalso]:
    medios, cobros, acl, _ = _servicios()
    return TestClient(create_app(servicio_cobros=cobros, reintento=None, medios_pago=medios)), acl


def test_http_registrar_y_listar_nunca_devuelven_el_token() -> None:
    client, _ = _http()
    with client:
        creado = client.post("/medios-pago", json=MEDIO)
        lista = client.get("/clientes/u-ana/medios-pago")

    assert creado.status_code == 200 and lista.status_code == 200
    assert TOKEN not in creado.text + lista.text
    assert "token" not in creado.json() and lista.json()[0]["ultimos4"] == "4242"


def test_http_datos_invalidos() -> None:
    client, _ = _http()
    with client:
        invalido = client.post("/medios-pago", json={**MEDIO, "ultimos4": "abcd"})
        corto = client.post("/medios-pago", json={**MEDIO, "ultimos4": "42"})

    assert invalido.status_code == 400
    assert corto.status_code == 422


def test_http_cobrar_con_medio_pago_devuelve_marca_y_ultimos4() -> None:
    client, acl = _http()
    with client:
        medio = client.post("/medios-pago", json=MEDIO).json()
        cobro = client.post("/cobros", json={**ORDEN, "medio_pago_id": medio["id"]}).json()
        tabla = client.post("/cobros/buscar", json={"poliza_id": "POL-MP-1"}).json()

    assert cobro["medio_pago"] == {"marca": "visa", "ultimos4": "4242"}
    assert tabla[0]["medio_pago"] == {"marca": "visa", "ultimos4": "4242"}
    assert "medio_pago_id" not in cobro and "token_medio_pago" not in cobro
    assert acl.ordenes[0].token_medio_pago == TOKEN


def test_http_un_cobro_con_token_suelto_no_trae_medio_pago() -> None:
    client, _ = _http()
    with client:
        cobro = client.post("/cobros", json={**ORDEN, "token_medio_pago": "tok_suelto"}).json()

    assert cobro["medio_pago"] is None


def test_http_medio_de_otro_cliente_responde_409() -> None:
    client, acl = _http()
    with client:
        ajeno = client.post("/medios-pago", json={**MEDIO, "cliente_id": "u-beto"}).json()
        r = client.post("/cobros", json={**ORDEN, "medio_pago_id": ajeno["id"]})

    assert r.status_code == 409 and "no existe o no es de este cliente" in r.json()["detail"]
    assert acl.llamadas == 0


def test_http_sin_token_ni_medio_responde_409() -> None:
    client, _ = _http()
    with client:
        r = client.post("/cobros", json=ORDEN)

    assert r.status_code == 409


def test_el_token_no_aparece_en_los_logs(caplog) -> None:
    client, _ = _http()
    with caplog.at_level(logging.DEBUG), client:
        medio = client.post("/medios-pago", json=MEDIO).json()
        client.post("/cobros", json={**ORDEN, "medio_pago_id": medio["id"]})

    assert TOKEN not in caplog.text


# ---------- cifrado y configuración


def test_cifrado_ida_y_vuelta_y_dueno_distinto() -> None:
    cifrado = CIFRADOR.cifrar(TOKEN, "m-1")

    assert TOKEN not in cifrado and CIFRADOR.descifrar(cifrado, "m-1") == TOKEN
    with pytest.raises(ErrorDeCifrado):
        CIFRADOR.descifrar(cifrado, "m-2")


def test_la_llave_local_es_valida_y_es_la_misma_de_auth() -> None:
    CifradorAesGcm.desde_base64(CIFRADO_CLAVE_LOCAL)
    assert CIFRADO_CLAVE_LOCAL == "c29sby1sb2NhbC1ub19zZWNyZXRhLWNpZnJhZG8tMDE="


def test_en_cloud_run_no_arranca_con_la_llave_local(monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "payments")
    monkeypatch.delenv("CIFRADO_CLAVE", raising=False)

    with pytest.raises(ValueError, match="CIFRADO_CLAVE"):
        Configuracion.desde_entorno()


def test_en_cloud_run_arranca_con_una_llave_real(monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "payments")
    monkeypatch.setenv("CIFRADO_CLAVE", "b" * 43 + "=")

    assert Configuracion.desde_entorno().cifrado_clave == "b" * 43 + "="
