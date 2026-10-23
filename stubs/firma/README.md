# stub-firma

Stub del proveedor de firma electrónica (HU-W03, CA-W03-03).

## Responsabilidad

Stub **por contrato** de un proveedor externo, igual que `stub-pasarela`: imita el contrato real y expone un endpoint de
control para cambiar de modo en caliente (`sano`, `lento`, `error`, `caido`). Es andamiaje de prueba, sin hexagonal ni
base de datos; **recuerda las firmas en memoria** (se reinicia limpio con el contenedor) para comportarse como un
proveedor real. Solo recibe el **hash** del documento, su versión y el identificador opaco del firmante: nunca el
contenido ni datos personales (regla de arquitectura #6).

## Contrato

- `GET /health` → siempre `{"estado": "ok"}`.
- `GET /control/modo` / `POST /control/modo {"modo": "..."}` → fija el modo global.
- `POST /v1/firmas` `{referencia_comercio, documento_hash, version_documento, firmante_id}` →
  `{"estado": "firmada", "referencia", "firmada_en", "motivo"}`. **Idempotente por `referencia_comercio`** (en Solventa,
  el `id_firma`, que es el id de la propuesta): repetirla devuelve la evidencia original sin firmar de nuevo.
- `GET /v1/firmas/{referencia_comercio}` → lo registrado, o `404`. Para verificar en vivo.
- `GET /v1/estado` → **sí obedece el modo simulado**: lo usa la sonda de recuperación del circuito, que no debe firmar.
- Segundo proveedor (EC031, CA-W03-10): `POST /alterna/v1/signatures` y `GET /alterna/v1/status`, con otros nombres de
  campos (`signature_request_id`, `document_digest`, `signer`) y estado `SIGNED`; comparte la misma memoria.
- Override de una sola petición: `?modo=...` o el header `X-Modo-Simulado`.

## Modos de falla

| Modo | Efecto | ¿Registra la firma? |
|---|---|---|
| `sano` | Responde en 20–120 ms con `firmada` | Sí |
| `lento` | Responde en 750–1100 ms (supera los 700 ms del ACL Worker) | **Sí, antes de la espera**: si el ACL corta, la firma igual quedó hecha |
| `error` | `503` inmediato | No |
| `caido` | No responde (60 s) | No |

## Correr

```bash
docker compose up stub-firma   # 8104
cd stubs/firma && python -m pytest
```
