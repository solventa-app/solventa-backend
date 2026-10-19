# Métricas del Sprint 1 — metodología y trazabilidad

Fuente: tablero Jira **KAN**, proyecto Solventa, sprint `KAN Sprint 1` (id 2; en Jira empieza el 2026-10-05 00:00 y
termina el 2026-10-18 23:59, hora de Bogotá: 14 días). Regenerado el 2026-10-18 (cierre del sprint; primera versión
el 2026-10-11). Nada de lo que sigue es inventado para el gráfico: todos los números vienen de campos reales de
Jira, leídos vía API (MCP Atlassian Rovo).

## Story Points por tarea (`customfield_10076`, "Story Points")

- Las 21 tareas de HU-W01 (`KAN-120` a `KAN-190`) y las 5 de HU-W05 (`KAN-196` a `KAN-200`) no tenían
  puntos cargados: el campo no estaba habilitado en la pantalla del tipo de incidencia "Tarea" (solo
  existía en "Historia"). Se habilitó en Jira y se repartieron los 20 pts de `KAN-24` y los 6 pts de
  `KAN-28` entre sus tareas, ponderado por complejidad relativa (suma exacta verificada contra cada HU padre).
- **Alcance agregado durante el sprint:** `KAN-207` (ajustes de versión de Vite en el frontend) y `KAN-208`
  (endpoints para insertar datos de prueba), 1 pt cada una, creadas el 17 de octubre dentro del sprint y sin historia
  padre. Se cuentan en el burndown y la velocity como **alcance agregado** (+2 pts), separadas de los 26 pts
  comprometidos, y **no aportan valor de negocio** (no tienen historia).

## Business Value por historia (`customfield_10111`) y por tarea (`customfield_10112`)

- `customfield_10111` (13 Historias del backlog, suma exacta **100 %**) ya estaba asignado manualmente por el equipo
  antes de esta sesión — no se tocó. Es la base del `value.png`: HU-W01, HU-W07, HU-W03 y HU-M01 15 %; HU-W04,
  HU-W05 y HU-M04 8 %; HU-W02, HU-M02, HU-M05 y HU-M06 3 %; HU-W06 y HU-M03 2 %.
- `customfield_10112` es un campo duplicado con el mismo nombre visible pero habilitado para "Tarea". Se repartió el
  valor de cada HU (15 % para HU-W01, 8 % para HU-W05) entre sus tareas, **proporcional a los Story Points**
  (`valor_tarea = (valor_HU / puntos_HU) × puntos_tarea`). Los valores guardados en Jira van redondeados a tres
  decimales; el gráfico los recalcula sin redondear.

## Burndown (`burndown.png`)

**No usa el estado nativo de Jira** (`resolutiondate`) como fecha de avance: las tareas de la semana 1 se marcaron
"Finalizada" el mismo día (11 de octubre), por lo que ese campo habría mostrado una caída vertical en un solo día, sin
reflejar el trabajo real distribuido durante la semana. En su lugar se usó la fecha `started` del último worklog de
cada tarea (el worklog sí tiene la fecha real de cuándo se hizo el trabajo, aunque se haya guardado después).
Las 5 tareas sin worklog (`KAN-177`, `KAN-181`, `KAN-189`, `KAN-198`, `KAN-199`, marcadas con `*`) usan su
`resolutiondate`. Puntos restantes = alcance del día menos la suma acumulada de Story Points de las tareas
completadas hasta esa fecha (alcance: 26 pts hasta el 16 de octubre, 28 desde el 17).

| Fecha | Completado ese día (pts) | Restante |
|---|---|---|
| 10-06 | 5 (KAN-120, KAN-178, KAN-180) | 21 |
| 10-07 | 2,5 (KAN-140, KAN-183) | 18,5 |
| 10-08 | 3 (KAN-141, KAN-142, KAN-182) | 15,5 |
| 10-09 | 2 (KAN-143, KAN-184) | 13,5 |
| 10-10 | 1 (KAN-144, KAN-145) | 12,5 |
| 10-13 | 3 (KAN-186, KAN-187, KAN-200) | 9,5 |
| 10-14 | 6 (KAN-179, KAN-181\*, KAN-185, KAN-188, KAN-197, KAN-198\*) | 3,5 |
| 10-15 | 0,5 (KAN-190) | 3 |
| 10-16 | 1,5 (KAN-196) | 1,5 |
| 10-17 | 2,5 (KAN-199\*, KAN-207, KAN-208) | 1 |
| 10-18 | 1 (KAN-177\*, KAN-189\*) | 0 |

Hay avance en 11 de los 14 días del sprint (sin avance: 10-05, 10-11 y 10-12, festivo en Colombia). Línea ideal:
26 → 0 pts en línea recta entre el inicio y el cierre del 18 de octubre; los sábados, domingos y el festivo van
sombreados.

## Velocity (`velocity.png`)

Acumulado semana a semana, con el plan repartido de forma lineal (13 pts por semana):

| Semana | Planeado (acum.) | Real (acum.) | Real de la semana |
|---|---|---|---|
| 1 (5–11 oct) | 13 | 13,5 | 13,5 |
| 2 (12–18 oct) | 26 | 28 (26 comprometidos + 2 agregados) | 14,5 |

## Business value entregado (`value.png`)

Las 26 tareas de HU-W01 y HU-W05 están "Finalizada", así que se entregó todo el valor comprometido en el sprint:
HU-W01 15 % + HU-W05 8 % = **23 % del valor de negocio del proyecto**; el 77 % restante sigue en el backlog.

## Salvedades

- Las historias `KAN-24` (HU-W01) y `KAN-28` (HU-W05) siguen en estado "Tareas por hacer" en Jira aunque todas sus
  tareas con puntos están finalizadas; el gráfico cuenta el valor por tareas, no por el estado de la historia.
- `KAN-166` (W05-01, conciliación de pagos) cuelga de `KAN-28`, está en el Sprint 1, sin puntos y en "Tareas por
  hacer". No pesa en el burndown ni en la velocity (0 pts) pero impide dar HU-W05 por cerrada en Jira.
- El corte es el 18 de octubre; el sprint termina esa noche, así que lo registrado después no está en las imágenes.

## Cómo se generaron las imágenes

`matplotlib` (Python), script ad hoc de la sesión (no versionado): los datos de origen y la metodología están en este
archivo para poder regenerarlas. Paleta verde `#0f7d5f` / dorado `#b08d3a`, validada para daltonismo y contraste.
