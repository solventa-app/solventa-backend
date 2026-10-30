# tests/newman/

Pruebas de integración del **BFF GraphQL** (HU-W01 y HU-M02, KAN-24; HU-W05, KAN-28) con [Newman](https://github.com/postmanlabs/newman),
el ejecutor de línea de comandos de Postman. Sirven para verificar el contrato visible por el frontend contra el
stack real de `docker compose`, sin escribir código.

| Archivo | Contenido |
|---|---|
| `solventa-bff.postman_collection.json` | Colección: 61 peticiones y 141 aserciones, en 10 carpetas |
| `solventa-local.postman_environment.json` | Entorno local: `bff_url`, `auth_url`, `acl_url`, `payments_url`, `stub_open_finance_url`, `stub_pasarela_url` y las credenciales **solo locales** del BACKOFFICE (`usuario_backoffice`, `clave_backoffice`) |

## Qué cubre

| Carpeta | Qué verifica | Criterio / escenario |
|---|---|---|
| 0. Salud | `/health` y `/metrics` del BFF | — |
| 1. Autenticación y usuarios | Login del BACKOFFICE, `yo`, alta de ASESOR y de CLIENTE con nombre (alias únicos por corrida; cualquier rol puede tener nombre; cada usuario ve el suyo al iniciar sesión y la lista de usuarios no expone nombres), alias repetido o con forma de correo, clave errónea y login de cada rol; guarda los tokens | HU-M02 |
| 2. Consentimiento | `registrarConsentimiento` como ASESOR, y en AUTH que el actor es ese ASESOR | CA-W01-02, CA-W01-11 |
| 3. Oferta con proveedores sanos | Oferta COMPLETA en los canales web y móvil, 5 fuentes, prima puntual, dinero como texto; `oferta(id)` devuelve la oferta guardada | CA-W01-03, CA-W01-06 |
| 4. Degradación | Con Open Finance lento: oferta PRELIMINAR, rango de prima, fuentes en CACHE, sin error; con un documento sin caché, el historial crediticio no llega y sale `ErrorOferta` con reintento; luego restaura el stub y espera a que cierren los circuitos | CA-W01-04, CA-W01-05, EC009 / EC010 |
| 5. Errores controlados | Consentimiento inexistente, valor inválido, plazo inválido y consulta inválida: siempre HTTP 200 o 4xx, nunca 5xx | CA-W01-01, CA-W01-05 |
| 6. Contacto y revocación | `contactarAsesor`, `revocarConsentimiento`, y que la oferta se rechace después de revocar | CA-W01-07, CA-W01-08 |
| 7. Autorización por rol | CLIENTE no lista usuarios ni cobros ni crea cobros, BACKOFFICE no cotiza, ASESOR no crea usuarios, token alterado y sin token | HU-M02 |
| 8. Suspensión | El BACKOFFICE suspende al CLIENTE y su login se rechaza; suspender un id inexistente | HU-M02 |
| 9. Cobros y conciliación | Pasarela lenta: `crearCobro` en el BFF deja el cobro `pendiente` en PAYMENTS aunque la pasarela sí cobró; `cobros(polizaId)` lo muestra; `reintentarCobro` concilia y queda `COBRADO` con **la misma referencia de la pasarela** (sin doble cobro); doble clic sin efecto; los cobros llevan `clienteId` y la tabla muestra el nombre del cliente; reenviar el mismo `idIntentoCobro` devuelve el mismo cobro y con otro monto da `ErrorCobro`; un rechazo con su motivo que se reintenta hasta 3 veces (cada vez con referencia nueva) y al cuarto da "requiere otro medio de pago"; `cobros` sin token exige sesión | CA-W05-01, CA-W05-02 |

**Autenticación:** la colección usa por defecto el token del ASESOR (`Bearer {{token_asesor}}`, en la autenticación de
la colección). Las peticiones que prueban otro rol lo indican en su propia autenticación, y las que no son del BFF
(salud, stub, ACL, AUTH) van sin token.

## Cómo correrlas

Requisitos: Docker y Node.js 18 o superior. Newman no se instala en el repo.

```bash
# 1. Levantar el stack (si 5432 o 6379 están ocupados, antepón POSTGRES_PORT=5433 REDIS_PORT=6380)
docker compose --profile servicios up -d --build --wait

# 2. Correr la colección (versión fijada: 6.2.2)
npx --yes newman@6.2.2 run tests/newman/solventa-bff.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json

# 3. Apagar
docker compose --profile servicios down -v
```

Para dejar un reporte (no se versiona; crea la carpeta `resultados/` y no la subas al repo):

```bash
npx --yes newman@6.2.2 run tests/newman/solventa-bff.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json \
  -r cli,json --reporter-json-export tests/newman/resultados/bff.json
```

## Notas de uso

- **El orden importa.** Las carpetas comparten variables de colección (tokens, ids y alias de la carpeta 1,
  `consentimiento_id`, `oferta_id`) y la carpeta 4 necesita que la 3 haya dejado datos en caché.
- **Requiere el BACKOFFICE local.** La carpeta 1 inicia sesión con el usuario que crea la tarea `crear-backoffice` del
  compose; sin él, nada de lo demás tiene token. Corre la colección completa, no peticiones sueltas.
- **Es repetible.** Los alias del ASESOR y del CLIENTE cambian en cada corrida (solo letras: un alias con 6 o más
  dígitos seguidos se rechaza por parecer un documento). La carpeta 4 restaura el stub a `sano` y espera 10 s a que los circuitos del ACL Worker
  cierren, así que se puede correr varias veces seguidas.
- **Solo contra local.** La carpeta 4 manipula el stub de Open Finance, la 9 manipula el stub de la pasarela y lee
  PAYMENTS directamente, y la 2 consulta AUTH directamente; en staging nada de eso es accesible. Para staging, omite esas peticiones y cambia `bff_url` y las credenciales del
  BACKOFFICE en el entorno (nunca las subas al repo).
- **Datos ficticios.** El documento `CC-NEWMAN-100001` no es una persona real. No pongas datos reales en la colección.
- **Los umbrales de tiempo son un piso**, medidos en un portátil con proveedores simulados (regla 9 de
  `CLAUDE.md`). No reemplazan las pruebas de carga con k6 de EC003 / EC004 (`tests/k6/`).

## Colección corta de HU-W01 (`hu-w01.postman_collection.json`)

Recorrido mínimo del caso insignia (oferta completa, degradada, timeout duro y consentimiento revocado), el mismo
de `docs/evidencias/flujo-prueba-local-hu-w01.md` y `scripts/humo_bff.py`. Útil para abrirlo en la app de Postman;
para la verificación completa usa `solventa-bff.postman_collection.json`.

## Colección de HU-W07 (`socios-w07.postman_collection.json`)

API REST de los socios de distribución (KAN-30) por el gateway: 55 peticiones y 52 aserciones en 9 carpetas.

| Carpeta | Qué verifica |
|---|---|
| 0. Acceso | Token ES256 de producción y de pruebas, clave errada (401), JWKS público, el contrato, y el 401 de Envoy sin token o con uno inventado, como `ErrorPublico` |
| 1. Consentimiento y cotización | Consentimiento a nombre del socio, oferta COMPLETA con el presupuesto restante, el consentimiento de otro socio (404) y datos inválidos (422) |
| 2. Propuesta, identidad y emisión | Aceptar con el contacto del titular, otro socio no la ve (404), el código por correo (del stub de notificaciones), código errado (409) y emitir **sin la URL del enlace** |
| 3. El titular firma y paga | La página de W03 con el token del enlace: firma, pasarela y `confirmarPago` → EMITIDA |
| 4. Seguimiento y ventas | Propuesta EMITIDA, póliza, PDF, otro socio (404), ventas con la comisión y el aviso al titular |
| 5. Presupuesto | El socio 2 (3 cotizaciones) cotiza hasta el 429 con `Retry-After` |
| 6. Modo prueba | Con el token de PRUEBAS, hasta EMITIDA con el código `000000` y sin tocar los micros |
| 7. Cuenta | Consumo, credenciales (403 con el token de pruebas) y estado del sistema |
| 8. Convivencia y BACKOFFICE | `/v1/graphql/web` sigue igual; `nombreSocio` y `estadoSistema` para el BACKOFFICE |

Requiere el stack completo: los socios demo los crea `crear-socios-demo` al levantarlo, con claves **solo locales**, que
están en el entorno (`socio1_*`, `socio2_*`). Se puede repetir: la carpeta 5 cotiza hasta agotar, aunque ya esté
agotado de una corrida anterior.

```bash
npx --yes newman@6.2.2 run tests/newman/socios-w07.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json
```

## Colección de HU-W04 (`hu-w04.postman_collection.json`)

Mesa de siniestros por el gateway (KAN-27, KAN-248): 26 peticiones y 52 aserciones en 8 carpetas.

| Carpeta | Qué verifica |
|---|---|
| 0. Preparación | Reinicio de la semilla en CLAIMS (solo local), sesión de BACKOFFICE y un ASESOR para probar permisos |
| 1. Bandeja | Los 10 casos, 7 en cola, orden por notificación y filtro por estado |
| 2. Detalle | Póliza real de POLICY con su `ramo`, 5 evidencias con URL firmada que descarga la imagen del almacenamiento, caso inexistente `null` y peritos con la fecha límite del informe |
| 3. Derivar | Deja el caso EN_REVISION; ya tiene perito, texto de más de 500 y perito inexistente como `ErrorSiniestro` |
| 4. Aprobar | Indemnización, método y plazo; ya resuelto; la orden queda `PENDIENTE_DISPERSION` en PAYMENTS; un caso derivado conserva su perito |
| 5. Rechazar | Motivo y comentario; un caso ya aprobado no se rechaza |
| 6. Autorización y versión | ASESOR sin permiso, sin token pide sesión, `/v1/graphql/web` igual que `/graphql/web`, mensajes en inglés |
| 7. Cierre | Vuelve a la semilla |

Requiere la semilla (`docker compose --profile servicios run --rm sembrar-siniestros`) y CLAIMS con `DEMO_REINICIAR=true` (así está en
compose). Variables propias de la colección: `claims_url` (`http://localhost:8012`).

```bash
npx --yes newman@6.2.2 run tests/newman/hu-w04.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json
```

## Colección de HU-W03 (`hu-w03.postman_collection.json`)

Suscripción, firma y póliza (KAN-26, plan de HU-W03 bloque 6). A diferencia de la colección del BFF, **todo entra por el
API Gateway** (`gateway_url`, `:8080`), como el front; los stubs y el ACL se tocan directo solo para fijar modos de falla.
Requiere el stack completo (`docker compose --profile servicios up -d --build --wait`) y corre **en orden**: las carpetas
comparten variables.

| Carpeta | Qué verifica | Criterio / escenario |
|---|---|---|
| 0. Preparación | El BACKOFFICE crea un CLIENTE titular **con correo** (sin contacto el aviso de la póliza se omite, W03-18), otro CLIENTE ajeno y un ASESOR (alias únicos por corrida) | — |
| 1. Camino feliz | Solicitar → `aceptarOferta` (la prima es la de la oferta; aceptar dos veces da la misma propuesta) → `documentoFirma` → firma rechazada sin autorizar el cobro o con una versión vieja (la propuesta sigue `ACEPTADA`) → firma → firmar otra vez da la misma evidencia → espera a `EMITIDA` → `poliza(id)` → PDF por el gateway → el aviso salió una sola vez | CA-W03-01 a 03, 07, 08 |
| 2. Autorización | Otro CLIENTE no ve ni acepta ni firma ni descarga lo ajeno; un ASESOR no firma pero sí descarga el PDF; BACKOFFICE 403 y sin token 401 en el PDF | W03-8 (decisión), CA-W03-03 |
| 3. Errores controlados | Oferta, propuesta y póliza inexistentes: error tipado o `null`, nunca 5xx | regla 4 |
| 4. Degradación | Open Finance lento (la `PRELIMINAR` no se acepta); KYC caído (`EN_REVISION`, sin póliza); pasarela que rechaza (`PAGO_RECHAZADO`, sin póliza); pago rechazado que se recupera (lo reabre el back-office: el cliente no puede y la póliza se emite con un solo aviso); firma caída y firma lenta, **por el gateway** (`ErrorFirma` reintentable con HTTP 200, nunca un 5xx, y al volver el reintento emite); notificaciones caídas (la póliza se emite y el PDF se descarga, el aviso sale solo al volver). Cada escenario restaura el stub y espera a que cierren los circuitos | CA-W03-05 y 06, EC009 / EC010 |
| 5. Registro del cliente y su contacto | W03-27 y W03-34: un CLIENTE sin correo, sin documento o con el documento de otro no se crea; `yo` trae el documento **enmascarado** (`•••• 1234`) y `tieneContacto`; `actualizarContacto` (correo inválido, sin datos, el titular cambia su correo, ASESOR, BACKOFFICE, sin token) y que el contacto no se puede leer por GraphQL. Los avisos por SMS y sin contacto (W03-18) ya no se prueban aquí: con el correo obligatorio ya no se puede crear ese cliente, y los cubren las pruebas de POLICY | CA-W03-08, CA-W03-09 |
| 6. Flujos v0.18 (F-B9) | **Asesor:** aceptar sin el contacto del titular da `ErrorAceptacion`; con él, emitir antes del código da `ErrorEmision`; el código llega por correo (sin el correo en claro), uno errado da `CODIGO_INVALIDO` con los intentos que quedan y el bueno `IDENTIDAD_VERIFICADA`; «Emitir» manda el enlace (`#t=` en el fragmento, la URL nunca vuelve al asesor); reenviarlo anula el anterior y un token inventado no revela nada; imagen sin fondo `IMAGEN_INVALIDA`, imagen válida (PUT a la URL firmada con `tests/newman/archivos/`) `FIRMADA`; pago aprobado con la pasarela caída al confirmar (`PASARELA_NO_DISPONIBLE`, 1 fallo) y luego conciliado sin doble cobro; avisos al titular (con el PDF) y al asesor. **Titular:** al abrir su cuenta con su documento ve la póliza que le vendió el asesor (sin `nombreTitular`); otro CLIENTE no. **Cliente con sesión:** sin enlace, firma `NOMBRE`, rechazo `FONDOS_INSUFICIENTES` (no cuenta como falla), pasarela caída 3 veces (1, 2 y `REQUIERE_OTRA_TARJETA`) y luego paga; otro CLIENTE no ve su página; listas por rol. **Tercer modo:** otro CLIENTE firma con `TRAZO` | CA-W03-02 a 08, W03-20 a W03-35 |

```bash
npx --yes newman@6.2.2 run tests/newman/hu-w03.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json
```

- Las esperas a la emisión son un sondeo (`postman.setNextRequest`, hasta 40 consultas cada 500 ms) porque la emisión corre en
  segundo plano; el número de consultas queda en el nombre de la prueba.
- **Solo contra local** la carpeta 4 (los stubs y el ACL son privados en staging). Las carpetas 0 a 3 sirven contra staging
  cambiando `gateway_url` y las credenciales del BACKOFFICE en el entorno (nunca las subas al repo).
- Datos ficticios: los documentos `CC-NEWMAN-W03-*` y los nombres `Cliente Newman *` no son personas reales.
- **Estado:** corrida completa contra el stack local el 2026-10-28 sin fallos (198 peticiones y 372 aserciones, con la carpeta 6;
  informe en `docs/evidencias/HU-W03-2026-10-28.md`). La carpeta 6 es **solo local** (usa los stubs de pasarela y
  notificaciones). Antes: corrida del 2026-10-24 sin fallos (111 peticiones ejecutadas y 204 aserciones; las 105 definidas más los
  sondeos); resultados y hallazgos en `docs/evidencias/HU-W03-newman-EC009-EC010-2026-10-24.md`, `CA-W03-05-firma-2026-10-24.md` y `CA-W03-06-reapertura-2026-10-24.md`.
  Si el `:8080` o el `:5432` están ocupados, ver «Cómo correrlas».
