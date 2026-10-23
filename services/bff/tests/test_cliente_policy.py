"""`ClientePolicyHttp` contra respuestas HTTP simuladas (sin levantar POLICY)."""

import asyncio
import json

import httpx
import pytest

from app.clientes import ClientePolicyHttp, ErrorServicio, PropuestaRechazada


def _crear(responder) -> tuple[dict, list[dict]]:
    enviados: list[dict] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        enviados.append(json.loads(peticion.content))
        return responder()

    async def correr() -> dict:
        transporte = httpx.MockTransport(manejar)
        async with httpx.AsyncClient(base_url="http://policy", transport=transporte) as cliente:
            return await ClientePolicyHttp(cliente).crear_propuesta("o-1", "u-1")

    return asyncio.run(correr()), enviados


def test_envia_la_oferta_y_el_actor_pero_nunca_un_precio() -> None:
    propuesta, enviados = _crear(lambda: httpx.Response(200, json={"id": "p-1"}))

    assert propuesta == {"id": "p-1"}
    assert enviados == [{"oferta_id": "o-1", "actor_usuario_id": "u-1"}]


@pytest.mark.parametrize(
    ("estado", "motivo"),
    [(404, "OFERTA_NO_ENCONTRADA"), (422, "OFERTA_PRELIMINAR"), (422, "OFERTA_VENCIDA")],
)
def test_404_y_422_son_rechazos_de_negocio_con_su_motivo(estado: int, motivo: str) -> None:
    with pytest.raises(PropuestaRechazada) as error:
        _crear(lambda: httpx.Response(estado, json={"detail": motivo}))

    assert error.value.motivo == motivo


@pytest.mark.parametrize("estado", [500, 503])
def test_un_5xx_es_error_de_servicio_para_ofrecer_reintento(estado: int) -> None:
    with pytest.raises(ErrorServicio):
        _crear(lambda: httpx.Response(estado, json={"detail": "RATING_NO_DISPONIBLE"}))


def test_obtener_propuesta_devuelve_none_si_no_existe() -> None:
    async def correr() -> dict | None:
        transporte = httpx.MockTransport(lambda peticion: httpx.Response(404))
        async with httpx.AsyncClient(base_url="http://policy", transport=transporte) as cliente:
            return await ClientePolicyHttp(cliente).obtener_propuesta("p-1")

    assert asyncio.run(correr()) is None
