# Cómo usa una historia el almacenamiento de archivos (KAN-250)

Decisión y razones: [ADR-10](adr/ADR-10-almacenamiento-de-archivos.md). Servicio: [`services/almacenamiento`](../services/almacenamiento/README.md).

## 1. Declara el tipo

Agrega una fila en `contracts/tipos-archivo.json` y copia el catálogo al servicio:

```json
{
  "tipo": "siniestro-foto",
  "descripcion": "Foto del daño reportado (HU-xxx).",
  "activo": true,
  "tipos_contenido": ["image/jpeg", "image/png"],
  "tamano_max_bytes": 5242880,
  "quien_sube": ["CLIENTE"],
  "quien_lee": ["CLIENTE", "ASESOR", "BACKOFFICE"],
  "propietario": "usuario",
  "requiere_consentimiento": false,
  "retencion_dias": null
}
```

```bash
python scripts/sincronizar_tipos_archivo.py      # el CI corre la versión --verificar
```

- `quien_sube` / `quien_lee`: roles (`CLIENTE`, `ASESOR`, `BACKOFFICE`) o `servicio:<nombre>`.
- `propietario: "usuario"`: un CLIENTE solo sube y lee los suyos (el id sale del token).
- `retencion_dias`: si no es `null`, Terraform crea la regla de ciclo de vida para el prefijo `<tipo>/`.
- Formatos con validación de contenido: `image/jpeg`, `image/png`, `application/pdf`, `video/mp4`. Otro formato
  necesita su firma en `servicio_archivos.py`.

## 2. Sube

**Desde la web o el móvil (por el BFF):**

```graphql
mutation { solicitarSubidaArchivo(entrada: {tipo: "siniestro-foto", tipoContenido: "image/jpeg", tamanoBytes: 123456}) {
  ... on SubidaArchivo { url metodo encabezados { nombre valor } archivo { id } }
  ... on ErrorArchivo { codigo mensaje } } }
```

Luego `PUT url` con el archivo como cuerpo y **todos** los `encabezados` tal cual (van firmados; si falta uno, el bucket
responde 403). Al terminar, `confirmarSubidaArchivo(id)` → `Archivo { estado }`.

**Desde otro servicio (HTTP interno, con su token de identidad):** `POST /archivos/subidas` con
`X-Actor-Rol: SERVICIO` y `X-Actor-Id: <servicio>`, y luego `POST /archivos/{id}/confirmar`. En Terraform, agrega
`"almacenamiento"` a `llama` del servicio en `.github/servicios.json`.

## 3. Entérate de que está listo

Suscríbete en `infra/almacenamiento.tf` a `archivo-disponible` o `archivo-rechazado` (filtra por el atributo
`tipoArchivo`). Esquemas en `contracts/events/archivo.*.schema.json`. O consulta `GET /archivos/{id}`.

## 4. Lee

`descargarArchivo(id)` (GraphQL) o `GET /archivos/{id}/descarga`: URL de 5 minutos. No la guardes ni la registres:
pide otra cuando la necesites. Cada una queda auditada.

## 5. Cierra la retención

Cuando el trámite termina, `POST /archivos/{id}/retencion` (un CLIENTE no puede). Desde ahí corren los días del tipo.

## Local

`docker compose up almacenamiento` levanta Postgres, `fake-gcs-server` (`:4443`) y el servicio (`:8009`). Con un
volumen de Postgres anterior a este cambio, crea la base una vez:
`docker compose exec postgres createdb -U postgres almacenamiento`. Con el emulador de Android, usa
`ALMACEN_URL_PUBLICA=http://10.0.2.2:4443` y el mismo `-public-host` en `fake-gcs`.
