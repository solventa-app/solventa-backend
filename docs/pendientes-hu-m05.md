# HU-M05 · Pendientes del backend

Para el equipo y los agentes de `solventa-backend`. Historia **KAN-35** (HU-M05, reporte de siniestro desde el
móvil), pareja de la mesa de siniestros de HU-W04 (KAN-27). Escrito el 2026-11-05, al cerrar la prueba de punta a punta.

## Lo que ya está (PR #503 y móvil #38)

- CLAIMS `POST /siniestros` y `GET /clientes/{id}/siniestros`, migración `0004`, evento `siniestro.reportado` v1.
- BFF y contrato v0.35: `solicitarSubidaEvidenciaSiniestro`, `reportarSiniestro` y `misSiniestros`, solo CLIENTE.
- `siniestro-foto` admite que la suba un CLIENTE (a nombre del caso).
- Póliza del cliente: firmante, quien la aceptó **o** su titular (`cliente_id` = HMAC del documento).
- Semillas locales: `crear-cliente-siniestros-demo` y la póliza del cliente demo en `sembrar-polizas-demo`.
- Probado en local con la app, de punta a punta: el reporte llega PENDIENTE a la bandeja con sus fotos geoetiquetadas,
  y la aprobación o el rechazo del operador se ven en la app. Evidencia:
  `solventa-frontend-mobile/docs/evidencias/2026-11-05-hu-m05-punta-a-punta.md`.

## Pendiente (en orden sugerido)

### 1. Newman `hu-m05` (KAN-278): **hecho** en `tests/newman/hu-m05.postman_collection.json` (2026-11-05)
Colección en `tests/newman/`, como `hu-w04`, sobre el compose con las semillas. Casos:

| # | Caso | Esperado |
|---|---|---|
| 1 | CLIENTE demo: `solicitarSubidaEvidenciaSiniestro` + PUT + `confirmarSubidaArchivo` + `reportarSiniestro` | `SiniestroReportado` PENDIENTE con radicado `SIN-AAAA-1NNN` y `fotos` = las enviadas |
| 2 | Repetir `reportarSiniestro` con el mismo `idReporte` | El mismo caso, sin uno nuevo en `siniestros` |
| 3 | Póliza de otro titular (una de `demo-siniestros-prop-NN`) | `ErrorSiniestro` «La póliza no existe o no es tuya.» |
| 4 | `ocurridoEn` fuera de la vigencia o en el futuro | `ErrorSiniestro` con el mensaje de CLAIMS |
| 5 | Una foto subida a nombre de otro `idReporte` | `ErrorSiniestro` «Una de las fotos no se subió bien…» |
| 6 | Sin token, o con token de ASESOR o BACKOFFICE | `SIN_SESION`, o «No tienes permiso…» |
| 7 | BACKOFFICE: `siniestros(estado: PENDIENTE)` | El caso del paso 1 aparece primero |
| 8 | BACKOFFICE aprueba y el CLIENTE consulta `misSiniestros` | El caso sale `APROBADO` |

### 2. La resolución que ve el cliente (contrato, aditivo): **hecho**, contrato **v0.37** (2026-11-05)
`SiniestroReportado` solo trae el estado. La app hoy muestra un texto genérico: «Tu indemnización está en proceso de
pago» o «Te enviamos el motivo…». Propuesta: un campo `resolucion` reducido para el CLIENTE, sin datos del operador ni
del perito:

```graphql
type ResolucionCliente {
  tipo: TipoResolucion!          # APROBACION, RECHAZO o DERIVACION
  resueltaEn: DateTime!
  motivo: MotivoRechazo          # solo RECHAZO
  viaRecurso: String             # solo RECHAZO
  indemnizacion: Monto           # solo APROBACION
  plazoDispersion: String        # solo APROBACION
}
extend type SiniestroReportado { resolucion: ResolucionCliente }
```

CLAIMS ya arma estos datos en el expediente; el BFF solo los recorta. El comentario del operador **no** va: es texto libre
del BACKOFFICE.

### 3. Geoetiqueta desde el EXIF (W04-14)
Hoy la geoetiqueta es la que declara la app: `latitud` y `longitud` de cada `FotoReporteInput`, tomadas del GPS del
teléfono. La foto no lleva GPS en su EXIF; el plugin de cámara de Android no lo escribe. Si se quiere evidencia más
fuerte:
- CLAIMS (o un consumidor de `archivo-disponible` para `siniestro-foto`) lee el EXIF, si lo trae, y lo compara con lo
  declarado.
- La verificación «Georreferenciación cotejada en rango de ocurrencia» de la mesa podría calcularse así, en vez de venir
  de la semilla.

### 4. Avisar al cliente cuando cambia el estado
`siniestro.aprobado`, `.rechazado` y `.derivado` ya se publican, sin consumidor. Para que el cliente se entere sin abrir
«Mis siniestros», un consumidor que mande el aviso: correo con el proveedor de notificaciones o push de HU-M04 con FCM.
Hoy el cliente se entera solo al consultar.

### 5. Staging
- Verificar en staging que CLAIMS lea `GET /archivos/{id}` del almacenamiento con su identidad de servicio. `claims` ya
  tiene `almacenamiento` en `llama` de `.github/servicios.json`, pero no se ha probado allí.
- Decidir cómo tener un cliente con póliza en staging para la demo: `crear-cliente-siniestros-demo` es solo local.
  Opciones: un Cloud Run Job equivalente, o el recorrido real (onboarding + cotización + firma + pago).
- CMEK y Cloud KMS para los campos cifrados nuevos (relato, ubicación) siguen la regla del resto de CLAIMS.

### 6. Medición (opcional)
k6 de `misSiniestros` sobre la secundaria (EC011/EC012), igual que la bandeja (`tests/k6/w04-bandeja.js`).

### 7. Documentación (KAN-279): **hecho** (2026-11-08)
`PROJECT_STATUS.md` y `CLAUDE.md`:
- HU-M05 en código y en local.
- Contrato **v0.35**; siguiente libre **v0.36**.
- CLAIMS `0004`.
- La tarea `crear-cliente-siniestros-demo`.

## Notas para quien lo tome

- El radicado de los casos del cliente arranca en `SIN-AAAA-1001` (contador en `contadores`). La semilla usa números
  menores y no chocan.
- `entidadAcreedora` llega como «Por confirmar con el asegurado» y la suma reclamada es la cobertura de la póliza
  (W04-8 la acota igual al aprobar). Si producto quiere que el cliente estime el valor, es un campo opcional en
  `ReporteSiniestroInput`.
- `crear_cliente_demo` escribe `correo_hmac` con un `UPDATE` directo, porque `ServicioUsuarios.crear` no lo guarda: solo
  lo hacen las cuentas del onboarding. Si se quiere que **todo** CLIENTE creado por un BACKOFFICE pueda entrar con su
  correo desde la app, eso va en AUTH y no en la semilla.
