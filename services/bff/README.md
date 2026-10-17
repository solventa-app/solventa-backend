# bff

BFF GraphQL de Solventa (web y móvil).

## Responsabilidad

BFF único de GraphQL para web y móvil (ADR-01): un solo Cloud Run con el esquema servido en dos rutas por canal
(`/graphql/web` y `/graphql/app`; el canal viaja en el contexto). **No tiene lógica de negocio**: orquesta AUTH,
ACL Worker, RISK y RATING y traduce al contrato de `contracts/graphql/schema.graphql`.

- Historias: HU-W01 (T-W01-7), HU-M02 (autenticación) y HU-W05 (KAN-28, cobros del back-office). La API REST B2B `/v1` llega con HU-W07 (Sprint 2).
- Regla: nunca devuelve un 5xx crudo al cliente; traduce la degradación a oferta preliminar o error con reintento (CA-W01-04/05).
  Los errores internos se enmascaran ("Unexpected error."); solo `ErrorPublico` muestra su mensaje.

## Contrato

`app/esquema.py` (Strawberry) es espejo 1:1 del contrato. `tests/test_esquema_contrato.py` lo compara con
`contracts/graphql/schema.graphql` en ambos sentidos: si difieren, se corrige el código, no el contrato.

| Operación | Estado |
|---|---|
| `registrarConsentimiento` / `revocarConsentimiento` | AUTH (tabla append-only en PostgreSQL, ver `services/auth/README.md`) |
| `solicitarOferta` | Completo: ver el flujo abajo |
| `oferta(id)` | Devuelve `null`: RATING no persiste ofertas todavía |
| `contactarAsesor` | **MOCK**: solo confirma (no hay servicio de asesores) |
| `cobros(polizaId, estado)` / `crearCobro` / `reintentarCobro` | Completas (KAN-28), sobre PAYMENTS: solo BACKOFFICE y **siempre con token**. `crearCobro` recibe `idIntentoCobro` del cliente (idempotencia) y devuelve `Cobro` o `ErrorCobro`. `Usuario.nombre` solo lo traen `iniciarSesion` y `yo` (el propio; nunca el JWT). Los cobros traen `idTransaccion` (referencia de la pasarela) y `cliente { id nombre }`: el BFF pide los nombres a AUTH en lotes de 200 y, si AUTH falla, los entrega sin nombre. El reintento concilia con la pasarela antes de cobrar (ver [`docs/cobros-conciliacion.md`](../../docs/cobros-conciliacion.md)) |
| `iniciarSesion`, `yo`, `usuarios`, `crearUsuario`, `cambiarRolUsuario`, `suspenderUsuario` | Completas (HU-M02), sobre AUTH |

## Autenticación y permisos (HU-M02)

`app/autenticacion_usuarios.py` verifica el JWT (`Authorization: Bearer <token>`) que emite AUTH, y cada resolutor de
`app/esquema.py` aplica su regla con `exigir_rol`. La matriz de permisos, las decisiones y el paso a paso con ejemplos
están en [`docs/autenticacion-usuarios.md`](../../docs/autenticacion-usuarios.md). Con `AUTENTICACION_USUARIOS=opcional`
(valor actual) las operaciones de antes siguen funcionando sin token; la gestión de usuarios y `yo` siempre lo exigen.

## Flujo de `solicitarOferta` (`app/servicio_oferta.py`)

1. AUTH: consentimiento vigente **antes** de consultar nada (regla #7). Si no lo hay → `ErrorOferta` sin reintento.
2. `cliente_id` opaco = HMAC-SHA256 del documento con `CLIENTE_ID_CLAVE` (`app/identificadores.py`).
   **REWIRE:** cuando AUTH tenga la tabla de identidad (T-W01-4 / JWT del Sprint 2), el `cliente_id` lo resolverá AUTH.
3. ACL Worker: las 5 fuentes en paralelo. El ACL ya degrada solo (caché o valor por defecto); si el ACL mismo no
   responde, la fuente va `degradado` y la oferta sale PRELIMINAR.
4. RISK escribe el perfil y devuelve `operation_time`; RATING calcula la oferta con lectura causal (D-02).
5. Cualquier falla de RISK/RATING → `ErrorOferta` con reintento; `valorCredito` inválido → sin reintento.
   Sin historial crediticio (RATING responde 422 `datos_insuficientes`, CA-W01-05) → `ErrorOferta` con reintento.

Antes del paso 1 se valida la solicitud (mismos límites que el formulario W01-01): documento con al menos 6 dígitos
y máximo 20 caracteres, `valorCredito` decimal mayor que 0, `plazoMeses` de 1 a 360 y `direccionInmueble` no vacía
(máximo 200 caracteres). Si algo falla → `ErrorOferta` sin reintento, sin tocar AUTH ni las fuentes.

`plazoMeses` y `direccionInmueble` viajan a RATING, que los usa en la prima (ver `services/rating/README.md`).
Nunca se loguean (tampoco el documento).

## Variables de entorno

| Variable | Default local | Nota |
|---|---|---|
| `AUTH_URL`, `ACL_URL`, `RISK_URL`, `RATING_URL`, `PAYMENTS_URL` | `http://localhost:800x` | En Cloud Run las fija Terraform desde `llama` en `.github/servicios.json` |
| `ACL_TIMEOUT_SEGUNDOS` | `1.0` | El ACL corta a los 700 ms; el margen cubre la red |
| `SERVICIOS_TIMEOUT_SEGUNDOS` | `2.0` | AUTH, RISK y RATING |
| `CLIENTE_ID_CLAVE` | `solo-local-no-secreta` | En la nube, secreto `clave-cliente-id`; el BFF no arranca con el valor local ni con el marcador de Terraform |
| `AUTENTICACION_SERVICIOS` | `ninguna` | `id_token` en Cloud Run: token de identidad del servidor de metadatos, audience = nombre del servicio |
| `JWT_SECRETO` | `solo-local-no-secreta-jwt-solventa-0001` | El mismo que AUTH (HS256). En la nube, secreto `clave-jwt`; no arranca con el valor local, el marcador ni uno de menos de 32 bytes |
| `AUTENTICACION_USUARIOS` | `opcional` | `opcional` u `obligatoria` (ver `docs/autenticacion-usuarios.md`) |

## Operación

- Puerto local: `8000`. GraphQL: `POST /graphql/web` y `/graphql/app` (GraphiQL en el navegador). Salud: `GET /health`. Métricas: `GET /metrics`.
- Pruebas: `pip install -r requirements.txt && pip install -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta). Sin red: dobles en `tests/fakes.py`.
- Humo de punta a punta (el mismo que corre el CI):
  ```bash
  docker compose --profile servicios up -d --build --wait
  python scripts/humo_bff.py          # desde la raíz del repo
  ```
  Inicia sesión como BACKOFFICE, crea un ASESOR y, con su token, recorre consentimiento → oferta COMPLETA →
  PRELIMINAR (Open Finance lento) → `ErrorOferta` tras revocar; además comprueba el actor y un rechazo por rol.
- Imagen: `docker build -t solventa-bff .`
