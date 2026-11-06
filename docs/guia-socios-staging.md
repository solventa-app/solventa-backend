# Socios de distribución en staging

Deja listo el portal de socios en staging: gateway propio, un socio de prueba y la web apuntando a ese gateway.

| Dato | Valor |
|---|---|
| Proyecto GCP | `solventa-511815` |
| Región | `us-central1` |
| Repositorios | `solventa-app/solventa-backend` y `solventa-app/solventa-frontend-web` |
| Web de staging | https://solventa-frontend-pink.vercel.app/socios/ingreso |
| Gateway de usuarios | `solventa-gateway-btxoyawl.uc.gateway.dev` (no cambia) |
| Gateway de socios | `solventa-gateway-socios` (se crea en esta guía) |
| Resultado | Un socio entra al portal, ve sus datos y prueba la API en el Swagger |

**Crear el socio no basta.** Hoy el portal no funciona en staging por tres motivos: el gateway de socios está apagado, el
gateway actual no tiene las rutas de socios, y Vercel envía todo `/v1/*` a ese gateway actual.

Hay que hacer dos liberaciones del backend y una de la web, y cada una necesita la aprobación de otra persona.

## Para la sesión de Claude que ejecute esto

- **Los comandos no se han ejecutado en GCP.** Se escribieron a partir del código y de Terraform. Verifica la salida de cada
  paso antes de seguir.
- Pide confirmación a la persona antes de cada PR hacia `main` y antes de crear recursos en GCP.
- **Nunca muestres, copies ni registres las claves.** No ejecutes `gcloud secrets versions access`: ese comando lo corre la
  persona en su propia terminal, solo cuando esta guía lo indica.
- No apliques Terraform a mano: se aplica desde el pipeline al llegar a `main`.
- No modifiques ni ejecutes el job `migrar-auth`: solo se copia.
- No aprueba ni integra sus propios PR: necesitan la revisión de otra persona.
- Usa títulos `tipo(alcance): KAN-30 descripción`, sin coautoría de Claude.
- Si un paso falla, consulta «Si algo falla». Si la causa no aparece ahí, detente y repórtalo.

## Requisitos

- Permisos en GCP: Cloud Run Admin, Secret Manager Admin, Logs Viewer, API Gateway Viewer y Service Account User sobre `rt-auth`.
- `gcloud`, `gh`, `jq` y `openssl` instalados, con sesión iniciada.
- Los repositorios `solventa-backend` y `solventa-frontend-web` actualizados en `develop`.
- Cloud SQL encendida. En GitHub, abre Actions > Costos staging > Run workflow > `estado`. Si está apagada, ejecuta `encender`.
- Otra persona disponible para aprobar los PR.

El gateway es gratis hasta 2 millones de llamadas al mes, según `infra/gateway.tf`.

## Pasos

### 1. Prepara las variables y revisa la clave de firma

```bash
export PROYECTO=solventa-511815
export REGION=us-central1
export SOCIO_ID=socio-staging-1
export SOCIO_NOMBRE="Socio Staging"
gcloud config set project $PROYECTO

gcloud secrets versions list clave-firma-socios --filter='state=enabled' --format='table(name,createTime)'
```

**Debes ver:** al menos dos versiones: el marcador de Terraform y la clave real. AUTH no arranca sin la clave real, así que
debería estar. Si solo hay una, detente y avisa: cargarla cambia la firma de los tokens.

### 2. Primera liberación: crea el gateway

En `solventa-backend`, crea una rama desde `main`:

```bash
git fetch origin
git switch -c hotfix/KAN-30-gateway-socios origin/main
```

Agrega esta línea en `infra/staging.tfvars`, debajo de `crear_gateway = true`:

```hcl
crear_gateway_socios    = true
```

```bash
git add infra/staging.tfvars
git commit -m "chore(infra): KAN-30 enciende el gateway de socios en staging"
git push -u origin hotfix/KAN-30-gateway-socios
gh pr create --base main --title "chore(infra): KAN-30 enciende el gateway de socios en staging" \
  --body "Paso 2 de docs/guia-socios-staging.md."
```

Cuando se apruebe e integre, espera a que termine **Deploy staging** y revisa su resumen.

**Debes ver:** el plan crea el API, su configuración y el gateway, sin destruir nada.

**Riesgo conocido:** en esta primera vez el JWKS va vacío. Si GCP rechaza la configuración por `x-google-jwks_uri`, detente y
avisa: hay que acordar una URL temporal para `jwks_socios_url`.

### 3. Segunda liberación: conecta el JWKS

```bash
export HOST_SOCIOS=$(gcloud api-gateway gateways describe solventa-gateway-socios \
  --location=$REGION --format='value(defaultHostname)')
echo "$HOST_SOCIOS"
curl -s -o /dev/null -w '%{http_code}\n' "https://$HOST_SOCIOS/v1/.well-known/jwks.json"
```

**Debes ver:** el dominio del gateway y `200`.

Crea otra rama desde `main` actualizada:

```bash
git fetch origin
git switch -c hotfix/KAN-30-jwks-socios origin/main
```

Agrega en `infra/staging.tfvars`, debajo de la línea anterior, con el dominio real:

```hcl
jwks_socios_url         = "https://<HOST_SOCIOS>/v1/.well-known/jwks.json"
```

Abre el PR igual que en el paso 2, con el título `chore(infra): KAN-30 conecta el JWKS del gateway de socios`.

**Debes ver:** el plan reemplaza `google_api_gateway_api_config`. Es esperado y el guardia lo permite. Si propone destruir
otra cosa, detente y avisa.

### 4. Crea las credenciales del socio

Las claves van a Secret Manager para que no queden en Cloud Logging. `crear_socio` imprime las claves que genera, y en un
Cloud Run Job esa salida se guarda en los registros. Si se le entregan fijas, no las imprime.

```bash
for ENTORNO in produccion pruebas; do
  openssl rand -hex 24 | tr -d '\n' \
    | gcloud secrets create ${SOCIO_ID}-clave-${ENTORNO} --data-file=- --replication-policy=automatic
  gcloud secrets add-iam-policy-binding ${SOCIO_ID}-clave-${ENTORNO} \
    --member="serviceAccount:rt-auth@${PROYECTO}.iam.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
```

### 5. Da de alta el socio

El job nuevo copia de `migrar-auth` la cuenta, los secretos y la conexión a Cloud SQL.

**No omitas `KMS_KEY_NAME`.** `migrar-auth` no la trae. Sin ella, la clave se cifra con otra llave, el alta termina bien y
después el socio no puede entrar.

```bash
export IMAGEN_AUTH=$(gcloud run services describe auth --region $REGION \
  --format='value(spec.template.spec.containers[0].image)')
export KMS_KEY_NAME=$(gcloud run services describe auth --region $REGION --format=json \
  | jq -r '.spec.template.spec.containers[0].env[] | select(.name=="KMS_KEY_NAME") | .value')
test -n "$KMS_KEY_NAME" && echo "KMS encontrada" || echo "FALTA KMS: detente"

gcloud run jobs describe migrar-auth --region $REGION --format export \
  | sed 's/migrar-auth/crear-socio/g' > /tmp/crear-socio.yaml
gcloud run jobs replace /tmp/crear-socio.yaml --region $REGION

gcloud run jobs update crear-socio --region $REGION \
  --image "$IMAGEN_AUTH" --command python \
  --args="-m,app.crear_socio,--id,${SOCIO_ID},--nombre,${SOCIO_NOMBRE},--tasa-comision,0.10,--presupuesto-cotizaciones,1000,--presupuesto-cotizaciones-pruebas,5000" \
  --update-env-vars "KMS_KEY_NAME=${KMS_KEY_NAME},SOCIO_CLIENT_ID_PRODUCCION=sol_prod_staging1,SOCIO_CLIENT_ID_PRUEBAS=sol_test_staging1" \
  --update-secrets "SOCIO_CLAVE_PRODUCCION=${SOCIO_ID}-clave-produccion:latest,SOCIO_CLAVE_PRUEBAS=${SOCIO_ID}-clave-pruebas:latest"

gcloud run jobs execute crear-socio --region $REGION --wait

gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="crear-socio"' \
  --limit 20 --freshness 15m --format='value(textPayload)'
```

**Debes ver:** `socio listo id=socio-staging-1 credenciales nuevas=2`.

Repetir el job es seguro: actualiza el socio y fija su presupuesto, pero no duplica credenciales.

### 6. Comprueba la API directamente

Este bloque lo corre **la persona en su propia terminal**, porque usa la clave real. La variable no se imprime.

```bash
CLAVE=$(gcloud secrets versions access latest --secret=${SOCIO_ID}-clave-produccion)
TOKEN=$(curl -s -X POST "https://$HOST_SOCIOS/v1/token" -H 'Content-Type: application/json' \
  -d "{\"clientId\":\"sol_prod_staging1\",\"clave\":\"$CLAVE\"}" | jq -r .accessToken)
unset CLAVE

curl -s -o /dev/null -w 'sin token: %{http_code}\n' "https://$HOST_SOCIOS/v1/consumo"
curl -s -o /dev/null -w 'con token: %{http_code}\n' -H "Authorization: Bearer $TOKEN" "https://$HOST_SOCIOS/v1/consumo"
unset TOKEN
```

**Debes ver:** `sin token: 401` y `con token: 200`.

### 7. Liberación de la web: apunta Vercel al gateway de socios

En `solventa-frontend-web`:

```bash
git fetch origin
git switch -c hotfix/KAN-30-rutas-socios-vercel origin/main
```

En `apps/web/vercel.json`, reemplaza la regla de `/v1/:path*` por estas dos, **en este orden** y con el dominio real:

```json
{ "source": "/v1/graphql/:path*", "destination": "https://solventa-gateway-btxoyawl.uc.gateway.dev/v1/graphql/:path*" },
{ "source": "/v1/:path*", "destination": "https://<HOST_SOCIOS>/v1/:path*" },
```

**El orden importa.** Si `/v1/:path*` va primero o reemplaza a la otra, la mesa de siniestros deja de funcionar, porque usa
`/v1/graphql/web`. Las reglas de PDF y `/(.*)` no cambian.

```bash
cd apps/web && npm ci && npm run formato:verificar && cd ../..
git add apps/web/vercel.json
git commit -m "fix(web): KAN-30 las rutas de socios van a su gateway"
git push -u origin hotfix/KAN-30-rutas-socios-vercel
gh pr create --base main --title "fix(web): KAN-30 las rutas de socios van a su gateway" \
  --body "Paso 7 de docs/guia-socios-staging.md del backend."
```

Espera a que termine **Deploy web**.

### 8. Comprueba el portal

La persona lo hace en el navegador. Primero obtiene la clave en su propia terminal:

```bash
gcloud secrets versions access latest --secret=${SOCIO_ID}-clave-produccion; echo
```

1. Entra a https://solventa-frontend-pink.vercel.app/socios/ingreso con el ID `sol_prod_staging1` y esa clave.
2. El panel debe mostrar credenciales, consumo, estado y ventas, sin errores.
3. Abre **API Pruebas**: debe decir «Autenticado con la clave de Pruebas (sandbox)».
4. En el Swagger, usa **Try it out** en `/v1/consumo`: debe responder 200.
5. **Comprueba que no se rompió nada:** entra como Operador y abre Siniestros.

### 9. Reintegra y limpia

Lleva los cambios a `develop` en ambos repositorios con un PR de `main` hacia `develop`, y borra lo temporal:

```bash
gh pr create --repo solventa-app/solventa-backend --base develop --head main \
  --title "chore(infra): KAN-30 reintegra main en develop tras el gateway de socios" --body "Paso 9 de la guía."
gh pr create --repo solventa-app/solventa-frontend-web --base develop --head main \
  --title "chore(web): KAN-30 reintegra main en develop tras las rutas de socios" --body "Paso 9 de la guía."

gcloud run jobs delete crear-socio --region $REGION --quiet
rm -f /tmp/crear-socio.yaml

for ENTORNO in produccion pruebas; do
  gcloud secrets remove-iam-policy-binding ${SOCIO_ID}-clave-${ENTORNO} \
    --member="serviceAccount:rt-auth@${PROYECTO}.iam.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
```

Los dos secretos del socio se conservan: guardan sus claves. Si el socio rota una clave desde el portal, el secreto deja de
ser la clave vigente.

## Otro socio

Repite los pasos 1, 4, 5, 6 y 9 con otro `SOCIO_ID` y otros `SOCIO_CLIENT_ID_*`. Las liberaciones no se repiten.

## Si algo falla

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| Deploy staging se detiene en el guardia | El plan destruye algo | Detente y muestra el resumen del plan. No uses `permitir_destruir` sin aprobación |
| Error de configuración por `x-google-jwks_uri` | GCP rechazó el JWKS vacío | Detente y acuerda una URL temporal |
| `401` al pedir `/v1/token` con la clave correcta | Falta `KMS_KEY_NAME` en el job | Revisa el paso 5. Pide ayuda antes de repetir: la credencial ya existe |
| `401` con token en `/v1/consumo` | Falta la segunda liberación o el JWKS está mal | Revisa `jwks_socios_url` y su `200` |
| `socio listo` con `credenciales nuevas=0` | La credencial ya existía | No cambia la clave. Rótala desde el portal si hace falta |
| El job no conecta a la base | Cloud SQL está detenida | Ejecuta Costos staging > `encender` |
| El portal responde 404 | Vercel sigue en el gateway anterior | Revisa el paso 7 y que Deploy web haya terminado |
| Siniestros dejó de cargar | Orden incorrecto en `vercel.json` | `/v1/graphql/:path*` debe ir antes que `/v1/:path*` |
| `gcloud` no reconoce una opción | Versión antigua de la CLI | Ejecuta `gcloud components update` y repite |

## Referencias en el repositorio

- `infra/README.md`, sección «API Gateway de los socios»
- `infra/gateway.tf` e `infra/gateway/openapi-socios.yaml`
- `services/auth/app/crear_socio.py`
- `docs/plan-completar-hu-w07.md`
- `scripts/revisar_plan.py`
