# stub-notificaciones

Stub del proveedor de notificaciones (HU-W03, bloque 5, CA-W03-08): el aviso al cliente de que su póliza fue emitida.

## Responsabilidad

Stub **por contrato** de un proveedor externo, igual que `stub-pasarela`, `stub-firma` y `stub-kyc`: expone un endpoint de
control para cambiar de modo en caliente (`sano`, `lento`, `error`, `caido`) y **recuerda los avisos en memoria** (se reinicia
limpio con el contenedor). **No manda nada de verdad**: guarda lo que "envió" y lo expone en `GET /control/enviados`, para
comprobar que se avisó **una sola vez**.

**El contrato es inventado**, no el de un proveedor real. Uno real resolvería el contacto (correo o teléfono) del
`destinatario_id` con su propia base; aquí no hay contacto. Solo llegan la plantilla, el identificador opaco del usuario y los
datos de la plantilla (número de póliza y ruta del documento): nunca el contenido del documento ni datos personales (regla de
arquitectura #6).

## Contrato

- `GET /health` → siempre `{"estado": "ok"}`.
- `GET /control/modo` / `POST /control/modo {"modo": "..."}` → fija el modo global.
- `GET /control/enviados` → los avisos registrados.
- `POST /v1/notificaciones` `{referencia_comercio, plantilla, destinatario_id, datos}` →
  `{"estado": "enviada", "referencia", "motivo"}`. **Idempotente por `referencia_comercio`** (en Solventa, el
  `id_notificacion`, `poliza-emitida-{id de la póliza}`): repetirla no manda otro aviso.
- `GET /v1/estado` → **sí obedece el modo simulado**: lo usa la sonda de recuperación del circuito, que no debe enviar.
- Override de una sola petición: `?modo=...` o el header `X-Modo-Simulado`.

## Modos de falla

| Modo | Efecto | ¿Registra el aviso? |
|---|---|---|
| `sano` | Responde en 20–120 ms con `enviada` | Sí |
| `lento` | Responde en 750–1100 ms (supera los 700 ms del ACL Worker) | **Sí, antes de la espera** |
| `error` | `503` inmediato | No |
| `caido` | No responde (60 s) | No |

## Correr

```bash
docker compose up stub-notificaciones   # 8106
cd stubs/notificaciones && python -m pytest
```
