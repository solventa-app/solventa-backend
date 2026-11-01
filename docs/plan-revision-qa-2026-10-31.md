# Plan de corrección · Revisión QA del equipo (2026-10-31)

> **Estado: planeación cerrada, lista para ejecutar.** Repartido entre dos desarrolladores, con todas las decisiones
> tomadas (sección 2).
> Cubre backend y web. Las causas se contrastaron con `develop` y, donde se indica, con el stack local.
> **Ya resuelto:** la semilla de siniestros (OP-06) está en el PR #472.

## 1. Reparto

| | Desarrollador A | Desarrollador B |
|---|---|---|
| **Perfiles** | **Asesor y Cliente** | **Operador y Socio** |
| **Además** | Firma y pago, que bloquean ambos perfiles | La base de moneda, que usan todos |
| **Contrato** | v0.24 | v0.23 |

**Lo primero en cada lista desbloquea el resto: A1 y B1 arrancan a la vez y no se pisan.** Cada tarea es una rama y un PR
hacia `develop`, y se hace en el orden indicado. Tamaños relativos S, M y L, como en `docs/sprint-3.md`.

### Dónde se cruzan

| Pieza | Dueño | Quién la usa | Regla |
|---|---|---|---|
| `useMoneda`, `montoVisible`, `HeaderPantalla`, `formato.ts` | B (B1) | A (A2) | A2 espera a que B1 esté en `develop` |
| `Paginador.vue` | B (B3) | A (A4) | A4 espera a que B3 esté en `develop` |
| `useAvisos.ts` generalizado | A (A3) | — | B no lo toca |
| `schema.graphql` y `socios-v1.json` | v0.23 = B, v0.24 = A | — | Nadie edita el contrato fuera de su PR. Si A llega primero, se intercambian los números |

```
A  A1 ──► A2 ──► A3 ──► A4 ──► A5 ──► A6 ──► A7        Asesor: A1 a A4 · Cliente: A5 a A7
          ▲              ▲
          │ B1           │ B3
B  B1 ──► B2 ──► B3 ──► B4 ──► B5 ──► B6 ──► B7 ──► B8  Operador: B2 a B5 · Socio: B6 a B8
```

Al final, **B9** se hace entre los dos: documentos, regresión y evidencia.

## 2. Decisiones

Todas cerradas por Felipe (2026-11-01).

| # | Decisión | Afecta |
|---|---|---|
| D-1 | **Estado del sistema:** el operador no debe ver ese menú. Se retiran la tarjeta y la ruta | B2 |
| D-2 | **Moneda:** todo lo que se consulta se convierte; lo que se firma, cobra o captura sigue en la moneda original, con el selector deshabilitado. La regla es la misma en toda la web | B1, A2, B4, B6 |
| D-3 | **Siniestro vencido:** al llegar a cero sin gestión, el sistema lo aprueba como si lo hubiera hecho el operador. Es un cambio automático de estado | B5 |
| D-3a | **Sin excepciones por verificaciones:** se aprueba aunque una verificación no cumpla. No revisar a tiempo es un error de procedimiento que asume el operador | B5 |
| D-3b | **Derivado a perito:** el plazo queda en pausa mientras esté derivado y el caso no se aprueba solo | B5 |
| D-4 | **Sesión por pestaña:** se mantiene, y cerrar sesión cierra también las pestañas duplicadas | A3 |
| D-5 | **Tras firmar:** se quita «Reenviar enlace» y en `FIRMADA` se muestra «Enviar enlace de pago», porque sin enlace vigente el cliente no puede pagar | A3 |
| D-6 | **«Mis datos»:** correo y teléfono completos, solo en `yo` y solo al propio usuario. Modifica W03-16 y exige revisar EC022 | A5 |
| D-7 | **Portal de socios:** la navegación se ajusta a medida que se construya. La propuesta de B7 es el punto de partida, sin visto bueno previo | B7 |

## 3. Desarrollador A: Asesor y Cliente

### Común

**A1 · Destrabar firma y pago** · Bloqueante · S · web y backend

Hoy el recorrido funciona por API, pero no en el navegador.

| Punto | Causa verificada | Corrección |
|---|---|---|
| AS-06 · El contrato no se ve | `/propuestas` y `/polizas` no están en el proxy de Vite ni en `vercel.json`: responde `200 text/html` y `urlDelPdf` mete ese HTML en el iframe. Por configuración, también afecta a Vercel | Añadir las rutas en los dos sitios y rechazar lo que no sea `application/pdf` |
| AS-07 · Error al confirmar | El preflight de `fake-gcs` responde 403 por `x-goog-content-length-range` y `x-goog-if-generation-match`. Trazo es el modo por defecto; por API, la firma queda `FIRMADA` | Añadir `-cors-headers` en el compose. Confirmar que el mensaje visto fue el de subida |
| QA-01 · Página en blanco tras pagar | El BFF devuelve `/pago/retorno/{id}?sesion=…` y la web solo tiene `/pago/retorno`. No se llama `confirmarPago`, así que **el cliente paga y la póliza no se emite** desde esa pantalla | Ruta `/pago/retorno/:propuestaId?` |
| QA-02 · El PDF de la póliza no abre por enlace | La ruta exige JWT y W03-04 no tiene sesión (visto en código) | Aceptar `X-Enlace-Firma` con el mismo control de `contrato.pdf` y actualizar `docs/autenticacion-usuarios.md` |
| QA-04 · Token mal formado da 503 | POLICY responde 422 y el BFF no lo reconoce | Responder 404 |

**Hecho cuando:** en un navegador real, con el stack local, el contrato se ve, se firma en los tres modos, se paga, vuelve
con la póliza emitida y el PDF abre.

### Perfil Asesor

**A2 · Moneda en cotización** · AS-01 · Alta · S · web · Depende de B1

Usar `montoVisible` en `OfertaCompleta`, `OfertaPreliminar` y `RevisarOfertaAceptada`. El selector queda deshabilitado en
`SolicitudYConsentimiento` y en las pantallas de firma y pago.

**Hecho cuando:** USD cambia los valores de la oferta y el contrato sigue en COP.

**A3 · Emisión sin callejones** · AS-02, AS-03, AS-04, AS-08 · Alta · M · web

1. Mostrar «Emitir» en `IDENTIDAD_VERIFICADA`. Hoy, al cerrar el modal, no hay forma de volver.
2. Recuperar la propuesta con `propuestas` al entrar a `/suscripcion/:ofertaId`, para que recargar no pierda el proceso.
3. Abrir el modal en el paso correcto. Hoy, tras recargar, «Reenviar enlace» vuelve al paso 1 y pide otro código.
4. La tarjeta debe poder pintarse con los datos de la propuesta: si la oferta venció, `oferta(id)` devuelve null.
5. Generalizar `useAvisos.ts` para propuestas (POLICY ya publica en `avisos:asesor:<usuario>`), quitar «Actualizar
   estado» y consultar una vez al reabrir, como W05-01.
6. Aplicar D-5: en `FIRMADA`, «Enviar enlace de pago» reemplaza a «Reenviar enlace».
7. Aplicar D-4: avisar el cierre de sesión a las demás pestañas. El token vive en `sessionStorage`, que es por pestaña: no es una medida de seguridad, y el navegador lo
   copia al duplicar la pestaña.

**Hecho cuando:** cerrar el modal o recargar en cualquier paso permite retomar, y el estado cambia sin recargar.

**A4 · Pantalla «Pólizas emitidas»** · AS-05 · Alta · L · web · Depende de A3 y B3

Hoy `G-06` no tiene pantalla (`PantallaPendiente`). El backend ya está listo: `propuestas(estado, pagina, tamano)` respondió
en vivo. **Objetivo:** que el asesor vea lo que ha gestionado. Sale del formato de las tablas de siniestros y de pagos.

| Columna | Dato | Nota |
|---|---|---|
| Cliente | `nombreTitular` y, debajo, `poliza.numero` en gris | Como cliente y radicado en la bandeja. Sin póliza, solo el nombre |
| Prima mensual | `prima` | Convertida (consulta) y alineada a la derecha |
| Cobertura | `cobertura` | Convertida |
| Plazo | `plazoMeses` | |
| Estado | `estadoVisible` con `Badge` | El detalle de `mensajeEstado` va en el `title` |
| Actualizada | `actualizadoEn` | Fecha corta; la completa en el `title` |
| Acciones | Según el estado | `EMITIENDO`: **Retomar**. `EMITIDA`: **Ver PDF**. `NO_EMITIDA`: sin acción |

- **Filtros:** chips Todos, Emitiendo, Emitida y No emitida, en el servidor con el argumento `estado`.
- **Paginación:** `Paginador.vue` con 10 por página y «Mostrando N de M» usando `total`. Un cambio de filtro vuelve a la
  página 1.
- **Estados:** cargando, vacío, vacío por filtro y error con reintento, como la bandeja.
- **En vivo:** un aviso actualiza solo su fila; una propuesta desconocida recarga la página una vez.
- **Encabezado:** `HeaderPantalla`, migas hacia el inicio del rol y header y footer fijos.
- **Sin esta entrega:** BACKOFFICE. W03-35 lo prevé, pero su home no tiene esa entrada y no se pidió.

**Hecho cuando:** el asesor ve solo lo suyo, filtra, pagina, retoma una pendiente y abre el PDF de una emitida.

### Perfil Cliente

**A5 · Mis datos y precarga** · CL-01, CL-02 · Alta · M · web, backend y contrato v0.24

1. Contrato: datos personales propios en `Usuario`, solo por `yo`. AUTH los entrega solo al propio usuario.
2. `MiPerfil`: mostrar documento enmascarado, correo, teléfono y, si existen, nacimiento y país de onboarding. Los clientes
   creados por BACKOFFICE no tienen datos de onboarding: se muestra lo que haya.
3. `SolicitudYConsentimiento`: documento, nombre y correo de solo lectura; enviar `documentoIdentidad` en null para que el
   BFF use el de la cuenta. Monto, plazo y dirección siguen editables porque no están guardados.

**Hecho cuando:** el cliente ve sus datos y cotiza sin digitar lo que ya existe.

**A6 · Del cotizar al firmar** · CL-03 · Alta · L · web · Depende de A1 y A5

El contrato ya lo soporta (W03-32 y W03-33) y k6 lo recorre.

1. En la oferta completa, el botón principal es «Firmar»: `aceptarOferta` → `emitirPropuesta` → `/firma`. «Contactar a un
   asesor» queda como secundario.
2. `useEnlaceFirma` admite dos autorizaciones: token del enlace, o sesión más `propuestaId`, también para el PDF.
3. Atender `EN_REVISION` y `NO_EMITIDA` con su mensaje, sin pasar a firmar.
4. Si la cuenta no tiene documento, explicarlo y ofrecer el asesor.
5. Marcar W03-19 y D-2 del plan del front como reemplazadas.

**Hecho cuando:** un cliente llega de cotizar a póliza emitida sin código ni asesor.

**A7 · «Mis pólizas» y recorridos reales** · S · web · Depende de A4 y A6

1. Reusar la pantalla de A4 para el CLIENTE: sin columna Cliente, y solo sus pólizas.
2. Crear `w03-recorrido.cy.ts` contra el stack real, de la oferta a la póliza, como ASESOR y como CLIENTE. **Esto cierra
   el vacío que dejó pasar estos fallos:** Newman y k6 van directo al BFF y Cypress usa mocks.

## 4. Desarrollador B: Operador y Socio

### Común

**B1 · Base de moneda** · OP-02, TR-01, QA-03 · Alta · M · web · Sin dependencias

Hoy el selector no está conectado a nada: `HeaderPantalla` recibe `COP` por prop, y `convertirMontos`,
`actualizarPreferencias` y `Usuario.moneda` no se usan. La conversión funciona en vivo (`185.000.000 COP` → `45.121,95 USD`).

1. `useMoneda.ts`, como `idioma`: se lee de la cuenta al iniciar sesión, se guarda con `actualizarPreferencias` y, sin
   sesión, en `localStorage`. La cuenta prevalece.
2. `montoVisible(monto)` para plantillas: devuelve el valor convertido, agrupa las peticiones (máximo 50) y guarda las ya
   convertidas.
3. Mientras convierte, o si falla, muestra el monto original con un aviso, nunca un error (regla 5).
4. `HeaderPantalla` usa `useMoneda` directamente y admite `monedaFija`, que deshabilita el selector. Con otra moneda
   muestra: «Valores de referencia. El cobro se hace en la moneda original».
5. `formatearMonto` usa los decimales de cada moneda: `11,83 USD` hoy se vería como `12`. COP y CLP siguen sin decimales.
6. Documentar la regla D-2 en `docs/convenciones.md` del front.

| Se convierte (consulta) | No se convierte (firma, cobro o captura) |
|---|---|
| Ofertas, siniestros, conciliación, pólizas, ventas del socio | Contrato, autorización de cobro, pago, póliza emitida, monto del crédito |

**Hecho cuando:** la moneda elegida sobrevive a navegar, a recargar y a volver a iniciar sesión.

### Perfil Operador

**B2 · Quitar el estado del sistema** · OP-01 · Media · S · web · D-1

Retirar la tarjeta de `HomeOperador`, la ruta `/operador/estado`, `EstadoSistema.vue`, `useEstadoSistema.ts`, sus textos y
sus pruebas. `estadoSistema` sigue en el BFF. El socio conserva `/v1/estado`. Ajustar CA-W07-14 y 15.

**B3 · Paginador en la bandeja** · OP-04 · Media · S · web

Extraer `Paginador.vue` de `ConciliacionDePagos` y usarlo en ambas pantallas, con 10 por página. La bandeja tiene ya 20
casos (PR #472). Un cambio de filtro vuelve a la página 1.

**B4 · Moneda del operador** · OP-03, OP-05 · Alta · S · web · Depende de B1

`montoVisible` en `ConciliacionDePagos`, `DetalleSiniestro` y `ConfirmacionAprobacion`. En W05-01 el total se suma en la
moneda original y luego se convierte, y solo se pide lo visible para no chocar con el límite.

**B5 · Plazo y aprobación automática** · OP-07 · Alta · L · web, backend y contrato v0.23

Hoy el reloj se recalcula cada 60 s y con más de 24 h solo muestra días y horas, así que parece fijo. Nadie actúa al vencer.

1. **Contrato:** `Resolucion.automatica`, para distinguirla de una aprobación manual.
2. **CLAIMS:** `POST /siniestros/_aprobar-vencidos`, idempotente, por Cloud Scheduler como los demás procesos periódicos. El
   Terraform se escribe, pero no se aplica.
3. **Mismo camino que el operador:** reutiliza `aprobar`, con su orden en PAYMENTS, evento y notificación, y actor
   `SISTEMA`. Si PAYMENTS falla, el caso sigue abierto y se reintenta en la siguiente pasada.
4. Índice por `(estado, vence_dictamen_en)` con migración idempotente.
5. **Web:** el contador muestra siempre minutos («1 d 5 h 12 min») y se actualiza cada 30 s; el detalle dice «Aprobado
   automáticamente por vencimiento» y el vencimiento se ve también en la bandeja.
6. **Derivados (D-3b):** el proceso los omite y el detalle cambia el contador por `vencimientoInforme` del perito. Si el
   caso vuelve a estar sin derivación, el plazo se recalcula con el tiempo que quedaba.
7. **Verificaciones (D-3a):** no son condición: un caso `PENDIENTE` vencido se aprueba siempre. Los casos resueltos o
   derivados no cambian.

**Hecho cuando:** un caso `PENDIENTE` vencido queda aprobado una sola vez, con su orden en PAYMENTS y marcado como
automático, y un caso derivado no cambia.

### Perfil Socio

**B6 · Marco fijo y moneda** · SO-01, SO-02 · Alta · M · web, backend y contrato v0.23 · Depende de B1

1. **SO-01:** en el panel el documento mide 2.073 px en una ventana de 660, y se desplaza toda la página. Causa probable:
   la `<table class="sr-only">` de `GraficoLlamadas.vue` se sale de `main`. Dar `relative` a `main`.
2. **SO-02:** `convertirMontos` no admite al socio. Crear `POST /v1/conversiones` en el contrato, el BFF, Envoy y
   `openapi-socios.yaml`, sin descontar presupuesto. Con un token de PRUEBAS usa tasas fijas (W07-10: no llama micros).
3. `montoVisible` en ventas y comisiones, usando esa ruta dentro del portal.

**B7 · Navegación del portal** · SO-03 · Media · M · web

No hay Figma: esta propuesta es el punto de partida y se ajusta con el equipo a medida que se construya (D-7). Una sola
barra fija para todo el portal, con `Pestanas`, reunida en un layout `PortalSocio.vue`:

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

Con la barra sobran las migas y el botón «Documentación de la API». El selector del panel se llama «Ver datos de» y se
dibuja distinto, para no parecer navegación. Se conservan las URL actuales.

**B8 · Token visible en Swagger** · SO-04 · Media · S · web

**No es un hueco de seguridad:** sin token, la API responde 401 (verificado). El portal lo añade sin mostrarlo porque el CSS
oculta «Authorize». Mostrar «Autenticado con la clave de Pruebas · vence 10:45» y un error claro si no se obtiene. Añadir a
Cypress que «Try it out» lleva el token, porque eso aún no se ha probado en el navegador.

### Cierre (A y B)

**B9 · Regresión y documentos** · M

Vitest, Cypress con mocks, Newman y los recorridos reales de A7 en verde, con evidencia en `docs/evidencias/`. Actualizar
`PROJECT_STATUS.md`, `CLAUDE.md` y los planes de W03, W04 y W07. Staging queda fuera: sigue pendiente de la decisión de
liberar, y allí habrá que verificar el CORS real del bucket y las rutas de Vercel.

## 5. Riesgos

- **Aprobación automática:** riesgo aceptado (D-3a). Genera órdenes de indemnización sin revisión humana, aunque una
  verificación no cumpla. La orden no dispersa dinero (W05-3), y `Resolucion.automatica` deja el rastro.
- **Vercel:** AS-06 se dedujo de `vercel.json`, sin probarlo en la nube.
- **Tablero:** F-F3 y F-F4 no tenían peso en Jira; este plan los vuelve prioritarios y aún hay que reflejarlo.
- **Tasas:** son de referencia. Por eso el aviso de B1 es parte del alcance.
- **Datos personales:** D-6 cambia una decisión documentada y exige revisar EC022.
