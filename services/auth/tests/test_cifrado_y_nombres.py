"""Nombre del CLIENTE (KAN-28): cifrado por campo, validación y consulta por ids."""

import logging

import jwt
import pytest
from fakes import (
    CIFRADOR_PRUEBAS,
    CLAVE_TITULAR_PRUEBAS,
    HASHER_RAPIDO,
    RepositorioUsuariosMemoria,
    extras_cliente,
    extras_cliente_http,
)
from fastapi.testclient import TestClient

from app.cifrado import CifradorAesGcm, ErrorDeCifrado
from app.configuracion import CIFRADO_CLAVE_LOCAL, JWT_SECRETO_LOCAL, Configuracion
from app.main import create_app
from app.sesiones import ServicioSesiones
from app.usuarios import MAXIMO_IDS_POR_CONSULTA, DatoInvalido, ServicioUsuarios

CLAVE = "Clave-segura-1"
NOMBRE = "María Fernanda Gómez"


def _servicio() -> tuple[ServicioUsuarios, RepositorioUsuariosMemoria]:
    repositorio = RepositorioUsuariosMemoria()
    return ServicioUsuarios(
        repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS
    ), repositorio


# ---------- cifrado


def test_ida_y_vuelta() -> None:
    cifrado = CIFRADOR_PRUEBAS.cifrar(NOMBRE, "u-1")

    assert NOMBRE not in cifrado
    assert CIFRADOR_PRUEBAS.descifrar(cifrado, "u-1") == NOMBRE


def test_el_mismo_texto_cifra_distinto_cada_vez() -> None:
    primero = CIFRADOR_PRUEBAS.cifrar(NOMBRE, "u-1")
    segundo = CIFRADOR_PRUEBAS.cifrar(NOMBRE, "u-1")

    assert primero != segundo


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
    monkeypatch.setenv("CLIENTE_ID_CLAVE", "clave-titular-real")

    with pytest.raises(ValueError, match="CIFRADO_CLAVE"):
        Configuracion.desde_entorno()


def test_jwt_local_sigue_rechazado_en_la_nube(monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "auth")
    monkeypatch.setenv("JWT_SECRETO", JWT_SECRETO_LOCAL)

    with pytest.raises(ValueError, match="JWT_SECRETO"):
        Configuracion.desde_entorno()


def test_cifrado_de_sobre_kms_desenvuelve_dek_al_arrancar() -> None:
    import base64
    from types import SimpleNamespace

    from app.cifrado import DesenvolvedorGoogleKms, construir_cifrador

    dek_32 = b"k" * 32
    envuelta_b64 = base64.urlsafe_b64encode(b"ciphertext-envuelto-por-kms").decode("ascii")
    llamadas: list[dict] = []

    class ClienteKmsFalso:
        def decrypt(self, request: dict):
            llamadas.append(request)
            return SimpleNamespace(plaintext=dek_32)

    config = Configuracion(
        database_url="postgresql://no-se-usa",
        jwt_secreto="secreto-de-prueba-con-32-bytes-o-mas",
        jwt_vigencia_minutos=60,
        kms_key_name="projects/solventa/locations/us-central1/keyRings/sr/cryptoKeys/campos",
        cifrado_clave_envuelta=envuelta_b64,
    )
    desenvolvedor = DesenvolvedorGoogleKms(config.kms_key_name, cliente=ClienteKmsFalso())
    cifrador = construir_cifrador(config, desenvolvedor=desenvolvedor)

    assert len(llamadas) == 1
    assert llamadas[0]["name"] == config.kms_key_name
    cifrado = cifrador.cifrar(NOMBRE, "u-1")
    assert cifrador.descifrar(cifrado, "u-1") == NOMBRE


def test_ec022_llave_kms_deshabilitada_impide_construir_cifrador_y_descifrar() -> None:
    import base64

    from app.cifrado import DesenvolvedorGoogleKms, ErrorCifrado, construir_cifrador

    envuelta_b64 = base64.urlsafe_b64encode(b"ciphertext-envuelto-por-kms").decode("ascii")

    class ClienteKmsDeshabilitado:
        def decrypt(self, request: dict):
            raise PermissionError("CryptoKeyVersion is DISABLED")

    config = Configuracion(
        database_url="postgresql://no-se-usa",
        jwt_secreto="secreto-de-prueba-con-32-bytes-o-mas",
        jwt_vigencia_minutos=60,
        kms_key_name="projects/solventa/locations/us-central1/keyRings/sr/cryptoKeys/campos",
        cifrado_clave_envuelta=envuelta_b64,
    )
    desenvolvedor = DesenvolvedorGoogleKms(config.kms_key_name, cliente=ClienteKmsDeshabilitado())

    with pytest.raises(ErrorCifrado, match="DISABLED"):
        construir_cifrador(config, desenvolvedor=desenvolvedor)


def test_en_la_nube_arranca_con_sobre_kms_sin_cifrado_clave_en_claro(monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "auth")
    monkeypatch.setenv("JWT_SECRETO", "un-secreto-real-de-mas-de-32-bytes-ok")
    monkeypatch.delenv("CIFRADO_CLAVE", raising=False)
    monkeypatch.setenv("CLIENTE_ID_CLAVE", "clave-titular-real")
    monkeypatch.setenv("SOCIOS_CLAVE_FIRMA", "clave-de-firma-de-socios-real")
    monkeypatch.setenv(
        "KMS_KEY_NAME", "projects/solventa/locations/us-central1/keyRings/sr/cryptoKeys/campos"
    )
    monkeypatch.setenv("CIFRADO_CLAVE_ENVUELTA", "Y2lwaGVydGV4dC1lbnZ1ZWx0bw==")

    cfg = Configuracion.desde_entorno()
    assert cfg.kms_key_name is not None
    assert cfg.cifrado_clave_envuelta == "Y2lwaGVydGV4dC1lbnZ1ZWx0bw=="


# ---------- servicio


def test_el_nombre_se_guarda_cifrado_y_no_sale_en_el_usuario() -> None:
    servicio, repositorio = _servicio()

    usuario = servicio.crear(
        "cliente.uno", CLAVE, "CLIENTE", NOMBRE, **extras_cliente("cliente.uno")
    )

    assert "nombre" not in usuario and "nombre_cifrado" not in usuario
    guardado = repositorio.filas[usuario["id"]]["nombre_cifrado"]
    assert guardado and NOMBRE not in guardado and "Fernanda" not in guardado


def test_los_espacios_sobrantes_se_limpian() -> None:
    servicio, _ = _servicio()
    usuario = servicio.crear(
        "cliente.uno", CLAVE, "CLIENTE", "  Ana   Pérez ", **extras_cliente("cliente.uno")
    )

    assert servicio.nombres([usuario["id"]]) == {usuario["id"]: "Ana Pérez"}


@pytest.mark.parametrize("rol", ["CLIENTE", "ASESOR", "BACKOFFICE"])
def test_cualquier_rol_puede_tener_nombre(rol: str) -> None:
    servicio, _ = _servicio()
    usuario = servicio.crear(
        "persona.uno",
        CLAVE,
        rol,
        NOMBRE,
        **(extras_cliente("persona.uno") if rol == "CLIENTE" else {}),
    )

    assert servicio.nombre_de(usuario["id"]) == NOMBRE


def test_nombre_de_un_usuario_sin_nombre_es_none() -> None:
    servicio, _ = _servicio()
    usuario = servicio.crear("asesor.uno", CLAVE, "ASESOR")

    assert servicio.nombre_de(usuario["id"]) is None
    assert servicio.nombre_de("no-existe") is None


def test_nombre_de_con_dato_danado_es_none() -> None:
    servicio, repositorio = _servicio()
    usuario = servicio.crear("asesor.uno", CLAVE, "ASESOR", NOMBRE)
    repositorio.filas[usuario["id"]]["nombre_cifrado"] = "dañado"

    assert servicio.nombre_de(usuario["id"]) is None


def test_nombres_por_lista_sigue_siendo_solo_de_clientes() -> None:
    servicio, _ = _servicio()
    asesor = servicio.crear("asesor.uno", CLAVE, "ASESOR", NOMBRE)

    assert servicio.nombres([asesor["id"]]) == {}


@pytest.mark.parametrize("nombre", ["", " ", "A", "x" * 101, "Ana\x00Pérez", "Ana\nPérez\x07"])
def test_nombres_invalidos(nombre: str) -> None:
    servicio, _ = _servicio()

    with pytest.raises(DatoInvalido):
        servicio.crear("cliente.uno", CLAVE, "CLIENTE", nombre, **extras_cliente("cliente.uno"))


def test_sin_cifrador_no_se_guardan_nombres() -> None:
    servicio = ServicioUsuarios(
        RepositorioUsuariosMemoria(), HASHER_RAPIDO, clave_titular=CLAVE_TITULAR_PRUEBAS
    )

    with pytest.raises(DatoInvalido, match="configurado"):
        servicio.crear("cliente.uno", CLAVE, "CLIENTE", NOMBRE, **extras_cliente("cliente.uno"))


def test_nombres_devuelve_solo_clientes_con_nombre() -> None:
    servicio, _ = _servicio()
    con = servicio.crear("cliente.uno", CLAVE, "CLIENTE", NOMBRE, **extras_cliente("cliente.uno"))
    sin = servicio.crear("cliente.dos", CLAVE, "CLIENTE", **extras_cliente("cliente.dos"))
    asesor = servicio.crear("asesor.uno", CLAVE, "ASESOR")

    resultado = servicio.nombres([con["id"], sin["id"], asesor["id"], "no-existe"])

    assert resultado == {con["id"]: NOMBRE}


def test_un_nombre_que_no_descifra_se_omite() -> None:
    servicio, repositorio = _servicio()
    bueno = servicio.crear("cliente.uno", CLAVE, "CLIENTE", NOMBRE, **extras_cliente("cliente.uno"))
    malo = servicio.crear(
        "cliente.dos", CLAVE, "CLIENTE", "Pedro Ruiz", **extras_cliente("cliente.dos")
    )
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
        servicio_usuarios=ServicioUsuarios(
            repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS
        ),
        servicio_sesiones=ServicioSesiones(repositorio, HASHER_RAPIDO, config.jwt_secreto, 60),
        config=config,
    )
    return TestClient(app)


def test_crear_cliente_con_nombre_y_consultarlo_por_ids() -> None:
    with _http() as client:
        creado = client.post(
            "/usuarios",
            json={
                "usuario": "cliente.uno",
                "clave": CLAVE,
                "rol": "CLIENTE",
                "nombre": NOMBRE,
                **extras_cliente_http("cliente.uno"),
            },
        )
        assert creado.status_code == 200
        assert creado.json()["nombre"] is None
        r = client.post("/usuarios/nombres", json={"ids": [creado.json()["id"]]})

    assert r.status_code == 200
    assert r.json() == {creado.json()["id"]: NOMBRE}


def test_http_el_login_y_el_get_traen_el_nombre_propio_pero_la_lista_no() -> None:
    with _http() as client:
        creado = client.post(
            "/usuarios",
            json={"usuario": "asesor.uno", "clave": CLAVE, "rol": "ASESOR", "nombre": NOMBRE},
        ).json()
        sesion = client.post("/sesiones", json={"usuario": "asesor.uno", "clave": CLAVE}).json()
        propio = client.get(f"/usuarios/{creado['id']}").json()
        lista = client.get("/usuarios").json()

    assert creado["nombre"] is None  # al crear no se devuelve
    assert sesion["usuario"]["nombre"] == NOMBRE
    assert propio["nombre"] == NOMBRE
    assert all(u["nombre"] is None for u in lista)


def test_http_el_nombre_no_viaja_en_el_jwt() -> None:
    with _http() as client:
        client.post(
            "/usuarios",
            json={"usuario": "asesor.uno", "clave": CLAVE, "rol": "ASESOR", "nombre": NOMBRE},
        )
        sesion = client.post("/sesiones", json={"usuario": "asesor.uno", "clave": CLAVE}).json()

    claims = jwt.decode(sesion["token"], options={"verify_signature": False})
    assert "nombre" not in claims and NOMBRE not in sesion["token"]


def test_http_demasiados_ids_da_400() -> None:
    with _http() as client:
        r = client.post("/usuarios/nombres", json={"ids": ["x"] * (MAXIMO_IDS_POR_CONSULTA + 1)})

    assert r.status_code == 400


def test_los_logs_no_llevan_el_nombre(caplog) -> None:
    with caplog.at_level(logging.DEBUG), _http() as client:
        creado = client.post(
            "/usuarios",
            json={
                "usuario": "cliente.uno",
                "clave": CLAVE,
                "rol": "CLIENTE",
                "nombre": NOMBRE,
                **extras_cliente_http("cliente.uno"),
            },
        ).json()
        client.post("/usuarios/nombres", json={"ids": [creado["id"]]})

    assert "Fernanda" not in caplog.text and "Gómez" not in caplog.text


def test_nombres_tambien_resuelve_por_titular() -> None:
    servicio, repositorio = _servicio()
    usuario = servicio.crear(
        "cliente.uno", CLAVE, "CLIENTE", NOMBRE, **extras_cliente("cliente.uno")
    )
    repositorio.filas[usuario["id"]]["titular_id"] = "titular-hmac"

    assert servicio.nombres(["titular-hmac"]) == {"titular-hmac": NOMBRE}
