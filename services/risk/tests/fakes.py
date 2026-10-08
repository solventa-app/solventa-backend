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

    def guardar(
        self, cliente_id: str, fuentes: list[dict[str, Any]]
    ) -> tuple[int, TimestampFalso]:
        self.secuencias[cliente_id] = self.secuencias.get(cliente_id, 0) + 1
        perfil_version = self.secuencias[cliente_id]
        self.guardados.append((cliente_id, fuentes))
        return perfil_version, TimestampFalso(1_700_000_000 + perfil_version, 1)


class BusEventosFalso:
    """Fake de `PuertoBusEventos`: guarda los eventos publicados para que la
    prueba los inspeccione, en vez de loguearlos de verdad."""

    def __init__(self) -> None:
        self.eventos: list[dict[str, Any]] = []

    def publicar(self, evento: dict[str, Any]) -> None:
        self.eventos.append(evento)
