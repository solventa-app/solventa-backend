# Plan para completar HU-W07 · Backend y frontend (v2: socios revenden pólizas)

> **Estado: borrador v2 (2026-10-24), sin aprobar.** Reemplaza la v1 del 2026-10-22, que suponía que el socio
> revendía las 5 fuentes de consulta. Jira: **KAN-30** (13 pts, Sprint 2). Rama: `feat/KAN-30-socios-api` (local, ya
> al día con `develop`). Plan gemelo del frontend: `solventa-frontend-web/docs/plan-completar-hu-w07.md`.

**HU-W07 Alta de socios de distribución e integración (API-First — Journey 1).** El socio de distribución vende la
póliza de Vida Hipotecario de Solventa embebida en su app, con credenciales y cuotas aisladas, y Solventa le paga una
comisión. El API Gateway autentica sus llamadas con tokens de acceso; la solicitud la resuelve el Core (cotización de
W01 y emisión de W03) y la respuesta vuelve al ecosistema del socio.

**Flujo (Jira):**
1. Se entra por el enlace del login al portal de socios.
2. El dashboard tiene cuatro partes:
   - **Credenciales:** 4 claves, 2 de producción y 2 de pruebas; cada par corresponde a los dos tokens del gateway.
   - **Estado:** la salud de cada API, que promedia la de los microservicios con la de sus APIs externas, en colores.
   - **Consumo:** estadísticas por API.
   - **Swagger exclusivo:** embebido para documentarse y probar.
3. Un sandbox de pruebas con mocks.

## Decisiones

| # | Tema | Decisión | Fecha |
|---|---|---|---|
| W07-1 | Qué se revende | **La póliza**: cotización (micros de W01) y emisión (micros de W03). No los datos de las fuentes. | 2026-10-23 |
| W07-2 | Alta | Solo BACKOFFICE, por **CLI** (`crear-socio`): socio, 4 claves, tasa de comisión y presupuesto. Endpoint y pantalla de BACKOFFICE, después. | 2026-10-22 |
| W07-3 | Portal | Se entra con la clave de producción del socio (no hay usuario de persona). El enlace del login lleva a `/socios/ingreso`, sin formulario de registro. | 2026-10-22 |
| W07-4 | Credenciales | Por entorno (PRODUCCIÓN y PRUEBAS) hay un par: una clave larga y un token corto (JWT ES256 de 15 min). | 2026-10-22 |
| W07-5 | Firma | **Enlace de firma.** La API devuelve un enlace del proveedor; el socio lo abre en su app y **firma el titular**, con lo que se mantiene el no repudio de W03-8. En el prototipo, `stubs/firma` simula esa página. | 2026-10-23 |
| W07-6 | Cobro de la prima | **Lo hace Solventa, igual que hoy**: convenio de recaudo del crédito (W03-5) y PAYMENTS. El socio no cobra. | 2026-10-23 |
| W07-7 | Comisión | **Un porcentaje de la prima, que se causa al cobrar.** Cada prima `cobrado` de una póliza vendida por un socio genera comisión = prima × tasa. La tasa queda congelada al aceptar la oferta. El pago al socio queda fuera. | 2026-10-23 |
| W07-8 | Aviso al socio | **Consulta periódica** de `GET /v1/propuestas/{id}` hasta un estado final. Sin webhooks. | 2026-10-23 |
| W07-9 | Presupuesto | **Solo en cotizaciones**, porque cada una consulta 5 fuentes externas. Aceptar, firmar y emitir no se limitan. Es durable (Postgres) y se descuenta como tokens. | 2026-10-23 |
| W07-10 | Modo prueba | Un **dummy interno** del BFF simula el flujo completo, emisión incluida. Los stubs representan a los proveedores reales y solo los usa producción. | 2026-10-22 |
| W07-12 | Salud general | Porcentaje ponderado por micro. Salud del micro = 0 si `/health` no responde; si responde, 70 % disponibilidad + 30 % puntaje de latencia. Pesos y objetivos en `salud.json` versionado (bloque 3b). | 2026-10-24 |
| W07-13 | Disponibilidad y latencia | Por micro, de las métricas nativas de Cloud Run (staging) o de Prometheus (local). El consolidado sale del gateway. Ventana configurable (15 min, 1 h, 24 h, 7 d). | 2026-10-24 |
| W07-14 | Visibilidad | El socio ve la salud general, el consolidado y las APIs. El detalle por micro solo lo ven BACKOFFICE y Grafana. | 2026-10-24 |
| W07-11 | Swagger | API exclusiva `/v1` con su OpenAPI, embebida en el portal en dos pestañas: producción y modo prueba. | 2026-10-22 |

## Lo que hay hoy (en `develop`, 2026-10-23)

**Cotización (W01): completa.**
- Cadena: BFF `solicitarOferta` (`services/bff/app/servicio_oferta.py`) → AUTH (consentimiento) → ACL (5 fuentes) →
  RISK (perfil) → RATING `POST /ofertas` (`services/rating/app/main.py:174`).
- Hallazgos abiertos en local: EC004 ❌ y EC008 ❌.

**Emisión (W03): a medias.** Está en `services/policy` (puerto 8007):

| Bloque de W03 | Estado |
|---|---|
| 1 · Propuesta (`POST /propuestas`) | Integrado (PR #213) |
| 2 · Firma (`/propuestas/{id}/documento-firma` y `/firma`, `stubs/firma`) | Integrado (PR #214) |
| 3 · UNDER y KYC | Pendiente |
| 4 · Cobro y emisión (`poliza.emitida`, POLICY suscrito a `cobro.estado-cambiado`) | Pendiente |
| 5 · PDF | Pendiente |

`docs/plan-completar-hu-w03.md` todavía dice «en rama» para los bloques 1 y 2.

**De socios no hay nada:** ni rol, ni tablas, ni `/v1`, ni JWKS.
- El gateway solo reenvía 4 rutas GraphQL con un corte de 0,7 s.
- AUTH firma en HS256.
- La firma de W03 es síncrona y solo para el CLIENTE titular con sesión.
- El BFF traduce según `Accept-Language` (`services/bff/app/idioma.py`).

## Fases (por la dependencia con W03)

| Fase | Qué entrega | Depende de |
|---|---|---|
| **A** | Contrato, AUTH de socios, `/v1` de consentimiento, **cotización**, aceptar y consultar la propuesta; gateway; portal; modo prueba **completo** (con emisión simulada); salud; consumo | Nada pendiente |
| **B** | Firma por enlace (ACL, `stubs/firma` y POLICY) | W03 bloque 2, ya integrado |
| **C** | Emisión real, PDF de la póliza y comisiones al cobrar | W03 bloques 3, 4 y 5 |

Si W03 no llega a tiempo, W07 se puede demostrar entero en modo prueba, y en producción hasta «firmada, en emisión».

## Análisis: API exclusiva para socios (sin cambios desde la v1)

- **Sub-app `/v1` montada en el BFF.** Tiene su propio `/v1/openapi.json` y `/v1/docs`. El socio nunca ve GraphQL.
- **Contrato primero:** `contracts/openapi/socios-v1.yaml`, con un CI que compara contra lo que genera la sub-app.
- **Gateway propio en GCP:** `solventa-socios-api`, con `infra/gateway/openapi-socios.yaml`.
- **Tokens:** `POST /v1/token` da un JWT ES256 que el gateway valida con el JWKS. Solo los de socio usan ES256.
- **La cuota no va en el borde.** GCP API Gateway cuenta las cuotas por proyecto consumidor, no por socio, así que
  el presupuesto se descuenta de forma atómica en AUTH y, si se agota, responde 429. Con Apigee viviría en el borde.

## Bloque 1 · Contrato `contracts/openapi/socios-v1.yaml` (contract-keeper, fase A)

| Grupo | Endpoints | Qué reutiliza |
|---|---|---|
| Acceso (públicos) | `POST /v1/token`, `GET /v1/.well-known/jwks.json`, `GET /v1/docs`, `GET /v1/openapi.json` | — |
| Consentimiento | `POST /v1/consentimientos` (`documentoIdentidad`, `finalidad`, `versionTexto`), `POST /v1/consentimientos/{id}/revocar` | `ConsentimientoInput` (regla 7) |
| Cotización | `POST /v1/cotizaciones` (`consentimientoId`, `documentoIdentidad`, `valorCredito`, `plazoMeses`, `direccionInmueble`) → la oferta COMPLETA o PRELIMINAR, más `restantes` del presupuesto | `SolicitudOfertaInput` y `Oferta` |
| Propuesta | `POST /v1/propuestas` (`ofertaId`), `GET /v1/propuestas/{id}`, `GET /v1/propuestas/{id}/documento-firma` | `Propuesta`, `EstadoPropuesta`, `DocumentoFirma` y `ErrorAceptacion` |
| Firma (fase B) | `POST /v1/propuestas/{id}/solicitud-firma` (`autorizaCobro`, `versionDocumento`) → `{urlFirma, expiraEn}` | `FirmarPropuestaInput` |
| Póliza (fase C) | `GET /v1/polizas/{id}`, `GET /v1/polizas/{id}/documento` (PDF) | `Poliza` |
| Cuenta | `GET /v1/estado`, `GET /v1/consumo`, `GET /v1/ventas` (pólizas, primas cobradas y comisiones por mes), `GET /v1/credenciales`, `POST /v1/credenciales/{entorno}/rotar` | — |

**Reglas comunes:**
- Los montos van como texto decimal, como en todo el contrato.
- Los errores siguen la forma de `ErrorPublico`, en español o inglés según `Accept-Language`.
- **Nunca un 5xx crudo** (regla 4): si las fuentes fallan, la oferta es PRELIMINAR.
- 429 `PRESUPUESTO_AGOTADO` con `Retry-After`, solo en cotizaciones.
- Un socio solo ve propuestas y pólizas que vendió él: 404 si no son suyas.

**También en el contrato:**
- `GET /v1/estado?ventana=` devuelve `general {porcentaje, color, disponibilidad, latenciaMediaMs, ventana}` y
  `apis[]`.
- En GraphQL, la consulta `estadoSistema(ventana)`, solo para BACKOFFICE, con el detalle por micro (bloque 3b).
- `contracts/salud.json`, con su validación.

En `contracts/CHANGELOG.md` va la siguiente versión aditiva libre (v0.13 si nadie la toma antes), y la sección
OpenAPI en `contracts/README.md` y `scripts/validar_contratos.py`.

## Bloque 2 · AUTH: socios, claves, presupuesto y tokens (backend-builder, fase A)

**Migración `0007_socios.py`** (la 0006 es `preferencias`), solo expandir, con `downgrade` probado:
- `socios`: `id`, `nombre`, `estado` ACTIVO|SUSPENDIDO, `tasa_comision` (`numeric(5,4)`, por ejemplo `0.1000` = 10 %)
  y fechas.
- `credenciales_socio`: `socio_id`, `entorno` PRODUCCION|PRUEBAS, `client_id` único y `clave_cifrada`. La clave se
  cifra con `CifradorCampos` de `services/auth/app/cifrado.py`, con AAD = `socio_id`, para que el portal pueda
  mostrarla; se compara en tiempo constante. Más `creada_en` y `revocada_en`.
- `presupuestos_socio`: PK (`socio_id`, `entorno`, `api`), con `api` CHECK `cotizaciones` (W07-9), `asignadas` y
  `consumidas`.
- `consumos_socio`: append-only (trigger de 0004/0005). Columnas `socio_id`, `entorno`, `api` (cotizaciones,
  propuestas, firma, polizas…), `resultado`, `latencia_ms` y `ocurrido_en`. Registra **todas** las llamadas, para
  las estadísticas, aunque solo cotizaciones descuenta. Sin documento ni datos personales.
- `SOCIO` se agrega a `ck_consentimientos_actor_rol`.

**`app/socios.py`:**
- Alta y rotación de claves.
- `reservar` es atómico: `UPDATE … WHERE consumidas < asignadas RETURNING`.
- `cerrar` devuelve la unidad si falla por un error nuestro.
- `registrar_consumo`.
- `consumo(rango)`.

**`app/tokens_socio.py`:**
- Firma en ES256 con `SOCIOS_CLAVE_FIRMA`: PEM en local, Secret Manager `clave-firma-socios` en la nube.
- Claims: `sub=socio_id`, `rol=SOCIO`, `entorno`, `scope`, `iss`, `aud`, `exp` (15 min) y `jti`.
- Publica el JWKS.

**Endpoints internos:**
- `POST /socios/tokens` (401 genérico y bloqueo por intentos de `sesiones.py`)
- `GET /.well-known/jwks.json`
- `GET /socios/{id}` (incluye `tasa_comision`)
- Credenciales: listar y rotar
- Presupuesto: reservar y cerrar
- `POST /socios/{id}/consumos`
- `GET /socios/{id}/consumo`

**CLI `app/crear_socio.py`** (patrón de `app/crear_usuario.py`, tarea `crear-socio` en compose):
- Idempotente.
- Crea el socio con `--tasa-comision` e imprime las 4 claves una vez.
- Asigna `--presupuesto-cotizaciones` (producción) y `--presupuesto-cotizaciones-pruebas`.
- También recarga el presupuesto.

## Bloque 3 · BFF: la API `/v1` (backend-builder, fases A–C)

**Paquete `services/bff/app/socios/`:**

| Archivo | Qué hace |
|---|---|
| `api.py` | La sub-app y sus rutas. Registra cada llamada en `consumos_socio`. |
| `autenticacion.py` | Verifica el JWT ES256 con el JWKS de AUTH (caché de 10 min) como defensa en profundidad. Lo lee de `X-Forwarded-Authorization` o `Authorization`. |
| `cotizacion.py` | Reserva el presupuesto → **reutiliza `ServicioOferta.solicitar`** (consentimiento, titular, ACL, RISK y RATING) → cierra la reserva. |
| `emision.py` | Aceptar (POLICY `POST /propuestas`, con `canal=SOCIO`, `socio_id` y la `tasa_comision` leída de AUTH), consultar la propuesta, documento de firma, solicitud de firma (fase B), póliza y PDF (fase C). Comprueba que la propuesta sea de ese socio. |
| `salud.py` | Salud del sistema, por micro y por API: ver el bloque 3b. |
| `sandbox.py` + `dummy/` | El modo prueba, descrito abajo. |


**Modo prueba (`sandbox.py`).** Con un token de PRUEBAS, el BFF responde con el dummy y no llama a ningún micro ni
stub:

| Paso | Cómo responde el dummy |
|---|---|
| Cotización | Mapeos JSON por documento de prueba (estilo WireMock): COMPLETA, PRELIMINAR, presupuesto agotado y consentimiento vencido |
| Propuesta y firma | Guarda el estado en Redis con TTL (el sandbox tolera perderlo en el apagado nocturno). El enlace de firma apunta a una página del dummy que firma al abrirla |
| Emisión | Progresa sola en segundos según el documento de prueba: EMITIDA, PENDIENTE_PAGO → EMITIDA, NO_EMITIDA, PAGO_RECHAZADO |
| Póliza | Un PDF de muestra marcado «MODO PRUEBA» |
| Ventas | Comisiones ficticias, para que el panel tenga datos |

## Bloque 3b · Salud del sistema: porcentaje ponderado, disponibilidad y latencia (backend-builder e infra-engineer, fase A)

### De dónde salen los números (sin instrumentar nada nuevo)

| Qué | Staging: Cloud Monitoring (gratis, ya en los tableros `observabilidad/gcp/dashboards/servicios.json` y `hu-w01.json`) | Local: Prometheus (perfil `observabilidad`) |
|---|---|---|
| Disponibilidad de un micro | `run.googleapis.com/request_count` por `service_name`: peticiones sin `response_code_class="5xx"` / total | `http_requests_total` (`prometheus-fastapi-instrumentator`): `status!="5xx"` / total |
| Latencia media de un micro | `run.googleapis.com/request_latencies` (distribución): media = suma / conteo, con `ALIGN_DELTA` en la ventana; p95 como referencia | `rate(http_request_duration_seconds_sum[v]) / rate(..._count[v])`; p95 con `histogram_quantile` |
| Consolidado del sistema (lo que vive el usuario) | Las métricas del API Gateway: `serviceruntime.googleapis.com/api/request_count` y `request_latencies`, sumando los dos gateways (usuarios y socios) | Las métricas de Envoy (`/stats/prometheus` del admin, que se agrega a `observabilidad/prometheus.yml`) |
| ¿Responde ahora? | `/health` de cada micro (ya en el plan) | Igual |
| APIs externas | `/circuitos` del ACL (ya en el plan) | Igual |

**Elección de la fuente:** `METRICAS_FUENTE` = `monitoring` (staging), `prometheus` (local) o `ninguna` (CI y
pruebas). Con `ninguna`, la salud sale solo de `/health`. Son dos clientes pequeños en `salud.py`, sin puertos y
adaptadores (regla 3). La librería `google-cloud-monitoring` se importa solo con `monitoring`.

**Caché:** el resultado se guarda 60 s por ventana en memoria, para no consultar Monitoring en cada petición. Las
lecturas de la API de Monitoring entran en su capa gratuita con ese volumen.

**Permisos:** la cuenta de runtime del BFF necesita `roles/monitoring.viewer` (`infra/servicios.tf`).

### Fórmulas

1. **Disponibilidad del micro** (ventana `v`) = peticiones sin 5xx / peticiones. Los 4xx cuentan como disponibles,
   porque son errores del que llama. Si no hubo tráfico (escala a cero), se muestra «sin tráfico», no 100 %.
2. **Puntaje de latencia** = 100 si la media ≤ el objetivo del micro, 0 si llega al doble del objetivo, y lineal entre
   los dos.
3. **Salud del micro** (0–100):
   - Si `/health` no responde: **0**.
   - Si responde: **70 % × disponibilidad + 30 % × puntaje de latencia**.
   - Sin tráfico: 100 si `/health` responde.
   - Los 70/30 son configurables.
4. **Salud general del sistema** = Σ(peso × salud del micro) / Σ(peso de los micros presentes). Un micro que aún no
   existe (por ejemplo UNDER antes de la fase C) aparece como «no disponible» y su peso se reparte entre los demás.
5. **Disponibilidad y latencia media consolidadas** = las del gateway en la ventana (el borde), no un promedio de los
   micros. Una petición del usuario pasa por varios micros, y promediarlos no daría lo que el usuario vive.
6. **Salud de cada API** (Cotización y Emisión) = promedio de dos partes: la salud media de sus micros (con la fórmula
   del punto 3) y la de sus APIs externas (circuito closed 100, half 50, opened 0):

   | API | Micros | APIs externas |
   |---|---|---|
   | Cotización | BFF, AUTH, ACL, RISK y RATING | open-finance y open-data |
   | Emisión | BFF, AUTH, POLICY, UNDER y PAYMENTS | firma, KYC y pasarela |

7. **Colores**, iguales para todo: verde ≥ 90, amarillo ≥ 70 y rojo < 70.

Salvedad documentada: el estado del circuito es por instancia del ACL.

### `services/bff/app/salud.json` (versionado; fuente en `contracts/salud.json`, copiado a la imagen como `fuentes.json`)

**Pesos propuestos** (suman 100), según lo crítico que es cada micro para vender una póliza:

| Micro | Peso | Por qué | Objetivo de latencia media |
|---|---|---|---|
| `bff` | **20** | Única puerta de entrada (usuarios y socios): si cae, cae todo. | 500 ms (EC010: la oferta ≤ 500 ms) |
| `auth` | **15** | Consentimiento obligatorio antes de consultar (regla 7), sesiones, tokens y presupuesto de socios: sin él no se cotiza ni se emite. | 120 ms (EC008, por dependencia) |
| `acl-worker` | **15** | Único contacto con los proveedores (regla 2): fuentes, firma, KYC y pasarela. | 300 ms (bajo el corte duro de 700 ms) |
| `rating` | **12** | Genera la oferta: sin él no hay cotización. | 120 ms (EC008) |
| `policy` | **12** | Propuesta, firma y emisión: sin él no hay venta. | 300 ms |
| `risk` | **10** | Perfil y score en el camino de la cotización. Pesa algo menos porque RATING tiene el score en caché (bloque 2 de W01). | 120 ms (EC008) |
| `under` | **6** | Decide la emisión, pero en segundo plano (Cloud Tasks), fuera del camino síncrono. | 300 ms |
| `payments` | **6** | Cobra en segundo plano. Un cobro pendiente no tumba la venta (W03-3). | 400 ms (incluye la pasarela por el ACL) |
| `consolidador-fuentes` | **4** | Concilia fuentes degradadas en segundo plano. Si cae, se pierde frescura, no ventas. | 1000 ms |

**Criterio de los pesos:**
- Camino síncrono del usuario (bff, auth, acl, rating, risk) más el núcleo de la venta (policy): 84 %.
- Lo asíncrono (under, payments, consolidador): 16 %.
- Los stubs no entran como micros: son las «APIs externas» y cuentan por sus circuitos en la salud de cada API.

**Objetivos de latencia:** son de **media** y salen de los umbrales p95 de los escenarios. Se ajustan con las
mediciones de staging.

**Configuración en el mismo archivo:**
- Ventanas permitidas: `["15m", "1h", "24h", "7d"]`, con `"1h"` por defecto.
- Los 70/30 de la fórmula.
- Los umbrales de color.
- Las APIs con sus micros y sus APIs externas.
- Una prueba en el CI verifica que los pesos suman 100 y que cada micro existe en `.github/servicios.json`.

### Quién ve qué

| Vista | Qué muestra | Dónde |
|---|---|---|
| **Socio** (`GET /v1/estado?ventana=`) | Barra de **salud general** (%), **disponibilidad y latencia media consolidadas**, y Cotización y Emisión con su color. **Sin nombres de micros internos.** | Pestaña «Estado» del portal |
| **BACKOFFICE** (GraphQL `estadoSistema(ventana)`, contrato aditivo) | Todo lo del socio, más una tabla por micro: peso, salud, disponibilidad, latencia media, p95, `/health` y «sin tráfico», más el estado de cada API externa | Pantalla «Estado del sistema» en la web (inicio del operador) y el panel nuevo «Salud del sistema» en el Grafana del equipo (`servicios.json`, mismas consultas de Monitoring) |

## Bloque 4 · Firma por enlace (fase B; ACL, `stubs/firma`, POLICY)

Es aditivo: la firma síncrona del CLIENTE de W03 queda igual.
- **ACL:** `PuertoFirmaElectronica` suma `solicitar_firma(hash, referencia) → url_firma`. Recibe el aviso del
  proveedor en `POST /firma/eventos` y lo reenvía a POLICY. Mantiene el circuito y los 700 ms. Con un segundo
  adaptador se cumple EC031.
- **`stubs/firma`:** `POST /v1/solicitudes` devuelve `url_firma`. `GET /firmar/{referencia}` es una página mínima con
  «Firmar», que al confirmar avisa al ACL. Conserva sus modos de falla.
- **POLICY:**
  - `POST /propuestas/{id}/solicitud-firma` guarda la solicitud.
  - `POST /eventos/firma-completada` registra la evidencia append-only con `actor=TITULAR` y `canal=SOCIO`, y agenda
    la emisión igual que la firma de W03.
  - Migración aditiva, en el siguiente número libre de POLICY.

## Bloque 5 · Comisiones (fase C, POLICY)

- **Migración aditiva en POLICY:**
  - En `propuestas`: `canal` (DIRECTO|SOCIO, con DIRECTO por defecto), `socio_id` y `tasa_comision`, congelada al
    aceptar.
  - `comisiones`: append-only, con `id`, `socio_id`, `poliza_id`, `cobro_id` **único** (idempotencia), `prima`,
    `tasa`, `comision`, `moneda` y `causada_en`.
- **Cuándo se causa:** al recibir `cobro.estado-cambiado` = cobrado (suscripción W03-7 del bloque 4 de W03) para una
  póliza con `canal=SOCIO`, se inserta `comision = prima × tasa` redondeada según la moneda (utilidades de dinero de
  rating/risk). Un rechazo no causa nada.
- **`GET /socios/{id}/ventas?desde=&hasta=`:** propuestas por estado, pólizas emitidas, primas cobradas y comisiones
  por mes. Lo consume `GET /v1/ventas`.
- POLICY sigue siendo el escritor único. El pago al socio queda fuera.

## Bloque 6 · Gateway e infraestructura (infra-engineer, fase A)

**Envoy (`infra/gateway/envoy.yaml`):**
- Prefijo `/v1` con plazo de 1,5 s, porque la cotización suma una reserva en AUTH a la cadena de W01.
- `jwt_authn` con `remote_jwks` de AUTH, que exime token, jwks, docs, openapi y la página de firma del dummy.
- Filtro `cors`.
- Ajustar `scripts/validar_gateway.py`.

**GCP:**
- `openapi-socios.yaml` (Swagger 2.0) con JWT, `allowCors` y plazos.
- API, config y gateway de socios detrás de `var.crear_gateway_socios`.
- Secreto `clave-firma-socios`.
- La URL del JWKS y la audiencia en `.github/servicios.json`.
- `roles/monitoring.viewer` para la cuenta del BFF y `METRICAS_FUENTE=monitoring` en su entorno.
- Panel «Salud del sistema» en `observabilidad/gcp/dashboards/servicios.json`.
- En local: `observabilidad/prometheus.yml` recoge también las métricas del admin de Envoy, y el BFF lleva
  `METRICAS_FUENTE=prometheus` en compose.
- `infra/README.md`.

**Despliegue:** solo por el pipeline al llegar a `main` (ADR-09). Nada de `apply` a mano.

## Bloque 7 · Frontend: portal de socios (solventa-frontend-web)

**Pantallas** con los ids de Figma que pasará Felipe, en `pantallas.ts`, `historia: 'HU-W07'`, protegidas por la
sesión de socio:
- `/socios/ingreso` (`IngresoSocio.vue`).
- `/socios/panel` (`PanelSocio.vue`), con seis pestañas:

| Pestaña | Contenido |
|---|---|
| Credenciales | Las 4 claves (clave larga y token corto por entorno): mostrar, copiar, generar token y rotar. |
| Estado | **Barra de porcentaje de la salud general** (con su color), **disponibilidad y latencia media consolidadas**, selector de ventana (15 min, 1 h, 24 h, 7 d), y Cotización y Emisión con su color. Sin nombres de micros internos. |
| Consumo | Cotizaciones contra el presupuesto (asignadas, consumidas y restantes), llamadas por API y serie diaria. |
| Ventas y comisiones | Propuestas por estado, pólizas emitidas, conversión de cotización a póliza, primas cobradas y comisión causada por mes. |
| Documentación y pruebas | Swagger embebido con el token de producción, con aviso de que cotizar descuenta presupuesto. |
| Modo prueba | Swagger embebido con el token de PRUEBAS, el distintivo «respuestas simuladas» y la tabla de documentos de prueba. |

**Pantalla de BACKOFFICE «Estado del sistema»:**
- Va en el inicio del operador (`HomeOperador.vue`) o como pantalla propia.
- Muestra la misma barra general, más la tabla por micro (peso, salud, disponibilidad, latencia media y p95) y las
  APIs externas.
- Lee `estadoSistema(ventana)` por GraphQL.

**Detalles:**
- `SwaggerSocios.vue` usa `swagger-ui-dist` en versión fija, sin CDN, e inyecta el token con `requestInterceptor`.
- `useSesionSocio.ts` guarda el token en memoria.
- El cliente REST usa la copia versionada de `socios-v1.yaml`.
- El enlace de `IngresoConRol.vue:114-120` se habilita.
- Los gráficos siguen la skill de dataviz.

## Documentos que actualiza cada bloque

| Bloque | Documentos |
|---|---|
| 1 | `contracts/CHANGELOG.md`, `contracts/README.md` |
| 2 | `services/auth/README.md`, `docs/autenticacion-usuarios.md` (§1 y §8) |
| 3 | `services/bff/README.md` |
| 4 y 5 | `services/policy/README.md`, `services/acl-worker/README.md`, `docs/plan-completar-hu-w03.md` (W03-8 y W03-9 con el canal socio; corregir el estado de los bloques 1 y 2) |
| 6 | `infra/README.md`, `README.md` raíz |
| 7 | `solventa-frontend-web/docs/plan-frontend.md` y su `plan-completar-hu-w07.md` |
| Cierre | Este plan, `CLAUDE.md` y `docs/evidencias/HU-W07-<fecha>.md` |

## Lo que hace Felipe a mano

- Pasar los ids de las pantallas W07 de Figma.
- Crear `clave-firma-socios` en Secret Manager de staging.
- Dar de alta el socio demo con `crear-socio`.

## Verificación

1. **`pytest`:**
   - AUTH: reserva atómica con concurrencia, ES256 contra JWKS, rotación y el consumo de todas las APIs registrado.
   - BFF: 429 solo en cotizaciones; aceptar sin límite; un socio no ve propuestas ajenas (404); sin consentimiento
     no hay cotización; salud con micros ausentes; dummy determinista.
   - Salud (con series falsas de Monitoring y Prometheus):
     - disponibilidad sin 5xx, con los 4xx contando como disponibles;
     - «sin tráfico»;
     - puntaje de latencia en el objetivo, entre el objetivo y el doble, y por encima del doble;
     - micro caído = 0;
     - suma ponderada con un micro ausente y su peso repartido;
     - colores;
     - `salud.json`: los pesos suman 100 y los micros existen;
     - el socio no recibe nombres de micros;
     - `estadoSistema` solo para BACKOFFICE.
   - POLICY: comisión idempotente por `cobro_id`, sin comisión en rechazados ni en `canal=DIRECTO`, y tasa
     congelada aunque cambie en AUTH.
   - ACL: firma por enlace y caso EC031.
2. **Migraciones:** `verificar-migraciones.sh auth` y `policy`, con `downgrade`.
3. **En vivo** (`docker compose --profile servicios up -d --build --wait`, más `crear-socio`), contra Envoy `:8080`:
   - **Fase A:** token → consentimiento → cotización COMPLETA → aceptar → propuesta ACEPTADA. Con un stub de fuentes
     `caido`, la oferta sale PRELIMINAR y aceptarla da error. Agotar el presupuesto da 429. Sin token, 401 del gateway.
   - **Salud** (con `--profile observabilidad`):
     - generar tráfico con k6;
     - comprobar que la latencia media y la disponibilidad de `/v1/estado` coinciden con las de Prometheus;
     - bajar `rating` y ver caer la barra en su peso (12 %) y Cotización en rojo;
     - poner el stub de open-data en `lento` y ver bajar el puntaje de latencia del ACL.
   - **Modo prueba:** el flujo completo hasta EMITIDA, sin que ningún stub reciba llamadas (se ve en sus logs).
   - **Fase B:** solicitud de firma → abrir `urlFirma` → la propuesta pasa a EN_EMISION.
   - **Fase C:** EMITIDA con su PDF. Un cobro `cobrado` crea una comisión y repetir el evento no la duplica.
4. **Newman:** `tests/newman/socios-w07.json`, como humo en el CI.
5. **Validadores:** `validar_gateway.py`, `validar_contratos.py` (deriva del OpenAPI) y `validar_servicios.py`.
6. **Frontend:** Vitest y Cypress (ingreso → token → «Try it out» en modo prueba → ventas y consumo).
7. **Evidencia:** `docs/evidencias/HU-W07-<fecha>.md` con números locales, aclarando que no son de staging.

## Pendiente

- Los ids de Figma.
- Coordinar con quien lleve W03 los bloques 4 y 5 de este plan, que tocan POLICY y el ACL, y el orden de las
  migraciones de POLICY.
- La versión del contrato: W07 y el plan de almacenamiento piden la siguiente libre.
- `docs/plan-almacenamiento-archivos.md` usa el puerto 8008, que W03 reserva para `under`. Corregirlo al retomarlo
  (por ejemplo, 8009).
