# tests/k6/

Pruebas de carga y de regresión de los escenarios de calidad del backend. Los guiones de los experimentos son
la base: se **copian y adaptan** aquí (no se enlazan).

| Guion a crear | Escenario | Umbral | Base reutilizable |
|---|---|---|---|
| `perfilamiento-baseline.js` | EC003 / EC004 | p95 ≤ 400 ms, p99 ≤ 800 ms | `solventa-arquitectura/experimento-1-acl-kyc/k6/baseline.js` |
| `falla-proveedor.js` | EC009 / EC010 | corte a 700 ms, 0 % de 5xx, oferta preliminar | `.../k6/falla-inyectada.js` (alterna modos del stub vía su endpoint de control) |
| `lectura-replica.js` | EC011 / EC012 | p95 ≤ 150 ms, p99 ≤ 300 ms; 0 ofertas con perfil obsoleto | `solventa-arquitectura/experimento-2-replica-riesgo/runner/` |

Guiones de cobros (HU-W05, FC-10, plan de HU-W05 bloque 4). Prueba **reducida** (minutos, no horas): lo que se mide es
la corrección (0 duplicados, 0 5xx) y la latencia del corte, no el rendimiento sostenido. Comparten `lib/cobros.js`.

| Guion | Escenario | Qué verifica | Umbral |
|---|---|---|---|
| `cobros-idempotencia.js` | FC-10 / CA-W05-04 | 20 usuarios mandan el MISMO `idIntentoCobro` a la vez | exactamente 1 cobro en la base; 0 5xx |
| `cobros-pasarela-lenta.js` | FC-10 / CA-W05-05 (EC009/EC010 en cobros) | 2 min con la pasarela en `lento`, luego `sano` | 0 % de 5xx y de `ErrorCobro`; p99 de `crearCobro` < 1,5 s; ≥ 95 % de los pendientes cobrados solos |

Variables: `BFF_URL` (en staging, la del gateway), `USUARIO_BACKOFFICE`, `CLAVE_BACKOFFICE`, y para la pasarela lenta
`STUB_PASARELA_URL` y `TOKEN_PASARELA` (`gcloud auth print-identity-token --audiences=stub-pasarela`). Ninguna clave va al repo.
Antes de correr contra staging: *Costos staging → encender*, y `python3 scripts/sembrar_cobros.py` si hace falta el operador.

Reglas:

- Cada corrida deja su resumen en `docs/evidencias/<EC>-<fecha>.md` (comando, cifras, límites). Los crudos grandes van a `resultados-crudos/` (ignorado por git).
- Son umbrales de un **piso**: staging o local con proveedores simulados y carga supuesta. Dilo en cada informe.
- Desde una VM en GCP se mide mejor que desde el portátil (aprendizaje del Experimento 1: el reloj y la red del equipo local contaminan).
- Los guiones se vuelven **regresión en cada sprint**: se vuelven a correr, no se descartan.
