"""Verificación de identidad del onboarding móvil (HU-M01): adaptador de Didit, caché cifrada por
huella de las imágenes y degradación sin 5xx."""

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_main import CONFIG_SIN_PROVEEDORES

from app.adapters.identidad_didit import (
    MOTIVO_DOCUMENTO,
    MOTIVO_ROSTRO,
    MOTIVO_VIDA,
    AdaptadorIdentidadDidit,
)
from app.application.cache_identidad import CacheIdentidad, huella_imagenes
from app.application.circuitos import NOMBRE_CIRCUITO_IDENTIDAD, construir_fabrica
from app.application.configuracion import Configuracion
from app.application.servicio_identidad import (
    MOTIVO_CIRCUITO_ABIERTO,
    MOTIVO_IMAGEN_NO_DISPONIBLE,
    MOTIVO_PROVEEDOR_NO_DISPONIBLE,
    DescargadorImagenes,
    ImagenNoDisponible,
    ServicioIdentidad,
)
from app.domain.puertos import DatosDocumento, ImagenesIdentidad, ResultadoIdentidad
from app.main import create_app

IMAGENES = ImagenesIdentidad(anverso=b"anverso", rostro=b"rostro", reverso=b"reverso")
LLAVE = bytes(range(32))

DOCUMENTO_OK = {
    "status": "Approved",
    "document_type": "Identity Card",
    "document_number": "1020304050",
    "first_name": "Ana María",
    "last_name": "Pérez Gómez",
    "date_of_birth": "1995-04-12",
    "issuing_state": "COL",
    "warnings": [],
}
VIDA_OK = {"status": "Approved", "method": "PASSIVE", "score": 95, "warnings": []}
ROSTRO_OK = {"status": "Approved", "score": 99.4, "warnings": []}


def _rechazo(riesgo: str, score: float = 10) -> dict:
    return {
        "status": "Declined",
        "score": score,
        "warnings": [
            {"risk": riesgo, "log_type": "error"},
            {"risk": "SOLO_INFORMATIVO", "log_type": "information"},
        ],
    }


def _didit(documento=DOCUMENTO_OK, vida=VIDA_OK, rostro=ROSTRO_OK, peticiones=None):
    def manejar(peticion: httpx.Request) -> httpx.Response:
        if peticiones is not None:
            peticiones.append(peticion)
        ruta = peticion.url.path
        if ruta == "/v3/id-verification/":
            return httpx.Response(200, json={"request_id": "r1", "id_verification": documento})
        if ruta == "/v3/passive-liveness/":
            return httpx.Response(200, json={"request_id": "r2", "liveness": vida})
        if ruta == "/v3/face-match/":
            return httpx.Response(200, json={"request_id": "r3", "face_match": rostro})
        return httpx.Response(404)

    cliente = httpx.Client(
        base_url="https://didit.prueba",
        headers={"x-api-key": "llave-prueba"},
        transport=httpx.MockTransport(manejar),
    )
    return AdaptadorIdentidadDidit("https://didit.prueba", "llave-prueba", 15, cliente=cliente)


# ------------------------------------------------------------------------------ adaptador


def test_aprobada_traduce_los_datos_del_documento_y_los_puntajes() -> None:
    peticiones: list[httpx.Request] = []
    resultado = _didit(peticiones=peticiones).verificar_identidad("onb-1", IMAGENES)

    assert resultado.estado == "aprobada" and resultado.motivo is None
    assert resultado.datos == DatosDocumento(
        nombres="Ana María",
        apellidos="Pérez Gómez",
        numero="1020304050",
        fecha_nacimiento="1995-04-12",
        pais="COL",
        tipo="Identity Card",
    )
    assert (resultado.vivo, resultado.puntaje_vida) == (True, 95)
    assert (resultado.coincide_rostro, resultado.puntaje_rostro) == (True, 99.4)
    assert [p.url.path for p in peticiones] == [
        "/v3/id-verification/",
        "/v3/passive-liveness/",
        "/v3/face-match/",
    ]
    for peticion in peticiones:
        assert peticion.headers["x-api-key"] == "llave-prueba"
        cuerpo = peticion.content
        # Didit no guarda la sesión ni enrola el rostro; solo viaja la referencia opaca.
        assert b'name="save_api_request"\r\n\r\nfalse' in cuerpo
        assert b'name="vendor_data"\r\n\r\nonb-1' in cuerpo
    # El rostro se compara contra la foto del documento.
    assert b"anverso" in peticiones[2].content and b"rostro" in peticiones[2].content


@pytest.mark.parametrize(
    ("documento", "vida", "rostro", "motivo", "alerta"),
    [
        (
            {**DOCUMENTO_OK, **_rechazo("DOCUMENT_EXPIRED")},
            VIDA_OK,
            ROSTRO_OK,
            MOTIVO_DOCUMENTO,
            "DOCUMENT_EXPIRED",
        ),
        (
            DOCUMENTO_OK,
            _rechazo("LIVENESS_FACE_ATTACK"),
            ROSTRO_OK,
            MOTIVO_VIDA,
            "LIVENESS_FACE_ATTACK",
        ),
        (
            DOCUMENTO_OK,
            VIDA_OK,
            _rechazo("LOW_FACE_MATCH_SIMILARITY"),
            MOTIVO_ROSTRO,
            "LOW_FACE_MATCH_SIMILARITY",
        ),
    ],
)
def test_cada_rechazo_tiene_su_motivo_y_solo_alertas_de_error(
    documento, vida, rostro, motivo, alerta
) -> None:
    resultado = _didit(documento, vida, rostro).verificar_identidad("onb-1", IMAGENES)

    assert resultado.estado == "rechazada"
    assert resultado.motivo == motivo
    assert resultado.alertas == (alerta,)


def test_sin_reverso_no_se_manda_back_image() -> None:
    peticiones: list[httpx.Request] = []
    _didit(peticiones=peticiones).verificar_identidad(
        "onb-1", ImagenesIdentidad(anverso=b"a", rostro=b"r")
    )
    assert b'name="back_image"' not in peticiones[0].content


# ------------------------------------------------------------------------------ caché


class AlmacenEnMemoria:
    def __init__(self) -> None:
        self.valores: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}

    def get(self, nombre):
        return self.valores.get(nombre)

    def set(self, nombre, valor, ex=None):
        self.valores[nombre] = valor
        self.ttl[nombre] = ex

    def delete(self, *nombres):
        for nombre in nombres:
            self.valores.pop(nombre, None)


APROBADA = ResultadoIdentidad(
    estado="aprobada",
    datos=DatosDocumento("Ana", "Pérez", "1020304050", "1995-04-12", "COL", "ID"),
    documento_autentico=True,
    coincide_rostro=True,
    puntaje_rostro=99.0,
    vivo=True,
    puntaje_vida=95.0,
    alertas=("X",),
)


def test_la_cache_guarda_cifrado_por_huella_y_devuelve_lo_mismo() -> None:
    almacen = AlmacenEnMemoria()
    cache = CacheIdentidad(almacen, LLAVE, ttl_segundos=123)
    huella = huella_imagenes(IMAGENES)
    cache.guardar(huella, APROBADA)

    ((clave, valor),) = almacen.valores.items()
    assert clave == f"acl:identidad:{huella}"
    assert almacen.ttl[clave] == 123
    # Ni el número ni el nombre quedan legibles en Redis.
    crudo = base64.urlsafe_b64decode(valor)
    assert b"1020304050" not in crudo and "Pérez".encode() not in crudo
    assert cache.obtener(huella) == APROBADA


def test_la_huella_cambia_con_cualquier_imagen_y_no_depende_de_datos_personales() -> None:
    otra = ImagenesIdentidad(anverso=b"anverso", rostro=b"otro-rostro", reverso=b"reverso")
    assert huella_imagenes(IMAGENES) != huella_imagenes(otra)
    assert len(huella_imagenes(IMAGENES)) == 64


def test_pendiente_no_se_guarda_y_otra_llave_o_valor_copiado_no_se_lee() -> None:
    almacen = AlmacenEnMemoria()
    cache = CacheIdentidad(almacen, LLAVE)
    cache.guardar("h1", ResultadoIdentidad(estado="pendiente", motivo="x"))
    assert almacen.valores == {}

    cache.guardar("h1", APROBADA)
    assert CacheIdentidad(almacen, bytes(32)).obtener("h1") is None
    almacen.valores["acl:identidad:h2"] = almacen.valores["acl:identidad:h1"]
    assert cache.obtener("h2") is None


# ------------------------------------------------------------------------------ servicio


class AdaptadorFalso:
    def __init__(self, resultado=APROBADA, error: Exception | None = None) -> None:
        self.resultado = resultado
        self.error = error
        self.llamadas = 0

    def verificar_identidad(self, referencia, imagenes, timeout_s=None):
        self.llamadas += 1
        if self.error is not None:
            raise self.error
        return self.resultado


def _descargar(url: str) -> bytes:
    if "falla" in url:
        raise ImagenNoDisponible("HTTP 404")
    return url.encode()


def _servicio(adaptador, cache=None, umbral=3) -> ServicioIdentidad:
    return ServicioIdentidad(
        adaptador, construir_fabrica(umbral, 60), NOMBRE_CIRCUITO_IDENTIDAD, 15, _descargar, cache
    )


def test_el_segundo_intento_con_las_mismas_imagenes_no_llama_al_proveedor() -> None:
    adaptador = AdaptadorFalso()
    servicio = _servicio(adaptador, CacheIdentidad(AlmacenEnMemoria(), LLAVE))

    assert servicio.verificar("onb-1", "u/a", "u/r", "u/b") == APROBADA
    assert servicio.verificar("onb-2", "u/a", "u/r", "u/b") == APROBADA
    assert adaptador.llamadas == 1
    servicio.verificar("onb-3", "u/a", "u/otro", "u/b")
    assert adaptador.llamadas == 2


def test_una_imagen_que_no_baja_deja_pendiente_sin_llamar_al_proveedor() -> None:
    adaptador = AdaptadorFalso()
    resultado = _servicio(adaptador).verificar("onb-1", "u/a", "u/falla")
    assert (resultado.estado, resultado.motivo) == ("pendiente", MOTIVO_IMAGEN_NO_DISPONIBLE)
    assert adaptador.llamadas == 0


def test_el_proveedor_caido_deja_pendiente_y_abre_el_circuito_sin_reintentar() -> None:
    adaptador = AdaptadorFalso(error=httpx.ConnectError("caído"))
    cache = CacheIdentidad(AlmacenEnMemoria(), LLAVE)
    servicio = _servicio(adaptador, cache, umbral=2)

    for _ in range(2):
        resultado = servicio.verificar("onb-1", "u/a", "u/r")
        assert (resultado.estado, resultado.motivo) == ("pendiente", MOTIVO_PROVEEDOR_NO_DISPONIBLE)
    assert adaptador.llamadas == 2  # una por intento: sin reintento, cada llamada se cobra
    abierto = servicio.verificar("onb-1", "u/a", "u/r")
    assert abierto.motivo == MOTIVO_CIRCUITO_ABIERTO
    assert adaptador.llamadas == 2


def test_el_descargador_solo_baja_de_los_origenes_permitidos_y_con_tope() -> None:
    def manejar(peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 50)

    cliente = httpx.Client(transport=httpx.MockTransport(manejar))
    descargador = DescargadorImagenes(["https://storage.googleapis.com/"], 5, cliente, 100)
    assert descargador.descargar("https://storage.googleapis.com/b/o?firma=1") == b"x" * 50
    with pytest.raises(ImagenNoDisponible):
        descargador.descargar("http://169.254.169.254/computeMetadata/v1/")
    with pytest.raises(ImagenNoDisponible):
        DescargadorImagenes(["https://storage.googleapis.com/"], 5, cliente, 10).descargar(
            "https://storage.googleapis.com/b/o"
        )


# ------------------------------------------------------------------------------ endpoint y config


def test_el_endpoint_nunca_responde_5xx_y_no_baja_de_origenes_ajenos() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post(
            "/identidad/verificar",
            json={
                "referencia": "onb-1",
                "anverso_url": "http://otro/a",
                "rostro_url": "http://otro/r",
            },
        )
        assert r.status_code == 200
        assert r.json()["estado"] == "pendiente"
        assert r.json()["motivo"] == MOTIVO_IMAGEN_NO_DISPONIBLE
        assert client.get("/circuitos").json()[NOMBRE_CIRCUITO_IDENTIDAD] == "closed"


def test_configuracion_de_identidad(monkeypatch: pytest.MonkeyPatch) -> None:
    for nombre in ("IDENTIDAD_URL", "DIDIT_API_KEY", "IDENTIDAD_LLAVE_CACHE"):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setenv("KYC_URL", "http://stub-kyc:8105")
    config = Configuracion.desde_entorno()
    assert config.identidad_url == "http://stub-kyc:8105"
    assert config.identidad_timeout_s == 15
    assert config.identidad_llave_cache is None

    monkeypatch.setenv("IDENTIDAD_URL", "https://verification.didit.me")
    with pytest.raises(ValueError):
        Configuracion.desde_entorno()  # proveedor real sin llave
    monkeypatch.setenv("DIDIT_API_KEY", "pendiente-de-cargar")
    with pytest.raises(ValueError):
        Configuracion.desde_entorno()
    monkeypatch.setenv("DIDIT_API_KEY", "llave-real")
    monkeypatch.setenv("IDENTIDAD_LLAVE_CACHE", base64.b64encode(LLAVE).decode())
    assert Configuracion.desde_entorno().identidad_llave_cache == LLAVE
    monkeypatch.setenv("IDENTIDAD_LLAVE_CACHE", base64.b64encode(b"corta").decode())
    with pytest.raises(ValueError):
        Configuracion.desde_entorno()


def test_nada_personal_llega_a_los_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    _servicio(AdaptadorFalso()).verificar("onb-1", "u/a", "u/r")
    _servicio(AdaptadorFalso(error=RuntimeError("1020304050"))).verificar("onb-1", "u/a", "u/r")
    texto = caplog.text + json.dumps([r.args for r in caplog.records], default=str)
    for dato in ("1020304050", "Pérez", "Ana", "1995-04-12", "onb-1"):
        assert dato not in texto


def test_en_local_reescribe_el_origen_despues_de_validarlo() -> None:
    vistas: list[str] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(f"{peticion.headers['host']} {peticion.url}")
        return httpx.Response(200, content=b"ok")

    cliente = httpx.Client(transport=httpx.MockTransport(manejar))
    descargador = DescargadorImagenes(
        ["http://localhost:4443/"],
        5,
        cliente,
        reescrituras={"http://localhost:4443/": "http://fake-gcs:4443/"},
    )
    assert descargador.descargar("http://localhost:4443/b/o?x=1") == b"ok"
    assert vistas == ["localhost:4443 http://fake-gcs:4443/b/o?x=1"]
