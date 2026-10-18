"""Extensión de Strawberry: un tramo por operación GraphQL (tipo y campos de primer nivel).

Sin trazas activas es un no-op (la API de OpenTelemetry no hace nada sin proveedor). NO guarda el
texto de la consulta ni las variables: podrían traer un documento de identidad o una clave
(regla 6). Del cliente solo salen nombres del esquema, que no son datos personales."""

from collections.abc import Generator

from opentelemetry import trace
from opentelemetry.trace import Tracer
from strawberry.extensions import SchemaExtension
from strawberry.types.execution import ExecutionContext


class TrazaGraphQL(SchemaExtension):
    def __init__(
        self, *, execution_context: ExecutionContext | None = None, tracer: Tracer | None = None
    ) -> None:
        if execution_context is not None:
            self.execution_context = execution_context
        self._tracer = tracer or trace.get_tracer("bff.graphql")

    def on_operation(self) -> Generator[None, None, None]:
        with self._tracer.start_as_current_span("GraphQL") as tramo:
            yield
            try:
                tipo, campos = self._describir()
                tramo.update_name(f"GraphQL {tipo} {campos}".strip())
                tramo.set_attribute("graphql.operation.type", tipo)
                tramo.set_attribute("graphql.fields", campos)
                nombre = self.execution_context.operation_name
                if nombre:
                    tramo.set_attribute("graphql.operation.name", nombre)
            except Exception:  # describir la operación nunca debe romper la respuesta
                tramo.set_attribute("graphql.operation.type", "desconocida")

    def _describir(self) -> tuple[str, str]:
        documento = self.execution_context.graphql_document
        operaciones = [d for d in documento.definitions if hasattr(d, "operation")]
        nombre = self.execution_context.operation_name
        elegida = next(
            (o for o in operaciones if nombre and o.name and o.name.value == nombre),
            operaciones[0],
        )
        campos = ",".join(
            s.name.value for s in elegida.selection_set.selections if hasattr(s, "name")
        )
        return elegida.operation.value, campos
