"""Dobles de prueba compartidos por las pruebas de RATING — en memoria, sin
levantar MongoDB."""

from typing import Any

from app.application.tiempo_causal import deserializar_operation_time


class LectorPerfilesFalso:
    """Fake de `LectorPerfiles` (ver `app/application/lector_perfiles.py`).
    Valida `operation_time` con la misma función que usa el adaptador real
    (para que un formato inválido se comporte igual en las pruebas)."""

    def __init__(
        self, perfil: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.perfil = perfil
        self.error = error
        self.llamadas: list[tuple[str, str]] = []

    def leer(self, cliente_id: str, operation_time: str) -> dict[str, Any] | None:
        deserializar_operation_time(operation_time)  # valida el formato, igual que el real
        self.llamadas.append((cliente_id, operation_time))
        if self.error is not None:
            raise self.error
        return self.perfil
