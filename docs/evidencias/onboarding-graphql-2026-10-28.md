# Evidencia — onboarding del móvil por GraphQL (HU-M01 / KAN-31, contrato v0.20) — 2026-10-28

**Corrida local, no de staging.** Docker Compose con `postgres`, `auth`, `almacenamiento` + `fake-gcs`, `acl-worker`
+ `stub-kyc` (rutas de Didit), `onboarding` y `bff`, sobre `develop` en `5b96d0b` (con el PR de AUTH #371). El guion
hace lo mismo que hará la app: llama al BFF en `/graphql/app` y hace el `PUT` de cada pieza directo a la URL firmada.
Las imágenes son bytes sintéticos con los marcadores del stub (`NUMERO=…`, `CASO=menor`). Ningún dato es real.

## Tres recorridos

1. Documento nuevo: VERIFICADA, la cuenta pasa a ACTIVO con nombre y documento enmascarado, y el token nuevo de
   `actualizarSesion` ya entra a lo demás. `propuestas` da error porque POLICY no se levantó en esta corrida, no por
   permisos.
2. Mismo documento con otro correo: RECHAZADA `DOCUMENTO_YA_REGISTRADO`, sin intentos. La cuenta pasa a RECHAZADA.
3. Menor de edad: RECHAZADA `MENOR_DE_EDAD` con 2 intentos. La cuenta sigue en PENDIENTE_VERIFICACION, porque aún
   puede reintentar.

En los tres: la subida sin consentimiento responde `SIN_CONSENTIMIENTO`, el token pendiente no entra a `propuestas` y
el login por la web responde `VERIFICACION_PENDIENTE`.

```text

### ana@example.com documento=1000000001 caso=aprobado
iniciarOnboarding -> INICIADO token vence 2026-10-28T15:03:18.228733+00:00
subida sin consentimiento -> {'codigo': 'SIN_CONSENTIMIENTO'}
registrarConsentimientoOnboarding -> INICIADO
  ANVERSO: PUT 200, confirmar -> INICIADO
  REVERSO: PUT 200, confirmar -> INICIADO
  ROSTRO: PUT 200, confirmar -> INICIADO
  VIDEO: PUT 200, confirmar -> INICIADO
crearCuentaOnboarding -> {'id': '<id>', 'estado': 'PENDIENTE_VERIFICACION', 'rol': 'CLIENTE'}
propuestas con token pendiente -> Estamos verificando tu identidad. Podrás usar esto al terminar.
iniciarSesion en web -> {'codigo': 'VERIFICACION_PENDIENTE', 'mensaje': 'Tu identidad aún no está verificada. Ingresa desde la app Solventa.'}
enviarVerificacion -> EN_VERIFICACION
miVerificacion -> {'estado': 'VERIFICADA', 'motivo': None, 'intentosRestantes': 2} (0.5 s)
actualizarSesion -> {'estado': 'ACTIVO', 'nombre': 'Usuario De Prueba', 'documentoEnmascarado': '•••• 0001'}
propuestas con token nuevo -> No pudimos consultar las propuestas. Intenta de nuevo.
iniciarSesion en web -> {'usuario': {'estado': 'ACTIVO'}}

### beto@example.com documento=1000000001 caso=aprobado
iniciarOnboarding -> INICIADO token vence 2026-10-28T15:03:19.628659+00:00
subida sin consentimiento -> {'codigo': 'SIN_CONSENTIMIENTO'}
registrarConsentimientoOnboarding -> INICIADO
  ANVERSO: PUT 200, confirmar -> INICIADO
  REVERSO: PUT 200, confirmar -> INICIADO
  ROSTRO: PUT 200, confirmar -> INICIADO
  VIDEO: PUT 200, confirmar -> INICIADO
crearCuentaOnboarding -> {'id': '<id>', 'estado': 'PENDIENTE_VERIFICACION', 'rol': 'CLIENTE'}
propuestas con token pendiente -> Estamos verificando tu identidad. Podrás usar esto al terminar.
iniciarSesion en web -> {'codigo': 'VERIFICACION_PENDIENTE', 'mensaje': 'Tu identidad aún no está verificada. Ingresa desde la app Solventa.'}
enviarVerificacion -> EN_VERIFICACION
miVerificacion -> {'estado': 'RECHAZADA', 'motivo': 'DOCUMENTO_YA_REGISTRADO', 'intentosRestantes': 0} (0.5 s)
actualizarSesion -> {'estado': 'RECHAZADA', 'nombre': None, 'documentoEnmascarado': None}
propuestas con token nuevo -> Estamos verificando tu identidad. Podrás usar esto al terminar.
iniciarSesion en web -> {'codigo': 'VERIFICACION_PENDIENTE', 'mensaje': 'Tu identidad aún no está verificada. Ingresa desde la app Solventa.'}

### caro@example.com documento=1000000003 caso=menor
iniciarOnboarding -> INICIADO token vence 2026-10-28T15:03:20.895341+00:00
subida sin consentimiento -> {'codigo': 'SIN_CONSENTIMIENTO'}
registrarConsentimientoOnboarding -> INICIADO
  ANVERSO: PUT 200, confirmar -> INICIADO
  REVERSO: PUT 200, confirmar -> INICIADO
  ROSTRO: PUT 200, confirmar -> INICIADO
  VIDEO: PUT 200, confirmar -> INICIADO
crearCuentaOnboarding -> {'id': '<id>', 'estado': 'PENDIENTE_VERIFICACION', 'rol': 'CLIENTE'}
propuestas con token pendiente -> Estamos verificando tu identidad. Podrás usar esto al terminar.
iniciarSesion en web -> {'codigo': 'VERIFICACION_PENDIENTE', 'mensaje': 'Tu identidad aún no está verificada. Ingresa desde la app Solventa.'}
enviarVerificacion -> EN_VERIFICACION
miVerificacion -> {'estado': 'RECHAZADA', 'motivo': 'MENOR_DE_EDAD', 'intentosRestantes': 2} (0.5 s)
actualizarSesion -> {'estado': 'PENDIENTE_VERIFICACION', 'nombre': None, 'documentoEnmascarado': None}
propuestas con token nuevo -> Estamos verificando tu identidad. Podrás usar esto al terminar.
iniciarSesion en web -> {'codigo': 'VERIFICACION_PENDIENTE', 'mensaje': 'Tu identidad aún no está verificada. Ingresa desde la app Solventa.'}
```

## Base de ONBOARDING después de la corrida (antes de probar la migración)

```text
estado | motivo | cuenta_notificada_en no nula
VERIFICADA||t
RECHAZADA|DOCUMENTO_YA_REGISTRADO|t
RECHAZADA|MENOR_DE_EDAD|f
```

La tercera no se notifica a AUTH a propósito: aún tiene intentos.

## Cuentas en AUTH

```text
ACTIVO|t|t
RECHAZADA|f|f
PENDIENTE_VERIFICACION|f|f
```

## Sin datos personales en los logs

`docker compose logs | grep -ciE "ana@example|beto@|1000000001|PRUEBA FICTICIA|Usuario De Prueba"` → **0**.

## Migración `0002` del onboarding

`alembic downgrade 0001` quita `cuenta_notificada_en` (versión `0001`, 0 columnas) y `alembic upgrade head` la vuelve a
poner (versión `0002`, 1 columna) con las filas de la corrida adentro. Bajar borra las marcas (la columna vuelve
vacía): en ese caso el onboarding vuelve a avisar a AUTH, que responde igual porque es idempotente. En staging no se
baja el esquema (ADR-07).

## Pruebas

- `services/onboarding`: 35 pruebas (7 nuevas en `tests/test_cuenta_auth.py`).
- `services/bff`: todas pasan; 8 nuevas en `tests/test_onboarding.py`. La equivalencia esquema↔contrato también pasa.
- `python scripts/validar_contratos.py` y `python scripts/validar_servicios.py`: OK.
