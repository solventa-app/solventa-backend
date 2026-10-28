"""EC031 para la página de pago alojada (HU-W03, W03-28): crear y consultar la sesión cumple el
mismo contrato del puerto con los dos proveedores, y `ServicioCobro` degrada igual con cualquiera
(nunca un 5xx; un rechazo de la solicitud no abre el circuito)."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.pasarela import AdaptadorPasarela
from app.adapters.pasarela_alterna import AdaptadorPasarelaAlterna
from app.application.circuitos import NOMBRE_CIRCUITO_PASARELA, construir_fabrica
from app.application.servicio_pagos import ServicioCobro
from app.domain.puertos import SolicitudSesionPago

SOLICITUD = SolicitudSesionPago(
    id_intento_cobro="int-1",
    monto="185000",
    moneda="COP",
    descripcion="Seguro de desempleo",
    url_retorno="http://localhost:5173/pago/retorno",
)
MEDIO = {"token": "tok_abc", "marca": "VISA", "tipo": "CREDITO"}


class Pasarela:
    """Memoria de sesiones compartida por las dos APIs simuladas."""

    def __init__(self, fallar: bool = False, rechazar_retorno: bool = False) -> None:
        self.sesiones: dict[str, dict] = {}
        self.fallar = fallar
        self.rechazar_retorno = rechazar_retorno
        self.cuerpos: list[dict] = []

    def crear(self, referencia: str) -> dict:
        for sesion in self.sesiones.values():
            if sesion["referencia"] == referencia:
                return sesion
        sesion = {
            "id": f"ses_{len(self.sesiones) + 1}",
            "referencia": referencia,
            "estado": "ABIERTA",
        }
        self.sesiones[sesion["id"]] = sesion
        return sesion

    def cobrar(self, sesion_id: str) -> None:
        self.sesiones[sesion_id]["estado"] = "COBRADA"


def _estandar(p: Pasarela) -> AdaptadorPasarela:
    def a_json(s: dict) -> dict:
        cobrada = s["estado"] == "COBRADA"
        return {
            "sesion_id": s["id"],
            "estado": s["estado"],
            "url_pago": f"http://pasarela/checkout/{s['id']}",
            "vence_en": "2026-10-27T15:10:00+00:00",
            "motivo": None,
            "medio": MEDIO if cobrada else None,
            "referencia": "pg-1" if cobrada else None,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if p.fallar:
            return httpx.Response(503)
        if request.method == "POST" and request.url.path == "/v1/sesiones-pago":
            cuerpo = json.loads(request.content)
            p.cuerpos.append(cuerpo)
            if p.rechazar_retorno:
                return httpx.Response(422, json={"detail": "url_retorno_no_permitida"})
            return httpx.Response(201, json=a_json(p.crear(cuerpo["referencia_comercio"])))
        sesion = p.sesiones.get(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=a_json(sesion)) if sesion else httpx.Response(404)

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    return AdaptadorPasarela("http://p", 0.7, cliente=cliente)


def _alterna(p: Pasarela) -> AdaptadorPasarelaAlterna:
    estados = {"ABIERTA": "OPEN", "COBRADA": "SUCCEEDED"}

    def a_json(s: dict) -> dict:
        cobrada = s["estado"] == "COBRADA"
        return {
            "id": s["id"],
            "reference": s["referencia"],
            "status": estados[s["estado"]],
            "checkout_url": f"http://pasarela/alterna/{s['id']}",
            "expires_at": "2026-10-27T15:10:00+00:00",
            "decline_code": None,
            "payment_method": (
                {"token": "tok_abc", "brand": "VISA", "funding": "credit"} if cobrada else None
            ),
            "transaction_id": "pg-1" if cobrada else None,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if p.fallar:
            return httpx.Response(503)
        if request.method == "POST" and request.url.path == "/alterna/v2/checkout-sessions":
            cuerpo = json.loads(request.content)
            p.cuerpos.append(cuerpo)
            if p.rechazar_retorno:
                return httpx.Response(422, json={"detail": "url_retorno_no_permitida"})
            return httpx.Response(201, json=a_json(p.crear(cuerpo["reference"])))
        sesion = p.sesiones.get(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=a_json(sesion)) if sesion else httpx.Response(404)

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    return AdaptadorPasarelaAlterna("http://p", 0.7, cliente=cliente)


FABRICAS = [pytest.param(_estandar, id="estandar"), pytest.param(_alterna, id="alterna")]


def _servicio(adaptador, umbral: int = 3) -> ServicioCobro:
    return ServicioCobro(
        adaptador, construir_fabrica(umbral, 5), NOMBRE_CIRCUITO_PASARELA, presupuesto_s=0.7
    )


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_crear_y_consultar_la_sesion(fabrica) -> None:
    pasarela = Pasarela()
    adaptador = fabrica(pasarela)

    sesion = adaptador.crear_sesion_pago(SOLICITUD)
    assert sesion.estado == "abierta" and sesion.url_pago and sesion.medio is None
    assert adaptador.crear_sesion_pago(SOLICITUD).sesion_id == sesion.sesion_id  # idempotente

    pasarela.cobrar(sesion.sesion_id)
    cobrada = adaptador.consultar_sesion_pago(sesion.sesion_id)
    assert cobrada.estado == "cobrada"
    assert (cobrada.medio.token, cobrada.medio.marca, cobrada.medio.tipo) == (
        "tok_abc",
        "VISA",
        "CREDITO",
    )
    assert cobrada.referencia == "pg-1"
    assert adaptador.consultar_sesion_pago("ses_nada") is None  # no es una falla


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_nunca_envia_datos_de_tarjeta(fabrica) -> None:
    pasarela = Pasarela()
    fabrica(pasarela).crear_sesion_pago(SOLICITUD)
    enviado = json.dumps(pasarela.cuerpos[0])
    for campo in ("numero", "card", "cvv", "pan"):
        assert campo not in enviado


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_el_caso_de_uso_degrada_sin_5xx(fabrica) -> None:
    pasarela = Pasarela()
    servicio = _servicio(fabrica(pasarela))
    creada = servicio.crear_sesion(SOLICITUD)
    assert creada.resultado == "creada"
    assert servicio.consultar_sesion(creada.sesion.sesion_id).resultado == "encontrado"
    assert servicio.consultar_sesion("ses_nada").resultado == "no_encontrado"

    caida = _servicio(fabrica(Pasarela(fallar=True)))
    resultado = caida.crear_sesion(SOLICITUD)
    assert (resultado.resultado, resultado.motivo) == ("no_disponible", "pasarela_no_disponible")
    assert caida.consultar_sesion("ses_1").resultado == "desconocido"


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_con_el_circuito_abierto_ni_se_intenta(fabrica) -> None:
    pasarela = Pasarela(fallar=True)
    servicio = _servicio(fabrica(pasarela), umbral=1)
    servicio.crear_sesion(SOLICITUD)  # abre el circuito
    pasarela.fallar = False

    resultado = servicio.crear_sesion(SOLICITUD)

    assert (resultado.resultado, resultado.motivo) == ("no_disponible", "circuito_abierto")


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_un_retorno_no_permitido_es_un_rechazo_y_no_abre_el_circuito(fabrica) -> None:
    pasarela = Pasarela(rechazar_retorno=True)
    servicio = _servicio(fabrica(pasarela), umbral=1)

    for _ in range(3):
        resultado = servicio.crear_sesion(SOLICITUD)
        assert resultado.resultado == "creada"
        assert (resultado.sesion.estado, resultado.sesion.motivo) == (
            "rechazada",
            "url_retorno_no_permitida",
        )

    pasarela.rechazar_retorno = False
    assert servicio.crear_sesion(SOLICITUD).sesion.estado == "abierta"


def test_alterna_no_da_por_cobrada_una_sesion_con_estado_desconocido() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "ses_1", "status": "QUIEN_SABE"})

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    sesion = AdaptadorPasarelaAlterna("http://p", 0.7, cliente=cliente).consultar_sesion_pago(
        "ses_1"
    )
    assert (sesion.estado, sesion.motivo) == ("procesando", "estado_desconocido_del_proveedor")
    assert sesion.medio is None


def test_endpoints_del_acl_siempre_responden_200_con_la_pasarela_caida() -> None:
    from test_main import CONFIG_SIN_PROVEEDORES

    from app.main import create_app

    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        creada = client.post(
            "/pagos/sesiones",
            json={
                "id_intento_cobro": "int-1",
                "monto": "185000",
                "moneda": "COP",
                "descripcion": "Seguro",
                "url_retorno": "http://localhost:5173/pago/retorno",
            },
        )
        consulta = client.get("/pagos/sesiones/ses_1")

    assert creada.status_code == 200
    assert creada.json() == {
        "resultado": "no_disponible",
        "sesion": None,
        "motivo": "pasarela_no_disponible",
    }
    assert consulta.status_code == 200
    assert consulta.json() == {"resultado": "desconocido", "sesion": None}
