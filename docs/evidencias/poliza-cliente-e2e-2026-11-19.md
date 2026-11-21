# Póliza del CLIENTE de punta a punta (hipotecario y auto) y póliza en la asistencia — 2026-11-19

Verificación pedida por QA: «el cliente no generaba la solicitud de póliza; validar punta a punta hipotecario y
vehicular». Corrida **local** con `docker compose --profile servicios --profile semilla` (rama
`feat/KAN-36-poliza-en-asistencia`, base `develop` fabebba más el contrato v0.65; repetida igual el 2026-11-21 tras integrar `develop`). **No es staging.**

## Resultado

| Colección | Qué cubre del cliente | Peticiones | Aserciones | Fallas |
|---|---|---|---|---|
| `hu-w03` | Hipotecario (`VIDA_DEUDOR`) como CLIENTE: consentimiento, oferta, `aceptarOferta`, `emitirPropuesta` («Firmar»), `firmarPorEnlace`, `iniciarPago`, pago rechazado y aprobado, `confirmarPago` → póliza EMITIDA y «Mis pólizas» (carpeta 6, 6.39 a 6.58) | 201 | 380 | 0 |
| `hu-w08` | Auto como CLIENTE: cotización, `aceptarOferta`, `emitirPropuesta` → EN_FIRMA y, **nuevo**, firma, pago y `confirmarPago` → póliza de AUTO EMITIDA con su vehículo y en «Mis pólizas» (2.2a a 2.2f) | 39 | 87 | 0 (dos corridas seguidas) |
| `hu-m05` | Reporte de siniestro del cliente | 18 | 47 | 0 |
| `hu-m06` | Asistencia en sitio | 36 | 108 | 0 |
| `hu-w04` | Mesa de siniestros | 26 | 52 | 0 |

Conclusión: el cliente **sí** genera la solicitud y llega a la póliza emitida en los dos productos. No se encontró el
fallo reportado en el backend actual.

## Ajustes a las colecciones

- `hu-w08`: pasos 2.2a a 2.2f (firma con la sesión del cliente, pago en el stub de la pasarela, `confirmarPago` por
  `/v1/graphql/web-suscripcion` como la web). El 4.1 ahora elige la póliza de auto que **cubre la fecha del siniestro**
  (la de la semilla): la que emite 2.2e empieza hoy.
- `hu-m05`: el 0.3 elige la póliza `VIDA_DEUDOR` del cliente demo, porque su reporte no lleva tipo de siniestro de auto y
  `hu-w08` ahora deja pólizas de AUTO para el mismo cliente.

## Póliza en la asistencia (v0.65), en vivo

Con el cliente demo (`cliente.siniestros`) por el gateway (`/graphql/app`), `solicitarAsistencia` a una grúa:

| `polizaId` | Respuesta |
|---|---|
| la hipotecaria de la semilla | `ErrorAsistencia` `POLIZA_NO_VALIDA` |
| la de auto de la semilla | `Asistencia` con `polizaId` (NO_CONFIRMADA en el primer intento, como el stub) |
| sin póliza (app anterior) | `Asistencia` con `polizaId: null` |
| un id inventado | `ErrorAsistencia` `POLIZA_NO_VALIDA` |

## Pendiente

- Repetir en staging cuando haya despliegue (las colecciones manipulan stubs: solo local).
- La web del cliente se revisó por código (`useSuscripcion.ts`), no con Cypress contra el stack.
