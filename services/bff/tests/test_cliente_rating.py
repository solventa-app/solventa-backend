"""`ClienteRatingHttp` contra respuestas HTTP simuladas (sin levantar RATING)."""

import asyncio
import json

import httpx
import pytest

from app.clientes import ClienteRatingHttp, DatosInsuficientes, ErrorServicio, SolicitudInvalida


def _generar(responder) -> tuple[dict | None, list[dict]]:
    enviados: list[dict] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        enviados.append(json.loads(peticion.content))
        return responder()

    async def correr() -> dict:
        transporte = httpx.MockTransport(manejar)
        async with httpx.AsyncClient(base_url="http://rating", transport=transporte) as cliente:
            return await ClienteRatingHttp(cliente).generar_oferta(
                "cli-1", "1700000000.1", "200000000", 240, "Calle 1 # 2-3, Cali"
            )

    return asyncio.run(correr()), enviados


def test_envia_plazo_y_direccion_a_rating() -> None:
    oferta, enviados = _generar(lambda: httpx.Response(200, json={"id": "o-1"}))

    assert oferta == {"id": "o-1"}
    assert enviados == [
        {
            "cliente_id": "cli-1",
            "operation_time": "1700000000.1",
            "cobertura": "200000000",
            "plazo_meses": 240,
            "direccion_inmueble": "Calle 1 # 2-3, Cali",
        }
    ]


def test_422_datos_insuficientes_se_traduce() -> None:
    with pytest.raises(DatosInsuficientes):
        _generar(lambda: httpx.Response(422, json={"detail": "datos_insuficientes"}))


def test_422_de_validacion_no_se_confunde_con_datos_insuficientes() -> None:
    # FastAPI responde 422 con una lista en `detail` cuando el cuerpo no cumple el modelo.
    with pytest.raises(ErrorServicio):
        _generar(lambda: httpx.Response(422, json={"detail": [{"msg": "field required"}]}))


def test_400_sigue_siendo_solicitud_invalida() -> None:
    with pytest.raises(SolicitudInvalida):
        _generar(lambda: httpx.Response(400, json={"detail": "cobertura"}))
