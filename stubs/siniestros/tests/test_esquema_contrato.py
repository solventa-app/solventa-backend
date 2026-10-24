"""El esquema de Strawberry del dummy debe ser equivalente a su contrato SDL (el contrato manda).

Mismo criterio que `services/bff/tests/test_esquema_contrato.py`: se compara en ambos sentidos,
incluidos los cambios "peligrosos" (valores por defecto, argumentos opcionales nuevos)."""

from pathlib import Path

from graphql import build_schema, find_breaking_changes, find_dangerous_changes

from app.esquema import schema

CONTRATO = Path(__file__).resolve().parents[3] / "contracts" / "graphql" / "siniestros.graphql"


def _diferencias(origen, destino) -> list[str]:
    cambios = find_breaking_changes(origen, destino) + find_dangerous_changes(origen, destino)
    return [cambio.description for cambio in cambios]


def test_el_esquema_implementado_es_equivalente_al_contrato() -> None:
    contrato = build_schema(CONTRATO.read_text(encoding="utf-8"))
    implementado = build_schema(str(schema))

    assert _diferencias(contrato, implementado) == []
    assert _diferencias(implementado, contrato) == []
