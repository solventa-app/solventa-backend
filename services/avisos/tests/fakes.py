"""Redis en memoria: lo justo para tickets (`getdel`) y publicación/suscripción."""

import asyncio
import json


class PubSubFalso:
    def __init__(self, redis: "RedisFalso") -> None:
        self._redis = redis
        self.canales: list[str] = []
        self.cola: asyncio.Queue = asyncio.Queue()
        self.cerrado = False
        self.loop: asyncio.AbstractEventLoop | None = None

    async def subscribe(self, *canales: str) -> None:
        if self._redis.falla_al_suscribir:
            raise ConnectionError("redis caído")
        self.loop = asyncio.get_running_loop()
        self.canales.extend(canales)
        self._redis.suscriptores.append(self)

    async def get_message(self, ignore_subscribe_messages: bool = False, timeout: float = 0.0):
        if self._redis.corta_stream:
            raise ConnectionError("redis caído")
        try:
            return await asyncio.wait_for(self.cola.get(), timeout)
        except TimeoutError:
            return None

    async def aclose(self) -> None:
        self.cerrado = True
        if self in self._redis.suscriptores:
            self._redis.suscriptores.remove(self)


class RedisFalso:
    def __init__(self) -> None:
        self.claves: dict[str, str] = {}
        self.suscriptores: list[PubSubFalso] = []
        self.todos: list[PubSubFalso] = []
        self.falla = False
        self.falla_al_suscribir = False
        self.corta_stream = False

    def poner_ticket(self, ticket: str, **datos) -> None:
        self.claves[f"avisos:ticket:{ticket}"] = json.dumps(datos)

    async def getdel(self, clave: str):
        if self.falla:
            raise ConnectionError("redis caído")
        return self.claves.pop(clave, None)

    def pubsub(self) -> PubSubFalso:
        nuevo = PubSubFalso(self)
        self.todos.append(nuevo)
        return nuevo

    def publicar(self, canal: str, mensaje: dict) -> int:
        """Se llama desde el hilo de la prueba; el servidor corre en otro hilo y otro bucle."""
        alcanzados = 0
        for suscriptor in list(self.suscriptores):
            if canal in suscriptor.canales and suscriptor.loop is not None:
                entrega = {"channel": canal.encode(), "data": json.dumps(mensaje).encode()}
                suscriptor.loop.call_soon_threadsafe(suscriptor.cola.put_nowait, entrega)
                alcanzados += 1
        return alcanzados
