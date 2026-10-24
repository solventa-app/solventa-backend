Evidencia de escenarios de calidad: `<EC>-<fecha>.md` (ver docs/convenciones.md).

## Métricas del Sprint 1 (Jira KAN)

`burndown.png`, `velocity.png`, `value.png` — generadas a partir de datos reales del tablero
KAN (no inventados), metodología y trazabilidad en `metricas-sprint-1.md`.

## Cobertura de código del Sprint 1 (SonarQube Cloud)

`cobertura-sprint-1.png` — evolución de la cobertura del backend y del frontend y cobertura por servicio, a partir de los
análisis reales de Sonar; metodología, tablas y trazabilidad en `cobertura-sprint-1.md`.

## Asignación de tareas del Sprint 1

`sprint-1-tareas-asignacion.md` — quién tiene asignada cada tarea de HU-W01/HU-W05, estimación vs.
tiempo real de lo cerrado en la semana 1, vía API de Jira.

## Flujo de prueba local de HU-W01 (backend)

`flujo-prueba-local-hu-w01.md` — smoke oficial (`scripts/humo_bff.py`) + recorrido manual por GraphQL
contra el stack real en docker compose: oferta completa, degradada, timeout duro, consentimiento
revocado. Capturas reales, no inventadas.

## Release burndown

`release-burndown.png` — puntos que le faltan al release (96 pts, 3 sprints) al cierre de cada sprint: el Sprint 1 real y el plan
de los otros dos; datos de Jira y del plan de trabajo, metodología en `release-burndown.md`.

## HU-W03: suscripción, firma y póliza (bloque 6)

`EC005-EC007-2026-10-24.md` — decisión y emisión de la póliza (de la firma a `EMITIDA`) con k6: p95 en carga normal y p99 en pico,
carga supuesta y límites de una corrida local. `HU-W03-newman-EC009-EC010-2026-10-24.md` — recorrido de extremo a extremo por el gateway
con Newman, autorización por rol y degradación de firma, KYC, pasarela y notificaciones, con los hallazgos. `resumen-k6-2026-10-24.md` —
resumen que deja `scripts/correr_k6.sh`. Todas son corridas locales, no de staging.
`CA-W03-05-firma`, `CA-W03-01-oferta-aceptada`, `CA-W03-06-reapertura` y `EC022-policy` (todas de 2026-10-24) — los informes de las brechas del bloque 7 contra los criterios oficiales: la firma sin 5xx por el gateway, la versión de reglas y la oferta preliminar, la reapertura de una póliza con el pago recuperado, y el cifrado de POLICY con sus logs. Corridas locales.
