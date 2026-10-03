# Sprint 1 — alcance del backend

Fuente: *Plan de Trabajo y Historias de Usuario del Sprint 1* (v1.1.0, semana 7) y `PlanningV1.2.xlsx` (documentos del curso, fuera de este repo).
Este archivo es el **resumen operativo para construir**; si el plan cambia, se actualiza aquí en el mismo cambio.

- **Fechas (tentativas, confirmar contra el calendario del curso):** 12–25 de octubre de 2026. El lunes 12 es festivo.
- **Meta:** un cliente o asesor consiente y recibe en línea una oferta de vida hipotecario (con degradación controlada si una fuente falla), y el back-office cobra y concilia pagos con una pasarela tokenizada.
- **Alcance:** HU-W01 (KAN-24, 20 pts, 38 h) + HU-W05 (KAN-28, 6 pts, 11 h) = 26 de 27 pts; **49 h de 52 h netas (holgura 3 h)**.
- **Regla de recorte:** si el Sprint 1 rinde < 22 pts, se recorta desde el final de la lista (HU-M06, HU-M02, HU-M04). Las HU de prioridad HH no se recortan.

## Decisiones (recomendación del plan; confirmar en el Planning)

| ID | Decisión | Recomendación | Antes de |
|---|---|---|---|
| D-01 | Base de velocidad | Recálculo 27/27/44 y regla de recorte | Planning S1 |
| D-02 | Read-your-writes RISK→RATING | Sesión causal: RISK devuelve el `operationTime` y RATING lee con `afterClusterTime`; lectura de la primaria como alternativa dentro de la misma solicitud | PF2-1 |
| D-03 | Cobro antes o después de emitir | Mantener la definición del equipo (antes) y actualizar el Journey 2 | Planning S2 |
| D-04 | Alcance de HA-SEG-002 | Cifrado de campo en la aplicación con Cloud KMS; CMEK nativo queda como mejora | PF2-1 |
| D-05 | Proveedores externos | Stubs por contrato, adaptadores intercambiables; sandbox reales si aparecen | PF2-1 |
| D-06 | Adelantar HU-W05 al Sprint 1 | Aprobar | Planning S1 |

## Actividades de backend (de la sección 2 del plan)

HU-W01 — Perfilamiento y oferta (38 h; las de pantallas van en `solventa-frontend`). La columna Servicio es una asignación propuesta de este repo; el plan solo asigna rol y horas:

| ID | Actividad | Horas | Habilitadora | Servicio |
|---|---|---|---|---|
| T-W01-4 | Tabla de datos de usuario (aquí: registro de consentimiento) | 3 | SEG-002 | `auth` |
| T-W01-6 | Tabla de oferta | 5 | LAT-001, LAT-003 | `rating` |
| T-W01-7 | Endpoint de datos de usuario | 2 | SEG-001 | `bff`, `risk` |
| T-W01-8 | Integración API a Open Finance | 6 | MOD-001, INT-003 | `acl-worker` |
| T-W01-9 | Integración API a Open Data | 5 | INT-003 | `acl-worker` |
| T-W01-10 | Circuit Breaker, Retry y degradación con caché (700 ms) | 4 | LAT-002 | `acl-worker` |
| T-W01-11 | Pruebas unitarias, de contrato y k6 (EC003, EC004, EC010) | 4 | — | `tests/` |
| T-W01-12 | Documentación técnica y revisión de código | 2 | — | — |

HU-W05 — Recaudo (11 h):

| ID | Actividad | Horas | Habilitadora | Servicio |
|---|---|---|---|---|
| T-W05-2 | Integración con la pasarela de pago | 3 | MOD-001 | `acl-worker`, `stubs/pasarela` |
| T-W05-3 | Estados de cobro, idempotencia y balances en el Core | 3 | MOD-001 | `payments` |
| T-W05-4 | Reintento de cobro con Cloud Tasks y Circuit Breaker | 1 | LAT-002 | `payments`, `acl-worker` |
| T-W05-5 | Pruebas de contrato e idempotencia, y documentación | 2 | — | `tests/` |

## Criterios de aceptación clave (resumen)

- **CA-W01-01:** sin consentimiento marcado el botón está deshabilitado y no se consulta ninguna fuente.
- **CA-W01-02:** el evento de consentimiento (actor, finalidad, versión del texto, sello de tiempo) se registra **antes** de consultar cualquier fuente.
- **CA-W01-03:** oferta con prima, cobertura y desglose del score, p95 ≤ 400 ms y p99 ≤ 800 ms en el servidor.
- **CA-W01-04:** si una fuente supera 700 ms o falla → último valor en caché o por defecto, oferta **preliminar** con rango de prima, sin exceder el presupuesto de latencia.
- **CA-W01-05:** sin datos suficientes ni caché → mensaje de error con reintentar y asesor; **nunca un 5xx crudo**.
- **CA-W01-06:** se listan las 5 fuentes (2 Open Finance + 3 Open Data) con su estado y la fecha de captura si el dato viene de caché.
- **CA-W01-07:** contacto con asesor (llamar, chatear o agendar) con confirmación y resumen del trámite.
- **CA-W01-08:** revocación del consentimiento: rechaza nuevas consultas e invalida la caché en ≤ 5 min.
- **CA-W01-09:** RATING usa el perfil recién escrito por RISK (read-your-writes).
- **CA-W01-10:** campos sensibles cifrados en reposo y en tránsito; logs sin datos personales.
- **CA-W01-11:** el asesor comercial autenticado ve la tarjeta de nueva cotización y usa el mismo formulario y flujo que el cliente.
- **CA-W05-01/02:** tabla de cobros por póliza con su estado; reintentar relanza el cobro con la misma clave de idempotencia.
- **CA-W05-03/04/05:** captura de tarjeta con componente tokenizado del proveedor PCI-DSS; un mismo intento de cobro se ejecuta una sola vez; si la pasarela supera 700 ms el cobro queda pendiente (sin 5xx) y se reintenta con Cloud Tasks.
- **CA-W05-06/07:** cambiar de pasarela no toca el Core; la orden de pago de indemnización (Sprint 3) actualiza el balance.

## Habilitadoras del Sprint 1

| HA | Alcance | Criterio | Base reutilizable |
|---|---|---|---|
| LAT-001 (KAN-50) | Cache-Aside de scores en Redis con TTL y versión de reglas | Lectura de caché ≤ 5 ms; p95 del journey ≤ 400 ms | Redis del Exp. 1 |
| LAT-002 (KAN-51) | Adaptadores de Open Finance, Open Data y pasarela con Circuit Breaker y Retry (700 ms) y degradación; sonda fuera del camino síncrono | 0 % de 5xx con el proveedor caído; el circuito cierra solo; confiabilidad ≥ 99,9 % | ACL Worker y Consolidador (Exp. 1) |
| LAT-003 (KAN-52) | RATING lee de la secundaria; incluye el mecanismo de D-02 | ≤ 10 ms extra; 0 ofertas con perfil obsoleto | replica set, escritor, lector y medición de lag (Exp. 2) |
| SEG-001 (KAN-63) | Registro append-only de consentimiento; la revocación invalida caché y bloquea consultas | 100 % de consultas con consentimiento vigente; revocación ≤ 5 min | — |
| SEG-002 (KAN-64) | Cifrado de campo con Cloud KMS, TLS, logs sin datos personales | 100 % cifrado; sin datos de tarjeta propios | — |
| MOD-001 (KAN-67) | Puertos estables (fuente financiera, fuente abierta, pasarela) y un adaptador stub por puerto | Sustituir un adaptador sin modificar ni redesplegar el Core | puerto hexagonal del Exp. 1 |
| INT-003 (KAN-73) | Plantilla de adaptador (interfaz, prueba de contrato, registro por configuración) | Nueva fuente en ≤ 1 semana sin cambiar RISK ni RATING | — |

Diferidas: LAT-004 y ESC-001 al Sprint 2 (dependen del API Gateway); ESC-004 al Sprint 3 en versión reducida.

## Escenarios a medir (umbrales = piso, no valor de producción)

| Escenario | Qué mide | Umbral | Cuándo |
|---|---|---|---|
| EC003 / EC004 | Latencia de perfilamiento y pricing | p95 ≤ 400 ms; p99 ≤ 800 ms | PF2-2 |
| EC009 / EC010 | Timeout duro y degradación con proveedor lento o caído | corte a 700 ms; 0 % de 5xx | PF2-2 |
| EC011 / EC012 | Lectura sobre la réplica | p95 ≤ 150 ms; p99 ≤ 300 ms | PF2-2 |
| EC022 | Cifrado y tarjeta tokenizada | 100 % en reposo y en tránsito | PF2-2 |
| EC023 | Revocación de consentimiento | ≤ 5 min | PF2-1 |
| EC031 | Reemplazo de adaptador | cero cambios en el Core | PF2-1 |
| Idempotencia | Mismo cobro recibido dos veces | 0 cobros duplicados | PF2-2 |

EC005/EC007 (emisión) se miden en el Sprint 2; EC008 tiene una ambigüedad de interpretación sin resolver y **no** se usa como criterio.

## Orden sugerido de construcción (PF2-1)

1. **Consentimiento en AUTH primero** (T-W01-4): ninguna fuente se consulta sin él; bloquea el resto.
2. En paralelo: puertos y adaptadores stub del ACL con Circuit Breaker (T-W01-8/9/10), RISK + RATING con réplica y caché (T-W01-6/7), y la línea de PAYMENTS con pasarela stub (T-W05-*).
3. Pruebas tempranas en PF2-1: EC023 (revocación) y EC031 (reemplazo de adaptador). Los k6 de latencia y falla inyectada van en PF2-2.

## Observaciones de trazabilidad pendientes (Jira)

- HA-SEG-001 cita EC026 (ataque a la API); el escenario de revocación es **EC023** (KAN-102) y no tiene enlace a KAN-63.
- HU-W05: el PDF del backlog le asocia HA-SEG-003, pero Jira (KAN-28) y el documento de arquitectura usan HA-SEG-002. El plan usa la relación de Jira.
