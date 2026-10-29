# Evidencia: verificación de identidad con Didit (HU-M01 / KAN-31), 2026-10-27

**Corrida local, no de staging.** Postgres + fake-gcs + `almacenamiento` + `stub-kyc` (rutas con forma de Didit v3) + `acl-worker`.
Las imágenes se subieron con URLs firmadas de `almacenamiento` y el ACL las descargó (reescribiendo `localhost:4443` → `fake-gcs`).

| Caso | Estado | Motivo | Alertas | ms |
|---|---|---|---|---|
| Aprobado | aprobada | — | — | 280 |
| Mismas imágenes (caché) | aprobada | — | — | 12 |
| Suplantación | rechazada | prueba_de_vida_fallida | LIVENESS_FACE_ATTACK | 173 |
| Rostro distinto | rechazada | rostro_no_coincide | LOW_FACE_MATCH_SIMILARITY | 262 |
| Documento vencido | rechazada | documento_rechazado | DOCUMENT_EXPIRED | 307 |
| Origen ajeno (anti-SSRF) | pendiente | imagen_no_disponible | — | 3 |
| Proveedor en modo `error` ×3 | pendiente | proveedor_no_disponible | — | 14–17 |

- Tras tres fallas el circuito `identidad` quedó `opened`; la sonda lo cerró al volver el stub a `sano`.
- La segunda llamada con las mismas imágenes no llegó al proveedor (servida desde Redis).
- El valor en Redis (`acl:identidad:<huella>`) no contiene ni el número ni el nombre (cifrado AES-GCM); TTL ≈ 30 días.
- Los logs solo traen la referencia opaca y códigos.

**Sin probar:** Didit real (sandbox ni producción). Pendiente cargar `didit-api-key` y fijar `IDENTIDAD_URL`.
