# Plan para completar HU-W07 · Backend y frontend

> **Estado: borrador (2026-10-22), sin aprobar ni empezar.** Se guardó para retomarlo más tarde. Ningún bloque tiene
> código todavía. Al retomarlo: revisar que `develop` no haya cambiado lo que dice «Lo que hay hoy» y cerrar los puntos
> de «Pendiente al retomar».
>
> Jira: **KAN-30** (13 pts, Sprint 2). Rama: `feat/KAN-30-socios-api` desde `develop` en los dos repos. El plan del
> frontend vivirá en `solventa-frontend-web/docs/plan-completar-hu-w07.md`.

**HU-W07 Alta de socios de distribución e integración (API-First — Journey 1).** Como socio de distribución, quiero
consumir las APIs de cotización/emisión con credenciales y cuotas aisladas, para vender seguros embebidos directamente
en la aplicación de mi negocio.

El API Gateway autentica las llamadas de los socios con tokens de acceso y valida sus cuotas. Procesa la solicitud
delegando la validación al Core y devuelve la respuesta al ecosistema del socio.

**Flujo (texto de Jira):**
1. El usuario entra por el registro de socios (el enlace está en el login principal).
2. El usuario llega al dashboard de socios con estos cambios:
   - Las credenciales muestran 4 claves, 2 productivas y 2 de pruebas. El par de claves responde a los tokens que
     pide el gateway.
   - El estado promedia la salud de los microservicios con la salud de cada API externa, y muestra el color de salud
     de cada API.
   - El consumo muestra estadísticas por cada API.
3. Implementar el sandbox de prueba para las APIs, con mocks.
4. Crear o limitar el Swagger para los socios de distribución (investigar).

## Contexto

Solventa revende a los socios de distribución el acceso que ya tiene a las **5 fuentes de consulta** de
`contracts/fuentes.json`: `cuentas-bancarias`, `historial-crediticio`, `afiliacion-pila`, `camara-comercio` y
`antecedentes-judiciales`. Hoy son mocks detrás del ACL Worker.

**Alcance real (aclarado con Felipe el 2026-10-22):** los socios **no** cotizan ni emiten pólizas. Es un prototipo
en el que hay cosas que pertenecen a Solventa y cosas que Solventa solo usa.

**Decisiones tomadas con Felipe:**

| # | Tema | Decisión |
|---|---|---|
| W07-1 | Alta | La hace solo BACKOFFICE. Por ahora es una CLI o semilla sobre las tablas; el endpoint de alta queda para después. Crea el socio, sus claves y un **presupuesto de peticiones por API** que se va consumiendo, como tokens. El consumo es durable y trazable. |
| W07-2 | Portal | Se entra con la clave del socio, no con un usuario de persona. |
| W07-3 | Credenciales | 4 claves, un par por entorno (PRODUCCIÓN y PRUEBAS). Cada par tiene una clave de larga duración y un token de acceso corto. |
| W07-4 | Consumo | Se persiste en Postgres, porque Redis se destruye cada noche con el apagado de costos. |
| W07-5 | Modo prueba | Lo responde un dummy interno de Solventa. Los stubs representan a los proveedores reales. |
| W07-6 | Swagger | Hay un Swagger exclusivo para socios, embebido en su portal para documentarse y probar. |
| W07-7 | Alcance | El plan cubre backend y frontend. |

**Lo que hay hoy (revisado en `develop` el 2026-10-22):**
- Nada de socios: no hay rol, ni tablas, ni rutas REST, ni JWKS.
- El gateway (Envoy local / GCP API Gateway) solo reenvía 4 rutas GraphQL al BFF, con corte de 0,7 s.
- AUTH firma JWT HS256 con un secreto compartido.
- El ACL expone `POST /fuentes/consultar` y `GET /circuitos`.
- En el frontend, el enlace al portal de socios está deshabilitado (`IngresoConRol.vue:114-120`).

## Análisis: ¿limitar APIs o Swagger exclusivo? → API exclusiva para socios

1. **Superficie propia `/v1`.** Es una sub-app FastAPI montada en el BFF (`app_socios`), el canal B2B que ya anunciaba
   `services/bff/README.md:11`. Tiene su propio `openapi_url=/v1/openapi.json` y `docs_url=/v1/docs`. El socio nunca
   ve GraphQL ni las rutas internas.
2. **Contrato primero.** `contracts/openapi/socios-v1.yaml` es la fuente de verdad. El CI compara lo que genera la
   sub-app contra ese archivo y falla si hay deriva.
3. **Gateway propio.** Una segunda API en GCP API Gateway (`solventa-socios-api`, con su gateway) con
   `infra/gateway/openapi-socios.yaml`, que solo publica `/v1/...`. Cada API de GCP admite una sola configuración, y
   separarlas evita mezclar seguridad y plazos con el canal de usuarios. Las primeras 2 M llamadas al mes no se
   cobran.

**Tokens en el borde:**
- `POST /v1/token` cambia el `client_id` y la clave larga por un JWT **ES256 de 15 min**.
- El gateway lo valida con `x-google-issuer` / `x-google-jwks_uri`. En local, Envoy usa `jwt_authn`.
- Solo los tokens de socio pasan a ES256. Los de usuario siguen en HS256, para no tocar W01.

**Cuotas:** GCP API Gateway cuenta las cuotas por proyecto consumidor de la API key, no por socio, así que no puede
aislarlas en un solo proyecto. Además, Felipe pide un presupuesto durable, no un límite por minuto. Por eso:
- El gateway autentica.
- El presupuesto se valida y descuenta de forma atómica en el Postgres de AUTH, el escritor único de la identidad de
  los socios.
- Si el presupuesto se agota, se responde **429**.
- Queda documentado que con Apigee la cuota sí viviría en el borde.

## Bloque 1 · Contrato (contract-keeper)

**`contracts/openapi/socios-v1.yaml`** (OpenAPI 3.1), endpoints:
- Públicos, sin token: `POST /v1/token`, `GET /v1/.well-known/jwks.json`, `GET /v1/docs` y `GET /v1/openapi.json`.
- Con token:
  - `POST /v1/consentimientos` y `POST /v1/consentimientos/{id}/revocar`. Se mantiene la regla 7: el socio registra el
    consentimiento de su cliente antes de consultar.
  - `POST /v1/fuentes/{fuente}/consultas`, con `consentimiento_id` y `documento`. Devuelve `datos`, `capturado_en`,
    `degradado` y `restantes`.
  - `GET /v1/estado`: salud por API.
  - `GET /v1/consumo?desde=&hasta=`: por API, asignadas, consumidas, restantes y serie diaria.
  - `GET /v1/credenciales` (las 4 claves, enmascaradas y con opción de mostrar) y
    `POST /v1/credenciales/{entorno}/rotar`.

**Errores:** con el formato de `ErrorPublico`. Nunca un 5xx crudo (regla 4): si el proveedor falla, la respuesta lleva
`degradado: true`. El 429 lleva `Retry-After` y el código `PRESUPUESTO_AGOTADO`.

**Bitácora y validación:**
- `contracts/CHANGELOG.md`, versión aditiva siguiente a la vigente (v0.11 si nada cambió antes).
- La sección OpenAPI en `contracts/README.md`.
- Extender `scripts/validar_contratos.py`.

## Bloque 2 · AUTH: socios, claves, presupuesto y tokens (backend-builder)

**Migración `0006_socios.py`**, solo expandir, con `downgrade` probado:
- `socios`: `id`, `nombre`, `estado` ACTIVO|SUSPENDIDO y fechas.
- `credenciales_socio`:
  - columnas `socio_id`, `entorno` PRODUCCION|PRUEBAS, `client_id` único y `clave_cifrada`;
  - `clave_cifrada` usa `CifradorCampos` de `services/auth/app/cifrado.py` con AAD `socio_id`, para que el portal
    pueda mostrarla; la verificación compara en tiempo constante;
  - `creada_en` y `revocada_en`.
- `presupuestos_socio`: PK (`socio_id`, `entorno`, `fuente`), con `asignadas` y `consumidas`.
- `consumos_socio`: append-only, con el trigger de 0004/0005. Guarda `socio_id`, `entorno`, `fuente`, `resultado`
  (RESERVADA|EXITOSA|DEGRADADA|LIBERADA|RECHAZADA), `latencia_ms` y `ocurrido_en`. **Sin documento ni datos
  personales.**
- Se agrega `SOCIO` a `ck_consentimientos_actor_rol`.

**`app/socios.py`:**
- Alta y rotación de claves.
- `reservar(socio, entorno, fuente)`: `UPDATE … SET consumidas = consumidas + 1 WHERE consumidas < asignadas
  RETURNING` más la fila RESERVADA.
- `cerrar(reserva, resultado, latencia)`: si es LIBERADA, devuelve la unidad. Se cobra la respuesta exitosa o
  degradada, y se devuelve la que falla por un error nuestro.
- `consumo(socio, rango)`: agregado por día y fuente.

**Tokens:**
- `app/tokens_socio.py` firma en ES256 con la clave privada de `SOCIOS_CLAVE_FIRMA`. En local va en PEM por variable
  de entorno; en la nube, en Secret Manager `clave-firma-socios`.
- Claims: `sub=socio_id`, `rol=SOCIO`, `entorno`, `scope=fuentes:consultar`, `iss`, `aud`, `exp` (15 min) y `jti`.
- El JWKS sale de la misma clave.

**Endpoints internos nuevos en `app/main.py`:**
- `POST /socios/tokens` (401 genérico; reutiliza el bloqueo por intentos de `sesiones.py`)
- `GET /.well-known/jwks.json`
- `GET /socios/{id}/credenciales`
- `POST /socios/{id}/credenciales/{entorno}/rotar`
- `POST /socios/{id}/consumos/reservar`
- `POST /socios/consumos/{id}/cerrar`
- `GET /socios/{id}/consumo`
- `POST /consentimientos` acepta `actor.rol=SOCIO`.

**CLI `app/crear_socio.py`** (sigue el patrón de `app/crear_usuario.py`, tarea `crear-socio` en compose):
- Es idempotente.
- Crea el socio y sus 4 claves, y las imprime una sola vez.
- Asigna el presupuesto por fuente y entorno, con `--presupuesto 1000 --presupuesto-pruebas 500` por defecto. También
  sirve para recargar un presupuesto.

## Bloque 3 · BFF: la API `/v1` y el modo prueba (backend-builder)

**Nuevo paquete `services/bff/app/socios/`**, montado en `main.py`:

| Archivo | Qué hace |
|---|---|
| `api.py` | La sub-app FastAPI con sus rutas. |
| `autenticacion.py` | Verifica el JWT ES256 con el JWKS de AUTH (caché de 10 min). Es defensa en profundidad, porque el gateway ya validó. Lee el token de `X-Forwarded-Authorization` o de `Authorization`, igual que `extraer_cabecera_autorizacion`. |
| `consultas.py` | El flujo de producción, descrito abajo. |
| `sandbox.py` | El entorno PRUEBAS, con el dummy interno. |
| `salud.py` | La salud por API, descrita abajo. |

**Flujo de producción (`consultas.py`):**
1. Valida el consentimiento y el titular. Reutiliza la lógica de `servicio_oferta.py`: `obtener_consentimiento` y el
   HMAC del documento.
2. Reserva el presupuesto en AUTH. Si no queda, responde 429.
3. Llama al ACL `POST /fuentes/consultar` con `PuertoAcl` de `app/clientes.py`.
4. Cierra la reserva con el resultado y la latencia.

**Modo prueba: un dummy interno, distinto de los stubs (`sandbox.py`)**

| | Producción (token PRODUCCIÓN) | Modo prueba (token PRUEBAS) |
|---|---|---|
| Camino | BFF → AUTH → ACL → stubs `open-finance` / `open-data` | BFF → **dummy interno** (no sale de Solventa) |
| Qué representa | La realidad: los stubs son los proveedores externos, por eso son microservicios aparte | Un componente de Solventa, al estilo Mockito/WireMock, para que el socio integre sin riesgo |
| Presupuesto | El de producción | El de PRUEBAS |

- **Qué es el dummy:** un módulo dentro del BFF, no un servicio más. No añade imagen, ni Cloud Run, ni costo. Tampoco
  choca con la regla 2, porque no habla con ningún proveedor.
- **Mapeos declarativos.** Las respuestas viven en `services/bff/app/socios/dummy/<fuente>.json`, con el formato
  `documento de prueba → respuesta`, como los mappings de WireMock. Las pruebas las validan contra los esquemas de
  `socios-v1.yaml`.
- **Casos:** cada documento de prueba documentado simula uno: sano, degradado, sin datos, consentimiento revocado y
  presupuesto agotado.
- **Consentimiento:** se simula en memoria y nunca se escribe en AUTH.
- **Alternativa descartada:** Prism, un mock generado desde el OpenAPI. Sería otro contenedor y otro servicio en Cloud
  Run para algo que el BFF resuelve con un JSON.

**Salud por API (`salud.py`):**
- Para cada una de las 5 APIs calcula `promedio(salud de microservicios, salud de la API externa)`.
- La salud de los microservicios sale del BFF, de `/health` de AUTH y de `/health` del ACL: 100 si responde y 0 si no.
- La salud de la API externa sale del circuito de su proveedor, en `GET /circuitos` del ACL. La relación fuente →
  proveedor ya existe en `fuentes.json`. Valores: closed 100, half-opened 50 y opened 0.
- Colores: verde ≥ 80, amarillo ≥ 50 y rojo < 50.
- Salvedad documentada: el estado del circuito es por instancia del ACL.

**Logs:** `socio_id` opaco y `fuente` en los logs estructurados, nunca el documento (regla 6).

**Configuración:** las URLs y la audiencia van en `app/configuracion.py`. Los timeouts del ACL no cambian.

## Bloque 4 · Gateway e infraestructura (infra-engineer)

**Envoy local (`infra/gateway/envoy.yaml`):**
- Rutas por prefijo `/v1`, con plazo de **1,5 s**. El corte de 0,7 s de HA-LAT-004 es del canal de usuarios, y aquí
  hay llamadas a AUTH más el ACL (700 ms).
- Filtro `jwt_authn` con `remote_jwks` hacia `auth:8000/.well-known/jwks.json`. Quedan exentos el token, el JWKS, los
  docs y el openapi.
- Filtro `cors` para el origen de la web, porque el Swagger embebido llama a `/v1` (ver bloque 5).
- Ajustar `scripts/validar_gateway.py`.

**GCP:**
- `infra/gateway/openapi-socios.yaml` (Swagger 2.0, que es lo que pide API Gateway), con `securityDefinitions` JWT y
  `x-google-endpoints`/`allowCors`.
- El JWKS sale por la misma ruta pública del gateway hacia el BFF, y de ahí a AUTH.
- En `infra/gateway.tf`: la API, la configuración y el gateway de socios, detrás de `var.crear_gateway_socios`, más
  la salida `gateway_socios_url`.
- En `.github/servicios.json`: el secreto `clave-firma-socios` (AUTH), la URL del JWKS y la audiencia (BFF).
- `infra/README.md` con las variables nuevas.

**Despliegue:** se aplica solo por el pipeline al llegar a `main` (ADR-09). Nada de `terraform apply` a mano. El
guardia de `revisar_plan.py` no debería ver destrucciones.

## Bloque 5 · Frontend: el portal de socios (solventa-frontend-web)

Antes de construirlo, revisar en Figma `Solventa-web-Final` si existen pantallas W07 y usar sus ids.

**Pantallas** en `apps/web/src/router/pantallas.ts`, con `historia: 'HU-W07'` y `sprint: 2`. Las protege la sesión
del socio, no un `Rol` de usuario:
- `/socios/ingreso`: `IngresoSocio.vue`, con el `client_id` y la clave de producción.
- `/socios/panel`: `PanelSocio.vue`, con cinco pestañas:
  - **Credenciales:** las 4 claves de PRODUCCIÓN y PRUEBAS, cada una con su clave larga y su token corto. Se pueden
    mostrar, copiar, generar el token y rotar.
  - **Estado:** las 5 APIs con su color.
  - **Consumo:** por API, asignadas, consumidas y restantes, con barra y serie diaria (siguiendo la skill de dataviz).
  - **Documentación y pruebas (producción):** el Swagger exclusivo de socios embebido en la pantalla.
  - **APIs en modo prueba:** el mismo Swagger embebido, preautorizado con el token de PRUEBAS; responde el dummy.
    Incluye la tabla de documentos de prueba con su caso.

**Swagger embebido:**
- Componente `SwaggerSocios.vue` con `swagger-ui-dist`, en versión fija y empaquetado sin CDN.
- Carga `/v1/openapi.json` desde el gateway.
- Inyecta el token corto del entorno de la pestaña con `requestInterceptor`. Así «Try it out» funciona sin pegar
  claves, y el socio prueba y se documenta en el mismo lugar.
- En producción, un aviso antes de ejecutar: cada «Try it out» descuenta del presupuesto real.
- En modo prueba, un distintivo visible: «Modo prueba · respuestas simuladas».
- **Requisito:** CORS en `/v1` para el origen de la web. Va en el filtro `cors` de Envoy, en `allowCors` del gateway de
  GCP y en `CORSMiddleware` de la sub-app, con los orígenes por configuración.

**Cliente y sesión:**
- `composables/useSesionSocio.ts` guarda el token en memoria y lo renueva con `/v1/token`.
- El cliente REST `api/socios.ts` usa la copia versionada de `socios-v1.yaml`, igual que con el contrato GraphQL.

**Otros cambios:**
- Habilitar el enlace de `IngresoConRol.vue:114-120` hacia `/socios/ingreso`.
- Mocks de desarrollo en `src/mocks`.
- Actualizar `docs/plan-frontend.md` y crear `docs/plan-completar-hu-w07.md`, gemelo de este.

## Documentos que actualiza cada bloque

| Bloque | Documentos |
|---|---|
| 1 | `contracts/CHANGELOG.md`, `contracts/README.md` |
| 2 | `services/auth/README.md`, `docs/autenticacion-usuarios.md` (§1 y §8: el gateway valida el JWT de socios) |
| 3 | `services/bff/README.md` |
| 4 | `infra/README.md`, `README.md` raíz |
| 5 | `solventa-frontend-web/docs/plan-frontend.md` y su `plan-completar-hu-w07.md` |
| Cierre | Este plan (estado), `CLAUDE.md` (estado), `docs/evidencias/HU-W07-<fecha>.md` |

## Lo que hace Felipe a mano

- Crear el secreto `clave-firma-socios` en Secret Manager de staging.
- Dar de alta el socio demo con la CLI `crear-socio`, mientras no exista el endpoint de alta.

## Verificación

1. **Pruebas:**
   - `pytest` en `services/auth`: la reserva es atómica (la concurrencia no supera `asignadas`), liberar devuelve la
     unidad, el ES256 se valida con el JWKS y la rotación invalida la clave anterior.
   - `pytest` en `services/bff`: 429 con el presupuesto agotado, el sandbox es determinista, los colores de salud
     salen bien con circuitos falsos, y sin consentimiento responde 403 sin llamar al ACL.
2. **Migración:** `bash scripts/verificar-migraciones.sh auth`, con `downgrade`.
3. **En vivo:**
   - `docker compose --profile servicios up -d --build --wait` y `docker compose run --rm crear-socio`.
   - Contra Envoy `:8080`: token → consentimiento → consulta de las 5 fuentes, en producción y en pruebas.
   - Bajar un stub a `caido` y ver `degradado: true` y el color en `/v1/estado`.
   - Agotar el presupuesto y obtener 429.
   - Llamar sin token y obtener 401 del gateway.
   - Reiniciar Redis y comprobar que el consumo no cambia.
4. **Newman:** la colección `tests/newman/socios-w07.json` con ese flujo, que entra al CI como humo.
5. **Validadores:** `scripts/validar_gateway.py`, `validar_contratos.py` (deriva del OpenAPI) y `validar_servicios.py`.
6. **Frontend:**
   - Vitest de las vistas.
   - Cypress E2E de ingreso → panel → generar token → «Try it out» en modo prueba (responde el dummy y no se toca
     ningún stub) → ver subir el consumo de PRUEBAS.
   - Comprobar en los logs de los stubs que una llamada en modo prueba no les llega.
7. **Evidencia:** `docs/evidencias/HU-W07-<fecha>.md` con números reales locales, aclarando que no son de staging.

## Pendiente al retomar

- Confirmar con Felipe el plan completo. Se guardó antes de aprobarlo.
- Confirmar qué significa «el usuario ingresa por el registro de socios» si el alta solo la hace BACKOFFICE. Supuesto
  de este plan: el enlace del login lleva al ingreso del portal (W07-1 y W07-2).
- El texto de Jira dice «cotización/emisión», pero el alcance acordado son las 5 fuentes. Conviene ajustar la
  historia en KAN-30.
- Revisar en Figma si hay pantallas W07.
- Dejar definido el endpoint de alta de socios desde BACKOFFICE (W07-1 lo deja para después).
