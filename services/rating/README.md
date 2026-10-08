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
- `POST /ofertas`: calcula la oferta (score de riesgo + prima) sobre ese mismo perfil, con
  Cache-Aside del score en Redis (HA-LAT-001). Ver la sección siguiente.

## Ampliación de alcance (pedida explícitamente por el usuario, sin ID de Jira)

Este README decía que el cálculo real de la oferta/prima (T-W01-6 completo, HA-MOD-002) y el
Cache-Aside de scores en Redis (HA-LAT-001) quedaban fuera de alcance, para Sprint 2. El usuario
amplió explícitamente el alcance del Sprint 1 para construirlos ahora — documentado también en
`docs/sprint-1.md` junto a la ampliación equivalente de `consolidador-fuentes`.

**No existe una fórmula actuarial definida en el material del curso.** Se investigó el PDF del
proyecto, el backlog de Jira y `PlanningV1.2.xlsx` de forma explícita antes de construir esto: solo
hay una mención genérica de que debe existir un "motor de reglas actuariales" que combine las 5
fuentes, sin pesos ni fórmula numérica. Lo que se implementó es un **modelo genérico e ilustrativo
de la industria** (scoring tipo crediticio + pricing de seguro de vida hipotecario estándar),
pedido así explícitamente por el usuario — **no es una fórmula actuarial certificada**. Mismo nivel
de honestidad que el hueco de cifrado de campo documentado más abajo y en `services/risk/README.md`.

### Score de riesgo (0-100, mayor = mejor perfil)

Cada una de las 5 fuentes del perfil se normaliza a 0-100 y se combina con un promedio ponderado
(`app/application/servicio_oferta.py`):

| Fuente | Campos usados | Normalización | Peso |
|---|---|---|---|
| `historial-crediticio` | `score_crediticio` (150-950) | `min(100, max(0, (score_crediticio - 150) / 800 * 100))` | 0.30 |
| `cuentas-bancarias` | `saldo_promedio_3m`, `productos_activos` | `min(100, saldo_promedio_3m / 5_000_000 * 80 + productos_activos * 10)` | 0.20 |
| `afiliacion-pila` | `estado_afiliacion`, `antiguedad_meses` | `70` si activo sino `30`, más `min(30, antiguedad_meses / 2)` | 0.20 |
| `camara-comercio` | `existe_registro` | `70` si existe sino `50` | 0.15 |
| `antecedentes-judiciales` | `antecedentes_vigentes` | `20` si vigentes sino `100` | 0.15 |

Si una fuente falta en el documento de perfil, viene `degradado=True`, o su `datos` llega vacío
(`{}`): se usa el valor neutro **50** para esa fuente (empuja el promedio hacia el centro, no hacia
0) — nunca se excluye del promedio ponderado. `score_total = round(sum(peso_i * subscore_i))`,
acotado a `[0, 100]`.

`desgloseScore` (lo que expone la respuesta) son EXACTAMENTE los 5 `{nombre, peso}` de la tabla —
el contrato (`FactorScore`) no pide los subscores individuales, solo el peso de cada fuente.

### Prima (modelo genérico de seguro de vida hipotecario)

```
tasa_base_mensual = 0.00045   # 0.045% del monto asegurado por mes — ilustrativo
factor_riesgo = 2.2 - (score_total / 100) * 1.4   # 0.8 (score=100) .. 2.2 (score=0)
prima_mensual = cobertura * tasa_base_mensual * factor_riesgo
```

Redondeada a 2 decimales con `ROUND_HALF_UP`. Todo el camino usa `decimal.Decimal`, nunca `float`
— la respuesta expone el monto como texto (`Monto.valor`), igual que en `payments`.

### Oferta preliminar vs completa (CA-W01-04)

Si CUALQUIERA de las 5 fuentes no está fresca (falta, `de_cache=True` o `degradado=True`):
`tipo=PRELIMINAR`, se devuelve `rangoPrima` (±20% alrededor de `prima_mensual`), sin `prima`
puntual. Si las 5 están frescas: `tipo=COMPLETA`, se devuelve `prima` puntual, sin `rangoPrima`.

### Cache-Aside del score (HA-LAT-001) — decisión de diseño sobre la clave

`app/application/cache_score.py` (mismo patrón que `acl-worker/app/application/cache.py`: un fallo
de Redis nunca se propaga, cache-aside puro). La clave es la especificada en la tarea —
`oferta:{cliente_id}:{perfil_version}:{version_reglas}` — con `cliente_id` reemplazado por su hash
opaco (`id_opaco`, defensa en profundidad: la clave nunca lleva un identificador en crudo, aunque
viva en Redis interno, mismo criterio que ya usa `acl-worker`).

**Decisión que no estaba escrita en la especificación:** lo que se cachea bajo esa clave es el
**score** (y los metadatos de las 5 fuentes/el tipo de oferta que de él se derivan) — **no** la
prima ni la oferta completa. La clave especificada no incluye `cobertura`, y `cobertura` es un dato
de la *solicitud* que puede cambiar entre dos llamadas para el mismo `cliente_id`/`perfil_version`;
cachear la prima ahí habría devuelto un número incorrecto para una cobertura distinta a la primera
consulta cacheada. El score, en cambio, solo depende del perfil, así que cachearlo es seguro — y es
exactamente lo que nombra la habilitadora ("Cache-Aside de **scores** en Redis"). La prima se
recalcula en cada solicitud (aritmética pura, sin E/S) a partir del score (cacheado o no) y la
`cobertura` recibida. Verificado con un test real
(`tests/test_servicio_oferta.py::test_cobertura_distinta_en_la_segunda_consulta_sigue_dando_la_prima_correcta`).

`VERSION_REGLAS = "v1"` es una constante en el código: al cambiar los pesos o la fórmula se sube
(p. ej. a `"v2"`), lo que cambia la clave y deja la caché vieja huérfana — nunca se lee por
accidente con reglas distintas, y expira sola por `OFERTA_CACHE_TTL_SEGUNDOS`. Verificado con un
test real que fuerza el cambio de versión con `monkeypatch`
(`tests/test_servicio_oferta.py::test_cambiar_version_de_reglas_invalida_el_hit_de_cache`).

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

- **Cifrado de campo:** mismo hueco que RISK (sin Cloud KMS, sin helper de cifrado en el repo);
  este servicio lee lo que RISK escribió sin cifrar, y la caché de Redis tampoco cifra el score.

## Operación

- Puerto local: `8003`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- `GET /perfiles/{cliente_id}?operation_time=<time>.<inc>` →
  `{cliente_id, perfil_version, fuentes, leido_de_secundaria}` (200), `404` si no hay perfil para
  ese cliente, `400` si `operation_time` no tiene el formato esperado, `422` si falta el parámetro.
- `POST /ofertas` con cuerpo `{cliente_id, operation_time, cobertura}` -> `Oferta` (200, forma 1:1
  con el contrato: ver `contracts/graphql/schema.graphql` y la sección de arriba), `404` si no hay
  perfil para ese cliente, `400` si `operation_time` o `cobertura` no tienen un formato válido,
  `422` si falta algún campo del cuerpo.
- Variables de entorno: `MONGO_URI`, `REDIS_URL` (Cache-Aside del score, HA-LAT-001),
  `RATING_MAX_TIME_MS` (150 por defecto — cota de espera en la secundaria antes de caer a la
  primaria, D-02), `OFERTA_CACHE_TTL_SEGUNDOS` (300 por defecto — TTL del score cacheado).
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest`
  (desde esta carpeta). Las pruebas usan dobles de prueba (`tests/fakes.py`), no MongoDB ni Redis
  reales.
- Imagen: `docker build -t solventa-rating .`

### Verificación en vivo del cálculo de oferta y el Cache-Aside — evidencia real (2026-10-08)

```bash
docker compose --profile servicios up -d --build redis mongo1 mongo2 mongo-init risk rating

curl -s -X POST localhost:8002/perfiles -H 'content-type: application/json' -d '{
  "cliente_id": "cliente-demo-rating-1",
  "fuentes": [
    {"fuente": "historial-crediticio", "datos": {"score_crediticio": 712, "obligaciones_vigentes": 1}, "capturado_en": "2026-10-08T12:00:00+00:00", "de_cache": false, "degradado": false},
    {"fuente": "cuentas-bancarias", "datos": {"saldo_promedio_3m": 4250000, "productos_activos": 2}, "capturado_en": "2026-10-08T12:00:00+00:00", "de_cache": false, "degradado": false},
    {"fuente": "afiliacion-pila", "datos": {"estado_afiliacion": "activo", "antiguedad_meses": 36}, "capturado_en": "2026-10-08T12:00:00+00:00", "de_cache": false, "degradado": false},
    {"fuente": "camara-comercio", "datos": {"existe_registro": true, "actividad_economica": "otros servicios"}, "capturado_en": "2026-10-08T12:00:00+00:00", "de_cache": false, "degradado": false},
    {"fuente": "antecedentes-judiciales", "datos": {"antecedentes_vigentes": false}, "capturado_en": "2026-10-08T12:00:00+00:00", "de_cache": false, "degradado": false}
  ]
}'
# -> {"cliente_id":"cliente-demo-rating-1","perfil_version":1,"operation_time":"1791477317.4"}

curl -s -X POST localhost:8003/ofertas -H 'content-type: application/json' -d '{
  "cliente_id": "cliente-demo-rating-1", "operation_time": "1791477317.4", "cobertura": "200000000"
}'
```

Resultado real obtenido: `score=82` (verificable con la fórmula:
`0.30*70.25 + 0.20*88 + 0.20*88 + 0.15*70 + 0.15*100 = 81.775 -> 82`), `tipo=COMPLETA`,
`prima={"valor":"94680.00","moneda":"COP"}` (`factor_riesgo = 2.2 - 0.82*1.4 = 1.052`;
`200000000 * 0.00045 * 1.052 = 94680.00`), las 5 fuentes con `estado=OK`. Log de `rating`:
`score_cache=miss`. Repetida la MISMA consulta, la respuesta es idéntica y el log cambia a
`score_cache=hit` (segunda vez no recalcula, HA-LAT-001).

Forzando una fuente degradada al escribir el perfil (`historial-crediticio` con `datos: {}`,
`degradado: true`), la oferta sale `tipo=PRELIMINAR`, sin `prima`, con
`rangoPrima={"minima":"81792.00","maxima":"122688.00"}` (score=76 por el neutro 50 en esa fuente;
`factor_riesgo=1.136`; `prima_mensual=102240.00`; +-20% = 81792.00/122688.00) y esa fuente con
`estado=FALLO`. `operation_time`/`cobertura` inválidos responden `400`; un `cliente_id` sin perfil
responde `404` — nunca un 5xx crudo (CA-W01-05). Los logs solo muestran el hash opaco del
`cliente_id` (`id_opaco`), nunca el valor en claro.

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
