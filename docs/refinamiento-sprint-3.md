# Refinamiento del Sprint 3 (borrador para validar con el equipo)

> **Estado:** borrador de refinamiento, 2026-10-31. Solo documentación: no se creó ni se cambió nada en Jira.
> **Lo que es dato y lo que es propuesta:** todo lo marcado **(Jira)** o **(código)** se leyó tal cual; todo lo marcado **PROPUESTA** lo redacté yo para
> que el equipo lo corrija. Los criterios oficiales (`CA-*`) están en un documento que **no pude leer** (ver 1.2): donde dependa de él, queda dicho.
> **Complementa a** [`sprint-3.md`](sprint-3.md) (alcance, detalle de pantallas, gráficos de cierre). Donde se contradigan en los pesos, **vale este documento** (ver 1.1).

## 1. Hallazgos que cambian la planeación

**1.1 Los pesos del equipo son por tarea, no por pantalla.** En Jira cada HU es la suma de sus **tareas** (`S2-<HU>-<n>`, tipo *Tarea*), y cada tarea
declara su estimación así: *«N pts · H h (8,7 h/pt, razón real del Sprint 1) · valor X % (valor de la HU × pts de la tarea ÷ pts de la HU)»*. Se comprobó
que suman: M02 = 2 + 1,5 + 1,5 + 0,5 + 0,5 = **6**; W07 = **6**; M01 = **13**; W04 = **13**; W03 = 6 + 4 + 3 = **13**. Es la misma metodología de
[`metricas-sprint-1.md`](evidencias/metricas-sprint-1.md) (puntos por tarea, valor por tarea proporcional a los puntos, burndown por la fecha `started`
del worklog). El reparto igual por pantallas de `sprint-3.md` (v1) era una aproximación mía y **queda reemplazado por los pesos por tarea de la sección 2**.

**1.2 Los criterios oficiales existen y tienen ID, pero no están en los repos.** Las tareas de Jira citan `CA-M02-01` a `07`, `CA-W07-01` a `09` y
`CA-W03-01` a `09` (documento «Detalle de historias del Sprint 2»). No está en los repositorios ni en Confluence, así que **no pude leer su texto**.
Antes dije que no existía ningún `CA-M*`: era incorrecto; solo no es accesible para mí. **M04 y M06 no tienen ningún `CA-*` en Jira ni tareas**: o el
documento del Sprint 3 aún no llegó, o hay que escribirlos. Lo que sí hay para ellos es el criterio de 1 a 3 viñetas de la Hoja de Trabajo de
Arquitectura, las habilitadoras (HA-*) y los escenarios de calidad (EC*).

**1.3 Los estados de Jira van detrás del código.** Ejemplos verificados:
- **HU-W07:** el plan de Daniel (`plan-completar-hu-w07.md`, 29/10) da por hechos en código y en local los bloques 0 a 9 (contrato, AUTH, POLICY, BFF `/v1`,
  salud, gateway, pruebas Newman, documentos). En Jira, `S2-W07-1` a `5`, `7` y `8` siguen en *Tareas por hacer*.
- **HU-W03:** los bloques 7 y 8 (brechas contra los criterios oficiales; aviso con PDF) están integrados; sus tareas `S2-W03-9` (KAN-240, 4 pts) y `S2-W03-10`
  (KAN-241, 3 pts) siguen en *Tareas por hacer*.
- **HU-W04 y HU-M01** figuran *Finalizada*, pero sus tareas de ingeniería están abiertas (`S2-W04-1` a `6` y `8` *En curso*; `S2-M01-1` a `8` casi todas
  *Tareas por hacer*), mientras el backend de ambas está integrado en `develop`.
Por eso **«lo que falta en Jira» no es una medida fiable** de lo que falta de verdad: antes de fijar el Sprint 3 hay que conciliar tarea por tarea (sección 2).

**1.4 Lo que existe hoy para M02, M04 y M06 (código en `develop`):**

| Pieza | Estado |
|---|---|
| App móvil: sistema de diseño, enrutador, `flutter_secure_storage`, teclado numérico y indicadores de PIN, fila de prestador | Existe (`lib/core/design_system`) |
| App móvil: `local_auth` (biometría), `firebase_messaging` (push), mapas, geolocalización | **No están en `pubspec.yaml`** |
| App móvil: pantallas de M01 y sesión persistida | En ramas `feat/KAN-31-*`, no en `develop` |
| AUTH: `POST /sesiones/renovar` | Existe; **no hay revocación de token** (solo revocación de consentimiento) |
| CLAIMS + PAYMENTS: aprobar un siniestro pide la orden de pago (`POST /indemnizaciones`, idempotente por siniestro) | Existe (HU-W04) |
| PAYMENTS: dispersión y balance de indemnizaciones | **Fuera de alcance «hasta el Sprint 3, HU-W04/HU-M04»** (README de PAYMENTS) |
| Eventos de siniestro (`siniestro.aprobado`, `rechazado`, `derivado`) con schema | Existen |
| Fuente de eventos de clima o vuelos, regla paramétrica, póliza paramétrica, ingesta con contrapresión | **No existen** (solo stubs de Open Finance, Open Data, pasarela, firma, KYC y notificaciones) |
| Push (FCM o un stub) en backend o app | **No existe** |
| Prestadores, mapas y el componente MAPS del ACL | **No existen** |

**1.5 Prioridades en Jira (anexo del backlog):** M04 *High*; M02 *Low*; M06 *Lowest*. El plan de trabajo de la semana 5 ya los trataba como backlog futuro
(M02 y M06) y a M04 como «candidato a experimento de Proyecto Final 2».

## 2. Tareas y pesos por HU

Equivalencia de horas que usa el equipo (observada en Jira): 0,25 pt → 2 h · 0,5 → 4,5 · 1 → 8,5 · 1,5 → 13 · 2 → 17,5 · 2,5 → 22 · 3 → 26 · 4 → 35.
«Realidad» es lo que encontré en el código o en los documentos; «Jira» es el estado que figura hoy.

### HU-M02 · KAN-32 · 6 pts · valor 3 % · prioridad Low · sin responsable · sin sprint

| Tarea | Pts | Horas | Valor | Jira | Realidad / nota |
|---|---:|---:|---:|---|---|
| KAN-225 `S2-M02-1` Lectura biométrica (`local_auth`) y token en Keystore o Keychain (M02-01) · CA-M02-01, 02 · EC022 | 2 | 17,5 | 1 % | Por hacer | `local_auth` no está en `pubspec`; `flutter_secure_storage` sí. Depende de KAN-171 (esqueleto de la app, *Finalizada*) |
| KAN-226 `S2-M02-2` No reconocida, PIN del teléfono y acceso concedido (M02-01a, 02, 02b, 03) · CA-M02-03, 04 | 1,5 | 13 | 0,75 % | Por hacer | Teclado y PIN del diseño listos; falta definir qué PIN es (1.4 y sección 5) |
| KAN-227 `S2-M02-3` AUTH: renovación y revocación del token con scopes por rol · CA-M02-06, 07 · EC025 | 1,5 | 13 | 0,75 % | Por hacer | Renovación existe; **falta la revocación** y que un usuario suspendido no pueda renovar |
| KAN-228 `S2-M02-4` Pruebas en dispositivo físico y Firebase Test Lab | 0,5 | 4,5 | 0,25 % | Por hacer | Evidencia de EC022 en hardware real |
| KAN-229 `S2-M02-5` Documentación técnica y revisión de código | 0,5 | 4,5 | 0,25 % | Por hacer | — |
| **Total** | **6** | **52,5** | **3 %** | | Sin `CA-M02-05` citado por ninguna tarea: confirmar si cubre algo |

### HU-M04 · KAN-38 · 13 pts · valor 8 % · prioridad High · sin responsable · sin tareas

No existe ninguna tarea. **PROPUESTA de desglose** que suma 13 pts, con las horas y el valor del esquema del equipo:

| Tarea propuesta | Pts | Horas | Valor | Cubre |
|---|---:|---:|---:|---|
| `S3-M04-1` Ingesta de eventos paramétricos: fuente simulada de clima y vuelos en el ACL, publicación en Pub/Sub con contrapresión y cero pérdida | 3 | 26 | 1,85 % | HA-ESC-002 (KAN-55) · EC016 |
| `S3-M04-2` Regla paramétrica determinística y póliza paramétrica en CLAIMS (umbral, idempotencia por evento y póliza) | 2 | 17,5 | 1,23 % | Criterio «cruza eventos con pólizas activas» |
| `S3-M04-3` Liquidación automática: orden de pago (reutiliza `POST /indemnizaciones`) y **dispersión y balance** de la indemnización | 2,5 | 22 | 1,54 % | Criterio «liquida el pago»; hoy fuera de alcance en PAYMENTS |
| `S3-M04-4` Notificación push: puerto y stub en el ACL (tipo FCM) y registro del dispositivo | 2 | 17,5 | 1,23 % | Criterio «notifica al cliente al instante» |
| `S3-M04-5` App y BFF móvil: push, celebración y detalle de la indemnización (M04-01 a 03) y `indemnizacion(id)` | 1,5 | 13 | 0,92 % | Criterio de la pantalla de celebración |
| `S3-M04-6` Eventos de dominio con schema versionado (`evento.parametrico.detectado`; reutiliza `siniestro.aprobado`) | 0,5 | 4,5 | 0,31 % | HA-INT-004 (KAN-74) · EC035 |
| `S3-M04-7` Pruebas: integración, carga reducida de EC016 (supuesto declarado), E2E | 1 | 8,5 | 0,62 % | EC016 |
| `S3-M04-8` Documentación técnica y revisión de código | 0,5 | 4,5 | 0,31 % | — |
| **Total** | **13** | **113,5** | **8 %** | |

### HU-M06 · KAN-36 · 6 pts · valor 3 % · prioridad Lowest (condicional) · sin responsable · sin tareas

**PROPUESTA de desglose** (suma 6 pts):

| Tarea propuesta | Pts | Horas | Valor | Cubre |
|---|---:|---:|---:|---|
| `S3-M06-1` ACL MAPS: puerto y adaptador del proveedor de mapas y prestadores (stub), con circuit breaker; cálculo de los 3 más cercanos y su tiempo estimado | 1,5 | 13 | 0,75 % | Criterio «lista los 3 más cercanos»; aislamiento del proveedor (Hoja de Arquitectura) |
| `S3-M06-2` BFF móvil: `prestadoresCercanos` y `solicitarAsistencia` (idempotente), lecturas sobre la réplica | 1,5 | 13 | 0,75 % | HA-LAT-003 · EC011, EC012 |
| `S3-M06-3` App: permiso de ubicación, mapa con pines y lista de 3 prestadores (M06-01) | 1,5 | 13 | 0,75 % | Criterio «interfaz de mapa con pines» |
| `S3-M06-4` App: formulario, envío y reenvío, confirmada, no confirmada, cancelar y cancelada (M06-02 a 06) | 1 | 8,5 | 0,5 % | Pantallas del prototipo |
| `S3-M06-5` Pruebas en dispositivo y k6 de EC011 y EC012 | 0,25 | 2 | 0,13 % | EC011, EC012 |
| `S3-M06-6` Documentación técnica y revisión de código | 0,25 | 2 | 0,13 % | — |
| **Total** | **6** | **51,5** | **3 %** | |

### HU-W07 · KAN-30 · 6 pts · valor 15 % · Daniel

| Tarea | Pts | Horas | Valor | Jira | Realidad |
|---|---:|---:|---:|---|---|
| KAN-217 `S2-W07-1` API Gateway de GCP con JWT (ES256, JWKS) y scopes · CA-W07-01 | 1,5 | 13 | 3,75 % | Por hacer | Bloques 1, 2, 4 y 7 del plan: hechos en local |
| KAN-218 `S2-W07-2` Cuotas y rate limiting por credencial de socio | 1 | 8,5 | 2,5 % | Por hacer | Hecho en local (AUTH, presupuesto, 429) |
| KAN-219 `S2-W07-3` API REST B2B `/v1` con timeout de 700 ms y fallback | 1,5 | 13 | 3,75 % | Por hacer | Hecho en local (BFF `/v1`, modo prueba) |
| KAN-220 `S2-W07-4` RATING como contexto delimitado, desplegable solo (EC028) | 0,5 | 4,5 | 1,25 % | Por hacer | Verificar con Daniel |
| KAN-221 `S2-W07-5` Autoescalado de Cloud Run en Terraform (EC015) | 0,5 | 4,5 | 1,25 % | Por hacer | Verificar con Daniel |
| KAN-222 `S2-W07-6` Portal técnico del socio (W07-01 a 03) · CA-W07-09 | 0,5 | 4,5 | 1,25 % | Por hacer | **Pendiente**: lo hace Daniel después, en el front |
| KAN-223 `S2-W07-7` Pruebas de contrato, k6 (EC001, EC002, EC015, EC018) y OWASP | 0,25 | 2 | 0,62 % | Por hacer | Newman `socios-w07` en verde; k6 y OWASP por verificar |
| KAN-224 `S2-W07-8` Procedimiento de alta de socio y documentación técnica | 0,25 | 2 | 0,62 % | Por hacer | Documentos hechos (bloque 9) |
| **Total** | **6** | **52** | **15 %** | | Pendiente real (según el plan): portal, staging (`clave-firma-socios`, `crear_gateway_socios`) y la integración con el portal |

### HU-W03 · KAN-26 · 13 pts · valor 15 % · Frans

| Tarea | Pts | Jira | Realidad |
|---|---:|---|---|
| `S2-W03-3`, `-4`, `-5`, `-7` (KAN-211, 212, 213, 215) | 4 | Finalizada | Hecho |
| `S2-W03-1`, `-2`, `-6` (KAN-209, 210, 214) | 0,5 + 0,5 + 0,5 | En curso | Backend y pantallas hechos con mocks; **falta la integración real** |
| `S2-W03-8` (KAN-216) Pruebas de contrato, E2E y k6 | 0,5 | En revisión | Newman 372/372 y k6 de EC005/EC007 hechos en local (Felipe); **E2E de Cypress contra el compose por hacer** |
| `S2-W03-9` (KAN-240) Brechas contra los criterios oficiales | 4 | Por hacer | **Hecho** (bloque 7), integrado |
| `S2-W03-10` (KAN-241) Aviso con PDF y contacto cifrado | 3 | Por hacer | **Hecho** (bloque 8), integrado |
| `S2-W03-11`, `-12` (KAN-251, 252) Modal del asesor; retorno de la pasarela | sin puntos | En revisión | Hechos con mocks (front, PR #88) |
| **Trabajo real que falta y no está en Jira** (F-F3 a F-F7, F-B10, OPS-1 a 4) | **PROPUESTA** | — | Ver abajo |

**PROPUESTA de tareas para lo que falta** (no existen en Jira; al crearlas, HU-W03 pasaría de 13 a **23,5 pts**, como ocurrió el 25/10 al pasar de 6 a 13. Decidir
si se abren o se absorben):

| Tarea | Pts | Horas | Qué cierra |
|---|---:|---:|---|
| F-F3 Flujo del CLIENTE con sesión (rutas `/propuestas/:id/*`, «Firmar», retorno `/pago/retorno/:propuestaId`) | 2 | 17,5 | Criterio «cliente o asesor» de la HU |
| F-F3b Cédula de solo lectura en W01-01 (W03-34) | 0,5 | 4,5 | W03-34 |
| F-F4 «Mis pólizas» / «Pólizas emitidas» para los tres roles | 3 | 26 | W03-35 |
| F-F5 Avisos en vivo (SSE) en «Mis pólizas» | 1,5 | 13 | W03-37 |
| F-F6 Integración del front con el backend real y Cypress contra el compose | 1,5 | 13 | `S2-W03-8` completo |
| F-F7 Cierre documental del front | 0,5 | 4,5 | — |
| F-B10 Integración de punta a punta, ASESOR y CLIENTE | 1,5 | 13 | Evidencia de la HU |
| **Total propuesto** | **10,5** | **91,5** | |

## 3. Criterios de aceptación refinados (PROPUESTA)

Escritos en formato *Dado / Cuando / Entonces* a partir del criterio de la Hoja de Trabajo, las habilitadoras y los escenarios de calidad. **No sustituyen a
los `CA-*` oficiales**: hay que cruzarlos con ellos cuando alguien con acceso al documento los revise.

### HU-M02 · autenticación biométrica nativa
Criterio de partida (Hoja de Trabajo): *«Inicia sesión validando la biometría localmente usando el hardware seguro del teléfono (Keystore / Keychain)»*.
Habilitadoras: HA-SEG-005 (KAN-115), HA-SEG-004 (KAN-66). Escenarios: EC022, EC025.

1. **Ingreso con biometría.** *Dado* un cliente con sesión previa y la biometría habilitada, *cuando* abre la app, *entonces* aparece el diálogo nativo del
   sistema y, si el sensor valida, se desbloquea el token guardado y entra a su inicio sin escribir clave.
2. **El token vive en el hardware.** *Dado* un token de sesión, *cuando* se guarda, *entonces* queda en el Keystore (Android) o el Keychain (iOS), solo se
   puede leer tras la biometría y nunca aparece en preferencias, logs ni copias de seguridad. *Y* la huella o el rostro **no salen del teléfono** (validación local).
   Medida: EC022, cifrado 100 % en reposo.
3. **Biometría no reconocida.** *Dado* un intento fallido, *cuando* el sensor no reconoce al cliente, *entonces* la pantalla dice la causa y la acción
   (reintentar o usar el PIN) y no borra nada escrito. Tras N fallos seguidos se pasa al ingreso alterno (N, a definir).
4. **Ingreso alterno.** *Dado* que la biometría no está disponible o falló N veces, *cuando* el cliente ingresa el PIN, *entonces* accede si es correcto; si no, ve
   el intento restante y el bloqueo temporal al agotarlos (qué PIN es: pregunta abierta 5.1).
5. **Renovación.** *Dado* un token próximo a vencer, *cuando* la app lo renueva, *entonces* recibe uno nuevo con los mismos scopes de su rol, sin pedir biometría
   de nuevo si la sesión sigue vigente (usa `POST /sesiones/renovar`, que ya existe).
6. **Revocación.** *Dado* un usuario suspendido o con el rol cambiado, *cuando* intenta renovar o usar el token, *entonces* AUTH lo rechaza (100 % de las
   llamadas con autenticación fuerte, EC025) y la app lo lleva a iniciar sesión; el token revocado no sirve por ningún canal.
7. **Cambio de biometría del teléfono.** *Dado* que el cliente agrega o quita una huella o rostro en el sistema, *cuando* abre la app, *entonces* el token
   guardado queda invalidado y debe iniciar sesión de nuevo (comportamiento recomendado; confirmar, 5.1).
8. **UI.** Se cumplen los criterios de INTERFAZ, RETROALIMENTACIÓN, ACCESIBILIDAD y ADAPTATIVO de las 5 pantallas (M02-01, 01a, 02, 02b, 03) y los transversales.

### HU-M04 · siniestro paramétrico automático
Criterio de partida (Hoja de Trabajo): *cruza eventos externos (clima y vuelos) con pólizas activas; si cumple el umbral paramétrico, liquida el pago y
notifica al instante; la app recibe la notificación push y despliega la pantalla de celebración, sin formularios*. Habilitadoras: HA-ESC-002 (KAN-55),
HA-INT-004 (KAN-74). Escenarios: EC016, EC035.

1. **Detección.** *Dado* una póliza paramétrica vigente con su umbral (p. ej. retraso de vuelo mayor o igual a X minutos, o precipitación mayor o igual a Y mm),
   *cuando* llega un evento que lo cumple, *entonces* se crea un siniestro paramétrico sin intervención humana.
2. **Sin evento que cumpla, no hay pago.** *Dado* un evento bajo el umbral, de otra zona o de una póliza no vigente, *cuando* se procesa, *entonces* no se paga
   y queda trazado el motivo.
3. **Idempotencia.** *Dado* el mismo evento recibido dos veces (reintento del proveedor o de la cola), *cuando* se procesa, *entonces* se paga **una sola vez**.
4. **Liquidación.** *Dado* un siniestro paramétrico aprobado, *cuando* se liquida, *entonces* se genera la orden de pago (idempotente por siniestro), se
   dispersa al medio del cliente y se publica `siniestro.aprobado` con schema versionado (EC035). Sin datos de tarjeta en servidores propios.
5. **Ingesta bajo avalancha.** *Dado* un evento climático masivo, *cuando* llegan hasta 1.000.000 de eventos en 10 min, *entonces* la cola los absorbe **sin
   perder ninguno** y aplica contrapresión sin degradar el tráfico síncrono (EC016). En este proyecto se medirá a una escala reducida declarada.
6. **Notificación.** *Dado* el pago liquidado, *cuando* se confirma, *entonces* el cliente recibe la notificación push en un máximo de T segundos (T, a definir:
   «al instante») con el monto.
7. **Celebración sin formularios.** *Dado* que el cliente toca la notificación, *cuando* abre la app, *entonces* ve la pantalla de celebración con el monto
   depositado y el acceso al detalle (evento detectado y desglose del pago), sin llenar nada.
8. **Cliente sin la app abierta o sin permiso de notificaciones.** *Cuando* no puede recibir el push, *entonces* ve el resultado al abrir la app (el estado se
   consulta, no depende solo de la notificación).
9. **UI.** Se cumplen los criterios de las 3 pantallas (M04-01 a 03) y los transversales. La animación de demo de M04-01 (auto-avance de 6 s) es una licencia
   de presentación y **no** modela el comportamiento real.

### HU-M06 · geolocalización de prestadores y asistencia en sitio
Criterio de partida (Hoja de Trabajo): *interfaz gráfica de mapa con pines; lista los 3 proveedores más cercanos calculando tiempos estimados*.
Habilitadora: HA-LAT-003 (KAN-52). Escenarios: EC011, EC012.

1. **Mapa y cercanos.** *Dado* el permiso de ubicación concedido, *cuando* el cliente abre el mapa, *entonces* ve su posición, los pines de prestadores
   (médicos o mecánicos) y una lista de los **3 más cercanos** ordenados por tiempo estimado.
2. **Sin permiso de ubicación.** *Dado* el permiso denegado o el GPS apagado, *cuando* abre el mapa, *entonces* se le ofrece indicar la zona a mano y no
   queda en un callejón sin salida.
3. **Solicitar asistencia.** *Dado* un prestador elegido, *cuando* envía el formulario, *entonces* ve «Enviando solicitud» y después «Asistencia confirmada»;
   la solicitud es **idempotente** (reenviar no crea una segunda).
4. **No se pudo confirmar.** *Dado* un fallo o un tiempo agotado, *cuando* termina el envío, *entonces* ve «No pudimos confirmar» con la causa y el
   reintento (M06-03b «Reenviando solicitud»); nunca un error técnico crudo.
5. **Cancelar.** *Dado* una asistencia confirmada, *cuando* pide cancelarla, *entonces* se le pide confirmación (M06-05) y, al aceptar, ve «Asistencia
   cancelada» (M06-06).
6. **Proveedor de mapas aislado.** *Dado* que el proveedor externo no responde, *cuando* se consulta, *entonces* el ACL corta a los 700 ms con su circuit
   breaker y la app muestra un mensaje con reintento; cambiar de proveedor no cambia la app (Hoja de Arquitectura).
7. **Latencia.** La consulta de prestadores responde en p95 de 150 ms o menos y p99 de 300 ms o menos (EC011, EC012) sobre la réplica de lectura.
8. **UI.** Se cumplen los criterios de las 8 pantallas (M06-01 a 06) y los transversales.

### HU-W07 y HU-W03: qué falta para darlas por cerradas
- **W07:** el portal del socio (W07-01 a 03, `S2-W07-6`), la integración del portal con el compose sin mocks, y lo de staging (`clave-firma-socios` antes de la
  próxima liberación, `crear_gateway_socios`, `jwks_socios_url` y el alta de los socios con un Job). Confirmar con Daniel el estado de `S2-W07-4`, `-5` y `-7`.
- **W03:** la integración del front (F-F3 a F-F7), la de punta a punta (F-B10), las medidas en staging y el correo real (necesita un dominio verificado).

## 4. Dependencias y riesgos

| Riesgo | Efecto | Qué lo mitiga |
|---|---|---|
| M04 depende de piezas que no existen (fuente de eventos, regla, ingesta, dispersión, push) y unos 10 de sus 13 pts son de backend (tareas 1 a 4 y 6) | Es la HU de mayor peso y la de menos base; no se cierra de punta a punta si falta una sola pieza | Decidir ya las preguntas 5.2; empezar por `S3-M04-1` a `-3`; el push y la app pueden usar stubs |
| La app móvil de `develop` no tiene `local_auth`, push, mapas ni geolocalización | M02, M04 y M06 arrancan agregando dependencias nativas y probando en dispositivo | Una tarea corta de «dependencias nativas y permisos» antes de las pantallas; Firebase Test Lab ya está en las tareas |
| El compromiso supera la velocidad histórica (26 pts por sprint; ver `sprint-3.md`) | Sprint con cola | Retirar M06 (Lowest, condicional) y M02 (Low) antes que M04 (High) |
| Los estados de Jira no reflejan el código | Burndown y valor entregado engañosos | Conciliar las tareas de la sección 1.3 antes de arrancar y registrar el worklog con la fecha real (`started`) |
| Los criterios oficiales no se pudieron leer | Los criterios de la sección 3 podrían contradecirlos | Que quien tenga el documento cruce las secciones 3 y 5 con los `CA-*` |
| Sin responsable en M02, M04 y M06 | Nadie refina ni construye | Asignar antes del primer día |

## 5. Preguntas abiertas (decisiones que no puedo tomar yo)

**5.1 M02.** ¿El «PIN del teléfono» es la credencial del sistema operativo o un PIN propio de la app (el diseño trae teclado y 4 indicadores)? ¿Cuántos fallos
antes del ingreso alterno y cuánto dura el bloqueo? ¿Invalida el token un cambio de biometría del teléfono? ¿Qué cubre `CA-M02-05`?

**5.2 M04.** ¿La fuente de clima y vuelos será un stub propio (como los demás proveedores) o una API real? ¿Cuáles son los umbrales y los productos paramétricos
(¿hay un ramo en el catálogo?)? ¿A qué medio se dispersa la indemnización (hoy PAYMENTS solo cobra)? ¿Cuál es el proveedor de push (FCM o un stub) y el tiempo
máximo de «al instante»? ¿A qué escala se demuestra EC016 en este proyecto?

**5.3 M06.** ¿Qué proveedor de mapas (Google Maps u otro) y de dónde salen los prestadores (catálogo propio o un servicio externo)? ¿A quién llega «solicitar
asistencia»: a CLAIMS, al prestador o solo se simula?

**5.4 Alcance.** ¿Entran al Sprint 3 las tareas de ingeniería abiertas de W04 (`S2-W04-1` a `8`) y M01 (`S2-M01-1` a `8`) aunque sus HU figuren *Finalizada*?
¿Se abren como tareas los trabajos de la sección 2 de W03 (+10,5 pts) o se absorben?

**5.5 Documento oficial.** ¿Dónde está el «Detalle de historias del Sprint 2» (y el del 3, si existe) para cruzar los `CA-*`?

## 6. Listo para comprometer (después de este refinamiento)

| HU | Criterios funcionales | Criterios UI | Tareas con peso | Responsable | Plan técnico | Pendiente para estar lista |
|---|---|---|---|---|---|---|
| M02 | Propuesta (sección 3) + `CA-M02-*` oficiales sin leer | Sí | Sí (5, Jira) | **No** | Esbozo en 1.4 y 2 | Responsable; 5.1; cruce con `CA-M02-*` |
| M04 | Propuesta (sección 3); sin `CA-*` | Sí | **Propuesta** (8) | **No** | Esbozo en 2 | Responsable; 5.2; abrir las tareas; criterios oficiales |
| M06 | Propuesta (sección 3); sin `CA-*` | Sí | **Propuesta** (6) | **No** | Esbozo en 2 | Responsable; 5.3; confirmar que entra |
| W07 | `CA-W07-01..09` (sin leer) | Sí | Sí (8, Jira) | Daniel | Sí (`plan-completar-hu-w07.md`) | Conciliar estados en Jira; portal y staging |
| W03 | `CA-W03-01..09` | Sí | Sí + propuesta (7) | Frans | Sí (`plan-hu-w03-frans.md`) | Conciliar estados; decidir si se abren las tareas nuevas |
