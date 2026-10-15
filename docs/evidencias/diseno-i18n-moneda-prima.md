# Diseño y estimación — moneda del navegador en la prima hipotecaria (+ idioma)

No es una historia del Sprint 1: no está en `docs/sprint-1.md` ni en Jira. Este documento es el "documentar y
estimar" pedido antes de decidir si se implementa — por regla del repo ("no sobre-construir"), no se toca
código hasta que esto se priorice en Planning o quepa en la holgura real.

## 1. Lo que se pide, precisado

> "La estimación [la prima] debe ser en la moneda del navegador en ese momento, y no debe cambiar una vez
> estimada."

Dos piezas distintas, de tamaño muy distinto:

1. **Moneda**: mostrar la prima ya calculada en la moneda del navegador de quien cotiza, fijada en el
   momento de cotizar.
2. **Idioma**: traducir los mensajes (errores de GraphQL, textos de dominio) según el idioma del navegador.

**Importante — esto NO es como `motor-preaprobacion`** (el otro proyecto revisado en esta conversación, que
sí decide crédito en 10 países): Solventa opera **solo en Colombia**. `valorCredito` en `SolicitudOfertaInput`
sigue siendo COP (es el crédito hipotecario real, otorgado por un banco colombiano) — eso no cambia. Lo único
que cambiaría es la **moneda en la que se MUESTRA la prima calculada** (`Oferta.prima`, `Oferta.cobertura`,
`Oferta.rangoPrima`), por conversión de tipo de cambio, no por recalcular el riesgo en otro país.

## 2. Limitación real que hay que decir de frente: la inmutabilidad no se puede garantizar hoy

`services/bff/app/esquema.py:220-222`: la query `oferta(id)` **siempre devuelve `null`** —
"RATING no persiste ofertas todavía: no hay de dónde leerla". `servicio_oferta.py` genera un
`uuid.uuid4()` nuevo en cada llamada y no guarda nada en ningún lado.

Consecuencia: hoy solo se puede garantizar que **una misma respuesta HTTP** use una sola tasa de cambio de
forma atómica (no se le pide la tasa dos veces a mitad de la misma solicitud). Pero **no existe manera de
re-consultar la misma oferta más tarde y obtener el mismo número** — porque no existe tal cosa como "la misma
oferta más tarde": cada `solicitarOferta` es una cotización nueva, siempre lo fue, con o sin moneda.
Garantizar que una prima YA MOSTRADA al usuario no cambie si refresca la página, en el día de hoy, es un
problema que vive en `solventa-frontend` (guardar la respuesta en el estado del cliente), no algo que el
backend pueda resolver sin construir primero la persistencia de ofertas emitidas — que es, deliberadamente,
trabajo de Sprint 2/3 (`policy`, emisión; ver `docs/sprint-1.md`: "EC005/EC007 (emisión) se miden en el
Sprint 2"). No lo prometo como parte de este alcance.

## 3. Diseño propuesto (moneda)

```
BFF: SolicitudOfertaInput { ...actual..., monedaPreferida: String }   # opcional, aditivo (regla #8)
        │
        ▼
RATING calcula igual que hoy, SIEMPRE en COP (sin tocar scoring/tasa/tope legal)
        │
        │  si monedaPreferida == "COP" o no se envía → responde igual que hoy
        │  si monedaPreferida != "COP":
        ▼
RATING pide la tasa de cambio al ACL Worker — NUEVO adaptador, mismo patrón que Open Finance/Open Data
(puerto + Circuit Breaker + timeout 700 ms + caché con TTL + sonda de recuperación; regla de arquitectura #2:
RATING no llama a un proveedor de FX directo)
        │
        ▼
Convierte prima/cobertura/rangoPrima con la tasa obtenida, redondea con los decimales ISO 4217 de la moneda
destino (Decimal, nunca float — mismo patrón que `money.py` de `motor-preaprobacion`), y pone esa moneda en
cada `Monto.moneda` de la respuesta.
```

**Por qué pedir `monedaPreferida` explícita en vez de inferirla del header `Accept-Language`:** un idioma de
navegador no determina una moneda de forma confiable (un `en-US` puede estar cotizando desde Colombia por
VPN o idioma del sistema en inglés). Que el frontend decida y la envíe explícita es más simple y no requiere
una tabla de mapeo idioma→moneda que de todos modos sería una aproximación.

## 4. Estimación de esfuerzo — moneda

| Pieza | Horas | Por qué |
|---|---|---|
| Nuevo adaptador de tipo de cambio en `acl-worker` (puerto, adaptador, stub `stubs/fx`, Circuit Breaker, caché, sonda) | 3-4 h | Mismo patrón que Open Finance/Open Data (T-W01-8/9/10 costó 6+5+4=15h combinadas; esto es un solo endpoint simple, bastante más chico) |
| Conversión + redondeo ISO 4217 en RATING (`money.py`-style) | 2 h | Lógica pura, sin estado |
| Cambio de contrato (`SolicitudOfertaInput.monedaPreferida`, aditivo) + `contracts/CHANGELOG.md` + aviso a `solventa-frontend` | 1 h | — |
| Pruebas (unitarias RATING, contrato del adaptador FX, humo con moneda≠COP) | 2 h | — |
| **Total** | **8-9 h** | |

**No cabe en las 3 h de holgura que quedan del Sprint 1.** Ni siquiera la pieza más chica (cambio de
contrato, 1h) alcanza a justificar abrir esto a medias sin el resto.

## 5. Estimación de esfuerzo — idioma (aparte, mucho más grande)

Hoy los mensajes de error (`"No tienes permiso para esta operación."`, `"Usuario o clave incorrectos."`,
etc.) están escritos en español **directamente en la lógica de negocio** de 6 servicios (`bff`, `auth`,
`risk`, `rating`, `payments`, `acl-worker`), sin catálogo centralizado. Hacer esto multi-idioma de verdad
requiere: extraer cada mensaje a un catálogo (`gettext`/JSON por locale), negociar el idioma en el BFF
(`Accept-Language` → catálogo), y decidir si AUTH/RISK/RATING devuelven códigos de error (y el BFF traduce)
en vez de strings — este último cambio es el correcto a largo plazo pero toca cada resolutor.

| Pieza | Horas |
|---|---|
| Extraer mensajes a códigos + catálogo en los 6 servicios | 5-6 h |
| Negociación de idioma en el BFF + traducción en la frontera | 2 h |
| Pruebas de regresión (que ningún mensaje se quede sin traducir) | 2 h |
| **Total** | **9-10 h** |

**Combinado moneda + idioma: 17-19 h** — muy por encima de la holgura del Sprint 1, y de un tamaño más
cercano a una habilitadora nueva (como LAT-002) que a una tarea suelta.

## 6. Recomendación

No implementar en el Sprint 1. Crear una historia nueva en Jira para que el equipo la priorice en Planning
del Sprint 2/3 — moneda primero (8-9h, acotado y con precedente claro en el patrón del ACL Worker), idioma
después y por separado (9-10h, toca más servicios y no tiene precedente en el repo). La limitación de la
sección 2 (inmutabilidad real de la oferta) debe citarse en la historia como dependencia de la persistencia
de ofertas (`policy`, Sprint 2/3), no como algo que esta historia resuelva por sí sola.
