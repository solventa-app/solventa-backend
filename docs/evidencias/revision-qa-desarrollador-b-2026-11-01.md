# Evidencia · Revisión QA, desarrollador B (B1 a B8) · 2026-11-01

Plan: [`docs/plan-revision-qa-2026-10-31.md`](../plan-revision-qa-2026-10-31.md). **Entorno: stack local con Docker
Compose, no staging.** La web corre con Vite y el navegador es el de Cypress, contra servicios reales.

| Tarea | PR | Verificado |
|---|---|---|
| B1 · Base de moneda | web #119 | La moneda convierte y sobrevive a navegar, a recargar y a volver a iniciar sesión |
| B2 · Sin estado del sistema | web #120 | El home del operador no lo muestra |
| B3 · Paginador | web #121 | Bandeja de 20 casos en 2 páginas; un filtro vuelve a la página 1 |
| B4 · Moneda del operador | web #122 | El detalle del siniestro muestra US$ |
| B5 · Plazo y aprobación automática | backend #477, web #124 | Abajo |
| B6 · Marco fijo y moneda del socio | backend #478, web #125 | El documento ya no desborda la ventana (antes 2.073 px en 660); `POST /v1/conversiones` responde en producción y pruebas |
| B7 · Navegación del portal | web #126 | Barra fija con cuatro secciones y sin migas |
| B8 · Token en Swagger | web #127 | La franja muestra la clave y «Try it out» lleva `Authorization: Bearer` y responde 200 |

## Aprobación automática, en vivo

Con un caso PENDIENTE (`sin-12`) y uno derivado (`sin-13`), ambos con el plazo vencido a mano en MongoDB:

| Paso | Resultado |
|---|---|
| Primera pasada de `POST /siniestros/_aprobar-vencidos` | `{"aprobados": 1, "pendientes": 0}` |
| Segunda pasada | `{"aprobados": 0, "pendientes": 0}`: no aprueba dos veces |
| `sin-12` | `APROBADO`, `automatica = true`, indemnización de 12.722.000 COP |
| PAYMENTS | Orden de `sin-12` en `PENDIENTE_DISPERSION`, sin dispersión |
| `sin-13`, derivado | Sigue `EN_REVISION`: espera al perito |

## Pruebas

| Prueba | Resultado |
|---|---|
| `cypress/integracion/operador-socio.cy.ts`, navegador contra el stack real | 6 de 6 |
| Vitest web | 426 |
| pytest: claims 56 y BFF 486 | En verde |
| Newman `hu-w04` | 52 de 52 |
| Newman `socios-w07` | 52 de 52 en la segunda corrida. La primera, con el stack recién levantado, dio 23 fallos en cascada desde la cotización; no se repitió |
| `scripts/verificar-migraciones.sh claims` | 3 migraciones |

## Sin probar

- **Staging:** nada. Cloud Scheduler `aprobar-siniestros-vencidos` está escrito y sin aplicar.
- **En local no hay disparador del plazo:** la bandeja muestra «Vencido» hasta llamar a mano
  `POST http://localhost:8012/siniestros/_aprobar-vencidos`.
- **Aviso de conversión fallida:** cubierto en Vitest, no provocado en vivo.
- **Cypress con mocks:** los specs de W04 y W07 se ajustaron y no se corrieron en local.
