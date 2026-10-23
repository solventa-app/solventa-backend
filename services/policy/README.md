# policy

POLICY: propuestas y pólizas (HU-W03, KAN-26). Servicio nuevo del Sprint 2.

## Responsabilidad

Único escritor de las tablas de suscripción (regla de arquitectura #1). Hoy, los **bloques 1 y 2** del plan
([`docs/plan-completar-hu-w03.md`](../../docs/plan-completar-hu-w03.md)): aceptar una oferta COMPLETA y guardarla como
propuesta, y firmarla con evidencia de no repudio. La emisión (bloque 4) y el PDF (bloque 5) llegan después.

- `POST /propuestas` `{oferta_id, actor_usuario_id?}`: lee la oferta de RATING (`GET /ofertas/{id}`) y crea la
  propuesta en estado `ACEPTADA`. **El precio sale de RATING, nunca del cliente.** Idempotente por `oferta_id`: aceptar
  dos veces (incluso en paralelo) devuelve la misma propuesta, aunque RATING ya haya dejado vencer la oferta.
  - `404 OFERTA_NO_ENCONTRADA`: la oferta venció o nunca existió.
  - `422 OFERTA_PRELIMINAR` / `OFERTA_VENCIDA`: no se puede aceptar.
  - `503 RATING_NO_DISPONIBLE`: RATING no responde; el BFF lo convierte en un error con reintento (nunca un 5xx al cliente).
- `GET /propuestas/{id}`: la propuesta, o 404.
- `GET /propuestas/{id}/documento-firma`: lo que el cliente lee antes de firmar (CA-W03-02): resumen, `monto_mensual`,
  `meses`, `forma_cobro` y `version`. Se arma siempre desde la propuesta guardada.
- `POST /propuestas/{id}/firma` `{autoriza_cobro, version_documento, firmante_id}` (CA-W03-03): firma con el proveedor **vía
  el ACL Worker** (solo el hash del documento) y guarda la evidencia; la propuesta pasa a `EN_EMISION`. Idempotente: firmar
  dos veces devuelve la misma evidencia. `422 COBRO_NO_AUTORIZADO`; `409 VERSION_DOCUMENTO_DESACTUALIZADA` /
  `PROPUESTA_NO_FIRMABLE`; `503 SERVICIO_FIRMA_NO_DISPONIBLE` (el proveedor no responde: reintentar; no queda evidencia).
  Hasta el bloque 4 nada saca a la propuesta de `EN_EMISION`.

## Diseño

- **Capas simples** (sin puertos y adaptadores): hexagonal es solo para `acl-worker` (regla #3). POLICY no llama a
  proveedores externos; la firma, el KYC y las notificaciones pasarán por el ACL Worker (regla #2).
- **Idempotencia en la base:** `oferta_id` es `UNIQUE` y el alta es `INSERT ... ON CONFLICT (oferta_id) DO NOTHING
  RETURNING` con el pool en `autocommit`, igual que PAYMENTS. Verificado en vivo con 8 aceptaciones simultáneas: una sola
  fila.
- **Cifrado en reposo (HA-SEG-002):** la prima, la cobertura y el plazo se guardan cifrados por campo (AES-GCM, el id de la
  propuesta como dato asociado). El `cliente_id` es el identificador opaco (HMAC) que calcula el BFF, nunca un documento.
- **Sin datos personales en logs** (regla #6): solo `id_opaco(...)` del cliente, la oferta, la propuesta y el firmante.
- **Verificado en vivo** (Postgres real + ACL Worker real + stub de firma): con el proveedor `lento` el ACL corta a ~719 ms,
  POLICY responde 503 reintentable y el reintento devuelve la misma referencia que el proveedor ya tenía; con el proveedor
  caído el circuito abre al 3.er intento y las siguientes llamadas fallan en ~10 ms; la sonda lo cierra sola.

## Esquema

`propuestas` (migración `0001_propuestas.py`): `id`, `oferta_id` (UNIQUE), `cliente_id`, `consentimiento_id`,
`actor_usuario_id`, `estado` (CHECK con los 7 estados del contrato), `oferta_cifrada`, `creado_en`, `actualizado_en`.
`evidencias_firma` (migración `0002_evidencias_firma.py`): `propuesta_id` (UNIQUE, FK), `documento_hash`,
`version_documento`, `documento_cifrado` (el documento exacto que se hasheó, cifrado), `firmante_id` (id opaco),
`referencia_proveedor`, `firmada_en`. **Append-only en la base**: triggers rechazan `UPDATE`, `DELETE` y `TRUNCATE`. Con
`documento_cifrado` se recalcula el hash y se prueba qué se firmó. La tabla de pólizas llega con el bloque 4.

## Configuración

| Variable | Default | Para qué |
|---|---|---|
| `DATABASE_URL` | — (obligatoria) | PostgreSQL `policy` |
| `RATING_URL` | `http://localhost:8003` | Dónde leer la oferta |
| `POLICY_RATING_TIMEOUT_SEGUNDOS` | `3.0` | Espera máxima a RATING |
| `CIFRADO_CLAVE` | llave local pública | 32 bytes en base64; en Cloud Run llega de Secret Manager (`clave-cifrado`) y no puede ser la local |
| `ACL_URL` | `http://localhost:8005` | ACL Worker (la firma pasa por él, nunca directo al proveedor) |
| `POLICY_ACL_TIMEOUT_SEGUNDOS` | `3.0` | Espera máxima al ACL (el ACL corta a 700 ms) |
| `AUTENTICACION_SERVICIOS` | `ninguna` | `id_token` en Cloud Run (RATING y el ACL son privados) |

## Migraciones y pruebas

```bash
bash scripts/verificar-migraciones.sh policy   # upgrade, downgrade a base y upgrade, contra Postgres real
cd services/policy && python -m pytest
```

Código copiado de PAYMENTS (sin código compartido entre servicios): `observabilidad.py`, `autenticacion_servicios.py`,
`cifrado.py` (con la diferencia de que un valor en claro no se acepta como cifrado, porque aquí no hay filas anteriores) y
el esqueleto de Alembic.
