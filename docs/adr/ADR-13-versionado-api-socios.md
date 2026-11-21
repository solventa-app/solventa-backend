# ADR-13 — Versionado de la API de socios: `/v2` convive con `/v1`

- **Estado:** propuesto. Verificado en local (pruebas del BFF y validadores del gateway); en staging sin probar.
- **Fecha:** 2026-11-20
- **Historia:** KAN-69 (HU-W07), habilitadora HA-MOD-003, escenario EC033 (evolucionar la API de socios sin romper
  a los socios que ya integraron).

## Contexto

Los socios integran la API REST `/v1` del BFF (`contracts/openapi/socios-v1.json`). Las reglas de `contracts/` solo
admiten cambios aditivos, y así se ha hecho hasta la 1.1.0. Pero hay cambios que no son aditivos, por ejemplo
cambiar la forma de un campo de la respuesta, y un socio con un cliente generado desde el contrato se rompe con
ellos. EC033 pide poder hacer esos cambios sin cortar a nadie.

El primer caso real es la oferta de `POST /v1/cotizaciones`, que trae el precio en dos campos que se excluyen entre
sí (`prima` en una COMPLETA y `rangoPrima` en una PRELIMINAR). Quien integra tiene que mirar `tipo` para saber cuál
leer.

## Decisión

1. **La versión va en la ruta** (`/v1/...`, `/v2/...`), no en una cabecera ni en el `Accept`. Es lo que ya hacían
   `/v1` y el gateway de GCP (`openapi-socios.yaml`), se ve en los logs y en el consumo, y Envoy y el API Gateway la
   enrutan sin reglas extra.
2. **Una versión nueva solo trae lo que cambia.** `/v2` tiene hoy una sola operación, `POST /v2/cotizaciones`, y su
   contrato propio (`socios-v2.json`, 2.0.0, servido en `/v2/openapi.json`). Todo lo demás (token, consentimientos,
   propuestas, pólizas y cuenta) se sigue llamando por `/v1`. No se duplica la API entera.
3. **La versión nueva es un adaptador sobre la anterior, en el BFF.** `/v2/cotizaciones` corre la misma regla de
   negocio que `/v1/cotizaciones` (`_cotizar`) y solo cambia la forma de la respuesta (`cotizacion_v2`). Así el
   presupuesto, el modo prueba, los scopes, los errores y el consumo son los mismos por construcción. Los micros no
   saben de versiones de la API pública.
4. **El consumo cuenta la operación, no la versión.** `/v2/cotizaciones` suma en `cotizaciones`, igual que `/v1`. La
   `Idempotency-Key` sí va por versión (`cotizaciones-v2`): la respuesta guardada ya tiene la forma de su versión.
5. **Cómo se retira una versión.** `/v1` no tiene fecha de retiro. Cuando se decida retirar una operación:
   - se anuncia con al menos **6 meses** de aviso a los socios activos (correo y la guía de integración);
   - desde el anuncio, sus respuestas llevan `Deprecation: true` y `Sunset: <fecha HTTP>` (RFC 8594) y un `Link` a la
     operación que la reemplaza;
   - se mira en `/v1/consumo` (por socio y API) quién la sigue llamando antes de apagarla;
   - al retirarla responde **410 `VERSION_RETIRADA`** con `ErrorPublico` durante otros 3 meses, nunca un 404 mudo.
   Nada de esto está construido hoy porque nada se ha anunciado: se construye con el primer anuncio.
6. **Los validadores conocen todas las versiones.** `scripts/validar_contratos.py` exige que las rutas de
   `socios-vN.json` vayan bajo `/vN/`, y `scripts/validar_gateway.py` compara la unión de los contratos con Envoy y
   `openapi-socios.yaml`. `scripts/sincronizar_socios.py` copia los dos contratos al BFF.

## Consecuencias

- Cambiar la forma de una respuesta deja de ser imposible: se hace en `/vN+1` sin tocar a quien está en `/vN`.
- Cada versión viva es código que se mantiene. Por eso una versión nueva solo trae lo que cambia y es un adaptador
  delgado, y por eso hay una política de retiro aunque hoy no se use.
- El portal de socios (web) no cambia: sigue en `/v1`.
- Lo que falta: probar `/v2` detrás del API Gateway real (`crear_gateway_socios = false` en staging) y publicar `/v2`
  en la guía de integración cuando el equipo lo decida.

## Alternativas descartadas

- **Versión en una cabecera (`Accept: application/vnd.solventa.v2+json`):** invisible en los logs y en el consumo,
  y el API Gateway de GCP enruta por ruta, no por cabecera.
- **Duplicar toda la API en `/v2`:** doble superficie que mantener sin ningún cambio que lo justifique.
- **Agregar `precio` a `/v1` y dejar `prima`/`rangoPrima`:** es aditivo, pero no simplifica nada para quien integra
  y no ejercita EC033, que es justamente poder hacer un cambio que no es aditivo.
