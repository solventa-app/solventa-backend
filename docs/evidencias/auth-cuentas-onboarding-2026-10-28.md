# Evidencia: cuentas del onboarding en AUTH (HU-M01 / KAN-31), 2026-10-28

**Corrida local, no de staging.** AUTH con PostgreSQL real (`docker compose`), migración `0009` aplicada (sobre la `0008` de W03).

| Paso | Resultado |
|---|---|
| Crear cuenta con `Vivo.Prueba@Ejemplo.com` | 200, `PENDIENTE_VERIFICACION`, alias opaco `c-…`, `tiene_contacto = true` |
| Mismo correo con otras mayúsculas y espacios | 409 |
| Login por correo, canal `web`, sin verificar | 403 `VERIFICACION_PENDIENTE` |
| Login por correo, canal `app`, sin verificar | 200, scopes `["verificacion-pendiente"]` |
| Clave mala por la web | 401 con el mensaje genérico (no revela el estado) |
| Cerrar verificación aprobada con nombre y documento | 200, `ACTIVO`, con `titular_id` (W03-36) |
| Renovar permisos con el token de la app | 200, scopes de CLIENTE, **misma expiración**, nombre y documento `•••• 5060` |
| Verificar otra cuenta con el mismo documento | 409 `Ya existe un cliente con ese documento.` |
| Cerrar verificación rechazada (otra cuenta) | `RECHAZADA` |

- En la base, `usuario` es opaco y `correo_cifrado`, `correo_hmac` y `documento_cifrado` no contienen el correo ni el documento; los logs de AUTH no
  contienen el correo ni el nombre (0 coincidencias).
- Migración: `heads` = `0009 (head)` (una cabeza); `upgrade` desde base, `downgrade base` y `upgrade` sin
  errores. Bajar a `0008` con una cuenta RECHAZADA la deja SUSPENDIDA (cumple el CHECK anterior) y volver a subir funciona.
- Pruebas: 187 de pytest (12 nuevas en `tests/test_cuentas_onboarding.py`).

**Sin probar:** la llamada desde el servicio `onboarding` y el BFF (siguiente PR) 
