# Prueba exploratoria FC-01 e indicadores de calidad (HU-W01 y HU-W05)

> **Estado: PLANTILLA, sesión sin realizar.** La sesión la hace una persona contra staging (decisión 11 de
> `docs/plan-completar-hu-w01.md`); este archivo se llena **después**, con lo que esa persona observe. Nada de lo que
> aparece abajo como resultado es un hallazgo: son los casos sugeridos, las pistas de dónde mirar y los campos a completar.

- **Factor de calidad:** FC-01 (adecuación funcional, resiliencia ante entradas borde y experiencia frente a degradación de proveedores).
- **Historias cubiertas:** HU-W01 (cotización y perfilamiento, pantallas W01-01 a W01-03) y HU-W05 (conciliación y reintento de cobros del back-office, W05-01).
- **Entorno:** staging desplegado a través del API Gateway (700 ms) y el front conectado.

## 1. Carta de la sesión (Session-Based Test Management)

| Campo | A completar |
|---|---|
| **Fecha** | |
| **Misión** | Explorar el flujo de cotización (HU-W01, como CLIENTE y como ASESOR) y la conciliación de pagos (HU-W05, como BACKOFFICE) buscando fallos al combinar degradación de proveedores, entradas borde y vencimiento/revocación del consentimiento |
| **Duración** | 60 minutos: 45 de ejecución activa y 15 de registro y aislamiento de hallazgos |
| **Responsable** | Una persona que **no** haya implementado lo que explora (revisión cruzada) |
| **Datos de prueba** | Ficticios (regla 6). Anota los usuarios, documentos y consentimientos que uses |
| **Versión probada** | SHA desplegado (lo muestra el informe del despliegue) |

## 2. Casos sugeridos y qué mirar

La columna «Observado» y «Estado» se llenan durante la sesión; las pistas salen de lo que el equipo ya conoce del diseño.

| # | Escenario | Qué mirar | Observado | Estado |
|---|---|---|---|---|
| 1 | Cotización con las 5 fuentes en línea | Oferta `COMPLETA`, prima puntual, dinero como texto decimal, fuentes en `OK` | | |
| 2 | Una fuente no obligatoria caída (p. ej. Open Data `caido`) | Oferta `PRELIMINAR` con rango de prima y fuentes en `CACHE`, nunca un 5xx | | |
| 3 | Falla la única fuente obligatoria (`historial-crediticio`) y no hay caché | `ErrorOferta` con reintento (CA-W01-05), mensaje claro; ojo: sin caché el corte de 700 ms del gateway puede devolver 504 antes de que el BFF conteste | | |
| 4 | Reutilizar el `consentimientoId` con otro documento | `ErrorOferta` con motivo `CONSENTIMIENTO_NO_VIGENTE`; el front vuelve a pedir el checkbox | | |
| 5 | Consentimiento revocado y consentimiento vencido (30 días) | La oferta se rechaza de inmediato; no se consulta ninguna fuente | | |
| 6 | Valores límite: plazo 0, 1, 360 y 361; valor del crédito 0 y muy grande; dirección vacía o sin ciudad | Se rechazan en el BFF sin llamar a servicios; mensajes del formulario | | |
| 7 | Cobros en W05-01: un `RECHAZADO` se reintenta hasta 3 veces; un `PENDIENTE` lo reintenta el sistema | Contador de reintentos, referencia `…-rN` distinta por intento, error al agotarlos | | |
| 8 | Roles: CLIENTE y ASESOR no entran a pagos; token vencido o alterado | «No tienes permiso» / «Tu sesión no es válida»; el front vuelve al login | | |
| 9 | Doble clic en «Reintentar» o en «Cobrar» | Un solo cobro (idempotencia por `idIntentoCobro`) | | |
| 10 | Arranque en frío tras un rato sin uso | Un 504 del gateway es esperable en la primera petición; el front debe ofrecer reintentar | | |

## 3. Hallazgos

| ID | Severidad (Blocker / Critical / Major / Minor) | Descripción | Pasos | Estado (abierto / corregido) |
|---|---|---|---|---|
| | | | | |

## 4. Indicadores de calidad del Sprint 1

Los que se pueden calcular hoy de forma verificable están en la columna «Dato verificable»; el resto se llena tras la sesión.

| Indicador | Dato verificable hoy | A completar tras la sesión |
|---|---|---|
| Defectos abiertos por severidad | — | De la tabla de hallazgos |
| Pruebas automatizadas del backend | Ver el CI del PR: pytest de cada servicio, validadores de contratos, de servicios y del gateway, humo E2E y Newman | |
| Cobertura de criterios de aceptación (CA-W01-01 a 11 y HU-W05) | — | Matriz criterio → prueba automática o caso exploratorio |
| Escenarios de calidad con evidencia | Los que tienen informe en `docs/evidencias/` (cada uno dice si la medición fue local o en staging) | |
| Cobertura de código | **No medida** | |
