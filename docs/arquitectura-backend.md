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
| `payments` | 8004 | Cobros con pasarela tokenizada, idempotencia, reintento | PostgreSQL `payments` | 1 |
| `acl-worker` | 8005 | Único contacto con proveedores externos; hexagonal; Circuit Breaker (700 ms) | Redis (estado del circuito y caché) | 1 |
| `stubs/open-finance` | 8101 | Proveedor simulado (2 fuentes) | — | 1 |
| `stubs/open-data` | 8102 | Proveedor simulado (3 fuentes) | — | 1 |
| `stubs/pasarela` | 8103 | Pasarela de pago simulada | — | 1 |
| `consolidador-fuentes` | 8006 | Reconciliación diferida de Open Finance/Open Data degradadas (`rq` sobre Redis) | Redis (cola, db 2) | 1 (ampliación de alcance) |
| `under`, `policy`, `claims`, `consolidador-kyc` | — | Suscripción, pólizas, siniestros y Consolidador de KYC (Truora) | — | 2–3 |

Cada servicio tiene su propia base: **nadie lee ni escribe en la base de otro**.

## Caso insignia (Journey 5) — flujo del Sprint 1

```
Cliente ─► BFF ─► AUTH        valida/registra consentimiento (síncrono, EC022/EC023)
                  └► ACL ─► Open Finance / Open Data   (síncrono, timeout duro 700 ms, Circuit Breaker;
                  │                                      degrada al último valor en caché)
                  └► RISK ─► MongoDB primaria            escritor único; emite perfil.actualizado (async)
                  └► RATING ◄─ MongoDB secundaria        replicación asíncrona (oplog); lectura causal (D-02)
                       └► Redis                          Cache-Aside de scores
         ◄──────────────── oferta (completa o preliminar), síncrono
```

El tramo síncrono es solo lo que el usuario espera; todo efecto colateral viaja por eventos (`contracts/events/`).

## Lo que enseñaron los experimentos (ya validado, no se re-discute)

- **Experimento 1 (Circuit Breaker + Retry en el ACL):** con el proveedor caído, las solicitudes que no dependen de él variaron < 1 % (límite: +10 %), 0 fallas, y el circuito cerró solo en ~6 s. El diseño en papel hacía que *una petición de usuario* probara si el proveedor volvió, pagando timeout + reintento (> 3 s). Se corrigió: **la sonda corre fuera del camino síncrono** (Consolidador).
- **Librería de Circuit Breaker:** `pybreaker` serializaba las llamadas (p95 de ~600 ms a ~3 s con 8 usuarios); se usa `purgatory`. Sus contadores no tienen locks: limitación conocida.
- **Experimento 2 (réplica de lectura de Riesgo):** staleness p95 ≈ 3,8 ms a la carga objetivo supuesta (200 perfiles/s), 0 pérdidas. Pero **leer de la secundaria justo después de escribir casi nunca ve lo propio** (≤ 0,35 % de las veces); con sesión causal sí. De ahí D-02.
- **Reloj:** Docker Desktop da saltos de ~2,1 s cada ~30 s en el reloj de pared; las mediciones de latencia usan `CLOCK_MONOTONIC`.
- Límites declarados: un solo host sin red real, proveedor simulado, carga supuesta, pocas repeticiones.

## Puertos del ACL Worker

Definidos en `services/acl-worker/app/domain/puertos.py` (propuesta del Sprint 1): `PuertoFuenteFinanciera`, `PuertoFuenteAbierta`, `PuertoPasarelaPagos`. KYC y firma electrónica se agregan en el Sprint 2.

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
| `bff` | `AUTH_URL`, `RISK_URL`, `RATING_URL`, `PAYMENTS_URL` |
| `auth`, `payments` | `DATABASE_URL` |
| `risk` | `MONGO_URI`, `ACL_URL` (no se usa todavía: RISK recibe los datos de las fuentes ya resueltos en el cuerpo de `POST /perfiles`, no llama al ACL Worker directamente) |
| `rating` | `MONGO_URI`, `REDIS_URL` (Cache-Aside de scores, Sprint 2), `RATING_MAX_TIME_MS` (150, D-02: cota de espera en la secundaria antes de caer a la primaria) |
| `acl-worker` | `REDIS_URL`, `OPEN_FINANCE_URL`, `OPEN_DATA_URL`, `PASARELA_URL`, `TIMEOUT_MS` (700), `BREAKER_UMBRAL_FALLOS` (3), `BREAKER_TTL_SEGUNDOS` (5), `SONDA_INTERVALO_SEGUNDOS` (2), `CACHE_TTL_SEGUNDOS` (300), `COLA_RECONCILIACION_REDIS_URL` (`redis://.../2`) |
| `consolidador-fuentes` | `REDIS_URL` (misma cola, db 2), `ACL_URL` |
| todos | `PORT` (lo inyecta Cloud Run) |

En staging, las variables sensibles (`DATABASE_URL`, `MONGO_URI`, `REDIS_URL`) vienen de Secret Manager y las URL entre servicios las
fija Terraform desde `.github/servicios.json` (campo `llama`). `rating` usa un `MONGO_URI` distinto al de `risk`: de solo lectura.

## Migraciones y despliegue

Cada servicio migra solo su almacén, con la misma imagen que el servicio: `auth` y `payments` con `alembic upgrade head`, `risk` con
`python -m migrations` (MongoDB). Orden único en local, CI y staging: **migrar → arrancar**. Detalle y principios en
[ADR-07](adr/ADR-07-cicd-y-migraciones.md).
