"""El esquema de Strawberry debe ser equivalente al contrato (regla #8: el contrato manda).

Se compara en ambos sentidos: un cambio "rompedor" en cualquiera de las dos
direcciones significa que algo sobra o falta en el código. También se revisan
los cambios "peligrosos" (valores por defecto, argumentos opcionales nuevos)."""

from pathlib import Path

from graphql import build_schema, find_breaking_changes, find_dangerous_changes

from app.esquema import schema

CONTRATO = Path(__file__).resolve().parents[3] / "contracts" / "graphql" / "schema.graphql"


def _diferencias(origen, destino) -> list[str]:
    cambios = find_breaking_changes(origen, destino) + find_dangerous_changes(origen, destino)
    return [cambio.description for cambio in cambios]


def test_el_esquema_implementado_es_equivalente_al_contrato() -> None:
    contrato = build_schema(CONTRATO.read_text(encoding="utf-8"))
    implementado = build_schema(str(schema))

    assert _diferencias(contrato, implementado) == []
    assert _diferencias(implementado, contrato) == []
