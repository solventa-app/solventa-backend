"""Socios de distribución (HU-W07): tokens ES256, credenciales, presupuesto, consumo y la CLI."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fakes import (
    CIFRADOR_PRUEBAS,
    HASHER_RAPIDO,
    AlmacenConsentimientosMemoria,
    RepositorioSociosMemoria,
    RepositorioUsuariosMemoria,
)
from fastapi.testclient import TestClient

from app.configuracion import SOCIOS_CLAVE_FIRMA_LOCAL
from app.crear_socio import main as crear_socio
from app.main import create_app
from app.socios import (
    MAX_INTENTOS,
    CredencialesSocioInvalidas,
    DatoSocioInvalido,
    PresupuestoAgotado,
    ServicioSocios,
)
from app.tokens_socio import AUDIENCIA, EMISOR, FirmadorTokensSocio

CREDENCIALES = {
    "PRODUCCION": ("sol_prod_demo1", "clave-produccion-demo-1"),
    "PRUEBAS": ("sol_test_demo1", "clave-pruebas-demo-1"),
}


class Reloj:
    def __init__(self) -> None:
        # Hora real: el token se verifica con `jwt.decode`, que mira el reloj del sistema.
        self.ahora = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.ahora


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()


@pytest.fixture
def servicio(reloj) -> ServicioSocios:
    servicio = ServicioSocios(
        RepositorioSociosMemoria(),
        CIFRADOR_PRUEBAS,
        FirmadorTokensSocio(SOCIOS_CLAVE_FIRMA_LOCAL, reloj=reloj),
        reloj=reloj,
    )
    servicio.guardar(
        "socio-demo-1", "Banco Demo", "0.10", {"PRODUCCION": 2, "PRUEBAS": 5}, CREDENCIALES
    )
    return servicio


def _verificar(token: str, jwks: dict) -> dict:
    clave = jwt.PyJWK(jwks["keys"][0]).key
    return jwt.decode(token, clave, algorithms=["ES256"], audience=AUDIENCIA, issuer=EMISOR)


def test_el_token_es_es256_y_se_valida_con_el_jwks(servicio) -> None:
    sesion = servicio.emitir_token(*CREDENCIALES["PRODUCCION"])

    claims = _verificar(sesion["token"], servicio.jwks())
    assert claims["sub"] == "socio-demo-1"
    assert claims["rol"] == "SOCIO"
    assert claims["entorno"] == "PRODUCCION"
    assert jwt.get_unverified_header(sesion["token"])["kid"] == servicio.jwks()["keys"][0]["kid"]
    assert claims["exp"] - claims["iat"] == 15 * 60


def test_la_clave_de_pruebas_da_un_token_de_pruebas(servicio) -> None:
    sesion = servicio.emitir_token(*CREDENCIALES["PRUEBAS"])

    assert _verificar(sesion["token"], servicio.jwks())["entorno"] == "PRUEBAS"


def test_el_jwks_no_expone_la_clave_privada(servicio) -> None:
    (jwk,) = servicio.jwks()["keys"]

    assert "d" not in jwk
    assert jwk["alg"] == "ES256" and jwk["crv"] == "P-256"


@pytest.mark.parametrize(
    "client_id,clave",
    [("sol_prod_demo1", "otra-clave"), ("sol_prod_no_existe", "clave-produccion-demo-1")],
)
def test_credenciales_erroneas_dan_el_mismo_error(servicio, client_id, clave) -> None:
    with pytest.raises(CredencialesSocioInvalidas, match="Credenciales inválidas"):
        servicio.emitir_token(client_id, clave)


def test_tras_varios_fallos_la_credencial_se_bloquea_aunque_la_clave_sea_correcta(
    servicio, reloj
) -> None:
    for _ in range(MAX_INTENTOS):
        with pytest.raises(CredencialesSocioInvalidas):
            servicio.emitir_token("sol_prod_demo1", "mala")
    with pytest.raises(CredencialesSocioInvalidas):
        servicio.emitir_token(*CREDENCIALES["PRODUCCION"])

    reloj.ahora += timedelta(minutes=16)
    assert servicio.emitir_token(*CREDENCIALES["PRODUCCION"])["socio_id"] == "socio-demo-1"


def test_un_socio_suspendido_no_recibe_token(servicio) -> None:
    servicio._repo.socios["socio-demo-1"]["estado"] = "SUSPENDIDO"

    with pytest.raises(CredencialesSocioInvalidas):
        servicio.emitir_token(*CREDENCIALES["PRODUCCION"])


def test_las_credenciales_se_muestran_con_la_clave_y_se_guardan_cifradas(servicio) -> None:
    credenciales = {c["entorno"]: c for c in servicio.credenciales("socio-demo-1")}

    assert credenciales["PRODUCCION"]["clave"] == "clave-produccion-demo-1"
    guardadas = servicio._repo.credenciales
    assert all("clave-" not in c["clave_cifrada"] for c in guardadas)


def test_rotar_revoca_la_clave_anterior(servicio) -> None:
    nueva = servicio.rotar("socio-demo-1", "PRODUCCION")

    with pytest.raises(CredencialesSocioInvalidas):
        servicio.emitir_token(*CREDENCIALES["PRODUCCION"])
    assert servicio.emitir_token(nueva["client_id"], nueva["clave"])["entorno"] == "PRODUCCION"
    assert nueva["client_id"].startswith("sol_prod_")
    assert len(servicio.credenciales("socio-demo-1")) == 2


def test_el_presupuesto_se_agota_y_devolver_libera_una_unidad(servicio) -> None:
    assert servicio.reservar("socio-demo-1", "PRODUCCION", "cotizaciones") == 1
    assert servicio.reservar("socio-demo-1", "PRODUCCION", "cotizaciones") == 0
    with pytest.raises(PresupuestoAgotado):
        servicio.reservar("socio-demo-1", "PRODUCCION", "cotizaciones")

    assert servicio.devolver("socio-demo-1", "PRODUCCION", "cotizaciones") == 1
    # El de pruebas es aparte.
    assert servicio.reservar("socio-demo-1", "PRUEBAS", "cotizaciones") == 4


def test_solo_las_cotizaciones_tienen_presupuesto(servicio) -> None:
    with pytest.raises(DatoSocioInvalido):
        servicio.reservar("socio-demo-1", "PRODUCCION", "propuestas")


def test_el_consumo_agrupa_por_api_y_por_dia(servicio, reloj) -> None:
    servicio.registrar_consumo("socio-demo-1", "PRODUCCION", "cotizaciones", "OK", 100)
    servicio.registrar_consumo("socio-demo-1", "PRODUCCION", "cotizaciones", "ERROR", 300)
    servicio.registrar_consumo("socio-demo-1", "PRODUCCION", "propuestas", "OK", 50)
    servicio.registrar_consumo("socio-demo-1", "PRUEBAS", "propuestas", "OK", 10)
    servicio.reservar("socio-demo-1", "PRODUCCION", "cotizaciones")
    hoy = datetime.now(UTC).date()

    consumo = servicio.consumo("socio-demo-1", "PRODUCCION", hoy, hoy)

    assert consumo["presupuesto"] == {"asignadas": 2, "consumidas": 1, "restantes": 1}
    assert consumo["por_api"] == [
        {"api": "cotizaciones", "llamadas": 2, "errores": 1, "latencia_media_ms": 200},
        {"api": "propuestas", "llamadas": 1, "errores": 0, "latencia_media_ms": 50},
    ]
    assert consumo["diario"] == [{"fecha": hoy, "llamadas": 3}]


def test_guardar_es_idempotente_y_no_duplica_credenciales(servicio) -> None:
    resultado = servicio.guardar(
        "socio-demo-1", "Banco Demo S.A.", "0.12", {"PRODUCCION": 10, "PRUEBAS": 5}, CREDENCIALES
    )

    assert resultado["credenciales_creadas"] == []
    assert resultado["socio"]["tasa_comision"] == "0.1200"
    assert len(servicio.credenciales("socio-demo-1")) == 2
    assert servicio.reservar("socio-demo-1", "PRODUCCION", "cotizaciones") == 9


@pytest.mark.parametrize("tasa", ["1", "-0.1", "diez"])
def test_la_tasa_va_entre_cero_y_uno(servicio, tasa) -> None:
    with pytest.raises(DatoSocioInvalido):
        servicio.guardar("socio-demo-2", "Otro", tasa, {})


def test_la_cli_crea_credenciales_al_azar_y_las_muestra_una_vez(capsys, reloj) -> None:
    servicio = ServicioSocios(
        RepositorioSociosMemoria(), CIFRADOR_PRUEBAS, FirmadorTokensSocio(SOCIOS_CLAVE_FIRMA_LOCAL)
    )
    argumentos = [
        "--id", "socio-demo-2", "--nombre", "Fintech Demo", "--tasa-comision", "0.08",
        "--presupuesto-cotizaciones", "100", "--presupuesto-cotizaciones-pruebas", "500",
    ]  # fmt: skip

    assert crear_socio(argumentos, servicio) == 0
    primera = capsys.readouterr().out
    assert crear_socio(argumentos, servicio) == 0
    segunda = capsys.readouterr().out

    assert primera.count("client_id=") == 2
    assert segunda == ""


def test_la_cli_usa_las_credenciales_fijas_del_entorno(monkeypatch, capsys) -> None:
    monkeypatch.setenv("SOCIO_CLIENT_ID_PRODUCCION", "sol_prod_fijo")
    monkeypatch.setenv("SOCIO_CLAVE_PRODUCCION", "clave-fija-local")
    servicio = ServicioSocios(
        RepositorioSociosMemoria(), CIFRADOR_PRUEBAS, FirmadorTokensSocio(SOCIOS_CLAVE_FIRMA_LOCAL)
    )

    crear_socio(
        ["--id", "s1", "--nombre", "Socio", "--tasa-comision", "0.1",
         "--presupuesto-cotizaciones", "1", "--presupuesto-cotizaciones-pruebas", "1"],
        servicio,
    )  # fmt: skip

    assert servicio.emitir_token("sol_prod_fijo", "clave-fija-local")["entorno"] == "PRODUCCION"
    salida = capsys.readouterr().out
    assert "clave-fija-local" not in salida and salida.count("client_id=") == 1


# ---------- HTTP ----------


@pytest.fixture
def cliente(servicio) -> TestClient:
    from app.sesiones import ServicioSesiones
    from app.usuarios import ServicioUsuarios

    repositorio = RepositorioUsuariosMemoria()
    app = create_app(
        almacen=AlmacenConsentimientosMemoria(),
        servicio_usuarios=ServicioUsuarios(repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS),
        servicio_sesiones=ServicioSesiones(repositorio, HASHER_RAPIDO, "x" * 32, 60),
        servicio_socios=servicio,
    )
    return TestClient(app)


def test_http_token_y_jwks(cliente) -> None:
    respuesta = cliente.post(
        "/socios/tokens", json={"client_id": "sol_prod_demo1", "clave": "clave-produccion-demo-1"}
    )

    assert respuesta.status_code == 200
    jwks = cliente.get("/.well-known/jwks.json").json()
    assert _verificar(respuesta.json()["token"], jwks)["sub"] == "socio-demo-1"
    assert cliente.post("/socios/tokens", json={"client_id": "x", "clave": "y"}).status_code == 401


def test_http_presupuesto_agotado_es_429(cliente) -> None:
    url = "/socios/socio-demo-1/presupuesto/PRODUCCION/cotizaciones/reservar"
    assert [cliente.post(url).status_code for _ in range(3)] == [200, 200, 429]


def test_http_socio_credenciales_rotar_y_consumo(cliente) -> None:
    assert cliente.get("/socios/socio-demo-1").json()["tasa_comision"] == "0.1000"
    assert cliente.get("/socios/no-existe").status_code == 404
    assert len(cliente.get("/socios/socio-demo-1/credenciales").json()) == 2
    assert cliente.post("/socios/socio-demo-1/credenciales/PRUEBAS/rotar").status_code == 200
    assert cliente.post("/socios/socio-demo-1/credenciales/OTRO/rotar").status_code == 422
    assert cliente.post("/socios/nombres", json={"ids": ["socio-demo-1"]}).json() == {
        "socio-demo-1": "Banco Demo"
    }

    registro = cliente.post(
        "/socios/socio-demo-1/consumos",
        json={"entorno": "PRODUCCION", "api": "cotizaciones", "resultado": "OK", "latencia_ms": 80},
    )
    assert registro.status_code == 204
    consumo = cliente.get("/socios/socio-demo-1/consumo", params={"entorno": "PRODUCCION"}).json()
    assert consumo["por_api"][0]["llamadas"] == 1


def test_http_un_socio_registra_el_consentimiento_como_actor(cliente) -> None:
    respuesta = cliente.post(
        "/consentimientos",
        json={
            "finalidad": "cotizacion",
            "version_texto": "v1",
            "actor": {"rol": "SOCIO", "usuario_id": "socio-demo-1"},
            "documento_identidad": "1234567890",
        },
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["actor"] == {"rol": "SOCIO", "usuario_id": "socio-demo-1"}
