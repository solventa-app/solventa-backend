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
| `under`, `policy`, `claims`, `consolidador` | — | Suscripción, pólizas, siniestros y sonda de recuperación del circuito | — | 2–3 |

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

## Variables de entorno (propuesta)

| Servicio | Variables |
|---|---|
| `bff` | `AUTH_URL`, `RISK_URL`, `RATING_URL`, `PAYMENTS_URL` |
| `auth`, `payments` | `DATABASE_URL` |
| `risk` | `MONGO_URI`, `MONGO_DB` (por defecto `risk`), `ACL_URL` |
| `rating` | `MONGO_URI`, `MONGO_DB`, `REDIS_URL` |
| `acl-worker` | `REDIS_URL`, `OPEN_FINANCE_URL`, `OPEN_DATA_URL`, `PASARELA_URL`, `TIMEOUT_MS` (700) |
| todos | `PORT` (lo inyecta Cloud Run) |

En staging, las variables sensibles (`DATABASE_URL`, `MONGO_URI`, `REDIS_URL`) vienen de Secret Manager y las URL entre servicios las
fija Terraform desde `.github/servicios.json` (campo `llama`). `rating` usa un `MONGO_URI` distinto al de `risk`: de solo lectura.

## Migraciones y despliegue

Cada servicio migra solo su almacén, con la misma imagen que el servicio: `auth` y `payments` con `alembic upgrade head`, `risk` con
`python -m migrations` (MongoDB). Orden único en local, CI y staging: **migrar → arrancar**. Detalle y principios en
[ADR-07](adr/ADR-07-cicd-y-migraciones.md).
