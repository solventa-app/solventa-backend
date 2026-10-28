"""El stream SSE contra un servidor uvicorn real (en un hilo) y un Redis en memoria. Un stream no
termina, así que `httpx.ASGITransport` (que espera la respuesta completa) no sirve aquí."""

import json
import threading
import time

import httpx
import pytest
import uvicorn
from fakes import RedisFalso

from app.configuracion import Configuracion
from app.main import create_app

ORIGEN = "http://localhost:5173"


@pytest.fixture
def servidor():
    arrancados: list[tuple[uvicorn.Server, threading.Thread]] = []

    def montar(redis: RedisFalso, latido: float = 0.05) -> str:
        config = Configuracion(
            redis_url="redis://x", origenes_permitidos=frozenset({ORIGEN}), latido_segundos=latido
        )
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(config, cliente_redis=redis),
                port=0,
                log_level="warning",
                timeout_graceful_shutdown=1,
            )
        )
        hilo = threading.Thread(target=server.run, daemon=True)
        hilo.start()
        while not server.started:
            time.sleep(0.01)
        arrancados.append((server, hilo))
        return f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"

    yield montar
    for server, hilo in arrancados:
        server.should_exit = True
        hilo.join(5)


def _cliente(url: str) -> httpx.Client:
    # Si el stream no manda nada en 3 s la prueba falla con ReadTimeout en vez de colgarse.
    return httpx.Client(base_url=url, timeout=3.0)


def _abrir(cliente: httpx.Client, ticket: str, origen: str | None = ORIGEN):
    cabeceras = {"Origin": origen} if origen else {}
    return cliente.stream("GET", "/avisos/stream", params={"ticket": ticket}, headers=cabeceras)


class Lector:
    """Lee un stream por trozos con un buffer: `iter_text()` solo se puede recorrer una vez."""

    def __init__(self, respuesta: httpx.Response) -> None:
        self._trozos = respuesta.iter_text()
        self.texto = ""

    def hasta(self, contiene: str) -> str:
        """Lee hasta que lo recibido contenga `contiene`; devuelve todo lo recibido hasta ahora."""
        while contiene not in self.texto:
            self.texto += next(self._trozos)
        return self.texto

    def resto(self) -> str:
        self.texto += "".join(self._trozos)
        return self.texto


def test_un_origen_ajeno_o_ausente_es_403_y_no_gasta_el_ticket(servidor) -> None:
    redis = RedisFalso()
    redis.poner_ticket("t1", rol="ASESOR", usuarioId="u-1")
    with _cliente(servidor(redis)) as cliente:
        with _abrir(cliente, "t1", "https://malo.example") as r:
            assert r.status_code == 403
        with _abrir(cliente, "t1", None) as r:
            assert r.status_code == 403
    assert "avisos:ticket:t1" in redis.claves


@pytest.mark.parametrize("ticket", ["", "no-existe", "x" * 500])
def test_un_ticket_que_no_existe_es_401(servidor, ticket: str) -> None:
    with _cliente(servidor(RedisFalso())) as cliente, _abrir(cliente, ticket) as r:
        assert r.status_code == 401


def test_el_ticket_sirve_una_sola_vez(servidor) -> None:
    redis = RedisFalso()
    redis.poner_ticket("t1", rol="ASESOR", usuarioId="u-1")
    with _cliente(servidor(redis)) as cliente:
        with _abrir(cliente, "t1") as primera:
            assert primera.status_code == 200
            with _abrir(cliente, "t1") as segunda:
                assert segunda.status_code == 401


def test_redis_caido_al_consumir_el_ticket_es_503_con_reintento(servidor) -> None:
    redis = RedisFalso()
    redis.falla = True
    with _cliente(servidor(redis)) as cliente, _abrir(cliente, "t1") as r:
        assert r.status_code == 503
        assert r.headers["retry-after"] == "5"


def test_redis_caido_al_suscribir_es_503(servidor) -> None:
    redis = RedisFalso()
    redis.poner_ticket("t1", rol="ASESOR", usuarioId="u-1")
    redis.falla_al_suscribir = True
    with _cliente(servidor(redis)) as cliente, _abrir(cliente, "t1") as r:
        assert r.status_code == 503


def test_el_asesor_recibe_solo_lo_suyo_como_evento_sse(servidor) -> None:
    redis = RedisFalso()
    redis.poner_ticket("t1", rol="ASESOR", usuarioId="u-1")
    with _cliente(servidor(redis, latido=5)) as cliente, _abrir(cliente, "t1") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["access-control-allow-origin"] == ORIGEN
        assert r.headers["cache-control"] == "no-cache, no-transform"
        lector = Lector(r)
        lector.hasta(": conectado")
        assert redis.publicar("avisos:asesor:otro", {"tipo": "propuesta"}) == 0
        mensaje = {"tipo": "propuesta", "propuestaId": "p-1", "estado": "EMITIDA"}
        assert redis.publicar("avisos:asesor:u-1", mensaje) == 1
        texto = lector.hasta("propuestaId")
    assert f"event: propuesta\ndata: {json.dumps(mensaje)}\n\n" in texto
    assert "otro" not in texto


def test_el_cliente_recibe_los_de_su_titular_y_el_backoffice_todos(servidor) -> None:
    redis = RedisFalso()
    redis.poner_ticket("tc", rol="CLIENTE", usuarioId="c-1", titularId="hmac")
    redis.poner_ticket("tb", rol="BACKOFFICE", usuarioId="b-1")
    with (
        _cliente(servidor(redis, latido=5)) as cliente,
        _abrir(cliente, "tc") as rc,
        _abrir(cliente, "tb") as rb,
    ):
        lc, lb = Lector(rc), Lector(rb)
        lc.hasta(": conectado")
        lb.hasta(": conectado")
        redis.publicar("avisos:titular:hmac", {"tipo": "propuesta", "propuestaId": "p-1"})
        redis.publicar("avisos:todos", {"tipo": "propuesta", "propuestaId": "p-2"})
        redis.publicar("avisos:cobros", {"cobroId": "k-1", "estado": "COBRADO"})
        del_cliente = lc.hasta("p-1")
        del_backoffice = lb.hasta("k-1")
    assert "p-2" not in del_cliente and "k-1" not in del_cliente
    assert "p-2" in del_backoffice and "p-1" not in del_backoffice  # solo `todos` y cobros
    assert "event: cobro" in del_backoffice  # sin `tipo`, el canal de cobros lo da


def test_un_cliente_sin_titular_no_se_suscribe_a_nada_pero_recibe_latidos(servidor) -> None:
    redis = RedisFalso()
    redis.poner_ticket("t1", rol="CLIENTE", usuarioId="c-1", titularId=None)
    with _cliente(servidor(redis, latido=0.02)) as cliente, _abrir(cliente, "t1") as r:
        texto = Lector(r).hasta(": latido")
    assert ": latido" in texto
    assert all(not s.canales for s in redis.todos)


def test_sin_mensajes_el_stream_manda_latidos(servidor) -> None:
    redis = RedisFalso()
    redis.poner_ticket("t1", rol="ASESOR", usuarioId="u-1")
    with _cliente(servidor(redis, latido=0.02)) as cliente, _abrir(cliente, "t1") as r:
        texto = Lector(r).hasta(": latido")
    assert texto.startswith("retry: 3000")


def test_si_redis_se_cae_a_mitad_el_stream_termina_y_libera_la_suscripcion(servidor) -> None:
    redis = RedisFalso()
    redis.poner_ticket("t1", rol="ASESOR", usuarioId="u-1")
    with _cliente(servidor(redis, latido=0.05)) as cliente, _abrir(cliente, "t1") as r:
        lector = Lector(r)
        lector.hasta(": conectado")
        redis.corta_stream = True
        lector.resto()  # termina solo: no hay que cerrar desde el cliente
    deadline = time.time() + 2
    while not all(p.cerrado for p in redis.todos) and time.time() < deadline:
        time.sleep(0.02)
    assert all(p.cerrado for p in redis.todos)


def test_al_cerrar_el_cliente_se_libera_la_suscripcion(servidor) -> None:
    redis = RedisFalso()
    redis.poner_ticket("t1", rol="ASESOR", usuarioId="u-1")
    with _cliente(servidor(redis, latido=0.05)) as cliente:
        with _abrir(cliente, "t1") as r:
            Lector(r).hasta(": conectado")
        deadline = time.time() + 2
        while redis.suscriptores and time.time() < deadline:
            time.sleep(0.02)
    assert redis.suscriptores == []


def test_el_ticket_no_aparece_en_los_logs_del_servicio(servidor, caplog) -> None:
    redis = RedisFalso()
    redis.poner_ticket("ticket-secreto-0001", rol="ASESOR", usuarioId="u-1")
    with caplog.at_level("DEBUG"):
        with _cliente(servidor(redis, latido=0.02)) as cliente:
            with _abrir(cliente, "ticket-secreto-0001") as r:
                Lector(r).hasta(": latido")
    del_servicio = [r.getMessage() for r in caplog.records if r.name.startswith("avisos")]
    assert del_servicio  # sí registra que abrió el stream
    assert "ticket-secreto-0001" not in " ".join(del_servicio)
