"""Dobles de prueba en memoria para `enviar-push` (mismo patrón del resto de servicios: fakes en
vez de mocks, cada uno cumple el Protocol real)."""

from typing import Any

from app.clientes import PushDeshabilitado, SinDispositivoActivo


class AuthFalso:
    """Fake de `PuertoAuth`. `destinos` mapea usuario_id -> destino; un usuario ausente de
    `destinos` y no listado en `sin_destino`/`deshabilitados` levanta `SinDispositivoActivo`."""

    def __init__(
        self,
        destinos: dict[str, dict[str, Any]] | None = None,
        deshabilitados: frozenset[str] = frozenset(),
    ) -> None:
        self.destinos = destinos or {}
        self.deshabilitados = deshabilitados
        self.tokens_invalidados: list[str] = []

    async def destino_push(self, usuario_id: str) -> dict[str, Any]:
        if usuario_id in self.deshabilitados:
            raise PushDeshabilitado()
        destino = self.destinos.get(usuario_id)
        if destino is None:
            raise SinDispositivoActivo()
        return destino

    async def token_invalido(self, dispositivo_id: str) -> None:
        self.tokens_invalidados.append(dispositivo_id)


class BusEventosFalso:
    """Fake de `PuertoBusEventos`: guarda los eventos publicados para que la prueba los
    inspeccione, en vez de loguearlos de verdad."""

    def __init__(self) -> None:
        self.eventos: list[dict[str, Any]] = []

    def publicar(self, evento: dict[str, Any]) -> None:
        self.eventos.append(evento)
