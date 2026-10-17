# Métricas del Sprint 1 — metodología y trazabilidad

Fuente: tablero Jira **KAN**, proyecto Solventa, sprint `KAN Sprint 1` (id 2, 2026-10-05 a 2026-10-19).
Generado el 2026-10-11 (semana 1 del sprint). Nada de lo que sigue es inventado para el gráfico:
todos los números vienen de campos reales de Jira, leídos vía API (`acli` / MCP Atlassian Rovo).

## Story Points por tarea (`customfield_10076`, "Story Points")

- Las 21 tareas de HU-W01 (`KAN-120` a `KAN-190`) y las 5 de HU-W05 (`KAN-196` a `KAN-200`) no tenían
  puntos cargados: el campo no estaba habilitado en la pantalla del tipo de incidencia "Tarea" (solo
  existía en "Historia"). Se habilitó en Jira y se repartieron los 20 pts de `KAN-24` y los 6 pts de
  `KAN-28` entre sus tareas, ponderado por complejidad relativa (ver tabla en el chat de la sesión;
  suma exacta verificada contra cada HU padre).

## Business Value por tarea (`customfield_10112`, "Valor de negocio" — **distinto** de
`customfield_10111`, que es el "Valor de negocio" a nivel de Historia)

- `customfield_10111` (13 Historias del backlog, suma exacta 100%) ya estaba asignado manualmente
  por el equipo antes de esta sesión — no se tocó.
- `customfield_10112` es un campo duplicado con el mismo nombre visible pero habilitado para "Tarea".
  Se repartió el valor de cada HU (15% para HU-W01, 8% para HU-W05) entre sus tareas, **proporcional
  a los Story Points** de cada una (`valor_tarea = (valor_HU / puntos_HU) × puntos_tarea`). Suma
  exacta verificada: 15.0 y 8.0.

## Burndown (`burndown.png`)

**No usa el estado nativo de Jira** (`resolutiondate`): las 25 tareas se marcaron "Finalizada" el
mismo día (11 de octubre), por lo que ese campo habría mostrado una caída vertical en un solo día,
sin reflejar el trabajo real distribuido durante la semana. En su lugar, se usó la fecha `started`
del último worklog registrado en cada tarea (el worklog sí tiene fecha real de cuándo se hizo el
trabajo, aunque se haya guardado en Jira después). Puntos restantes = 26 menos la suma acumulada de
Story Points de las tareas completadas hasta esa fecha.

| Fecha | Completado ese día (pts) | Restante |
|---|---|---|
| 10-06 | 5.0 (KAN-120, 178, 180) | 21.0 |
| 10-07 | 2.5 (KAN-140, 183) | 18.5 |
| 10-08 | 3.0 (KAN-141, 142, 182) | 15.5 |
| 10-09 | 2.0 (KAN-143, 184) | 13.5 |
| 10-10 | 1.0 (KAN-144, 145) | 12.5 |

Línea ideal: 26 → 0 pts en línea recta sobre los 14 días del sprint. 12 de octubre marcado como
festivo (lunes festivo en Colombia).

## Velocity (`velocity.png`)

Semana 1 (5–11 oct): planeado 13 pts (mitad del compromiso, ritmo lineal) vs. real 13.5 pts
completados — ligeramente adelantado. Semana 2 (12–19 oct) todavía en curso al momento de generar
el gráfico.

## Business value entregado (`value.png`)

De las 25 tareas repartidas, las que ya estaban "Finalizada" al 2026-10-11 suman:
- HU-W01: 10.125% de 15% comprometido (12 de 21 tareas).
- HU-W05: 0% de 8% comprometido (ninguna tarea arrancada).
- Total semana 1: ≈10.1% de valor de negocio entregado, de 23% comprometido en el sprint.

## Cómo se generaron las imágenes

`matplotlib` (Python), script ad hoc de la sesión (no versionado — los datos de origen y la
metodología están en este archivo para poder regenerarlas).
