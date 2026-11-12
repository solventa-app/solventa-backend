"""Endpoints HTTP de dispositivos de push (KAN-259), con dobles (sin PostgreSQL)."""

from fakes import (
    CIFRADOR_PRUEBAS,
    CLAVE_TITULAR_PRUEBAS,
    HASHER_RAPIDO,
    AlmacenConsentimientosMemoria,
    RepositorioDispositivosPushMemoria,
    RepositorioRefrescosMemoria,
    RepositorioUsuariosMemoria,
    extras_cliente_http,
)
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.dispositivos_push import ServicioDispositivosPush
from app.main import create_app
from app.sesiones import ServicioSesiones
from app.usuarios import ServicioUsuarios

CLAVE = "Clave-segura-1"


def _cliente() -> tuple[TestClient, RepositorioDispositivosPushMemoria]:
    repositorio = RepositorioUsuariosMemoria()
    refrescos = RepositorioRefrescosMemoria()
    repo_push = RepositorioDispositivosPushMemoria()
    config = Configuracion("postgresql://no-se-usa", "secreto-de-prueba-con-32-bytes-o-mas", 60)
    dispositivos_push = ServicioDispositivosPush(
        repo_push,
        CIFRADOR_PRUEBAS,
        habilitado_push=lambda uid: bool(repositorio.filas[uid].get("notificaciones_push", True)),
    )
    app = create_app(
        almacen=AlmacenConsentimientosMemoria(),
        servicio_usuarios=ServicioUsuarios(
            repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS
        ),
        servicio_sesiones=ServicioSesiones(
            repositorio,
            HASHER_RAPIDO,
            config.jwt_secreto,
            60,
            refrescos=refrescos,
            dispositivos_push=dispositivos_push,
        ),
        servicio_dispositivos_push=dispositivos_push,
        config=config,
    )
    return TestClient(app), repo_push


def _crear_cliente(client: TestClient, alias: str = "cliente.259") -> str:
    r = client.post(
        "/usuarios",
        json={"usuario": alias, "clave": CLAVE, "rol": "CLIENTE", **extras_cliente_http(alias)},
    )
    assert r.status_code == 200
    return r.json()["id"]


def test_login_en_la_app_con_dispositivo_registra_push_y_el_segundo_revoca_el_primero() -> None:
    client, repo_push = _cliente()
    with client:
        _crear_cliente(client)

        primera = client.post(
            "/sesiones",
            json={
                "usuario": "cliente.259",
                "clave": CLAVE,
                "canal": "app",
                "dispositivo": {"instalacion_id": "A", "plataforma": "android"},
            },
        )
        assert primera.status_code == 200
        assert primera.json()["sids_revocados"] == []

        segunda = client.post(
            "/sesiones",
            json={
                "usuario": "cliente.259",
                "clave": CLAVE,
                "canal": "app",
                "dispositivo": {"instalacion_id": "B", "plataforma": "android"},
            },
        )
        assert segunda.status_code == 200
        assert len(segunda.json()["sids_revocados"]) == 1

    activos = [f for f in repo_push.filas.values() if f["activo"]]
    assert len(activos) == 1 and activos[0]["instalacion_id"] == "B"


def test_registrar_token_push_y_leerlo_en_destino() -> None:
    client, _ = _cliente()
    with client:
        usuario_id = _crear_cliente(client)
        client.post(
            "/sesiones",
            json={
                "usuario": "cliente.259",
                "clave": CLAVE,
                "canal": "app",
                "dispositivo": {"instalacion_id": "A", "plataforma": "android"},
            },
        )

        registrado = client.put(
            "/dispositivos-push/token",
            json={"usuario_id": usuario_id, "instalacion_id": "A", "token": "token-fcm-real"},
        )
        assert registrado.status_code == 204

        destino = client.get(f"/interno/destino-push/{usuario_id}")

    assert destino.status_code == 200
    assert destino.json() == {
        "dispositivo_id": destino.json()["dispositivo_id"],
        "token": "token-fcm-real",
        "plataforma": "android",
    }


def test_destino_push_sin_dispositivo_da_404() -> None:
    client, _ = _cliente()
    with client:
        usuario_id = _crear_cliente(client)
        r = client.get(f"/interno/destino-push/{usuario_id}")

    assert r.status_code == 404


def test_apagar_la_preferencia_hace_que_destino_push_de_409() -> None:
    client, _ = _cliente()
    with client:
        usuario_id = _crear_cliente(client)
        client.post(
            "/sesiones",
            json={
                "usuario": "cliente.259",
                "clave": CLAVE,
                "canal": "app",
                "dispositivo": {"instalacion_id": "A", "plataforma": "android"},
            },
        )
        client.put(
            "/dispositivos-push/token",
            json={"usuario_id": usuario_id, "instalacion_id": "A", "token": "token-fcm"},
        )

        apagado = client.put(
            f"/usuarios/{usuario_id}/preferencias/push", json={"habilitado": False}
        )
        assert apagado.status_code == 200
        assert apagado.json()["notificaciones_push"] is False

        destino = client.get(f"/interno/destino-push/{usuario_id}")

    assert destino.status_code == 409
    assert destino.json()["detail"] == "PUSH_DESHABILITADO"


def test_token_invalido_borra_el_token_y_destino_vuelve_a_dar_404() -> None:
    client, repo_push = _cliente()
    with client:
        usuario_id = _crear_cliente(client)
        client.post(
            "/sesiones",
            json={
                "usuario": "cliente.259",
                "clave": CLAVE,
                "canal": "app",
                "dispositivo": {"instalacion_id": "A", "plataforma": "android"},
            },
        )
        client.put(
            "/dispositivos-push/token",
            json={"usuario_id": usuario_id, "instalacion_id": "A", "token": "token-fcm"},
        )
        dispositivo_id = next(iter(repo_push.filas))

        invalidado = client.post(f"/interno/dispositivos-push/{dispositivo_id}/token-invalido")
        assert invalidado.status_code == 204

        destino = client.get(f"/interno/destino-push/{usuario_id}")

    assert destino.status_code == 404


def test_cerrar_sesion_apaga_el_dispositivo_de_push() -> None:
    client, repo_push = _cliente()
    with client:
        _crear_cliente(client)
        sesion = client.post(
            "/sesiones",
            json={
                "usuario": "cliente.259",
                "clave": CLAVE,
                "canal": "app",
                "dispositivo": {"instalacion_id": "A", "plataforma": "android"},
            },
        ).json()

        cierre = client.post("/sesiones/cerrar", json={"refresh_token": sesion["refresh_token"]})
        assert cierre.status_code == 204

    fila = next(iter(repo_push.filas.values()))
    assert fila["activo"] is False
    assert fila["motivo_desactivacion"] == "CIERRE_SESION"
