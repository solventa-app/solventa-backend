# KAN-223: k6 EC015 y EC018 y OWASP ZAP sobre la API de socios `/v1` (2026-11-21)

> **Corrida local, no de staging.** AUTH y el BFF en un solo proceso cada uno (uvicorn, sin gateway), PostgreSQL 16 y
> Redis locales, sin Docker. Los números sirven para validar los guiones y el comportamiento del límite de tasa
> (KAN-217, PR #676); el piso de los escenarios se mide en staging (regla 9). Todo con socios en entorno **PRUEBAS**
> (modo prueba de W07: no toca micros ni stubs), así que la latencia es la del BFF + AUTH, no la de la cotización real.

## Entorno

| Pieza | Detalle |
| --- | --- |
| Código | rama `feat/KAN-217-limite-tasa-socios` (a830c81), que trae el límite de tasa |
| AUTH | `:8001`, PostgreSQL 16 local, firma ES256 con la clave local fija |
| BFF | `:8000`, Redis local (`db 3`) para límite de tasa, idempotencia y antifraude |
| Socios | `socio-k6-1`, `socio-k6-2` y `socio-k6-3`, creados con `python -m app.crear_socio` (presupuesto alto) |
| Herramientas | k6 1.3.0; OWASP ZAP 2.16.1 (complementos automation, openapi y reports) |

Las definiciones de EC015 y EC018 no están escritas en este repo: la interpretación de cada una va declarada en su guion
y en `tests/k6/README.md`.

## EC015: escalamiento del tráfico de socios

`tests/k6/ec015-socios-escala.js`, con el BFF en `LIMITE_SOCIO_RPS_PRUEBAS=0` (sin límite, para medir solo la escala).
3 socios, 4 escalones de 1 min (15, 30, 45 y 60 cotizaciones/s en total), `POST /v1/cotizaciones` con consentimiento
previo.

| Umbral | Resultado |
| --- | --- |
| p95 ≤ 700 ms en cada escalón | ✅ 14,7 / 12,7 / 11,5 / 11,1 ms (15 / 30 / 45 / 60 por segundo) |
| 0 % 5xx | ✅ 0 de 10.274 |
| ≥ 99 % de 200 con oferta | ✅ 100 % (10.274) |

p95 global 12,6 ms, p99 15,4 ms, máximo 83,5 ms; nunca hubo más de 4 VU activos. El autoescalado de Cloud Run y el
gateway no entran en esta corrida.

## EC018: aislamiento entre socios (límite de tasa)

`tests/k6/ec018-socios-aislamiento.js`, con los límites por defecto (`PRODUCCION=20`, `PRUEBAS=5` por segundo). Durante
2 min el socio 1 llama a 50 cotizaciones/s y el socio 2 a 3/s, a la vez.

| Umbral | Resultado |
| --- | --- |
| Socio normal: 0 % 429 | ✅ 0 de 361 |
| Socio normal: 0 % 5xx | ✅ 0 de 361 |
| Socio normal: p95 ≤ 700 ms | ✅ 13,8 ms |
| Abusivo: 0 % 5xx | ✅ 0 de 6.000 |
| Abusivo: todo 429 es `LIMITE_DE_TASA` con `Retry-After: 1` | ✅ 100 % (5.396) |

Al abusivo se le aceptaron 604 (4,94/s, el tope de 5/s) y su presupuesto bajó exactamente 604 (3.588 → 4.192 en el
consumo): **el 429 no descuenta presupuesto**.

## OWASP ZAP sobre `/v1`

`scripts/zap_socios.sh` con el plan `tests/zap/socios-v1.yaml`: importa `/v1/openapi.json` (23 URL), escaneo pasivo y
activo de fuerza baja (1 min 54 s), con el token de `socio-k6-3` de PRUEBAS. El BFF corrió con
`LIMITE_SOCIO_RPS_PRUEBAS=100` para que el límite no cortara el escaneo.

| Riesgo (confianza) | Alerta | Dónde | Lectura |
| --- | --- | --- | --- |
| Alto (baja) | Path Traversal | `POST /v1/consentimientos`, campos `documentoIdentidad` y `versionTexto` | **Falso positivo.** ZAP mandó el último tramo de la ruta (`consentimientos`) como valor y vio una respuesta parecida. Reproducido a mano: el BFF no lee archivos; responde 201 y devuelve el consentimiento, sin contenido del disco. Ver la nota de abajo |
| Medio (alta) | CSP no definida | `/v1/docs` | Swagger UI de pruebas, sin datos. Aceptado por ahora |
| Medio (media) | Falta cabecera anti-clickjacking | `/v1/docs` | Igual que la anterior |
| Bajo (media) | JavaScript de otro dominio | `/v1/docs` | Swagger UI carga sus recursos de un CDN |
| Bajo (media) | Falta `X-Content-Type-Options` | 6 URL, entre ellas `/v1/.well-known/jwks.json` y `/v1/docs` | Recomendable añadir `nosniff` en el BFF o en el gateway |
| Informativo | User Agent Fuzzer, Modern Web Application | `/v1/estado`, `/v1/docs` | Sin acción |

Sin hallazgos reales de inyección, traversal ni exposición de datos en las operaciones con token.

**Nota del falso positivo.** En modo prueba, `POST /v1/consentimientos` acepta un `documentoIdentidad` sin dígitos
(`consentimientos`), mientras que en producción el BFF exige al menos 6 dígitos (`app/socios/api.py`) antes de llamar a
AUTH. La diferencia no abre nada (el modo prueba no toca micros ni guarda datos reales), pero el sandbox debería
rechazar lo mismo que producción. Queda como ajuste aparte, fuera de este PR.

## Lo que falta

- Correr los tres en staging, detrás del gateway y con Cloud Run autoescalando.
- Añadir ZAP sobre `/v1` al CI como paso informativo. El socio demo de PRUEBAS tiene un tope de 5/s, así que haría falta
  un socio propio del CI con `LIMITE_SOCIO_RPS_PRUEBAS` mayor o un `delayInMs` alto en el plan.
- Cabeceras `X-Content-Type-Options` y CSP en `/v1/docs`, si el equipo las quiere.
