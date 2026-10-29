# Plan para completar HU-W07 · Socios de distribución (KAN-30), v3

> **Estado: borrador v3 (2026-10-29), por aprobar.** Reemplaza la v2 (rama `feat/KAN-30-socios-api`, 2026-10-24) y la
> sección 3 del plan consolidado `docs/plan-hu-w07-y-ajustes.md` (rama `chore/KAN-30-plan-w07-y-ajustes`, 2026-10-27).
> Esas dos ramas quedan como historia: están 228 y 95 commits detrás de `develop` y no se integran.
>
> Rama: `feat/KAN-30-socios-api-v3`, desde `develop` (`280b32a`). Reparto: **Felipe el backend y Daniel el front**. El
> plan del front es `solventa-frontend-web/docs/plan-w01-w05-w07-front.md` (rama `chore/KAN-30-plan-w03-w07-front`).
>
> Revisado contra el código de `develop` del backend y del front, sin levantar infraestructura.

**HU-W07 · Alta de socios de distribución e integración (API-First, Journey 1).** Un socio de distribución vende la
póliza de Vida Hipotecario de Solventa dentro de su app. Usa credenciales y cuotas aisladas, y Solventa le paga una
comisión. El API Gateway autentica sus llamadas con tokens de acceso. El Core resuelve la solicitud con la cotización de
W01 y la emisión de W03, y la respuesta vuelve al socio.

**Flujo (Jira):**
1. El portal de socios se abre desde el enlace del login.
2. El panel tiene cuatro partes:
   - **Credenciales:** 4 claves, 2 de producción y 2 de pruebas. Cada par corresponde a los dos tokens del gateway.
   - **Estado:** la salud de cada API, en colores, que combina la de sus microservicios y la de sus APIs externas.
   - **Consumo:** estadísticas por API.
   - **Swagger exclusivo** embebido, para documentarse y probar.
3. Un modo de prueba con respuestas simuladas.

---

## 1. Qué hay hoy en `develop` (2026-10-29)

**Los prerrequisitos que pedía el reparto ya están:** W03 terminada en back y front, y M01 en código.

| Prerrequisito | Estado | Dónde |
|---|---|---|
| Flujo del asesor de W03 (bloque 9): código, «Emitir» con UNDER y KYC, enlace de 30 min, firma en tres modos, pago síncrono, reenvío del enlace | ✅ | POLICY `0007`: `/propuestas/{id}/codigo`, `/codigo/verificar`, `/emitir`, `/enlace`, `/enlace-estado`, `/contrato.pdf`, `/firma-enlace` y `/pago/{sesion}/confirmar`. Newman `hu-w03`, carpeta 6 |
| Front de W03 (modal del asesor, firma y retorno de la pasarela) | ✅ | Front #92 |
| Pasarela con página propia y tokenización, más el proveedor alterno | ✅ | `stubs/pasarela` (`/v1/sesiones-pago`, `/checkout/{id}`, `/alterna/v2/…`) |
| PAYMENTS con sesiones y medios de pago | ✅ | `0008_sesiones_y_medios_de_pago` |
| Lista `propuestas` por rol y `documentoEnmascarado` | ✅ | Contrato y `GET /propuestas?cliente_id=&actor_usuario_id=` |
| La revocación avisa a POLICY | ✅ | `POST /propuestas/consentimiento-revocado` desde el BFF |
| M01 (cuentas y onboarding) | ✅ en código | AUTH `0009_cuentas_onboarding`, contrato v0.20 |
| `/v1/graphql/{canal}` (W04-10) | ✅ | BFF `main.py`, Envoy y `openapi.yaml` (rutas exactas, 0,7 s) |

**De socios no hay nada:** ni rol, ni tablas, ni la API `/v1` REST, ni JWKS. AUTH firma todo en HS256. El
`CHECK` `ck_consentimientos_actor_rol` (AUTH `0004`) admite solo CLIENTE, ASESOR y BACKOFFICE.

**Queda fuera de esta versión** (decisión de Felipe, 2026-10-29): los huecos de RISK W01-A3 a A5 (borrar el perfil al
revocar, que venza y que el batch revise el consentimiento). Se resuelven **después de la primera versión de W07, con
back y front**. Mientras tanto, la revocación de un socio avisa al ACL y a POLICY, igual que la de un usuario, pero no a
RISK.

---

## 2. Decisiones

**Siguen de la v2 y del plan consolidado:**

| # | Tema | Decisión | Estado |
|---|---|---|---|
| W07-1 | Qué se revende | **La póliza:** cotización (W01) y emisión (W03). No los datos de las fuentes | Cerrada |
| W07-3 | Portal | Se entra con la clave de producción del socio; no hay usuario de persona. El enlace del login lleva a `/socios/ingreso` | Cerrada |
| W07-4 | Credenciales | Por entorno (PRODUCCIÓN y PRUEBAS), un par: una clave larga y un token corto (JWT ES256 de 15 min) | Cerrada |
| W07-5 | Firma | **El enlace de W03** (W03-22): Solventa lo envía al correo del titular, que firma y paga en la página de Solventa | Cerrada |
| W07-6 | Cobro | **La pasarela de W03:** el titular paga con su tarjeta en la página del stub. El socio nunca ve la tarjeta | Cerrada |
| W07-7 | Comisión | **Se causa al emitir**, porque la emisión ocurre al confirmarse el primer pago (síncrono). Comisión = prima × tasa congelada al aceptar. Una por póliza. El pago al socio queda fuera | Cerrada |
| W07-8 | Aviso al socio | **Consulta periódica** de `GET /v1/propuestas/{id}`. Sin webhooks ni SSE (W07-19) | Cerrada |
| W07-9 | Presupuesto | **Solo en cotizaciones**, porque cada una consulta 5 fuentes. Durable (Postgres), descontado de forma atómica en AUTH; agotado da 429 | Cerrada |
| W07-10 | Modo prueba | Un **dummy interno del BFF** simula el flujo completo con el token de PRUEBAS. No llama a ningún micro ni stub | Cerrada |
| W07-11 | Swagger | API exclusiva con su OpenAPI, embebida en el portal en dos pestañas (producción y prueba) | Cerrada |
| W07-12 a 14 | Salud | Porcentaje ponderado por micro, disponibilidad y latencia de Cloud Monitoring (staging) o Prometheus (local). El socio ve el general y las APIs; el detalle por micro, solo BACKOFFICE | Cerrada (pesos en la sección 7) |
| W07-15 | Identidad del cliente final | El socio pide el código, que llega al correo del titular, y lo verifica con lo que el titular le dicta. Luego «emite» | Cerrada |
| W07-16 | Contacto del cliente final | El socio envía nombre y correo al aceptar; POLICY los guarda cifrados en la propuesta y los borra con el consentimiento si no se emite (W03-25 y 26) | Cerrada |
| W07-17 | El enlace no vuelve al socio | La API responde el estado y el vencimiento del enlace, **nunca la URL**. El socio puede pedir reenviarlo | Cerrada |
| W07-18 | Reabrir | El socio no reabre una propuesta (W03-14). Un rechazo lo resuelve el titular con otra tarjeta | Cerrada |
| W07-19 | Tiempo real | No. Si un socio pide avisos activos, serían webhooks: trabajo futuro | Cerrada |

**Nuevas en la v3 (2026-10-29):**

| # | Tema | Decisión | Estado |
|---|---|---|---|
| W07-20 | **Rol propio `SOCIO`** | El socio es un intermediario como el ASESOR, pero con rol propio. POLICY guarda `actor_rol = SOCIO` y `socio_id`; AUTH registra el consentimiento con `actor_rol = SOCIO`. En POLICY, las ramas del intermediario pasan a `rol in ROLES_INTERMEDIARIO` (ASESOR y SOCIO). **Los avisos al asesor** (SSE `avisos:asesor:<id>` y el correo `poliza-emitida-asesor`) **excluyen al socio sin cambios**, porque ya comparan con `"ASESOR"`. Se descartó «ASESOR con canal SOCIO» porque obligaba a excluir a mano los avisos y la lista, y dejaba «ASESOR» en POLICY y «SOCIO» en AUTH para la misma venta | **Cerrada** |
| W07-21 | **Alta de socios sin interfaz** | En este MVP no hay pantalla ni endpoint de alta. Los socios se crean con la CLI `crear-socio` de AUTH, idempotente. En local corre como tarea del compose en el perfil `semilla`. En staging, después, con un *job* puntual o en la base. La rotación de claves desde el portal sí entra, porque es criterio de Jira | **Cerrada** |
| W07-22 | **Dos socios demo** | `socio-demo-1` y `socio-demo-2`, con ids fijos, tasas distintas y presupuesto. Con dos, Newman prueba que un socio no ve lo del otro (404). Sus claves locales son fijas y **solo locales**, en el entorno de Newman, como las del BACKOFFICE | **Cerrada** |
| W07-23 | Contrato | **GraphQL v0.22** (`estadoSistema`) y el archivo nuevo `contracts/openapi/socios-v1.yaml`. La reserva de la v0.19 se libera en `contracts/CHANGELOG.md` y en `CLAUDE.md` | **Cerrada** |
| W07-24 | `/v1` compartido | **La API de socios convive con `/v1/graphql/{canal}`** (W04-10): <br>• en el BFF va como un `APIRouter` con rutas explícitas, **nunca** como `Mount("/v1")`, que se tragaría `/v1/graphql/*`; <br>• en Envoy, rutas de socios con su prefijo (`/v1/token`, `/v1/cotizaciones`, `/v1/propuestas`…) y `jwt_authn` ES256 **solo en ellas**: `/v1/graphql/*` sigue con el JWT HS256 de usuario, que valida el BFF | **Cerrada** (2026-10-29) |
| W07-25 | Plazo del gateway para socios | **0,7 s, como el resto.** La cotización suma solo la reserva atómica en AUTH, de pocos ms, a la cadena de W01 (EC010 ≤ 500 ms). Si la medición local muestra que no cabe, se sube a 1,5 s solo en `/v1/cotizaciones`. La v2 proponía 1,5 s para todo `/v1` | **Cerrada** (2026-10-29) |
| W07-26 | Clave de firma ES256 | **Igual que el resto de secretos del sistema** (`JWT_SECRETO`, `CIFRADO_CLAVE`): en local, una clave fija marcada «solo local, no secreta» en `docker-compose.yml` y como valor por defecto en `configuracion.py` (como `JWT_SECRETO_LOCAL`), con su entrada en la lista blanca de `.gitleaks.toml`; en staging, `clave-firma-socios` de Secret Manager, obligatoria. **Sigue siendo ES256 y no HS256 como los usuarios**, porque el gateway valida el token del socio con la clave pública (JWKS): con HS256 tendría que guardar el mismo secreto que firma los tokens de todos los roles | **Cerrada** (2026-10-29) |
| W07-27 | Huecos de RISK | Fuera de esta versión (sección 1) | **Cerrada** |

---

## 3. Modelo de datos

### AUTH · migración `0010_socios` (la `0008` es de W03 y la `0009` de M01)

Solo expandir, con `downgrade` probado:

| Tabla | Columnas | Reglas |
|---|---|---|
| `socios` | `id`, `nombre`, `estado` (ACTIVO o SUSPENDIDO), `tasa_comision` (`numeric(5,4)`, p. ej. `0.1000` = 10 %), `creado_en`, `actualizado_en` | Nombre único |
| `credenciales_socio` | `id`, `socio_id`, `entorno` (PRODUCCION o PRUEBAS), `client_id` (único), `clave_cifrada` (`CifradorCampos`, AAD = `socio_id`, para que el portal la muestre), `creada_en`, `revocada_en` | Una vigente por socio y entorno (índice único parcial `revocada_en IS NULL`). Se compara en tiempo constante |
| `presupuestos_socio` | PK (`socio_id`, `entorno`, `api`), `api` CHECK `cotizaciones`, `asignadas`, `consumidas` | `reservar` = `UPDATE … WHERE consumidas < asignadas RETURNING` |
| `consumos_socio` | `socio_id`, `entorno`, `api`, `resultado`, `latencia_ms`, `ocurrido_en` | Append-only (trigger como en `0004`). **Registra todas las llamadas**, aunque solo cotizaciones descuenta. Sin documento ni datos personales |
| `consentimientos` | `ck_consentimientos_actor_rol` suma `SOCIO` | `actor_usuario_id` lleva el `socio_id` |

### POLICY · migración `0008_socios_y_comisiones` (la `0007` es de W03)

| Cambio | Detalle |
|---|---|
| `propuestas.tasa_comision` | Nula salvo con `actor_rol = SOCIO`. La tasa se congela al aceptar (la lee el BFF de AUTH). **El socio es `actor_usuario_id`** cuando `actor_rol = SOCIO` (ya tiene índice), así que no hace falta una columna `socio_id` |
| `comisiones` | `id`, `socio_id`, `poliza_id` (**único**: idempotencia), `propuesta_id`, `prima`, `tasa`, `comision`, `moneda`, `causada_en`. Append-only. Dinero como texto decimal, redondeado según la moneda |

`actor_rol` de POLICY no tiene `CHECK` en la base: el `SOCIO` se admite en los modelos de la API (`^(CLIENTE|ASESOR|SOCIO)$`).

---

## 4. La API `/v1` (contrato `contracts/openapi/socios-v1.json`)

| Grupo | Endpoints | Qué reutiliza |
|---|---|---|
| Acceso (públicos) | `POST /v1/token`, `GET /v1/.well-known/jwks.json`, `GET /v1/docs`, `GET /v1/openapi.json` | — |
| Consentimiento | `POST /v1/consentimientos`, `POST /v1/consentimientos/{id}/revocar` | `ConsentimientoInput` (regla 7). Revocar avisa al ACL y a POLICY, como hoy (RISK después, W07-27) |
| Cotización | `POST /v1/cotizaciones` → la oferta COMPLETA o PRELIMINAR, más `restantes` | `ServicioOferta.solicitar`. **Única que descuenta presupuesto** (W07-9) |
| Propuesta | `POST /v1/propuestas` (`ofertaId` y **`contacto {nombre, correo}` obligatorio**), `GET /v1/propuestas/{id}` con estado, `mensajeEstado` y el vencimiento del enlace | `aceptarOferta` (W03-25) y `explicar_estado` del BFF |
| Identidad | `POST /v1/propuestas/{id}/codigo`, `POST /v1/propuestas/{id}/codigo/verificar` | POLICY W03-20 |
| Emisión | `POST /v1/propuestas/{id}/emitir` (UNDER, KYC y envío del enlace), `POST /v1/propuestas/{id}/reenviar-enlace` | POLICY W03-21 y W03-22 |
| Póliza | `GET /v1/polizas/{id}`, `GET /v1/polizas/{id}/documento` (PDF firmado) | La ruta REST del PDF que ya tiene el BFF |
| Cuenta | `GET /v1/estado?ventana=`, `GET /v1/consumo`, `GET /v1/ventas`, `GET /v1/credenciales`, `POST /v1/credenciales/{entorno}/rotar` | — |

**Reglas comunes:**
- Montos como texto decimal.
- Errores con la forma de `ErrorPublico`, en ES o EN según `Accept-Language`.
- **Nunca un 5xx crudo** (regla 4).
- 429 `PRESUPUESTO_AGOTADO` con `Retry-After`, solo en cotizaciones.
- **404 si la propuesta o la póliza no es del socio.**
- La URL del enlace nunca aparece (W07-17).

**En GraphQL (v0.22, aditivo):** `estadoSistema(ventana)`, solo para BACKOFFICE, con el detalle por micro. La lista
`propuestas` del BACKOFFICE muestra el nombre del socio cuando `actor_rol = SOCIO` (campo aditivo `Propuesta.socio`
o similar, a definir en el bloque 1).

---

## 5. Qué toca de lo existente (todo lo demás es código nuevo)

| Dónde | Cambio |
|---|---|
| `services/policy/app/main.py` | Los modelos con `^(CLIENTE\|ASESOR)$` (líneas 85 y 150) suman `SOCIO`; `socio_id` y `tasa_comision` al aceptar; `GET /propuestas?socio_id=`; `GET /socios/{id}/ventas` |
| `services/policy/app/application/servicio_firma_y_pago.py` | `emitir` y `reenviar_enlace`: `rol in ROLES_INTERMEDIARIO` (líneas 311 y 339). El enlace se envía igual que para el ASESOR |
| `services/policy/app/application/servicio_propuestas.py` | El contacto es obligatorio también para SOCIO |
| `services/policy/app/application/repositorio_propuestas.py` | `emitir_poliza` inserta la comisión **en la misma transacción** si `actor_rol = SOCIO` |
| `services/policy/app/application/avisos.py` y `servicio_emision.py` | **Sin cambios:** ya excluyen al socio (comparan con `"ASESOR"`). Se agrega una prueba que lo fija |
| `services/auth` | Migración `0010`, `app/socios.py`, `app/tokens_socio.py`, endpoints internos y la CLI `app/crear_socio.py` |
| `services/bff` | Paquete nuevo `app/socios/` (`api.py`, `autenticacion.py`, `cotizacion.py`, `emision.py`, `salud.py`, `sandbox.py`) montado como router en `main.py`; `estadoSistema` en `esquema.py`; `clientes.py` suma los métodos de AUTH y POLICY para socios |
| `contracts/` | `openapi/socios-v1.json`, `salud.json`, GraphQL v0.22, `CHANGELOG.md` (libera la v0.19), `README.md` y `scripts/validar_contratos.py` |
| `infra/gateway/envoy.yaml` y `scripts/validar_gateway.py` | Rutas de socios con `jwt_authn` y CORS (W07-24) |
| `infra/` (Terraform, sin `apply`) | `openapi-socios.yaml` y el gateway de socios detrás de `crear_gateway_socios = false`; secreto `clave-firma-socios`; `roles/monitoring.viewer` para el BFF |
| `docker-compose.yml` | Tarea `crear-socio` en el perfil `semilla`; `METRICAS_FUENTE=prometheus` en el BFF |
| `observabilidad/prometheus.yml` | Recoge las métricas del admin de Envoy |

---

## 6. Bloques (commits sucesivos en la rama)

| # | Qué entrega | Depende de |
|---|---|---|
| 0 | Este plan versionado | — |
| 1 | **Contrato:** `socios-v1.json`, GraphQL v0.22 (`estadoSistema`), `salud.json` y su validación, `CHANGELOG.md` con la v0.19 liberada. **Es lo que desbloquea a Daniel** | 0 |
| 2 | **AUTH:** `0010`, socios, credenciales, presupuesto atómico, consumos, ES256 y JWKS, endpoints internos, CLI `crear-socio` y la tarea del compose con los dos socios demo | 1 |
| 3 | **POLICY:** `0008`, rol `SOCIO` en el flujo del intermediario, comisión al emitir, lista por `socio_id` y `GET /socios/{id}/ventas` | 1 |
| 4 | **BFF `/v1`:** autenticación con el JWKS, registro de consumo, cotización con reserva, propuesta, código, emitir, reenvío, póliza, PDF y cuenta | 2, 3 |
| 5 | **Modo prueba:** el dummy con el token de PRUEBAS (cotización por documento de prueba, código fijo `000000`, «emitir», un enlace a una página del dummy que firma y «paga» sola, y estado en Redis con TTL) | 4 |
| 6 | **Salud:** `GET /v1/estado` y `estadoSistema`, con Prometheus en local y Cloud Monitoring en staging | 4 |
| 7 | **Gateway e infraestructura:** Envoy, `validar_gateway.py`, Terraform detrás de su bandera (sin `apply`) | 4 |
| 8 | **Pruebas:** pytest, `verificar-migraciones.sh auth` y `policy`, Newman `socios-w07`, y la evidencia `docs/evidencias/HU-W07-<fecha>.md` | 5, 6, 7 |
| 9 | **Documentos y cierre:** README de AUTH, POLICY y BFF, `infra/README.md`, `CLAUDE.md` y este plan | 8 |

**Integración con el front (después del bloque 8):** Felipe levanta el compose con los dos socios demo y Daniel corre el
portal contra el backend sin MSW. Mientras tanto el front trabaja con MSW a partir del contrato del bloque 1.

---

## 7. Salud del sistema

**Fórmulas, sin cambios desde la v2:**
- Salud de un micro: **0** si `/health` no responde; si responde, **70 % de disponibilidad + 30 % de puntaje de latencia**.
- Puntaje de latencia: 100 en el objetivo y 0 al doble, lineal entre los dos.
- Salud general: suma ponderada de los micros.
- Consolidado: las métricas del gateway.
- Colores: verde ≥ 90, amarillo ≥ 70 y rojo < 70.

**Corrección a la v2 (aprobada el 2026-10-29).** Con la fórmula de la v2, si RATING se cae la barra queda en 90 % y
Cotización en 90, las dos en verde, aunque no se pueda cotizar. Por eso:
- **una API queda en rojo si cualquiera de sus micros no responde** (los necesita todos); si todos responden, se usa el
  promedio de sus micros y de sus APIs externas;
- **la barra general no queda en verde si alguna API está en rojo:** como mucho, amarillo.

**Pesos revisados.** Desde la v2 llegaron `almacenamiento`, `avisos`, `onboarding` y `claims`. **Emisión usa
`almacenamiento`** para la imagen de la firma, así que entra en el cálculo. Los otros tres no están en el camino de
cotizar ni emitir y quedan fuera.

| Micro | Peso | Objetivo de latencia media |
|---|---|---|
| `bff` | 18 | 500 ms |
| `auth` | 14 | 120 ms |
| `acl-worker` | 14 | 300 ms |
| `policy` | 12 | 300 ms |
| `rating` | 10 | 120 ms |
| `risk` | 8 | 120 ms |
| `under` | 7 | 300 ms (ahora decide en línea, al «Emitir») |
| `payments` | 7 | 400 ms (el pago es síncrono al regresar de la pasarela) |
| `almacenamiento` | 6 | 300 ms |
| `consolidador-fuentes` | 4 | 1000 ms |

**APIs para el socio:**

| API | Micros | APIs externas (estado del circuito) |
|---|---|---|
| Cotización | BFF, AUTH, ACL, RISK y RATING | open-finance y open-data |
| Emisión | BFF, AUTH, POLICY, UNDER, PAYMENTS y ALMACENAMIENTO | firma, KYC, pasarela y notificaciones |

Pesos aprobados por Felipe el 2026-10-29; una prueba del CI verifica que suman 100 y que cada micro existe en
`.github/servicios.json`.

---

## 8. Verificación

1. **pytest:**
   - AUTH: reserva atómica con concurrencia, ES256 contra el JWKS, rotación, consumo registrado y la CLI idempotente.
   - POLICY: el SOCIO recorre código → emitir → enlace como el ASESOR; los avisos al asesor no salen para un SOCIO;
     comisión única por póliza y con la tasa congelada.
   - BFF: 429 solo en cotizaciones; un socio no ve lo del otro (404); sin consentimiento no hay cotización; el enlace
     nunca vuelve; `/v1/graphql/*` sigue igual; el dummy es determinista; fórmulas y pesos de salud.
2. **Migraciones:** `bash scripts/verificar-migraciones.sh auth` y `policy` (subir, bajar y subir).
3. **En vivo, en local** (`docker compose --profile servicios up -d --build --wait` y luego
   `docker compose --profile servicios run --rm crear-socio`), por Envoy `:8080`:
   - token → consentimiento → cotización → propuesta con contacto → código → emitir → enlace en el correo del stub →
     firma y pago en la página de la pasarela → `EMITIDA` → comisión en `GET /v1/ventas`;
   - presupuesto agotado da 429; sin token, 401 del gateway; el socio 2 recibe 404 en las propuestas del socio 1;
   - modo prueba hasta `EMITIDA` sin llamadas a los stubs;
   - salud con el perfil `observabilidad`: bajar `rating` y ver Cotización en rojo y la barra general en amarillo.
4. **Newman `socios-w07`** como humo en el CI.
5. **Validadores:** `validar_contratos.py`, `validar_gateway.py` y `validar_servicios.py`.
6. **Logs:** sin documento, nombre, correo ni claves (EC022).

**Nada en staging en este plan:** ni `terraform apply`, ni el secreto, ni el gateway de socios. Se hace con la próxima
liberación, por el pipeline (ADR-09).

---

## 9. Cambios en Jira (los hace Felipe)

| Incidencia | Cambio |
|---|---|
| KAN-30 | Descripción: el socio **revende la póliza** y hace el papel del asesor por la API; el titular firma y paga con el enlace de W03; comisión al emitir. Los socios se crean por semilla, sin pantalla de alta |
| KAN-30, criterios | No cambian. Propuesta opcional: «el socio ve sus pólizas emitidas, las primas y la comisión causada por mes» |
| Tareas de back | Contrato v0.22 y `socios-v1.json`; AUTH `0010` y la CLI; POLICY `0008` y las comisiones; BFF `/v1`; modo prueba; salud; gateway; pruebas y evidencia |
| Tareas de front (Daniel) | Las del plan del front: portal (W07-01 a 03), estado del sistema del BACKOFFICE y la guía en PDF |
| Nueva | «Huecos de RISK: borrar el perfil al revocar, vencimiento y batch» (W01-A3 a A5), después de la primera versión de W07 |
| Nueva, futura | «Alta de socios desde el BACKOFFICE» y «Webhooks para socios» (fuera del MVP) |

---

## 10. Pendiente antes de empezar

1. ~~El visto bueno de Felipe sobre W07-24, 25 y 26 y los pesos~~: cerrados el 2026-10-29.
2. Pasar al plan del front lo que cambia frente a la v2: contrato v0.22, rol SOCIO y dos socios demo.
3. Coordinar con Daniel la fecha del contrato (bloque 1), que es lo que necesita para arrancar sin adivinar.

---

## 11. Estado de ejecución

| # | Bloque | Estado | Verificación |
|---|---|---|---|
| 0 | Plan | Hecho | — |
| 1 | Contrato v0.22 | Hecho | `validar_contratos.py` (OpenAPI de socios y `salud.json`), `sincronizar_socios.py --verificar`, paridad del BFF y 421 pruebas del BFF |
| 2 | AUTH | Hecho | Pruebas de AUTH (token ES256 contra el JWKS, bloqueo, rotación, presupuesto, consumo y CLI); `verificar-migraciones.sh auth`; en vivo contra PostgreSQL: los dos socios demo (la segunda corrida no duplica), token, 3 reservas y 429, devolver, consumo, rotar y JWKS |
| 3 | POLICY | Hecho | Pruebas de POLICY (el SOCIO recorre código → emitir → enlace → firma → pago, sin avisos del asesor; comisión única con la tasa congelada; ventas por mes; HTTP); `verificar-migraciones.sh policy`; en vivo contra PostgreSQL: la comisión se causa una vez (185000 × 0,10 = 18500) y la lista filtra por socio |

**Desvíos frente al plan (decididos al ejecutar):**

- **El contrato REST va en JSON** (`contracts/openapi/socios-v1.json`), no en YAML: el repo no tiene `pyyaml`, FastAPI
  trabaja en JSON y el front genera sus tipos igual. El BFF lleva una copia en `app/socios-v1.json`
  (`scripts/sincronizar_socios.py`, verificado en el CI), igual que `salud.json`.
- **POLICY no suma `socio_id`:** con `actor_rol = SOCIO`, el socio es `actor_usuario_id`, que ya tiene índice.
- **`CLAUDE.md` se actualiza al cierre** (bloque 9): el PR #387 cambia la misma línea.
- **Los socios demo se crean al levantar el compose** (`crear-socios-demo`, del que depende `auth`, como
  `crear-backoffice`), no en el perfil `semilla`: así Daniel tiene socios sin un paso extra.
