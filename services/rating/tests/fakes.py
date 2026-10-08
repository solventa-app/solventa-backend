"""Dobles de prueba compartidos por las pruebas de RATING — en memoria, sin
levantar MongoDB ni Redis."""

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


class AlmacenClaveValorFalso:
    """Fake en memoria que cumple el `Protocol` `PuertoAlmacenClaveValor` de
    `app/application/cache_score.py` sin Redis real."""

    def __init__(self) -> None:
        self.valores: dict[str, str] = {}

    def get(self, nombre: str) -> str | None:
        return self.valores.get(nombre)

    def set(self, nombre: str, valor: str, ex: int | None = None) -> None:
        self.valores[nombre] = valor


class ServicioOfertaFalso:
    """Doble de `ServicioOferta` para probar solo la plumbería HTTP de
    `POST /ofertas` (códigos de estado), sin ejercitar el cálculo real."""

    def __init__(
        self,
        oferta: dict[str, Any] | None = None,
        score_de_cache: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.oferta = oferta
        self.score_de_cache = score_de_cache
        self.error = error
        self.llamadas: list[tuple[str, str, str]] = []

    def generar(
        self, cliente_id: str, operation_time: str, cobertura: str
    ) -> tuple[dict[str, Any] | None, bool]:
        self.llamadas.append((cliente_id, operation_time, cobertura))
        if self.error is not None:
            raise self.error
        return self.oferta, self.score_de_cache
