# acl-worker

ACL Worker: capa anticorrupción hexagonal.

## Responsabilidad

Único punto de contacto con proveedores externos (Open Finance, Open Data y pasarela de pagos). **Arquitectura hexagonal** (HA-MOD-001):

- `app/domain/puertos.py`: puertos estables `PuertoFuenteFinanciera`, `PuertoFuenteAbierta` y `PuertoPasarelaPagos`.
- `app/adapters/`: un adaptador por puerto (`AdaptadorOpenFinance`, `AdaptadorOpenData`, `AdaptadorPasarela`) — traducción HTTP pura, sin Circuit Breaker ni caché; apuntan a `stubs/`, intercambiables por un sandbox real sin tocar el dominio ni la capa de aplicación (EC031).
- `app/application/`: casos de uso (`ServicioConsultaFuentes`, `ServicioCobro`) que orquestan adaptador + Circuit Breaker (`purgatory`) + retry acotado + degradación con caché o valor por defecto, timeout duro de **700 ms** (HA-LAT-002, EC009/EC010).
- La sonda de recuperación del circuito corre **fuera del camino síncrono** (`app/application/sonda.py`, lección del Experimento 1).

Diseño traducido (no copiado) del Experimento 1 — Node.js/TypeScript con Opossum+BullMQ
(`solventa-arquitectura@11e4be6`, `experimento-1-acl-kyc/`) a Python/FastAPI con `purgatory`.
**Fuera de alcance de este sprint** (queda en el Experimento 1, no se porta): el ciclo
asíncrono crear→pollear de Truora/KYC (específico de ese proveedor). KYC y firma electrónica
se agregan en el Sprint 2.

**Ampliación de alcance pedida por el usuario** (fuera de `docs/sprint-1.md` original, ver la
nota en ese archivo): cuando `ServicioConsultaFuentes.consultar()` cae en la rama de falla
(circuito abierto o cualquier excepción) encola, fire-and-forget, un job de reconciliación
diferida (`app/application/reconciliacion.py`: `ProductorReconciliacion`, cola `rq` en la db 2
de Redis) que consume `services/consolidador-fuentes` — traducción del Consolidador KYC del
Experimento 1 a Python/`rq`, aplicada aquí a Open Finance/Open Data (no a KYC).

## Cobertura de tareas del Sprint 1

- **T-W01-8 (Open Finance, CE 004/2024) y T-W01-9 (Open Data):** adaptadores HTTP puros contra
  `stubs/open-finance` (`cuentas-bancarias`, `historial-crediticio`) y `stubs/open-data`
  (`afiliacion-pila`, `camara-comercio`, `antecedentes-judiciales`). `AdaptadorOpenFinance`
  envía `consentimiento_id` y `finalidad` (por defecto `"perfilamiento"`) en cabeceras
  (`X-Consentimiento-Id`, `X-Finalidad`), query params y cuerpo; el stub responde `403` si
  alguno falta o viene vacío.
- **Fuentes por configuración (`contracts/fuentes.json`, EC033) y modificabilidad (EC031):**
  `app/application/fuentes_config.py` carga el catálogo de la ruta dada, de `FUENTES_CONFIG_PATH`, de la
  **copia que lleva la imagen** (`app/fuentes.json`, idéntica a `contracts/fuentes.json`: la imagen se
  construye solo con esta carpeta y no ve `contracts/`) o del contrato en el repo; sin ninguno, las 5
  fuentes por defecto. `python scripts/sincronizar_fuentes.py` deja las copias iguales y el CI verifica que
  lo estén. Pruebas automatizadas en `tests/test_ec031_y_ec033.py` y `tests/test_fuentes_copia.py`.
- **Tiempo por proveedor (EC008):** la métrica `acl_proveedor_duracion_segundos{proveedor,resultado}`
  (histograma de Prometheus, `GET /metrics`) mide cada llamada **en vivo** a un proveedor (resultado `ok`,
  `timeout`, `error` o `circuito_abierto`); lo servido desde la caché no cuenta. Las etiquetas no llevan datos
  personales. `tests/k6/gateway-presupuesto.js` mide el presupuesto de ≤ 120 ms contra este servicio.
- **T-W01-10 (Circuit Breaker, Retry, degradación y EC010 ≤ 500 ms):** un breaker por proveedor
  (`ServicioConsultaFuentes`/`ServicioCobro`), retry acotado al presupuesto por proveedor
  (`PRESUPUESTO_MS_<PROVEEDOR>` acotado por `TIMEOUT_MS`, p. ej. 120 ms para EC008 o 700 ms por defecto),
  degradación cache-aside en Redis (`app/application/cache.py`, TTL 5 min). Cuando ya existe un
  valor en caché, compite caché contra proveedor usando `presupuesto_con_cache_s`
  (`PRESUPUESTO_CON_CACHE_MS`, 300 ms por defecto) para cumplir **EC010 ≤ 500 ms**, o sirve
  directamente desde caché dentro del TTL si `CACHE_SERVIR_EN_TTL=true` (salvo `forzar_proveedor=True`).
  Si no hay nada en caché, devuelve un valor mínimo con `degradado=True`. Nunca se propaga un 5xx crudo.
- **Invalidación activa de caché al revocar consentimiento (CA-W01-08):** `POST /fuentes/invalidar-cache`
  con `{"cliente_id": "..."}` en el cuerpo borra de inmediato las claves cacheadas de todas las fuentes
  configuradas para ese titular, además del TTL pasivo de 5 minutos.
- **T-W05-2 (lado ACL de la pasarela):** `AdaptadorPasarela` + `ServicioCobro`; sin circuito
  disponible, el cobro queda `pendiente` (CA-W05-04). La idempotencia persistente y el reintento
  vía Cloud Tasks (T-W05-3/4) son responsabilidad de `payments`, no de este servicio.
- **Conciliación (KAN-28):** `PuertoPasarelaPagos.consultar` busca un cobro por la **referencia del comercio** (la
  `id_intento_cobro` que envía Solventa), como consultan las pasarelas reales; `ServicioCobro.consultar` usa el mismo
  Circuit Breaker y el mismo presupuesto, y responde `encontrado`, `no_encontrado` o `desconocido` (sin poder
  preguntar). Un cobro que no se intentó porque el circuito estaba abierto lleva `motivo=circuito_abierto`, distinto de
  `pasarela_no_disponible`, para que PAYMENTS no le cuente ese intento. Los logs enmascaran la póliza (`id_opaco`).

## Decisiones de diseño que no estaban ya escritas

- **Sonda de recuperación sin control manual de `purgatory`:** la librería no expone un evento
  "circuito abierto hace N segundos" ni un método para forzar el half-open — su recuperación es
  perezosa (se reevalúa en el próximo `with breaker:` tras vencer el TTL). `SondaRecuperacion`
  explota esa misma pereza: un bucle `asyncio` en el lifespan de FastAPI consulta cada
  `SONDA_INTERVALO_SEGUNDOS` qué breakers están `opened` y, si encuentra uno, es ella quien entra
  primero al `with breaker:` — en un hilo (`asyncio.to_thread`), para no bloquear el loop de
  eventos — pagando el costo real de la llamada antes de que llegue una petición de usuario. No es
  100 % infalible (una petición de usuario podría ganarle la carrera justo cuando vence el TTL
  entre dos ticks); se mitiga con un intervalo de sonda (2 s) sensiblemente menor al TTL del
  breaker (5 s). Documentado también en `docs/arquitectura-backend.md`.
- **Sondeo sin efectos de negocio:** la sonda de Open Finance/Open Data reusa `consultar()` con un
  `cliente_id`/`consentimiento_id`/`finalidad` sintéticos (`"sonda"`, no datos reales); la de la pasarela usa
  un endpoint dedicado `GET /v1/estado` en el stub (en vez de `cobrar()`) para no disparar una
  orden de pago real solo por sondear.
- **Retry sin backoff:** con un presupuesto total de 700 ms, esperar entre intentos le resta
  tiempo útil al segundo intento sin aportar nada; se reintenta una sola vez, solo si el primer
  fallo fue rápido (deja presupuesto de verdad) — un timeout real no deja margen para reintentar.
- **Estado del breaker en memoria del proceso:** se usa el `SyncInMemoryUnitOfWork` por defecto de
  `purgatory` (no Redis-backed): sin persistencia entre reinicios ni locks entre hilos — limitación
  conocida y aceptada, igual que la ya documentada de `purgatory` en `docs/arquitectura-backend.md`.
- **Clave de caché sin `cliente_id` en crudo:** la clave en Redis guarda el hash SHA-256 del
  cliente, no el id en crudo (defensa en profundidad, además de que ni logs ni etiquetas de
  métricas usan el id sin hashear — `app/application/identificadores.py`).
- **Tasas de cambio (KAN-209):** `GET /tasas-cambio` devuelve cuántos COP vale una unidad de cada moneda del
  selector de la web (texto decimal). Hoy sale de una tabla de referencia fija versionada
  (`app/tasas_cambio.json`, `AdaptadorTasasReferencia`) que hace de stub del proveedor; el BFF la usa para
  `convertirMontos`. Un proveedor real es otro adaptador de `PuertoTasasCambio` (con circuito y caché como Open
  Finance), sin tocar el BFF.

## Pendiente, fuera de alcance de esta tarea

- Cifrado de campo (KMS) del valor cacheado en Redis: SEG-002 está asignado a `auth` y `risk`;
  este servicio no persiste el dato real en un almacén propio, solo lo cachea como degradación temporal.
- KYC y firma electrónica (Sprint 2).

## Operación

- Puerto local: `8005`. Salud: `GET /health`. Métricas Prometheus: `GET /metrics`.
- Observabilidad del circuito (sin datos personales): `GET /circuitos` → `{"open-finance": "closed", ...}`.
- Endpoints internos (backend-a-backend, no forman parte de `contracts/`):
  - `POST /fuentes/consultar` `{cliente_id, consentimiento_id, fuente, finalidad?, forzar_proveedor?}` → `{fuente, datos, capturado_en, de_cache, degradado}`.
  - `POST /fuentes/invalidar-cache` `{cliente_id}` → `{invalidado: true}` (CA-W01-08, nunca `cliente_id` en la URL).
  - `POST /pagos/cobrar` `{id_intento_cobro, poliza_id, monto, moneda, token_medio_pago}` → `{estado, referencia, motivo, detalle}`.
  - `POST /sonda/ejecutar` → una pasada de la sonda; la llama Cloud Scheduler con `DISPARADOR=externo` (en Cloud Run no hay bucle fiable). Devuelve el estado de cada circuito. El estado de los circuitos vive en la memoria de cada instancia.
  - `GET /pagos/cobros/{referencia_comercio}` → siempre 200 con `{resultado: encontrado|no_encontrado|desconocido, estado, referencia, motivo}`.
  - `POST /pagos/sesiones` `{id_intento_cobro, monto, moneda, descripcion, url_retorno, idioma}` → siempre 200 con
    `{resultado: creada|no_disponible, sesion, motivo}`: la sesión de la **página de pago alojada** de la pasarela
    (HU-W03, W03-28), idempotente por intento. Un retorno fuera de la lista blanca de la pasarela llega como sesión
    `rechazada` y no abre el circuito.
  - `GET /pagos/sesiones/{sesion_id}` → siempre 200 con `{resultado: encontrado|no_encontrado|desconocido, sesion}`. Es la
    confirmación de servidor a servidor al regresar del navegador; con `cobrada` trae el medio tokenizado (`token`,
    `marca`, `tipo`), **nunca el número ni los últimos dígitos**. Mismo circuito y presupuesto que el cobro.
- **Cambiar de pasarela (EC031, CA-W05-06):** `PROVEEDOR_PASARELA=estandar|alterna` elige el adaptador. Para sumar un proveedor: una clase en `app/adapters/` que cumpla `PuertoPasarelaPagos` (`cobrar`, `consultar`, `crear_sesion_pago`, `consultar_sesion_pago`, `sondear`, `cerrar`), una línea en la elección de `app/main.py` y su prueba de contrato en `tests/test_ec031_pasarela.py` y `tests/test_ec031_sesiones_pago.py`. `AdaptadorPasarelaAlterna` es el ejemplo (otra API, otros campos y estados). PAYMENTS y el BFF no cambian.
- Con `DISPARADOR=externo` la reconciliación diferida va por Cloud Tasks (`app/application/cola_tareas.py`) hacia `POST /reconciliar` del consolidador, y exige `GCP_PROYECTO`, `GCP_REGION`, `TAREAS_CUENTA` y `CONSOLIDADOR_URL` (los pone Terraform; opcional `TAREAS_COLA_RECONCILIACION`).
- Variables de entorno: `DISPARADOR` (`bucle`; `externo` desactiva el bucle de la sonda y cambia `rq` por Cloud Tasks), `PROVEEDOR_PASARELA` (`estandar`), `REDIS_URL`, `OPEN_FINANCE_URL`, `OPEN_DATA_URL`, `PASARELA_URL`,
  `TIMEOUT_MS` (700), `PRESUPUESTO_MS_<PROVEEDOR>` (cualquier proveedor: `OPEN_FINANCE`, `OPEN_DATA`, `PASARELA`,
  `FIRMA`, `KYC`, `NOTIFICACIONES`… ; sin variable vale `TIMEOUT_MS`; p. ej. 120 para EC008 en Open Finance),
  `PLAZO_RUTA_SINCRONA_MS` (700), `RESERVA_SALTOS_MS` (250) y `PROVEEDORES_RUTA_SINCRONA` (`firma,pasarela`; ver abajo),
  `PRESUPUESTO_CON_CACHE_MS` (300, EC010 ≤ 500 ms), `CACHE_SERVIR_EN_TTL` (`false`; `true` en Cloud Run),
  `FUENTES_CONFIG_PATH` (`contracts/fuentes.json`), `BREAKER_UMBRAL_FALLOS` (3),
  `BREAKER_TTL_SEGUNDOS` (5), `SONDA_INTERVALO_SEGUNDOS` (2), `CACHE_TTL_SEGUNDOS` (300),
  `COLA_RECONCILIACION_REDIS_URL` (`redis://localhost:6379/2`, db de Redis distinta a la de la
  caché — ver `services/consolidador-fuentes/README.md`).
- Pruebas: `pip install -r requirements.txt && pip install -r ../../requirements-dev.txt && python -m pytest` (desde esta carpeta).
- Imagen: `docker build -t solventa-acl-worker .`

### Verificación en vivo (Circuit Breaker + sonda)

```bash
docker compose --profile servicios up -d --build redis acl-worker stub-open-finance stub-open-data stub-pasarela stub-firma stub-kyc stub-notificaciones

# (a) camino sano
curl -s -X POST localhost:8005/fuentes/consultar \
  -H 'content-type: application/json' \
  -d '{"cliente_id":"c-1","consentimiento_id":"k-1","fuente":"cuentas-bancarias"}'

# (b) se fuerza el stub a caído: 3 fallos abren el circuito, degrada sin 5xx
curl -s -X POST localhost:8101/control/modo -H 'content-type: application/json' -d '{"modo":"caido"}'
for i in 1 2 3 4; do
  curl -s -X POST localhost:8005/fuentes/consultar \
    -H 'content-type: application/json' \
    -d '{"cliente_id":"c-1","consentimiento_id":"k-1","fuente":"cuentas-bancarias"}'
done
curl -s localhost:8005/circuitos   # -> open-finance: "opened"

# (c) el stub vuelve a sano: la sonda (no una petición de "usuario") cierra el circuito sola
curl -s -X POST localhost:8101/control/modo -H 'content-type: application/json' -d '{"modo":"sano"}'
sleep 6
curl -s localhost:8005/circuitos   # -> open-finance: "closed"
```


## Firma electrónica (HU-W03, bloque 2)

- `POST /firma/firmar` `{id_firma, documento_hash, version_documento, firmante_id}` → `{estado: firmada | pendiente,
  referencia, firmada_en, motivo}`. Siempre 200: con el proveedor caído, lento o el circuito abierto responde `pendiente`
  (`firma_no_disponible` / `circuito_abierto`) y POLICY ofrece reintentar. El proveedor es idempotente por `id_firma`, así
  que reintentar tras un corte a los 700 ms devuelve la misma evidencia en vez de firmar dos veces.
- Solo viaja el **hash** del documento: el contenido (con la prima) vive cifrado en POLICY junto a la evidencia.
- Circuito `firma`, con su sonda de recuperación (`GET /v1/estado`, que nunca firma), igual que la pasarela.
- EC031 / CA-W03-10: dos adaptadores (`app/adapters/firma.py` y `firma_alterna.py`), elegidos con `PROVEEDOR_FIRMA`
  (`estandar` | `alterna`). Variables nuevas: `FIRMA_URL` (`http://localhost:8104`), `PRESUPUESTO_MS_FIRMA`.

**Ruta síncrona del usuario (CA-W03-05 y W03-29).** La firma y las dos llamadas de pago (crear la sesión y confirmarla al regresar) son lo que el usuario de W03 espera: si el proveedor tarda, el corte de 700 ms
del gateway (HA-LAT-004) llegaba antes que el del ACL y el cliente veía un 504. Los proveedores de `PROVEEDORES_RUTA_SINCRONA` quedan
acotados a `PLAZO_RUTA_SINCRONA_MS − RESERVA_SALTOS_MS` (700 − 250 = **450 ms** por defecto) aunque su `PRESUPUESTO_MS_*` sea mayor; así
la respuesta es un `ErrorFirma` con reintento y no un 5xx. El cobro, el KYC y el aviso corren en segundo plano y conservan su presupuesto.
La reserva es lo que cuestan los saltos BFF → POLICY → ACL (~80 ms medidos en local); súbela si la nube mide más. `PLAZO_RUTA_SINCRONA_MS`
debe coincidir con el `timeout` del gateway en `infra/gateway/*`: hoy no hay una verificación automática entre los dos.
Agregar un proveedor nuevo no toca el código: su variable `PRESUPUESTO_MS_<PROVEEDOR>` y, si lo espera el usuario, su nombre en `PROVEEDORES_RUTA_SINCRONA`.

## KYC (HU-W03, bloque 3)

- `POST /kyc/verificar` `{id_verificacion, cliente_id}` → `{estado: aprobada | rechazada | pendiente, referencia, motivo,
  verificada_en}`. Siempre 200: con el proveedor caído, lento o el circuito abierto responde `pendiente`
  (`kyc_no_disponible` / `circuito_abierto`) y UNDER manda la propuesta a revisión manual. Idempotente por `id_verificacion`.
- Solo viaja el identificador opaco del cliente, nunca un documento. Un proveedor real pediría el documento y una validación
  biométrica por un canal propio (decisión W03-10); el contrato del stub es inventado.
- Circuito `kyc` con su sonda (`GET /v1/estado`, que nunca verifica). Variables nuevas: `KYC_URL` (`http://localhost:8105`),
  `PRESUPUESTO_MS_KYC` (700).

## Verificación de identidad (HU-M01)

- `POST /identidad/verificar` `{referencia, anverso_url, rostro_url, reverso_url?}` → `{estado: aprobada | rechazada | pendiente, motivo,
  datos?, documento_autentico, coincide_rostro, puntaje_rostro, vivo, puntaje_vida, alertas}`. Siempre 200. Las imágenes llegan como URLs
  firmadas de `almacenamiento`; el ACL las descarga solo de `IDENTIDAD_ORIGENES_IMAGENES` (anti-SSRF, máx. 10 MB) y llama a las APIs
  independientes de Didit v3 (`id-verification`, `passive-liveness`, `face-match`, encabezado `x-api-key`, `save_api_request=false`).
- Motivos: `documento_rechazado`, `prueba_de_vida_fallida`, `rostro_no_coincide`; en `pendiente`, `imagen_no_disponible`,
  `circuito_abierto` o `proveedor_no_disponible`. Circuito `identidad` con su sonda (`GET /`), sin reintento (Didit cobra por llamada).
- **Caché por huella**: llave `acl:identidad:{sha256 de las imágenes}`, nunca la cédula; valor cifrado AES-GCM con
  `IDENTIDAD_LLAVE_CACHE` (32 bytes en base64), TTL `IDENTIDAD_CACHE_TTL_SEGUNDOS` (30 días). Solo guarda resultados definitivos.
  Sin llave en la nube, la caché se apaga con un aviso; en local la llave es aleatoria por proceso.
- Variables: `IDENTIDAD_URL` (por omisión `KYC_URL`, el stub), `DIDIT_API_KEY` (obligatoria si `IDENTIDAD_URL` apunta a Didit),
  `IDENTIDAD_TIMEOUT_SEGUNDOS` (15), `IDENTIDAD_ORIGENES_IMAGENES`, `IDENTIDAD_REESCRITURA_ORIGENES` (`orig=dest`, solo en local para
  fake-gcs). Logs solo con la referencia opaca y códigos. Evidencia: `docs/evidencias/identidad-didit-2026-10-27.md`.

## Notificaciones (HU-W03, bloque 5)

- `POST /notificaciones/enviar` `{id_notificacion, plantilla, destinatario_id, datos, correo?, telefono?, adjuntos?}` → `{estado: enviada | pendiente | rechazada, referencia,
  motivo}`. Siempre 200: con el proveedor caído, lento o el circuito abierto responde `pendiente`
  (`notificaciones_no_disponibles` / `circuito_abierto`) y POLICY reintenta. Idempotente por `id_notificacion`: repetirlo no
  manda dos avisos.
- **Regla cambiada en el bloque 8 (W03-16, 2026-10-24).** Antes solo viajaban la plantilla, el id opaco y los datos de la plantilla, y el
  proveedor resolvía el contacto. Ahora el aviso lleva el contacto del cliente (`correo` o `telefono`, que guarda AUTH) y el PDF de la
  póliza como adjunto (`{nombre, tipo, contenido_base64}`): **datos personales y financieros** que viajan solo en el cuerpo de la
  petición al proveedor y nunca se guardan ni se escriben en logs ni en etiquetas. El contrato del stub es inventado.
- Reglas del adjunto, validadas en la ruta con un `422` cuyo mensaje no repite el contenido: solo con `correo` (un SMS no lleva
  archivos), tipo `application/pdf`, base64 válido y no vacío, hasta 3 por aviso y hasta `ADJUNTO_MAXIMO_BYTES` (5.000.000 por omisión) ya
  decodificado. Un `422` es un error de quien llama, no del proveedor: no se reintenta.
- Circuito `notificaciones` con su sonda (`GET /v1/estado`, que nunca envía). Variables nuevas: `NOTIFICACIONES_URL`
  (`http://localhost:8106`), `PRESUPUESTO_MS_NOTIFICACIONES` (700), `ADJUNTO_MAXIMO_BYTES` (5000000).
- **Correo real con Resend** (`app/adapters/correo_resend.py`): `NOTIFICACIONES_PROVEEDOR=resend` cambia el simulador por
  `POST https://api.resend.com/emails` (EC031: el mismo puerto, otro adaptador). Variables: `NOTIFICACIONES_PROVEEDOR`
  (`simulado` | `resend`), `RESEND_API_KEY` (secreto `resend-api-key`; con `resend` y sin ella no arranca) y `CORREO_REMITENTE`
  (de un dominio verificado en Resend). El asunto y el cuerpo de las 4 plantillas (`codigo-verificacion`, `enlace-firma`,
  `poliza-emitida` y `poliza-emitida-asesor`) viven en el adaptador, con los datos escapados en el HTML. El `id_notificacion`
  va como `Idempotency-Key`: un reintento no manda dos correos. Un aviso sin `correo` o con una plantilla desconocida
  responde `rechazada` sin llamar a Resend (POLICY lo omite); 429, 5xx y fallas de red cuentan para el circuito y quedan
  `pendiente`; otro 4xx es `rechazada` con el nombre del error de Resend, nunca su mensaje (puede repetir el correo). La
  sonda hace `GET /domains`, que nunca envía (con una llave de solo envío responde 401, y eso también cuenta como arriba).
  Cómo cargar la llave: `infra/README.md`, «Resend».
