"""Fixtures compartidas de las pruebas del ACL Worker."""

import socket
import threading
import time
from collections.abc import Iterator

import pytest


def _iniciar_servidor_lento(demora_s: float | None) -> tuple[str, Iterator[None]]:
    """Servidor TCP mínimo (sin FastAPI/uvicorn) para probar el timeout duro
    HTTP de verdad: si `demora_s` es None, nunca responde (simula un proveedor
    caído); si tiene un valor, responde 200 después de esperar esos segundos."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    sock.settimeout(0.2)
    puerto = sock.getsockname()[1]
    detener = threading.Event()

    def _manejar(conexion: socket.socket) -> None:
        try:
            conexion.recv(4096)
            if demora_s is not None:
                time.sleep(demora_s)
                cuerpo = b"{}"
                respuesta = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(cuerpo)).encode() + b"\r\n\r\n" + cuerpo
                )
                conexion.sendall(respuesta)
        finally:
            if demora_s is not None:
                conexion.close()
            # si demora_s es None, deliberadamente no se cierra: el socket queda
            # colgado hasta que el cliente cancele por su propio timeout.

    def _atender() -> None:
        while not detener.is_set():
            try:
                conexion, _ = sock.accept()
            except TimeoutError:
                continue
            threading.Thread(target=_manejar, args=(conexion,), daemon=True).start()

    hilo = threading.Thread(target=_atender, daemon=True)
    hilo.start()

    def _detener() -> None:
        detener.set()
        sock.close()

    return f"http://127.0.0.1:{puerto}", _detener


@pytest.fixture
def servidor_caido() -> Iterator[str]:
    """Nunca responde: toda petición debe terminar en httpx.TimeoutException."""
    url, detener = _iniciar_servidor_lento(demora_s=None)
    try:
        yield url
    finally:
        detener()


@pytest.fixture
def servidor_lento() -> Iterator[str]:
    """Responde 200, pero después de 300 ms: suficiente para violar un
    presupuesto de 100 ms sin colgar la prueba más de lo necesario."""
    url, detener = _iniciar_servidor_lento(demora_s=0.3)
    try:
        yield url
    finally:
        detener()
