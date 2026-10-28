"""`ClientePolicyHttp`, firma y pago (v0.18): rutas, cabeceras de acceso y rechazos."""

import asyncio

import httpx
import pytest

from app.clientes import Acceso, ClientePolicyHttp, ErrorServicio, FlujoRechazado


def _llamar(operacion, responder):
    """Corre `operacion(cliente)` contra `responder(peticion)`; devuelve (resultado, peticiones)."""
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return responder(peticion)

    async def correr():
        transporte = httpx.MockTransport(manejar)
        async with httpx.AsyncClient(base_url="http://policy", transport=transporte) as cliente:
            return await operacion(ClientePolicyHttp(cliente))

    return asyncio.run(correr()), vistas


def test_el_token_del_enlace_viaja_en_la_cabecera_y_nunca_en_la_url() -> None:
    _, vistas = _llamar(
        lambda c: c.estado_enlace("p-1", Acceso(enlace_token="tok-secreto-largo-0001")),
        lambda _: httpx.Response(200, json={}),
    )

    assert vistas[0].url.path == "/propuestas/p-1/enlace-estado"
    assert vistas[0].headers["X-Enlace-Firma"] == "tok-secreto-largo-0001"
    assert "tok-secreto" not in str(vistas[0].url)
    assert "X-Titular-Id" not in vistas[0].headers


def test_el_titular_de_la_sesion_viaja_en_su_propia_cabecera() -> None:
    _, vistas = _llamar(
        lambda c: c.iniciar_pago("p-1", Acceso(titular_id="titular-hmac"), "http://f/r", "es"),
        lambda _: httpx.Response(200, json={}),
    )

    assert vistas[0].headers["X-Titular-Id"] == "titular-hmac"
    assert "X-Enlace-Firma" not in vistas[0].headers


def test_resolver_enlace_manda_el_token_en_el_cuerpo() -> None:
    resuelto, vistas = _llamar(
        lambda c: c.resolver_enlace("tok-secreto-largo-0001"),
        lambda _: httpx.Response(200, json={"propuesta_id": "p-1", "vigente": True}),
    )

    assert resuelto["propuesta_id"] == "p-1"
    assert b"tok-secreto-largo-0001" in vistas[0].content
    assert "tok-secreto" not in str(vistas[0].url)


def test_un_enlace_que_no_existe_es_none() -> None:
    resuelto, _ = _llamar(
        lambda c: c.resolver_enlace("tok-desconocido-0000"),
        lambda _: httpx.Response(404, json={"detail": "PROPUESTA_NO_ENCONTRADA"}),
    )

    assert resuelto is None


def test_un_rechazo_de_negocio_trae_su_motivo_y_sus_datos() -> None:
    with pytest.raises(FlujoRechazado) as error:
        _llamar(
            lambda c: c.verificar_codigo("p-1", "000000"),
            lambda _: httpx.Response(
                422, json={"detail": "CODIGO_INVALIDO", "intentos_restantes": 2}
            ),
        )

    assert error.value.motivo == "CODIGO_INVALIDO"
    assert error.value.detalle == {"intentos_restantes": 2}


@pytest.mark.parametrize("estado", [500, 502, 503])
def test_un_5xx_es_error_de_servicio_y_no_un_rechazo(estado: int) -> None:
    with pytest.raises(ErrorServicio):
        _llamar(lambda c: c.reenviar_enlace("p-1"), lambda _: httpx.Response(estado, json={}))


def test_la_lista_manda_solo_los_filtros_presentes() -> None:
    _, vistas = _llamar(
        lambda c: c.listar_propuestas(None, "u-1", "EMITIDA", 2, 10),
        lambda _: httpx.Response(200, json={}),
    )

    assert dict(vistas[0].url.params) == {
        "actor_usuario_id": "u-1",
        "estado_visible": "EMITIDA",
        "pagina": "2",
        "tamano": "10",
    }


def test_emitir_manda_el_rol_y_el_titular_en_el_cuerpo() -> None:
    _, vistas = _llamar(
        lambda c: c.emitir("p-1", "CLIENTE", "titular-hmac"),
        lambda _: httpx.Response(200, json={}),
    )

    assert vistas[0].url.path == "/propuestas/p-1/emitir"
    assert vistas[0].content == b'{"rol":"CLIENTE","titular_id":"titular-hmac"}'


def test_la_revocacion_se_avisa_con_titular_y_consentimiento() -> None:
    _, vistas = _llamar(
        lambda c: c.consentimiento_revocado("titular-hmac", "c-1"),
        lambda _: httpx.Response(204),
    )

    assert vistas[0].url.path == "/propuestas/consentimiento-revocado"
    assert b'"consentimiento_id":"c-1"' in vistas[0].content
