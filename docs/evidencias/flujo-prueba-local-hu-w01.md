# Flujo de prueba local — HU-W01 (backend, vía docker compose)

Evidencia del caso insignia de punta a punta, **solo backend**: GraphQL del BFF contra el stack real
en `docker compose` (sin frontend). Todas las respuestas de este documento son capturas reales de una
corrida contra el stack local el 2026-10-11 — nada está inventado para el documento.

## Cómo reproducirlo

```bash
# Si 5432/6379 ya están ocupados en tu máquina (p. ej. por otro proyecto):
POSTGRES_PORT=5433 REDIS_PORT=6380 docker compose up -d
POSTGRES_PORT=5433 REDIS_PORT=6380 docker compose --profile servicios up -d --build --wait

curl localhost:8000/health   # {"servicio":"bff","estado":"ok"}

# Smoke oficial (el mismo que corre en CI): registra consentimiento, pide oferta sana,
# degrada Open Finance a "lento", revoca el consentimiento y confirma el bloqueo.
python scripts/humo_bff.py
```

## 1. Smoke oficial (`scripts/humo_bff.py`)

Es el mismo script que corre el CI contra el stack real (`ci.yml`, "humo del stack"). Salida real:

```
1. registrarConsentimiento
2. solicitarOferta con proveedores sanos
   -> Oferta COMPLETA (131 ms)
3. solicitarOferta con Open Finance lento
   -> Oferta PRELIMINAR (737 ms)
   fuentes no frescas: [('historial-crediticio', 'CACHE'), ('cuentas-bancarias', 'CACHE')]
4. revocarConsentimiento y volver a solicitar
   -> ErrorOferta No hay un consentimiento vigente para consultar tus datos. (6 ms)
OK: humo del BFF en verde
```

Los 737 ms del paso 3 son el timeout duro del ACL Worker (700 ms, EC009/EC010) en acción: el
proveedor lento no respondió a tiempo, el ACL Worker cortó la espera y cayó a caché — nunca esperó
los 750-1100 ms reales del stub.

## 2. Recorrido manual ampliado (GraphQL directo contra el BFF)

Cubre lo que el script oficial no ejerce: `contactarAsesor`, una oferta degradada con caché ya tibia
(sin esperar el timeout), y qué pasa cuando el circuito ya estaba abierto.

### 2.1 Registrar consentimiento

```graphql
mutation { registrarConsentimiento(entrada: {finalidad: "oferta-vida-hipotecario", versionTexto: "v1"})
  { id finalidad versionTexto vigente otorgadoEn revocadoEn } }
```

```json
{"data":{"registrarConsentimiento":{"id":"d1e021b4-aca0-4d15-8863-4f3f688094bc",
  "finalidad":"oferta-vida-hipotecario","versionTexto":"v1","vigente":true,
  "otorgadoEn":"2026-10-12T01:55:24.379551+00:00","revocadoEn":null}}}
```

### 2.2 Oferta completa (proveedores sanos) — CA-W01-03a

140 ms. `tipo: COMPLETA`, prima puntual (`$94,680 COP`), las 5 fuentes `estado: OK`, sin `rangoPrima`.

### 2.3 Oferta degradada con caché tibia (proveedores en modo `error`) — CA-W01-03b

```bash
curl -X POST localhost:8101/control/modo -d '{"modo":"error"}'
curl -X POST localhost:8102/control/modo -d '{"modo":"error"}'
```

Misma solicitud (mismo documento, caché del paso 2.2 ya poblada), 52 ms:

```json
{"data":{"solicitarOferta":{"__typename":"Oferta","tipo":"PRELIMINAR",
  "prima":null,
  "rangoPrima":{"minima":{"valor":"75744.00"},"maxima":{"valor":"113616.00"}},
  "fuentes":[{"id":"historial-crediticio","origen":"OPEN_FINANCE","estado":"CACHE",
              "capturadoEn":"2026-10-12T01:55:42.281098+00:00"}, "...4 más, todas CACHE"]}}}
```

Nunca un 5xx: degrada a la última foto en caché (regla de arquitectura #4), más rápido que el camino
sano porque ni siquiera intenta el proveedor (el circuito ya estaba abierto por los fallos anteriores).

### 2.4 Documento nuevo, sin caché, proveedores en modo `lento`, circuito ya abierto

```json
{"data":{"solicitarOferta":{"__typename":"Oferta","tipo":"PRELIMINAR",
  "prima":null,
  "rangoPrima":{"minima":{"valor":"108000.00"},"maxima":{"valor":"162000.00"}},
  "fuentes":[{"estado":"FALLO","capturadoEn":null}, "...5 fuentes, todas FALLO"]}}}
```

47 ms — el circuito seguía abierto (TTL de 5 s) desde el paso anterior, así que el ACL Worker ni
intentó el proveedor lento; sin caché previa para este documento, las fuentes salen `FALLO` en vez de
`CACHE`, pero RATING igual arma una oferta preliminar con rango genérico. Confirma la regla #4 por un
camino distinto al del script oficial: **nunca** se devuelve un error crudo, incluso sin ningún dato
de fuentes. Este caso puntual no se traza a ningún EC específico del sprint — es un hallazgo de esta
corrida, no un escenario medido formalmente.

### 2.5 Contactar a un asesor — CA-W01-07

```json
{"data":{"contactarAsesor":{"canal":"CHAT",
  "resumen":"Recibimos tu solicitud (CHAT) sobre la oferta 03203e21-a899-4dad-ae6c-2b3796bcfb71. Un asesor te contactará."}}}
```

### 2.6 Revocar consentimiento y confirmar el bloqueo — CA-W01-01/08, regla de arquitectura #7

```json
{"data":{"revocarConsentimiento":{"id":"d1e021b4-...","vigente":false,
  "revocadoEn":"2026-10-12T01:56:24.907308+00:00"}}}
```

Nueva solicitud con ese mismo `consentimientoId`:

```json
{"data":{"solicitarOferta":{"__typename":"ErrorOferta",
  "mensaje":"No hay un consentimiento vigente para consultar tus datos.","puedeReintentar":false}}}
```

## Resumen de lo verificado

| Caso | Resultado | Evidencia |
|---|---|---|
| Oferta completa, proveedores sanos | `COMPLETA`, 131-140 ms | 1, 2.2 |
| Timeout duro de 700 ms (EC009/EC010) | cae a `PRELIMINAR` en 737 ms, nunca espera los 750-1100 ms reales | 1 |
| Degradado con caché tibia | `PRELIMINAR` con fuentes `CACHE`, más rápido que el camino sano | 2.3 |
| Degradado sin caché y circuito abierto | `PRELIMINAR` genérico, fuentes `FALLO`, nunca 5xx | 2.4 |
| Contactar asesor | confirmación con el canal pedido | 2.5 |
| Consentimiento revocado bloquea la consulta | `ErrorOferta`, `puedeReintentar: false`, nunca 5xx | 1, 2.6 |

Después de cada corrida, los stubs quedan (o se devuelven manualmente) en modo `sano`
(`POST /control/modo {"modo":"sano"}` en 8101/8102/8103) para poder repetir el flujo.
