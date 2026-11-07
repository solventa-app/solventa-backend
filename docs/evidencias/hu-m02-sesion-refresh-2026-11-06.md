# Evidencia · HU-M02 sesión con refresh detrás del gateway (KAN-227)

Fecha: 2026-11-06. Entorno: **local** (`docker compose --profile servicios up -d --build --wait`, Envoy en `:8080`,
Postgres real de AUTH). No es staging.

## Pruebas de AUTH

`python -m pytest` en `services/auth` (contenedor `python:3.14-slim` con `requirements.txt` y `requirements-dev.txt`):
suite completa en verde, incluidas `tests/test_refresh.py` y `tests/test_refresh_gracia.py` (21 pruebas de refresh).

## Newman `hu-m02` por el gateway

```bash
npx --yes newman@6.2.2 run tests/newman/hu-m02.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json
```

| Iteraciones | Peticiones | Aserciones | Fallidas | Duración |
|---|---|---|---|---|
| 1 | 17 | 57 | 0 | 1,5 s (respuesta media 58 ms) |

Qué quedó verificado en vivo (B4 del plan de HU-M02):

- `iniciarSesion` por `/graphql/app` devuelve `refreshToken` y `refreshExpiraEn`; por `/graphql/web` el refresh va solo
  en la cookie `solventa_refresh`.
- `renovarSesion` rota el refresh.
- **Período de gracia con Postgres real:** reintentar con el refresh anterior antes de 30 s entrega un par nuevo y
  revoca el sucesor que no llegó (la consulta `revocar_sucesor_sin_usar` funciona contra la tabla real).
- Con el sucesor ya usado, el refresh viejo da `SESION_VENCIDA` y cae toda la cadena.
- `cerrarSesion` revoca y es idempotente; un refresh web no sirve en la app; una cuenta suspendida no renueva.

Pendiente: lo mismo en GCP API Gateway (staging).
