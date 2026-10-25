"""EC031 / HA-MOD-001 / CA-W03-10: cambiar de proveedor de firma es agregar un adaptador.

Los dos adaptadores cumplen el MISMO contrato del puerto: las mismas pruebas, parametrizadas,
corren contra cada uno. Y el caso de uso (`ServicioFirma`, con su Circuit Breaker y sus reintentos)
funciona igual con cualquiera: no sabe qué proveedor hay detrás."""

import json

import httpx
import pytest

from app.adapters.firma import AdaptadorFirma
from app.adapters.firma_alterna import AdaptadorFirmaAlterna
from app.application.circuitos import NOMBRE_CIRCUITO_FIRMA, construir_fabrica
from app.application.servicio_firma import ServicioFirma
from app.domain.puertos import SolicitudFirma

SOLICITUD = SolicitudFirma(
    id_firma="propuesta-7",
    documento_hash="b" * 64,
    version_documento="v1",
    firmante_id="usuario-1",
)
FIRMADA_EN = "2026-10-22T12:00:00+00:00"


def _estandar(vistas: list[dict], fallar: bool = False) -> AdaptadorFirma:
    def handler(request: httpx.Request) -> httpx.Response:
        if fallar:
            return httpx.Response(503)
        if request.method == "POST" and request.url.path == "/v1/firmas":
            vistas.append(json.loads(request.content))
            return httpx.Response(
                200, json={"estado": "firmada", "referencia": "fe-1", "firmada_en": FIRMADA_EN}
            )
        return httpx.Response(200, json={"ok": True})

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    return AdaptadorFirma("http://p", 0.7, cliente=cliente)


def _alterna(
    vistas: list[dict], fallar: bool = False, estado: str = "SIGNED"
) -> AdaptadorFirmaAlterna:
    def handler(request: httpx.Request) -> httpx.Response:
        if fallar:
            return httpx.Response(503)
        if request.method == "POST" and request.url.path == "/alterna/v1/signatures":
            vistas.append(json.loads(request.content))
            return httpx.Response(
                200, json={"status": estado, "signature_id": "sg-1", "signed_at": FIRMADA_EN}
            )
        return httpx.Response(200, json={"ok": True})

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    return AdaptadorFirmaAlterna("http://p", 0.7, cliente=cliente)


FABRICAS = [pytest.param(_estandar, id="estandar"), pytest.param(_alterna, id="alterna")]


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_ambos_adaptadores_devuelven_el_mismo_resultado_del_puerto(fabrica) -> None:
    resultado = fabrica([]).firmar(SOLICITUD)

    assert resultado.estado == "firmada"
    assert resultado.referencia in ("fe-1", "sg-1")
    assert resultado.firmada_en == FIRMADA_EN


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_solo_viaja_el_hash_y_nunca_el_contenido_del_documento(fabrica) -> None:
    vistas: list[dict] = []

    fabrica(vistas).firmar(SOLICITUD)

    enviado = json.dumps(vistas[0])
    assert "b" * 64 in enviado
    assert set(vistas[0].values()) == {"propuesta-7", "b" * 64, "v1", "usuario-1"}


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_la_sonda_no_firma(fabrica) -> None:
    vistas: list[dict] = []

    fabrica(vistas).sondear()

    assert vistas == []


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_el_caso_de_uso_degrada_igual_con_cualquier_proveedor(fabrica) -> None:
    servicio = ServicioFirma(
        fabrica([], fallar=True), construir_fabrica(3, 10), NOMBRE_CIRCUITO_FIRMA, 0.7
    )

    resultado = servicio.firmar(SOLICITUD)

    assert resultado.estado == "pendiente"


def test_un_estado_desconocido_del_proveedor_alterno_no_se_toma_como_firmado() -> None:
    resultado = _alterna([], estado="REVIEW").firmar(SOLICITUD)

    assert resultado.estado == "pendiente"
    assert resultado.motivo == "estado_desconocido_del_proveedor"
