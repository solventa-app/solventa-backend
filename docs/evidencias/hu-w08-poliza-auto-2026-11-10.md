# HU-W08 · Póliza de auto de punta a punta (2026-11-10)

KAN-290, [`docs/plan-poliza-auto.md`](../plan-poliza-auto.md). Stack local de `docker compose` con todos los PR del auto
(contrato v0.42, `stub-vehiculos`, ACL, RISK `0005`, RATING, POLICY `0009` y UNDER, BFF y CLAIMS), la web
(`solventa-frontend-web#170`) y la app (`solventa-frontend-mobile#73`). **No es staging.**

## Newman (`tests/newman/hu-w08.postman_collection.json`)

Dos corridas seguidas sin fallos: **33 peticiones, 76 aserciones, 0 fallas**.

| Caso | Resultado |
|---|---|
| `ABC123` Mazda 3 2024 | COMPLETA, prima $ 332.198/mes, valor comercial $ 118.500.000, 6 coberturas, 7 factores, placa `ABC•••` |
| `GHI789` Prado (hurto muy alto) | COMPLETA, factor de hurto 1,40, más cara que `ABC123` |
| `YZA456` (Fasecolda caída) | PRELIMINAR con rango, Fasecolda en `FALLO` |
| `NOX000`, `STU901`, `PQR678` | `VEHICULO_NO_ENCONTRADO`, `VEHICULO_NO_ASEGURABLE` (hurto) y `VEHICULO_NO_ASEGURABLE` (más de 15 años) |
| Aceptar y emitir `ABC123` / `JKL012` | `EN_FIRMA` / `EN_REVISION` (siniestralidad alta) |
| Socio de producción | `GHI789` oferta AUTO; `PQR678` 422 sin gastar presupuesto |
| Choque parcial sobre la póliza de auto | Sin tipo: rechazado. Aprobar sin valor: lo pide; 900.000: no supera el deducible; 8.000.000: **$ 6.576.500** (8.000.000 − 1 SMMLV), cobertura «Pérdida parcial por daños» |

## Web (Cypress contra el stack real)

`e2e/cypress/integracion/w08-auto.cy.ts` (en `solventa-frontend-web`), **2/2 en verde**: el CLIENTE demo cotiza `abc 123`, ve la oferta
con el vehículo y las 6 coberturas y «Firmar» lo lleva a la firma en su sesión; el operador aprueba un choque de auto con
el valor del daño y ve la indemnización de $ 6.576.500.

## App móvil

`tool/verificar.sh` en el devcontainer: análisis estricto sin problemas y 794 pruebas, con el reporte de auto (M05-01
pide el tipo). La prueba de integración «HU-W08: reporta un choque sobre la póliza de auto con su tipo»
(`integration_test/reporte_siniestro_test.dart`, con el fake del BFF) corre en los emuladores del CI; sin dispositivo
la captura de la foto no corre (igual que las demás de M05), así que en local se verificó hasta M05-01.

## Hallazgos

- En local hay que reiniciar el `gateway` del compose tras actualizar: Envoy lee sus rutas al arrancar y, sin eso,
  `/v1/cotizaciones-auto` responde 404.
- `scripts/verificar-migraciones.sh policy` falla sobre una base local con datos (la `0007` no baja con propuestas
  `EN_FIRMA`); la `0009` se verificó aparte y sobre una base vacía (ver el PR de POLICY).
