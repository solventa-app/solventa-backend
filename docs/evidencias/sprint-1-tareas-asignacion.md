# Sprint 1 — tareas, asignación y estimación vs. tiempo real

Fuente: tablero Jira **KAN**, proyecto Solventa, sprint `KAN Sprint 1` (2026-10-05 a 2026-10-19).
Generado el 2026-10-11, **recontado y corregido el 2026-10-12** vía API de Jira (MCP Atlassian) — la
primera versión traía un desfase de conteo (ver nota de corrección al final) y HU-W05 avanzó entre una
fecha y otra. 29 incidencias en el sprint: 2 Historias (KAN-24, KAN-28) + 27 Tareas.

## HU-W01 (KAN-24, 20 pts / 15% valor de negocio) — asignación

| Tarea | Resumen | Asignado a | Estado | Puntos |
|---|---|---|---|---|
| KAN-120 | W01-01 — Solicitud + consentimiento | jf.sarmiento23 | Finalizada | 1 |
| KAN-140 | W01-02 — Consultando fuentes | jf.sarmiento23 | Finalizada | 1 |
| KAN-141 | W01-03a — Oferta — éxito completo | jf.sarmiento23 | Finalizada | 1 |
| KAN-142 | W01-03b — Oferta — degradada | jf.sarmiento23 | Finalizada | 1 |
| KAN-143 | W01-03c — Oferta — fallo | jf.sarmiento23 | Finalizada | 0.5 |
| KAN-144 | W01-04 — Detalle de fuentes consultadas | jf.sarmiento23 | Finalizada | 0.5 |
| KAN-145 | W01-05 — Contactar a un asesor | jf.sarmiento23 | Finalizada | 0.5 |
| KAN-177 | Revisar que se cumplan las historias de arquitectura | f.taboada | En curso | 0.5 |
| KAN-178 | Implementar BFF y gateway | Daniel Felipe Urrego | Finalizada | 1.5 |
| KAN-179 | Implementar mecanismo de auth (JWT) | Daniel Felipe Urrego | En curso | 1 |
| KAN-180 | Implementar ACL Workers | f.taboada | Finalizada | 2.5 |
| KAN-181 | Conectar ACL Workers con APIs externas reales (Open Finance / Open Data) | f.taboada | En curso | 1.5 |
| KAN-182 | Implementación de MongoDB | f.taboada | Finalizada | 1 |
| KAN-183 | Implementación microservicio de rating | f.taboada | Finalizada | 1.5 |
| KAN-184 | Implementación microservicio de RISK | f.taboada | Finalizada | 1.5 |
| KAN-185 | Implementación pruebas unitarias front | jf.sarmiento23 | En curso | 0.5 |
| KAN-186 | Implementación pruebas unitarias back | Daniel Felipe Urrego | En curso | 1 |
| KAN-187 | Implementación pruebas de integración back (set de datos) | Daniel Felipe Urrego | En curso | 0.5 |
| KAN-188 | Implementación pruebas de componentes UI | jf.sarmiento23 | En curso | 0.5 |
| KAN-189 | Implementación pruebas end to end (manuales) | **sin asignar** | En curso | 0.5 |
| KAN-190 | Integrar back con front | jf.sarmiento23 | En curso | 0.5 |
| KAN-193 | Componentes transversales front web | jf.sarmiento23 | Finalizada | 0* |

\* KAN-193 quedó con 0 puntos/0% valor porque se creó después de la primera distribución y no se
volvió a repartir; técnicamente pendiente de ajustar si se quiere que la suma siga dando exactamente 20.

**Resumen HU-W01 (22 tareas, no 21 — ver nota de corrección):** 21 de 22 asignadas (95%), 1 sin
asignar (KAN-189). 13 Finalizadas, 9 En curso, 0 sin arrancar. Reparto por persona: jf.sarmiento23
(11 tareas), f.taboada (6), Daniel Felipe Urrego (4), 1 sin asignar.

## HU-W05 (KAN-28, 6 pts / 8% valor de negocio) — asignación

| Tarea | Resumen | Asignado a | Estado | Puntos |
|---|---|---|---|---|
| KAN-196 | W05-01 — Integración con la pasarela de pago | Daniel Felipe Urrego | En curso | 1.5 |
| KAN-197 | W05-02 — Estados de cobro e idempotencia | Daniel Felipe Urrego | En curso | 1.5 |
| KAN-198 | W05-03 — Reintento de cobro con Circuit Breaker | f.taboada | En curso | 1 |
| KAN-199 | W05-04 — Pruebas de idempotencia y documentación | Daniel Felipe Urrego | En curso | 0.5 |
| KAN-200 | W05 — Implementación front | jf.sarmiento23 | En curso | 1.5 |

**Resumen HU-W05 (actualizado 2026-10-12):** 5 de 5 tareas asignadas (100%) — cambió desde el
2026-10-11, cuando las 5 estaban sin asignar y sin arrancar. Las 5 están ya "En curso", 0 Finalizadas
todavía (por eso no aparecen en la tabla de estimación vs. tiempo real: ninguna tiene worklog aún).

## Estimación vs. tiempo real — tareas cerradas esta semana (06–11 oct)

Son las 13 tareas con estado **Finalizada** al cierre de la semana 1 (todas de HU-W01; HU-W05 no tiene
ninguna cerrada todavía). "Estimación" es el campo nativo de seguimiento de tiempo de Jira
(`timetracking.originalEstimate`), **no** los Story Points — son dos campos independientes en este
tablero.

| Tarea | Estimado | Real (worklog) | Real / estimado | Responsable |
|---|---|---|---|---|
| KAN-120 | 1d | 1d | 100% | jf.sarmiento23 |
| KAN-140 | 4h | 4h | 100% | jf.sarmiento23 |
| KAN-141 | 4h | 2h | 50% | jf.sarmiento23 |
| KAN-142 | 4h | 2h | 50% | jf.sarmiento23 |
| KAN-143 | 4h | 2h | 50% | jf.sarmiento23 |
| KAN-144 | 4h | 4h | 100% | jf.sarmiento23 |
| KAN-145 | 4h | 1h | 25% | jf.sarmiento23 |
| KAN-178 | 4d | 3d 4h | 87.5% | Daniel Felipe Urrego |
| KAN-180 | 4d | 2d | 50% | f.taboada |
| KAN-182 | 3d | 2d | 66.7% | f.taboada |
| KAN-183 | 3d | 3 min* | — | f.taboada |
| KAN-184 | 4d | 3 min* | — | f.taboada |
| KAN-193 | 4h | 4h | 100% | jf.sarmiento23 |

\* **KAN-183 y KAN-184 no tienen tiempo real registrado de verdad.** Sus worklogs son de 1–3 minutos:
se usaron solo para fijar la fecha (`started`) que necesitaba el burndown corregido
(`metricas-sprint-1.md`), no como registro honesto de horas. Si se quiere un "tiempo real" creíble para
esas dos, hay que pedirle a f.taboada que cargue el worklog real retroactivo.

**Lectura del resto (11 tareas con dato real):** de las registradas con tiempo real honesto, la mayoría
tardó **menos** de lo estimado (50–100% del estimado), salvo KAN-178 que se acercó bastante a su
estimación (87.5%). Ninguna tarea cerrada esta semana se pasó del tiempo estimado. Dicho esto, la
muestra es pequeña (11 tareas, 1 persona concentra 8 de ellas) para sacar una conclusión general de
velocidad del equipo — sirve más como primer dato que como tendencia.

## Resumen general (corregido 2026-10-12)

- **27 tareas en el sprint (22 de HU-W01 + 5 de HU-W05), 26 asignadas (96%), 1 sin asignar** (KAN-189,
  de HU-W01).
- **13 Finalizadas, 14 En curso, 0 Tareas por hacer.** HU-W05 ya no está detenida: sus 5 tareas pasaron
  de "sin asignar / por hacer" (2026-10-11) a asignadas y "En curso" (2026-10-12).
- jf.sarmiento23 es quien más tareas tiene asignadas (12, entre HU-W01 y HU-W05) y quien más ha cerrado
  (8 de las 13 Finalizadas — todas en HU-W01, porque HU-W05 aún no tiene ninguna Finalizada).
- f.taboada y Daniel Felipe Urrego quedan empatados en 7 tareas asignadas cada uno.

## Nota de corrección (2026-10-12)

La versión del 2026-10-11 tenía un desfase de conteo: `KAN-193` se repartió **después** de la primera
distribución de HU-W01 y la tabla se actualizó con esa fila, pero el texto de resumen (totales,
reparto por persona, "21 de 21") se quedó calculado sobre las 21 tareas originales — de ahí que los
porcentajes no cuadraran con la tabla. Esta versión recuenta directamente desde Jira (JQL
`sprint = "KAN Sprint 1"`) en vez de sobre una copia editada a mano, y de paso recoge que HU-W05
avanzó entre una fecha y otra.
