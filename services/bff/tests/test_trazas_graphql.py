"""La extensión de trazas de GraphQL: un tramo por operación y sin el texto de la consulta."""

import json

import strawberry
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.trazas_graphql import TrazaGraphQL


@strawberry.type
class Query:
    @strawberry.field
    def saludo(self, nombre: str) -> str:
        return f"hola {nombre}"


def _schema_con_memoria() -> tuple[strawberry.Schema, InMemorySpanExporter]:
    memoria = InMemorySpanExporter()
    proveedor = TracerProvider()
    proveedor.add_span_processor(SimpleSpanProcessor(memoria))
    tracer = proveedor.get_tracer("prueba")
    schema = strawberry.Schema(Query, extensions=[lambda: TrazaGraphQL(tracer=tracer)])
    return schema, memoria


def test_un_tramo_por_operacion_con_el_tipo_y_los_campos():
    schema, memoria = _schema_con_memoria()
    resultado = schema.execute_sync('query Saludar { saludo(nombre: "mundo") }')
    assert resultado.errors is None
    [tramo] = memoria.get_finished_spans()
    assert tramo.name == "GraphQL query saludo"
    assert tramo.attributes["graphql.operation.type"] == "query"
    assert tramo.attributes["graphql.fields"] == "saludo"


def test_no_guarda_el_texto_de_la_consulta_ni_sus_valores():
    schema, memoria = _schema_con_memoria()
    schema.execute_sync('{ saludo(nombre: "documento 1234567890") }')
    [tramo] = memoria.get_finished_spans()
    assert "1234567890" not in json.dumps(dict(tramo.attributes))
    assert "1234567890" not in tramo.name
