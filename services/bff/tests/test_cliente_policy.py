"""`ClientePolicyHttp` contra respuestas HTTP simuladas (sin levantar POLICY)."""

import asyncio
import json

import httpx
import pytest

from app.clientes import ClientePolicyHttp, ErrorServicio, FirmaRechazada, PropuestaRechazada


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


def _firmar(responder) -> dict:
    async def correr() -> dict:
        transporte = httpx.MockTransport(lambda peticion: responder(peticion))
        async with httpx.AsyncClient(base_url="http://policy", transport=transporte) as cliente:
            return await ClientePolicyHttp(cliente).firmar_propuesta("p-1", True, "v1", "u-1")

    return asyncio.run(correr())


def test_firmar_manda_el_firmante_de_la_sesion_y_la_autorizacion() -> None:
    vistas: list[httpx.Request] = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(200, json={"referencia": "fe-1"})

    assert _firmar(responder) == {"referencia": "fe-1"}
    assert vistas[0].url.path == "/propuestas/p-1/firma"
    assert json.loads(vistas[0].content) == {
        "autoriza_cobro": True,
        "version_documento": "v1",
        "firmante_id": "u-1",
    }


@pytest.mark.parametrize(
    ("estado", "detalle", "motivo"),
    [
        (404, "propuesta no encontrada", "PROPUESTA_NO_FIRMABLE"),
        (409, "VERSION_DOCUMENTO_DESACTUALIZADA", "VERSION_DOCUMENTO_DESACTUALIZADA"),
        (422, "COBRO_NO_AUTORIZADO", "COBRO_NO_AUTORIZADO"),
    ],
)
def test_firmar_traduce_los_rechazos_de_negocio(estado: int, detalle: str, motivo: str) -> None:
    with pytest.raises(FirmaRechazada) as error:
        _firmar(lambda peticion: httpx.Response(estado, json={"detail": detalle}))

    assert error.value.motivo == motivo


def test_firmar_con_el_proveedor_caido_es_error_de_servicio() -> None:
    with pytest.raises(ErrorServicio):
        _firmar(
            lambda peticion: httpx.Response(503, json={"detail": "SERVICIO_FIRMA_NO_DISPONIBLE"})
        )


def test_documento_firma_409_o_404_es_none() -> None:
    for estado in (404, 409):

        async def correr(estado=estado) -> dict | None:
            transporte = httpx.MockTransport(lambda peticion: httpx.Response(estado))
            async with httpx.AsyncClient(base_url="http://policy", transport=transporte) as cliente:
                return await ClientePolicyHttp(cliente).documento_firma("p-1")

        assert asyncio.run(correr()) is None


def test_obtener_poliza_devuelve_none_si_no_existe_y_los_datos_si_existe() -> None:
    async def correr(respuesta: httpx.Response) -> dict | None:
        transporte = httpx.MockTransport(lambda peticion: respuesta)
        async with httpx.AsyncClient(base_url="http://policy", transport=transporte) as cliente:
            return await ClientePolicyHttp(cliente).obtener_poliza("pz-1")

    assert asyncio.run(correr(httpx.Response(404))) is None
    assert asyncio.run(correr(httpx.Response(200, json={"id": "pz-1"}))) == {"id": "pz-1"}
    with pytest.raises(ErrorServicio):
        asyncio.run(correr(httpx.Response(503)))


def test_generar_documento_manda_el_nombre_en_el_cuerpo_y_no_en_la_url() -> None:
    vistas: list[httpx.Request] = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(200, content=b"%PDF-1.3")

    async def correr() -> bytes | None:
        transporte = httpx.MockTransport(responder)
        async with httpx.AsyncClient(base_url="http://policy", transport=transporte) as cliente:
            return await ClientePolicyHttp(cliente).generar_documento("pz-1", "Ana Maria Perez")

    assert asyncio.run(correr()) == b"%PDF-1.3"
    assert vistas[0].method == "POST"
    assert vistas[0].url.path == "/polizas/pz-1/documento"
    assert "Ana" not in str(vistas[0].url)
    assert json.loads(vistas[0].content) == {"asegurado": "Ana Maria Perez"}


def test_generar_documento_devuelve_none_si_no_existe_y_falla_si_policy_falla() -> None:
    async def correr(respuesta: httpx.Response) -> bytes | None:
        transporte = httpx.MockTransport(lambda peticion: respuesta)
        async with httpx.AsyncClient(base_url="http://policy", transport=transporte) as cliente:
            return await ClientePolicyHttp(cliente).generar_documento("pz-1", None)

    assert asyncio.run(correr(httpx.Response(404))) is None
    with pytest.raises(ErrorServicio):
        asyncio.run(correr(httpx.Response(500)))
    with pytest.raises(ErrorServicio):
        asyncio.run(correr(httpx.Response(422)))
