# ADR-12 — CMEK en Cloud SQL: deuda consciente y procedimiento para activarlo

- **Estado:** aceptado como deuda consciente (2026-11-20). La activación queda lista para una ventana del equipo.
- **Historia:** HA-SEG-002 (KAN-64), escenario EC022. Pendiente 7 del plan de Felipe del Sprint 2
  (`sprint-2/pendientes-sprint-2-felipe.md` en la carpeta del proyecto).

## Contexto

HA-SEG-002 pide cifrado en reposo con llaves administradas por Solventa (CMEK) para los datos personales y
financieros. Hoy hay tres capas, y solo falta una:

| Capa | Estado | Dónde |
|---|---|---|
| Campos personales cifrados con AES-256-GCM y llave envuelta por Cloud KMS (llave `campos-personales`) | ✅ en AUTH, RISK, RATING, PAYMENTS, POLICY, ONBOARDING y CLAIMS | `cifrado.py` de cada servicio, `infra/kms.tf` |
| Bucket de archivos con CMEK (llave `archivos`) | ✅ | `infra/almacenamiento.tf` |
| Disco de Cloud SQL con CMEK | ❌ `usar_cmek_sql = false` | `infra/staging.tfvars:20`, `infra/cloud-sql.tf` (`encryption_key_name`) |
| Memorystore Redis con CMEK | ❌ `usar_cmek_redis = false` (Redis solo guarda cachés derivadas y desechables) | `infra/staging.tfvars:21` |
| MongoDB Atlas M0 (RISK, RATING, CLAIMS) | No admite CMEK (exige M10, ~USD 57/mes). Lo cubre el cifrado por campo | `infra/README.md` |

**Por qué Cloud SQL no se activó con una bandera.** Cloud SQL solo acepta `encryption_key_name` al **crear** la
instancia. Poner `usar_cmek_sql = true` hace que Terraform **reemplace** `google_sql_database_instance.principal`,
es decir, que la borre y la cree de nuevo, y la instancia de staging no tiene respaldos automáticos
(`backup_configuration.enabled = false`). El guardia del pipeline (`scripts/revisar_plan.py`, ADR-09) bloquea ese
reemplazo a propósito, y está bien que lo haga: perder `auth`, `payments`, `policy`, `almacenamiento` y
`onboarding` sin exportarlas antes no tiene vuelta atrás.

## Decisión

1. **Por ahora, CMEK en Cloud SQL queda como deuda consciente.** La mitigación vigente es el cifrado por campo con
   llave de Cloud KMS: un dato personal leído del disco sin la llave de KMS no se puede descifrar. El disco usa el
   cifrado por defecto de Google (AES-256 con llaves de Google).
2. **TLS en tránsito:** Cloud Run habla con Cloud SQL por el socket `/cloudsql` del conector de Cloud SQL (`infra/servicios.tf`), que cifra siempre, y el tráfico público entra
   por HTTPS de Cloud Run y API Gateway. Evidencia a guardar en la siguiente sesión de staging:
   `gcloud sql instances describe solventa-backend-postgres --format='value(settings.ipConfiguration.sslMode)'`.
3. **La activación se hace en una ventana del equipo con el procedimiento de abajo**, nunca en un merge automático.
   Costo: unas horas de Cloud SQL encendido (centavos). La llave es la misma `campos-personales`, sin costo extra.
4. **Redis** se puede activar con `usar_cmek_redis = true` en un PR propio: Memorystore se borra y se crea a diario
   con `costos-staging.yml`, así que toma la llave en el siguiente encendido sin migrar nada.

## Procedimiento para activar CMEK en Cloud SQL (30 a 60 min, con staging encendido)

Lo corre una persona del equipo con permisos sobre el proyecto `solventa-511815`. Ningún paso lo hace el pipeline
solo.

1. **Encender y congelar.** Encender Cloud SQL con `Costos staging` y avisar al equipo que staging no se usa durante
   la ventana (nadie escribe mientras se exporta).
2. **Exportar cada base al bucket** (la cuenta de servicio de la instancia necesita `roles/storage.objectAdmin` sobre
   el bucket de respaldo):

   ```bash
   INSTANCIA=solventa-backend-postgres
   BUCKET=gs://solventa-511815-respaldos-sql   # crear antes si no existe, con retención corta
   for BASE in auth payments policy almacenamiento onboarding; do
     gcloud sql export sql "$INSTANCIA" "$BUCKET/cmek-$(date +%F)/$BASE.sql.gz" --database="$BASE"
   done
   gsutil ls -l "$BUCKET/cmek-$(date +%F)/"   # comprobar que están los cinco y que no pesan 0
   ```

3. **Aplicar con permiso de reemplazo.** PR que cambia `usar_cmek_sql = true` en `infra/staging.tfvars`. Al
   mergearlo a `main`, el guardia va a bloquear el reemplazo de la instancia. Revisar el plan en el resumen del job y
   correr `deploy-staging.yml` a mano con `permitir_destruir = true`. Terraform crea la instancia nueva con
   `encryption_key_name` y las bases vacías.
4. **Importar** en el mismo orden:

   ```bash
   for BASE in auth payments policy almacenamiento onboarding; do
     gcloud sql import sql "$INSTANCIA" "$BUCKET/cmek-$(date +%F)/$BASE.sql.gz" --database="$BASE" --quiet
   done
   ```

5. **Secretos de conexión.** La instancia conserva el nombre y Terraform vuelve a escribir la versión del secreto
   de la clave de `postgres`. Revisar que los servicios arrancan (`/health`) y, si alguno guarda la URL de conexión
   aparte, actualizarla.
6. **Verificar** y guardar la evidencia en `docs/evidencias/HA-SEG-002-cmek-<fecha>.md`:
   - `gcloud sql instances describe "$INSTANCIA" --format='value(diskEncryptionConfiguration.kmsKeyName)'` muestra
     la llave `campos-personales`.
   - `gcloud sql instances describe "$INSTANCIA" --format='value(diskEncryptionStatus.kmsKeyVersionName)'`.
   - Newman de regresión (`tests/newman/hu-w01`, `hu-w03`, `socios-w07`) en verde contra staging.
7. **Si algo falla:** la exportación sigue en el bucket. Se vuelve a `usar_cmek_sql = false`, se aplica de nuevo con
   `permitir_destruir = true` y se importa igual.

## Consecuencias

- HA-SEG-002 (KAN-64) queda «En curso» hasta la ventana. Con este ADR, el riesgo está escrito, mitigado (cifrado
  por campo con KMS) y con un plan probado en papel.
- Cualquier cambio que fuerce otra recreación de la instancia (región, versión mayor de Postgres) debe aprovechar
  la misma ventana.
