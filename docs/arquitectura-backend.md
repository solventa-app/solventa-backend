# Arquitectura del backend (resumen operativo)

Resumen de lo que importa para construir. La fuente completa es el Documento de Arquitectura refinado (v6, fuera de este repo) y,
para el comportamiento medido, `../solventa-arquitectura/DISENO-EXPERIMENTOS.md`. Si el código obliga a cambiar algo de aquí,
se actualiza este archivo en el mismo cambio.

## Servicios

| Servicio | Puerto local | Responsabilidad | Almacén (escritor único) | Sprint |
|---|---|---|---|---|
| `bff` | 8000 | BFF GraphQL único (web `/graphql/web`, móvil `/graphql/app`); sin lógica de negocio | — | 1 |
| `auth` | 8001 | Consentimiento append-only; JWT con scopes (Sprint 2) | PostgreSQL `auth` | 1 |
| `risk` | 8002 | Escribe el perfil de riesgo; devuelve `operationTime`; cifrado de campo | MongoDB primaria | 1 |
| `rating` | 8003 | Calcula la oferta; lee de la secundaria; caché Redis | — (solo lee) | 1 |
| `payments` | 8004 | Cobros con pasarela tokenizada, idempotencia, reintento con conciliación, búsqueda para el back-office | PostgreSQL `payments` | 1 |
| `acl-worker` | 8005 | Único contacto con proveedores externos; hexagonal; Circuit Breaker (700 ms) | Redis (estado del circuito y caché) | 1 |
| `stubs/open-finance` | 8101 | Proveedor simulado (2 fuentes) | — | 1 |
| `stubs/open-data` | 8102 | Proveedor simulado (3 fuentes) | — | 1 |
| `stubs/pasarela` | 8103 | Pasarela de pago simulada | — | 1 |
| `consolidador-fuentes` | 8006 | Reconciliación diferida de Open Finance/Open Data degradadas (`rq` sobre Redis) | Redis (cola, db 2) | 1 (ampliación de alcance) |
| `under`, `policy`, `claims`, `consolidador-kyc` | — | Suscripción, pólizas, siniestros y Consolidador de KYC (Truora) | — | 2–3 |

Cada servicio tiene su propia base: **nadie lee ni escribe en la base de otro**.

## Caso insignia (Journey 5) — flujo del Sprint 1

```
Cliente ─► API Gateway (700 ms) ─► BFF ─► AUTH        valida/registra consentimiento (titular_id + vence_en 30d; síncrono, EC022/EC023)
                                      └► ACL ─► Open Finance / Open Data   (síncrono, timeout duro 700 ms / 300 ms con caché, Circuit Breaker;
                                      │                                      degrada al último valor en caché; invalidación en < 5 min al revocar)
                                      └► RISK ─► MongoDB primaria            escritor único; cifra `fuentes.datos` (AES-GCM + KMS); calcula score,
                                      │     │                                `nivelRiesgo` y `fechaVigencia`; guarda `ScoreCacheado` en Redis;
                                      │     └──► Pub/Sub `perfil-actualizado` (async) ──► RATING invalida caché del cliente
                                      └► RATING ◄─ MongoDB secundaria        replicación asíncrona (oplog); lectura causal (D-02); descifra `datos`
                                           └► Redis                          lee `ScoreCacheado` (Cache-Aside de scores)
         ◄──────────────────────────────────── oferta (completa o preliminar) con `perfilRiesgo`, síncrono
```

El tramo síncrono es solo lo que el usuario espera; todo efecto colateral viaja por eventos (`contracts/events/`).

**Quién orquesta (decidido 2026-10-08, ampliado 2026-10-18):** el BFF lee la lista de fuentes desde `contracts/fuentes.json`,
llama al ACL Worker por cada una de las 5 fuentes en paralelo y pasa los resultados tal cual a `POST /perfiles` de RISK (que
no llama al ACL Worker); RISK persiste el perfil cifrado en MongoDB, guarda el `ScoreCacheado` en Redis y devuelve `operation_time`,
`score`, `nivel_riesgo` y `fecha_vigencia`. Luego el BFF reenvía a RATING el `operation_time` que RISK devolvió. El BFF no calcula
nada de negocio: solo secuencia las llamadas, verifica que el `titular_id` del consentimiento coincida con el documento de la
solicitud y traduce la degradación al contrato (`ErrorOferta` con `motivo` o oferta preliminar). Al revocar un consentimiento, el
BFF llama también a `POST /fuentes/invalidar-cache` en el ACL Worker para limpiar la caché de las fuentes en segundos (CA-W01-08).

**Autenticación entre servicios y API Gateway (HA-LAT-004):** delante del BFF está GCP API Gateway (en local, Envoy en `:8080` con
`infra/gateway/envoy.yaml`) con timeout duro de 700 ms por ruta (`infra/gateway/openapi.yaml`). En Cloud Run el BFF es privado y
solo la cuenta `gateway-bff` tiene `roles/run.invoker` sobre él; el resto de servicios exige un token de identidad con
audience = nombre del servicio destino. Todos los que llaman a otro servicio lo envían cuando
`AUTENTICACION_SERVICIOS=id_token` (lo fija `.github/servicios.json` en staging; en local vale `ninguna`): el BFF
(`services/bff/app/autenticacion_servicios.py`, asíncrono), PAYMENTS → ACL Worker, el ACL Worker → stubs de proveedores y
`consolidador-fuentes` → ACL Worker y AUTH (copias síncronas del mismo módulo en cada servicio; KAN-176, 2026-10-17).

## Lo que enseñaron los experimentos (ya validado, no se re-discute)

- **Experimento 1 (Circuit Breaker + Retry en el ACL):** con el proveedor caído, las solicitudes que no dependen de él variaron < 1 % (límite: +10 %), 0 fallas, y el circuito cerró solo en ~6 s. El diseño en papel hacía que *una petición de usuario* probara si el proveedor volvió, pagando timeout + reintento (> 3 s). Se corrigió: **la sonda corre fuera del camino síncrono** (Consolidador).
- **Librería de Circuit Breaker:** `pybreaker` serializaba las llamadas (p95 de ~600 ms a ~3 s con 8 usuarios); se usa `purgatory`. Sus contadores no tienen locks: limitación conocida.
- **Experimento 2 (réplica de lectura de Riesgo):** staleness p95 ≈ 3,8 ms a la carga objetivo supuesta (200 perfiles/s), 0 pérdidas. Pero **leer de la secundaria justo después de escribir casi nunca ve lo propio** (≤ 0,35 % de las veces); con sesión causal sí. De ahí D-02.
- **Reloj:** Docker Desktop da saltos de ~2,1 s cada ~30 s en el reloj de pared; las mediciones de latencia usan `CLOCK_MONOTONIC`.
- Límites declarados: un solo host sin red real, proveedor simulado, carga supuesta, pocas repeticiones.

## Puertos del ACL Worker

Definidos en `services/acl-worker/app/domain/puertos.py` (propuesta del Sprint 1): `PuertoFuenteFinanciera`, `PuertoFuenteAbierta`, `PuertoPasarelaPagos`. KYC y firma electrónica se agregan en el Sprint 2.

`PuertoPasarelaPagos` tiene `cobrar` y `consultar` (KAN-28). `consultar` busca un cobro por la **referencia del
comercio** (el `id_intento_cobro` que Solventa envía), como consultan las pasarelas reales; la usa PAYMENTS para
**conciliar** antes de reintentar un cobro pendiente (si la pasarela ya cobró, se toma ese resultado sin cobrar de
nuevo). Flujo y decisiones en [`cobros-conciliacion.md`](cobros-conciliacion.md).

Un Circuit Breaker (`purgatory`) por adaptador/proveedor (`open-finance`, `open-data`, `pasarela`),
no por fuente individual. `purgatory` no expone un evento "el circuito lleva N segundos abierto" ni
un método para forzar el half-open: su único mecanismo de recuperación es perezoso (la próxima vez
que algo entra a `with breaker:` después de que vence el TTL, decide ahí mismo si reabre o cierra).
La sonda de recuperación (regla de arquitectura #5) explota exactamente esa pereza: un bucle
`asyncio` en segundo plano (lifespan de FastAPI) revisa cada pocos segundos qué breakers están
abiertos y, si encuentra uno, es ELLA quien entra primero al `with breaker:` — pagando el costo de
la llamada real al adaptador — antes de que llegue una petición de usuario. No es 100 % infalible
(una petición de usuario podría ganarle la carrera justo en el instante en que vence el TTL entre dos
ticks de la sonda); se mitiga con un intervalo de sonda sensiblemente menor al TTL del breaker. Ver
`services/acl-worker/app/application/sonda.py` y `circuitos.py`.

## Consolidador de fuentes (reconciliación diferida)

**Ampliación de alcance pedida directamente por el usuario**, fuera de `docs/sprint-1.md`
original (ver la nota en ese archivo) — no responde a ninguna HU/HA/CA del plan del sprint.
Diseño TRADUCIDO (no copiado) del Consolidador KYC del Experimento 1 (`solventa-arquitectura@11e4be6`,
Node.js/TypeScript + BullMQ) a Python + `rq` (Redis Queue), aplicado a Open Finance/Open Data —
el Consolidador de KYC (Truora) en sí sigue diferido al Sprint 2, igual que antes.

```
acl-worker (ServicioConsultaFuentes)        consolidador-fuentes (worker rq en hilo de fondo)
  circuito abierto o falla ──► encola job ──► cola `fuentes-reconciliacion` (Redis db 2)
  (fire-and-forget, dedup 30s)                        │
                                                        ▼
                                     POST /fuentes/consultar al ACL Worker (nunca al proveedor
                                     directo — regla de arquitectura #2)
                                        │
                              fresco ──► job resuelto (la caché ya quedó al día)
                              sigue degradado ──► excepción → `rq` reintenta (backoff 10/30/60/120/300s,
                                                   máx. 5 intentos, luego FailedJobRegistry)
```

- Productor en `acl-worker` (`app/application/reconciliacion.py`): `ProductorReconciliacion`
  deduplica por `(fuente, cliente)` con `SET NX EX 30` en Redis y encola con `rq`, sin bloquear
  nunca la respuesta ya resuelta al llamador (`de_cache`/`degradado`).
- Consumidor en `services/consolidador-fuentes`: FastAPI con `/health`/`/metrics` normales +
  `rq.SimpleWorker` corriendo en un hilo de fondo arrancado desde el lifespan (mismo patrón que
  `SondaRecuperacion`). Sin arquitectura hexagonal (regla de arquitectura #3): es andamiaje de
  reconciliación, no el límite anticorrupción con proveedores externos — ese límite ya lo tiene
  `acl-worker`.
- Detalle de implementación no evidente: `rq.SimpleWorker.work()` instala manejadores de señal de
  proceso con `signal.signal()`, que solo funciona en el hilo principal del intérprete — al correr
  en un hilo de fondo, el trabajador los desactiva (`_SimpleWorkerSinSenales`) y usa `max_idle_time`
  para que `work()` retorne sola y el apagado (`detener()`) sea acotado en el tiempo. Ver
  `services/consolidador-fuentes/app/worker.py`.

## Variables de entorno (propuesta)

| Servicio | Variables |
|---|---|
| `bff` | `AUTH_URL`, `ACL_URL`, `RISK_URL`, `RATING_URL`, `PAYMENTS_URL`, `CLIENTE_ID_CLAVE`, `FUENTES_CONFIG_PATH` |
| `auth` | `DATABASE_URL`, `CIFRADO_CLAVE`, `KMS_KEY_NAME`, `CIFRADO_CLAVE_ENVUELTA`, `CLIENTE_ID_CLAVE`, `CONSENTIMIENTO_VIGENCIA_DIAS` (30) |
| `payments` | `DATABASE_URL`, `ACL_URL` |
| `risk` | `MONGO_URI`, `REDIS_URL` (`ScoreCacheado`), `CIFRADO_CLAVE`, `KMS_KEY_NAME`, `CIFRADO_CLAVE_ENVUELTA`, `PUBSUB_TEMA_PERFIL_ACTUALIZADO`, `BATCH_TAMANO_LOTE` (500) |
| `rating` | `MONGO_URI`, `REDIS_URL` (Cache-Aside de scores), `RATING_MAX_TIME_MS` (150, D-02), `CIFRADO_CLAVE`, `KMS_KEY_NAME`, `CIFRADO_CLAVE_ENVUELTA` |
| `acl-worker` | `REDIS_URL`, `OPEN_FINANCE_URL`, `OPEN_DATA_URL`, `PASARELA_URL`, `TIMEOUT_MS` (700), `PRESUPUESTO_MS_OPEN_FINANCE`, `PRESUPUESTO_MS_OPEN_DATA`, `PRESUPUESTO_MS_PASARELA`, `CACHE_SERVIR_EN_TTL`, `BREAKER_UMBRAL_FALLOS` (3), `BREAKER_TTL_SEGUNDOS` (5), `SONDA_INTERVALO_SEGUNDOS` (2), `CACHE_TTL_SEGUNDOS` (300), `COLA_RECONCILIACION_REDIS_URL` (`redis://.../2`) |
| `consolidador-fuentes` | `REDIS_URL` (misma cola, db 2), `ACL_URL`, `AUTH_URL` |
| todos | `PORT` (lo inyecta Cloud Run) |

En staging, las variables sensibles (`DATABASE_URL`, `MONGO_URI`, `REDIS_URL`, `CIFRADO_CLAVE_ENVUELTA`) vienen de Secret Manager y las URL entre servicios las
fija Terraform desde `.github/servicios.json` (campo `llama`). `rating` usa un `MONGO_URI` distinto al de `risk`: de solo lectura.

## Cifrado en reposo (D-04, HA-SEG-002 / EC022)

- **Cifrado de campo en la aplicación (AES-GCM de 256 bits) con sobre de KMS:** AUTH cifra `usuarios.nombre_cifrado` y RISK cifra `fuentes[].datos` (`datos_cifrados`, dejando `datos: {}` en MongoDB) usando `CifradorAesGcm`. En staging, la DEK se guarda envuelta en Secret Manager (`CIFRADO_CLAVE_ENVUELTA`) y se desenvuelve una sola vez al arrancar el contenedor con la llave `campos-personales` de Cloud KMS (`KMS_KEY_NAME`). Solo las cuentas de servicio de `auth`, `risk` y `rating` tienen `roles/cloudkms.cryptoKeyEncrypterDecrypter`. En local y en CI se usa `CIFRADO_CLAVE` directo sin llamar a GCP.
- **CMEK de disco:** configurable en Terraform para Cloud SQL (`usar_cmek_sql`) y Memorystore Redis (`usar_cmek_redis`). MongoDB Atlas M0 no soporta CMEK en el tier gratuito, por lo que queda cubierto al 100 % por el cifrado de campo en RISK antes de escribir en MongoDB.

## Batch nocturno de reproceso (HA-ESC-004 / EC017)

- Cloud Run Job `risk-reproceso` (`python -m app.batch_reproceso` sobre la imagen de `services/risk`), disparado por Cloud Scheduler (`30 23 * * *` America/Bogota, detrás de `crear_batch`).
- Recorre en lotes (`BATCH_TAMANO_LOTE`, por defecto 500) los perfiles cuya `version_reglas` sea distinta de la `VERSION_REGLAS` actual, recalcula `score_total`, `nivel_riesgo` y `fecha_vigencia` y actualiza tanto MongoDB como la caché de Redis. Es idempotente: si se interrumpe y se relanza, retoma desde el primer lote pendiente sin reprocesar los documentos ya actualizados.

## Migraciones y despliegue

Cada servicio migra solo su almacén, con la misma imagen que el servicio: `auth` y `payments` con `alembic upgrade head`, `risk` con
`python -m migrations` (MongoDB, incluida `v0002_cifrar_datos_fuentes`). Orden único en local, CI y staging: **migrar → arrancar**. Detalle y principios en
[ADR-07](adr/ADR-07-cicd-y-migraciones.md).
