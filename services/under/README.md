# under

UNDER: decisión de suscripción (HU-W03, KAN-213). Servicio nuevo del Sprint 2.

## Responsabilidad

Decide si una propuesta firmada se aprueba, se rechaza o pasa a revisión manual. **Sin almacén**: decide y responde; quien lo
llama (POLICY, en el bloque 4 del plan [`docs/plan-completar-hu-w03.md`](../../docs/plan-completar-hu-w03.md)) guarda el
resultado en la propuesta. Habla con el proveedor de KYC **solo a través del ACL Worker** (regla de arquitectura #2).

- `POST /decisiones` `{propuesta_id, cliente_id, nivel_riesgo, cobertura{valor, moneda}}` →
  `{decision, motivos[], kyc{estado, referencia}}`. Siempre 200: nunca un 5xx por el proveedor (regla #4).
  `cliente_id` es el identificador opaco (HMAC) del BFF, nunca un documento. `nivel_riesgo` es el `perfilRiesgo` de la oferta
  (`BAJO`, `MEDIO`, `ALTO`).

## Reglas (`app/application/reglas.py`)

Reglas simples y explícitas, no un modelo: lo que se necesita es una decisión automática, rápida y explicable.

| Condición | Decisión | Motivo |
|---|---|---|
| El KYC rechaza la identidad | `RECHAZADA` | `KYC_RECHAZADO` |
| El KYC no responde (proveedor caído, circuito abierto, ACL inalcanzable) | `REVISION_MANUAL` | `KYC_NO_DISPONIBLE` |
| `nivel_riesgo` = `ALTO` | `REVISION_MANUAL` | `RIESGO_ALTO` |
| `nivel_riesgo` ausente o desconocido | `REVISION_MANUAL` | `RIESGO_DESCONOCIDO` |
| Cobertura por encima del tope (`COBERTURA_MAXIMA_AUTOMATICA`, 500.000.000 COP por defecto) | `REVISION_MANUAL` | `COBERTURA_ALTA` |
| Moneda distinta de COP | `REVISION_MANUAL` | `MONEDA_NO_SOPORTADA` |
| Ninguna de las anteriores | `APROBADA` | — |

Solo el KYC rechaza. Una falla de un proveedor o un riesgo que no permite aprobar sola **nunca rechaza**: va a revisión
manual. Los motivos de revisión se acumulan. Los motivos son códigos, sin datos personales ni montos.

**Los umbrales (tope de cobertura, qué niveles aprueban) son supuestos de este sprint**, no reglas de negocio validadas:
confirmarlos con el equipo de suscripción.

## Diseño

- Idempotente de hecho: el KYC es idempotente por `id_verificacion` (= el id de la propuesta), así que decidir dos veces la
  misma propuesta da la misma decisión.
- Sin hexagonal (regla #3): capas simples. Sin base de datos, sin secretos.
- Sin datos personales en logs (regla #6): solo `id_opaco(...)` de la propuesta y del cliente, y los códigos de motivo.

## Configuración

| Variable | Default | Para qué |
|---|---|---|
| `ACL_URL` | `http://localhost:8005` | ACL Worker (el KYC pasa por él, nunca directo al proveedor) |
| `UNDER_ACL_TIMEOUT_SEGUNDOS` | `3.0` | Espera máxima al ACL (el ACL corta al proveedor a 700 ms) |
| `COBERTURA_MAXIMA_AUTOMATICA` | `500000000` | Tope (COP) para aprobar sola |
| `AUTENTICACION_SERVICIOS` | `ninguna` | `id_token` en Cloud Run (el ACL es privado) |

## Verificado en vivo

Con el ACL Worker real y `stubs/kyc` (puerto 8105): aprobada ~100 ms; KYC lento, el ACL corta a ~740 ms y la propuesta va a
revisión; con el KYC en error el circuito abre al 3.er intento y las siguientes decisiones tardan ~17 ms; la sonda lo cierra
sola; con el stub o el ACL caídos responde 200 con `REVISION_MANUAL`; los logs no llevan cliente, cobertura ni propuesta.

Código copiado de POLICY (sin código compartido entre servicios): `observabilidad.py`, `autenticacion_servicios.py`,
`identificadores.py`.

```bash
cd services/under && python -m pytest
```
