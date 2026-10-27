# stub-kyc

Stub del proveedor de KYC (verificación de identidad), HU-W03, decisión W03-10.

## Responsabilidad

Stub **por contrato** de un proveedor externo, igual que `stub-pasarela` y `stub-firma`: expone un endpoint de control para
cambiar de modo en caliente (`sano`, `lento`, `error`, `caido`, `rechaza`) y **recuerda las verificaciones en memoria** (se
reinicia limpio con el contenedor).

**El contrato es inventado**, no el de Truora ni el de otro proveedor real. Un KYC real pide el documento del cliente y una
validación (p. ej. biométrica); aquí solo llega el identificador opaco del cliente (regla de arquitectura #6) y el resultado
es determinista: `aprobada`, salvo en modo `rechaza`. El consolidador de KYC real queda para el Sprint 3.

## Contrato

- `GET /health` → siempre `{"estado": "ok"}`.
- `GET /control/modo` / `POST /control/modo {"modo": "..."}` → fija el modo global.
- `POST /v1/verificaciones` `{referencia_comercio, cliente_id}` →
  `{"estado": "aprobada" | "rechazada", "referencia", "motivo", "verificada_en"}`. **Idempotente por `referencia_comercio`**
  (en Solventa, el `id_verificacion`, que es el id de la propuesta).
- `GET /v1/verificaciones/{referencia_comercio}` → lo registrado, o `404`. Para verificar en vivo.
- `GET /v1/estado` → **sí obedece el modo simulado**: lo usa la sonda de recuperación del circuito, que no debe verificar.
- Override de una sola petición: `?modo=...` o el header `X-Modo-Simulado`.

## Rutas con la forma de Didit (HU-M01)

- `POST /v3/id-verification/`, `/v3/passive-liveness/`, `/v3/face-match/` (multipart) y `GET /` para la sonda. Piden `x-api-key`
  (sin ella `403`); falta una imagen → `400`. Obedecen los mismos modos.
- Marcadores dentro de los bytes de la imagen: `CASO=ilegible|vencido|menor|suplantacion|rostro-distinto` y `NUMERO=<n>`. Los datos por
  omisión son ficticios (`COL`, número `1000000001`).

## Modos de falla

| Modo | Efecto | ¿Registra la verificación? |
|---|---|---|
| `sano` | Responde en 20–120 ms con `aprobada` | Sí |
| `rechaza` | Responde con `rechazada` (`identidad_no_verificada`) | Sí |
| `lento` | Responde en 750–1100 ms (supera los 700 ms del ACL Worker) | **Sí, antes de la espera** |
| `error` | `503` inmediato | No |
| `caido` | No responde (60 s) | No |

## Correr

```bash
docker compose up stub-kyc   # 8105
cd stubs/kyc && python -m pytest
```
