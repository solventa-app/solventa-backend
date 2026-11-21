"""API REST de socios (`/v1`, HU-W07) con dobles: token ES256, ErrorPublico, presupuesto, el socio
como intermediario del flujo de W03, consumo y convivencia con `/v1/graphql/*`."""

import re

import jwt
import pytest
from fakes import (
    CONSENTIMIENTO_VIGENTE,
    AuthFalso,
    PolicyFalso,
    RatingFalso,
    SociosFalso,
    clientes_falsos,
    token_de_prueba,
)
from fastapi.testclient import TestClient

from app.clientes import ErrorServicio, SolicitudInvalida
from app.configuracion import Configuracion
from app.identificadores import cliente_id_desde_documento
from app.main import CONSUMO_PERDIDO, create_app
from app.socios import scopes
from app.socios.api import DEVOLUCION_FALLIDA
from app.socios.autenticacion import SocioActual
from app.socios.modo_prueba import ModoPrueba

DOCUMENTO = "1012345678"
COTIZACION = {
    "consentimientoId": "c-socio",
    "documentoIdentidad": DOCUMENTO,
    "valorCredito": "150000000",
    "plazoMeses": 60,
    "direccionInmueble": "Calle 1 # 2-3",
}
CONTACTO = {"nombre": "Camila Rojas", "correo": "camila@correo.com"}


class Entorno:
    def __init__(self, presupuesto: int = 2) -> None:
        self.config = Configuracion.desde_entorno()
        titular = cliente_id_desde_documento(DOCUMENTO, self.config.cliente_id_clave)
        self.auth = AuthFalso(
            consentimientos={
                "c-socio": {
                    **CONSENTIMIENTO_VIGENTE,
                    "id": "c-socio",
                    "titular_id": titular,
                    "actor": {"rol": "SOCIO", "usuario_id": "socio-1"},
                },
                "c-otro": {
                    **CONSENTIMIENTO_VIGENTE,
                    "id": "c-otro",
                    "titular_id": titular,
                    "actor": {"rol": "SOCIO", "usuario_id": "socio-2"},
                },
            }
        )
        self.socios = SociosFalso(presupuesto=presupuesto)
        self.policy = PolicyFalso()
        self.rating = RatingFalso(
            guardadas={
                "o-1": {
                    **RatingFalso().oferta,
                    "clienteId": titular,
                    "consentimientoId": "c-socio",
                    "plazoMeses": 60,
                    "vigenteHasta": "2099-01-01T00:00:00+00:00",
                }
            }
        )
        self.clientes = clientes_falsos(
            auth=self.auth, socios=self.socios, policy=self.policy, rating=self.rating
        )

    def cliente(self) -> TestClient:
        return TestClient(create_app(config=self.config, clientes=self.clientes))

    def cabeceras(self, socio: str = "socio-1", entorno: str = "PRODUCCION") -> dict:
        return {"Authorization": f"Bearer {self.socios.token(socio, entorno)}"}


@pytest.fixture
def e() -> Entorno:
    return Entorno()


def _es_error_publico(respuesta, estado: int, codigo: str) -> dict:
    assert respuesta.status_code == estado, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["codigo"] == codigo
    assert set(cuerpo) >= {"codigo", "mensaje", "puedeReintentar"}
    return cuerpo


# ---------- Acceso ----------


def test_token_con_la_clave_del_socio(e) -> None:
    with e.cliente() as client:
        bien = client.post("/v1/token", json={"clientId": "sol_prod_1", "clave": "Clave-prod-1"})
        mal = client.post("/v1/token", json={"clientId": "sol_prod_1", "clave": "otra"})
        incompleto = client.post("/v1/token", json={"clientId": "sol_prod_1"})

    assert bien.status_code == 200
    assert bien.json()["tokenType"] == "Bearer" and bien.json()["entorno"] == "PRODUCCION"
    assert jwt.get_unverified_header(bien.json()["accessToken"])["alg"] == "ES256"
    _es_error_publico(mal, 401, "CREDENCIALES_INVALIDAS")
    _es_error_publico(incompleto, 422, "DATOS_INVALIDOS")


def test_las_rutas_publicas_no_piden_token(e) -> None:
    with e.cliente() as client:
        jwks = client.get("/v1/.well-known/jwks.json")
        contrato = client.get("/v1/openapi.json")
        docs = client.get("/v1/docs")

    assert jwks.json()["keys"][0]["kid"] == "kid-prueba"
    assert contrato.json()["openapi"] == "3.1.0" and "/v1/cotizaciones" in contrato.json()["paths"]
    assert docs.status_code == 200 and "/v1/openapi.json" in docs.text


@pytest.mark.parametrize(
    "cabecera",
    [
        None,
        "Basic abc",
        "Bearer no-es-un-jwt",
        f"Bearer {token_de_prueba('ASESOR')}",  # el HS256 de un usuario no sirve aquí
    ],
)
def test_sin_token_de_socio_valido_es_401(e, cabecera) -> None:
    with e.cliente() as client:
        respuesta = client.get(
            "/v1/consumo", headers={"Authorization": cabecera} if cabecera else {}
        )

    _es_error_publico(respuesta, 401, "TOKEN_INVALIDO")
    assert respuesta.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "cambios",
    [
        {"aud": "otra"},
        {"iss": "otro"},
        {"rol": "ASESOR"},
        {"exp": 1},
        # HA-SEG-004: sin el claim `scopes` o mal formado no es un token de AUTH.
        {"scopes": None},
        {"scopes": "cotizaciones"},
        {"scopes": [1, 2]},
    ],
)
def test_un_token_con_otros_claims_es_401(e, cambios) -> None:
    with e.cliente() as client:
        token = e.socios.token(**cambios)
        respuesta = client.get("/v1/consumo", headers={"Authorization": f"Bearer {token}"})

    _es_error_publico(respuesta, 401, "TOKEN_INVALIDO")


def test_el_jwks_se_guarda_y_se_lee_de_x_forwarded_authorization(e) -> None:
    with e.cliente() as client:
        for _ in range(3):
            cabecera = {"X-Forwarded-Authorization": e.cabeceras()["Authorization"]}
            assert client.get("/v1/consumo", headers=cabecera).status_code == 200

    assert e.socios.pedidos_jwks == 1


# ---------- Scopes del token (HA-SEG-004, EC025) ----------

PUBLICAS = {
    "/v1/token",
    "/v1/.well-known/jwks.json",
    "/v1/openapi.json",
    "/v1/docs",
    "/v2/openapi.json",
}
# La tabla de `socios-v1.json` («Scopes»): (método, ruta) → scope que exige.
SCOPE_DE_RUTA = {
    ("POST", "/v1/consentimientos"): scopes.COTIZACIONES,
    ("POST", "/v1/consentimientos/{consentimiento_id}/revocar"): scopes.COTIZACIONES,
    ("POST", "/v1/cotizaciones"): scopes.COTIZACIONES,
    ("POST", "/v1/cotizaciones-auto"): scopes.COTIZACIONES,
    ("POST", "/v2/cotizaciones"): scopes.COTIZACIONES,
    ("POST", "/v1/conversiones"): scopes.COTIZACIONES,
    ("POST", "/v1/propuestas"): scopes.PROPUESTAS,
    ("GET", "/v1/propuestas/{propuesta_id}"): scopes.PROPUESTAS,
    ("POST", "/v1/propuestas/{propuesta_id}/codigo"): scopes.PROPUESTAS,
    ("POST", "/v1/propuestas/{propuesta_id}/codigo/verificar"): scopes.PROPUESTAS,
    ("POST", "/v1/propuestas/{propuesta_id}/emitir"): scopes.PROPUESTAS,
    ("POST", "/v1/propuestas/{propuesta_id}/reenviar-enlace"): scopes.PROPUESTAS,
    ("GET", "/v1/polizas/{poliza_id}"): scopes.POLIZAS,
    ("GET", "/v1/polizas/{poliza_id}/documento"): scopes.POLIZAS,
    ("GET", "/v1/estado"): scopes.CUENTA,
    ("GET", "/v1/consumo"): scopes.CUENTA,
    ("GET", "/v1/ventas"): scopes.CUENTA,
    ("GET", "/v1/credenciales"): scopes.CUENTA,
    ("POST", "/v1/credenciales/{entorno}/rotar"): scopes.CUENTA,
}
MENSAJE_SIN_PERMISO = "El token no tiene permiso para esta operación."


def _rutas_v1_con_token() -> set[tuple[str, str]]:
    # Del esquema que FastAPI arma con todas las rutas del BFF (los routers incluidos no se ven
    # en `app.routes`), no del contrato: así aparece también una ruta que nadie documentó.
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos())
    return {
        (metodo.upper(), ruta)
        for ruta, operaciones in app.openapi()["paths"].items()
        if ruta.startswith(("/v1/", "/v2/"))
        and not ruta.startswith("/v1/graphql")
        and ruta not in PUBLICAS
        for metodo in operaciones
    }


def _url(ruta: str) -> str:
    return ruta.replace("{entorno}", "PRUEBAS").replace("{", "x-").replace("}", "")


def _con_scopes(e: Entorno, *lista: str) -> dict:
    return {"Authorization": f"Bearer {e.socios.token(scopes=list(lista))}"}


def test_cada_ruta_v1_con_token_declara_su_scope(e) -> None:
    """EC025 (100 % de las llamadas): toda ruta `/v1` con token, incluida una que se agregue
    mañana, responde 403 a un token sin scopes, y está en la tabla del contrato."""
    rutas = _rutas_v1_con_token()
    assert rutas == set(SCOPE_DE_RUTA), "una ruta /v1 o /v2 nueva necesita su scope (y su fila)"
    with e.cliente() as client:
        for metodo, ruta in sorted(rutas):
            respuesta = client.request(metodo, _url(ruta), headers=_con_scopes(e))
            _es_error_publico(respuesta, 403, "PROHIBIDO")


@pytest.mark.parametrize(("metodo", "ruta"), sorted(SCOPE_DE_RUTA))
def test_sin_el_scope_de_la_ruta_es_403(e, metodo, ruta) -> None:
    otros = sorted(scopes.TODOS - {SCOPE_DE_RUTA[(metodo, ruta)]})
    with e.cliente() as client:
        es = client.request(metodo, _url(ruta), headers=_con_scopes(e, *otros))
        en = client.request(
            metodo, _url(ruta), headers={**_con_scopes(e, *otros), "Accept-Language": "en"}
        )

    assert _es_error_publico(es, 403, "PROHIBIDO")["mensaje"] == MENSAJE_SIN_PERMISO
    assert _es_error_publico(en, 403, "PROHIBIDO")["mensaje"] == (
        "The token is not allowed to perform this operation."
    )


@pytest.mark.parametrize(("metodo", "ruta"), sorted(SCOPE_DE_RUTA))
def test_con_el_scope_de_la_ruta_pasa(e, metodo, ruta) -> None:
    with e.cliente() as client:
        respuesta = client.request(
            metodo, _url(ruta), headers=_con_scopes(e, SCOPE_DE_RUTA[(metodo, ruta)])
        )

    # La respuesta de siempre (200, 404 o 422 sin cuerpo), nunca el 403 del scope.
    assert respuesta.status_code != 403, respuesta.text


def test_un_403_por_scope_no_gasta_presupuesto(e) -> None:
    sin_cotizar = _con_scopes(e, scopes.PROPUESTAS, scopes.POLIZAS, scopes.CUENTA)
    with e.cliente() as client:
        respuesta = client.post("/v1/cotizaciones", json=COTIZACION, headers=sin_cotizar)

    _es_error_publico(respuesta, 403, "PROHIBIDO")
    assert e.socios.consumidas == {}


def test_un_403_por_scope_no_entra_al_modo_prueba(e, monkeypatch) -> None:
    llamadas = []

    async def cotizar(*args, **kwargs):
        llamadas.append(args)
        raise AssertionError("no debía llegar al modo prueba")

    monkeypatch.setattr(ModoPrueba, "cotizar", cotizar)
    token = e.socios.token(entorno="PRUEBAS", scopes=[scopes.CUENTA])
    with e.cliente() as client:
        respuesta = client.post(
            "/v1/cotizaciones", json=COTIZACION, headers={"Authorization": f"Bearer {token}"}
        )

    _es_error_publico(respuesta, 403, "PROHIBIDO")
    assert llamadas == [] and e.socios.consumidas == {}


def test_el_403_queda_en_el_consumo(e) -> None:
    with e.cliente() as client:
        client.post("/v1/cotizaciones", json=COTIZACION, headers=_con_scopes(e, scopes.CUENTA))

    assert e.socios.consumos == [("socio-1", "PRODUCCION", "cotizaciones", "ERROR_CLIENTE")]


def test_como_usuario_conserva_los_scopes() -> None:
    socio = SocioActual("s", "PRODUCCION", frozenset({"propuestas", "cotizaciones"}))

    assert socio.como_usuario().scopes == ("cotizaciones", "propuestas")


# ---------- Consentimiento y cotización ----------


def test_el_consentimiento_queda_a_nombre_del_socio(e) -> None:
    with e.cliente() as client:
        respuesta = client.post(
            "/v1/consentimientos",
            json={"documentoIdentidad": DOCUMENTO, "finalidad": "cotizacion", "versionTexto": "v1"},
            headers=e.cabeceras(),
        )

    assert respuesta.status_code == 201 and respuesta.json()["vigente"] is True
    registro = e.auth.registros_consentimiento[-1]
    assert e.auth.actores[-1] == {"rol": "SOCIO", "usuario_id": "socio-1"}
    assert registro["canal"] == "API"
    assert registro["titular_id"] == cliente_id_desde_documento(
        DOCUMENTO, e.config.cliente_id_clave
    )


def test_cotizar_descuenta_el_presupuesto_y_no_expone_el_desglose(e) -> None:
    with e.cliente() as client:
        respuesta = client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras())

    cuerpo = respuesta.json()
    assert respuesta.status_code == 200, respuesta.text
    assert cuerpo["presupuestoRestante"] == 1
    assert cuerpo["oferta"]["tipo"] == "COMPLETA"
    assert "desgloseScore" not in cuerpo["oferta"]
    assert cuerpo["oferta"]["fuentes"] == [{"nombre": "historial-crediticio", "estado": "OK"}]


def test_el_presupuesto_agotado_es_429_con_retry_after(e) -> None:
    with e.cliente() as client:
        codigos = [
            client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras()).status_code
            for _ in range(2)
        ]
        agotado = client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras())

    assert codigos == [200, 200]
    _es_error_publico(agotado, 429, "PRESUPUESTO_AGOTADO")
    assert int(agotado.headers["retry-after"]) > 0


def test_datos_invalidos_no_gastan_presupuesto(e) -> None:
    with e.cliente() as client:
        respuesta = client.post(
            "/v1/cotizaciones", json={**COTIZACION, "plazoMeses": 201}, headers=e.cabeceras()
        )

    _es_error_publico(respuesta, 422, "DATOS_INVALIDOS")
    assert e.socios.consumidas == {}


def test_el_consentimiento_de_otro_socio_no_sirve(e) -> None:
    with e.cliente() as client:
        respuesta = client.post(
            "/v1/cotizaciones",
            json={**COTIZACION, "consentimientoId": "c-otro"},
            headers=e.cabeceras(),
        )

    _es_error_publico(respuesta, 404, "NO_ENCONTRADO")
    assert e.socios.consumidas == {}


def test_si_la_cotizacion_falla_por_nosotros_la_unidad_vuelve(e) -> None:
    e.rating.error = ErrorServicio("caido")
    with e.cliente() as client:
        respuesta = client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras())

    cuerpo = _es_error_publico(respuesta, 503, "SERVICIO_NO_DISPONIBLE")
    assert cuerpo["puedeReintentar"] is True and respuesta.headers["retry-after"]
    assert e.socios.devoluciones == [("socio-1", "PRODUCCION")]


# ---------- Propuesta, identidad y emisión ----------


def test_aceptar_como_socio_lleva_el_contacto_y_la_tasa(e) -> None:
    with e.cliente() as client:
        respuesta = client.post(
            "/v1/propuestas", json={"ofertaId": "o-1", "contacto": CONTACTO}, headers=e.cabeceras()
        )
        sin_contacto = client.post(
            "/v1/propuestas", json={"ofertaId": "o-1"}, headers=e.cabeceras()
        )

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["estado"] == "ACEPTADA" and respuesta.json()["enlace"] is None
    assert e.policy.contactos_recibidos[-1] == CONTACTO
    assert e.policy.tasas_recibidas[-1] == "0.1000"
    _es_error_publico(sin_contacto, 422, "DATOS_INVALIDOS")


def test_otro_socio_no_ve_ni_opera_la_propuesta(e) -> None:
    with e.cliente() as client:
        pid = client.post(
            "/v1/propuestas", json={"ofertaId": "o-1", "contacto": CONTACTO}, headers=e.cabeceras()
        ).json()["id"]
        propia = client.get(f"/v1/propuestas/{pid}", headers=e.cabeceras())
        ajena = client.get(f"/v1/propuestas/{pid}", headers=e.cabeceras("socio-2"))
        emitir_ajena = client.post(f"/v1/propuestas/{pid}/emitir", headers=e.cabeceras("socio-2"))

    assert propia.status_code == 200
    _es_error_publico(ajena, 404, "NO_ENCONTRADO")
    _es_error_publico(emitir_ajena, 404, "NO_ENCONTRADO")


def test_codigo_verificar_y_emitir_sin_devolver_la_url(e) -> None:
    with e.cliente() as client:
        pid = client.post(
            "/v1/propuestas", json={"ofertaId": "o-1", "contacto": CONTACTO}, headers=e.cabeceras()
        ).json()["id"]
        codigo = client.post(f"/v1/propuestas/{pid}/codigo", json={}, headers=e.cabeceras())
        verificada = client.post(
            f"/v1/propuestas/{pid}/codigo/verificar",
            json={"codigo": "123456"},
            headers=e.cabeceras(),
        )
        emitida = client.post(f"/v1/propuestas/{pid}/emitir", headers=e.cabeceras())

    assert codigo.json()["correoEnmascarado"] == "a•••@correo.com"
    assert verificada.json()["estado"] == "IDENTIDAD_VERIFICADA"
    cuerpo = emitida.json()
    assert cuerpo["propuesta"]["estado"] == "EN_FIRMA"
    assert cuerpo["enlace"]["correoEnmascarado"]
    assert cuerpo["propuesta"]["enlace"]["vigente"] is not None
    assert "http" not in str(cuerpo) and "#t=" not in str(cuerpo)  # W07-17
    assert ("emitir", pid, "SOCIO", None) in e.policy.pedidos


def test_un_codigo_invalido_dice_cuantos_intentos_quedan(e) -> None:
    from app.clientes import FlujoRechazado

    e.policy.rechazos["verificar_codigo"] = FlujoRechazado("CODIGO_INVALIDO", intentos_restantes=3)
    with e.cliente() as client:
        pid = client.post(
            "/v1/propuestas", json={"ofertaId": "o-1", "contacto": CONTACTO}, headers=e.cabeceras()
        ).json()["id"]
        respuesta = client.post(
            f"/v1/propuestas/{pid}/codigo/verificar",
            json={"codigo": "000001"},
            headers=e.cabeceras(),
        )

    assert _es_error_publico(respuesta, 409, "CODIGO_INVALIDO")["intentosRestantes"] == 3


# ---------- Cuenta ----------


def test_credenciales_y_rotar_solo_con_token_de_produccion(e) -> None:
    with e.cliente() as client:
        lista = client.get("/v1/credenciales", headers=e.cabeceras())
        de_pruebas = client.get("/v1/credenciales", headers=e.cabeceras(entorno="PRUEBAS"))
        rotada = client.post("/v1/credenciales/PRUEBAS/rotar", headers=e.cabeceras())

    assert lista.json()["socio"] == {"id": "socio-1", "nombre": "Banco Demo"}
    assert {c["clientId"] for c in lista.json()["credenciales"]} == {"sol_prod_1", "sol_test_1"}
    _es_error_publico(de_pruebas, 403, "PROHIBIDO")
    assert rotada.json()["entorno"] == "PRUEBAS" and rotada.json()["clave"] == "Clave-nueva"


def test_consumo_y_ventas(e) -> None:
    with e.cliente() as client:
        consumo = client.get("/v1/consumo", headers=e.cabeceras())
        ventas = client.get("/v1/ventas", headers=e.cabeceras())
        fecha_mala = client.get("/v1/ventas", params={"desde": "ayer"}, headers=e.cabeceras())

    assert consumo.json()["porApi"][0] == {
        "api": "cotizaciones",
        "llamadas": 2,
        "errores": 0,
        "latenciaMediaMs": 120,
    }
    assert ventas.json()["tasaComision"] == "0.1000"
    _es_error_publico(fecha_mala, 422, "DATOS_INVALIDOS")


def test_cada_llamada_del_socio_queda_en_el_consumo_antes_de_responder(e) -> None:
    """KAN-30: el registro se espera dentro de la petición. Antes era una tarea suelta tras la
    respuesta, que en Cloud Run (`cpu_idle = true`) podía no correr nunca."""
    with e.cliente() as client:
        client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras())
        assert e.socios.consumos == [("socio-1", "PRODUCCION", "cotizaciones", "OK")]
        client.get("/v1/propuestas/no-existe", headers=e.cabeceras())
        assert e.socios.consumos[-1] == ("socio-1", "PRODUCCION", "propuestas", "ERROR_CLIENTE")


def test_si_el_consumo_no_se_registra_la_llamada_sigue_y_queda_en_la_metrica(e) -> None:
    async def cae(*_):
        raise ErrorServicio("AUTH caído")

    e.socios.registrar_consumo = cae
    antes = CONSUMO_PERDIDO._value.get()
    with e.cliente() as client:
        respuesta = client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras())

    assert respuesta.status_code == 200
    assert CONSUMO_PERDIDO._value.get() == antes + 1


def test_un_cuerpo_invalido_cuenta_en_el_consumo_del_socio(e) -> None:
    """KAN-30: FastAPI valida el cuerpo antes de la ruta, así que el 422 no se contaba."""
    with e.cliente() as client:
        respuesta = client.post("/v1/cotizaciones", json={"plazoMeses": 1}, headers=e.cabeceras())

    _es_error_publico(respuesta, 422, "DATOS_INVALIDOS")
    assert e.socios.consumos == [("socio-1", "PRODUCCION", "cotizaciones", "ERROR_CLIENTE")]


def test_un_cuerpo_invalido_sin_token_valido_es_401_y_no_cuenta(e) -> None:
    """HA-SEG-004: el token y su scope se revisan antes que el cuerpo (dependencia de FastAPI)."""
    with e.cliente() as client:
        respuesta = client.post(
            "/v1/cotizaciones", json={}, headers={"Authorization": "Bearer no-es-un-jwt"}
        )
        malformado = client.post(
            "/v1/cotizaciones",
            content=b"{no es json",
            headers={"Authorization": "Bearer no-es-un-jwt", "Content-Type": "application/json"},
        )

    _es_error_publico(respuesta, 401, "TOKEN_INVALIDO")
    # Un JSON que ni se puede leer se rechaza antes de las dependencias: sigue siendo 422.
    _es_error_publico(malformado, 422, "DATOS_INVALIDOS")
    assert e.socios.consumos == []


def test_las_consultas_de_la_cuenta_no_cuentan_en_el_consumo(e) -> None:
    """KAN-30: el portal las lee al abrirse; si contaran, mirar el consumo lo subiría."""
    with e.cliente() as client:
        for ruta in ("/v1/consumo", "/v1/estado", "/v1/ventas", "/v1/credenciales"):
            assert client.get(ruta, headers=e.cabeceras()).status_code == 200, ruta
        client.post("/v1/credenciales/PRUEBAS/rotar", headers=e.cabeceras())
        client.post(
            "/v1/conversiones",
            json={"moneda": "USD", "montos": [{"valor": "1000", "moneda": "COP"}]},
            headers=e.cabeceras(),
        )

    assert [c[2] for c in e.socios.consumos] == ["conversiones"]


def test_si_la_cotizacion_lanza_la_unidad_vuelve_al_presupuesto(e) -> None:
    """KAN-30: antes una excepción se volvía 503 y la unidad quedaba gastada."""

    async def explota(*_):
        raise RuntimeError("bug")

    with e.cliente() as client:
        client.app.state.servicio_oferta.solicitar = explota
        respuesta = client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras())

    _es_error_publico(respuesta, 503, "SERVICIO_NO_DISPONIBLE")
    assert e.socios.devoluciones == [("socio-1", "PRODUCCION")]
    assert e.socios.consumidas[("socio-1", "PRODUCCION")] == 0


def test_si_no_se_puede_devolver_la_unidad_queda_en_la_metrica(e) -> None:
    async def explota(*_):
        raise RuntimeError("bug")

    async def no_devuelve(*_):
        raise ErrorServicio("AUTH caído")

    antes = DEVOLUCION_FALLIDA._value.get()
    with e.cliente() as client:
        client.app.state.servicio_oferta.solicitar = explota
        e.socios.devolver = no_devuelve
        respuesta = client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras())

    _es_error_publico(respuesta, 503, "SERVICIO_NO_DISPONIBLE")
    assert DEVOLUCION_FALLIDA._value.get() == antes + 1


def test_un_rango_de_consumo_invalido_es_422_y_no_503(e) -> None:
    async def rechaza(*_):
        raise SolicitudInvalida("El rango va hasta 366 días.")

    e.socios.consumo = rechaza
    with e.cliente() as client:
        respuesta = client.get(
            "/v1/consumo?desde=2025-01-01&hasta=2026-11-19", headers=e.cabeceras()
        )

    _es_error_publico(respuesta, 422, "DATOS_INVALIDOS")


# ---------- Idempotency-Key (v0.54) ----------


def _con_llave(e, llave: str = "llave-1") -> dict:
    return {**e.cabeceras(), "Idempotency-Key": llave}


def test_un_reintento_con_la_misma_llave_no_descuenta_ni_cuenta_otra_vez(e) -> None:
    """El 504 del gateway: el BFF terminó la cotización y el socio reintenta con la misma llave."""
    with e.cliente() as client:
        primera = client.post("/v1/cotizaciones", json=COTIZACION, headers=_con_llave(e))
        segunda = client.post("/v1/cotizaciones", json=COTIZACION, headers=_con_llave(e))

    assert primera.status_code == segunda.status_code == 200
    assert segunda.json() == primera.json()
    assert segunda.headers["idempotent-replayed"] == "true"
    assert "idempotent-replayed" not in primera.headers
    assert e.socios.consumidas[("socio-1", "PRODUCCION")] == 1
    assert [c for c in e.socios.consumos if c[2] == "cotizaciones"] == [
        ("socio-1", "PRODUCCION", "cotizaciones", "OK")
    ]


def test_sin_llave_cada_cotizacion_descuenta(e) -> None:
    with e.cliente() as client:
        client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras())
        client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras())

    assert e.socios.consumidas[("socio-1", "PRODUCCION")] == 2


def test_llaves_distintas_son_cotizaciones_distintas(e) -> None:
    with e.cliente() as client:
        client.post("/v1/cotizaciones", json=COTIZACION, headers=_con_llave(e, "a"))
        client.post("/v1/cotizaciones", json=COTIZACION, headers=_con_llave(e, "b"))

    assert e.socios.consumidas[("socio-1", "PRODUCCION")] == 2


def test_la_misma_llave_con_otro_cuerpo_es_422_y_no_descuenta(e) -> None:
    with e.cliente() as client:
        client.post("/v1/cotizaciones", json=COTIZACION, headers=_con_llave(e))
        otra = client.post(
            "/v1/cotizaciones", json={**COTIZACION, "plazoMeses": 120}, headers=_con_llave(e)
        )

    _es_error_publico(otra, 422, "IDEMPOTENCIA_EN_CONFLICTO")
    assert e.socios.consumidas[("socio-1", "PRODUCCION")] == 1


def test_la_llave_de_un_socio_no_le_sirve_a_otro(e) -> None:
    with e.cliente() as client:
        client.post("/v1/cotizaciones", json=COTIZACION, headers=_con_llave(e))
        almacen = client.app.state.idempotencia_socios

    assert all(":socio-1:PRODUCCION:cotizaciones:" in clave for clave in almacen.datos)


def test_con_la_primera_en_curso_responde_409_con_retry_after(e) -> None:
    async def reclamada(*_):
        return {"estado": "EN_CURSO"}

    with e.cliente() as client:
        client.app.state.idempotencia_socios.reclamar = reclamada
        respuesta = client.post("/v1/cotizaciones", json=COTIZACION, headers=_con_llave(e))

    cuerpo = _es_error_publico(respuesta, 409, "SOLICITUD_EN_CURSO")
    assert cuerpo["puedeReintentar"] is True and respuesta.headers["retry-after"] == "1"
    assert e.socios.consumidas.get(("socio-1", "PRODUCCION"), 0) == 0


def test_si_la_cotizacion_falla_la_llave_se_libera(e) -> None:
    sin_consentimiento = {**COTIZACION, "consentimientoId": "c-otro"}
    with e.cliente() as client:
        primera = client.post("/v1/cotizaciones", json=sin_consentimiento, headers=_con_llave(e))
        segunda = client.post("/v1/cotizaciones", json=sin_consentimiento, headers=_con_llave(e))
        almacen = client.app.state.idempotencia_socios

    assert primera.status_code == segunda.status_code
    assert segunda.json()["codigo"] != "SOLICITUD_EN_CURSO"
    assert almacen.datos == {}


@pytest.mark.parametrize("llave", ["", "con espacio", "x" * 256])
def test_una_llave_invalida_es_422(e, llave) -> None:
    with e.cliente() as client:
        respuesta = client.post("/v1/cotizaciones", json=COTIZACION, headers=_con_llave(e, llave))

    _es_error_publico(respuesta, 422, "DATOS_INVALIDOS")
    assert e.socios.consumidas.get(("socio-1", "PRODUCCION"), 0) == 0


def test_si_redis_no_responde_se_cotiza_sin_idempotencia(e) -> None:
    async def caido(*_):
        raise ConnectionError("redis")

    with e.cliente() as client:
        client.app.state.idempotencia_socios.reclamar = caido
        respuesta = client.post("/v1/cotizaciones", json=COTIZACION, headers=_con_llave(e))

    assert respuesta.status_code == 200
    assert e.socios.consumidas[("socio-1", "PRODUCCION")] == 1


def test_el_modo_prueba_tambien_respeta_la_llave(e) -> None:
    cabeceras = {**e.cabeceras(entorno="PRUEBAS"), "Idempotency-Key": "llave-prueba"}
    with e.cliente() as client:
        consentimiento = client.post(
            "/v1/consentimientos",
            json={
                "documentoIdentidad": "1000000001",
                "finalidad": "cotizacion",
                "versionTexto": "v1",
            },
            headers=cabeceras,
        )
        cuerpo = {**COTIZACION, "consentimientoId": consentimiento.json()["id"],
                  "documentoIdentidad": "1000000001"}  # fmt: skip
        primera = client.post("/v1/cotizaciones", json=cuerpo, headers=cabeceras)
        segunda = client.post("/v1/cotizaciones", json=cuerpo, headers=cabeceras)

    assert primera.status_code == 200, primera.text
    assert segunda.json() == primera.json()
    assert e.socios.consumidas[("socio-1", "PRUEBAS")] == 1


def test_una_falla_inesperada_es_503_controlado_y_en_ingles(e) -> None:
    async def explota(*_):
        raise RuntimeError("bug")

    e.socios.consumo = explota
    with e.cliente() as client:
        respuesta = client.get("/v1/consumo", headers={**e.cabeceras(), "Accept-Language": "en"})

    cuerpo = _es_error_publico(respuesta, 503, "SERVICIO_NO_DISPONIBLE")
    assert cuerpo["mensaje"] == "The service is not available right now. Please try again."


def test_la_api_de_socios_no_tapa_v1_graphql(e) -> None:
    with e.cliente() as client:
        respuesta = client.post("/v1/graphql/web", json={"query": "{ __typename }"})

    assert respuesta.status_code == 200 and respuesta.json()["data"] == {"__typename": "Query"}


def test_el_contrato_y_las_rutas_coinciden(e) -> None:
    """Contrato primero (regla 8): cada operación del OpenAPI de socios tiene su ruta en el BFF y
    al revés."""

    # Los nombres de los parámetros cambian de estilo (`propuestaId` en el contrato, `propuesta_id`
    # en Python): se compara la forma de la ruta.
    def forma(camino: str) -> str:
        return re.sub(r"\{[^}]+\}", "{}", camino)

    app = create_app(config=e.config, clientes=e.clientes)
    rutas = {
        (metodo.upper(), forma(camino))
        for camino, ops in app.openapi()["paths"].items()
        if camino.startswith("/v1/") and not camino.startswith("/v1/graphql")
        for metodo in ops
    }
    with e.cliente() as client:
        contrato = client.get("/v1/openapi.json").json()
    esperadas = {
        (metodo.upper(), forma(camino))
        for camino, ops in contrato["paths"].items()
        for metodo in ops
    }

    assert rutas == esperadas


def test_conversiones_convierte_sin_descontar_presupuesto(e) -> None:
    cuerpo = {"montos": [{"valor": "48500.00", "moneda": "COP"}], "moneda": "USD"}
    with e.cliente() as client:
        respuesta = client.post("/v1/conversiones", json=cuerpo, headers=e.cabeceras())
        moneda_mala = client.post(
            "/v1/conversiones", json={**cuerpo, "moneda": "XXX"}, headers=e.cabeceras()
        )
        sin_token = client.post("/v1/conversiones", json=cuerpo)
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["montos"][0]["moneda"] == "USD"
    assert moneda_mala.status_code == 422
    assert sin_token.status_code == 401
