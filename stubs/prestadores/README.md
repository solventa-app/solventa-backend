# stub-prestadores

Red de prestadores de asistencia en sitio simulada (HU-M06, contrato v0.49, tarea B7 del plan de HU-M06). La arquitectura
pone la red de prestadores detrás del ACL Worker, como KYC y firma: CLAIMS pide el desenlace al ACL
(`POST /prestadores/asistencias`) y el ACL llama a este stub. Antes la simulación vivía dentro de CLAIMS
(`ASISTENCIA_SIMULACION`, retirado).

## Contrato

- `GET /health` y `GET /metrics`.
- `GET|POST /control/modo` (`sano`, `lento`, `error`, `caido`), igual que los demás stubs; override por petición con
  `?modo=` o `X-Modo-Simulado`.
- `GET /v1/estado`: para la sonda de recuperación del circuito del ACL (obedece el modo, no confirma nada).
- `POST /v1/asistencias` `{asistencia_id, intento, tipo_asistencia, prestador_id, prestador_latitud, prestador_longitud,
  latitud, longitud}` → `{estado: CONFIRMADA | NO_CONFIRMADA, minutos_estimados, motivo}`.

## Variables

| Variable | Por defecto | Uso |
|---|---|---|
| `PRESTADORES_SIMULACION` | `primero_falla` | `primero_falla`: el primer intento no se confirma (`PRESTADOR_SIN_RESPUESTA`) y el reintento sí (M06-04b y M06-04a). `confirma`: siempre confirma |
| `PRESTADORES_MINUTOS_FIJOS` | vacío | Fija los minutos de llegada (p. ej. `1` para probar «Mis asistencias» sin esperar) |

Sin `PRESTADORES_MINUTOS_FIJOS`, los minutos son la línea recta (Haversine) a 20 km/h más 2 min de alistamiento, las
mismas reglas de la app. Sin coordenadas del cliente no hay tiempo.

## Privacidad

Recibe la ubicación del cliente porque es adónde debe llegar el prestador. No la escribe en los logs y no guarda nada.

## Correr

```bash
docker compose --profile servicios up -d --build --wait stub-prestadores
cd stubs/prestadores && python -m pytest
```

Puerto **8109** (el 8107 es de `stub-vehiculos` y el 8108 de `stub-eventos-parametricos`, PR #589).
