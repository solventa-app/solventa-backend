# Alcance faltante frente al enunciado

> **Corte: 2026-11-10.** Cruce del enunciado oficial (`MISW4501-202614-Proyecto.pdf`, secciones 5.1 y 5.2) con el código
> de los tres repos (`solventa-backend`, `solventa-frontend` y `solventa-frontend-mobile`).
> **Decisión de Frans (2026-11-10):** se sigue primero con la póliza de auto ([`plan-poliza-auto.md`](plan-poliza-auto.md),
> KAN-290) y lo de abajo queda anotado para después.

## Recorridos críticos (5.1)

| # | Recorrido | Estado | Historia |
|---|---|---|---|
| 1 | Cotización embebida | Hecho | HU-W07 (KAN-30) |
| 2 | Suscripción y emisión | Hecho | HU-W03 (KAN-26) |
| 3 | Siniestro asistido | Hecho; la póliza de auto le da una póliza coherente | W04, M05, M06 y KAN-290 |
| 4 | **Siniestro paramétrico automático:** un evento externo (retraso de vuelo, evento climático) dispara el pago sin intervención humana | **Falta el backend.** El móvil tiene las pantallas M04 con un datasource simulado (`FakeCoberturaRemoteDataSource`) | HU-M04 (KAN-38), Sprint 3, 13 pts |
| 5 | Perfilamiento y oferta de vida hipotecario | Hecho | HU-W01 (KAN-24) |
| 6 | **Gestión del ciclo de vida:** renovaciones, modificaciones, cancelaciones, reportes al regulador y a reaseguradoras | **Nada.** «Renovar» en el código es la sesión, no la póliza | HU-W06 (KAN-29, sin sprint) cubre solo el reporte ACORD de reaseguro. Renovar, modificar, cancelar y el reporte al regulador **no tienen historia** |

## Funcionalidades propias del móvil (5.2)

| Funcionalidad | Estado | Historia |
|---|---|---|
| Reporte de siniestro con cámara | Parcial: solo fotos. Faltan video, foto del recibo y la geoetiqueta del EXIF | HU-M05 (KAN-35) |
| Onboarding y autenticación biométrica | Hecho | HU-M01, HU-M02 |
| **Notificaciones push:** pago paramétrico, vencimiento y renovación, estado del siniestro | **Nada:** sin `firebase_messaging` y sin envío en el backend | Sin historia |
| Geolocalización y asistencia | Hecho | HU-M06 (KAN-36) |
| **Billetera de pólizas y modo offline:** coberturas y credenciales sin conexión, con sincronización posterior | **Nada** | HU-M03 (KAN-33, sin sprint, 13 pts) |
| **Escaneo de documentos:** digitalizar soportes desde la cámara | **Nada** | Sin historia |

## Web (5.2)

La «administración de pólizas» se queda en listar y ver el PDF (G-06): no se puede modificar, renovar ni cancelar. Depende del
recorrido 6.

## Orden recomendado (cuando se retome)

1. **M04 en el backend** (KAN-38): ya está comprometido en el Sprint 3 y es un recorrido crítico completo.
2. **Ciclo de vida, mínimo:** cancelar y renovar la póliza, y un reporte al regulador y uno ACORD a reaseguradoras que se
   descarguen. Es el único recorrido crítico sin nada.
3. **Push:** lo necesitan el paramétrico, la renovación y el estado del siniestro.
4. **Billetera offline** (M03).
5. **Escaneo de documentos:** reutiliza la cámara de M05 y el almacenamiento.

Tamaño estimado de lo pendiente: ~13 pts (M04) + 13 a 20 (ciclo de vida) + ~6 (push) + 13 (M03) + 3 a 6 (escaneo). Con
10 pts por semana (hoja de trabajo de arquitectura) no cabe en lo que queda del Sprint 3 (cierra el 2026-11-23) junto
con la póliza de auto.
