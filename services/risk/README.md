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
- Emite `perfil.actualizado` por un bus de eventos **placeholder** (ver huecos abajo).
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
  creado_en}`, donde `fuentes` es la lista de `DatosFuente` recibida tal cual. Es append-only por
  versión (no se sobreescribe la versión anterior): cada escritura inserta un documento nuevo.
- **Asignación atómica de versión:** `find_one_and_update($inc)` sobre una colección de contadores
  (`risk.contadores_version`, `_id = cliente_id`) en la MISMA sesión que el `insert_one` del
  perfil — evita que dos escrituras concurrentes para el mismo cliente choquen de versión (a
  diferencia de leer el máximo existente y sumarle 1 sin atomicidad), y hace que
  `session.operation_time` final sea el de la escritura más reciente (el `insert_one`).
- **Bus de eventos:** interfaz mínima (`PuertoBusEventos`, función/clase — **sin** puertos y
  adaptadores formales, regla de arquitectura #3: hexagonal es solo para `acl-worker`) con un
  adaptador `BusEventosLog` que por ahora solo loguea el evento. Ver huecos.

## Pendiente, fuera de alcance de esta tarea (huecos documentados)

- **Cifrado de campo (SEG-002/D-04/CA-W01-10):** no hay Cloud KMS disponible (sin proyecto GCP,
  ver `CLAUDE.md`) y no existe todavía ningún helper de cifrado en el repo (AUTH tampoco lo ha
  construido). Los datos de las fuentes se persisten **sin cifrar**. Deliberadamente no se simula
  con un cifrado de juguete (daría falsa confianza). Pendiente para cuando AUTH y/o
  `infra-engineer` tengan el helper + KMS.
- **Bus de eventos real:** no existe infraestructura de Pub/Sub (sin proyecto GCP). `BusEventosLog`
  es un placeholder que solo loguea una versión **redactada** del evento (con el `cliente_id`
  hasheado, nunca en crudo — regla de arquitectura #6; el evento que se "publica" internamente sí
  lleva `clienteId` en claro, tal como exige el contrato). Cuando `infra-engineer` levante Pub/Sub
  real, se sustituye `BusEventosLog` por un publicador real detrás del mismo `PuertoBusEventos`,
  sin tocar `ServicioPerfiles`.
- **Idempotencia de `POST /perfiles` ante reintentos de red:** cada llamada crea una nueva versión;
  no hay deduplicación por clave de idempotencia (no pedida por ningún CA de esta tarea).

## Operación

- Puerto local: `8002`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- `POST /perfiles` `{cliente_id, fuentes: [{fuente, datos, capturado_en, de_cache, degradado}, ...]}`
  → `{cliente_id, perfil_version, operation_time}`.
- Variables de entorno: `MONGO_URI`, `ACL_URL` (no se usa en esta tarea — RISK no llama al ACL
  Worker directamente, recibe los datos ya resueltos en el cuerpo de la petición; se mantiene
  porque ya está en `docker-compose.yml`).
- Pruebas: `pip install -r requirements.txt -r ../../requirements-dev.txt && python -m pytest`
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
