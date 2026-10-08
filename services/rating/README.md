# rating

RATING: cálculo de oferta sobre réplica de lectura.

## Responsabilidad

RATING calcula la oferta de vida hipotecario. **Nunca escribe en Riesgo.**

- `GET /perfiles/{cliente_id}?operation_time=<valor>`: lee el perfil de la **secundaria** de
  MongoDB con sesión causal (D-02, HA-LAT-003: 0 ofertas con perfil obsoleto). `operation_time` es
  el valor que RISK devolvió en `POST /perfiles` (hoy se pasa directo en la petición; en producción
  lo reenviaría el BFF).
- Si la secundaria no alcanza ese punto de corte dentro de `RATING_MAX_TIME_MS` (umbral corto,
  coherente con EC011/EC012: p95 ≤150 ms, p99 ≤300 ms), cae a leer de la **primaria** dentro de la
  MISMA solicitud — nunca un error al usuario por este motivo (D-02, alternativa explícita del
  plan).
- Cache-Aside de scores en Redis (HA-LAT-001) y el cálculo real de la oferta/prima (reglas
  actuariales) quedan **fuera de alcance de esta tarea** (T-W01-6 completo y HA-MOD-002, Sprint 2).

### Corrección de trazabilidad (importante)

Este README antes decía que la base reutilizable era `lector-rating` y `ryw.py` del Experimento 2
(`solventa-arquitectura@11e4be6`). **Eso no es correcto**: el Experimento 2
(`experimento-2-replica-riesgo`) solo tiene en `../solventa-arquitectura` el README de la
estructura esperada — ese código **no existe** en ningún lado de ese repo (se verificó
directamente). Este servicio se construyó desde cero a partir de la especificación de
`docs/sprint-1.md` (D-02, HA-LAT-003), no portando código de un experimento que nunca se construyó.

## Mecanismo de lectura causal (D-02) — lo que sí se construyó aquí

`app/application/lector_perfiles.py`:

1. Deserializa `operation_time` (mismo formato `"<time>.<inc>"` que usa RISK para serializar,
   duplicado a propósito en `tiempo_causal.py` — sin código compartido entre servicios) a un
   `bson.Timestamp`.
2. Abre una `ClientSession` con `causal_consistency=True` y le hace
   `advance_operation_time(punto_corte)` — eso agrega `afterClusterTime` a los comandos de esa
   sesión.
3. Lee con `read_preference=SECONDARY` y `max_time_ms=RATING_MAX_TIME_MS`: el servidor espera a que
   la secundaria alcance el punto de corte antes de responder; si no lo alcanza a tiempo, la
   operación falla con `MaxTimeMSExpired` (pymongo: `ExecutionTimeout`/`OperationFailure`).
4. Si eso ocurre, cae a leer con `read_preference=PRIMARY` en la MISMA sesión y solicitud (nunca un
   error al usuario por este motivo).

La decisión "intentar secundaria, caer a primaria si se agota el tiempo" está aislada como una
función pura e inyectable (`leer_con_fallback_a_primaria`), probada con dobles en
`tests/test_lector_perfiles.py` sin necesitar un MongoDB real. La parte que sí depende de pymongo
de verdad (sesión, `read_preference`, `maxTimeMS`) se verifica en vivo (ver abajo), no con pytest.

## Pendiente, fuera de alcance de esta tarea (huecos documentados)

- **Cache-Aside de scores en Redis (HA-LAT-001):** `REDIS_URL` ya está en `docker-compose.yml` pero
  este servicio todavía no lo usa — es T-W01-6 completo, Sprint 2.
- **Cálculo de la oferta/prima (reglas actuariales, HA-MOD-002):** este servicio solo devuelve el
  documento de perfil tal cual RISK lo escribió (`cliente_id`, `perfil_version`, `fuentes`,
  `leido_de_secundaria`); no calcula prima, cobertura ni desglose de score.
- **Cifrado de campo:** mismo hueco que RISK (sin Cloud KMS, sin helper de cifrado en el repo);
  este servicio lee lo que RISK escribió sin cifrar.

## Operación

- Puerto local: `8003`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- `GET /perfiles/{cliente_id}?operation_time=<time>.<inc>` →
  `{cliente_id, perfil_version, fuentes, leido_de_secundaria}` (200), `404` si no hay perfil para
  ese cliente, `400` si `operation_time` no tiene el formato esperado, `422` si falta el parámetro.
- Variables de entorno: `MONGO_URI`, `REDIS_URL` (reservada para el Cache-Aside de Sprint 2),
  `RATING_MAX_TIME_MS` (150 por defecto — cota de espera en la secundaria antes de caer a la
  primaria, D-02).
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest`
  (desde esta carpeta). Las pruebas usan dobles de prueba (`tests/fakes.py`), no MongoDB real.
- Imagen: `docker build -t solventa-rating .`

### Verificación en vivo (lectura causal real contra el replica set) — evidencia real

```bash
docker compose --profile servicios up -d --build redis mongo1 mongo2 mongo-init risk rating

# 1) Camino feliz: escribir en RISK y leer de inmediato en RATING con el
#    operation_time devuelto.
curl -s -X POST localhost:8002/perfiles -H 'content-type: application/json' -d '{
  "cliente_id": "cliente-demo-1",
  "fuentes": [{"fuente": "cuentas-bancarias", "datos": {"saldo": 100}, "capturado_en": "2026-10-07T12:00:00+00:00", "de_cache": false, "degradado": false}]
}'
curl -s "localhost:8003/perfiles/cliente-demo-1?operation_time=<T>"
```

Resultado real obtenido (2026-10-07): RISK devolvió `operation_time=1791424486.4`; RATING, leído
de inmediato (mismo segundo) con ese valor, respondió en **10 ms** con el dato recién escrito y
`"leido_de_secundaria": true` — confirma read-your-writes real sobre la secundaria, sin dato
obsoleto (CA-W01-09).

**Camino de fallback (D-02, alternativa) — se intentó forzar lag real, con resultados mixtos y
honestos (no se simuló de forma artificial poco representativa):**

- Con `RATING_MAX_TIME_MS=150` (el *default*), la replicación local entre `mongo1`/`mongo2` es
  consistentemente más rápida (~10 ms observados) que el presupuesto — igual que midió el
  Experimento 1 en papel (staleness p95 ~3,8 ms a la carga objetivo). El fallback no se dispara de
  forma natural en este entorno de un solo host sin carga real.
- Se probó con un `operation_time` deliberadamente en el futuro (más allá del `clusterTime` real
  del cluster). Mongo lo rechaza de inmediato con `OperationFailure` código 72
  (`InvalidOptions: readConcern afterClusterTime value must not be greater than the current
  clusterTime`) — **no** es la ruta de `maxTimeMS` agotado que D-02 describe, es una validación de
  entrada distinta. Sí confirmó que `leer_con_fallback_a_primaria` **intenta la primaria** cuando
  la secundaria falla (se vio en el log: la excepción ocurrió en la llamada a `_de_primaria`, no en
  `_de_secundaria`) — pero con este valor inválido la primaria recibe el MISMO error (su propio
  `clusterTime` tampoco alcanza ese punto inventado), y como el código solo envuelve en `try/except`
  la llamada a la secundaria (a propósito: un fallo de la primaria debe propagarse, no esconderse),
  esa combinación terminó en un **500 crudo** — un hueco real, pero que requiere un
  `operation_time` imposible (uno que RISK nunca devuelve: el suyo siempre es <= el `clusterTime`
  de la primaria que lo generó) para producirse. Documentado como hueco de endurecimiento de
  entrada, no como falla del mecanismo D-02 en sí.
- Se forzó lag real pausando el contenedor de `mongo2` (`docker pause`) antes de una escritura y
  lectura nuevas, con un `operation_time` válido (no inventado). **Primera ronda** (antes del
  cierre descrito abajo): la lectura no completó en 10 s (`curl --max-time 10` cortó, `HTTP 000`)
  — con la secundaria totalmente inalcanzable (no solo atrasada), la selección de servidor de
  pymongo bloqueaba antes de llegar a `max_time_ms`. Al despausar, el cluster se recuperó solo.

### Hueco cerrado: secundaria inalcanzable (no solo atrasada)

El hallazgo de arriba era real y con dos causas, ambas corregidas en `app/application/lector_perfiles.py`
y `app/main.py`:

1. **Selección de servidor sin acotar**: por defecto pymongo bloquea hasta 30 s decidiendo que no
   hay secundaria utilizable, antes de llegar siquiera a la consulta que `max_time_ms` acota.
2. **Socket sin timeout**: aun acotando lo anterior, la topología podía seguir creyendo que la
   secundaria estaba sana (un `docker pause` congela el proceso, no cierra el socket) y despachar
   la consulta ahí — sin timeout de socket por defecto en pymongo, esa lectura se queda colgada
   indefinidamente esperando una respuesta que nunca llega.

**Fix**: `LectorPerfilesMongo` ahora recibe DOS `MongoClient` — uno para la primaria (timeouts por
defecto, más generosos: el fallback no debe ser tan frágil como el camino que intenta evitar) y uno
dedicado exclusivamente a la secundaria, con `serverSelectionTimeoutMS`, `connectTimeoutMS` y
`socketTimeoutMS` acotados al mismo presupuesto (`RATING_MAX_TIME_MS`). `ServerSelectionTimeoutError`
y `AutoReconnect` (incluye `NetworkTimeout`) se agregaron a `ERRORES_FALLBACK_A_PRIMARIA`.

**Segunda ronda de verificación en vivo** (con el fix aplicado, mismo `docker pause` sobre `mongo2`):

```
RISK: operation_time=1791425256.2
[mongo2 pausado]
GET /perfiles/... → HTTP 200 en 0.565s, {"leido_de_secundaria": false, ...}   # antes: HTTP 000 a los 10s+
[mongo2 reanudado, 3s de espera]
GET /perfiles/... → HTTP 200 en 0.444s, {"leido_de_secundaria": true, ...}    # recuperado solo, sin reiniciar nada
```

Log de `rating` en el primer intento: `WARNING rating.lector: secundaria no alcanzo operationTime a
tiempo (maxTimeMS=150) cliente=<hash>; se cayo a la primaria`.

**Conclusión:** el mecanismo D-02 (sesión causal + `maxTimeMS` + fallback a primaria) ahora cubre
tanto "la secundaria responde pero no alcanzó el punto de corte a tiempo" (caso original de la
especificación) como "la secundaria está totalmente inalcanzable" (hallazgo de esta verificación) —
en ambos casos, sin 5xx y sin bloquear más allá del presupuesto configurado.
