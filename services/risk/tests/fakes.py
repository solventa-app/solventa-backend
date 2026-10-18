"""Dobles de prueba compartidos por las pruebas de RISK — en memoria, sin
levantar MongoDB (mismo patrón de las pruebas de `acl-worker`: fakes en vez
de mocks)."""

from typing import Any


class TimestampFalso:
    """Duck-type de `bson.Timestamp`: solo necesita `.time`/`.inc` para que
    `serializar_operation_time` funcione (ver `tiempo_causal.py`)."""

    def __init__(self, time: int, inc: int) -> None:
        self.time = time
        self.inc = inc


class RepositorioPerfilesFalso:
    """Fake de `RepositorioPerfiles`: asigna versión incremental en memoria
    por `cliente_id`, como haría `find_one_and_update($inc)` en Mongo."""

    def __init__(self) -> None:
        self.secuencias: dict[str, int] = {}
        self.guardados: list[tuple[str, list[dict[str, Any]]]] = []
        self.metadatos_guardados: list[dict[str, Any]] = []

    def guardar(
        self, cliente_id: str, fuentes: list[dict[str, Any]], **kwargs: Any
    ) -> tuple[int, TimestampFalso]:
        self.secuencias[cliente_id] = self.secuencias.get(cliente_id, 0) + 1
        perfil_version = self.secuencias[cliente_id]
        self.guardados.append((cliente_id, fuentes))
        self.metadatos_guardados.append(
            {
                "cliente_id": cliente_id,
                "perfil_version": perfil_version,
                "score_total": kwargs.get("score_total", 50),
                "nivel_riesgo": kwargs.get("nivel_riesgo", "MEDIO"),
                "fecha_vigencia": kwargs.get(
                    "fecha_vigencia", "2026-11-07T12:00:00+00:00"
                ),
                "version_reglas": kwargs.get("version_reglas", "v1"),
            }
        )
        return perfil_version, TimestampFalso(1_700_000_000 + perfil_version, 1)


class AlmacenClaveValorFalso:
    """Fake en memoria que cumple `PuertoAlmacenClaveValor` de `cache_score.py`."""

    def __init__(self) -> None:
        self.valores: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    def get(self, nombre: str) -> str | None:
        return self.valores.get(nombre)

    def set(self, nombre: str, valor: str, ex: int | None = None) -> None:
        self.valores[nombre] = valor
        self.ttls[nombre] = ex


class BusEventosFalso:
    """Fake de `PuertoBusEventos`: guarda los eventos publicados para que la
    prueba los inspeccione, en vez de loguearlos de verdad."""

    def __init__(self) -> None:
        self.eventos: list[dict[str, Any]] = []

    def publicar(self, evento: dict[str, Any]) -> None:
        self.eventos.append(evento)

