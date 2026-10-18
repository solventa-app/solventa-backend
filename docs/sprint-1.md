# Sprint 1 — alcance del backend

Fuente: *Plan de Trabajo y Historias de Usuario del Sprint 1* (v1.1.0, semana 7) y `PlanningV1.2.xlsx` (documentos del curso, fuera de este repo).
Este archivo es el **resumen operativo para construir**; si el plan cambia, se actualiza aquí en el mismo cambio.

- **Fechas (tentativas, confirmar contra el calendario del curso):** 12–25 de octubre de 2026. El lunes 12 es festivo.
- **Meta:** un cliente o asesor consiente y recibe en línea una oferta de vida hipotecario (con degradación controlada si una fuente falla), y el back-office cobra y concilia pagos con una pasarela tokenizada.
- **Alcance:** HU-W01 (KAN-24, 20 pts, 38 h) + HU-W05 (KAN-28, 6 pts, 11 h) = 26 de 27 pts; **49 h de 52 h netas (holgura 3 h)**.
- **Regla de recorte:** si el Sprint 1 rinde < 22 pts, se recorta desde el final de la lista (HU-M06, HU-M02, HU-M04). Las HU de prioridad HH no se recortan.

## Decisiones (recomendación del plan; confirmar en el Planning)

| ID | Decisión | Recomendación | Antes de |
|---|---|---|---|
| D-01 | Base de velocidad | Recálculo 27/27/44 y regla de recorte | Planning S1 |
| D-02 | Read-your-writes RISK→RATING | Sesión causal: RISK devuelve el `operationTime` y RATING lee con `afterClusterTime`; lectura de la primaria como alternativa dentro de la misma solicitud | PF2-1 |
| D-03 | Cobro antes o después de emitir | Mantener la definición del equipo (antes) y actualizar el Journey 2 | Planning S2 (preguntas abiertas en `docs/cobros-conciliacion.md`, sección 10) |
| D-04 | Alcance de HA-SEG-002 | Cifrado de campo en la aplicación con Cloud KMS; CMEK nativo queda como mejora | PF2-1 |
| D-05 | Proveedores externos | Stubs por contrato, adaptadores intercambiables; sandbox reales si aparecen | PF2-1 |
| D-06 | Adelantar HU-W05 al Sprint 1 | Aprobar | Planning S1 |

## Actividades de backend (de la sección 2 del plan)

HU-W01 — Perfilamiento y oferta (38 h; las de pantallas van en `solventa-frontend`). La columna Servicio es una asignación propuesta de este repo; el plan solo asigna rol y horas:

| ID | Actividad | Horas | Habilitadora | Servicio |
|---|---|---|---|---|
| T-W01-4 | Tabla de datos de usuario (aquí: registro de consentimiento) | 3 | SEG-002 | `auth` |
| T-W01-6 | Tabla de oferta | 5 | LAT-001, LAT-003 | `rating` |
| T-W01-7 | Endpoint de datos de usuario | 2 | SEG-001 | `bff`, `risk` |
| T-W01-8 | Integración API a Open Finance | 6 | MOD-001, INT-003 | `acl-worker` |
| T-W01-9 | Integración API a Open Data | 5 | INT-003 | `acl-worker` |
| T-W01-10 | Circuit Breaker, Retry y degradación con caché (700 ms) | 4 | LAT-002 | `acl-worker` |
| T-W01-11 | Pruebas unitarias, de contrato y k6 (EC003, EC004, EC010) | 4 | — | `tests/` |
| T-W01-12 | Documentación técnica y revisión de código | 2 | — | — |

HU-W05 — Recaudo (11 h):

| ID | Actividad | Horas | Habilitadora | Servicio |
|---|---|---|---|---|
| T-W05-2 | Integración con la pasarela de pago | 3 | MOD-001 | `acl-worker`, `stubs/pasarela` |
| T-W05-3 | Estados de cobro, idempotencia y balances en el Core | 3 | MOD-001 | `payments` |
| T-W05-4 | Reintento de cobro con Cloud Tasks y Circuit Breaker | 1 | LAT-002 | `payments`, `acl-worker` |
| T-W05-5 | Pruebas de contrato e idempotencia, y documentación | 2 | — | `tests/` |

## Criterios de aceptación clave (resumen)

- **CA-W01-01:** sin consentimiento marcado el botón está deshabilitado y no se consulta ninguna fuente.
- **CA-W01-02:** el evento de consentimiento (actor, finalidad, versión del texto, sello de tiempo) se registra **antes** de consultar cualquier fuente.
- **CA-W01-03:** oferta con prima, cobertura y desglose del score, p95 ≤ 400 ms y p99 ≤ 800 ms en el servidor.
- **CA-W01-04:** si una fuente supera 700 ms o falla → último valor en caché o por defecto, oferta **preliminar** con rango de prima, sin exceder el presupuesto de latencia.
- **CA-W01-05:** sin datos suficientes ni caché → mensaje de error con reintentar y asesor; **nunca un 5xx crudo**.
- **CA-W01-06:** se listan las 5 fuentes (2 Open Finance + 3 Open Data) con su estado y la fecha de captura si el dato viene de caché.
- **CA-W01-07:** contacto con asesor (llamar, chatear o agendar) con confirmación y resumen del trámite.
- **CA-W01-08:** revocación del consentimiento: rechaza nuevas consultas e invalida la caché en ≤ 5 min.
- **CA-W01-09:** RATING usa el perfil recién escrito por RISK (read-your-writes).
- **CA-W01-10:** campos sensibles cifrados en reposo y en tránsito; logs sin datos personales.
- **CA-W01-11:** el asesor comercial autenticado ve la tarjeta de nueva cotización y usa el mismo formulario y flujo que el cliente.
- **CA-W05-01/02:** tabla de cobros por póliza con su estado; reintentar relanza el cobro con el mismo `id_intento_cobro`.
- **CA-W05-03/04/05:** captura de tarjeta con componente tokenizado del proveedor PCI-DSS; un mismo intento de cobro se ejecuta una sola vez; si la pasarela supera 700 ms el cobro queda pendiente (sin 5xx) y se reintenta con Cloud Tasks.
- **CA-W05-06/07:** cambiar de pasarela no toca el Core; la orden de pago de indemnización (Sprint 3) actualiza el balance.

## Habilitadoras del Sprint 1

| HA | Alcance | Criterio | Base reutilizable |
|---|---|---|---|
| LAT-001 (KAN-50) | Cache-Aside de scores en Redis con TTL y versión de reglas | Lectura de caché ≤ 5 ms; p95 del journey ≤ 400 ms | Redis del Exp. 1 |
| LAT-002 (KAN-51) | Adaptadores de Open Finance, Open Data y pasarela con Circuit Breaker y Retry (700 ms) y degradación; sonda fuera del camino síncrono | 0 % de 5xx con el proveedor caído; el circuito cierra solo; confiabilidad ≥ 99,9 % | ACL Worker y Consolidador (Exp. 1) |
| LAT-003 (KAN-52) | RATING lee de la secundaria; incluye el mecanismo de D-02 | ≤ 10 ms extra; 0 ofertas con perfil obsoleto | replica set, escritor, lector y medición de lag (Exp. 2) |
| SEG-001 (KAN-63) | Registro append-only de consentimiento; la revocación invalida caché y bloquea consultas | 100 % de consultas con consentimiento vigente; revocación ≤ 5 min | — |
| SEG-002 (KAN-64) | Cifrado de campo con Cloud KMS, TLS, logs sin datos personales | 100 % cifrado; sin datos de tarjeta propios | — |
| MOD-001 (KAN-67) | Puertos estables (fuente financiera, fuente abierta, pasarela) y un adaptador stub por puerto | Sustituir un adaptador sin modificar ni redesplegar el Core | puerto hexagonal del Exp. 1 |
| INT-003 (KAN-73) | Plantilla de adaptador (interfaz, prueba de contrato, registro por configuración) | Nueva fuente en ≤ 1 semana sin cambiar RISK ni RATING | — |

Diferidas: LAT-004 y ESC-001 al Sprint 2 (dependen del API Gateway); ESC-004 al Sprint 3 en versión reducida.

## Escenarios a medir (umbrales = piso, no valor de producción)

| Escenario | Qué mide | Umbral | Cuándo |
|---|---|---|---|
| EC003 / EC004 | Latencia de perfilamiento y pricing | p95 ≤ 400 ms; p99 ≤ 800 ms | PF2-2 |
| EC009 / EC010 | Timeout duro y degradación con proveedor lento o caído | corte a 700 ms; 0 % de 5xx | PF2-2 |
| EC011 / EC012 | Lectura sobre la réplica | p95 ≤ 150 ms; p99 ≤ 300 ms | PF2-2 |
| EC022 | Cifrado y tarjeta tokenizada | 100 % en reposo y en tránsito | PF2-2 |
| EC023 | Revocación de consentimiento | ≤ 5 min | PF2-1 |
| EC031 | Reemplazo de adaptador | cero cambios en el Core | PF2-1 |
| Idempotencia | Mismo cobro recibido dos veces | 0 cobros duplicados | PF2-2 |

EC005/EC007 (emisión) se miden en el Sprint 2; EC008 tiene una ambigüedad de interpretación sin resolver y **no** se usa como criterio.

## Orden sugerido de construcción (PF2-1)

1. **Consentimiento en AUTH primero** (T-W01-4): ninguna fuente se consulta sin él; bloquea el resto.
2. En paralelo: puertos y adaptadores stub del ACL con Circuit Breaker (T-W01-8/9/10), RISK + RATING con réplica y caché (T-W01-6/7), y la línea de PAYMENTS con pasarela stub (T-W05-*).
3. Pruebas tempranas en PF2-1: EC023 (revocación) y EC031 (reemplazo de adaptador). Los k6 de latencia y falla inyectada van en PF2-2.

## Observaciones de trazabilidad pendientes (Jira)

- HA-SEG-001 cita EC026 (ataque a la API); el escenario de revocación es **EC023** (KAN-102) y no tiene enlace a KAN-63.
- HU-W05: el PDF del backlog le asocia HA-SEG-003, pero Jira (KAN-28) y el documento de arquitectura usan HA-SEG-002. El plan usa la relación de Jira.
- **Ampliación de alcance pedida directamente por el usuario (sin HU/HA/CA de este plan, sin ID de Jira):**
  `services/consolidador-fuentes` reconcilia de forma diferida las consultas a Open Finance/Open Data que
  `acl-worker` degradó (circuito abierto o falla), traduciendo a Python + `rq` el patrón del Consolidador
  KYC del Experimento 1 (`solventa-arquitectura@11e4be6`). Documentado en `docs/arquitectura-backend.md`
  (sección "Consolidador de fuentes") y en el README de ambos servicios. No cubre KYC (sigue en Sprint 2) ni
  cambia ningún criterio de aceptación de HU-W01/HU-W05: solo evita que un dato degradado quede sin
  reintentarse una vez que el proveedor se recupera.
- **Corrección sobre el Experimento 2 (réplica de Riesgo):** `experimento-2-replica-riesgo` en
  `../solventa-arquitectura` está **sin construir** (verificado directamente): solo tiene el README de
  la estructura esperada, `escritor-risk`/`lector-rating`/`ryw.py` no existen como código en ningún lado
  de ese repo. `services/risk` y `services/rating` (T-W01-6/7, D-02/HA-LAT-003) se construyeron desde
  cero a partir de esta especificación, no portando código de un experimento que nunca se construyó. Los
  README de ambos servicios tenían una afirmación de reutilización incorrecta; ya se corrigió ahí.
- **Cifrado de campo (SEG-002/D-04/CA-W01-10) fuera de alcance en `risk`:** sin proyecto GCP no hay Cloud
  KMS disponible, y no existe todavía ningún helper de cifrado en el repo (AUTH tampoco lo ha construido
  para T-W01-4). `risk` persiste los datos de las fuentes sin cifrar — hueco real, documentado en
  `services/risk/README.md`, deliberadamente no simulado con un cifrado de juguete.
- **Ampliación de alcance pedida directamente por el usuario (sin HU/HA/CA nuevos, sin ID de
  Jira):** `services/rating` ya tenía el mecanismo de lectura causal (D-02/HA-LAT-003), pero el
  cálculo real de la oferta (T-W01-6 completo: score + prima, HA-MOD-002) y el Cache-Aside de
  scores en Redis (HA-LAT-001) estaban marcados como "fuera de alcance, Sprint 2" en su README. El
  usuario pidió construirlos ahora, con una salvedad explícita: **no hay ninguna fórmula actuarial
  definida en el material del curso** (se investigó el PDF del proyecto, el backlog de Jira y
  `PlanningV1.2.xlsx`) — solo la mención genérica de un "motor de reglas actuariales". Se implementó
  un modelo genérico e ilustrativo de la industria (scoring tipo crediticio ponderado por las 5
  fuentes + pricing de seguro de vida hipotecario estándar), documentado como tal —no como fórmula
  actuarial certificada— en `services/rating/README.md`, con la tabla de pesos exacta
  (historial-crediticio 30%, cuentas-bancarias 20%, afiliación-pila 20%, cámara-comercio 15%,
  antecedentes-judiciales 15%) y la fórmula de prima. Nuevo endpoint `POST /ofertas`; no cambia
  ningún criterio de aceptación de HU-W01 (los cumple: CA-W01-03/04/06). El Cache-Aside cachea
  específicamente el **score** (no la prima, que depende de `cobertura`, un dato de la solicitud) —
  decisión de diseño documentada en el README del servicio. Verificado en vivo con números reales
  (score=82, prima=94680.00 COP sobre una cobertura de 200.000.000 con las 5 fuentes sanas; score=76
  y rango 81.792,00–122.688,00 con una fuente degradada) y con pruebas que fuerzan el cambio de
  `VERSION_REGLAS` para confirmar que invalida el hit de caché.
- **Hueco encontrado y CERRADO al verificar D-02 en vivo (`services/rating`):** el mecanismo de fallback a
  la primaria (D-02) cubría de entrada el caso que la especificación describe (la secundaria responde
  pero no alcanza el `afterClusterTime` dentro de `maxTimeMS`). Al forzar lag real pausando el contenedor
  de la secundaria (`docker pause`), se encontró un caso relacionado pero distinto — la secundaria
  totalmente inalcanzable, no solo atrasada — con dos causas (ambas corregidas, ver
  `services/rating/README.md` para la evidencia con números reales de ambas rondas de verificación):
  1. Selección de servidor bloqueada hasta `serverSelectionTimeoutMS` por defecto (30 s) antes de llegar
     a la consulta que `maxTimeMS` acota.
  2. Aun acotando lo anterior, la topología podía seguir creyendo que la secundaria estaba sana y
     despachar la consulta ahí, colgándose indefinidamente en el socket (`docker pause` no cierra la
     conexión, solo congela el proceso) — sin timeout de socket por defecto en pymongo.
  Se resolvió con un segundo `MongoClient` dedicado a la lectura de la secundaria, con
  `serverSelectionTimeoutMS`/`connectTimeoutMS`/`socketTimeoutMS` acotados al mismo presupuesto
  (`RATING_MAX_TIME_MS`), y `ServerSelectionTimeoutError`/`AutoReconnect` agregados al conjunto de errores
  que disparan el fallback. Verificado en vivo: con `mongo2` pausado, la lectura responde 200 en ~0.57s
  (`leido_de_secundaria:false`, sin 5xx ni cuelgue) en vez de bloquear 10s+; al reanudar `mongo2`, la
  siguiente lectura ya vuelve `leido_de_secundaria:true` sin reiniciar nada.
- **BFF (T-W01-7, 2026-10-09):** `services/bff` implementa el contrato GraphQL con Strawberry y orquesta
  `solicitarOferta` (AUTH → ACL Worker ×5 en paralelo → RISK → RATING). El consentimiento de AUTH es un **mock en
  memoria** con los endpoints definitivos, a la espera de T-W01-4; `oferta(id)` devuelve `null`, `contactarAsesor` es
  un mock y `cobros`/`reintentarCobro` responden "no disponible" hasta KAN-28 (resuelto el 2026-10-16, ver la nota de cobros en el back-office). Verificado en vivo con
  `scripts/humo_bff.py` sobre el compose: COMPLETA ~130 ms en caliente; PRELIMINAR ~730-765 ms con Open Finance lento
  (lo fija el corte de 700 ms del ACL; la oferta degradada excede el p95 de 400 ms de CA-W01-03, a medir con k6 en
  PF2-2); `ErrorOferta` sin reintento tras revocar.
- **T-W01-4 hecho (KAN-24, 2026-10-17):** el consentimiento deja de ser un mock en memoria. Tabla `consentimientos`
  append-only en la base `auth` (migración `0004`), con un trigger que rechaza borrar o reescribir filas: solo se
  puede fijar `revocado_en` una vez. Mismas rutas HTTP, así que el BFF y el frontend no cambian. Verificado en vivo:
  `verificar-migraciones.sh auth`, humo, Newman y que un consentimiento sobreviva a reiniciar AUTH. Sigue pendiente
  emitir `consentimiento.registrado`/`revocado` (no hay Pub/Sub).
- **Autenticación de usuarios (HU-M02 simple para web, 2026-10-14, trazada a KAN-24 por CA-W01-11):** usuarios
  `CLIENTE`/`ASESOR`/`BACKOFFICE` en Postgres (base `auth`), inicio de sesión con JWT HS256 y permisos por rol en el BFF,
  con la bandera `AUTENTICACION_USUARIOS=opcional` para no romper al frontend. **CA-W01-11 queda cumplido en el
  backend** (el ASESOR se autentica, usa el mismo flujo y queda como actor del consentimiento); la tarjeta visual es de
  `solventa-frontend`. **CA-W01-02 gana el actor** que le faltaba. Móvil (biometría, refresh) y API Gateway quedan
  documentados como pendientes en `docs/autenticacion-usuarios.md`.
- **Cobros en el back-office (HU-W05, KAN-28, 2026-10-16):** CA-W05-01 (tabla de cobros por póliza con su estado) y
  CA-W05-02 (reintentar con la misma clave) **cumplidos en el backend**: `cobros(polizaId, estado)` y `reintentarCobro`
  en el BFF, solo BACKOFFICE con token. Cada reintento —manual o automático— **concilia** primero con la pasarela, lo
  que cierra un doble cobro posible cuando la pasarela cobraba pero respondía después de los 700 ms; además, los
  intentos con el circuito abierto ya no cuentan (una caída larga dejaba cobros `pendiente` para siempre). Detalle y
  paso a paso en `docs/cobros-conciliacion.md`. **Aviso de planificación:** exponer cobros en el BFF no era una tarea
  del plan del sprint (las de backend de HU-W05 son T-W05-2 a 5). Estimación ~9,5 h frente a las 2 h de T-W05-5 y las
  3 h de holgura: hay que registrarla en Jira bajo KAN-28.
- **Ajustes de cobros (KAN-28, 2026-10-17):** `clave_idempotencia` se renombra a `id_intento_cobro` en todo el producto
  (en la pasarela, `referencia_comercio`), con migraciones de expansión y contracción en PAYMENTS y el evento
  `cobro.estado-cambiado` en `version: 2`. El BFF suma `crearCobro` (solo BACKOFFICE con token; el cliente envía
  `idIntentoCobro`), y PAYMENTS responde 409 si el mismo intento llega con otros datos. **Tampoco estaba en el plan del
  sprint:** hay que registrar estas horas en Jira bajo KAN-28.
- **Nombre del cliente en los cobros (KAN-28, 2026-10-17):** el back-office ve a quién se le cobra. AUTH guarda el nombre
  del CLIENTE cifrado por campo (decisión D-04 en su versión local: llave de entorno, Cloud KMS pendiente), PAYMENTS guarda
  solo el `cliente_id` y el BFF compone `cobros.cliente { id nombre }` en lotes. **Tampoco estaba en el plan del sprint:**
  registrar las horas en Jira bajo KAN-28. La relación póliza→cliente la declara quien ordena el cobro hasta que exista el
  servicio de pólizas.
- **Reglas de la oferta de HU-W01 (KAN-24, 2026-10-17, decididas con el equipo):** CA-W01-05 ya no queda cubierto
  solo por fallas de RISK/RATING. El **historial crediticio es la única fuente obligatoria**: si no llega ni en línea
  ni de caché, RATING responde `datos_insuficientes` y el BFF devuelve `ErrorOferta` con reintento; si llega y falla
  cualquier otra (incluidos los antecedentes judiciales), la oferta es PRELIMINAR. Las 5 fuentes se siguen consultando
  en paralelo (en orden sumarían hasta 5 × 700 ms). Además, el BFF valida la solicitud (documento, valor, plazo de 1 a
  360 meses y dirección) antes de consultar nada, y RATING usa el plazo y la ciudad de la dirección en la prima con
  factores **ilustrativos** (ver `services/rating/README.md`). El `consolidador-fuentes` verifica en AUTH que el
  consentimiento siga vigente antes de reintentar una fuente (regla #7). Sin cambios de contrato.
- **Reintento de cobros rechazados (KAN-28, 2026-10-17, decidido con el equipo según el prototipo W05-01):** un cobro
  `rechazado` se puede reintentar a mano hasta 3 veces; el `pendiente` lo sigue reintentando el sistema solo. Como la
  pasarela es idempotente por referencia, cada reintento va con la suya (`{idIntentoCobro}-rN`, migración `0006` de
  PAYMENTS). Contrato v0.6: `Cobro.reintentosRestantes`. No se construye captura de tarjeta (CA-W05-03): el prototipo no
  la tiene y la prima se factura con la cuota del crédito (W03-02b). `scripts/sembrar_cobros.py` deja los datos de la demo.
- **Nombre propio (KAN-28, 2026-10-18):** cada usuario ve su nombre al iniciar sesión (`Usuario.nombre` en
  `iniciarSesion` y `yo`, cifrado en AUTH y fuera del JWT). `Cobro.idTransaccion` llegó aparte con el reintento de rechazados.
  Los pagos siguen siendo solo del BACKOFFICE. Pedido por el front (KAN-207). **Tampoco estaba en el plan del sprint:**
  registrar las horas en Jira bajo KAN-28.
- **Medios de pago para la demo (KAN-208, 2026-10-18):** cada cliente de la siembra tiene una tarjeta de prueba tokenizada por el
  mock de la pasarela (`POST /v1/tokens`); PAYMENTS guarda el token cifrado (`medios_pago`, migración `0007`, misma llave de
  AUTH) y el cobro se crea con `medioPagoId`. Contrato v0.8: `Cobro.medioPago { marca ultimos4 }`. El mock suma controles de la
  demo: estado de cada tarjeta (rechaza con su motivo) y latencia configurable. **No** hay captura de tarjeta en el backend
  (CA-W05-03 sigue fuera de alcance): ningún endpoint del BFF ni de PAYMENTS acepta un número de tarjeta. Fuera del plan del
  sprint: registrar las horas en Jira bajo KAN-208.
