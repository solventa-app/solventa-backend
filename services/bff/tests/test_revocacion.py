"""W01-A2: la revocación avisa a ACL, POLICY y RISK, y ningún aviso la hace fallar."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.clientes import ErrorServicio
from app.revocacion import avisar_revocacion


class _Destino:
    def __init__(self, falla: bool = False) -> None:
        self.falla = falla
        self.pedidos: list[tuple] = []

    async def _recibir(self, *argumentos: str) -> None:
        if self.falla:
            raise ErrorServicio("HTTP 503")
        self.pedidos.append(argumentos)

    invalidar_cache_cliente = consentimiento_revocado = _recibir


def _avisar(acl: _Destino, policy: _Destino, risk: _Destino) -> None:
    clientes = SimpleNamespace(acl=acl, risk=risk)
    asyncio.run(avisar_revocacion(clientes, policy, "titular-hmac", "c-1"))


def test_avisa_a_los_tres() -> None:
    acl, policy, risk = _Destino(), _Destino(), _Destino()

    _avisar(acl, policy, risk)

    assert acl.pedidos == [("titular-hmac",)]
    assert policy.pedidos == risk.pedidos == [("titular-hmac", "c-1")]


def test_risk_caido_no_hace_fallar_ni_frena_a_los_demas(caplog: pytest.LogCaptureFixture) -> None:
    acl, policy, risk = _Destino(), _Destino(), _Destino(falla=True)

    with caplog.at_level(logging.WARNING, logger="bff.revocacion"):
        _avisar(acl, policy, risk)

    assert acl.pedidos and policy.pedidos
    assert "RISK no recibió" in caplog.text
    assert "titular-hmac" not in caplog.text
