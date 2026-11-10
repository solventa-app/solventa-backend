"""«Olvidé mi contraseña» (KAN-26): buscar la cuenta por correo y cambiar la clave en AUTH."""

from datetime import timedelta

import pytest
from fakes import (
    CIFRADOR_PRUEBAS,
    HASHER_RAPIDO,
    AlmacenConsentimientosMemoria,
    Reloj,
    RepositorioRefrescosMemoria,
    RepositorioUsuariosMemoria,
)
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app
from app.sesiones import CredencialesInvalidas, RefreshInvalido, ServicioSesiones
from app.usuarios import CuentaNoActiva, DatoInvalido, ServicioUsuarios

SECRETO = "secreto-de-prueba-con-32-bytes-o-mas"
CLAVE_HMAC = "clave-hmac-de-prueba"
CORREO = "operador@solventa.local"
CLAVE = "Clave-segura-1"
NUEVA = "Nueva-clave-9"


def _preparar():
    repo = RepositorioUsuariosMemoria()
    refrescos = RepositorioRefrescosMemoria()
    usuarios = ServicioUsuarios(repo, HASHER_RAPIDO, CIFRADOR_PRUEBAS, clave_titular=CLAVE_HMAC)
    sesiones = ServicioSesiones(
        repo,
        HASHER_RAPIDO,
        SECRETO,
        15,
        reloj=Reloj(),
        clave_correo=CLAVE_HMAC,
        refrescos=refrescos,
    )
    cuenta = usuarios.crear("operador.uno", CLAVE, "BACKOFFICE", correo=CORREO)
    return repo, usuarios, sesiones, cuenta


def test_busca_la_cuenta_activa_por_correo_sin_importar_mayusculas() -> None:
    _, usuarios, _, cuenta = _preparar()

    assert usuarios.id_para_restablecer("  Operador@Solventa.LOCAL ") == cuenta["id"]


@pytest.mark.parametrize("correo", ["nadie@ejemplo.com", "no-es-un-correo", ""])
def test_un_correo_sin_cuenta_o_invalido_da_none_sin_error(correo: str) -> None:
    _, usuarios, _, _ = _preparar()

    assert usuarios.id_para_restablecer(correo) is None


def test_una_cuenta_suspendida_no_se_restablece() -> None:
    _, usuarios, _, cuenta = _preparar()
    usuarios.suspender(cuenta["id"])

    assert usuarios.id_para_restablecer(CORREO) is None
    with pytest.raises(CuentaNoActiva):
        usuarios.restablecer_clave(cuenta["id"], NUEVA)


def test_restablecer_cambia_la_clave_y_devuelve_el_correo() -> None:
    _, usuarios, sesiones, cuenta = _preparar()

    correo = usuarios.restablecer_clave(cuenta["id"], NUEVA)

    assert correo == CORREO
    assert sesiones.iniciar_sesion(CORREO, NUEVA)["usuario"]["id"] == cuenta["id"]
    with pytest.raises(CredencialesInvalidas):
        sesiones.iniciar_sesion(CORREO, CLAVE)


def test_la_clave_nueva_cumple_las_mismas_reglas_del_alta() -> None:
    _, usuarios, _, cuenta = _preparar()

    with pytest.raises(DatoInvalido):
        usuarios.restablecer_clave(cuenta["id"], "corta")
    with pytest.raises(DatoInvalido):
        usuarios.restablecer_clave(cuenta["id"], "sin-mayuscula-1")


def test_restablecer_levanta_el_bloqueo_por_intentos_fallidos() -> None:
    repo, usuarios, _, cuenta = _preparar()
    repo.filas[cuenta["id"]].update(intentos_fallidos=4, bloqueado_hasta=Reloj().ahora)

    usuarios.restablecer_clave(cuenta["id"], NUEVA)

    fila = repo.filas[cuenta["id"]]
    assert fila["intentos_fallidos"] == 0 and fila["bloqueado_hasta"] is None


def test_http_cierra_todas_las_sesiones_de_la_cuenta() -> None:
    repo = RepositorioUsuariosMemoria()
    refrescos = RepositorioRefrescosMemoria()
    reloj = Reloj()
    usuarios = ServicioUsuarios(repo, HASHER_RAPIDO, CIFRADOR_PRUEBAS, clave_titular=CLAVE_HMAC)
    sesiones = ServicioSesiones(
        repo,
        HASHER_RAPIDO,
        SECRETO,
        15,
        reloj=reloj,
        clave_correo=CLAVE_HMAC,
        refrescos=refrescos,
    )
    cuenta = usuarios.crear("operador.uno", CLAVE, "BACKOFFICE", correo=CORREO)
    refresh = sesiones.iniciar_sesion(CORREO, CLAVE, "web")["refresh_token"]
    app = create_app(
        almacen=AlmacenConsentimientosMemoria(),
        servicio_usuarios=usuarios,
        servicio_sesiones=sesiones,
        config=Configuracion("postgresql://no-se-usa", SECRETO, 15),
    )
    with TestClient(app) as cliente:
        buscado = cliente.post("/usuarios/restablecimiento/buscar", json={"correo": CORREO})
        assert buscado.json() == {"usuario_id": cuenta["id"]}
        ninguno = cliente.post("/usuarios/restablecimiento/buscar", json={"correo": "x@y.co"})
        assert ninguno.json() == {"usuario_id": None}

        debil = cliente.post(f"/usuarios/{cuenta['id']}/clave", json={"clave": "corta"})
        assert debil.status_code == 400
        ok = cliente.post(f"/usuarios/{cuenta['id']}/clave", json={"clave": NUEVA})
        assert (ok.status_code, ok.json()) == (200, {"correo": CORREO})
        ajena = cliente.post("/usuarios/no-existe/clave", json={"clave": NUEVA})
        assert ajena.status_code == 404

    reloj.ahora += timedelta(seconds=1)
    with pytest.raises(RefreshInvalido):
        sesiones.renovar_con_refresh(refresh, "web")


def test_el_correo_no_se_loguea(caplog) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    _, usuarios, _, cuenta = _preparar()
    usuarios.id_para_restablecer(CORREO)
    usuarios.restablecer_clave(cuenta["id"], NUEVA)

    assert "solventa.local" not in caplog.text and NUEVA not in caplog.text
