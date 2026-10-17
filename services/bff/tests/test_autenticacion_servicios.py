"""Token de identidad hacia servicios privados de Cloud Run (sin metadatos reales)."""

import asyncio

import httpx
import pytest

from app.autenticacion_servicios import (
    VIGENCIA_SEGUNDOS,
    AutenticacionIdToken,
    autenticacion_para,
)


def test_en_local_no_agrega_autenticacion() -> None:
    assert autenticacion_para("ninguna", "risk") is None


def test_modo_desconocido_falla_al_arrancar() -> None:
    with pytest.raises(ValueError):
        autenticacion_para("otro", "risk")


def _probar(auth: AutenticacionIdToken, peticiones: int) -> list[str]:
    cabeceras: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        cabeceras.append(request.headers["Authorization"])
        return httpx.Response(200)

    async def correr() -> None:
        transporte = httpx.MockTransport(responder)
        async with httpx.AsyncClient(transport=transporte, auth=auth) as cliente:
            for _ in range(peticiones):
                await cliente.get("http://risk/health")

    asyncio.run(correr())
    return cabeceras


def test_agrega_el_token_con_el_audience_del_servicio_y_lo_cachea() -> None:
    pedidos: list[str] = []

    async def pedir(audiencia: str) -> str:
        pedidos.append(audiencia)
        return "token-1"

    cabeceras = _probar(AutenticacionIdToken("risk", pedir_token=pedir), peticiones=3)

    assert cabeceras == ["Bearer token-1"] * 3
    assert pedidos == ["risk"]


def test_renueva_el_token_al_vencer() -> None:
    ahora = [0.0]
    emitidos = iter(["token-1", "token-2"])

    async def pedir(audiencia: str) -> str:
        return next(emitidos)

    auth = AutenticacionIdToken("risk", pedir_token=pedir, reloj=lambda: ahora[0])
    primera = _probar(auth, peticiones=1)
    ahora[0] = VIGENCIA_SEGUNDOS + 1
    segunda = _probar(auth, peticiones=1)

    assert primera == ["Bearer token-1"]
    assert segunda == ["Bearer token-2"]
