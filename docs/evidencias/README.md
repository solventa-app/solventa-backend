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
