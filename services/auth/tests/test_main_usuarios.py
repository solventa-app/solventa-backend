"""Endpoints HTTP de sesiones y usuarios, con dobles (sin PostgreSQL)."""

from fakes import (
    CIFRADOR_PRUEBAS,
    CLAVE_TITULAR_PRUEBAS,
    HASHER_RAPIDO,
    AlmacenConsentimientosMemoria,
    RepositorioUsuariosMemoria,
    extras_cliente_http,
)
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app
from app.sesiones import ServicioSesiones
from app.usuarios import ServicioUsuarios

CLAVE = "clave-segura-1"


def _cliente() -> TestClient:
    repositorio = RepositorioUsuariosMemoria()
    config = Configuracion("postgresql://no-se-usa", "secreto-de-prueba-con-32-bytes-o-mas", 60)
    app = create_app(
        almacen=AlmacenConsentimientosMemoria(),
        servicio_usuarios=ServicioUsuarios(
            repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS
        ),
        servicio_sesiones=ServicioSesiones(repositorio, HASHER_RAPIDO, config.jwt_secreto, 60),
        config=config,
    )
    return TestClient(app)


def test_crear_iniciar_sesion_y_consultar() -> None:
    with _cliente() as client:
        creado = client.post(
            "/usuarios", json={"usuario": "asesor.017", "clave": CLAVE, "rol": "ASESOR"}
        )
        sesion = client.post("/sesiones", json={"usuario": "asesor.017", "clave": CLAVE})
        leido = client.get(f"/usuarios/{creado.json()['id']}")

    assert creado.status_code == 200
    assert "hash_clave" not in creado.json() and "clave" not in creado.json()
    assert sesion.status_code == 200
    assert sesion.json()["token"] and sesion.json()["usuario"]["rol"] == "ASESOR"
    assert leido.json()["usuario"] == "asesor.017"


def test_credenciales_invalidas_responden_401_con_mensaje_generico() -> None:
    with _cliente() as client:
        r = client.post("/sesiones", json={"usuario": "nadie", "clave": CLAVE})

    assert r.status_code == 401
    assert r.json()["detail"] == "Usuario o clave incorrectos."


def test_alias_invalido_400_y_duplicado_409() -> None:
    with _cliente() as client:
        invalido = client.post(
            "/usuarios",
            json={
                "usuario": "a@b.co",
                "clave": CLAVE,
                "rol": "CLIENTE",
                **extras_cliente_http("a@b.co"),
            },
        )
        client.post(
            "/usuarios",
            json={
                "usuario": "cliente.a",
                "clave": CLAVE,
                "rol": "CLIENTE",
                **extras_cliente_http("cliente.a"),
            },
        )
        duplicado = client.post(
            "/usuarios",
            json={
                "usuario": "cliente.a",
                "clave": CLAVE,
                "rol": "CLIENTE",
                **extras_cliente_http("cliente.a"),
            },
        )

    assert invalido.status_code == 400
    assert duplicado.status_code == 409


def test_listar_cambiar_rol_y_suspender() -> None:
    with _cliente() as client:
        creado = client.post(
            "/usuarios",
            json={
                "usuario": "cliente.a",
                "clave": CLAVE,
                "rol": "CLIENTE",
                **extras_cliente_http("cliente.a"),
            },
        ).json()
        listado = client.get("/usuarios", params={"rol": "CLIENTE"}).json()
        con_rol = client.post(f"/usuarios/{creado['id']}/rol", json={"rol": "ASESOR"}).json()
        suspendido = client.post(f"/usuarios/{creado['id']}/suspender").json()
        sesion = client.post("/sesiones", json={"usuario": "cliente.a", "clave": CLAVE})

    assert [u["usuario"] for u in listado] == ["cliente.a"]
    assert con_rol["rol"] == "ASESOR"
    assert suspendido["estado"] == "SUSPENDIDO"
    assert sesion.status_code == 401


def test_usuario_inexistente_404() -> None:
    with _cliente() as client:
        assert client.get("/usuarios/no-existe").status_code == 404
        assert client.post("/usuarios/no-existe/suspender").status_code == 404
        assert client.post("/usuarios/no-existe/rol", json={"rol": "ASESOR"}).status_code == 404


def test_preferencias_de_idioma_y_moneda() -> None:
    """KAN-209: se guardan, salen al iniciar sesión y en `GET /usuarios/{id}`, y se validan."""
    with _cliente() as client:
        creado = client.post(
            "/usuarios",
            json={
                "usuario": "cliente.pref",
                "clave": CLAVE,
                "rol": "CLIENTE",
                **extras_cliente_http("cliente.pref"),
            },
        ).json()
        assert (creado["idioma"], creado["moneda"]) == ("ES", "COP")

        cambio = client.post(
            f"/usuarios/{creado['id']}/preferencias", json={"idioma": "EN", "moneda": "USD"}
        )
        sesion = client.post("/sesiones", json={"usuario": "cliente.pref", "clave": CLAVE})
        leido = client.get(f"/usuarios/{creado['id']}")
        invalido = client.post(
            f"/usuarios/{creado['id']}/preferencias", json={"idioma": "FR", "moneda": "USD"}
        )
        inexistente = client.post(
            "/usuarios/no-existe/preferencias", json={"idioma": "EN", "moneda": "USD"}
        )

    assert cambio.status_code == 200
    assert (cambio.json()["idioma"], cambio.json()["moneda"]) == ("EN", "USD")
    assert sesion.json()["usuario"]["idioma"] == "EN"
    assert leido.json()["moneda"] == "USD"
    assert invalido.status_code == 400
    assert inexistente.status_code == 404
