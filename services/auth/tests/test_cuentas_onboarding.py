"""Cuentas del onboarding del móvil (HU-M01): correo, estados de verificación y login por canal."""

import logging
from datetime import timedelta

import jwt
import pytest
from fakes import (
    CIFRADOR_PRUEBAS,
    HASHER_RAPIDO,
    AlmacenConsentimientosMemoria,
    Reloj,
    RepositorioUsuariosMemoria,
)
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app
from app.sesiones import CredencialesInvalidas, ServicioSesiones, VerificacionPendiente
from app.usuarios import DatoInvalido, ServicioUsuarios, UsuarioDuplicado

SECRETO = "secreto-de-prueba-con-32-bytes-o-mas"
CLAVE_HMAC = "clave-hmac-de-prueba"
CORREO = "Ana.Prueba@Ejemplo.com"
CLAVE = "clave-segura-1"


def _preparar():
    repo = RepositorioUsuariosMemoria()
    reloj = Reloj()
    usuarios = ServicioUsuarios(repo, HASHER_RAPIDO, CIFRADOR_PRUEBAS, clave_titular=CLAVE_HMAC)
    sesiones = ServicioSesiones(
        repo, HASHER_RAPIDO, SECRETO, 60, reloj=reloj, clave_correo=CLAVE_HMAC
    )
    cuenta = usuarios.crear_cuenta_onboarding(CORREO, CLAVE)
    return repo, reloj, usuarios, sesiones, cuenta


def _scopes(sesion: dict) -> list[str]:
    # Sin revisar fechas: el reloj de la prueba avanza más que el real.
    opciones = {"verify_exp": False, "verify_iat": False}
    return jwt.decode(sesion["token"], SECRETO, algorithms=["HS256"], options=opciones)["scopes"]


def test_la_cuenta_nace_pendiente_con_el_correo_solo_cifrado_y_alias_opaco() -> None:
    repo, _, _, _, cuenta = _preparar()
    fila = repo.filas[cuenta["id"]]
    assert (cuenta["rol"], cuenta["estado"]) == ("CLIENTE", "PENDIENTE_VERIFICACION")
    assert "ana" not in cuenta["usuario"].lower() and "@" not in cuenta["usuario"]
    assert "ana.prueba" not in str(fila).lower()
    assert cuenta["tiene_contacto"] is True  # el correo sirve también para el aviso de póliza


def test_un_correo_una_cuenta_sin_importar_mayusculas() -> None:
    _, _, usuarios, _, _ = _preparar()
    with pytest.raises(UsuarioDuplicado):
        usuarios.crear_cuenta_onboarding("  ana.prueba@ejemplo.COM ", "otra-clave-9")


def test_correo_o_clave_invalidos() -> None:
    _, _, usuarios, _, _ = _preparar()
    with pytest.raises(DatoInvalido):
        usuarios.crear_cuenta_onboarding("no-es-correo", CLAVE)
    with pytest.raises(DatoInvalido):
        usuarios.crear_cuenta_onboarding("b@ejemplo.com", "corta")


def test_pendiente_en_la_app_recibe_solo_el_scope_de_verificacion() -> None:
    _, _, _, sesiones, _ = _preparar()
    sesion = sesiones.iniciar_sesion("ana.prueba@ejemplo.com", CLAVE, "app")
    assert _scopes(sesion) == ["verificacion-pendiente"]


def test_pendiente_fuera_de_la_app_no_entra_y_no_cuenta_como_fallo() -> None:
    repo, _, _, sesiones, cuenta = _preparar()
    for canal in ("web", None):
        with pytest.raises(VerificacionPendiente):
            sesiones.iniciar_sesion(CORREO, CLAVE, canal)
    assert repo.filas[cuenta["id"]]["intentos_fallidos"] == 0
    # Con la clave mala el mensaje sigue siendo el genérico: no revela el estado.
    with pytest.raises(CredencialesInvalidas):
        sesiones.iniciar_sesion(CORREO, "clave-mala-123", "web")


def test_verificar_activa_la_cuenta_con_el_nombre_cifrado() -> None:
    repo, _, usuarios, sesiones, cuenta = _preparar()
    activa = usuarios.cerrar_verificacion(cuenta["id"], True, "ANA MARIA PRUEBA")
    assert activa["estado"] == "ACTIVO"
    assert "ANA" not in repo.filas[cuenta["id"]]["nombre_cifrado"]
    assert usuarios.nombre_de(cuenta["id"]) == "ANA MARIA PRUEBA"
    # Idempotente, y ya entra por la web con los scopes de CLIENTE.
    assert (
        usuarios.cerrar_verificacion(cuenta["id"], True, "ANA MARIA PRUEBA")["estado"] == "ACTIVO"
    )
    assert _scopes(sesiones.iniciar_sesion(CORREO, CLAVE, "web")) == ["consentimiento", "oferta"]


def test_rechazar_y_no_reabrir() -> None:
    _, _, usuarios, sesiones, cuenta = _preparar()
    assert usuarios.cerrar_verificacion(cuenta["id"], False)["estado"] == "RECHAZADA"
    assert usuarios.cerrar_verificacion(cuenta["id"], True, "X Y") is None
    assert _scopes(sesiones.iniciar_sesion(CORREO, CLAVE, "app")) == ["verificacion-pendiente"]


def test_cerrar_una_cuenta_que_no_es_del_onboarding_no_aplica() -> None:
    _, _, usuarios, _, _ = _preparar()
    otra = usuarios.crear("asesor.017", CLAVE, "ASESOR")
    assert usuarios.cerrar_verificacion(otra["id"], False) is None


def test_renovar_permisos_conserva_la_expiracion() -> None:
    _, reloj, usuarios, sesiones, cuenta = _preparar()
    sesion = sesiones.iniciar_sesion(CORREO, CLAVE, "app")
    usuarios.cerrar_verificacion(cuenta["id"], True, "ANA PRUEBA")
    reloj.ahora += timedelta(minutes=10)
    nueva = sesiones.renovar_permisos(cuenta["id"], sesion["expira_en"])
    assert _scopes(nueva) == ["consentimiento", "oferta"]
    assert nueva["expira_en"] == sesion["expira_en"]
    reloj.ahora += timedelta(hours=1)
    with pytest.raises(CredencialesInvalidas):
        sesiones.renovar_permisos(cuenta["id"], sesion["expira_en"])


def test_sin_correo_en_logs(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _, _, usuarios, sesiones, cuenta = _preparar()
    sesiones.iniciar_sesion(CORREO, CLAVE, "app")
    usuarios.cerrar_verificacion(cuenta["id"], True, "ANA PRUEBA")
    assert "ejemplo.com" not in caplog.text.lower() and "ANA" not in caplog.text


def test_rutas_http_del_onboarding() -> None:
    repo = RepositorioUsuariosMemoria()
    config = Configuracion("postgresql://no-se-usa", SECRETO, 60)
    app = create_app(
        almacen=AlmacenConsentimientosMemoria(),
        servicio_usuarios=ServicioUsuarios(
            repo, HASHER_RAPIDO, CIFRADOR_PRUEBAS, clave_titular=CLAVE_HMAC
        ),
        servicio_sesiones=ServicioSesiones(
            repo, HASHER_RAPIDO, SECRETO, 60, clave_correo=CLAVE_HMAC
        ),
        config=config,
    )
    with TestClient(app) as cliente:
        creada = cliente.post("/usuarios/onboarding", json={"correo": CORREO, "clave": CLAVE})
        assert creada.json()["estado"] == "PENDIENTE_VERIFICACION"
        cuenta_id = creada.json()["id"]
        dup = cliente.post("/usuarios/onboarding", json={"correo": CORREO, "clave": CLAVE})
        assert dup.status_code == 409
        registrado = cliente.post("/usuarios/onboarding/correo-registrado", json={"correo": CORREO})
        assert registrado.json() == {"registrado": True}
        libre = cliente.post(
            "/usuarios/onboarding/correo-registrado", json={"correo": "libre@ejemplo.com"}
        )
        assert libre.json() == {"registrado": False}
        web = cliente.post("/sesiones", json={"usuario": CORREO, "clave": CLAVE, "canal": "web"})
        assert (web.status_code, web.json()["detail"]) == (403, "VERIFICACION_PENDIENTE")
        app_ = cliente.post("/sesiones", json={"usuario": CORREO, "clave": CLAVE, "canal": "app"})
        assert app_.status_code == 200
        cerrada = cliente.post(
            f"/usuarios/{cuenta_id}/verificacion",
            json={"aprobada": True, "nombre": "Ana Prueba", "documento": "1000000001"},
        )
        assert cerrada.json()["estado"] == "ACTIVO"
        renovada = cliente.post(
            "/sesiones/renovar",
            json={"usuario_id": cuenta_id, "expira_en": app_.json()["expira_en"]},
        )
        assert renovada.status_code == 200
        assert renovada.json()["usuario"]["nombre"] == "Ana Prueba"
        assert renovada.json()["usuario"]["documento_enmascarado"] == "•••• 0001"
        otra = cliente.post(
            "/usuarios/onboarding", json={"correo": "b@ejemplo.com", "clave": CLAVE}
        )
        repetido = cliente.post(
            f"/usuarios/{otra.json()['id']}/verificacion",
            json={"aprobada": True, "nombre": "Otra", "documento": "1000000001"},
        )
        assert repetido.status_code == 409
        assert _scopes(renovada.json()) == ["consentimiento", "oferta"]
        assert (
            cliente.post("/usuarios/nadie/verificacion", json={"aprobada": False}).status_code
            == 404
        )


def test_verificar_con_documento_fija_el_titular_y_no_lo_repite() -> None:
    repo, _, usuarios, _, cuenta = _preparar()
    activa = usuarios.cerrar_verificacion(cuenta["id"], True, "Ana Prueba", "1000000001")
    assert activa["estado"] == "ACTIVO"
    fila = repo.filas[cuenta["id"]]
    assert fila["titular_id"] and "1000000001" not in str(fila)
    assert usuarios.identidad_de(cuenta["id"])["documento"] == "1000000001"
    otra = usuarios.crear_cuenta_onboarding("otra@ejemplo.com", CLAVE)
    with pytest.raises(UsuarioDuplicado):
        usuarios.cerrar_verificacion(otra["id"], True, "Otra Persona", "1000000001")
    assert repo.filas[otra["id"]]["estado"] == "PENDIENTE_VERIFICACION"
