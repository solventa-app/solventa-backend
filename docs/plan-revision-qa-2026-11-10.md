# Plan de corrección · Revisión QA (2026-11-10)

> **Estado: decisiones cerradas por Frans (2026-11-10), listo para ejecutar.** Cubre backend y web.
> Cada causa se contrastó con `develop` (backend `039b6b0`, web `d993acc`) y, donde dice «en vivo», con el stack local
> (`docker compose --profile servicios`) y la web contra el BFF real (Cypress con `cypress.integracion.config.ts`).
> Sigue a [`plan-revision-qa-2026-10-31.md`](plan-revision-qa-2026-10-31.md); la siguiente versión libre del contrato es
> **v0.42**.

## 1. Hallazgos

| # | Punto de QA | Qué pasa hoy | Causa verificada |
|---|---|---|---|
| Q-01 | **El cliente no puede cotizar** | En vivo: llena el formulario, pulsa «Consultar oferta» y W01-02 dice «No hay una solicitud en curso para consultar» | T6 (`6043d78`) vacía `captura.documentoIdentidad` cuando la cuenta ya tiene documento (el BFF lo toma de AUTH), pero la guarda de `ConsultandoFuentes.vue:24` sigue exigiéndolo. **Con la guarda corregida, el resto funciona en vivo:** oferta → «Firmar» → W03-02 en su sesión → firma → W03-03. Por API, el mismo cliente llega a `EN_FIRMA` sin errores. El spec de integración del cliente solo comprobaba los campos, no el envío: por eso pasó |
| Q-02 | **Documentos firmados en PDF** | El contrato firmado y la póliza se **regeneran** con `fpdf2` cada vez que se piden; no se guarda ningún PDF | Decisión W03-9 («sin GCS ni costo nuevo»). El tipo `poliza-pdf` existe en `contracts/tipos-archivo.json`, pero `activo: false` |
| Q-03 | **Plazo del siniestro y aprobación automática** | El contador baja cada 30 s y al llegar a cero dice «Vencido», **pero el caso no cambia**: ni en local ni a tiempo en staging | (a) En local **nadie llama** a `POST /siniestros/_aprobar-vencidos`: solo lo dispara Cloud Scheduler (`infra/tareas.tf`). (b) En staging corre cada 15 min (`aprobar_siniestros_cron`), así que tarda hasta 15 min. (c) La web no vuelve a consultar el caso al llegar a cero: sigue en «Vencido» hasta recargar. (d) La semilla deja plazos de 6 a 72 h: QA no tiene un caso que venza en minutos |
| Q-04 | **Doble scroll en el portal de socios** | En vivo, a 1280 y 390 px: la página ya no se desplaza (B6 funcionó), pero **la barra de pestañas tiene su propio scroll vertical** (36/28 px) en **todas** las pestañas; en la guía el visor del PDF (`h-[70vh]`) se desplaza dentro de `main`, que también se desplaza | `Pestanas` usa `overflow-x-auto`, que fuerza `overflow-y: auto`, y el contenido mide 8 px más que la barra. La guía embebe un `iframe` de alto fijo dentro de un contenedor desplazable |
| Q-05 | **Responsive del portal** | A 390 px: el header parte idioma y moneda en otra línea, la barra muestra su scroll horizontal y el footer fijo ocupa ~130 px. Solo quedan 482 de 800 px para el contenido | Cabecera, franja, pestañas y footer son fijos también en móvil |
| Q-06 | **Orden Pruebas/Producción** | La barra dice «API Pruebas · API Producción»; «Ver datos de» dice «Producción · Pruebas» | `PortalSocio.vue` y `ENTORNOS` en `PanelSocio.vue` usan órdenes distintos |
| Q-07 | **Fuentes consultadas: cliente** | El cliente **ya no** ve la opción en el menú y la ruta W01-04 es solo para ASESOR | Hecho en web #151. Solo falta cubrirlo con una prueba |
| Q-08 | **Fuentes consultadas: historial del asesor** | W01-04 muestra solo la oferta en memoria o la de la **última propuesta**. No hay historial, ni paginación, ni el «una sola → detalle», ni estado vacío por historial | **No existe un registro de solicitudes.** La oferta vive en Redis con TTL (`oferta-aceptable:*`, RATING) y desaparece; las propuestas solo cubren las ofertas aceptadas. AUTH sí guarda cada consentimiento con su actor (`actor_usuario_id`), pero no el resultado por fuente |
| Q-09 | **Pólizas emitidas: asesor y cliente** | G-06 existe para ambos roles con filtros, paginación y estado vacío. Pero: (a) tocar una fila no abre nada: solo «Ver PDF» o «Retomar»; (b) el asesor con una sola póliza ve la tabla; (c) no hay pantalla de detalle para el asesor (W03-04 solo sirve con enlace o sesión de CLIENTE) | Falta la pantalla de detalle. El backend ya basta: `propuesta(id)` trae la póliza, la prima, la cobertura y el titular, filtrado por rol |

## 2. Decisiones

| # | Decisión | Recomendación | Estado |
|---|---|---|---|
| D-1 | **Guardar los PDF firmados** (Q-02). Reemplaza W03-9 | Guardar **dos documentos inmutables** en `almacenamiento`: el **contrato firmado** (con la evidencia de la firma) al firmar y la **póliza** al emitir. Se sirven desde el bucket y ya no se regeneran. Llevan datos personales (nombre del titular): bucket con CMEK, como el resto. Retención: vigencia de la póliza más el plazo legal | **Cerrada:** sí, y **cifrados con fuerza**: cada PDF se cifra en POLICY antes de subirlo (AES-256-GCM con clave de datos propia por documento, envuelta por Cloud KMS, el mismo sobre de D-04) y el bucket además usa CMEK. El bucket nunca ve el PDF en claro |
| D-2 | **Dónde vive el historial de fuentes** (Q-08) | **RISK**, que ya escribe el riesgo y guarda el consentimiento: una colección `consultas` por solicitud (consentimiento, actor, fecha, tipo de oferta y el **estado** de cada fuente, nunca sus datos). El nombre del titular va cifrado, como en los demás almacenes. Al revocar o vencer el consentimiento se borra el perfil, pero la consulta se conserva como rastro | **Cerrada:** RISK |
| D-3 | **Cadencia de la aprobación automática** (Q-03) | Cloud Scheduler cada minuto (cuesta lo mismo: se cobra por trabajo, no por ejecución) y un disparador equivalente en el compose | Propuesta |
| D-4 | **«Una sola» en pólizas y fuentes** | Sin filtro y con `total = 1`: se abre directo el detalle (`replace`, para que «Atrás» no rebote). Con filtro, siempre la tabla | Propuesta |
| D-5 | **Orden de los entornos** | **Pruebas primero, Producción después**, en la barra y en «Ver datos de». El panel sigue abriendo en Producción | Propuesta |

## 3. Tareas

Cada tarea es una rama y un PR hacia `develop`. Tamaños S, M y L, como en `docs/sprint-3.md`.

**T1 · El cliente vuelve a cotizar** · Q-01 · Bloqueante · S · web · KAN-26
1. `useCotizacion`: marcar `documentoDeCuenta` en la captura; la guarda de W01-02 acepta documento **o** documento de cuenta.
2. Integración: el spec del cliente envía el formulario y llega a W03-02 en su sesión. El pago se cubre en T6.

**Hecho cuando:** un cliente con documento en la cuenta cotiza y llega a firmar en el navegador, contra el stack real.

**T2 · Plazo del siniestro que de verdad se cumple** · Q-03 · Alta · M · backend, infra y web · KAN-27
1. Compose: un disparador local que llame a `_aprobar-vencidos` cada minuto, como el Scheduler.
2. `aprobar_siniestros_cron = "* * * * *"` (D-3). El Terraform se escribe; se aplica desde el pipeline.
3. Web: al llegar a cero, W04-02 vuelve a pedir el caso cada 30 s hasta verlo resuelto y entonces muestra «Aprobado
   automáticamente por vencimiento». La bandeja hace lo mismo con sus filas.
4. Semilla: opción para dejar un caso PENDIENTE que venza en 2 min, para que QA lo pruebe sin tocar la base.
5. Prueba de integración: un caso que vence pasa a APROBADO, con `automatica = true` y su orden en PAYMENTS. Un caso
   derivado no cambia (D-3b).

**Hecho cuando:** en local, un caso que llega a cero queda aprobado en menos de un minuto y la pantalla lo muestra sin
recargar.

**T3 · Portal de socios: un solo scroll, responsive y orden** · Q-04, Q-05, Q-06 · Alta · M · web · KAN-30
1. `Pestanas`: `overflow-y-hidden` y altura que contenga el subrayado; sin barra visible en móvil (desliza con el dedo).
2. Guía: el visor ocupa el alto disponible de `main` (`flex-1`, sin `70vh`) para que haya un solo desplazamiento.
3. Swagger de Pruebas y de Producción: revisar que no agregue un tercer scroll.
4. Móvil (< 640 px): idioma y moneda en una línea con el título o en un menú; footer no fijo, al final del contenido.
5. D-5: el mismo orden en `PortalSocio.vue` y en `ENTORNOS`.
6. Cypress: a 1280 y 390 px, en las cuatro pestañas, **solo `main` se desplaza** y no hay scroll horizontal. Es la
   medición que se usó para este diagnóstico.

**T4 · Pólizas emitidas con detalle** · Q-09 · Alta · L · web · KAN-26
1. Pantalla **G-06b · Detalle de la póliza** (`/polizas/:propuestaId`, ASESOR y CLIENTE) con `propuesta(id)`: número,
   estado, titular (solo el asesor), prima y cobertura convertidas (consulta), plazo, vigencia, fecha de emisión y «Ver
   PDF». Mensajes del estado cuando no está emitida.
2. Toda la fila de G-06 abre el detalle (y con teclado). «Retomar» del asesor se queda como acción de la fila.
3. D-4 para los dos roles: una sola → detalle; ninguna → estado vacío con «Nueva cotización».
4. La tabla pasa a tarjetas por debajo de 640 px. Textos en ES y EN.
5. Cypress con mocks: cero, una y varias pólizas, para cada rol; paginación de 10.

**T5 · Historial de fuentes consultadas** · Q-07, Q-08 · Alta · L · backend, contrato v0.45 y web · KAN-24 · Depende de D-2

> **Backend hecho (2026-11-10):** contrato **v0.45** (la v0.42 ya era de la póliza de auto y la v0.43 y v0.44 de KAN-26),
> RISK `0006`, BFF y Newman `historial-fuentes-w01`. Dos ajustes frente a lo escrito abajo: **(a)** el BFF registra la
> consulta **tras** la oferta (necesita el tipo y el estado de cada fuente que entregó RATING, para no duplicar su lógica en
> RISK) y, si RISK falla, la oferta sigue; **(b)** el **nombre del titular no se guarda**: el asesor solo escribe el
> documento al cotizar, así que la fila muestra el documento enmascarado. Falta la web (punto 4).

1. **Contrato v0.42 (aditivo):** `consultasFuentes(pagina, tamano): PaginaConsultas!` y `consultaFuentes(id)`, solo
   ASESOR y solo las suyas (el filtro sale del token). Cada consulta: id, fecha, nombre del titular, documento
   enmascarado, tipo de oferta y fuentes (`id`, `nombre`, `origen`, `estado`, `capturadoEn`).
2. **RISK:** guarda la consulta al calcular el perfil, con migración idempotente e índice por `(actor, fecha)`.
3. **BFF:** expone las dos consultas; un CLIENTE recibe `SIN_PERMISO`.
4. **Web W01-04:** historial paginado (10 por página); tocar una fila abre el detalle actual de fuentes. Con una sola
   consulta, directo al detalle; con ninguna, estado vacío con «Nueva cotización» (D-4). Tarjetas en móvil. ES y EN.
5. Pruebas: pytest de RISK y del BFF, Newman (asesor ve lo suyo, otro asesor no, cliente rechazado) y Cypress (cliente
   sin la opción del menú y sin acceso a la ruta).

**T6 · Documentos firmados guardados** · Q-02 · Media · L · backend, contrato y almacenamiento · KAN-26 · Depende de D-1
1. Catálogo: activar `poliza-pdf` y crear `contrato-firmado` (solo sube POLICY; leen el titular, su asesor y BACKOFFICE).
2. POLICY: al firmar guarda el contrato firmado; al emitir, la póliza. **Cifrado (D-1):** AES-256-GCM con una clave de
   datos por documento, envuelta por Cloud KMS (en local, la clave fija de desarrollo), con el id de la propuesta como
   dato asociado; se guarda la clave envuelta junto al id del archivo en POLICY y se verifica la etiqueta al descifrar.
   El tipo de contenido en el bucket es `application/octet-stream`; el PDF solo existe en claro en memoria al servirlo. Si el almacenamiento falla, la emisión sigue y la
   subida se reintenta (regla 4); mientras tanto el PDF se genera como hoy.
3. El PDF se sirve desde el almacenamiento con la misma autorización de hoy (`contrato.pdf` y `documentoUrl`).
4. Actualizar W03-9 en `plan-completar-hu-w03.md`, `docs/almacenamiento.md` y revisar EC022.
5. Recorrido real del cliente de punta a punta (`w03-recorrido.cy.ts`, pendiente de A7): cotizar, firmar, pagar en la
   pasarela del compose y abrir el PDF guardado. Nota: `cy.origin` falla con un error interno de Cypress 16 al entrar
   a la pasarela de `:8103`; hay que resolverlo o pagar por la API en esa prueba.

## 4. Orden

```
T1 (bloqueante) ─► T4 ─► T6
T2   T3                      ← independientes, se pueden hacer en paralelo
D-2 ─► T5
```

Al cerrar: Vitest, Cypress con mocks, Newman y la integración en verde, con evidencia en `docs/evidencias/`, y
`PROJECT_STATUS.md` y `CLAUDE.md` al día.

## 5. Riesgos

- **D-1 guarda datos personales en el bucket.** Hoy no hay ningún PDF en reposo; exige CMEK (pendiente de verificar en
  staging) y revisar EC022.
- **Atlas M0 (100 operaciones/s):** la colección `consultas` agrega una escritura por cotización. Es poca carga, pero se
  suma a la del batch nocturno.
- **Aprobación cada minuto:** genera órdenes de indemnización sin revisión humana más rápido. Es el riesgo ya aceptado
  en D-3a del plan anterior.
