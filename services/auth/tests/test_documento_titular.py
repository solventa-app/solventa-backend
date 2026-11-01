"""Documento y titular del CLIENTE (HU-W03, W03-27, W03-34 y W03-36): obligatorio al crearlo,
cifrado por campo, el mismo `titular_id` del consentimiento, único, enmascarado para el propio
usuario y en claro solo por el endpoint privado de identidad."""

import hashlib
import hmac
import logging

import pytest
from fakes import (
    CIFRADOR_PRUEBAS,
    CLAVE_TITULAR_PRUEBAS,
    HASHER_RAPIDO,
    RepositorioUsuariosMemoria,
)
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app
from app.sesiones import ServicioSesiones
from app.usuarios import (
    DatoInvalido,
    ServicioUsuarios,
    UsuarioDuplicado,
    contexto_documento,
    enmascarar_documento,
    normalizar_documento,
)

CLAVE = "clave-segura-1"
CORREO = "ana.perez@ejemplo.co"
DOCUMENTO = "1020304050"


def _servicio() -> tuple[ServicioUsuarios, RepositorioUsuariosMemoria]:
    repositorio = RepositorioUsuariosMemoria()
    servicio = ServicioUsuarios(repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS)
    return servicio, repositorio


def _titular_como_el_bff(documento: str) -> str:
    """Copia de `cliente_id_desde_documento` del BFF (`services/bff/app/identificadores.py`)."""
    return hmac.new(
        CLAVE_TITULAR_PRUEBAS.encode(), documento.strip().upper().encode(), hashlib.sha256
    ).hexdigest()


def _cliente(servicio: ServicioUsuarios, alias: str = "cliente.uno", documento: str = DOCUMENTO):
    return servicio.crear(alias, CLAVE, "CLIENTE", correo=CORREO, documento=documento)


# ---------- validación


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [("1020304050", "1020304050"), ("  1020304050 ", "1020304050"), ("ab123456", "AB123456")],
)
def test_el_documento_se_normaliza(entrada: str, esperado: str) -> None:
    assert normalizar_documento(entrada) == esperado


@pytest.mark.parametrize("documento", ["", "1234", "10.203.040", "10 20 30", "x" * 21, "12-345678"])
def test_documentos_invalidos(documento: str) -> None:
    with pytest.raises(DatoInvalido, match="documento de identidad no es válido"):
        normalizar_documento(documento)


@pytest.mark.parametrize(
    ("documento", "esperado"),
    [
        ("123456", "••3456"),  # 6: solo los últimos 4, longitud conservada
        ("1234567", "12•4567"),  # 7: ya aplica 2 + 4
        ("1020304050", "10••••4050"),
        ("AB123456", "AB••3456"),  # pasaporte con letras
        ("A1234567X", "A1•••567X"),
        ("12345", "•2345"),
        ("1234", "1234"),
    ],
)
def test_enmascarar_conserva_longitud(documento: str, esperado: str) -> None:
    resultado = enmascarar_documento(documento)
    assert resultado == esperado
    assert len(resultado) == len(documento)


# ---------- crear


def test_el_cliente_queda_con_el_titular_del_consentimiento_y_el_documento_cifrado() -> None:
    servicio, repositorio = _servicio()

    creado = _cliente(servicio, documento=" 1020304050 ")
    fila = repositorio.filas[creado["id"]]

    assert fila["titular_id"] == _titular_como_el_bff("1020304050")
    assert DOCUMENTO not in str(fila.values())
    assert (
        CIFRADOR_PRUEBAS.descifrar(fila["documento_cifrado"], contexto_documento(creado["id"]))
        == DOCUMENTO
    )
    assert "documento" not in str(creado) and "titular" not in str(creado)


@pytest.mark.parametrize(
    "faltante",
    [{"correo": CORREO}, {"documento": DOCUMENTO}, {}],
    ids=["sin-doc", "sin-correo", "nada"],
)
def test_un_cliente_sin_documento_o_sin_correo_no_se_crea(faltante: dict) -> None:
    servicio, repositorio = _servicio()

    with pytest.raises(DatoInvalido, match="documento de identidad y su correo"):
        servicio.crear("cliente.uno", CLAVE, "CLIENTE", **faltante)
    assert repositorio.filas == {}


@pytest.mark.parametrize("rol", ["ASESOR", "BACKOFFICE"])
def test_solo_los_clientes_guardan_documento(rol: str) -> None:
    servicio, _ = _servicio()

    with pytest.raises(DatoInvalido, match="solo se guarda para clientes"):
        servicio.crear("otro.rol", CLAVE, rol, documento=DOCUMENTO)


def test_un_documento_repetido_no_crea_otro_cliente() -> None:
    servicio, repositorio = _servicio()
    _cliente(servicio, "cliente.uno", "ab123456")

    with pytest.raises(UsuarioDuplicado, match="cliente con ese documento"):
        _cliente(servicio, "cliente.dos", " AB123456")
    assert len(repositorio.filas) == 1


def test_repetir_el_mismo_alta_dice_que_el_alias_existe() -> None:
    servicio, _ = _servicio()
    _cliente(servicio)

    with pytest.raises(UsuarioDuplicado, match="usuario con ese alias"):
        _cliente(servicio)


def test_sin_clave_del_titular_no_se_guarda_el_documento() -> None:
    servicio = ServicioUsuarios(RepositorioUsuariosMemoria(), HASHER_RAPIDO, CIFRADOR_PRUEBAS)

    with pytest.raises(DatoInvalido, match="no está configurado para guardar el documento"):
        _cliente(servicio)


# ---------- lectura


def test_documento_enmascarado_e_identidad_del_propio_cliente() -> None:
    servicio, _ = _servicio()
    creado = _cliente(servicio)

    assert servicio.documento_enmascarado_de(creado["id"]) == "10••••4050"
    assert servicio.identidad_de(creado["id"]) == {
        "titular_id": _titular_como_el_bff(DOCUMENTO),
        "documento": DOCUMENTO,
    }


def test_sin_documento_no_hay_mascara_ni_identidad() -> None:
    servicio, repositorio = _servicio()
    asesor = servicio.crear("asesor.uno", CLAVE, "ASESOR")
    anterior = _cliente(servicio)
    # Un CLIENTE anterior a la 0008 no tiene titular ni documento.
    repositorio.filas[anterior["id"]].update(titular_id=None, documento_cifrado=None)

    for usuario_id in (asesor["id"], anterior["id"], "no-existe"):
        assert servicio.documento_enmascarado_de(usuario_id) is None
        assert servicio.identidad_de(usuario_id) is None


def test_un_documento_danado_no_sale_y_no_se_loguea(caplog) -> None:
    servicio, repositorio = _servicio()
    creado = _cliente(servicio)
    repositorio.filas[creado["id"]]["documento_cifrado"] = "esto-no-es-un-cifrado"

    with caplog.at_level(logging.WARNING):
        assert servicio.identidad_de(creado["id"]) is None
        assert servicio.documento_enmascarado_de(creado["id"]) is None
    assert "esto-no-es" not in caplog.text


# ---------- HTTP


def _http() -> TestClient:
    repositorio = RepositorioUsuariosMemoria()
    config = Configuracion("postgresql://no-se-usa", "secreto-de-prueba-con-32-bytes-o-mas", 60)
    app = create_app(
        servicio_usuarios=ServicioUsuarios(
            repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS
        ),
        servicio_sesiones=ServicioSesiones(repositorio, HASHER_RAPIDO, config.jwt_secreto, 60),
        config=config,
    )
    return TestClient(app)


def _crear(client: TestClient, alias: str = "cliente.uno", **extra):
    cuerpo = {"usuario": alias, "clave": CLAVE, "rol": "CLIENTE", "correo": CORREO}
    return client.post("/usuarios", json={**cuerpo, **extra})


def test_http_crear_valida_el_documento() -> None:
    with _http() as client:
        sin = _crear(client)
        invalido = _crear(client, documento_identidad="10.203")
        bueno = _crear(client, documento_identidad=DOCUMENTO)
        repetido = _crear(client, "cliente.dos", documento_identidad=DOCUMENTO)

    assert sin.status_code == 400 and invalido.status_code == 400
    assert bueno.status_code == 200 and DOCUMENTO not in bueno.text
    assert repetido.status_code == 409


def test_http_la_mascara_sale_solo_al_propio_usuario() -> None:
    with _http() as client:
        creado = _crear(client, documento_identidad=DOCUMENTO).json()
        sesion = client.post("/sesiones", json={"usuario": "cliente.uno", "clave": CLAVE}).json()
        propio = client.get(f"/usuarios/{creado['id']}").json()
        lista = client.get("/usuarios").json()

    assert creado["documento_enmascarado"] is None
    assert sesion["usuario"]["documento_enmascarado"] == "10••••4050"
    assert propio["documento_enmascarado"] == "10••••4050"
    assert all(u["documento_enmascarado"] is None for u in lista)


def test_http_identidad_solo_de_un_cliente_verificado() -> None:
    with _http() as client:
        creado = _crear(client, documento_identidad=DOCUMENTO).json()
        asesor = client.post(
            "/usuarios", json={"usuario": "asesor.uno", "clave": CLAVE, "rol": "ASESOR"}
        ).json()
        identidad = client.get(f"/usuarios/{creado['id']}/identidad")
        de_asesor = client.get(f"/usuarios/{asesor['id']}/identidad")
        inexistente = client.get("/usuarios/no-existe/identidad")

    assert identidad.status_code == 200
    assert identidad.json() == {
        "titular_id": _titular_como_el_bff(DOCUMENTO),
        "documento": DOCUMENTO,
    }
    assert de_asesor.status_code == 404 and inexistente.status_code == 404


def test_los_logs_no_llevan_el_documento(caplog) -> None:
    with caplog.at_level(logging.DEBUG), _http() as client:
        creado = _crear(client, documento_identidad=DOCUMENTO).json()
        _crear(client, "cliente.dos", documento_identidad=DOCUMENTO)
        client.get(f"/usuarios/{creado['id']}/identidad")
        client.post("/sesiones", json={"usuario": "cliente.uno", "clave": CLAVE})

    assert DOCUMENTO not in caplog.text and "4050" not in caplog.text
