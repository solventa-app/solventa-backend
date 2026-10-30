# Plan para completar HU-W04 · Backend real de la mesa de siniestros (KAN-27)

> **Estado: hecho en código y en local (2026-10-29).** Backend en `develop` (PR #378) y la web conectada al BFF real
> (front #91 y #94); el dummy se retiró en el PR #384. Lo que queda está en «Pendiente», al final de «Estado de ejecución».
>
> Aprobado por Felipe el 2026-10-28 y ejecutado en la rama `feat/KAN-27-backend-siniestros`. Reemplaza el
> borrador anterior de este archivo (sin versionar), que proponía el puerto 8010, URLs HMAC interinas y otra numeración.

## Contexto

El front de HU-W04 está casi listo, pero consume un **dummy** (`stubs/siniestros`, puerto 8107, JSON en memoria, sin
base ni token). Se creó mientras faltaban prerrequisitos que **ya están en `develop`**:

- el almacenamiento (KAN-250), con URLs firmadas V4 y el propietario `siniestro` previsto;
- POLICY con pólizas reales;
- `exigir_rol` / `SOLO_BACKOFFICE` en el BFF;
- el replica set de MongoDB.

Ahora se construye el **backend real** (`claims`) respetando los planes en curso (W03 bloque 9, W07 y almacenamiento),
los diagramas de arquitectura y una regla de Felipe: **en lo posible no borrar ni cambiar el backend existente**. Lo
que sí se toca está listado en «Qué toca de lo existente».

El borrador sin versionar `docs/plan-completar-hu-w04.md` quedó desactualizado: el puerto 8010 ya está ocupado, dice
que el almacenamiento es un borrador, usa el interino HMAC y tiene la numeración vieja. Este plan lo reemplaza y se
versiona en el primer commit.

**Por qué MongoDB** (revisado a pedido de Felipe):

- el diagrama de componentes v3 dice «MongoDB (Dominio Siniestros)»;
- el de despliegue pone «Cloud Run CLAIMS (Siniestros)» → «MongoDB Atlas M0 · Dom. Riesgo + Siniestros», y Postgres
  solo para Identidad y Pólizas/Pagos;
- HA-LAT-003 declara que soporta HU-W04;
- `CLAUDE.md` dice lo mismo.

El dummy no tenía base de datos (decisión del plan del front de W04), así que no condiciona nada.

## Ramas y commits

- **Backend:** `feat/KAN-27-backend-siniestros` desde `develop` (45f140b). Las ramas KAN-27 que existen ya están
  fusionadas y no se reutilizan.
- **Front:** `feat/KAN-27-siniestros-backend-real` desde `develop` (7caaf2d).
- **Commits evolutivos**, al menos uno por bloque, en Conventional Commits en español con la clave de la tarea, por
  ejemplo `feat(claims): KAN-242 …` o `feat(payments): KAN-244 …`.
- **Autoría solo de Felipe**, sin coautorías ni menciones a asistentes.
- **Push** a la rama del feature y **un PR por repo** hacia `develop` al final. Abrirlo lo decide Felipe.
- **Sin reintegración `main → develop` ahora** (decisión de Felipe). El hotfix #331 queda solo en `main`, y la próxima
  liberación lo resuelve.

**Los PR se encolan; no se espera a que se aprueben** (la autorización hacia `develop` llega más tarde):

- **Todo el feature va en una sola rama por repo,** con commits sucesivos. Ningún bloque espera a que se fusione otro:
  cada bloque se apoya en el commit anterior de la misma rama.
- **El visto bueno de Frans sobre PAYMENTS (W04-6) no bloquea.** `POST /indemnizaciones` se construye en la misma rama y
  Frans lo revisa en el PR. Si lo rechaza, se revierte ese commit y la aprobación vuelve a «solo en claims + evento», sin
  tocar los demás bloques.
- **La rama del front (solo documentación) no depende de la del backend:** su PR se encola por separado.
- **Mientras el PR espera,** otros PR pueden llegar a `develop`. Antes de abrirlo se **mezcla `develop` en la rama**, sin
  rebase porque la rama ya está publicada. Si otro PR tomó la versión del contrato o la migración `0009` de PAYMENTS, se
  **renumera** en ese momento.
- **Push** de la rama al cerrar cada bloque, para que el trabajo no quede solo en local.

## Decisiones (Felipe, 2026-10-28)

| # | Decisión | Estado |
|---|---|---|
| W04-1 | Servicio nuevo **`claims`**, escritor único de siniestros, peritos, evidencias, asignaciones y resoluciones. **Puerto 8012** (el 8011 lo tomó `avisos` en `develop` el 2026-10-28; primero se había elegido el 8011) | Cerrada |
| W04-2 | **MongoDB con colecciones referenciadas.** La base `claims` va en el mismo replica set o cluster de RISK. Se escribe en la primaria; la bandeja y el detalle se leen de la **secundaria** (`secondaryPreferred` + `maxTimeMS`, como `services/rating/app/application/lector_perfiles.py`). Tras una mutación se devuelve lo escrito, sin releer la réplica | Cerrada |
| W04-3 | **Pólizas reales con un script nuevo de POLICY** (`python -m app.sembrar_demo`): 10 propuestas y pólizas `EMITIDA` con prima, cobertura y plazo cifrados, ids fijos e idempotente. Después, la semilla de `claims` apunta a esas pólizas | Cerrada |
| W04-4 | **`ramo` lo pone el BFF** (`RAMO_POLIZA`, porque hay un solo producto). Lo agrega a `Poliza` en el contrato como cambio aditivo. **POLICY no se modifica**. El BFF resuelve la póliza del siniestro con el `GET /polizas/{id}` que ya existe en POLICY | Cerrada |
| W04-5 | **Evidencias en el almacenamiento.** Tipos nuevos `siniestro-foto` y `siniestro-documento` en el catálogo: propietario `siniestro`; suben `servicio:claims` (y `CLIENTE` con HU-M04); leen `BACKOFFICE` y `servicio:claims`. El BFF entrega la URL firmada (300 s) pidiéndola **como el BACKOFFICE que consulta**, para que quede auditada. En staging el bucket sigue apagado: encenderlo es una decisión de costo | Cerrada |
| W04-6 | **Orden de pago en PAYMENTS**, sin dispersión, que sigue en el Sprint 3. `POST /indemnizaciones` es idempotente por `siniestro_id`. `claims` la pide **antes** de guardar la aprobación; si PAYMENTS falla, se devuelve `ErrorSiniestro` con reintento y no queda nada a medias. **Requiere el visto bueno de Frans y cambiar W05-3** | Cerrada (sujeta a Frans) |
| W04-7 | **SLA:** `venceDictamenEn` = notificado + `SLA_DICTAMEN_DIAS_HABILES` (2). `vencimientoInforme` = derivación + `tiempoRespuestaDias` hábiles, a las 17:00 de Colombia y sin festivos (la regla del dummy) | Propuesta |
| W04-8 | **Indemnización** = suma reclamada, acotada a la cobertura de la póliza | Propuesta |
| W04-9 | **Eventos `siniestro.aprobado`, `.rechazado` y `.derivado` validados al publicar** (`jsonschema` en ejecución; los esquemas se copian a la imagen con un script verificado en el CI). Van al bus de log | Propuesta |
| W04-10 | **`/v1/graphql/{web,app,movil}` en el BFF.** Las rutas actuales siguen igual, como alias. Convive con W07, porque la API REST de socios usará `/v1/<recurso>`, sin `graphql`, registrada después | Propuesta |
| W04-11 | Todas las operaciones con `exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)` | Cerrada |
| W04-12 | **Contrato v0.21, aditivo.** Se reutilizan `Poliza`, `Monto` y `EstadoPoliza` | Cerrada |
| W04-13 | **Ejecutada el 2026-10-29: el dummy se retiró** (decisión de Felipe, tras pasar la web al BFF; los datos se movieron a `services/claims/semilla/`). Decisión original: **el dummy se conserva, sin borrarlo.** Deja de usarse y se marca obsoleto en su README, en `docs/convenciones.md` y en el encabezado de `contracts/graphql/siniestros.graphql`. Se retira en una tarea aparte cuando el equipo lo decida | Cerrada |
| W04-14 | **Geoetiqueta desde el EXIF:** no se trabaja ahora y se anota en el plan del front. El backend guarda `geoetiquetada`, `latitud` y `longitud` por evidencia (por ahora los fija la semilla) | Cerrada |
| W04-15 | **Reinicio para las E2E:** `claims` `POST /_demo/reiniciar`, solo con `DEMO_REINICIAR=true` (compose; nunca en staging) | Propuesta |
| W04-16 | **Sin `terraform apply`.** Atlas (usuario `claims`, secreto `mongo-uri-claims`) y Cloud Run se aplican por el pipeline | Cerrada |

## Modelo de datos (MongoDB `claims`, colecciones referenciadas)

Sigue las convenciones de RISK:

- migraciones idempotentes en `services/claims/migrations/versiones/NNNN_*.py`, con `python -m migrations` y la
  colección `_migraciones`;
- `$jsonSchema` en cada colección e índices con nombre;
- ids `uuid4` como texto;
- fechas `*_en` en UTC;
- dinero como texto decimal más la moneda;
- datos personales en `*_cifrado`, con AES-GCM (se copia `CifradorCampos` de RISK).

Las referencias a otros servicios van solo por id.

| Colección | Campos | Índices / reglas |
|---|---|---|
| `siniestros` | `_id`; `radicado` (`SIN-AAAA-NNNN`, con contador en `contadores`); `poliza_id` (→ POLICY `polizas.id`); `estado` (PENDIENTE, EN_REVISION, APROBADO, RECHAZADO); `asegurado_nombre_cifrado`; `asegurado_documento_cifrado`; `cobertura_aplicada`; `entidad_acreedora`; `ocurrido_en`; `notificado_en`; `ubicacion_cifrada`; `descripcion_cifrada`; `suma_reclamada {valor, moneda}`; `vence_dictamen_en`; `creado_en`; `actualizado_en` | Único `radicado`; `ix_estado_notificado`; `ix_poliza` |
| `peritos` | `_id`, `nombre`, `ciudad`, `red`, `registro`, `tiempo_respuesta_dias`, `activo`, `creado_en` | Único `registro` |
| `evidencias_siniestro` (intermedia siniestro ↔ archivo) | `_id`, `siniestro_id`, `archivo_id` (→ almacenamiento), `tipo` (FOTO o DOCUMENTO), `titulo`, `nombre_archivo`, `registrada_en`, `geoetiquetada`, `latitud`, `longitud`, `nota`, `creado_en` | Único (`siniestro_id`, `archivo_id`) |
| `verificaciones_siniestro` | `_id`, `siniestro_id`, `orden`, `descripcion`, `cumple`, `creado_en` | Único (`siniestro_id`, `orden`) |
| `asignaciones_perito` (intermedia siniestro ↔ perito) | `_id`, `siniestro_id`, `perito_id`, `instrucciones_cifradas`, `asignada_en`, `vence_informe_en`, `actor_usuario_id` | **Único `siniestro_id`**; append-only |
| `resoluciones_siniestro` | `_id`, `siniestro_id`, `tipo` (APROBACION o RECHAZO), `motivo`, `comentario_cifrado`, `notificacion`, `via_recurso`, `indemnizacion`, `metodo_transferencia`, `plazo_dispersion`, `orden_pago_id` (→ PAYMENTS), `resuelta_en`, `actor_usuario_id` | **Único `siniestro_id`**; append-only |

## MongoDB: qué existe y qué se necesita

**Hoy hay una sola base, `risk`, con dos colecciones:**

- `perfiles` (índice `cliente_id` + `perfil_version`, datos cifrados): la escribe RISK y la lee RATING desde la
  secundaria con un usuario de solo lectura (`mongo-uri-rating`);
- `_migraciones`: el registro de migraciones de RISK.

**Ninguna colección se reutiliza en `claims`.** El perfil de riesgo es de otro dominio, y la regla «nadie lee la base de
otro» impide leerlo. Si algún día un siniestro necesitara el riesgo del asegurado, se pediría a RISK por su API. La póliza
que enlaza el siniestro no está en Mongo: vive en Postgres, en POLICY.

**Prerrequisitos que ya existen y se reutilizan:**

- el replica set local `rs0` (`mongo1` y `mongo2`), necesario para leer de la secundaria;
- el cluster Atlas M0 de staging (`crear_mongo_atlas = true`);
- el patrón de migraciones y de lectura en la secundaria.

**Lo que se crea:**

- la base `claims`, con sus propias colecciones y su propio `_migraciones`;
- el usuario Atlas `claims`, con permiso solo sobre esa base, y el secreto `mongo-uri-claims`.

**Ojo:** Atlas M0 admite unas 100 operaciones por segundo para todo el cluster, compartido con RISK y RATING. Alcanza
para la mesa de un operador, pero limita la medición de EC011/EC012 en staging.

## Qué toca de lo existente (todo lo demás es código nuevo)

| Dónde | Cambio | Tipo |
|---|---|---|
| `contracts/graphql/schema.graphql` + `CHANGELOG.md` | Tipos de siniestros, consultas y mutaciones nuevas; `Poliza` suma `ramo` | Aditivo (v0.21) |
| `contracts/tipos-archivo.json` y su copia `services/almacenamiento/app/tipos-archivo.json` | Dos tipos nuevos de archivo. **Sin cambios de código** en el almacenamiento | Aditivo (datos) |
| `contracts/graphql/siniestros.graphql`, `stubs/siniestros/README.md`, `docs/convenciones.md` | Solo una nota de «obsoleto» | Documentación |
| `services/bff/app/esquema.py`, `clientes.py`, `configuracion.py`, `main.py`, `tests/fakes.py`, `tests/test_permisos.py` | Tipos y resolutores nuevos, cliente de `claims`, `CLAIMS_URL` y `RAMO_POLIZA`, y routers `/v1/graphql/*` **adicionales**. `Poliza` gana un campo resuelto en el BFF. Las rutas y operaciones actuales no cambian | Aditivo en archivos existentes |
| `services/policy` | **Solo un archivo nuevo**, `app/sembrar_demo.py`. Ningún archivo existente cambia | Nuevo |
| `services/payments` | Migración nueva `0009_indemnizaciones`, rutas nuevas en `app/main.py` y un módulo nuevo; README. **Es el servicio de Frans: necesita su visto bueno** | Aditivo en archivos existentes |
| `.github/servicios.json` | Entrada `claims` nueva; `bff.llama` suma `claims` | Aditivo |
| `docker-compose.yml` | Servicios nuevos `claims`, `migrar-claims`, `sembrar-polizas-demo` y `sembrar-siniestros`; `bff` suma `CLAIMS_URL` | Aditivo |
| `.github/workflows/ci.yml`, `.github/dependabot.yml` | Filtro y entradas de `claims` | Aditivo |
| `scripts/verificar-migraciones.sh` | Caso `claims` en el `case` | Aditivo |
| `infra/mongo-atlas.tf` | Usuario `claims` y versión del secreto `mongo-uri-claims` | Aditivo (Terraform, sin `apply`) |
| `infra/gateway/envoy.yaml`, `infra/gateway/openapi.yaml` | Rutas nuevas `/v1/graphql/*`; las actuales se conservan | Aditivo |
| `docs/`: `CLAUDE.md`, `arquitectura-backend.md`, `cobros-conciliacion.md`, `plan-completar-hu-w05.md` (W05-3), `plan-hu-w07-y-ajustes.md` (nota W04-10), `plan-completar-hu-w04.md` | Estado, `claims` en el Sprint 2 con el puerto 8012, indemnizaciones, W05-3 y la convivencia con `/v1` | Documentación |
| **Nada se borra en el backend** | El dummy se conserva (W04-13) | — |
| Front (`solventa-frontend-web`) | **Solo documentación** en `docs/plan-ejecucion-hu-w04-siniestros.md`. Los ajustes (que sí quitarán el uso del dummy y la copia de su SDL) se hacen después en ese repo | Documentación |

## Bloques (commits sucesivos en la rama)

| # | Clave | Qué entrega |
|---|---|---|
| 0 | KAN-249 | **Primer paso tras aprobar:** guardar una copia de este plan como referencia en `docs/plan-completar-hu-w04.md` del backend, reemplazando el borrador sin versionar. La nota W04-10 va en `docs/plan-hu-w07-y-ajustes.md` cuando ese plan llegue a `develop` (hoy vive solo en la rama `chore/KAN-30-plan-w07-y-ajustes`) |
| 1 | KAN-242 | **Contrato v0.21:** tipos de siniestros, `Poliza.ramo`, `siniestro.*.schema.json` y tipos `siniestro-foto` / `-documento` (con `scripts/sincronizar_tipos_archivo.py`). Esqueleto en el BFF para que pase la paridad (`tests/test_esquema_contrato.py`) |
| 2 | KAN-242 | **`services/claims`** con capas simples (regla 3): migraciones Mongo, repositorio con primaria y secundaria, `GET /siniestros?estado=`, `GET /siniestros/{id}`, `GET /siniestros/{id}/peritos`, `/health` y `/metrics`. Registro en `servicios.json` (8012, `migracion`, `MONGO_URI`, `llama: [almacenamiento, policy, payments]`), compose, CI, dependabot, `verificar-migraciones.sh` e `infra/mongo-atlas.tf` |
| 3 | KAN-242 | **Semillas:** POLICY `sembrar_demo` y `claims` `python -m app.sembrar`, que crea peritos, siniestros y verificaciones y **sube las 37 imágenes del dummy al almacenamiento** como `servicio:claims` (las copia a `services/claims/semilla/`). Incluye `/_demo/reiniciar` |
| 4 | KAN-244 | **Resolución:** aprobar, rechazar y derivar, con las reglas y los mensajes del dummy, el SLA y la indemnización. PAYMENTS `0009` + `POST/GET /indemnizaciones` en un commit propio, que Frans revisa en el PR y que se puede revertir solo |
| 5 | KAN-245 | **Eventos `siniestro.*`** validados al publicar, con la prueba de que uno inválido no se publica |
| 6 | KAN-243 | **`Evidencia.url`** firmada por el almacenamiento desde el BFF. Si falla, la evidencia sale «no disponible»; nunca un 5xx |
| 7 | KAN-246 | **BFF:** `ClienteClaimsHttp`, resolutores reales con `SOLO_BACKOFFICE`, la póliza resuelta con POLICY más `ramo`, y `/v1/graphql/{canal}` en `main.py`, Envoy y OpenAPI |
| 8 | KAN-247 | **Front, solo documentado:** en la rama del front se escribe una sección «Conexión al backend real» en `docs/plan-ejecucion-hu-w04-siniestros.md`, con los ajustes de la sección «Front» de este plan y la nota de la geoetiqueta. **No se cambia código del front en este feature**: se ejecuta después en ese repo |
| 9 | KAN-248 | **Pruebas:** pytest; Newman `hu-w04` por el gateway (sustituye al E2E de la web, que queda para cuando el front se conecte); `verificar-migraciones.sh` de `claims` y `payments`; k6 de lectura de la bandeja (EC011/EC012) con evidencia |
| 10 | KAN-249 | **Documentos y cierre:** README de `claims`, docs de la tabla anterior, `CLAUDE.md` y este plan con el estado final |

**Dependencias:**
- 0 → 1 → 2 → 3.
- 4, 5 y 6 van después del 3. La parte de PAYMENTS del 4 la revisa Frans en el PR, sin frenar la construcción.
- 7 va después de 4, 5 y 6.
- 8 va después del 7.
- 9 y 10 cierran.

**Qué se reutiliza** (no se reescribe):

- **De RISK:** `CifradorCampos` y su patrón de migraciones Mongo.
- **De RATING:** la lectura en la secundaria (`lector_perfiles.py`).
- **De POLICY:**
  - el cliente del almacenamiento (`cliente_almacenamiento.py`): actor `servicio:<nombre>` y reescritura
    `localhost:4443 → fake-gcs:4443`;
  - `autenticacion_servicios.py`;
  - `numero_de_poliza`.
- **De POLICY y PAYMENTS:** `BusEventosLog` y `construir_evento_*`.
- **Del BFF:** `exigir_rol` y `SOLO_BACKOFFICE`.
- **Del dummy:** las reglas, los mensajes y los datos de `repositorio.py` y `datos/*.json`.

## Front: qué está listo y qué cambia (revisado en `solventa-frontend-web` develop `7caaf2d`)

**Listo, sin cambios:**

- las 7 pantallas, el modal y las confirmaciones;
- las consultas y mutaciones (mismos nombres, argumentos y campos);
- la union se distingue por la forma de la respuesta;
- los mensajes de negocio se muestran tal cual;
- el router ya exige el rol BACKOFFICE;
- `ejecutar()` ya manda el JWT y traduce 502, 503 y 504;
- `<img :src="e.url">` acepta URLs firmadas absolutas;
- no hay CSP que lo bloquee.

**Ajustes:**

| # | Qué | Dónde |
|---|---|---|
| 1 | `SINIESTROS = { endpoint: '/v1/graphql/web', conToken: true }` | `composables/useSiniestros.ts:17` |
| 2 | Proxy `'/v1'` hacia el gateway; quitar `'/graphql/siniestros'` y `VITE_SINIESTROS_URL` | `apps/web/vite.config.ts`, `env.d.ts`, `.env.example` |
| 3 | Rewrite `/v1/:path*` hacia el gateway | `apps/web/vercel.json` |
| 4 | Importar los tipos de `graphql/tipos.ts` en lugar de `tipos-siniestros.ts`, en 6 archivos | `src/` |
| 5 | Quitar `siniestros.graphql` de `scripts/contrato.mjs`, de `codegen.ts`, de la exclusión de cobertura y del CI; borrar `contrato/siniestros.graphql` y `tipos-siniestros.ts` | front |
| 6 | La bandeja pide solo `id radicado estado asegurado { nombre } notificadoEn`, para no firmar unas 37 URLs en cada carga | `useSiniestros.ts` |
| 7 | Mocks en `*/v1/graphql/web`, registrados también en `mocks/browser.ts`, para que `dev:mock` funcione sin backend | `src/mocks/` |
| 8 | Los 8 specs E2E reinician con `POST http://localhost:8012/_demo/reiniciar` | `e2e/cypress/e2e/**/w04-*` |
| 9 | Nota de la geoetiqueta desde el EXIF (W04-14), pendiente | `docs/plan-ejecucion-hu-w04-siniestros.md` |

La URL firmada vence a los 300 s. Los 5 minutos cuentan para **descargar** la imagen, no para mirarla: el operador
puede quedarse lo que necesite en el detalle y aprobar, rechazar o derivar después. Lo que falla pasado ese tiempo es
**ampliar** una imagen (se abre en otra pestaña) o **bajar** hasta una que aún no cargaba (`loading="lazy"`). Queda
como el **ajuste 10** del plan del front, pendiente: al fallar, volver a consultar `siniestro(id)` una vez para recibir
URLs nuevas. El backend no cambia y no se sube el vencimiento.

## Estado de ejecución (2026-10-28)

Rama del backend `feat/KAN-27-backend-siniestros` (desde `develop` 45f140b) y del front
`feat/KAN-27-siniestros-backend-real` (desde `develop` 7caaf2d), ambas en GitHub y **sin PR** (se encolan).

| # | Bloque | Estado | Verificación |
|---|---|---|---|
| 0 | Plan versionado | Hecho | — |
| 1 | Contrato v0.21 | Hecho | Paridad del BFF y `validar_contratos.py` en verde |
| 2 | Servicio `claims` | Hecho | 20 pruebas; `verificar-migraciones.sh claims` (2 corridas, 2 migraciones); MongoDB rechaza un documento inválido |
| 3 | Semillas | Hecho | En vivo: 10 siniestros con pólizas reales de POLICY, 37 archivos `DISPONIBLE` en el almacenamiento, 0 datos personales en claro; la segunda corrida no duplica nada |
| 4 | Resolución + PAYMENTS | Hecho (PAYMENTS en un commit propio para Frans) | 117 pruebas de PAYMENTS y `verificar-migraciones.sh payments`; 8 aprobaciones simultáneas dejan 1 resolución y 1 orden |
| 5 | Eventos validados | Hecho | Un evento inválido no se publica; en vivo, el log solo lleva ids opacos |
| 6 y 7 | BFF y `/v1` | Hecho | 331 pruebas del BFF; `validar_gateway.py` (8 rutas); de punta a punta por Envoy con la imagen firmada descargada |
| 8 | Front documentado | Hecho | Sección «Conexión al backend real» en el plan del front; sin cambios de código |
| 9 | Pruebas | Hecho | Newman `hu-w04` 26 peticiones / 52 aserciones, dos corridas en verde; k6 EC011/EC012 local p95 28 ms y p99 32 ms ([evidencia](evidencias/EC011-EC012-W04-2026-10-28.md)) |
| 10 | Documentos | Hecho | README de `claims` y PAYMENTS, `arquitectura-backend.md`, `convenciones.md`, `cobros-conciliacion.md`, `plan-completar-hu-w05.md` (W05-3), README del dummy, `CLAUDE.md` |

**Desvíos frente al plan (decididos al ejecutar):**

- **La semilla de `claims` monta los datos del dummy** (`./stubs/siniestros/app/datos:/semilla:ro`) en vez de copiarlos
  a `services/claims/semilla/`: evita duplicar ~8 MB de imágenes y el dummy se conserva (W04-13). Si el dummy se retira,
  sus `datos/` deben moverse antes. **Hecho el 2026-10-29:** se movieron a `services/claims/semilla/` al retirar el dummy.
- **`contracts/graphql/siniestros.graphql` no lleva la nota de obsoleto en su encabezado**: la web verifica su copia byte
  a byte en el CI y cambiarlo rompería ese chequeo. La nota va en `CHANGELOG.md` (v0.21), el README del dummy y
  `convenciones.md`.
- **Se agregó el alcance `claims`** a `.github/workflows/pr-titulo.yml` y `convenciones.md`, y `verificar-migraciones.sh`
  generaliza el caso de MongoDB (`risk | claims`, mismo comportamiento para RISK).
- **El reinicio de la demo no toca las órdenes de PAYMENTS**: aprobar otra vez un caso reiniciado devuelve la misma
  orden (idempotente).
- **La nota W04-10 en `docs/plan-hu-w07-y-ajustes.md`** queda pendiente: ese plan no está en `develop` (vive en la rama
  `chore/KAN-30-plan-w07-y-ajustes`). Al integrarlo: la API REST de socios va en `/v1/<recurso>`, registrada después de
  `/v1/graphql/*`, y W07 conserva la versión v0.21 que le reservó el onboarding (W04 quedó en la v0.21).
- En este equipo el puerto 5432 lo usa un PostgreSQL local; el compose se levantó con `POSTGRES_PORT=55432`.

- **Al mezclar `develop` (2026-10-28) cambiaron dos números:** el puerto de `claims` pasó de 8011 a **8012**, porque
  `avisos` tomó el 8011, y el contrato de W04 pasó de v0.19 a **v0.21**, porque la entrada v0.20 del onboarding dejó la
  v0.19 reservada para HU-W07. La migración `0009` de PAYMENTS seguía libre.

**Pendiente:**

1. ~~Abrir los dos PR hacia `develop`~~: backend #378 y front #89 fusionados (2026-10-28). Sigue pendiente el visto
   bueno de Frans sobre el commit de PAYMENTS (c964110).
2. ~~Ejecutar los ajustes del front~~: 1 a 8 hechos en los PR del front #91 y #94 (2026-10-29), con los 39 E2E de W04
   contra el backend local. Quedan el 9 (geoetiqueta, punto 4) y el **10: renovar la URL firmada** de las evidencias
   cuando vence (ver «Front»).
3. Staging: liberación a `main`; el usuario de Atlas `claims` y su secreto salen por el pipeline; **las evidencias no
   funcionan en staging mientras `crear_almacenamiento = false`** (decisión de costo); las semillas son solo locales.
4. La geoetiqueta desde el EXIF (W04-14), con la subida móvil de HU-M04.
5. ~~La reintegración `main → develop` del hotfix #331~~: hecha en el PR #381 (2026-10-29).
6. Los cambios en Jira de la tabla siguiente.

## Cambios en Jira (los hace Felipe)

| Incidencia | Cambio |
|---|---|
| KAN-27 | Done → **In Progress**. Descripción: backend real `claims` (MongoDB), almacenamiento y orden en PAYMENTS |
| KAN-247 | Done, con la nota de que el bloque 8 cambia el endpoint y el token |
| KAN-242 | Bloques 1 a 3 |
| KAN-243 | De «URLs HMAC» a «evidencias en el almacenamiento (KAN-250)» |
| KAN-244 | Resolución, SLA y orden en PAYMENTS (enlazar KAN-28, W05-3 y Frans) |
| KAN-245 | Eventos validados al publicar |
| KAN-246 | `/v1/graphql/{canal}` con alias y convivencia con W07 |
| KAN-248 / KAN-249 | Pruebas y cierre |
| KAN-243, 245, 246 y 248 | «Rol: Web» → «Rol: Backend». KAN-249: «Rol: Móvil» → Backend |
| KAN-28 | Comentario: cambia W05-3 y la orden de indemnización entra en el Sprint 2 |
| CA-W04 | Validar los 29 criterios propuestos. CA-W04-08: la etiqueta saldrá del EXIF (pendiente) |
| Nueva | «Geoetiqueta desde el EXIF» (front y extracción al subir, ligada a HU-M04) |
| Nueva | «Retirar el dummy `stub-siniestros`» (W04-13) |

## Verificación

**Por bloque:**
- `python -m pytest` y `ruff check .`.
- `python scripts/validar_servicios.py`, `validar_contratos.py` y `sincronizar_tipos_archivo.py --verificar`.
- `bash scripts/verificar-migraciones.sh claims` (dos corridas idempotentes) y `payments` (upgrade, downgrade,
  upgrade).

**De punta a punta:**
1. `docker compose --profile servicios up --wait`, luego `sembrar-polizas-demo` y `sembrar-siniestros`.
2. Por Envoy `:8080` y con un JWT de BACKOFFICE:
   - `siniestros`;
   - `siniestro(id)`, con una URL de evidencia que se descarga desde `fake-gcs`;
   - derivar, luego aprobar (una sola orden en PAYMENTS aunque se repita) y rechazar otro caso.
3. `/graphql/web` y `/v1/graphql/web` responden lo mismo.
4. Un CLIENTE recibe un error de permiso.
5. El dummy sigue respondiendo en 8107 sin cambios.

**Web:** sin cambios de código en este feature; solo se revisa que la sección documentada del front cubra los 9 ajustes.
Después, los ajustes se hicieron en el repo del front (#91 y #94) y la web dejó de usar el dummy.

**Logs:** sin nombre, documento, descripción ni coordenadas (EC022).
