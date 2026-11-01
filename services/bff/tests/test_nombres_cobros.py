"""W05-01: el nombre del cliente sale de AUTH o, sin cuenta, de la propuesta en POLICY."""

import asyncio
from types import SimpleNamespace

from app.clientes import ErrorServicio
from app.esquema import _cobro, _nombres_de_clientes

COBRO = {
    "id": "c-1",
    "poliza_id": "propuesta-1",
    "cliente_id": "titular-hmac",
    "estado": "cobrado",
    "monto": "100",
    "moneda": "COP",
    "actualizado_en": "2026-10-31T12:00:00+00:00",
}


class _Auth:
    def __init__(self, nombres: dict[str, str]) -> None:
        self.nombres = nombres

    async def nombres_de_clientes(self, ids: list[str]) -> dict[str, str]:
        return {i: self.nombres[i] for i in ids if i in self.nombres}


class _Policy:
    def __init__(self, nombres: dict[str, str] | None = None, falla: bool = False) -> None:
        self.nombres = nombres or {}
        self.falla = falla
        self.pedidos: list[list[str]] = []

    async def nombres_titulares(self, ids: list[str]) -> dict[str, str]:
        self.pedidos.append(ids)
        if self.falla:
            raise ErrorServicio("HTTP 503")
        return {i: self.nombres[i] for i in ids if i in self.nombres}


def _nombre(auth: _Auth, policy: _Policy) -> str | None:
    clientes = SimpleNamespace(auth=auth, policy=policy)
    info = SimpleNamespace(context={"clientes": clientes, "usuario": None})
    nombres = asyncio.run(_nombres_de_clientes(info, [COBRO]))
    return _cobro(COBRO, nombres).cliente.nombre


def test_con_cuenta_el_nombre_es_de_auth_y_no_se_consulta_policy() -> None:
    policy = _Policy({"propuesta-1": "Otro"})

    assert _nombre(_Auth({"titular-hmac": "Ana"}), policy) == "Ana"
    assert policy.pedidos == []


def test_sin_cuenta_el_nombre_es_el_de_la_propuesta() -> None:
    assert _nombre(_Auth({}), _Policy({"propuesta-1": "Carlos"})) == "Carlos"


def test_con_el_contacto_purgado_sale_sin_nombre() -> None:
    assert _nombre(_Auth({}), _Policy()) is None


def test_policy_caido_no_impide_ver_el_cobro() -> None:
    assert _nombre(_Auth({}), _Policy(falla=True)) is None
