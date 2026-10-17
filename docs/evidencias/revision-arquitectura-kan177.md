# KAN-177 — Revisión de cumplimiento de las reglas de arquitectura (Sprint 1)

Auditoría de lo entregado en HU-W01 (KAN-24) y HU-W05 (KAN-28) contra las 9 reglas de arquitectura de
`CLAUDE.md` y los escenarios de `docs/sprint-1.md`. Hecha por lectura de código + verificación en vivo
contra el stack (`docker compose --profile servicios up --build --wait`), no solo por inspección.

## Resultado por regla

| # | Regla | Estado | Evidencia |
|---|---|---|---|
| 1 | Escritor único por almacén | ✅ Cumple | `grep` confirma 0 escrituras (`insert_one`/`update_one`) en `rating/app/` — solo lee, con `read_preference=Secondary()` y sesión causal (`afterClusterTime`, D-02) en `lector_perfiles.py`. `risk/app/application/repositorio_perfiles.py` es el único escritor de `perfiles`; su propio docstring lo declara ("regla de arquitectura #1"). `auth`/`payments` escriben solo su propia base (migraciones Alembic por servicio). |
| 2 | ACL Worker único contacto con proveedores externos | ✅ Cumple | `grep -rl "OPEN_FINANCE_URL\|OPEN_DATA_URL"` solo da resultados en `acl-worker` y los stubs. Verificado también en vivo para `pasarela` (ver regla 4). |
| 3 | Hexagonal solo en `acl-worker` | ✅ Cumple | Única carpeta `app/domain` del repo. `bff`/`auth` son planos (ni siquiera tienen `app/application`); `risk`/`rating`/`payments` tienen `app/application` pero sin puertos ni dominio — exactamente lo que pide la regla. |
| 4 | Nunca 5xx crudo por falla de proveedor | ✅ Cumple | Verificado en vivo (KAN-198): 4 cobros con la pasarela en modo `error` — **siempre HTTP 200**, nunca 5xx; degradan a `pendiente`. `servicio_fuentes.py` nunca deja escapar una excepción cruda (degrada a caché o a valor mínimo). |
| 5 | Sonda de recuperación fuera del camino síncrono | ✅ Cumple | `SondaRecuperacion` es genérica (`dict[str, Sondeo]`), corre en background desde el lifespan de FastAPI, cubre los 3 circuitos (`open-finance`, `open-data`, `pasarela`). |
| 6 | Sin datos personales en logs/métricas; cifrado en reposo/tránsito; cero datos de tarjeta | ⚠️ Parcial | **Sin datos de tarjeta**: 0 ocurrencias de `numero_tarjeta`/`cvv`/`pan` en todo el repo; solo se usa `token_medio_pago` (tokenizado por el proveedor PCI-DSS). **Sin datos personales en logs**: 45 usos de `id_opaco()` en los puntos de logging; 0 líneas de `logger.*` que referencien `clave`/`documento`/`alias` en claro. **Cifrado de campo (SEG-002/D-04) NO está construido** — hueco real, pero ya documentado honestamente en el propio código (`risk/app/application/repositorio_perfiles.py`: "no hay Cloud KMS disponible... no existe todavía ningún helper de cifrado... NO simulado con un cifrado de juguete"). No es un hallazgo nuevo, es consistente con que D-04 sigue como decisión pendiente de confirmar en el Planning. |
| 7 | Sin consentimiento vigente no se consulta ninguna fuente | ✅ Cumple | `bff/app/servicio_oferta.py`: primero llama a `auth.obtener_consentimiento`, valida `vigente`, y solo entonces consulta las 5 fuentes vía ACL. Verificado en vivo en una sesión anterior: revocar y volver a pedir oferta da `ErrorOferta` sin consultar nada. |
| 8 | Contratos primero | ✅ Cumple | `contracts/CHANGELOG.md` v0.1 documenta KAN-24 (usuarios/sesión) como "Solo cambios aditivos", con aviso explícito de impacto a `solventa-frontend`. |
| 9 | Umbrales son piso, no producción | — | No aplica verificar en código; es una regla de proceso (medir en staging con proveedores simulados). Sin staging desplegado todavía, no hay nada que contradiga esto. |

## Escenarios de calidad verificados en vivo en esta revisión

- **EC009/EC010** (timeout duro + degradación, 0% 5xx): confirmado para `pasarela` (KAN-198) y ya estaba confirmado para Open Finance/Open Data (KAN-181).
- **Idempotencia** (mismo cobro dos veces → 0 duplicados): confirmado — mismo `clave_idempotencia` enviado 2 veces por `POST /cobros` devuelve el mismo `id`/`referencia`; balance no se duplicó.
- **EC023** (revocación de consentimiento ≤ 5 min): ya verificado en una sesión anterior — el efecto es inmediato (bloquea en la siguiente consulta, no hace falta esperar).

No se corrieron en esta tarea (fuera de alcance de KAN-177, pertenecen a `quality-engineer`): EC003/EC004 (latencia con k6), EC011/EC012 (staleness de réplica con carga), EC022 (cifrado en tránsito con TLS real).

## Gaps conocidos (ya documentados antes de esta revisión, no son hallazgos nuevos)

1. **Cifrado de campo (SEG-002/D-04)** no construido — bloqueado por falta de proyecto GCP/KMS. Documentado en el propio código de `risk`.
2. **Autenticación servicio-a-servicio incompleta**: `docs/arquitectura-backend.md` ya señala que `PAYMENTS → ACL Worker` y `consolidador-fuentes → ACL Worker` todavía no envían el token de identidad que sí envía el BFF (`autenticacion_servicios.py`); en staging esas llamadas recibirían 403. Se corrige antes del primer despliegue.

## Conclusión

Las 9 reglas de arquitectura se cumplen o están correctamente fuera de alcance para este sprint, con una
única brecha real (cifrado de campo) que ya estaba identificada, documentada en el código y explicada por
una dependencia externa (proyecto GCP) que no existe todavía — no requiere acción nueva, solo seguir
rastreándola hasta que D-04 se confirme y haya un proyecto GCP real.
