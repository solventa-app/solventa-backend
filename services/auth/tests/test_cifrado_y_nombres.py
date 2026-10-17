"""Nombre del CLIENTE (KAN-28): cifrado por campo, validación y consulta por ids."""

import logging

import pytest
from fakes import CIFRADOR_PRUEBAS, HASHER_RAPIDO, RepositorioUsuariosMemoria
from fastapi.testclient import TestClient

from app.cifrado import CifradorAesGcm, ErrorDeCifrado
from app.configuracion import CIFRADO_CLAVE_LOCAL, JWT_SECRETO_LOCAL, Configuracion
from app.main import create_app
from app.sesiones import ServicioSesiones
from app.usuarios import MAXIMO_IDS_POR_CONSULTA, DatoInvalido, ServicioUsuarios

CLAVE = "clave-segura-1"
NOMBRE = "María Fernanda Gómez"


def _servicio() -> tuple[ServicioUsuarios, RepositorioUsuariosMemoria]:
    repositorio = RepositorioUsuariosMemoria()
    return ServicioUsuarios(repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS), repositorio


# ---------- cifrado


def test_ida_y_vuelta() -> None:
    cifrado = CIFRADOR_PRUEBAS.cifrar(NOMBRE, "u-1")

    assert NOMBRE not in cifrado
    assert CIFRADOR_PRUEBAS.descifrar(cifrado, "u-1") == NOMBRE


def test_el_mismo_texto_cifra_distinto_cada_vez() -> None:
    assert CIFRADOR_PRUEBAS.cifrar(NOMBRE, "u-1") != CIFRADOR_PRUEBAS.cifrar(NOMBRE, "u-1")


def test_el_cifrado_de_un_usuario_no_sirve_para_otro() -> None:
    cifrado = CIFRADOR_PRUEBAS.cifrar(NOMBRE, "u-1")

    with pytest.raises(ErrorDeCifrado):
        CIFRADOR_PRUEBAS.descifrar(cifrado, "u-2")


def test_una_llave_distinta_no_descifra() -> None:
    otro = CifradorAesGcm(b"x" * 32)

    with pytest.raises(ErrorDeCifrado):
        otro.descifrar(CIFRADOR_PRUEBAS.cifrar(NOMBRE, "u-1"), "u-1")


def test_un_valor_alterado_o_basura_no_descifra() -> None:
    with pytest.raises(ErrorDeCifrado):
        CIFRADOR_PRUEBAS.descifrar("esto-no-es-un-cifrado", "u-1")


def test_la_llave_local_tiene_32_bytes() -> None:
    CifradorAesGcm.desde_base64(CIFRADO_CLAVE_LOCAL)  # no lanza


def test_llave_invalida_se_rechaza() -> None:
    with pytest.raises(ValueError):
        CifradorAesGcm.desde_base64("corta")


def test_en_la_nube_no_arranca_con_la_llave_local(monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "auth")
    monkeypatch.setenv("JWT_SECRETO", "un-secreto-real-de-mas-de-32-bytes-ok")
    monkeypatch.delenv("CIFRADO_CLAVE", raising=False)

    with pytest.raises(ValueError, match="CIFRADO_CLAVE"):
        Configuracion.desde_entorno()


def test_jwt_local_sigue_rechazado_en_la_nube(monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "auth")
    monkeypatch.setenv("JWT_SECRETO", JWT_SECRETO_LOCAL)

    with pytest.raises(ValueError, match="JWT_SECRETO"):
        Configuracion.desde_entorno()


# ---------- servicio


def test_el_nombre_se_guarda_cifrado_y_no_sale_en_el_usuario() -> None:
    servicio, repositorio = _servicio()

    usuario = servicio.crear("cliente.uno", CLAVE, "CLIENTE", NOMBRE)

    assert "nombre" not in usuario and "nombre_cifrado" not in usuario
    guardado = repositorio.filas[usuario["id"]]["nombre_cifrado"]
    assert guardado and NOMBRE not in guardado and "Fernanda" not in guardado


def test_los_espacios_sobrantes_se_limpian() -> None:
    servicio, _ = _servicio()
    usuario = servicio.crear("cliente.uno", CLAVE, "CLIENTE", "  Ana   Pérez ")

    assert servicio.nombres([usuario["id"]]) == {usuario["id"]: "Ana Pérez"}


@pytest.mark.parametrize("rol", ["ASESOR", "BACKOFFICE"])
def test_solo_los_clientes_tienen_nombre(rol: str) -> None:
    servicio, _ = _servicio()

    with pytest.raises(DatoInvalido, match="Solo los clientes"):
        servicio.crear("persona.uno", CLAVE, rol, NOMBRE)


@pytest.mark.parametrize("nombre", ["", " ", "A", "x" * 101, "Ana\x00Pérez", "Ana\nPérez\x07"])
def test_nombres_invalidos(nombre: str) -> None:
    servicio, _ = _servicio()

    with pytest.raises(DatoInvalido):
        servicio.crear("cliente.uno", CLAVE, "CLIENTE", nombre)


def test_sin_cifrador_no_se_guardan_nombres() -> None:
    servicio = ServicioUsuarios(RepositorioUsuariosMemoria(), HASHER_RAPIDO)

    with pytest.raises(DatoInvalido, match="configurado"):
        servicio.crear("cliente.uno", CLAVE, "CLIENTE", NOMBRE)


def test_nombres_devuelve_solo_clientes_con_nombre() -> None:
    servicio, _ = _servicio()
    con = servicio.crear("cliente.uno", CLAVE, "CLIENTE", NOMBRE)
    sin = servicio.crear("cliente.dos", CLAVE, "CLIENTE")
    asesor = servicio.crear("asesor.uno", CLAVE, "ASESOR")

    resultado = servicio.nombres([con["id"], sin["id"], asesor["id"], "no-existe"])

    assert resultado == {con["id"]: NOMBRE}


def test_un_nombre_que_no_descifra_se_omite() -> None:
    servicio, repositorio = _servicio()
    bueno = servicio.crear("cliente.uno", CLAVE, "CLIENTE", NOMBRE)
    malo = servicio.crear("cliente.dos", CLAVE, "CLIENTE", "Pedro Ruiz")
    repositorio.filas[malo["id"]]["nombre_cifrado"] = "dañado"

    assert servicio.nombres([bueno["id"], malo["id"]]) == {bueno["id"]: NOMBRE}


def test_limite_de_ids_por_consulta() -> None:
    servicio, _ = _servicio()

    with pytest.raises(DatoInvalido):
        servicio.nombres([f"u-{i}" for i in range(MAXIMO_IDS_POR_CONSULTA + 1)])


# ---------- HTTP


def _http() -> TestClient:
    repositorio = RepositorioUsuariosMemoria()
    config = Configuracion("postgresql://no-se-usa", "secreto-de-prueba-con-32-bytes-o-mas", 60)
    app = create_app(
        servicio_usuarios=ServicioUsuarios(repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS),
        servicio_sesiones=ServicioSesiones(repositorio, HASHER_RAPIDO, config.jwt_secreto, 60),
        config=config,
    )
    return TestClient(app)


def test_crear_cliente_con_nombre_y_consultarlo_por_ids() -> None:
    with _http() as client:
        creado = client.post(
            "/usuarios",
            json={"usuario": "cliente.uno", "clave": CLAVE, "rol": "CLIENTE", "nombre": NOMBRE},
        )
        assert creado.status_code == 200
        assert "nombre" not in creado.json()
        r = client.post("/usuarios/nombres", json={"ids": [creado.json()["id"]]})

    assert r.status_code == 200
    assert r.json() == {creado.json()["id"]: NOMBRE}


def test_http_nombre_de_asesor_da_400() -> None:
    with _http() as client:
        r = client.post(
            "/usuarios",
            json={"usuario": "asesor.uno", "clave": CLAVE, "rol": "ASESOR", "nombre": NOMBRE},
        )

    assert r.status_code == 400


def test_http_demasiados_ids_da_400() -> None:
    with _http() as client:
        r = client.post("/usuarios/nombres", json={"ids": ["x"] * (MAXIMO_IDS_POR_CONSULTA + 1)})

    assert r.status_code == 400


def test_los_logs_no_llevan_el_nombre(caplog) -> None:
    with caplog.at_level(logging.DEBUG), _http() as client:
        creado = client.post(
            "/usuarios",
            json={"usuario": "cliente.uno", "clave": CLAVE, "rol": "CLIENTE", "nombre": NOMBRE},
        ).json()
        client.post("/usuarios/nombres", json={"ids": [creado["id"]]})

    assert "Fernanda" not in caplog.text and "Gómez" not in caplog.text
