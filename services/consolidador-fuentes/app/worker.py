"""Trabajador de reconciliación: consume la cola `fuentes-reconciliacion` con
`rq.SimpleWorker`, corriendo en un hilo de fondo arrancado desde el lifespan
de FastAPI — mismo patrón que `SondaRecuperacion` en `acl-worker`
(`app/application/sonda.py`), necesario porque `rq.Worker.work()` es
bloqueante. Así el contenedor expone `/health`/`/metrics` normales Y procesa
jobs, sin un segundo proceso/entrypoint.

Se usa `SimpleWorker` (ejecuta el job en el mismo proceso, sin `fork()`) y no
el `Worker` por defecto de `rq` (que hace fork por job, pensado para
ejecutarse desde el hilo principal de un proceso dedicado): forkear desde un
hilo que no es el principal es frágil en Python. `SimpleWorker` evita ese
problema por completo; el costo es perder el aislamiento por proceso de cada
job, aceptable aquí porque la tarea es una sola llamada HTTP acotada por
timeout (`app/tareas.py`), no código que pueda colgar el proceso para
siempre.

Cuatro detalles de `rq` que no son evidentes corriendo `work()` en un hilo
que no es el principal (y que rompían el trabajador en la práctica —no solo
en las pruebas—: se detectaron recién al verificar en vivo con
`docker compose`, con Redis real y el reintento de un job real, no con las
pruebas unitarias):

1. `rq.SimpleWorker.work()` siempre llama a `signal.signal()` para instalar
   sus manejadores de SIGINT/SIGTERM — y `signal.signal()` solo puede
   invocarse desde el hilo principal del intérprete. Sin el no-op de
   `_SimpleWorkerSinSenales` abajo, la primera llamada a `work()` lanzaba
   `ValueError: signal only works in main thread...` y el trabajador nunca
   procesaba un job. El apagado ordenado del proceso ya lo maneja
   uvicorn/FastAPI en el hilo principal; aquí basta con poder pedirle parar
   al bucle sin pasar por señales de sistema operativo.
2. Al ejecutar cada job, `rq` aplica su propio timeout (`death_penalty_class`,
   por defecto `UnixSignalDeathPenalty`) armando una alarma con
   `signal.signal(signal.SIGALRM, ...)` — la misma restricción de hilo
   principal que el punto 1, pero esta vez por job, no al arrancar `work()`:
   sin corregirlo, cada job fallaba de inmediato con el mismo `ValueError`
   *antes* de ejecutar `app.tareas.reconciliar_fuente` (la llamada HTTP real
   al ACL Worker nunca llegaba a pasar) y solo se veía reflejado como un
   reintento agendado, nunca como un job resuelto. Se usa
   `rq.timeouts.TimerDeathPenalty` (implementado con `threading.Timer`, sin
   esa restricción) en vez del valor por defecto.
3. Construir `rq.SimpleWorker(...)` conecta a Redis de inmediato (vía
   `client_setname`) — no es perezoso como `redis.Redis.from_url()` o
   `rq.Queue(...)`. Por eso la construcción se hace dentro del hilo de fondo
   (`_trabajar`), nunca en `__init__`: si Redis está caído al iniciar, el
   hilo lo registra en logs y termina, pero no tumba el lifespan de FastAPI
   ni el contenedor (que sigue respondiendo `/health`).
4. Los reintentos con backoff (`rq.Retry(interval=[...])`, ver
   `REINTENTOS_RECONCILIACION` en `services/acl-worker/app/main.py`) no se
   vuelven a encolar solos: `rq` los guarda en un registro
   `ScheduledJobRegistry` (`rq:scheduled:<cola>` en Redis) y es el
   **scheduler** de `rq` (`RQScheduler.enqueue_scheduled_jobs()`) quien los
   mueve de ahí a la cola real cuando vence su tiempo. `work(with_scheduler=
   False)` nunca arranca ese scheduler (se evita a propósito: por defecto
   corre en un subproceso aparte, innecesario aquí) — sin promoverlos a
   mano, un job reintentado queda varado para siempre en `rq:scheduled:...`,
   nunca vuelve a ejecutarse. Se promueven manualmente, en el propio hilo de
   fondo, en cada ciclo de inactividad (como máximo cada `MAX_OCIO_SEGUNDOS`).
"""

import logging
import threading

import redis
import rq
from rq.scheduler import RQScheduler
from rq.timeouts import TimerDeathPenalty

logger = logging.getLogger("consolidador_fuentes.worker")

NOMBRE_COLA = "fuentes-reconciliacion"

# Cota superior de cuánto puede tardar `detener()` en encontrar un punto
# seguro para salir si el trabajador está inactivo (sin jobs) cuando se pide
# el apagado: en vez de depender de que una señal de SO interrumpa un hilo
# que no es el principal (no funciona, ver punto 1 arriba), `work()` se
# invoca con `max_idle_time` para que retorne sola cada vez que no hay jobs,
# y el bucle externo decide si volver a invocarla o salir.
MAX_OCIO_SEGUNDOS = 5


class _SimpleWorkerSinSenales(rq.SimpleWorker):
    """Ver puntos 1 y 2 del docstring del módulo: no instala manejadores de
    señal de proceso al arrancar, y usa un timeout por job que no depende del
    hilo principal — porque este trabajador nunca corre en el hilo
    principal."""

    death_penalty_class = TimerDeathPenalty

    def _install_signal_handlers(self) -> None:
        return None


class TrabajadorReconciliacion:
    def __init__(self, redis_url: str, nombre_cola: str = NOMBRE_COLA) -> None:
        self._redis_url = redis_url
        self._nombre_cola = nombre_cola
        self._conexion: redis.Redis | None = None
        self._hilo: threading.Thread | None = None
        self._detener_solicitado = threading.Event()

    def iniciar(self) -> None:
        self._hilo = threading.Thread(target=self._trabajar, daemon=True)
        self._hilo.start()

    def _trabajar(self) -> None:
        try:
            self._conexion = redis.Redis.from_url(self._redis_url)
            cola = rq.Queue(self._nombre_cola, connection=self._conexion)
            worker = _SimpleWorkerSinSenales([cola], connection=self._conexion)
            # Ver punto 4 del docstring del módulo: promueve a mano los
            # reintentos vencidos (sin lock, sin subproceso — somos el único
            # consumidor de esta cola).
            planificador = RQScheduler(cola, connection=self._conexion)
            planificador.prepare_registries([self._nombre_cola])
        except Exception:
            # Redis caído al iniciar, o cualquier otro fallo al conectar: se
            # registra y el hilo termina, pero el contenedor sigue vivo
            # (sigue respondiendo /health) — nunca tumba el proceso completo.
            logger.exception("el trabajador de reconciliación no pudo conectar a Redis")
            return

        while not self._detener_solicitado.is_set():
            try:
                planificador.enqueue_scheduled_jobs()
            except Exception:
                logger.exception("no se pudieron promover los reintentos vencidos a la cola")
            try:
                worker.work(with_scheduler=False, max_idle_time=MAX_OCIO_SEGUNDOS)
            except Exception:
                logger.exception("el trabajador de reconciliación terminó con un error")
                return

    def detener(self) -> None:
        self._detener_solicitado.set()
        if self._hilo is not None:
            self._hilo.join(timeout=MAX_OCIO_SEGUNDOS + 2)
        if self._conexion is not None:
            try:
                self._conexion.close()
            except Exception:
                logger.warning("no se pudo cerrar la conexión de Redis del trabajador")
