# Evidencia: revocación y retención del perfil (CA-W01-08 ampliado), 2026-10-31

**Actualizada: verificada en vivo con el compose completo (sin staging).**

| Comprobación | Resultado |
|---|---|
| RISK: 82 pruebas y `ruff check` | Pasan |
| BFF: 476 pruebas | Pasan |
| Revocar borra perfil y caché; dos veces da 204 | `test_retencion_perfiles.py` |
| Revocar un consentimiento antiguo no alcanza al de otro vigente | Solo se comprueba el filtro enviado a Mongo |
| Purga y batch: vigente, vencido, viejo reciente, viejo vencido | `test_batch_reproceso.py` (evaluador de filtro en memoria) |
| RISK caído no hace fallar la revocación | `test_revocacion.py` |
| Sin titular en los logs | Pruebas de RISK y BFF |

## Corrida en vivo (compose completo, por el gateway)

| Comprobación | Resultado |
|---|---|
| Migración `0004` en MongoDB real | Aplicada, con sus 3 índices |
| El perfil guarda `consentimiento_id` y su vencimiento | Sí |
| Revocar | 79 ms de punta a punta; perfil borrado de Mongo y `score:` y `oferta:` del titular borrados de Redis |
| Cotizar tras revocar | `CONSENTIMIENTO_NO_VIGENTE`, pide consentimiento otra vez |
| Revocar un consentimiento antiguo | Queda solo el perfil del vigente |
| `POST /perfiles/_purgar` | Borra el perfil vencido |
| W01-01 en navegador (Cypress contra el backend real) | El ASESOR debe dar nombre y correo válidos y llega a la oferta; el CLIENTE no ve esos campos |
| Correo del titular | Cifrado en POLICY (`correo_cifrado`); recibe código, enlace y PDF |

**Bug encontrado en esta corrida:** la imagen de RISK no traía `redis`, y un `except ImportError` lo ocultaba: RISK nunca escribió ni borró caché. Corregido en `fix/KAN-24-redis-en-risk`. Bloque 2 no funcionaba en imagen real hasta este arreglo.
