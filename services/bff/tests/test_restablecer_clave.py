"""«Olvidé mi contraseña» (KAN-26): enlace de un solo uso, mensajes que no revelan y tope."""

import logging
from datetime import UTC, datetime, timedelta

from fakes import AclFalso, AuthFalso, clientes_falsos
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app

CORREO = "asesor@solventa.test"
CLAVE_NUEVA = "Nueva-clave-9"


def _pila():
    acl, auth = AclFalso(), AuthFalso()
    app = create_app(
        config=Configuracion.desde_entorno(), clientes=clientes_falsos(auth=auth, acl=acl)
    )
    cliente = TestClient(app)
    cliente.__enter__()
    return cliente, acl, auth


def _gql(cliente, consulta: str, enlace: str | None = None, ip: str | None = None) -> dict:
    cabeceras = {}
    if enlace:
        cabeceras["X-Enlace-Clave"] = enlace
    if ip:
        cabeceras["X-Forwarded-For"] = ip
    return cliente.post("/graphql/web", json={"query": consulta}, headers=cabeceras).json()


def _solicitar(cliente, correo: str = CORREO, ip: str | None = None) -> dict:
    return _gql(
        cliente, f'mutation {{ solicitarRestablecimientoClave(correo: "{correo}") }}', ip=ip
    )


def _token(acl: AclFalso) -> str:
    enlace = acl.notificaciones[-1]["datos"]["enlace"]
    return enlace.split("#t=")[1]


def _restablecer(cliente, token: str | None, clave: str = CLAVE_NUEVA) -> dict:
    q = (
        f'mutation {{ restablecerClave(clave: "{clave}") {{ __typename '
        "... on ErrorAutenticacion { codigo mensaje } } }"
    )
    return _gql(cliente, q, enlace=token)["data"]["restablecerClave"]


def test_responde_siempre_true_exista_o_no_la_cuenta() -> None:
    cliente, acl, _ = _pila()
    assert _solicitar(cliente)["data"]["solicitarRestablecimientoClave"] is True
    assert len(acl.notificaciones) == 1
    assert acl.notificaciones[0]["plantilla"] == "restablecer-clave"
    assert _solicitar(cliente, "nadie@ejemplo.com")["data"]["solicitarRestablecimientoClave"]
    assert _solicitar(cliente, "no-es-correo")["data"]["solicitarRestablecimientoClave"]
    assert len(acl.notificaciones) == 1  # nada se envía si no hay cuenta o el correo es malo


def test_el_enlace_lleva_el_token_en_el_fragmento_y_cambia_la_clave() -> None:
    cliente, acl, auth = _pila()
    _solicitar(cliente)
    enlace = acl.notificaciones[0]["datos"]["enlace"]
    assert "/restablecer-clave#t=" in enlace and "?" not in enlace
    token = _token(acl)
    assert _gql(cliente, "{ enlaceRestablecimientoVigente }", token)["data"] == {
        "enlaceRestablecimientoVigente": True
    }
    assert _restablecer(cliente, token)["__typename"] == "RestablecimientoExitoso"
    assert auth.claves["asesor.uno"] == CLAVE_NUEVA
    assert acl.notificaciones[-1]["plantilla"] == "clave-cambiada"


def test_el_enlace_sirve_una_sola_vez() -> None:
    cliente, acl, _ = _pila()
    _solicitar(cliente)
    token = _token(acl)
    _restablecer(cliente, token)
    repetido = _restablecer(cliente, token)
    assert repetido["codigo"] == "ENLACE_INVALIDO"
    assert _gql(cliente, "{ enlaceRestablecimientoVigente }", token)["data"] == {
        "enlaceRestablecimientoVigente": False
    }


def test_un_enlace_nuevo_invalida_el_anterior() -> None:
    cliente, acl, _ = _pila()
    _solicitar(cliente)
    primero = _token(acl)
    _solicitar(cliente)
    segundo = _token(acl)
    assert primero != segundo
    assert _restablecer(cliente, primero)["codigo"] == "ENLACE_INVALIDO"
    assert _restablecer(cliente, segundo)["__typename"] == "RestablecimientoExitoso"


def test_token_ausente_o_inventado_da_el_mismo_mensaje() -> None:
    cliente, _, _ = _pila()
    a, b = _restablecer(cliente, None), _restablecer(cliente, "inventado")
    assert a == b and a["codigo"] == "ENLACE_INVALIDO"


def test_una_clave_debil_no_gasta_el_enlace() -> None:
    cliente, acl, _ = _pila()
    _solicitar(cliente)
    token = _token(acl)
    debil = _restablecer(cliente, token, "clave")
    assert debil["codigo"] == "CLAVE_DEBIL"
    assert _restablecer(cliente, token)["__typename"] == "RestablecimientoExitoso"


def test_tope_por_correo_sin_avisar() -> None:
    cliente, acl, _ = _pila()
    for _i in range(5):
        assert _solicitar(cliente)["data"]["solicitarRestablecimientoClave"] is True
    assert len(acl.notificaciones) == 3


def test_tope_por_ip_aunque_cambie_el_correo() -> None:
    cliente, acl, auth = _pila()
    for i in range(12):
        auth.correos[f"c{i}@ejemplo.com"] = "u-1"
        _solicitar(cliente, f"c{i}@ejemplo.com", ip="203.0.113.9")
    assert len(acl.notificaciones) == 10


def test_enlace_vencido_a_los_30_minutos() -> None:
    import asyncio

    from app.servicio_restablecimiento import EnlaceInvalido, ServicioRestablecimiento
    from app.socios.modo_prueba import AlmacenMemoria

    ahora = [datetime(2026, 11, 10, 12, 0, tzinfo=UTC)]
    acl = AclFalso()
    servicio = ServicioRestablecimiento(
        clientes_falsos(auth=AuthFalso(), acl=acl),
        AlmacenMemoria(),
        "material",
        "http://front",
        reloj=lambda: ahora[0],
    )

    async def correr() -> None:
        await servicio.solicitar(CORREO, None)
        token = acl.notificaciones[0]["datos"]["enlace"].split("#t=")[1]
        assert await servicio.enlace_vigente(token)
        ahora[0] += timedelta(minutes=31)
        assert not await servicio.enlace_vigente(token)
        try:
            await servicio.restablecer(token, CLAVE_NUEVA)
        except EnlaceInvalido:
            return
        raise AssertionError("debió venir EnlaceInvalido")

    asyncio.run(correr())


def test_una_falla_de_auth_no_se_nota_al_solicitar() -> None:
    cliente, acl, auth = _pila()
    auth.falla = True
    assert _solicitar(cliente)["data"]["solicitarRestablecimientoClave"] is True
    assert acl.notificaciones == []


def test_sin_correo_ni_token_en_los_logs(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    cliente, acl, _ = _pila()
    _solicitar(cliente)
    token = _token(acl)
    _restablecer(cliente, token)
    assert "solventa.test" not in caplog.text and token not in caplog.text
    assert CLAVE_NUEVA not in caplog.text
