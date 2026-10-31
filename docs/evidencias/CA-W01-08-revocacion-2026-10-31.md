# Evidencia: revocación y retención del perfil (CA-W01-08 ampliado), 2026-10-31

**Solo pruebas automáticas, sin compose ni staging.** La prueba en vivo con MongoDB y Redis reales, el recorrido por la
web y el plazo de ≤ 5 min quedan **pendientes** para la fase de validación.

| Comprobación | Resultado |
|---|---|
| RISK: 82 pruebas y `ruff check` | Pasan |
| BFF: 476 pruebas | Pasan |
| Revocar borra perfil y caché; dos veces da 204 | `test_retencion_perfiles.py` |
| Revocar un consentimiento antiguo no alcanza al de otro vigente | Solo se comprueba el filtro enviado a Mongo |
| Purga y batch: vigente, vencido, viejo reciente, viejo vencido | `test_batch_reproceso.py` (evaluador de filtro en memoria) |
| RISK caído no hace fallar la revocación | `test_revocacion.py` |
| Sin titular en los logs | Pruebas de RISK y BFF |
