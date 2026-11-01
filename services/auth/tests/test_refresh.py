"""Refresh tokens (T22): rotación, reuso, revocación, canal y estados sin verificar."""

import hashlib
import json
import logging
from datetime import timedelta

import pytest
from fakes import (
    CIFRADOR_PRUEBAS,
    CLAVE_TITULAR_PRUEBAS,
    HASHER_RAPIDO,
    AlmacenConsentimientosMemoria,
    Reloj,
    RepositorioRefrescosMemoria,
    RepositorioUsuariosMemoria,
    extras_cliente,
)
from fastapi.testclient import TestClient

from app.configuracion import RUTA_SESION, Configuracion
from app.main import create_app
from app.sesiones import MENSAJE_REFRESH, RefreshInvalido, ServicioSesiones
from app.usuarios import ServicioUsuarios

SECRETO = "secreto-de-prueba-con-32-bytes-o-mas"
CLAVE = "Clave-segura-1"


def _preparar(rol: str = "ASESOR", dias: int = 8):
    repositorio = RepositorioUsuariosMemoria()
    refrescos = RepositorioRefrescosMemoria()
    reloj = Reloj()
    usuario = ServicioUsuarios(
        repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS
    ).crear("u.022", CLAVE, rol, **(extras_cliente("u.022") if rol == "CLIENTE" else {}))
    sesiones = ServicioSesiones(
        repositorio, HASHER_RAPIDO, SECRETO, 60, reloj=reloj, refrescos=refrescos, refresh_dias=dias
    )
    return sesiones, repositorio, refrescos, reloj, usuario


def test_el_login_entrega_refresh_opaco_guardado_solo_como_hash() -> None:
    sesiones, _, refrescos, reloj, _ = _preparar()

    sesion = sesiones.iniciar_sesion("u.022", CLAVE, "web")

    assert sesion["refresh_expira_en"] == reloj.ahora + timedelta(days=8)
    (fila,) = refrescos.filas.values()
    assert fila["hash_token"] == hashlib.sha256(sesion["refresh_token"].encode()).hexdigest()
    assert sesion["refresh_token"] not in json.dumps(fila, default=str)
    assert fila["canal"] == "web"


def test_renovar_rota_el_refresh_y_el_anterior_queda_invalido() -> None:
    sesiones, _, refrescos, reloj, _ = _preparar()
    r1 = sesiones.iniciar_sesion("u.022", CLAVE, "web")["refresh_token"]
    reloj.ahora += timedelta(minutes=30)

    nueva = sesiones.renovar_con_refresh(r1, "web")

    assert nueva["refresh_token"] != r1
    assert nueva["expira_en"] == reloj.ahora + timedelta(minutes=60)
    assert nueva["refresh_expira_en"] == reloj.ahora + timedelta(days=8)
    assert len({f["familia_id"] for f in refrescos.filas.values()}) == 1
    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r1, "web")


def test_reusar_un_refresh_usado_revoca_toda_la_cadena(caplog) -> None:
    sesiones, _, _, _, _ = _preparar()
    r1 = sesiones.iniciar_sesion("u.022", CLAVE, "web")["refresh_token"]
    r2 = sesiones.renovar_con_refresh(r1, "web")["refresh_token"]

    with caplog.at_level(logging.WARNING, logger="auth.sesiones"):
        with pytest.raises(RefreshInvalido):
            sesiones.renovar_con_refresh(r1, "web")  # reuso
    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r2, "web")  # el vigente de la cadena también cae

    assert "refresh reusado" in caplog.text
    assert r1 not in caplog.text and r2 not in caplog.text


def test_refresh_vencido_o_desconocido_da_el_mismo_mensaje() -> None:
    sesiones, _, _, reloj, _ = _preparar(dias=8)
    r1 = sesiones.iniciar_sesion("u.022", CLAVE, "web")["refresh_token"]
    reloj.ahora += timedelta(days=8, seconds=1)

    mensajes = set()
    for refresh in (r1, "no-existe"):
        with pytest.raises(RefreshInvalido) as error:
            sesiones.renovar_con_refresh(refresh, "web")
        mensajes.add(str(error.value))
    assert mensajes == {MENSAJE_REFRESH}


def test_el_refresh_es_del_canal_con_que_se_emitio() -> None:
    sesiones, _, _, _, _ = _preparar(rol="CLIENTE")
    r_app = sesiones.iniciar_sesion("u.022", CLAVE, "app")["refresh_token"]

    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r_app, "web")
    assert sesiones.renovar_con_refresh(r_app, "app")["refresh_token"]


def test_cerrar_sesion_y_suspender_revocan() -> None:
    sesiones, repositorio, _, _, usuario = _preparar()
    r1 = sesiones.iniciar_sesion("u.022", CLAVE, "web")["refresh_token"]
    sesiones.cerrar_sesion(r1)
    sesiones.cerrar_sesion("no-existe")  # idempotente
    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r1, "web")

    r2 = sesiones.iniciar_sesion("u.022", CLAVE, "web")["refresh_token"]
    repositorio.suspender(usuario["id"])
    sesiones.revocar_de_usuario(usuario["id"])
    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r2, "web")


def test_cuenta_suspendida_sin_revocar_igual_se_rechaza_y_revoca_la_cadena() -> None:
    sesiones, repositorio, refrescos, _, usuario = _preparar()
    r1 = sesiones.iniciar_sesion("u.022", CLAVE, "web")["refresh_token"]
    repositorio.suspender(usuario["id"])

    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r1, "web")
    assert all(f["revocado_en"] for f in refrescos.filas.values())


def test_renovar_revalida_el_rol_actual() -> None:
    sesiones, repositorio, _, _, usuario = _preparar(rol="ASESOR")
    r1 = sesiones.iniciar_sesion("u.022", CLAVE, "web")["refresh_token"]
    repositorio.filas[usuario["id"]]["rol"] = "BACKOFFICE"

    assert sesiones.renovar_con_refresh(r1, "web")["usuario"]["rol"] == "BACKOFFICE"


@pytest.mark.parametrize("estado", ["PENDIENTE_VERIFICACION", "RECHAZADA"])
def test_sin_verificar_renueva_solo_por_la_app(estado: str) -> None:
    sesiones, repositorio, _, _, usuario = _preparar(rol="CLIENTE")
    repositorio.filas[usuario["id"]]["estado"] = estado
    r_app = sesiones.iniciar_sesion("u.022", CLAVE, "app")["refresh_token"]

    nueva = sesiones.renovar_con_refresh(r_app, "app")
    assert nueva["usuario"]["estado"] == estado
    # Un refresh web de la misma cuenta (emitido antes de quedar sin verificar) tampoco sirve.
    repositorio.filas[usuario["id"]]["estado"] = "ACTIVO"
    r_web = sesiones.iniciar_sesion("u.022", CLAVE, "web")["refresh_token"]
    repositorio.filas[usuario["id"]]["estado"] = estado
    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(r_web, "web")


def test_el_token_pendiente_conserva_su_alcance_al_renovar() -> None:
    sesiones, repositorio, _, _, usuario = _preparar(rol="CLIENTE")
    repositorio.filas[usuario["id"]]["estado"] = "PENDIENTE_VERIFICACION"
    r = sesiones.iniciar_sesion("u.022", CLAVE, "app")["refresh_token"]
    import jwt

    token = sesiones.renovar_con_refresh(r, "app")["token"]
    claims = jwt.decode(token, SECRETO, algorithms=["HS256"])
    assert claims["scopes"] == ["verificacion-pendiente"]


def test_duraciones_salen_del_json_y_el_entorno_las_sobrescribe(monkeypatch) -> None:
    datos = json.loads(RUTA_SESION.read_text(encoding="utf-8"))
    monkeypatch.delenv("JWT_VIGENCIA_MINUTOS", raising=False)
    monkeypatch.delenv("REFRESH_VIGENCIA_DIAS", raising=False)
    config = Configuracion.desde_entorno()
    assert (config.jwt_vigencia_minutos, config.refresh_vigencia_dias) == (
        datos["acceso_minutos"],
        datos["refresh_dias"],
    )
    monkeypatch.setenv("JWT_VIGENCIA_MINUTOS", "1")
    monkeypatch.setenv("REFRESH_VIGENCIA_DIAS", "2")
    config = Configuracion.desde_entorno()
    assert (config.jwt_vigencia_minutos, config.refresh_vigencia_dias) == (1, 2)


def test_endpoints_http_refresh_y_cerrar() -> None:
    repositorio = RepositorioUsuariosMemoria()
    config = Configuracion("postgresql://no-se-usa", SECRETO, 60)
    sesiones = ServicioSesiones(
        repositorio, HASHER_RAPIDO, SECRETO, 60, refrescos=RepositorioRefrescosMemoria()
    )
    app = create_app(
        almacen=AlmacenConsentimientosMemoria(),
        servicio_usuarios=ServicioUsuarios(
            repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS
        ),
        servicio_sesiones=sesiones,
        config=config,
    )
    with TestClient(app) as client:
        client.post("/usuarios", json={"usuario": "asesor.1", "clave": CLAVE, "rol": "ASESOR"})
        login = client.post(
            "/sesiones", json={"usuario": "asesor.1", "clave": CLAVE, "canal": "web"}
        ).json()
        ok = client.post(
            "/sesiones/refresh", json={"refresh_token": login["refresh_token"], "canal": "web"}
        )
        reuso = client.post(
            "/sesiones/refresh", json={"refresh_token": login["refresh_token"], "canal": "web"}
        )
        cierre = client.post("/sesiones/cerrar", json={"refresh_token": ok.json()["refresh_token"]})
        tras_cierre = client.post(
            "/sesiones/refresh", json={"refresh_token": ok.json()["refresh_token"], "canal": "web"}
        )

    assert ok.status_code == 200 and ok.json()["refresh_token"] != login["refresh_token"]
    assert reuso.status_code == 401 and reuso.json()["detail"] == MENSAJE_REFRESH
    assert cierre.status_code == 204
    assert tras_cierre.status_code == 401
