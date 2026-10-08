# consolidador-fuentes

Consolidador de reconciliación diferida de Open Finance/Open Data.

> **Ampliación de alcance pedida directamente por el usuario**, fuera de `docs/sprint-1.md`
> original (ver la nota en la sección "Observaciones de trazabilidad pendientes" de ese archivo
> y la sección "Consolidador de fuentes" de `docs/arquitectura-backend.md`): no responde a
> ninguna HU/HA/CA del plan del Sprint 1. Diseño **traducido** (no copiado) del Consolidador KYC
> del Experimento 1 (`solventa-arquitectura@11e4be6`, Node.js/TypeScript + BullMQ) a
> Python + `rq` (Redis Queue), aplicado aquí a Open Finance y Open Data — el Consolidador de KYC
> (Truora) en sí sigue fuera de alcance, diferido al Sprint 2.

## Responsabilidad

Cuando `acl-worker` (`app/application/servicio_fuentes.py`) no consigue una respuesta real de
Open Finance/Open Data (circuito abierto o cualquier falla) — sin importar si termina
respondiendo al llamador con el último valor en caché o con un valor degradado mínimo — encola,
de forma fire-and-forget, un job de reconciliación en la cola `fuentes-reconciliacion` (Redis,
db 2, separada de la caché de `acl-worker` en la db 1). Este servicio consume esa cola y repite
la consulta real llamando al ACL Worker por HTTP — **nunca al proveedor directo** (regla de
arquitectura #2): `acl-worker` sigue siendo el único punto de contacto con proveedores externos.

- `app/worker.py` (`TrabajadorReconciliacion`): corre `rq.SimpleWorker` en un hilo de fondo
  arrancado desde el lifespan de FastAPI — mismo patrón que `SondaRecuperacion` de `acl-worker`
  (`rq.Worker.work()` es bloqueante). Cuatro detalles de `rq` no evidentes corriendo en un hilo
  que no es el principal, documentados en el módulo (los cuatro se encontraron verificando en
  vivo con Redis real, no con las pruebas unitarias — ver "Limitaciones conocidas" más abajo para
  el porqué las pruebas no los detectan): (1) `work()` instala manejadores de señal de proceso con
  `signal.signal()`, que solo funciona en el hilo principal — se desactivan
  (`_SimpleWorkerSinSenales`); (2) el timeout por job de `rq` (`death_penalty_class`) también usa
  `signal.signal()` (SIGALRM) — se cambia a `rq.timeouts.TimerDeathPenalty`, que usa
  `threading.Timer`; (3) construir `rq.SimpleWorker(...)` conecta a Redis de inmediato (no es
  perezoso como `redis.Redis.from_url()`), por eso se construye dentro del hilo de fondo, no en
  `__init__` — si Redis está caído al iniciar, el hilo lo registra en logs y termina, pero el
  contenedor sigue vivo y respondiendo `/health`; (4) los reintentos con backoff
  (`rq.Retry(interval=[...])`) solo se promueven de `rq:scheduled:...` a la cola real si algo
  llama a `RQScheduler.enqueue_scheduled_jobs()` — `work(with_scheduler=False)` no lo hace sola,
  así que el trabajador lo hace a mano en cada ciclo de inactividad.
- `app/tareas.py` (`reconciliar_fuente`): la tarea que ejecuta `rq`. Si la respuesta del ACL
  Worker vuelve fresca (`de_cache=False` y `degradado=False`), el job termina bien (la caché ya
  quedó al día porque `ServicioConsultaFuentes` la re-guarda en su propio camino de éxito). Si
  sigue degradada, o el ACL Worker no responde, lanza una excepción para que `rq` reintente con el
  backoff configurado por el productor (`REINTENTOS_RECONCILIACION` en
  `services/acl-worker/app/main.py`: máx. 5 intentos, 10/30/60/120/300 s); agotados, `rq` mueve el
  job al `FailedJobRegistry` (equivalente a la DLQ del diseño original) — no se reintenta
  indefinidamente ni queda oculto.
- `app/main.py`: FastAPI con `/health` y `/metrics` únicamente; arranca/detiene el trabajador en
  el lifespan.
- `app/identificadores.py`: `id_opaco` duplicado de `services/acl-worker/app/application/identificadores.py`
  (regla de arquitectura #6, sin `cliente_id` en crudo en logs) — cada servicio es una imagen
  Docker independiente, sin paquete Python compartido entre servicios.

Sin arquitectura hexagonal (regla de arquitectura #3): es andamiaje de reconciliación, no el
límite anticorrupción con proveedores externos — ese límite ya lo tiene `acl-worker`.

## Fuera de alcance de este servicio

- Decidir si debe recalcularse una oferta/perfil ya mostrado con el dato degradado una vez que
  aquí se consigue el dato fresco: es una decisión de negocio de RISK/RATING, no de este
  Consolidador (igual que en el diseño original de KYC).
- KYC y firma electrónica (Sprint 2): el Consolidador de KYC del Experimento 1 no se portó, solo
  se tradujo su patrón de reconciliación diferida.
- Persistencia propia: este servicio no escribe en ningún almacén de negocio, solo consume la
  cola de Redis y vuelve a llamar al ACL Worker.

## Operación

- Puerto local: `8006`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Variables de entorno: `REDIS_URL` (`redis://localhost:6379/2` por defecto, misma cola que
  `COLA_RECONCILIACION_REDIS_URL` del lado productor en `acl-worker`), `ACL_URL`
  (`http://localhost:8005` por defecto), `ACL_TIMEOUT_S` (3 s por defecto — más holgado que el
  presupuesto de 700 ms del camino síncrono, porque esta llamada corre en un job de fondo, no en
  el camino de usuario).
- Pruebas: `pip install -r requirements.txt pytest ruff && python -m pytest && ruff check .`
  (desde esta carpeta; no hay `requirements-dev.txt` compartido en este servicio todavía).
- Imagen: `docker build -t solventa-consolidador-fuentes .`

### Verificación en vivo (reconciliación diferida)

```bash
docker compose --profile servicios up -d --build redis acl-worker consolidador-fuentes stub-open-finance stub-open-data

# (a) se fuerza el stub a caído: acl-worker degrada y encola una reconciliación
curl -s -X POST localhost:8101/control/modo -H 'content-type: application/json' -d '{"modo":"caido"}'
for i in 1 2 3 4; do
  curl -s -X POST localhost:8005/fuentes/consultar \
    -H 'content-type: application/json' \
    -d '{"cliente_id":"c-1","consentimiento_id":"k-1","fuente":"cuentas-bancarias"}'
done
docker compose exec redis redis-cli -n 2 LLEN "rq:queue:fuentes-reconciliacion"   # > 0

# (b) el stub vuelve a sano: el Consolidador reconcilia solo, sin esperar al usuario.
# El primer reintento (a los ~10 s) puede fallar todavía si el circuito de acl-worker no
# cerró a tiempo (la sonda tarda ~6 s); el segundo (a los ~30 s más) ya debería ver el dato
# fresco — en la práctica, hasta ~40 s en total.
curl -s -X POST localhost:8101/control/modo -H 'content-type: application/json' -d '{"modo":"sano"}'
sleep 40
docker compose logs consolidador-fuentes | grep "job resuelto"   # "dato fresco obtenido, job resuelto"

# (c) una consulta normal ya hereda el dato fresco (de_cache:false)
curl -s -X POST localhost:8005/fuentes/consultar \
  -H 'content-type: application/json' \
  -d '{"cliente_id":"c-1","consentimiento_id":"k-1","fuente":"cuentas-bancarias"}'
```

## Limitaciones conocidas

- `tests/test_worker.py` usa una URL de Redis sin nadie escuchando a propósito (para no depender
  de Redis real en pytest): eso hace que la construcción de `rq.SimpleWorker` falle rápido, antes
  de llegar a invocar `work()` ni a ejecutar un job. Los 4 detalles de `rq` documentados en
  `app/worker.py` (manejadores de señal, timeout por job, conexión no perezosa, promoción manual
  de reintentos) solo se manifiestan con Redis real y al menos un job real — por diseño, ninguna
  prueba unitaria de este servicio los ejercita; se encontraron y confirmaron verificando en vivo
  con `docker compose` (ver sección anterior). Una prueba de integración con Redis real (`testcontainers`
  o un contenedor de Redis en CI) los cubriría; no se agregó en este alcance.
- `TrabajadorReconciliacion.detener()` pide parar vía un `threading.Event` y usa
  `max_idle_time=5` en `rq.SimpleWorker.work()` para acotar el apagado (en vez de depender de
  `request_stop`/señales de SO, que no son fiables desde un hilo que no es el principal): en el
  peor caso (trabajador inactivo, sin jobs) el apagado ordenado puede tardar hasta ~7 s. Aceptable
  para este alcance: el hilo es `daemon=True`, así que nunca impide que el proceso termine.
- Estado del trabajador en memoria del proceso, sin alta disponibilidad: si el contenedor se
  reinicia con un job en vuelo, `rq` lo reintenta según su propio mecanismo de recuperación de
  jobs huérfanos (no se agregó lógica propia encima).
