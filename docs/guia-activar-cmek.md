# Guía: activar CMEK en Cloud SQL y Redis de staging (KAN-64, HA-SEG-002)

Decisión y contexto en [ADR-12](adr/ADR-12-cmek-cloud-sql.md). Esta guía es el paso a paso para la persona del equipo
que hace la ventana. El código ya está en `develop` (`usar_cmek_sql = true` y `usar_cmek_redis = true` en
`infra/staging.tfvars`); lo que falta lo hace una persona con permisos de propietario sobre `solventa-511815`.

## Costo

| Qué | Costo estimado |
|---|---|
| Llave `campos-personales` (ya existe) | USD 0 extra: es la misma llave de Cloud KMS |
| Operaciones de KMS de los discos | Centavos al mes (USD 0,03 por cada 10.000 operaciones) |
| Cloud SQL encendido durante la ventana | Unas horas de `db-f1-micro`: menos de USD 0,10 |
| Bucket de respaldo con los volcados | Pocos MB, menos de USD 0,01; se borra a los 7 días |

## Antes de la ventana

1. **Avisar al equipo** de que staging no se usa durante la ventana (de 30 a 60 minutos). Nadie escribe mientras se
   exporta.
2. **No liberar a `main` hasta la ventana.** La liberación que lleve este cambio dispara *Deploy staging* y su plan
   va a **reemplazar** `google_sql_database_instance.principal` (y Redis, si está encendido). El guardia
   (`scripts/revisar_plan.py`) lo bloquea a propósito, así que ese despliegue y el botón *Costos staging* quedan
   bloqueados hasta terminar la ventana. Lo ideal es liberar justo al empezarla.

## Durante la ventana

```bash
PROYECTO=solventa-511815
INSTANCIA=solventa-backend-postgres
BUCKET=gs://$PROYECTO-respaldos-sql
FECHA=$(date +%F)
```

1. **Encender** Cloud SQL con *Costos staging → encender* (antes de liberar, para que no lo bloquee el guardia).
2. **Crear el bucket de respaldo** (una sola vez) y darle escritura a la cuenta de la instancia:

   ```bash
   gcloud storage buckets create "$BUCKET" --project="$PROYECTO" --location=us-central1 \
     --uniform-bucket-level-access
   echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}' > /tmp/ciclo.json
   gcloud storage buckets update "$BUCKET" --lifecycle-file=/tmp/ciclo.json
   SA_SQL=$(gcloud sql instances describe "$INSTANCIA" --project="$PROYECTO" --format='value(serviceAccountEmailAddress)')
   gcloud storage buckets add-iam-policy-binding "$BUCKET" --member="serviceAccount:$SA_SQL" \
     --role=roles/storage.objectAdmin
   ```

3. **Exportar las cinco bases** y comprobar que ninguna pesa 0:

   ```bash
   for BASE in auth payments policy almacenamiento onboarding; do
     gcloud sql export sql "$INSTANCIA" "$BUCKET/cmek-$FECHA/$BASE.sql.gz" --database="$BASE" \
       --clean --if-exists --project="$PROYECTO"
   done
   gcloud storage ls -l "$BUCKET/cmek-$FECHA/"
   ```

4. **Liberar a `main`** (`release/sprint-N` → `main`). *Deploy staging* va a fallar en el paso del plan con el
   reemplazo de la instancia: es lo esperado. Revisar en el resumen del job que lo único que se reemplaza o destruye
   es `google_sql_database_instance.principal` (y `google_redis_instance.cache` si estaba encendido), más lo que
   cuelga de ellos (bases, usuarios y versiones de secretos de conexión).
5. **Aplicar con permiso**: *Actions → Deploy staging → Run workflow* sobre `main`, servicios `todos`, infra `true` y
   `permitir_destruir = true`. Terraform crea el agente de Cloud SQL si falta, le da permiso sobre la llave y crea la
   instancia nueva con `encryption_key_name`, con las bases vacías.
6. **Importar** en el mismo orden:

   ```bash
   SA_SQL=$(gcloud sql instances describe "$INSTANCIA" --project="$PROYECTO" --format='value(serviceAccountEmailAddress)')
   gcloud storage buckets add-iam-policy-binding "$BUCKET" --member="serviceAccount:$SA_SQL" \
     --role=roles/storage.objectAdmin   # la instancia nueva puede tener otra cuenta
   for BASE in auth payments policy almacenamiento onboarding; do
     gcloud sql import sql "$INSTANCIA" "$BUCKET/cmek-$FECHA/$BASE.sql.gz" --database="$BASE" \
       --project="$PROYECTO" --quiet
   done
   ```

   El despliegue del paso 5 alcanza a correr las migraciones sobre las bases vacías. No pasa nada: los volcados se
   exportaron con `--clean --if-exists`, así que cada importación borra esas tablas antes de crear las suyas. Todos los
   servicios se conectan como `postgres` (lo crea Terraform), así que no hay usuarios que recrear.
7. **Verificar** que los servicios arrancan (`/health` de `auth`, `payments`, `policy`, `almacenamiento` y
   `onboarding`) y que el disco usa la llave:

   ```bash
   gcloud sql instances describe "$INSTANCIA" --project="$PROYECTO" \
     --format='value(diskEncryptionConfiguration.kmsKeyName,diskEncryptionStatus.kmsKeyVersionName)'
   gcloud redis instances describe solventa-backend-redis --region=us-central1 --project="$PROYECTO" \
     --format='value(customerManagedKey)'   # solo si Redis está encendido
   ```

8. **Guardar la evidencia** en `docs/evidencias/HA-SEG-002-cmek-<fecha>.md`: la salida del paso 7, el Newman de
   regresión contra staging (`hu-w01`, `hu-w03`, `socios-w07`) y la hora de inicio y fin de la ventana.
9. **Apagar** con *Costos staging → apagar* si no se va a usar staging.

## Si algo sale mal

Los volcados siguen en el bucket por 7 días. Se vuelve a `usar_cmek_sql = false` con un hotfix, se corre *Deploy
staging* otra vez con `permitir_destruir = true` y se importa igual que en el paso 6.

## Redis

Redis no necesita migración: es una caché y *Costos staging* lo borra y lo recrea. Si está apagado al liberar, nace
con la llave en el siguiente *encender*; si está encendido, el paso 5 lo reemplaza junto con Cloud SQL.
