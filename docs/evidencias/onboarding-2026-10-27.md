# Evidencia: servicio `onboarding` (HU-M01 / KAN-31), 2026-10-27

**Corrida local, no de staging.** Postgres + fake-gcs + `almacenamiento` + `acl-worker` + `stub-kyc` (forma de Didit) +
`onboarding`, con `docker compose`. Cada caso es un recorrido completo por HTTP: iniciar, consentimiento, cuatro
subidas con URL firmada (PUT directo al emulador), confirmar, enviar y esperar el resultado. La verificación corre en
el hilo local (`DISPARADOR=bucle`).

| Caso | Estado | Motivo | Intento | Restantes | ms (enviar → resultado) |
|---|---|---|---|---|---|
| Aprobado | VERIFICADA | — | 1 | 2 | 442 |
| Mismo documento que el anterior | RECHAZADA | DOCUMENTO_YA_REGISTRADO | 1 | 0 | 237 |
| Menor de edad | RECHAZADA | MENOR_DE_EDAD | 1 | 2 | 438 |
| Suplantación | RECHAZADA | SUPLANTACION | 1 | 2 | 437 |
| Reintento de la anterior con capturas nuevas | VERIFICADA | — | 2 | 1 | 438 |
| Rostro distinto | RECHAZADA | ROSTRO_NO_COINCIDE | 1 | 2 | 231 |
| Proveedor en modo `error` (×4) | EN_REVISION | PROVEEDOR_NO_DISPONIBLE | 1 | 2 | 223–226 |

- `GET /onboarding/{id}` con el proveedor caído: 50 lecturas, media de 3 ms (EC010, ≤ 500 ms).
- La cuenta ligada responde en `GET /usuarios/{id}/onboarding` (`miVerificacion`): VERIFICADA.
- Sin token: `401 TOKEN_INVALIDO`. Subida sin consentimiento: `409 SIN_CONSENTIMIENTO`.
- `datos_extraidos` solo tiene texto cifrado (AES-GCM); solo existen filas de las 2 sesiones VERIFICADAS.
- `DELETE` sobre `eventos_onboarding` falla por el trigger append-only.
- Las 40 piezas quedaron con `retener_desde` en `almacenamiento` (corren los 30 días).
- Los logs de `onboarding` y `acl-worker` no contienen números de documento ni nombres (búsqueda: 0 coincidencias).
- Migración `0001`: `upgrade` → `downgrade base` → `upgrade` sin errores, una sola cabeza.

**Sin probar:** Cloud Tasks real (`DISPARADOR=externo`), Pub/Sub de `onboarding.*`, Didit real y el aviso a AUTH (PR
siguiente).
