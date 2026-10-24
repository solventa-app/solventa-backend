# Evidencia CA-W03-01 — Oferta aceptada: versión de reglas y oferta preliminar (HU-W03)

- **Fecha:** 2026-10-24
- **Historia:** HU-W03 (KAN-26), plan de HU-W03 bloque 7, filas 2 y 3
- **Criterio:** CA-W03-01 — al aceptar una oferta `COMPLETA` se guarda la oferta aceptada (**prima, cobertura, versión de reglas y consentimiento**) y se
  muestra su resumen. Una `PRELIMINAR` no se puede aceptar: **se ofrece recalcularla** (D-11).

> **Corrida LOCAL (docker compose en un portátil), no es una medición de staging.**

## 1. Lo que faltaba

1. La **versión de reglas** no se guardaba: RATING la calculaba (`VERSION_REGLAS`) pero no la incluía en la oferta que guarda y sirve, y la propuesta de POLICY
   solo guardaba el consentimiento y la oferta cifrada.
2. Una oferta `PRELIMINAR` respondía «Solo puedes aceptar una oferta completa.» con `puedeReintentar: false`: no ofrecía recalcular.

## 2. El cambio

- **RATING:** la oferta lleva `versionReglas` (también cuando el score viene de la caché).
- **POLICY:** `propuestas.version_reglas`, migración `0005` (solo expandir, columna nula). Va en una columna y no en `oferta_cifrada` porque no es un dato personal y sirve
  para saber con qué reglas se vendió cada póliza. Una oferta guardada antes del cambio no la trae: la propuesta se acepta igual y queda en nulo.
- **BFF:** `OFERTA_PRELIMINAR` devuelve «Esta oferta es preliminar y no se puede aceptar. Vuelve a calcularla.» y `puedeReintentar: true`. Reintentar es volver a cotizar, no repetir
  la aceptación. El motivo de cada error lleva ahora su texto y si es reintentable en una sola tabla. Diccionario `EN` actualizado.
- **Contrato v0.13:** sin cambio de campos ni de tipos; se documenta la semántica en `schema.graphql` y en `contracts/CHANGELOG.md`.

## 3. Resultado

| Comprobación | Resultado |
|---|---|
| Pruebas nuevas | RATING 2, POLICY 2, BFF 1 (más una que se actualizó) |
| Suites completas, en la imagen de cada servicio | POLICY **147**, BFF **233**, RATING **87** pasan |
| `ruff check` | Pasa. `ruff format` marca 2 archivos de `test_fuentes_copia.py` que no se tocaron |
| Migración `0005` en la base real | Una sola cabeza; al subir existe `version_reglas` (nula), al bajar a `0004` desaparece, al subir vuelve |
| Propuestas creadas por el flujo real (Newman, por el gateway) | **6 de 6 con `version_reglas = v1`**; las 831 anteriores quedan en nulo |
| Newman, oferta `PRELIMINAR` por el gateway | `ErrorAceptacion`, motivo `OFERTA_PRELIMINAR`, `puedeReintentar: true`, mensaje «…Vuelve a calcularla.» |
| Newman, colección completa | 94 peticiones, 171 aserciones, 0 fallos, 0 respuestas 5xx |

## 4. Límites

- **`OFERTA_VENCIDA` no cambió.** Su mensaje también dice «Vuelve a cotizar», pero sigue con `puedeReintentar: false`: el criterio solo pide el caso de la preliminar. Es una inconsistencia pequeña por decidir.
- **El frontend aún no usa este error:** la copia local del frontend es anterior a W03, así que nadie lo consume todavía. Cuando se construya la pantalla W03-01, un botón «reintentar» genérico que repita `aceptarOferta` con la misma oferta fallaría igual; el contrato (v0.13) dice que reintentar es volver a cotizar.
- **La versión de reglas no se muestra en ninguna API:** se guarda para auditoría. Quien la quiera ver hoy la consulta en la base.
- Solo hay `v1`: el valor cambia cuando se suba `VERSION_REGLAS`, y eso no se ha ejercitado más que con una prueba unitaria.
