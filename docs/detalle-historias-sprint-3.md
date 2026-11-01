# Detalle de historias del Sprint 3 (refinamiento, PROPUESTA para validar con el equipo)

> Es el documento que las tareas de Jira de los sprints 1 y 2 citan como «Detalle de historias del Sprint N» y que
> para el Sprint 3 no existía. Se construye con las fuentes de la Entrega 7 (Plan de Trabajo y Historias de Usuario,
> PlanningV1.2, Prototipado v23), la Hoja de Trabajo de Arquitectura (Entrega 5) y el enunciado del proyecto. Esas
> fuentes viven fuera del repo, en un espacio de contexto no versionado.
>
> **Es una propuesta: no se creó ni se cambió nada en Jira.** Los `CA-*` y los pesos de este documento se vuelven
> oficiales cuando el equipo los acepte. Complementa a [`sprint-3.md`](sprint-3.md) y
> [`refinamiento-sprint-3.md`](refinamiento-sprint-3.md); donde difieran en el alcance, **gana este** (sigue el plan
> oficial).

## 1. Alcance oficial del Sprint 3

Objetivo del plan: terminar el onboarding móvil, la mesa de siniestros web y el siniestro paramétrico, y entregar.
Tres semanas (2–22 nov 2026, festivos 2 y 16 nov), 85 h netas, compromiso 39 pts.

| HU | Jira | Pts | Horas del plan | Estado de partida |
|---|---|---|---|---|
| HU-M01 · Onboarding y verificación biométrica (cierre) | KAN-31 | 13 | 25 | Esqueleto del Sprint 2; faltan cámara, KYC y resultado |
| HU-W04 · Siniestros asistidos y peritaje | KAN-27 | 13 | 25 | Backend `claims` y mesa web hechos en local; faltan tareas abiertas y staging |
| HU-M04 · Siniestro paramétrico automático | KAN-38 | 13 | 25 | Sin tareas en Jira; solo existe `POST /indemnizaciones` |
| HU-M06 · Prestadores y asistencia en sitio (condicional) | KAN-36 | 6 | 11 | Sin tareas; no existe API ni mapas |
| Regresión y entrega final (T-R-1 a T-R-4) | — | — | 8 | — |

Regla de recorte si la capacidad no alcanza: **primero M06, luego M02, luego M04**. Arrastres (W03, W07) y M02 se
tratan en la sección 7.

## 2. HU-M01 · Onboarding y verificación biométrica (cierre)

- **Historia:** como cliente asegurado, quiero registrarme escaneando mi documento y haciendo prueba de vida, para
  verificar mi identidad bajo la regulación colombiana.
- **Prioridad / tamaño:** HH (alta-alta) · 13 pts. **Habilitadoras:** HA-LAT-002 (EC009, EC010), HA-SEG-002 (EC022),
  HA-SEG-004 (EC025).
- **Componentes:** app Flutter, `onboarding` (puerto y adaptador KYC sobre el ACL, stub Truora), AUTH (scopes JWT),
  `almacenamiento` (fotos y video por URL firmada, ADR-10).
- **Dependencias:** consentimiento vigente antes de consultar (regla 7), ACL con corte de 700 ms, `almacenamiento`
  encendido en staging (`crear_almacenamiento = true`).

### Tareas y pesos (peso = pts · horas Jira a 8,7 h/pt · valor = VN de la HU × pts tarea ÷ 13)

Son las tareas que ya existen en Jira (`S2-M01-1…8`, en «KAN Sprint 2»). El esqueleto de la app (T-M01-1, 3 h) se hizo en el Sprint 2 y no tiene tarea propia; los 13 puntos de la historia quedan repartidos en estas 8.

| Jira | Plan | Actividad | Rol | Plan (h) | Pts | Horas Jira |
|---|---|---|---|---|---|---|
| KAN-231 | T-M01-2 | Bienvenida y autorización de datos con registro del consentimiento (M01-01, M01-02) | Web y móvil | 2 | 1 | 8,5 |
| KAN-232 | T-M01-3 | Captura del documento por ambas caras (M01-03, M01-04) | Móvil | 4 | 2,5 | 22 |
| KAN-233 | T-M01-4 | Prueba de vida con la cámara frontal (M01-05) | Móvil | 3 | 2 | 17,5 |
| KAN-234 | T-M01-5 | Puerto y adaptador de KYC (stub tipo Truora) en el ACL y endpoint de onboarding | Líder técnico | 5 | 3 | 26 |
| KAN-235 | T-M01-6 | Verificación, resultado y activación de biometría (M01-06 a M01-08) | Móvil | 3 | 2 | 17,5 |
| KAN-236 | T-M01-7 | Cifrado de datos personales y biométricos y scopes JWT del onboarding | Líder técnico | 2 | 1 | 8,5 |
| KAN-237 | T-M01-8 | Pruebas: flutter_test, Firebase Test Lab y k6 (EC009, EC010) | Backend | 2 | 1 | 8,5 |
| KAN-238 | T-M01-9 | Documentación técnica y revisión de código | Backend | 1 | 0,5 | 4,5 |
| | | **Total** | | **22** | **13** | |

### Criterios de aceptación propuestos

| ID | Dado / Cuando / Entonces | Pantalla | EC / HA |
|---|---|---|---|
| CA-M01-01 | Dado un usuario nuevo, cuando abre la app, entonces ve la autorización de tratamiento de datos personales y no avanza sin aceptarla. | M01-01 | HA-SEG-001 |
| CA-M01-02 | Dado que aceptó, cuando continúa, entonces el consentimiento se registra (append-only) **antes** de consultar cualquier fuente o proveedor. | M01-02 | EC022 |
| CA-M01-03 | Dado el paso de documento, cuando captura el anverso y el reverso, entonces ambas caras quedan en `almacenamiento` por URL firmada y se envían al KYC. | M01-03, M01-04 | — |
| CA-M01-04 | Dado el paso de prueba de vida, cuando graba con la cámara frontal, entonces el video llega al proveedor KYC/AML y el cliente ve que se está verificando. | M01-05 | EC009 |
| CA-M01-05 | Dado el KYC lento o caído, cuando el proveedor no responde en 700 ms, entonces el cliente recibe un estado «verificación pendiente» con reintento y nunca un 5xx. | M01-06 | EC009, EC010 |
| CA-M01-06 | Dado un resultado aprobado, cuando termina la verificación, entonces se muestra la confirmación, se crea la identidad y se ofrece activar la biometría del dispositivo. | M01-07, M01-08 | — |
| CA-M01-07 | Dado un resultado rechazado, cuando termina, entonces se indica el motivo comprensible y se permite repetir sin repetir la autorización. | M01-07 | — |
| CA-M01-08 | Dado el onboarding, cuando se guardan los datos personales, entonces están cifrados en reposo (campo + KMS) y no aparecen en logs ni en métricas. | — | EC022 |
| CA-M01-09 | Dado un token del onboarding, cuando se usa fuera de su alcance, entonces el JWT con scopes lo rechaza. | — | EC025 |

## 3. HU-W04 · Siniestros asistidos y peritaje

- **Historia:** como operador de siniestros, quiero evaluar las evidencias enviadas desde el móvil para aprobar,
  rechazar o derivar a un perito, para resolver el siniestro del cliente.
- **Prioridad / tamaño:** HL (alta-baja) · 13 pts. **Habilitadoras:** HA-LAT-003 (EC011, EC012), HA-MOD-003 (EC030),
  HA-INT-004 (EC035).
- **Estado:** el backend (`claims`, MongoDB, eventos `siniestro.*`, `POST /indemnizaciones`) y la mesa web están
  hechos en local ([`plan-completar-hu-w04.md`](plan-completar-hu-w04.md)). Lo abierto: staging, el visto bueno de
  Frans sobre PAYMENTS, la renovación de la URL firmada de evidencias y la geoetiqueta desde el EXIF (con HU-M04/M06).

### Tareas y pesos

| ID | Actividad | Rol | Plan (h) | Pts | Horas Jira |
|---|---|---|---|---|---|
| T-W04-1 | CLAIMS: modelo del siniestro y expediente, API de bandeja y detalle sobre la réplica | Backend | 5 | 3 | 26 |
| T-W04-2 | Evidencia multimedia geoetiquetada con URLs firmadas | Web | 3 | 1,5 | 13 |
| T-W04-3 | Aprobar, rechazar y derivar a perito; asignación con SLA; orden de pago hacia W05 | Backend | 4 | 2 | 17,5 |
| T-W04-4 | Eventos de dominio de siniestros con schema versionado | Web | 3 | 1,5 | 13 |
| T-W04-5 | API versionada `/v1` en el BFF Web | Web | 2 | 1 | 8,5 |
| T-W04-6 | Bandeja, detalle, asignar perito y confirmaciones, con motivo de rechazo | Web | 5 | 2,5 | 22 |
| T-W04-7 | Pruebas de integración, E2E del operador y k6 (EC011, EC012) | Web | 2 | 1 | 8,5 |
| T-W04-8 | Documentación técnica y revisión de código | Móvil | 1 | 0,5 | 4,5 |
| | **Total** | | **25** | **13** | |

### Criterios de aceptación propuestos

| ID | Dado / Cuando / Entonces | Pantalla | EC / HA |
|---|---|---|---|
| CA-W04-01 | Dado un siniestro, cuando el operador abre el expediente, entonces ve la evidencia multimedia con su geoetiqueta. | Detalle | EC011 |
| CA-W04-02 | Dado la bandeja, cuando se consulta con carga normal, entonces responde con p95 dentro del umbral de EC011/EC012 leyendo de la secundaria. | Bandeja | EC011, EC012 |
| CA-W04-03 | Dado un siniestro en revisión, cuando el operador lo aprueba, entonces PAYMENTS registra **una** orden de pago (idempotente) y se emite `siniestro.aprobado`. | Detalle | EC035 |
| CA-W04-04 | Dado un siniestro, cuando el operador lo rechaza, entonces debe indicar el motivo, que queda en la resolución y en el evento. | Detalle | — |
| CA-W04-05 | Dado un caso complejo, cuando el operador lo deriva, entonces asigna un perito con SLA y el perito queda en el expediente. | Asignar perito | — |
| CA-W04-06 | Dado el evento de un siniestro, cuando se publica, entonces se valida contra su schema versionado antes de salir. | — | EC035 |
| CA-W04-07 | Dado un usuario sin rol BACKOFFICE, cuando llama a la mesa, entonces recibe 403. | — | EC030 |
| CA-W04-08 | Dado el cambio de versión de la API, cuando el cliente usa `/v1`, entonces las rutas anteriores siguen respondiendo como alias. | — | EC030 |

## 4. HU-M04 · Siniestro paramétrico automático

- **Historia:** como cliente con póliza paramétrica, quiero recibir el pago y el aviso cuando el clima o el vuelo
  cruzan el umbral, sin reclamar, para tener la indemnización de inmediato.
- **Prioridad / tamaño:** 13 pts · valor 8 %. **Habilitadoras:** HA-ESC-002 (ingesta de eventos con contrapresión),
  HA-INT-004. **Escenarios:** EC016 (≥ 1.000.000 de eventos en 10 min sin pérdida, reducido en pruebas).
- **Riesgo R8:** la saga paramétrica y su compensación. **Código existente:** `claims` y `POST /indemnizaciones`
  idempotente. **Falta:** fuente de eventos, regla paramétrica, ingesta con contrapresión, dispersión (hoy fuera de
  alcance de PAYMENTS) y push.

### Tareas y pesos

| ID | Actividad | Rol | Plan (h) | Pts | Horas Jira |
|---|---|---|---|---|---|
| T-M04-1 | Ingesta de eventos de clima y vuelos por el ACL + Pub/Sub con contrapresión (ESC-002) | Backend | 5 | 3 | 26 |
| T-M04-2 | Cruce con pólizas y umbral | Backend | 4 | 2 | 17,5 |
| T-M04-3 | Liquidación con PAYMENTS y evento con schema (INT-004) | Backend | 3 | 1,5 | 13 |
| T-M04-4 | Compensación / saga y su prueba | Backend | 3 | 1,5 | 13 |
| T-M04-5 | Notificaciones SNS/FCM y registro del dispositivo | Backend | 3 | 1,5 | 13 |
| T-M04-6 | App: notificación, celebración y detalle | Móvil | 4 | 2 | 17,5 |
| T-M04-7 | Pruebas k6 de la ráfaga reducida (EC016) e `integration_test` del push | Backend | 2 | 1 | 8,5 |
| T-M04-8 | Documentación | Backend | 1 | 0,5 | 4,5 |
| | **Total** | | **25** | **13** | |

### Criterios de aceptación propuestos

| ID | Dado / Cuando / Entonces | Pantalla | EC / HA |
|---|---|---|---|
| CA-M04-01 | Dado un evento de clima o vuelo, cuando llega, entonces se ingesta sin perder ninguno aun en ráfaga (contrapresión). | — | EC016, ESC-002 |
| CA-M04-02 | Dado un evento que cruza el umbral de una póliza paramétrica vigente, cuando se cruza, entonces se crea **un** siniestro paramétrico (idempotente por evento y póliza). | — | — |
| CA-M04-03 | Dado un siniestro paramétrico, cuando se liquida, entonces PAYMENTS registra la indemnización y se publica el evento con schema versionado. | — | INT-004 |
| CA-M04-04 | Dado que la liquidación falla, cuando se detecta, entonces la saga compensa y el siniestro queda en un estado reintentable, sin pago doble. | — | R8 |
| CA-M04-05 | Dado el cliente con dispositivo registrado, cuando se liquida, entonces recibe la notificación push y ve la pantalla de celebración. | Notificación | — |
| CA-M04-06 | Dado el push, cuando el cliente lo abre, entonces ve el detalle del pago (monto, evento y póliza) sin datos personales de terceros. | Detalle | — |
| CA-M04-07 | Dado el proveedor de notificaciones caído, cuando falla el envío, entonces el pago no se bloquea y el aviso se reintenta. | — | — |

## 5. HU-M06 · Prestadores y asistencia en sitio (condicional)

- **Prioridad / tamaño:** 6 pts · 11 h · se recorta primero. **Habilitadora:** HA-LAT-003. No existe API de
  prestadores ni de mapas.

| ID | Actividad | Rol | Plan (h) | Pts | Horas Jira |
|---|---|---|---|---|---|
| T-M06-1 | API de prestadores: los 3 más cercanos (LAT-003) | Backend | 2 | 1 | 8,5 |
| T-M06-2 | Mapa con pines y lista | Móvil | 3 | 2 | 17,5 |
| T-M06-3 | Formulario de envío y reenvío | Móvil | 2 | 1 | 8,5 |
| T-M06-4 | Estados confirmada, no confirmada y cancelación | Backend | 2 | 1 | 8,5 |
| T-M06-5 | Pruebas con la API de mapas simulada | Backend | 1 | 0,5 | 4,5 |
| T-M06-6 | Documentación | Backend | 1 | 0,5 | 4,5 |
| | **Total** | | **11** | **6** | |

| ID | Dado / Cuando / Entonces | Pantalla | EC / HA |
|---|---|---|---|
| CA-M06-01 | Dado el permiso de ubicación, cuando el cliente pide asistencia, entonces ve los 3 prestadores más cercanos en mapa y lista. | Mapa | LAT-003 |
| CA-M06-02 | Dado sin permiso de ubicación, cuando pide asistencia, entonces puede indicar la dirección a mano. | Mapa | — |
| CA-M06-03 | Dado un prestador elegido, cuando envía la solicitud, entonces ve su estado y puede reenviarla o cancelarla. | Estado | — |
| CA-M06-04 | Dado el proveedor de mapas caído, cuando falla, entonces se muestra la lista sin mapa, no un error. | Mapa | — |

## 6. Regresión y entrega final (8 h)

| ID | Actividad | Rol | Horas |
|---|---|---|---|
| T-R-1 | Regresión E2E web de los journeys en alcance | Web | 2 |
| T-R-2 | Regresión E2E móvil (`integration_test`) | Móvil | 2 |
| T-R-3 | Validaciones k6 de EC016 y EC017 reducidas | Backend | 2 |
| T-R-4 | Documentación final, video y entrega | Líder | 2 |

## 7. Arrastres y otras historias

- **HU-W03 (KAN-26) y HU-W07 (KAN-30):** hechas en código y en local; lo que falta es lo que necesita staging y la
  integración con el portal del socio. No consumen capacidad nueva salvo cierre.
- **HU-M02 (KAN-32, 6 pts):** autenticación biométrica nativa. AUTH tiene `POST /sesiones/renovar` pero no revocación;
  la app aún no usa `local_auth`. Es la siguiente en el orden de recorte tras M06.

## 7b. Claves en Jira (cargadas el 2026-10-31)

El sprint «KAN Sprint 3» (id 36, 3–23 nov 2026) existe en Jira con las tareas nuevas. Las de M01 y W04 siguen en «KAN Sprint 2» y pasan al 3 al cerrarlo si quedan abiertas.

| Historia | Tareas en Jira |
|---|---|
| HU-M01 | KAN-231 a KAN-238 (`S2-M01-1…8`) |
| HU-W04 | KAN-242 a KAN-249 (`S2-W04-1…8`) |
| HU-M04 | KAN-255 a KAN-262 (`S3-M04-1…8`) |
| HU-M06 | KAN-263 a KAN-268 (`S3-M06-1…6`) |
| Regresión y entrega | KAN-269 a KAN-272 (`S3-R-1…4`, sin puntos) |

## 8. Riesgos y decisiones abiertas

| # | Riesgo / decisión | Qué se necesita |
|---|---|---|
| R8 | Saga paramétrica y compensación | Probarla con fallo inyectado (CA-M04-04) antes de dispersar |
| R10 | Pruebas móviles (cámara, biometría, push) | Firebase Test Lab o dispositivo; sin ellas M01-04 y M04-05 quedan sin verificar |
| D-M04 | Fuente real de eventos de clima/vuelos | Decisión de producto; mientras tanto, stub |
| D-M06 | Proveedor de mapas | Decisión de producto; mientras tanto, simulado |
| D-DISP | Dispersión del pago (W05-3 cambiada) | Visto bueno de Frans sobre PAYMENTS |
| D-ASIG | Responsables de M02, M04 y M06 | Reparto del equipo |

## 9. Definición de listo y de hecho

Se usan sin cambios el DoR y el DoD del plan oficial (sección 2.1). Esta HU está lista para comprometer cuando el
equipo acepte los `CA-*` y los pesos de este documento y se asignen responsables.
