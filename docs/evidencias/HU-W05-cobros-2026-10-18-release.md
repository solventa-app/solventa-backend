# HU-W05 (KAN-28): cobros y conciliación de punta a punta, tras el release #182 — 2026-10-18

Prueba con `scripts/probar_cobros.py` (descripción y escenarios en [cobros-conciliacion.md](../cobros-conciliacion.md)),
sobre el despliegue completo del release #182 (los 10 servicios en el mismo commit) con Cloud Tasks, Cloud Scheduler y las
instancias fijas. La pasarela es el simulador `stubs/pasarela` en cada modo.

## Resultado

| Entorno | Resultado |
|---|---|
| **Staging** (`solventa-511815`) | **31 de 31** en verde |
| Local (docker compose) | **31 de 31** en verde |

## Lo nuevo frente a la corrida anterior (docs/evidencias/HU-W05-cobros-2026-10-18.md)

- **Escenario 6, reintento automático:** un cobro `PENDIENTE` (pasarela caída) pasa **solo** a `COBRADO` al volver la pasarela, sin
  llamar a `reintentarCobro`: **7 s** en staging (Cloud Tasks) y **6 s** en local (el bucle de PAYMENTS).
- **Confirmado en los logs de PAYMENTS:** el `POST /cobros/_reintentar` que resuelve esos cobros lo hace el agente de usuario
  `Google-Cloud-Tasks` y responde 200; la sonda del ACL la dispara `Google-Cloud-Scheduler` cada minuto, también con 200.
- La comprobación «la pasarela no tiene el cobro (404)» pasó a hacerse con `?modo=sano` mientras la caída sigue: con la pasarela de
  vuelta, el reintento automático cobra el `PENDIENTE` a los pocos segundos y la versión anterior (que miraba después) fallaba por
  esa carrera, no por un defecto del sistema.

## Detalle de la corrida en staging

El cliente es `cliente.demo` y el back-office `operador.demo`. Los cobros de la prueba quedan en la base de staging (datos ficticios).
La pasarela quedó en `sano`.

| Escenario | Comprobación | Resultado |
|---|---|---|
| 1. Cobro con la pasarela sana | el cobro queda COBRADO | OK |
| 1. Cobro con la pasarela sana | trae la referencia de la pasarela (pg-…) | OK |
| 1. Cobro con la pasarela sana | el back-office ve el nombre del cliente | OK |
| 1. Cobro con la pasarela sana | PAYMENTS guardó el mismo cobro con la misma referencia | OK |
| 1. Cobro con la pasarela sana | la pasarela tiene ese cobro con esa misma referencia | OK |
| 2. Idempotencia: la misma orden no cobra dos veces | reenviar la misma orden devuelve el mismo cobro | OK |
| 2. Idempotencia: la misma orden no cobra dos veces | y el mismo estado y la misma referencia | OK |
| 2. Idempotencia: la misma orden no cobra dos veces | la misma clave con otro monto da ErrorCobro (conflicto) | OK |
| 2. Idempotencia: la misma orden no cobra dos veces | la pasarela conserva la referencia original (sin segundo cobro) | OK |
| 3. Pasarela lenta: queda PENDIENTE y el reintento concilia sin cobrar de nuevo | con la pasarela lenta (> 700 ms) el cobro queda PENDIENTE (nunca un 5xx) | OK |
| 3. Pasarela lenta: queda PENDIENTE y el reintento concilia sin cobrar de nuevo | pero la pasarela SÍ cobró (el riesgo de doble cobro) | OK |
| 3. Pasarela lenta: queda PENDIENTE y el reintento concilia sin cobrar de nuevo | reintentarCobro lo deja COBRADO con la referencia que ya tenía la pasarela | OK |
| 3. Pasarela lenta: queda PENDIENTE y el reintento concilia sin cobrar de nuevo | un segundo reintento (doble clic) devuelve lo mismo, sin tocar nada | OK |
| 3. Pasarela lenta: queda PENDIENTE y el reintento concilia sin cobrar de nuevo | la pasarela sigue con UNA sola referencia | OK |
| 4. Pasarela caída (503): PENDIENTE sin cobro, y se cobra una sola vez al volver | el cobro queda PENDIENTE con un motivo, no un error | OK |
| 4. Pasarela caída (503): PENDIENTE sin cobro, y se cobra una sola vez al volver | la pasarela NO tiene el cobro (404): no se cobró nada | OK |
| 4. Pasarela caída (503): PENDIENTE sin cobro, y se cobra una sola vez al volver | al volver la pasarela, el reintento lo deja COBRADO | OK |
| 4. Pasarela caída (503): PENDIENTE sin cobro, y se cobra una sola vez al volver | con una sola referencia en la pasarela | OK |
| 5. Pasarela rechaza: reintentos manuales (máximo 3) y otro medio de pago | el cobro queda RECHAZADO por fondos_insuficientes con 3 reintentos | OK |
| 5. Pasarela rechaza: reintentos manuales (máximo 3) y otro medio de pago | si la pasarela sigue rechazando, quedan 2, 1 y 0 reintentos | OK |
| 5. Pasarela rechaza: reintentos manuales (máximo 3) y otro medio de pago | sin reintentos, pide otro medio de pago | OK |
| 5. Pasarela rechaza: reintentos manuales (máximo 3) y otro medio de pago | si el cliente corrige y la pasarela acepta, el reintento cobra y deja 2 | OK |
| 5. Pasarela rechaza: reintentos manuales (máximo 3) y otro medio de pago | el reintento usó una referencia de comercio nueva (-r1) | OK |
| 6. Reintento automático: un PENDIENTE se cobra solo al volver la pasarela | con la pasarela caída el cobro queda PENDIENTE | OK |
| 6. Reintento automático: un PENDIENTE se cobra solo al volver la pasarela | y la pasarela no lo tiene (404) | OK |
| 6. Reintento automático: un PENDIENTE se cobra solo al volver la pasarela | sin tocar nada, pasa solo a COBRADO (7 s) | OK |
| 6. Reintento automático: un PENDIENTE se cobra solo al volver la pasarela | con la referencia de la pasarela y reintentado más de una vez | OK |
| 7. Tabla de cobros del back-office y permisos | la tabla muestra los 7 cobros de la prueba, cada uno con su estado | OK |
| 7. Tabla de cobros del back-office y permisos | y el nombre del cliente | OK |
| 7. Tabla de cobros del back-office y permisos | filtrar por RECHAZADO muestra el rechazo y solo rechazados | OK |
| 7. Tabla de cobros del back-office y permisos | sin sesión no se ve ningún cobro | OK |

## Límites de esta evidencia

- La pasarela es un simulador, no un proveedor real: los tiempos son los del mock (lento = 750–1.100 ms; corte del ACL a 700 ms).
- No cubre la captura de tarjeta (CA-W05-03), el cobro de la prima por el CLIENTE ni el pago de indemnizaciones (CA-W05-07).
- Una corrida por entorno.
