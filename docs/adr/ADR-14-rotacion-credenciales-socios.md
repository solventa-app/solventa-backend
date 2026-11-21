# ADR-14 — Rotación de la clave de firma y de las credenciales de los socios

- **Estado:** propuesto. En código: la clave anterior en el JWKS (AUTH), verificada con pruebas. La rotación
  **automática** necesita infraestructura y permisos que no están en el repo (ver «Qué falta»).
- **Fecha:** 2026-11-20
- **Historia:** KAN-66 (HU-W07), habilitadora HA-SEG-004 (rotación automática de credenciales).

## Contexto

Hay dos secretos en la API de socios:

1. **La clave de firma ES256** (`SOCIOS_CLAVE_FIRMA`, Secret Manager `clave-firma-socios`). Firma los tokens de 15
   minutos de todos los socios. El BFF y el API Gateway validan con el JWKS de AUTH.
2. **La credencial de cada socio** (`clientId` y clave, tabla `credenciales_socio` de AUTH). El socio la rota cuando
   quiere desde el portal (`POST /v1/credenciales/{entorno}/rotar`), y la anterior queda revocada en el acto.

Hasta hoy, cambiar la clave de firma invalidaba de golpe todos los tokens vivos: el JWKS solo tenía la clave nueva.
No había forma de rotarla sin un corte de hasta 15 minutos para todos los socios.

## Decisión

### Clave de firma: dos claves en el JWKS durante la rotación (hecho)

- AUTH lee una variable opcional, `SOCIOS_CLAVE_FIRMA_ANTERIOR`. Si trae una clave, su parte pública se publica en
  el JWKS **después** de la vigente, pero **nunca firma**. Vacía o con el marcador de Terraform
  (`pendiente-de-cargar`) significa que no hay rotación en curso.
- El BFF ya vuelve a pedir el JWKS cuando llega un `kid` que no conoce (`app/socios/autenticacion.py`), y el API
  Gateway y Envoy lo refrescan solos (caché de 10 minutos). Con las dos claves publicadas, los tokens viejos y los
  nuevos validan a la vez.

Rotación manual, sin corte (hoy):

1. Generar la clave nueva fuera del repo:
   `openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -outform DER | base64 -w0` (el mismo formato
   de `infra/README.md`).
2. Cargar la clave **vigente** como anterior: `gcloud secrets versions add clave-firma-socios-anterior` con el valor
   actual de `clave-firma-socios`.
3. Cargar la nueva: `gcloud secrets versions add clave-firma-socios`.
4. Desplegar una revisión nueva de AUTH (lee los secretos al arrancar). Desde ahí firma con la nueva y publica las
   dos.
5. Pasados **al menos 25 minutos** (15 de vida del token más 10 de caché del JWKS), cargar el marcador en
   `clave-firma-socios-anterior` y desplegar AUTH otra vez. El JWKS vuelve a tener una sola clave.

### Clave de firma: rotación automática (diseño, falta infraestructura)

- Secret Manager con `rotation { rotation_period = "7776000s" }` (90 días) y un tema de Pub/Sub para el aviso de
  rotación.
- Un Cloud Run job suscrito a ese tema que hace los pasos 1 a 4 y programa el 5 (Cloud Tasks con 30 minutos de
  demora). Necesita `secretmanager.secretVersionAdder` sobre los dos secretos y `run.services.update` sobre AUTH.
- Alternativa más simple si no se quiere un job: que AUTH lea los secretos en cada arranque y que la rotación
  dispare un `gcloud run services update auth` desde el mismo job.

### Credenciales de los socios: vencimiento a 90 días (diseño, falta construir)

Rotarlas de forma automática sin avisar rompería la integración del socio, que tiene la clave en su sistema. Lo que
se propone es que **venzan**, con aviso y solapamiento:

- Migración de AUTH: `credenciales_socio.vence_en` (por defecto `creada_en + 90 días`) y `rotar_credencial` deja la
  anterior viva **7 días** (`revocada_en = now() + 7 días`) en lugar de revocarla en el acto, para que el socio cambie
  su sistema sin corte.
- Un Cloud Scheduler diario que avise (correo al contacto del socio) a los 15 y a los 3 días del vencimiento.
- `POST /v1/token` con una credencial vencida responde lo mismo que con una inválida (`CREDENCIALES_INVALIDAS`),
  como el resto de fallos.
- El portal (W07-02) muestra la fecha de vencimiento: cambio aditivo en `socios-v1.json` (`Credencial.venceEn`).

## Qué falta y quién lo hace

| Pieza | Necesita | Estado |
|---|---|---|
| Secreto `clave-firma-socios-anterior` | `"SOCIOS_CLAVE_FIRMA_ANTERIOR": "clave-firma-socios-anterior"` en los `secretos` de `auth` (`.github/servicios.json`). Terraform lo crea con el marcador al aplicar desde el pipeline (~USD 0,06 al mes por versión activa) y AUTH lo trata como «sin rotación» hasta que se cargue | Hecho (2026-11-21): queda listo para la primera rotación manual |
| Rotación automática de la clave de firma | Rotación de Secret Manager, tema de Pub/Sub, Cloud Run job y sus permisos | Pendiente: infraestructura |
| Vencimiento de credenciales | Migración de AUTH, scheduler, plantilla de correo y el campo en el portal | Pendiente: código en AUTH, BFF y web |

## Consecuencias

- La clave de firma ya se puede rotar a mano sin cortar a ningún socio, en cuanto exista el secreto de la anterior.
- Mientras la anterior está publicada, un token robado firmado con ella sigue sirviendo hasta que vence (15 min);
  por eso se quita en cuanto pasan los 25 minutos.
- Una clave anterior mal cargada hace fallar el arranque de AUTH (falla claro, como la vigente).
