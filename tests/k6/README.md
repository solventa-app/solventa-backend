# tests/k6/

Pruebas de carga y de regresión de los escenarios de calidad del backend contra **staging** (o contra el gateway
local). Los guiones se adaptaron de los experimentos de `solventa-arquitectura` y **todo lo que reportan es medido
por k6**: ninguna cifra se calcula, se descuenta ni se fija a mano (si un guion necesita un dato que no puede
medir, lo dice y no lo inventa).

| Guion | Escenario | Umbral | Qué mide de verdad |
|---|---|---|---|
| `perfilamiento-baseline.js` | EC003 / EC004 (HA-LAT-001) | p95 ≤ 400 ms (normal), p99 ≤ 800 ms (pico), 0 % 5xx | La duración de `solicitarOferta` por el gateway/BFF a 5,6 req/s y luego a 3× |
| `falla-proveedor.js` | EC009 / EC010 (HA-LAT-002) | Ninguna llamada espera al proveedor lento; p99 ≤ 500 ms con caché y proveedor caído; 0 % 5xx | La duración de `solicitarOferta` con el stub de Open Finance en `lento` y luego en `caido` (con la caché recalentada entre las dos fases) |
| `lectura-replica.js` | EC011 / EC012 (HA-LAT-003) | p95 ≤ 150 ms, p99 ≤ 300 ms; 0 perfiles obsoletos | Escribe en RISK y lee de RATING (`GET /perfiles/{id}?operation_time=`): solo el tiempo de la **lectura** y si lo leído es lo recién escrito (misma versión y marca) |
| `gateway-presupuesto.js` | EC001 / EC002 / EC008 (HA-LAT-004) | p99 ≤ 500 ms por el gateway; ≤ 120 ms por dependencia (p95); corte duro de 700 ms | La duración por el gateway; la dependencia, llamando al ACL (`POST /fuentes/consultar`, `forzar_proveedor=true`); y el corte con el stub en `lento` |

La preparación común (crear un ASESOR de prueba con el BACKOFFICE, iniciar sesión y registrar el consentimiento del
titular) vive en `lib/preparar.js`. Un BACKOFFICE **no** puede registrar consentimientos ni pedir ofertas (matriz de
`docs/autenticacion-usuarios.md`), por eso se usa un ASESOR. Si la preparación falla, la corrida se aborta: medir
respuestas de error con HTTP 200 daría números sin sentido.

Guiones de cobros (HU-W05, FC-10, plan de HU-W05 bloque 4). Prueba **reducida** (minutos, no horas): lo que se mide es
la corrección (0 duplicados, 0 5xx) y la latencia del corte, no el rendimiento sostenido. Comparten `lib/cobros.js`.

| Guion | Escenario | Qué verifica | Umbral |
|---|---|---|---|
| `cobros-idempotencia.js` | FC-10 / CA-W05-04 | 20 usuarios mandan el MISMO `idIntentoCobro` a la vez | exactamente 1 cobro en la base; 0 5xx |
| `cobros-pasarela-lenta.js` | FC-10 / CA-W05-05 (EC009/EC010 en cobros) | 2 min con la pasarela en `lento`, luego `sano` | 0 % de 5xx y de `ErrorCobro`; p99 de `crearCobro` < 1,5 s; ≥ 95 % de los pendientes cobrados solos |

Variables: `BFF_URL` (en staging, la del gateway), `USUARIO_BACKOFFICE`, `CLAVE_BACKOFFICE`, y para la pasarela lenta
`STUB_PASARELA_URL` y `TOKEN_PASARELA` (`gcloud auth print-identity-token --audiences=stub-pasarela`). Ninguna clave va al repo.
Antes de correr contra staging: *Costos staging → encender*, y `python3 scripts/sembrar_cobros.py` si hace falta el operador.

Guiones de emisión (HU-W03, plan de HU-W03 bloque 6). Un CLIENTE de prueba recorre todo por el gateway (`lib/emision.js`):
consentimiento → oferta → `aceptarOferta` → `documentoFirma` → `firmarPropuesta` → espera a `EMITIDA`. Lo que se mide es de la
firma a `EMITIDA` (consulta cada `SONDEO_MS`, 100 ms por defecto, así que la cifra puede traer hasta ese margen de más); lo
anterior no cuenta. Una emisión que no llega a `EMITIDA` entra con el tiempo que se esperó, y un recorrido que se cae antes de
firmar rompe el umbral.

| Guion | Escenario | Umbral | Qué mide de verdad |
|---|---|---|---|
| `ec005-emision.js` | EC005 (CA-W03-07) | p95 ≤ 1,5 s; ≥ 99 % `EMITIDA`; 0 % 5xx | De `firmarPropuesta` a `EMITIDA` a `RITMO_EMISION` firmas por segundo (**supuesto: 1**) |
| `ec007-emision-pico.js` | EC007 (CA-W03-07) | p99 ≤ 3 s; ≥ 99 % `EMITIDA`; 0 % 5xx | Lo mismo, subiendo a `FACTOR_PICO` × el ritmo normal (**supuesto: 3×**), sosteniéndolo `DURACION_PICO` y bajando |

**La carga de EC005 y EC007 no sale de ningún documento del repo:** el ritmo y el factor son supuestos que cada informe debe
declarar (`RITMO_EMISION`, `FACTOR_PICO`; también `CLIENTES_PRUEBA`, `SONDEO_MS` y `TIMEOUT_EMISION_MS`). Los guiones crean
CLIENTES con el BACKOFFICE (`USUARIO_BACKOFFICE` / `CLAVE_BACKOFFICE`), así que en staging necesitan esas credenciales.

Reglas:

## Cómo correrlos

`scripts/correr_k6.sh` corre los cuatro guiones, guarda los JSON crudos en `tests/k6/resultados-crudos/` (ignorado
por git) y deja un resumen en `docs/evidencias/resumen-k6-<fecha>.md`. Si k6 no está instalado usa la imagen oficial
con Docker, así que no hay que instalar nada.

```bash
# Local: el stack con el gateway (docker compose --profile servicios up -d --build --wait)
STAGING_URL=http://localhost:8080 ACL_URL=http://localhost:8005 ./scripts/correr_k6.sh

# Más corto, para una verificación rápida (declara las duraciones en el informe):
DURACION_NORMAL=3m DURACION_PICO=1m DURACION_FASE=2m DURACION_CORTE=1m \
STAGING_URL=http://localhost:8080 ACL_URL=http://localhost:8005 ./scripts/correr_k6.sh

# Staging (el gateway; los servicios internos son privados, ver abajo):
STAGING_URL="https://<gateway-url>" ./scripts/correr_k6.sh
```

Modo reducido (por defecto, `MODO_REDUCIDO=true`): 10 min a 5,6 req/s (EC003) + 2 min de pico a 3× (EC004);
5 min por fase en EC009/EC010; 10 min en EC011/EC012; y 10 min + 2 min en el gateway. `MODO_REDUCIDO=false` sube a la
duración completa. Cada guion acepta `DURACION_*` para acotarla.

### Lo que necesita cada guion

| Guion | Acceso necesario | Sin ese acceso |
|---|---|---|
| `perfilamiento-baseline.js` | El gateway o el BFF | — |
| `falla-proveedor.js` | El gateway + el control del stub de Open Finance (`STUB_OPEN_FINANCE_URL`) | Con el stub privado: `OMITIR_CONTROL_STUB=true` y cambiar el modo a mano en cada fase |
| `lectura-replica.js` | **RISK y RATING directos** (`RISK_URL`, `RATING_URL`; privados en staging: `RISK_TOKEN` y `RATING_TOKEN` con `gcloud auth print-identity-token --audiences=<url>`) | Se aborta: no se puede medir la réplica sin hablar con ellos |
| `gateway-presupuesto.js` | El gateway; la parte de dependencia, además el **ACL** (`ACL_URL`, privado en staging: `ACL_TOKEN`) | La parte EC008 de dependencia **no se mide** y el informe lo dice |

## Reglas

- Cada corrida deja su evidencia en `docs/evidencias/<EC>-<fecha>.md`: comando, cifras **tal como las imprimió k6**, qué
  se midió y qué se proyectó, y límites. **Una corrida local no es una medición de staging** y el informe debe decirlo;
  la de staging se hace en la sesión de medición (`docs/plan-completar-hu-w01.md`, «Lo que hace felipe a mano»).
- Son umbrales de un **piso**: staging con proveedores simulados (stubs en Cloud Run, D-05) y almacenes en nivel mínimo
  (`db-f1-micro`, Atlas M0, Redis BASIC 1 GB). Dilo siempre en cada informe.
- Antes de medir en staging, enciende Cloud SQL y Redis (*Actions → Costos staging → encender*) y activa
  `calentar_camino_critico = true` si quieres evitar arranques en frío durante la ventana; al terminar, vuelve a apagar.
- Los guiones son **regresión en cada sprint**: se vuelven a correr, no se descartan.
- Lo que tarda más de 700 ms (un proveedor lento **sin caché**, un cobro con la pasarela lenta) por el gateway termina en
  un 504 que el front trata como «intenta de nuevo»; el guion `falla-proveedor.js` mide el caso con caché (donde el BFF
  sí contesta a tiempo) y `scripts/humo_bff.py` comprueba la aplicación directo al BFF en los casos que el gateway corta.
