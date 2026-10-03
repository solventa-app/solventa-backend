# tests/k6/

Pruebas de carga y de regresión de los escenarios de calidad del backend. Los guiones de los experimentos son
la base: se **copian y adaptan** aquí (no se enlazan).

| Guion a crear | Escenario | Umbral | Base reutilizable |
|---|---|---|---|
| `perfilamiento-baseline.js` | EC003 / EC004 | p95 ≤ 400 ms, p99 ≤ 800 ms | `solventa-arquitectura/experimento-1-acl-kyc/k6/baseline.js` |
| `falla-proveedor.js` | EC009 / EC010 | corte a 700 ms, 0 % de 5xx, oferta preliminar | `.../k6/falla-inyectada.js` (alterna modos del stub vía su endpoint de control) |
| `lectura-replica.js` | EC011 / EC012 | p95 ≤ 150 ms, p99 ≤ 300 ms; 0 ofertas con perfil obsoleto | `solventa-arquitectura/experimento-2-replica-riesgo/runner/` |

Reglas:

- Cada corrida deja su resumen en `docs/evidencias/<EC>-<fecha>.md` (comando, cifras, límites). Los crudos grandes van a `resultados-crudos/` (ignorado por git).
- Son umbrales de un **piso**: staging o local con proveedores simulados y carga supuesta. Dilo en cada informe.
- Desde una VM en GCP se mide mejor que desde el portátil (aprendizaje del Experimento 1: el reloj y la red del equipo local contaminan).
- Los guiones se vuelven **regresión en cada sprint**: se vuelven a correr, no se descartan.
