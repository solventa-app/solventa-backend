# policy

POLICY: propuestas y pólizas (HU-W03, KAN-26). Servicio nuevo del Sprint 2.

## Responsabilidad

Único escritor de las tablas de suscripción (regla de arquitectura #1). Hoy, el **bloque 1** del plan
([`docs/plan-completar-hu-w03.md`](../../docs/plan-completar-hu-w03.md)): aceptar una oferta COMPLETA y
guardarla como propuesta. La firma (bloque 2), la emisión (bloque 4) y el PDF (bloque 5) llegan después.

- `POST /propuestas` `{oferta_id, actor_usuario_id?}`: lee la oferta de RATING (`GET /ofertas/{id}`) y crea la
  propuesta en estado `ACEPTADA`. **El precio sale de RATING, nunca del cliente.** Idempotente por `oferta_id`: aceptar
  dos veces (incluso en paralelo) devuelve la misma propuesta, aunque RATING ya haya dejado vencer la oferta.
  - `404 OFERTA_NO_ENCONTRADA`: la oferta venció o nunca existió.
  - `422 OFERTA_PRELIMINAR` / `OFERTA_VENCIDA`: no se puede aceptar.
  - `503 RATING_NO_DISPONIBLE`: RATING no responde; el BFF lo convierte en un error con reintento (nunca un 5xx al cliente).
- `GET /propuestas/{id}`: la propuesta, o 404.

## Diseño

- **Capas simples** (sin puertos y adaptadores): hexagonal es solo para `acl-worker` (regla #3). POLICY no llama a
  proveedores externos; la firma, el KYC y las notificaciones pasarán por el ACL Worker (regla #2).
- **Idempotencia en la base:** `oferta_id` es `UNIQUE` y el alta es `INSERT ... ON CONFLICT (oferta_id) DO NOTHING
  RETURNING` con el pool en `autocommit`, igual que PAYMENTS. Verificado en vivo con 8 aceptaciones simultáneas: una sola
  fila.
- **Cifrado en reposo (HA-SEG-002):** la prima, la cobertura y el plazo se guardan cifrados por campo (AES-GCM, el id de la
  propuesta como dato asociado). El `cliente_id` es el identificador opaco (HMAC) que calcula el BFF, nunca un documento.
- **Sin datos personales en logs** (regla #6): solo `id_opaco(...)` del cliente y de la oferta.

## Esquema

`propuestas` (migración `0001_propuestas.py`): `id`, `oferta_id` (UNIQUE), `cliente_id`, `consentimiento_id`,
`actor_usuario_id`, `estado` (CHECK con los 7 estados del contrato), `oferta_cifrada`, `creado_en`, `actualizado_en`.
Las tablas de póliza y de evidencia de firma llegan con sus bloques, cada una en su migración.

## Configuración

| Variable | Default | Para qué |
|---|---|---|
| `DATABASE_URL` | — (obligatoria) | PostgreSQL `policy` |
| `RATING_URL` | `http://localhost:8003` | Dónde leer la oferta |
| `POLICY_RATING_TIMEOUT_SEGUNDOS` | `3.0` | Espera máxima a RATING |
| `CIFRADO_CLAVE` | llave local pública | 32 bytes en base64; en Cloud Run llega de Secret Manager (`clave-cifrado`) y no puede ser la local |
| `AUTENTICACION_SERVICIOS` | `ninguna` | `id_token` en Cloud Run (RATING es privado) |

## Migraciones y pruebas

```bash
bash scripts/verificar-migraciones.sh policy   # upgrade, downgrade a base y upgrade, contra Postgres real
cd services/policy && python -m pytest
```

Código copiado de PAYMENTS (sin código compartido entre servicios): `observabilidad.py`, `autenticacion_servicios.py`,
`cifrado.py` (con la diferencia de que un valor en claro no se acepta como cifrado, porque aquí no hay filas anteriores) y
el esqueleto de Alembic.
