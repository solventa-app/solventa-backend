# Plan de corrección · Revisión QA del equipo (2026-10-31)

> **Estado: propuesta, con 7 decisiones por confirmar (sección 2).** Cubre backend y web. Sale de la revisión manual del
> equipo sobre el stack local. Cada causa se contrastó con el código de `develop` y, donde se indica, con el stack
> corriendo (web por Vite en `:5173`, gateway en `:8080`). Nada se ha corregido todavía.

## 1. Resumen

| Resultado | Puntos | Cuáles |
|---|---:|---|
| Defecto confirmado | 12 | Moneda (OP-02, OP-03, OP-05, AS-01, SO-02, TR-01), AS-03, AS-06, AS-07, AS-08, SO-01, OP-06 |
| Funcionalidad pendiente que ya estaba prevista | 5 | AS-05, CL-01, CL-02, CL-03, OP-04 |
| Mejora de diseño | 3 | AS-04, OP-07, SO-03 |
| Funciona como se diseñó: hay que decidir o explicar | 3 | OP-01, AS-02, SO-04 |
| **Hallazgos nuevos de esta revisión de QA** | 4 | QA-01 a QA-04, uno de ellos bloqueante |

**Lo más importante:**

1. **El recorrido de firma y pago no funciona en el navegador**, aunque por API sí. Fallan cuatro cosas seguidas: el
   contrato no se ve, firmar con trazo falla, la página de retorno de la pasarela queda en blanco y el PDF de la póliza
   no abre. Por eso van primero (bloque 0).
2. **El selector de moneda es decorativo.** No está conectado a nada, pero el backend ya tiene lo necesario
   (`convertirMontos`, `actualizarPreferencias`, `Usuario.moneda`). Es un solo arreglo en la web, no uno por pantalla.
3. **Casi todo lo del CLIENTE y «Pólizas emitidas» es front pendiente** (F-F3, F-F3b y F-F4 de `docs/sprint-3.md`). El
   backend y el contrato ya lo soportan.

**Por qué no lo detectaron las pruebas:** Newman y k6 van directo al BFF, y Cypress usa mocks. Ninguna prueba recorre en
un navegador real la firma y el pago. La cubre el bloque 7.

## 2. Decisiones por confirmar

Cada una trae la opción recomendada. El plan asume esa opción; si se elige otra, cambia el bloque indicado.

| # | Decisión | Recomendación | Alternativa | Bloque |
|---|---|---|---|---|
| D-1 | ¿Quién ve el estado del sistema? Hoy es del BACKOFFICE por diseño (W07-04, CA-W07-14 y 15) | Retirar la tarjeta y la ruta de la web del operador; dejar `estadoSistema` en el BFF y ajustar esos CA | Crear un rol `ADMIN` que la vea: más costoso, toca AUTH, BFF y contrato | 5 |
| D-2 | ¿Qué se convierte al cambiar de moneda? | Todo lo que se **consulta**. Lo que se **firma, cobra o captura** sigue en la moneda original (contrato, autorización de cobro, pasarela, «Monto del crédito») con el selector deshabilitado | Mostrar también el valor de referencia «≈ USD» en esas pantallas | 1 |
| D-3 | ¿Qué significa «gestionar automáticamente» un siniestro cuyo plazo llega a cero? | **Escalar, no decidir:** marcarlo vencido, subirlo en la bandeja y avisar. Aprobar genera una orden de indemnización y rechazar notifica al asegurado: ninguna debería ocurrir sola | Aprobar o rechazar automáticamente: pide validación jurídica antes | 5 |
| D-4 | ¿Se mantiene la sesión por pestaña? | Sí, y añadir que cerrar sesión cierre también las pestañas duplicadas | Exigir login en cada pestaña duplicada | 2 |
| D-5 | «Reenviar enlace» cuando el cliente ya firmó | Quitarlo como se pidió, pero en `FIRMADA` mostrar **«Enviar enlace de pago»**. El enlace dura 30 min y `FIRMADA` significa que falta pagar: sin ese botón, quien pierda el enlace no puede pagar | Quitarlo sin reemplazo y aceptar ese riesgo | 2 |
| D-6 | Correo y teléfono en «Mis datos» | Mostrarlos completos, solo en `yo` y solo al propio usuario; el documento sigue enmascarado. Modifica W03-16 («legibles solo por POLICY») y exige revisar EC022 | Mostrarlos enmascarados | 4 |
| D-7 | Navegación del portal de socios | La propuesta de la sección 3.4. No hay Figma: requiere visto bueno de diseño | Conservar la estructura y solo fijar las migas | 6 |

## 3. Matriz de hallazgos

Severidad: **Bloqueante** (impide terminar un recorrido), **Alta** (requisito no cumplido), **Media** (funciona mal o
incompleto), **Baja**. Evidencia: **Vivo** (reproducido en el stack), **Código** (confirmado leyendo el código),
**Sin reproducir**.

### 3.1 Operador

| ID | Reportado | Sev. | Evidencia | Causa | Corrección |
|---|---|---|---|---|---|
| OP-01 | Se ve System Status | Media | Código | No es un error: `HomeOperador.vue` trae la tarjeta y `/operador/estado` admite BACKOFFICE, según W07-04. El operador y `admin.solventa` son el mismo rol | D-1 |
| OP-02 | La moneda vuelve a COP al navegar | Alta | Código | `HeaderPantalla` recibe `moneda` por prop con valor `COP` y ninguna pantalla la conecta. Al cambiar de ruta se vuelve a montar | Bloque 1 |
| OP-03 | Conciliación no cambia valores | Alta | Código | La web no llama `convertirMontos` en ninguna parte | Bloque 1 |
| OP-04 | Bandeja sin paginador | Media | Código | `BandejaSiniestros.vue` no pagina. El de conciliación está escrito dentro de esa pantalla | Extraer `Paginador.vue` y usarlo en ambas, 10 por página. Con la semilla de 10 casos debe mostrarse igual («1 de 1») |
| OP-05 | Siniestros no cambia valores | Alta | Código | Igual que OP-03 | Bloque 1 |
| OP-06 | Semilla en revisión sin bloqueo de perito | Media | Código | Es un dato malo. Solo derivar lleva a `EN_REVISION`, pero la semilla trae 2 de 3 casos así con `resolucion: null` (`services/claims/semilla/siniestros.json`, líneas 93 y 523). El botón se bloquea según la derivación, no según el estado | Darles asignación y perito en la semilla, con una prueba que lo exija |
| OP-07 | El tiempo restante debe correr y gestionarse al llegar a cero | Alta | Código | Tres vacíos. **Reloj:** se recalcula cada 60 s, y con más de 24 h solo muestra días y horas, así que parece fijo. **Backend:** nadie actúa al vencer. **Derivados:** el contador sigue corriendo aunque el caso espera al perito | Bloque 5 y D-3 |

### 3.2 Asesor

| ID | Reportado | Sev. | Evidencia | Causa | Corrección |
|---|---|---|---|---|---|
| AS-01 | Cotización no cambia valores | Alta | Código | Igual que OP-03 | Bloque 1 |
| AS-02 | ¿La sesión entre pestañas es por seguridad? | Baja | Código, sin reproducir | **No es una medida de seguridad.** El token vive en `sessionStorage`, que es por pestaña: una pestaña nueva abierta a mano no lo tiene. El navegador sí lo copia al **duplicar** una pestaña. Confirmar cómo se abrió la pestaña en la revisión | D-4 |
| AS-03 | Cerrar el modal antes de emitir pierde el proceso | Alta | Código | En `IDENTIDAD_VERIFICADA` la tarjeta de `RevisarOfertaAceptada.vue` solo ofrece «Actualizar estado»: no hay forma de volver a emitir. Además la pantalla no recupera la propuesta al recargar, y «Reenviar enlace» tras recargar abre el modal en el paso 1 y pide otra vez el código | Botón «Emitir» en `IDENTIDAD_VERIFICADA`, recuperar la propuesta con `propuestas` al entrar y abrir el modal en el paso correcto |
| AS-04 | Sobra «Actualizar estado» | Media | Código | La pantalla no usa SSE. POLICY ya publica en `avisos:asesor:<usuario>`, pero `useAvisos.ts` solo entiende cobros | Generalizar `useAvisos`, quitar el botón y consultar una vez al reabrir, igual que W05-01 |
| AS-05 | «Pólizas emitidas» no lleva a nada | Alta | Vivo | `G-06` no tiene componente (`PantallaPendiente`): es F-F4, pendiente. El backend responde: `propuestas` devolvió datos reales del asesor | Bloque 3 |
| AS-06 | El contrato PDF no se ve | Bloqueante | Vivo | `/propuestas` y `/polizas` no están en el proxy de Vite ni en `vercel.json`. Por el proxy web responde `200 text/html`; por el gateway, un `%PDF` válido. `urlDelPdf` solo mira `.ok` y mete el HTML en el iframe. **Por la configuración, también afecta a Vercel** | Añadir ambas rutas en los dos sitios y rechazar lo que no sea `application/pdf` |
| AS-07 | Error al confirmar el contrato | Bloqueante | Vivo | El bucket local rechaza la subida desde el navegador: el preflight responde **403** por `x-goog-content-length-range` y `x-goog-if-generation-match`, y `fake-gcs` no los admite. TRAZO es el modo por defecto. Con una imagen válida, sin navegador, la firma queda `FIRMADA`; el modo NOMBRE también. Confirmar que el mensaje visto fue el de error de subida | Añadir `-cors-headers` a `fake-gcs` en el compose. Verificar en staging el CORS real, que está configurado y nunca se probó |
| AS-08 | «Reenviar enlace» aparece ya firmado | Media | Código | La condición incluye `FIRMADA`, igual que `reenviar_enlace` de POLICY | D-5 |

### 3.3 Cliente

| ID | Reportado | Sev. | Evidencia | Causa | Corrección |
|---|---|---|---|---|---|
| CL-01 | Faltan datos personales | Alta | Vivo | `MiPerfil.vue` pide 6 campos. `yo` ya ofrece `documentoEnmascarado`, moneda e idioma. Correo y teléfono están en AUTH, pero el contrato los excluye a propósito (W03-16). Los clientes de onboarding tienen además nacimiento y país en `onboarding` | Bloque 4 y D-6. Mostrar solo lo que exista: los creados por BACKOFFICE no tienen datos de onboarding |
| CL-02 | Cotización sin datos precargados | Alta | Código | Es F-F3b. `validar()` exige digitar el documento aunque el BFF ya cotiza con el de la cuenta si se envía null | Documento, nombre y correo de solo lectura. Monto, plazo y dirección siguen editables: no están guardados |
| CL-03 | Debe pasar directo a firmar | Alta | Código | La pantalla sigue la decisión vieja D-2 («solo ASESOR»), que W03-32 y W03-33 reemplazaron. El contrato y k6 ya usan el flujo con sesión | Bloque 4 |

### 3.4 Socio

| ID | Reportado | Sev. | Evidencia | Causa | Corrección |
|---|---|---|---|---|---|
| SO-01 | Header y footer no quedan fijos | Media | Vivo | En el panel el documento mide 2.073 px en una ventana de 660: se desplaza toda la página. En documentación mide 660, sin problema. **Causa probable:** la `<table class="sr-only">` de `GraficoLlamadas.vue` es absoluta y se sale de `main` | Dar `relative` a `main` y comprobar en Cypress que el documento no supere la ventana |
| SO-02 | Ajustar moneda | Alta | Código | Igual que OP-03, y además `convertirMontos` no admite al socio: usa REST con su propio token | Bloque 1, con una operación REST nueva |
| SO-03 | Navegación confusa; la miga se pierde | Media | Vivo | Las migas están dentro del `main` que se desplaza. «Producción/Pruebas» aparece con dos significados: datos del panel y API de la documentación | Propuesta abajo, D-7 |
| SO-04 | El Swagger no pide token | Media | Vivo | **No es un hueco de seguridad.** Sin token, `/v1/consumo` y `/v1/cotizaciones` responden **401**. `SwaggerSocios.vue` añade el token del entorno sin mostrarlo, porque el CSS oculta «Authorize» a propósito (plan W07, 5.4). Si falla al pedirlo, la petición sale sin token y el usuario no entiende el 401 | Mostrar «Autenticado con la clave de Pruebas · vence 10:45» y un error claro si no se obtiene. Añadir a Cypress que «Try it out» lleva el token |

**Propuesta de navegación (D-7).** Una sola barra fija para todo el portal, con el componente `Pestanas`:

```
┌ Solventa | Portal de socios                          ES  COP ┐  fijo
│ Banco Andino Demo · Socio de distribución      Cerrar sesión │  fijo
│ [Panel] [Guía de integración] [API Pruebas] [API Producción] │  fijo
├──────────────────────────────────────────────────────────────┤
│ Ver datos de:  (•) Producción  ( ) Pruebas     ← solo en Panel│  desplazable
│ …tarjetas, consumo, ventas…                                  │
├──────────────────────────────────────────────────────────────┤
│ Footer                                                       │  fijo
└──────────────────────────────────────────────────────────────┘
```

- Con la barra siempre visible sobran las migas y el botón «Documentación de la API».
- En el panel, el selector pasa a llamarse «Ver datos de» y se dibuja distinto, para no parecer navegación.
- Un layout `PortalSocio.vue` reúne el marco común y se conservan las URL actuales.

### 3.5 Transversal

| ID | Reportado | Sev. | Evidencia | Causa | Corrección |
|---|---|---|---|---|---|
| TR-01 | Revisar la moneda en toda la aplicación | Alta | Vivo | `convertirMontos` funciona en vivo (`185.000.000 COP` → `45.121,95 USD`); falta usarlo | Bloque 1, con la lista de pantallas |

### 3.6 Hallazgos nuevos de QA

| ID | Hallazgo | Sev. | Evidencia | Causa | Corrección |
|---|---|---|---|---|---|
| QA-01 | **Tras pagar, la página queda en blanco** | Bloqueante | Vivo | El BFF devuelve `/pago/retorno/{id}?sesion=…` y la web solo tiene `/pago/retorno`. Como `RetornoPago` no se monta, **no se llama `confirmarPago`: el cliente paga y la póliza no se emite** desde esa pantalla | Ruta `/pago/retorno/:propuestaId?` y prueba de punta a punta |
| QA-02 | El PDF de la póliza no abre por enlace | Alta | Código | `/polizas/{id}/documento` exige JWT, pero W03-04 no tiene sesión y envía `X-Enlace-Firma`. Respondería 401 | Aceptar el token del enlace en esa ruta, con el mismo control de `contrato.pdf`, y reflejarlo en `docs/autenticacion-usuarios.md` |
| QA-03 | USD se redondea sin centavos | Media | Vivo | `formatearMonto` fija 0 decimales. `48.500 COP` son `11,83 USD` y se verían como `12` | Usar los decimales propios de cada moneda |
| QA-04 | Un token mal formado da 503 | Baja | Vivo | POLICY responde 422, el BFF no lo reconoce y devuelve 503. Debe ser 404, como un token inexistente (regla 4) | Mapear ese caso |

## 4. Plan por bloques

Tamaños relativos, como en `docs/sprint-3.md`. Cada fila es una rama y un PR hacia `develop`.

| Bloque | Contenido | Puntos | Repos | Tamaño | Depende de |
|---|---|---|---|---:|---|
| **0 · Destrabar firma y pago** | Rutas de PDF, CORS de `fake-gcs`, ruta de retorno, PDF de póliza con enlace | AS-06, AS-07, QA-01, QA-02, QA-04 | web, backend | S | — |
| **1 · Moneda** | Estado global, conversión, formato y REST de socios | OP-02, OP-03, OP-05, AS-01, SO-02, TR-01, QA-03 | web, backend, contrato | L | D-2 |
| **2 · Emisión del asesor** | Retomar, SSE, reenvío y cierre entre pestañas | AS-03, AS-04, AS-08, AS-02 | web | M | D-4, D-5 |
| **3 · Pólizas** | Pantalla `G-06` para los tres roles, con SSE | AS-05 | web | L | 2 |
| **4 · Cliente** | Datos, precarga y flujo con sesión | CL-01, CL-02, CL-03 | web, backend, contrato | L | 0, D-6 |
| **5 · Operador** | Paginador, semilla, plazo y estado del sistema | OP-01, OP-04, OP-06, OP-07 | web, backend, contrato | M | D-1, D-3 |
| **6 · Socio** | Layout, navegación y token visible | SO-01, SO-03, SO-04 | web | M | D-7 |
| **7 · Cierre** | Recorridos en navegador real, documentos y regresión | Todos | web, backend | M | 0 a 6 |

Orden: **0 primero y solo**, porque desbloquea probar todo lo demás. Después 1, 2, 4 y 5 pueden ir en paralelo; 3 tras 2 y
6 cuando lo apruebe diseño. Los bloques 0, 2, 3 y 4 tocan W03; el 5, W04; el 6, W07; y el 1 cruza todas las pantallas.
**Conviene arrancar el 1 pronto:** cambia cómo se muestran los montos, y cuanto más tarde, más pantallas hay que rehacer.

### Bloque 1 · Moneda

1. `useMoneda.ts`, como `idioma`: se lee de `Usuario.moneda` al iniciar sesión, se guarda con `actualizarPreferencias` y,
   sin sesión, en `localStorage`. `HeaderPantalla` se conecta a él directamente, sin prop.
2. Un componente `MontoVisible` reemplaza a `formatearMonto` en pantallas de consulta. Agrupa las peticiones (máximo 50
   por llamada, `MAXIMO_MONTOS`) y guarda las ya convertidas.
3. Si la conversión falla, muestra el monto original con un aviso, nunca un error (regla 5).
4. **Se convierte:** W01-02 a W01-04, W04-02 y confirmaciones, W05-01, «Pólizas» y ventas del socio. **No se convierte
   (D-2):** W03-02, W03-02b, W03-03, W03-04 y el campo del crédito.
5. En W05-01 el total se suma primero en la moneda original y luego se convierte. Solo se convierte la página visible y
   el total, para no chocar con el límite.
6. Socio: operación REST nueva en `socios-v1.json`, Envoy y `openapi-socios.yaml`, que no descuente presupuesto.

### Bloque 4 · Flujo del CLIENTE

1. Contrato v0.23 con los campos de CL-01 (D-6). AUTH expone el contacto solo al propio usuario.
2. `useEnlaceFirma` admite dos autorizaciones: token del enlace, o sesión más `propuestaId`, también para el PDF.
3. En la oferta completa, el botón principal del CLIENTE es «Firmar»: `aceptarOferta` → `emitirPropuesta` → `/firma`.
   «Contactar a un asesor» queda como opción secundaria.
4. Atender `EN_REVISION` y `NO_EMITIDA` con su mensaje, sin pasar a firmar.
5. Si un cliente no tiene documento en su cuenta, explicarlo y ofrecer el asesor.
6. Actualizar W03-19 y D-2 en los documentos: esas decisiones quedan reemplazadas.

### Bloque 5 · Plazo del siniestro (con D-3 aprobada)

1. **Web:** el contador muestra siempre minutos («1 d 5 h 12 min»), se actualiza cada 30 s y la bandeja muestra el
   vencimiento.
2. **Contrato v0.23:** `Siniestro.escaladoEn` y el evento `siniestro.sla-vencido`.
3. **CLAIMS:** `POST /siniestros/_vencer-dictamenes`, idempotente, disparado por Cloud Scheduler como los demás procesos
   periódicos (en Cloud Run no hay bucles de fondo). El Terraform se escribe, pero no se aplica.
4. **Derivados:** el contador se reemplaza por `vencimientoInforme` del perito.
5. Índice por `(estado, vence_dictamen_en)` con migración idempotente.

## 5. Contrato v0.23

Solo cambios aditivos, antes del código (regla 8):

| Cambio | Superficie | Bloque |
|---|---|---|
| Datos personales propios en `Usuario`, solo por `yo` | GraphQL | 4 |
| `Siniestro.escaladoEn` | GraphQL | 5 |
| `siniestro.sla-vencido` | Evento | 5 |
| Conversión de montos para socios | REST `socios-v1` | 1 |

## 6. Criterios de salida

| Bloque | Se da por hecho cuando |
|---|---|
| 0 | En un navegador real, con el stack local: el contrato se ve, se firma en los tres modos, se paga, vuelve a una pantalla con la póliza emitida y el PDF abre |
| 1 | Elegir USD se mantiene al navegar y al volver a iniciar sesión, y cambia los valores de todas las pantallas de consulta; las de firma y pago siguen en COP |
| 2 | Cerrar el modal o recargar en cualquier paso permite retomar; el estado cambia sin recargar y no hay «Actualizar estado» |
| 3 | Cada rol ve solo sus pólizas, filtra por estado, abre el PDF y retoma las pendientes |
| 4 | Un CLIENTE cotiza sin digitar sus datos y llega a póliza emitida sin código ni asesor |
| 5 | Los casos de la semilla se comportan como los nuevos; un caso vencido queda escalado una sola vez y sin decisión automática |
| 6 | Header, barra y footer siguen fijos al desplazar en todo el portal, y siempre hay una forma visible de volver |
| 7 | Vitest, Cypress con mocks, Newman y los recorridos reales pasan, con evidencia en `docs/evidencias/` |

**Bloque 7 añade** un `w03-recorrido.cy.ts` contra el stack real (ASESOR y CLIENTE, de la oferta a la póliza) y actualiza
`PROJECT_STATUS.md`, `CLAUDE.md` y los planes de W03, W04 y W07. Staging queda fuera: sigue pendiente de la decisión de
liberar. Allí habrá que verificar además el CORS real del bucket.

## 7. Riesgos

- **Vercel:** AS-06 se dedujo de `vercel.json`, sin probarlo en la nube. El arreglo debe verificarse al desplegar.
- **Sprint 3:** F-F3 y F-F4 ya estaban sin tamaño comprometido en Jira. Este plan los vuelve prioritarios y aún hay que
  reflejarlo en el tablero.
- **Tasas de cambio:** son de referencia y solo para mostrar. La pantalla debe decirlo para que nadie las tome por el
  valor cobrado.
- **Datos personales:** D-6 amplía quién puede leer correo y teléfono. Sigue siendo el propio titular, pero cambia una
  decisión documentada.
