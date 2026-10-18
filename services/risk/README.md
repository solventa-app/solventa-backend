# risk

RISK: único escritor del perfil de riesgo.

## Responsabilidad

RISK es el **único escritor del perfil de riesgo** (MongoDB primaria, réplica de lectura para RATING).

- `POST /perfiles`: recibe `cliente_id` y los datos ya resueltos de las fuentes (el mismo shape que
  ya devuelve `acl-worker` en `POST /fuentes/consultar` — `fuente`, `datos`, `capturado_en`,
  `de_cache`, `degradado`; no se inventa un contrato nuevo con el ACL Worker). Persiste un documento
  de perfil versionado (`perfil_version` incremental y atómico por cliente) con el *write concern*
  por defecto que ya fija `mongo-init` (`w:1`, deliberadamente NO `majority` — ver
  `dev/mongo/init-replica-set.js` y `docs/arquitectura-backend.md`).
- **D-02 (read-your-writes):** usa una `ClientSession` explícita para leer `session.operation_time`
  después de escribir, lo serializa a texto y lo devuelve en la respuesta HTTP — es lo que el
  llamador (hoy, directo en el body; en producción lo reenviaría el BFF) le pasa a RATING para su
  sesión causal.
- **Score, `nivelRiesgo` y `fechaVigencia` (HA-LAT-001, `PerfilRiesgo`):** calcula el score de
  riesgo ponderado (`score_total`), clasifica `nivel_riesgo` (`ALTO` 0-39, `MEDIO` 40-69, `BAJO`
  70-100), calcula `fecha_vigencia` (+30 días ISO-8601), persiste los 4 campos junto al perfil en
  MongoDB y escribe el `ScoreCacheado` en Redis (`oferta:{id_opaco}:{perfil_version}:{version_reglas}`).
  Carga pesos, orígenes y obligatoriedad desde `contracts/fuentes.json` (con fallback a constantes
  si no está montado).
- **Cifrado de campo + sobre KMS (SEG-002 / D-04 / CA-W01-10 / EC022):** antes de insertar en
  MongoDB, cifra cada `fuente.datos` con AES-256-GCM y sobre KMS (`datos_cifrados`) y persiste
  `datos: {}` en reposo.
- **Eventos (`perfil.actualizado`):** emite `perfil.actualizado` por `BusEventosLog` (local) o
  `BusEventosPubSub` (cuando `PUBSUB_TOPIC_PERFIL_ACTUALIZADO` está configurado), incluyendo
  `nivelRiesgo`, `fechaVigencia` y `versionReglas`.
- **Batch nocturno `risk-reproceso` (HA-ESC-004, EC017):** ejecutable como
  `python -m app.batch_reproceso` para recalcular perfiles vencidos o con `version_reglas`
  anterior en lotes idempotentes sin contención con el tráfico interactivo.
- Logs sin datos personales: `cliente_id` nunca se loguea en crudo (hash opaco, mismo patrón que
  `acl-worker/app/application/identificadores.py`).

### Corrección de trazabilidad (importante)

Este README antes decía que la base reutilizable era `escritor-risk` del Experimento 2
(`solventa-arquitectura@11e4be6`). **Eso no es correcto**: el Experimento 2
(`experimento-2-replica-riesgo`) solo tiene en `../solventa-arquitectura` el README de la
estructura esperada — `escritor-risk`, `lector-rating` y `ryw.py` **no existen como código** en
ningún lado de ese repo. Este servicio se construyó desde cero a partir de la especificación de
`docs/sprint-1.md` (D-02, HA-LAT-003) y de este mismo README, no portando código de un experimento
que nunca se construyó.

## Diseño (decisiones no escritas en el plan)

- **Formato de `operationTime` serializado:** texto `"<time>.<inc>"` (los dos componentes de
  `bson.timestamp.Timestamp`), p. ej. `"1733600123.7"` — simple, explícito y reversible sin tocar
  BSON binario ni JSON extendido de Mongo. Ver `app/application/tiempo_causal.py`. RATING usa el
  mismo formato (duplicado a propósito, sin código compartido entre servicios).
- **Forma del documento de perfil** (`risk.perfiles`): `{cliente_id, perfil_version, fuentes,
  creado_en, score_total, nivel_riesgo, fecha_vigencia, version_reglas}`, donde cada elemento de
  `fuentes` persiste `datos: {}` y `datos_cifrados: <sobre KMS + AES-256-GCM>`. Es append-only por
  versión (no se sobreescribe la versión anterior): cada escritura inserta un documento nuevo.
- **Asignación atómica de versión:** `find_one_and_update($inc)` sobre una colección de contadores
  (`risk.contadores_version`, `_id = cliente_id`) en la MISMA sesión que el `insert_one` del
  perfil — evita que dos escrituras concurrentes para el mismo cliente choquen de versión (a
  diferencia de leer el máximo existente y sumarle 1 sin atomicidad), y hace que
  `session.operation_time` final sea el de la escritura más reciente (el `insert_one`).
- **Bus de eventos:** interfaz mínima (`PuertoBusEventos`, función/clase — **sin** puertos y
  adaptadores formales, regla de arquitectura #3: hexagonal es solo para `acl-worker`) con
  `BusEventosLog` (por defecto) y `BusEventosPubSub` (activo si se define
  `PUBSUB_TOPIC_PERFIL_ACTUALIZADO`).

## Huecos cerrados y alcances pendientes

- **Cifrado de campo (SEG-002/D-04/CA-W01-10/EC022) — CERRADO:** `app/application/cifrado.py`
  implementa cifrado de sobre AES-256-GCM con DEK aleatoria por campo envuelta por una KEK de 256
  bits (`RISK_KEK_BASE64`, compartida con RATING; en local usa una clave de desarrollo determinista
  con warning explícito). Ningún documento en `risk.perfiles` guarda PII ni datos financieros en
  texto plano (`datos: {}`, payload en `datos_cifrados`).
- **Bus de eventos Pub/Sub — CERRADO:** `BusEventosPubSub` publica el contrato JSON en Cloud
  Pub/Sub (o en el emulador local si `PUBSUB_EMULATOR_HOST` está definido) cuando se configura
  `PUBSUB_TOPIC_PERFIL_ACTUALIZADO`, con degradación segura a log si Pub/Sub falla.
- **Idempotencia de `POST /perfiles` ante reintentos de red:** cada llamada crea una nueva versión;
  no hay deduplicación por clave de idempotencia (no pedida por ningún CA de esta tarea).

## Operación

- Puerto local: `8002`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- `POST /perfiles` `{cliente_id, fuentes: [{fuente, datos, capturado_en, de_cache, degradado}, ...]}`
  → `{cliente_id, perfil_version, operation_time, score_total, nivel_riesgo, fecha_vigencia, version_reglas}`.
- Batch nocturno: `python -m app.batch_reproceso` (variables opcionales `BATCH_LIMITE`, `BATCH_TAMANO_LOTE`, `BATCH_PAUSA_MS`).
- Variables de entorno: `MONGO_URI`, `MONGO_DB` (`risk`), `REDIS_URL`, `OFERTA_CACHE_TTL_SEGUNDOS`
  (`300`), `RISK_KEK_BASE64` (32 bytes en base64 para cifrado AES-256-GCM), `RISK_KMS_KEY_ID`
  (`local-dev-kek`), `PUBSUB_PROJECT_ID`, `PUBSUB_TOPIC_PERFIL_ACTUALIZADO`, `ACL_URL`.
- Pruebas: `pip install -r requirements.txt && pip install -r ../../requirements-dev.txt && python -m pytest`
  (desde esta carpeta). Las pruebas usan dobles de prueba (`tests/fakes.py`), no MongoDB real —
  ver "Verificación en vivo" más abajo para la prueba con el replica set real.
- Imagen: `docker build -t solventa-risk .`

## Migraciones

MongoDB `risk` (este servicio es su único escritor; RATING solo lee y **no** migra). Runner propio mínimo: cada migración es un módulo
`migrations/versiones/NNNN_descripcion.py` con `aplicar(db)`, registrado en la colección `_migraciones`.

- Aplicar: `python -m migrations` (necesita `MONGO_URI`; `MONGO_DB` por defecto `risk`). En `docker compose` lo hace `migrar-risk`; en staging, el Cloud Run Job `migrar-risk`.
- **Idempotentes** (crear un índice que ya existe no falla) y compatibles hacia atrás: se aplican mientras la revisión anterior aún atiende (ADR-07).
- Verificar: `bash ../../scripts/verificar-migraciones.sh risk` (aplica dos veces y revisa el registro).
- El índice compuesto que necesita RATING para `find_one({"cliente_id": ...}, sort=[("perfil_version", -1)])`
  (T-W01-7) va en la migración `migrations/versiones/0002_perfiles.py` (`cliente_id` ascendente,
  `perfil_version` descendente); la base es `0001`.
- La migración `migrations/versiones/0003_cifrar_datos_fuentes.py` (con alias `v0002_cifrar_datos_fuentes.py`)
  cifra in-place e idempotentemente cualquier documento existente que aún tenga `fuentes[].datos` en claro.

### Verificación en vivo (read-your-writes real, D-02/HA-LAT-003)

```bash
docker compose --profile servicios up -d --build redis mongo1 mongo2 mongo-init risk rating

# Escribir un perfil en RISK
curl -s -X POST localhost:8002/perfiles -H 'content-type: application/json' -d '{
  "cliente_id": "cliente-demo-1",
  "fuentes": [{"fuente": "cuentas-bancarias", "datos": {"saldo": 100}, "capturado_en": "2026-10-07T12:00:00+00:00", "de_cache": false, "degradado": false}]
}'
# -> {"cliente_id":"cliente-demo-1","perfil_version":1,"operation_time":"<time>.<inc>"}

# Leerlo inmediatamente en RATING con el operation_time devuelto arriba
curl -s "localhost:8003/perfiles/cliente-demo-1?operation_time=<time>.<inc>"
# -> {"cliente_id":"cliente-demo-1","perfil_version":1,"fuentes":[...],"leido_de_secundaria":true|false}
```

Ver `services/rating/README.md` para el detalle del mecanismo de lectura (sesión causal +
fallback a primaria) y la evidencia de la verificación en vivo.
