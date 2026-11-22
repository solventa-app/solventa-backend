Evidencia de escenarios de calidad: `<EC>-<fecha>.md` (ver docs/convenciones.md).

## Métricas del Sprint 1 (Jira KAN)

`burndown.png`, `velocity.png`, `value.png` — generadas a partir de datos reales del tablero
KAN (no inventados), metodología y trazabilidad en `metricas-sprint-1.md`.

## Cobertura de código del Sprint 1 (SonarQube Cloud)

`cobertura-sprint-1.png` — evolución de la cobertura del backend y del frontend y cobertura por servicio, a partir de los
análisis reales de Sonar; metodología, tablas y trazabilidad en `cobertura-sprint-1.md`.

## Anexo al plan de trabajo: reajuste del Sprint 2

`anexo-reajuste-sprint-2.docx` (y su copia en `anexo-reajuste-sprint-2.pdf`) — documento para tutores y evaluadores que explica el
intercambio de historias entre los sprints 2 y 3 y el alcance de sus tareas, tomando como referencia el Plan de trabajo v1.2.0 y sus
figuras de planeación. Incluye la justificación de cada cambio, el efecto en capacidad y compromiso, la evidencia de la semana 1 y los
límites de esa evidencia. `figura-3-cronograma-reajustado.png` y `figura-4-capacidad-reajustada.png` son las dos figuras de planeación
nuevas, con el diseño de las figuras 1 y 2 del plan.

## Métricas del Sprint 2, semana 1 (Jira KAN)

`burndown-sprint-2-semana-1.png`, `velocity-sprint-2-semana-1.png`, `value-sprint-2-semana-1.png` — evidencia de avance semanal del
19 al 25 de octubre de 2026 (corte 25/10), generada con datos reales del tablero KAN; muestran dos lecturas, A (historias y tareas
Finalizadas) y B (solo tareas Finalizadas, la base de las gráficas del Sprint 1 y la única comparable con ellas). Metodología, tablas y salvedades en `metricas-sprint-2-semana-1.md`.

## Cierre del Sprint 2 (Jira KAN y SonarQube Cloud)

`burndown-sprint-2.png`, `velocity-sprint-2.png`, `value-sprint-2.png` — evidencia de cierre del 19 de octubre al 1 de noviembre
de 2026 (corte 1/11): 44,5 de 45 pts y 52,2 % de 53 % de valor de negocio, con datos reales del tablero KAN. Metodología, tablas,
excepciones de fechas y salvedades en `metricas-sprint-2.md`.

`cobertura-sprint-2.png` — evolución de la cobertura del backend, la web y la app móvil, y cobertura por servicio del backend al
cierre, a partir de los análisis reales de Sonar; tablas y trazabilidad en `cobertura-sprint-2.md`.

## Métricas del Sprint 3 (Jira KAN)

`burndown-sprint-3.png`, `velocity-sprint-3.png`, `value-sprint-3.png` — corte parcial del 8/11 (semana 1), con su metodología en
`metricas-sprint-3.md`.

`burndown-sprint-3-semana-2.png`, `velocity-sprint-3-semana-2.png`, `value-sprint-3-semana-2.png` — corte parcial del 13/11 (semana 2):
32 de 33,5 pts y 16,7 % de 17,6 % de valor de negocio, con datos reales del tablero KAN. Metodología, tablas y salvedades (KAN-290 fuera
de los números, HU-M04 cerrada en un día, KAN-278 con deuda) en `metricas-sprint-3-semana-2.md`.

## Asignación de tareas del Sprint 1

`sprint-1-tareas-asignacion.md` — quién tiene asignada cada tarea de HU-W01/HU-W05, estimación vs.
tiempo real de lo cerrado en la semana 1, vía API de Jira.

## Flujo de prueba local de HU-W01 (backend)

`flujo-prueba-local-hu-w01.md` — smoke oficial (`scripts/humo_bff.py`) + recorrido manual por GraphQL
contra el stack real en docker compose: oferta completa, degradada, timeout duro, consentimiento
revocado. Capturas reales, no inventadas.

`EC017-2026-10-18.md` — batch nocturno de RISK (HA-ESC-004): caudal de una tarea, reparto entre 4 tareas contra MongoDB real y efecto sobre el p95 de EC003, medidos en local a escala reducida el 2026-10-24, con la proyección, sus límites y el riesgo de Atlas M0 (100 operaciones por segundo, sin medir). No es evidencia de staging.

## Release burndown

`release-burndown.png` — puntos que le faltan al release (96 pts, 3 sprints) al cierre de cada sprint: los Sprints 1 y 2 reales
(64 y 19,5 pts por hacer), el plan original y el **plan reajustado del 25/10/2026** (HU-W03 a 13 pts, HU-W07 a 6, HU-M02 al Sprint 3, HU-M01 cerrando
en el Sprint 2 y HU-W04 adelantada del Sprint 3); datos de Jira y del plan de trabajo, con la justificación del cambio y sus riesgos
en `release-burndown.md`.

## HU-W03: suscripción, firma y póliza (bloque 6)

`EC005-EC007-2026-10-24.md` — decisión y emisión de la póliza (de la firma a `EMITIDA`) con k6: p95 en carga normal y p99 en pico,
carga supuesta y límites de una corrida local. `HU-W03-newman-EC009-EC010-2026-10-24.md` — recorrido de extremo a extremo por el gateway
con Newman, autorización por rol y degradación de firma, KYC, pasarela y notificaciones, con los hallazgos. `resumen-k6-2026-10-24.md` —
resumen que deja `scripts/correr_k6.sh`. Todas son corridas locales, no de staging.
`CA-W03-05-firma`, `CA-W03-01-oferta-aceptada`, `CA-W03-06-reapertura` y `EC022-policy` (todas de 2026-10-24) — los informes de las brechas del bloque 7 contra los criterios oficiales: la firma sin 5xx por el gateway, la versión de reglas y la oferta preliminar, la reapertura de una póliza con el pago recuperado, y el cifrado de POLICY con sus logs. Corridas locales.
`EC022-contacto-2026-10-24.md` — bloque 8: el correo y el teléfono del cliente (dato personal nuevo en AUTH) cifrados por campo, 0 coincidencias en 478.275 líneas de log de 20 contenedores, 0 columnas de contacto en POLICY y PAYMENTS, y el contacto sin ninguna ruta de lectura por GraphQL; con el aviso por correo (PDF), por SMS y omitido. Corrida local.
`bloque-7-filas-4-7-8-9-2026-10-24.md` — lo que el cliente ve cuando la propuesta no avanza (`motivoEstado`), `oferta(id)` que ya devuelve la oferta guardada, el análisis del aviso con el PDF, y lo que se pudo verificar de staging sin desplegar (plan de Terraform dirigido, «UNDER caído» repetido 10 veces). Corrida local.
