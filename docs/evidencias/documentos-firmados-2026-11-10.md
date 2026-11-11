# Evidencia: documentos firmados guardados y cifrados (Q-02, KAN-26, T6), 2026-11-10

**Corrida local, no de staging.** Compose con `almacenamiento`, `migrar-policy` y `policy` reconstruidos desde la rama
`feat/KAN-26-documentos-firmados-guardados`; el bucket es `fake-gcs`. **Cloud KMS real sin probar**: en local la llave de datos
de cada documento se envuelve con la llave de desarrollo (`CIFRADO_CLAVE`); con `KMS_KEY_NAME` se envuelve en Cloud KMS.

| Qué | Resultado |
|---|---|
| Migración `0010_documentos_firmados` | Aplicada por `migrar-policy`; tabla con `UNIQUE (propuesta_id, tipo)` y `CHECK` de tipo |
| Subir y bajar, en una base temporal | `upgrade head` → `downgrade base` → `upgrade head`: `0010 (head)` |
| pytest POLICY | 302 pruebas en verde (28 nuevas: `test_sobre_documentos`, `test_documentos_firmados`) |
| pytest ALMACENAMIENTO | 65 en verde (catálogo con los dos tipos nuevos y la regla del contenedor cifrado) |
| Newman `hu-w03` (201 peticiones, 380 aserciones) | Firma con imagen, pago y emisión en verde; 2 fallas en la carpeta 5 (`crearUsuario` sin correo y el campo `yo`), que son de AUTH y no tocan POLICY ni los PDF |

## Lo que se vio en el bucket y en la base

- Tras firmar y emitir, el bucket tiene objetos `contrato-firmado/...` y `poliza-pdf/...` con tipo de contenido
  `application/octet-stream`. Los primeros bytes son `SOLVENC1\x01` (la marca y la versión del contenedor) y **no aparecen ni
  `%PDF` ni `SOLVENTA` en claro**.
- `documentos_firmados` guarda por documento el id del archivo, la llave de datos **envuelta** (80 caracteres en base64), el
  SHA-256 del PDF en claro y su tamaño (p. ej. contrato 4450 bytes, póliza 4280): ni el PDF ni la llave en claro.
- `POST /polizas/{id}/documento` pedido dos veces con nombres distintos de asegurado entrega **el mismo PDF** (SHA-256
  `2cb2a337b2d4…`, igual al guardado en `documentos_firmados`): el primero que se guardó es el que se entrega.

## Casos cubiertos por pytest (sin red)

- Ida y vuelta; una llave de datos y un nonce distintos por documento; copiar el contenedor o la llave a otra propuesta o a otro
  tipo falla; un byte alterado, la etiqueta truncada, un contenedor en claro o sin marca, otra llave maestra y una huella
  distinta no se sirven; KMS deshabilitado es una falla de disponibilidad (EC022), no de integridad.
- Si guardar falla (almacenamiento caído, KMS caído), la firma y la emisión siguen, nada sin cifrar llega al bucket y el
  documento se guarda en la siguiente consulta; si leer falla, se regenera. Un documento alterado en el bucket responde
  `DocumentoAlterado` (503 `DOCUMENTO_NO_DISPONIBLE`) y no se sirve.
- El bucket (el servicio real) rechaza un PDF en claro declarado como `application/octet-stream`.
- Purga: el contrato firmado de una propuesta cancelada antes de emitir se retiene (1 día).
- Nada del contenido, la llave envuelta ni la huella sale en los logs.

## Incidente de la corrida

`scripts/verificar-migraciones.sh policy` baja la base **real** del compose hasta `base`; con datos locales la bajada falló en la
`0007` y dejó la base en esa revisión, sin `documentos_firmados`, `producto` ni las tablas de comisiones. Se restauró con
`alembic upgrade head` (vuelve a `0010`; las tablas con datos de propuestas y pólizas siguieron intactas). **Se perdieron los datos
locales de comisiones de socios y el `producto` de las propuestas de demo** (quedan como vida deudor). El mismo script es seguro
en el CI (base vacía); en local conviene correr el ciclo contra una base temporal, como se hizo después para esta evidencia.

**Sin probar:** Cloud KMS real, staging, y el recorrido del cliente en el navegador (Cypress, punto 5 del plan).
