# Plan para completar HU-W05 · Backend

> Plan del **backend** (`solventa-backend`) para HU-W05 (KAN-28). El del front vive en
> `solventa-frontend-web/docs/plan-completar-hu-w05.md`. Comparte reglas con el de W01 (`docs/plan-completar-hu-w01.md`):
> se mide contra staging, pruebas reducidas, sin costos nuevos salvo los aprobados, y cada bloque actualiza sus
> documentos en el mismo PR. Las decisiones marcadas como cerradas no se reinterpretan.

**HU-W05 · Recaudo y pago de indemnizaciones.** Como sistema de tesorería, quiero procesar el cobro de primas e
indemnizaciones a través de pasarelas de pago tokenizadas, para garantizar las transacciones con máxima confiabilidad
sin almacenar datos de tarjeta en servidores propios.

**Historias de arquitectura:** HA-SEG-002 (cifrado CMEK de PII y datos financieros, EC022) y HA-MOD-001 (capa
anticorrupción, EC031). Por `docs/sprint-1.md`, la tarea T-W05-4 también se apoya en HA-LAT-002 (circuit breaker y
700 ms). **Plan de pruebas:** FC-10 (integración con la pasarela: contrato e idempotencia con Postman/Newman;
idempotencia, confirmación transaccional y manejo de errores, con evidencia de flujo completo y trazabilidad).

## Estado hoy (revisado en `develop`, 2026-10-18)

Ya está hecho y no se vuelve a construir:

- `cobros`, `crearCobro` y `reintentarCobro` en el BFF, solo BACKOFFICE con token. Pantalla W05-01 integrada en el front.
- Idempotencia por `idIntentoCobro` (CA-W05-04): el mismo intento se ejecuta una sola vez; 409 si llega con otros datos.
- Pasarela lenta (> 700 ms) → cobro `PENDIENTE` sin 5xx (CA-W05-05). Cada reintento **concilia** con la pasarela antes
  de cobrar, así que no hay doble cobro.
- RECHAZADO se reintenta a mano hasta 3 veces (`reintentosRestantes`, referencia `{idIntentoCobro}-rN`, contrato v0.6).
- Token de identidad entre servicios (PAYMENTS → ACL), nombre del cliente cifrado en AUTH, semilla
  `scripts/sembrar_cobros.py`, Newman (carpeta 9) y pruebas unitarias en PAYMENTS, ACL y BFF.
- Grafana con GCP y trazas (PR #165 y #166): los cobros ya se ven en los tableros y en Flujos.

## Decisiones

| # | Tema | Decisión | Estado |
|---|---|---|---|
| W05-1 | Reintentos | RECHAZADO: a mano hasta 3 veces, cada uno con su referencia. PENDIENTE: lo reintenta el sistema. | Cerrada (2026-10-17) |
| W05-2 | Captura de tarjeta (CA-W05-03) | **No se construye.** El prototipo no tiene esa pantalla y W03-02b factura la prima con la cuota del crédito. PAYMENTS solo recibe un **token opaco** del proveedor y **nunca** datos de tarjeta, que es lo que exige PCI-DSS. Se ajusta CA-W05-03 en Jira. | Cerrada (2026-10-17) |
| W05-3 | Indemnizaciones (CA-W05-07) | Pasan al **Sprint 3**, con la historia de siniestros. PAYMENTS ya tiene `GET /polizas/{id}/balance`; la orden de pago de indemnización y su exposición en GraphQL llegan allá. | Cerrada (`docs/sprint-1.md`) |
| W05-4 | Cobro antes o después de emitir (D-03) y primer cobro al firmar | **Sprint 2**, con HU-W03 (`docs/cobros-conciliacion.md`, sección 10). | Cerrada |
| W05-5 | Cifrado (HA-SEG-002) | Lo de W01 cubre la base de PAYMENTS (CMEK en Cloud SQL y llave en Secret Manager envuelta por KMS). Además se cifra en la aplicación el **`token_medio_pago`**, que hoy se guarda en claro. `cliente_id` es un id opaco y no se cifra. | Cerrada (sigue la decisión 6 de W01) |
| W05-6 | Cambio de pasarela (HA-MOD-001, EC031) | Se demuestra con un **segundo adaptador** de pasarela elegido por configuración, con 0 líneas cambiadas en PAYMENTS y el BFF. Este plan es el dueño de EC031; el bloque 5 de W01 apunta aquí. | Cerrada |
| W05-7 | Trabajo en segundo plano en Cloud Run | Los bucles de fondo no corren en staging (bloque 0). Se disparan desde fuera: **Cloud Tasks** para el reintento de cobros y la reconciliación de fuentes, **Cloud Scheduler** para la sonda del circuito. Capa gratuita. | Cerrada (felipe, 2026-10-18) |
| W05-8 | Evento `cobro.estado-cambiado` | Sigue solo en el log. Pub/Sub se aprobó únicamente para `PerfilActualizado` (decisión 9 de W01). | Cerrada |

## Plan de cambios (backend)

### Bloque 0 · Trabajo en segundo plano que no corre en Cloud Run (P1, afecta también a W01)

**Hallazgo.** Los servicios en Cloud Run tienen `cpu_idle = true` y mínimo 0 instancias (`infra/servicios.tf`, líneas
145-147 y 185): solo tienen CPU mientras atienden una petición y se apagan sin tráfico. Tres piezas dependen de un bucle
de fondo que en staging, por eso, **no corre de forma confiable**:

| Pieza | Dónde | Qué deja de pasar en staging |
|---|---|---|
| Reintento automático de cobros `PENDIENTE` | `payments/app/application/reintento.py` (bucle `asyncio`, "placeholder local de Cloud Tasks") | Los pendientes no se reintentan solos (CA-W05-05) y un cobro que queda en `cobrando` no se concilia |
| Sonda de recuperación del circuito | `acl-worker/app/application/sonda.py` | El circuito no se cierra fuera del camino del usuario (regla 5, HA-LAT-002) |
| Reconciliación de fuentes degradadas | `consolidador-fuentes` (trabajador `rq` en el lifespan) | La cola `fuentes-reconciliacion` no se consume (W01) |

**Arreglo decidido (W05-7), sin cambiar la lógica, solo el disparador** (como ya prevé el docstring de `reintento.py`):

| Pieza | Disparador nuevo | Endpoint interno |
|---|---|---|
| Reintento de cobros | **Cloud Tasks**: al quedar `PENDIENTE`, PAYMENTS encola una tarea con espera creciente que llama a su propio endpoint con token de identidad. Es lo que dice CA-W05-05. | `POST /cobros/_reintentar` → `ejecutar_una_pasada` |
| Sonda del circuito | **Cloud Scheduler** cada minuto | `POST /_sonda` del ACL Worker |
| Reconciliación de fuentes | **Cloud Tasks**: el ACL encola una tarea HTTP por fuente degradada en vez de un job de `rq` | `POST /reconciliar` del consolidador |

En local y en CI los bucles siguen igual (bandera `DISPARADOR=bucle|externo`). Costo: Cloud Tasks y Cloud Scheduler en
la capa gratuita (1 millón de operaciones al mes y 3 tareas programadas por cuenta de facturación; con la del batch de
W01 se usan dos).

### Bloque 1 · Cifrado del token del medio de pago (HA-SEG-002, EC022)

| Dónde | Cambio |
|---|---|
| PAYMENTS | Copiar el cifrador AES-GCM de AUTH y cifrar `token_medio_pago` al guardar; la llave de datos viene de Secret Manager envuelta por KMS (bloque 3 de W01). |
| PAYMENTS · migración | Alembic de expansión: cifra los tokens ya guardados, idempotente y con `downgrade` probado (`scripts/verificar-migraciones.sh payments`). |
| infra | Permiso de cifrar/descifrar con la llave de KMS para la cuenta de PAYMENTS y el secreto `clave-cifrado` en su `servicios.json`. |
| Prueba | EC022 para cobros: la fila en Postgres no muestra el token sin la llave. Se suma a la evidencia `EC022-<fecha>.md` de W01. |

### Bloque 2 · Cambio de pasarela sin tocar el Core (HA-MOD-001, EC031)

| Dónde | Cambio |
|---|---|
| ACL Worker | Segundo adaptador del puerto de pasarela (`PasarelaAlterna`) que habla con un proveedor de forma distinta (otros nombres de campos y códigos de estado) y traduce al mismo contrato del puerto. Se elige con `PROVEEDOR_PASARELA`. |
| `stubs/pasarela` | Un modo de la API "alterna" en el mismo stub (sin servicio nuevo en Cloud Run). |
| Prueba | Pruebas de contrato del puerto corren contra los dos adaptadores. Evidencia `docs/evidencias/EC031-<fecha>.md`: el diff del cambio, que solo toca `acl-worker/app/adapters/` y configuración (0 líneas en `payments` y `bff`), y una corrida de cobros con cada adaptador. |

### Bloque 3 · Reintento con Cloud Tasks y cobros atascados (CA-W05-05)

Va junto con el bloque 0. Además:

| Dónde | Cambio |
|---|---|
| PAYMENTS | Un cobro que quedó en `cobrando` (el proceso se cayó a mitad) se concilia en la siguiente tarea en vez de quedarse así. |
| PAYMENTS | Bucle y botón a la vez no cuentan dos intentos: el contador se actualiza en la misma transacción que toma el cobro (`SELECT … FOR UPDATE SKIP LOCKED`). |
| Prueba | pytest con la pasarela en `caido` → `PENDIENTE` → se recupera → la tarea lo cobra una sola vez. |

### Bloque 4 · Pruebas y evidencia de FC-10 (reducidas, contra staging)

| Escenario | Meta | Cómo se prueba | Evidencia |
|---|---|---|---|
| FC-10 contrato e idempotencia | 0 cobros duplicados | Newman carpeta 9 contra la URL de staging (gateway) | `docs/evidencias/FC-10-<fecha>.md` con el reporte `htmlextra` |
| Idempotencia bajo carga | 0 duplicados con el mismo `idIntentoCobro` | k6 **2 minutos**: 20 usuarios mandan el mismo intento a la vez | Conteo de cobros en la base = 1 por intento |
| Pasarela lenta (EC009/EC010 para cobros) | 0 % de 5xx; `PENDIENTE` y luego `COBRADO` | k6 **5 minutos** con el stub de pasarela en `lento` y luego normal | p99, 5xx y el cobro recuperado por Cloud Tasks |
| Cambio de pasarela (EC031) | 0 líneas en el Core | Bloque 2 | `EC031-<fecha>.md` |
| Cifrado (EC022) | 100 % cifrado | Bloque 1 | Junto a `EC022-<fecha>.md` de W01 |

**Datos en staging:** `scripts/sembrar_cobros.py` hoy es solo local (usa el stub de la pasarela del compose). Se adapta
para staging: `BFF_URL` (la del gateway), `STUB_PASARELA_URL` del stub desplegado y token de identidad de `gcloud` para
llamar a su endpoint de control, que es privado.

Todo en la misma sesión de medición de W01 (staging encendido, correr, apagar).

### Bloque 5 · Observabilidad de W05

| Dónde | Cambio |
|---|---|
| `observabilidad/gcp/dashboards/hu-w05.json` | Paneles del tablero Servicios filtrados a BFF, PAYMENTS, ACL y pasarela, más un panel de logs de cobros por estado (sin datos personales). Ya listado en el bloque 1 de W01. |

### Lo que el API Gateway (bloque 10 de W01) cambia en W05

Nada en el código de cobros: `cobros`, `crearCobro` y `reintentarCobro` pasan por el gateway con el mismo JWT
(`X-Forwarded-Authorization`). La semilla y Newman apuntan a la `gateway_url`.

## Cambios en despliegue, infraestructura y pipelines (backend)

| Dónde | Cambio | Bloque | Costo |
|---|---|---|---|
| `infra/` | Cola de Cloud Tasks `reintento-cobros` (y `reconciliacion-fuentes`), cuenta con `cloudtasks.enqueuer` para PAYMENTS y ACL, `run.invoker` del servicio destino (`crear_tareas`) | 0, 3 | Capa gratuita |
| `infra/` | Cloud Scheduler para la sonda del ACL (cada minuto) | 0 | Capa gratuita |
| `.github/servicios.json` | `DISPARADOR=externo` y nombre de la cola en PAYMENTS, ACL y consolidador; `clave-cifrado` en PAYMENTS | 0, 1 | — |
| `infra/` | KMS: permiso para la cuenta de PAYMENTS | 1 | — |
| `.github/workflows/ci.yml` | Pruebas de contrato del puerto de pasarela con los dos adaptadores | 2 | — |

Lo que **no** cambia: el contrato GraphQL de cobros, la semilla y el flujo de despliegue.

## Documentos que actualiza cada bloque

| Bloque | Documentos |
|---|---|
| 0 | `docs/arquitectura-backend.md` (disparadores externos), `docs/cobros-conciliacion.md`, `infra/README.md` (`crear_tareas`, Scheduler), README de `payments`, `acl-worker` y `consolidador-fuentes` |
| 1 | README de `payments`, `docs/cobros-conciliacion.md` (token cifrado) |
| 2 | README de `acl-worker` (cómo agregar una pasarela), `docs/evidencias/EC031-<fecha>.md` |
| 3 | `docs/cobros-conciliacion.md` (sección 8: los dos problemas quedan resueltos) |
| 4 | `tests/k6/README.md`, `tests/newman/README.md`, `docs/evidencias/` |
| Al terminar | `docs/sprint-1.md` (CA-W05-03 y CA-W05-07 como quedaron) y `CLAUDE.md` → **Estado** |

## Lo que hace felipe a mano (backend)

| # | Cuándo | Qué haces | Dónde |
|---|---|---|---|
| 1 | Ahora | Ajustar en Jira (KAN-28): CA-W05-03 sin captura de tarjeta (token opaco del proveedor) y CA-W05-07 (indemnizaciones) al Sprint 3 | Jira |
| 2 | Bloques 0 y 1 | Aprobar el release a `main` que crea las colas, el Scheduler y los permisos | GitHub |
| 3 | Antes de la sesión de medición | *Costos staging → encender*; sembrar cobros en staging con `scripts/sembrar_cobros.py` (ya adaptado en el bloque 4) con el usuario operador, cuando te pase el comando | GitHub → Actions y tu terminal |
| 4 | En la sesión | Correr Newman y k6 cuando te pase los comandos, con Grafana abierto | Tu terminal |
| 5 | Al terminar | *Costos staging → apagar* | GitHub → Actions |

## Estado de implementación (2026-10-18, rama `feat/KAN-28-completar-hu-w05`, PR por abrir)

Hecho y probado con pruebas automáticas locales (PAYMENTS 90, ACL Worker 92, mock de la pasarela 24; ruff y
`terraform validate` limpios). **Nada de esto se ha corrido contra staging ni se ha aplicado en GCP.**

| Bloque | Estado | Detalle |
|---|---|---|
| 0 · Disparadores externos | **Parcial** | PAYMENTS: reintento por Cloud Tasks (`DISPARADOR=externo`, `POST /cobros/_reintentar`). ACL: sonda por Cloud Scheduler (`POST /sonda/ejecutar`). Terraform en `infra/tareas.tf` con `crear_tareas = true`, sin aplicar. **Pendiente:** la reconciliación de fuentes del consolidador (hoy cola `rq`) |
| 1 · Cifrado del token | Hecho | `enc:v1:` AES-256-GCM, migración `0007` (idempotente, con `downgrade`); la migración contra Postgres real la verifica el CI (`verificar-migraciones.sh payments`). Falta la envoltura con KMS cuando entre el bloque 3 de W01 |
| 2 · Cambio de pasarela (EC031) | Hecho en pruebas | `AdaptadorPasarelaAlterna`, stub con segunda API, pruebas de contrato parametrizadas. **Pendiente:** correr el stack con `PROVEEDOR_PASARELA=alterna` y Newman (`docs/evidencias/EC031-pasarela-2026-10-18.md`, sección 4) |
| 3 · Atascados | **Parcial** | Los `cobrando` viejos se concilian y se cobran una vez. **No se hizo** el `SELECT … FOR UPDATE SKIP LOCKED`: bucle y botón a la vez no cobran dos veces (concilia + la pasarela es idempotente); solo pueden contar dos intentos |
| 4 · Pruebas FC-10 | **Preparado, sin correr** | `tests/k6/cobros-idempotencia.js`, `cobros-pasarela-lenta.js`, semilla adaptada a staging y `docs/evidencias/FC-10-plantilla.md` (solo plantilla, sin cifras) |
| 5 · Tableros | Pendiente | Depende de los tableros por historia de HU-W01 |

Límite conocido del bloque 0: el estado de los circuitos vive en la memoria de cada instancia, así que la sonda por Scheduler
revisa los de la instancia que recibe la llamada. Compartir ese estado (Redis) queda para después.

## Para después

- **Sprint 2:** D-03 (cobro antes o después de emitir) y el primer cobro al firmar, con HU-W03.
- **Sprint 3:** orden de pago de indemnización, balance en GraphQL y su pantalla, con siniestros (CA-W05-07).
- Evento `cobro.estado-cambiado` por Pub/Sub cuando una historia lo consuma.

## Decisiones abiertas

Ninguna. Del Planning solo sigue abierta D-01 (base de velocidad), que es de proceso del equipo.
