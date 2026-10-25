# Evidencia CA-W03-06 — Un pago rechazado que se recupera reabre la póliza (HU-W03)

- **Fecha:** 2026-10-24
- **Historia:** HU-W03 (KAN-26), plan de HU-W03 bloque 7, fila 5; decisión W03-14
- **Criterio:** CA-W03-06 — con un cobro rechazado o pendiente, el cliente sabe qué pasa con su póliza y qué hacer. Decisión W03-14: **solo el
  back-office y los procesos de reconciliación reabren una póliza; el cliente no.**

> **Corrida LOCAL (docker compose en un portátil), no es una medición de staging.**

## 1. El problema

`PAGO_RECHAZADO` era un estado final: la emisión normal solo avanza desde `EN_EMISION` y `PENDIENTE_PAGO`, y el SQL de emisión lo impedía a propósito.
Pero un cobro puede recuperarse después: el back-office reintenta uno rechazado (`reintentarCobro`, hasta 3 veces), o la pasarela cobra uno pendiente cuando POLICY
ya agotó sus 12 consultas. PAYMENTS lo cobraba, y **la póliza se quedaba sin emitir para siempre**: nadie se lo decía a POLICY.

## 2. El cambio

- **POLICY, `reconciliar_cobro(propuesta_id)`:** reenvía la misma orden de cobro. PAYMENTS contesta con el cobro que ya existe, sea cual sea su estado, **sin llamar a
  la pasarela ni cobrar de nuevo** (`crear_cobro` idempotente), así que sirve como consulta. Si está `cobrado`, emite la póliza; si sigue rechazado, no cambia nada; si se
  está reintentando, vuelve a `PENDIENTE_PAGO` con otra tanda de consultas. Solo reabre desde `PAGO_RECHAZADO` y `PENDIENTE_PAGO`, y nunca sin la firma.
- **Dos disparadores, ninguno del cliente:**
  1. **Back-office:** el BFF le avisa a POLICY (`POST /propuestas/{id}/_reconciliar-cobro`) justo después de `reintentarCobro`. Es de mejor esfuerzo: si POLICY no
     responde, el reintento del back-office no falla.
  2. **Proceso de reconciliación:** el barrido `POST /reconciliacion/cobros` revisa un lote (`RECONCILIACION_LOTE_MAX`, 100) de propuestas de los últimos
     `RECONCILIACION_DIAS` (30), de las más antiguas a las más nuevas, y devuelve un resumen. Una propuesta que falle no detiene a las demás.
- **Disparador en la nube:** un trabajo de Cloud Scheduler `reconciliacion-cobros-policy` llama al barrido cada `reconciliacion_cobros_cron` (15 min por defecto), con la
  misma cuenta que la sonda del ACL, que solo gana permiso para invocar a POLICY (`infra/tareas.tf`). Solo existe con `crear_tareas = true`.
- Los endpoints de POLICY son privados y el BFF no expone ninguna mutación del cliente para esto.
- Detalles que importan: una propuesta que sigue rechazada **no se vuelve a escribir** en cada pasada (si no, nunca saldría de la ventana de días); si falla el aviso
  después de emitir, la póliza no se deshace y se deja una tarea que reintente solo el aviso.
- Sin cambio de campos en el contrato (v0.14 documenta la semántica: `PAGO_RECHAZADO` ya no es final).

## 3. Resultado

**Pruebas:** POLICY 170 (23 nuevas) y BFF 241 (8 nuevas) pasan, en la imagen de cada servicio. `ruff` limpio en lo tocado.

**En vivo, con el stack real y por el gateway** (guion de verificación; el escenario del back-office quedó además en la colección de Newman, 4.60 a 4.72):

| Caso | Resultado |
|---|---|
| Pasarela rechaza | La propuesta queda `PAGO_RECHAZADO`, sin póliza |
| Un **CLIENTE** intenta `reintentarCobro` | Error de rol; la propuesta sigue `PAGO_RECHAZADO` |
| El **BACKOFFICE** reintenta con la pasarela ya sana | Cobro `COBRADO` y la propuesta pasa a `EMITIDA` con póliza; **1 solo aviso** al cliente |
| El back-office reintenta otra vez | No duplica la póliza ni el aviso |
| PAYMENTS cobra el reintento **sin pasar por el BFF** | POLICY no se entera: sigue `PAGO_RECHAZADO` |
| `POST /reconciliacion/cobros` | Resumen `revisadas 6, emitidas 1, rechazadas 5, pendientes 0, fallidas 0`; la propuesta queda `EMITIDA` con 1 aviso. Las otras 5 eran rechazos viejos de pruebas anteriores, cuyo cobro sigue rechazado: **no se emitieron** |
| Segundo barrido | `emitidas 0`: no emite nada más |
| Reconciliar con el cobro aún rechazado | `PAGO_RECHAZADO`, sin póliza |
| Reconciliar un id que no es una propuesta | `404` (el BFF avisa por cada cobro que reintenta el back-office, también los que no son de W03) |
| Colección Newman completa | 111 peticiones ejecutadas, 204 aserciones, 0 fallos, 0 respuestas 5xx |

## 4. Límites

- **El job de Scheduler no está aplicado ni probado en GCP.** `terraform fmt` y `terraform validate` pasan (sobre una copia, porque la caché local de proveedores del repo está
  incompleta); `terraform plan` no se corrió porque necesita credenciales, y nada se aplicó: el job se crea cuando el pipeline aplica la infraestructura desde `main` (ADR-09).
  Hasta entonces, lo automático es el aviso del back-office. Los 15 minutos son un supuesto.
- **Cuenta de Scheduler:** con este hay tres trabajos (`sonda-acl`, `risk-reproceso-nocturno` y este), que es justo el tope de la capa gratuita que documenta `crear_tareas`
  (3 por cuenta de facturación; no se verificó contra la factura). Si la cuenta de facturación tiene trabajos de otros proyectos, el cuarto cobra centavos al mes.
- **Un cobro rechazado que agotó sus 3 reintentos** («requiere otro medio de pago») no puede recuperarse por aquí: el cliente no tiene cómo cambiar el medio de pago (el
  `tokenMedioPago` de W03 es el del convenio de recaudo, fijo por configuración). Es una decisión de producto abierta del plan.
- **El cliente no ve el motivo ni qué hacer** al estar en `PAGO_RECHAZADO`: es la fila 4 del bloque 7, que espera los textos de Figma. Esta fila resuelve que la póliza se pueda emitir; no lo que
  se le muestra al cliente mientras tanto.
- **El front no debe tratar `PAGO_RECHAZADO` como final.** Un cliente que consulta otra vez puede encontrar `EMITIDA`.
- Una sola corrida local, una muestra por caso, sin carga. El barrido recorre las propuestas en serie: con miles de propuestas rechazadas por revisar, `RECONCILIACION_LOTE_MAX` y la
  frecuencia del Scheduler habrá que ajustarlos con datos de staging.
