# Siembra de siniestros en staging

Carga 20 siniestros de demostración, con sus pólizas y evidencias, para probar la mesa de siniestros en la web.

| Dato | Valor |
|---|---|
| Proyecto GCP | `solventa-511815` |
| Región | `us-central1` |
| Repositorio | `solventa-app/solventa-backend`, rama `develop` |
| Web de staging | https://solventa-frontend-pink.vercel.app |
| Resultado | 20 siniestros: 7 pendientes, 6 en revisión, 4 aprobados y 3 rechazados |
| Tiempo estimado | 30 minutos |

## Para la sesión de Claude que ejecute esto

- **Los comandos no se han ejecutado en GCP.** Se escribieron a partir del código y de Terraform. Verifica la salida de cada
  paso antes de seguir con el siguiente.
- Pide confirmación a la persona antes de crear recursos en GCP (pasos 2 a 4) y antes de borrarlos (paso 6).
- No apliques Terraform y no cambies código del repositorio: esta tarea no lo necesita.
- No modifiques ni ejecutes los jobs `migrar-*`: solo se copian.
- No borres datos a mano en MongoDB ni en Cloud SQL.
- No imprimas valores de secretos ni los copies a archivos.
- Si un paso falla, consulta «Si algo falla». Si la causa no aparece ahí, detente y repórtalo.

## Contexto

- Los servicios son privados y piden su token al servidor de metadatos de GCP. Por eso la semilla no corre desde un portátil y
  se ejecuta como Cloud Run Job.
- Los jobs `migrar-policy` y `migrar-claims` ya tienen la cuenta de servicio, los secretos y la conexión a Cloud SQL. Se copian
  para no repetir esa configuración.
- La imagen de `claims` no incluye `services/claims/semilla/`. Esos archivos se montan desde un bucket temporal.
- Son 20 casos, no 30. Llegar a 30 exige cambiar el código de la semilla y publicar una imagen nueva.

## Requisitos

- Permisos en el proyecto: Cloud Run Admin, Storage Admin, Logs Viewer y Service Account User sobre `rt-policy` y `rt-claims`.
- `gcloud` con sesión iniciada y el repositorio `solventa-backend` actualizado en `develop`.
- Cloud SQL encendida. En GitHub, abre Actions > Costos staging > Run workflow > `estado`. Si está apagada, ejecuta `encender` y
  espera a que termine.
- Un usuario Operador (BACKOFFICE) de staging, para comprobar el resultado en la web.

## Pasos

### 1. Prepara las variables

Ejecuta todo en la misma terminal, desde la raíz de `solventa-backend`.

```bash
export PROYECTO=solventa-511815
export REGION=us-central1
export BUCKET=${PROYECTO}-semilla-temp
gcloud config set project $PROYECTO

gcloud run jobs list --region $REGION --format='value(metadata.name)' | grep -E 'migrar-(policy|claims)'

export IMAGEN_POLICY=$(gcloud run services describe policy --region $REGION --format='value(spec.template.spec.containers[0].image)')
export IMAGEN_CLAIMS=$(gcloud run services describe claims --region $REGION --format='value(spec.template.spec.containers[0].image)')
export URL_POLICY=$(gcloud run services describe policy --region $REGION --format='value(status.url)')
export URL_ALMACEN=$(gcloud run services describe almacenamiento --region $REGION --format='value(status.url)')

echo "$IMAGEN_POLICY"; echo "$IMAGEN_CLAIMS"; echo "$URL_POLICY"; echo "$URL_ALMACEN"
```

**Debes ver:** los dos jobs `migrar-*` y cuatro valores no vacíos.

### 2. Sube los datos de la semilla a un bucket temporal

```bash
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access
gcloud storage cp -r services/claims/semilla/* gs://$BUCKET/
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member="serviceAccount:rt-claims@${PROYECTO}.iam.gserviceaccount.com" \
  --role=roles/storage.objectViewer

gcloud storage ls gs://$BUCKET/
```

**Debes ver:** `siniestros.json`, `peritos.json` y la carpeta `medios/` en la raíz del bucket.

### 3. Crea las 20 pólizas de demostración

El job nuevo copia de `migrar-policy` la cuenta, los secretos y la conexión a Cloud SQL.

```bash
gcloud run jobs describe migrar-policy --region $REGION --format export \
  | sed 's/migrar-policy/sembrar-polizas-demo/g' > /tmp/sembrar-polizas-demo.yaml
gcloud run jobs replace /tmp/sembrar-polizas-demo.yaml --region $REGION

gcloud run jobs update sembrar-polizas-demo --region $REGION \
  --image "$IMAGEN_POLICY" --command python --args=-m,app.sembrar_demo

gcloud run jobs execute sembrar-polizas-demo --region $REGION --wait

gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="sembrar-polizas-demo"' \
  --limit 20 --freshness 15m --format='value(textPayload)'
```

**Debes ver:** `Pólizas de demostración listas: 20`. Si indica 10, la imagen es antigua: detente y avisa.

### 4. Crea los siniestros

Tarda unos minutos porque sube cada evidencia al almacenamiento.

```bash
gcloud run jobs describe migrar-claims --region $REGION --format export \
  | sed 's/migrar-claims/sembrar-siniestros/g' > /tmp/sembrar-siniestros.yaml
gcloud run jobs replace /tmp/sembrar-siniestros.yaml --region $REGION

gcloud run jobs update sembrar-siniestros --region $REGION \
  --image "$IMAGEN_CLAIMS" --command python --args=-m,app.sembrar \
  --memory 1Gi --task-timeout 1800 \
  --update-env-vars "SEMILLA_DIR=/semilla,POLICY_URL=$URL_POLICY,ALMACENAMIENTO_URL=$URL_ALMACEN" \
  --add-volume name=semilla,type=cloud-storage,bucket=$BUCKET,readonly=true \
  --add-volume-mount volume=semilla,mount-path=/semilla

gcloud run jobs execute sembrar-siniestros --region $REGION --wait

gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="sembrar-siniestros"' \
  --limit 30 --freshness 30m --format='value(textPayload)'
```

**Debes ver:** `Siniestros de demostración listos: 20`.

### 5. Comprueba el resultado en la web

Esta revisión la hace la persona en el navegador.

1. Entra a https://solventa-frontend-pink.vercel.app como Operador y abre Siniestros.
2. La bandeja debe mostrar **Página 1 de 2**. Con *Siguiente* pasa a la página 2.
3. Debe haber 7 pendientes, 6 en revisión, 4 aprobados y 3 rechazados.
4. Abre un caso **En revisión**: debe tener perito asignado y el botón Derivar bloqueado.
5. Abre un caso con fotos: las imágenes deben cargar.

### 6. Limpia lo temporal

Hazlo solo después de comprobar la web. Los datos sembrados se quedan; esto solo borra los jobs y el bucket.

```bash
gcloud run jobs delete sembrar-polizas-demo --region $REGION --quiet
gcloud run jobs delete sembrar-siniestros --region $REGION --quiet
gcloud storage rm -r gs://$BUCKET
rm -f /tmp/sembrar-polizas-demo.yaml /tmp/sembrar-siniestros.yaml
```

## Después de sembrar

- **Los pendientes no duran.** Sus plazos vencen en un máximo de 3 días. Si el Scheduler `aprobar-siniestros-vencidos` está
  activo, los aprobará solos y creará sus órdenes en PAYMENTS, sin dispersar dinero.
- **Para una demo:** vuelve a ejecutar los pasos 2, 4 y 6 el día anterior. Eso renueva los plazos y devuelve los casos a su
  estado inicial. Las variables del paso 1 deben estar definidas.
- **Repetir es seguro, pero reinicia:** los ids `sin-01` a `sin-20` son fijos. Lo que alguien haya gestionado en esos casos se
  pierde. Otros casos no cambian.
- **Terraform:** los jobs y el bucket quedan fuera de su estado. Por eso hay que borrarlos en el paso 6.
- **Datos:** los nombres y documentos son ficticios, y la semilla crea clientes opacos `demo-siniestros-cli-NN`.

## Si algo falla

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| Paso 3: no conecta a la base | Cloud SQL está detenida | Ejecuta Costos staging > `encender` y repite el paso |
| `No hay datos de semilla en /semilla` | Archivos en una subcarpeta o volumen sin montar | Revisa que `siniestros.json` esté en la raíz del bucket y el permiso de `rt-claims` |
| `PROPUESTA_SIN_POLIZA` o 404 en `/propuestas` | El paso 3 no terminó o usó una imagen antigua | Repite el paso 3 y confirma que indique 20 |
| 401 o 403 hacia POLICY o almacenamiento | Falta la variable o el permiso de invocación | Confirma que `AUTENTICACION_SERVICIOS=id_token` venga de `migrar-claims` y que `rt-claims` tenga `roles/run.invoker` |
| `DuplicateKeyError` por `radicado` | Ya existe un caso con ese radicado | Detente y avisa. No borres datos a mano |
| `gcloud` no reconoce una opción | Versión antigua de la CLI | Ejecuta `gcloud components update` y repite |
| El job falló a mitad de la carga | Corte temporal de algún servicio | Repite `gcloud run jobs execute`: no duplica registros ni evidencias |

## Referencias en el repositorio

- `services/claims/app/sembrar.py`
- `services/claims/app/semilla_aleatoria.py`
- `services/policy/app/sembrar_demo.py`
- `infra/servicios.tf`
