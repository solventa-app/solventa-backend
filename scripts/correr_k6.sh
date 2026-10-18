#!/usr/bin/env bash
# Corre los guiones de k6 contra staging (o contra el gateway local :8080 por defecto) y guarda los
# resúmenes crudos en tests/k6/resultados-crudos/ y un resumen Markdown en docs/evidencias/.
#
# Uso:
#   STAGING_URL=https://<gateway-url> ./scripts/correr_k6.sh [guion.js ...]
#   MODO_REDUCIDO=true STAGING_URL=http://localhost:8080 ./scripts/correr_k6.sh
#
# Si k6 no está instalado, usa la imagen oficial con Docker (`grafana/k6`), con la red del host.
# Variables que se reenvían a los guiones si están definidas (ver tests/k6/README.md):
#   duraciones   DURACION_NORMAL DURACION_PICO DURACION_FASE DURACION_CORTE
#   servicios    STUB_OPEN_FINANCE_URL ACL_URL RISK_URL RATING_URL (y ACL_TOKEN RISK_TOKEN RATING_TOKEN)
#   sesión       AUTH_TOKEN CONSENTIMIENTO_ID USUARIO_BACKOFFICE CLAVE_BACKOFFICE DOCUMENTO_PRUEBA
#   control      OMITIR_CONTROL_STUB
# El resumen dice SIEMPRE contra qué URL y con qué duraciones corrió: una corrida local no es evidencia
# de staging.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

STAGING_URL="${STAGING_URL:-${GATEWAY_URL:-${BFF_URL:-http://localhost:8080}}}"
MODO_REDUCIDO="${MODO_REDUCIDO:-true}"
FECHA="${FECHA:-$(date -u +%Y-%m-%d)}"
CRUDOS_DIR="tests/k6/resultados-crudos"
EVIDENCIAS_DIR="docs/evidencias"

mkdir -p "$CRUDOS_DIR" "$EVIDENCIAS_DIR"

if command -v k6 >/dev/null 2>&1; then
  k6_cmd=(k6)
elif command -v docker >/dev/null 2>&1; then
  k6_cmd=(docker run --rm -i --network host --user "$(id -u):$(id -g)" -v "$RAIZ":/work -w /work grafana/k6:latest)
else
  echo "ERROR: ni k6 ni Docker están disponibles (https://grafana.com/docs/k6/latest/set-up/install-k6/)." >&2
  exit 1
fi

# Variables opcionales que se reenvían tal cual a k6.
reenviar=()
for nombre in DURACION_NORMAL DURACION_PICO DURACION_FASE DURACION_CORTE STUB_OPEN_FINANCE_URL \
  ACL_URL RISK_URL RATING_URL ACL_TOKEN RISK_TOKEN RATING_TOKEN AUTH_TOKEN CONSENTIMIENTO_ID \
  USUARIO_BACKOFFICE CLAVE_BACKOFFICE DOCUMENTO_PRUEBA OMITIR_CONTROL_STUB; do
  if [ -n "${!nombre:-}" ]; then
    reenviar+=(-e "${nombre}=${!nombre}")
  fi
done

if [ "$#" -gt 0 ]; then
  GUIONES=("$@")
else
  GUIONES=(
    "tests/k6/perfilamiento-baseline.js"
    "tests/k6/falla-proveedor.js"
    "tests/k6/lectura-replica.js"
    "tests/k6/gateway-presupuesto.js"
  )
fi

RESUMEN_MD="${EVIDENCIAS_DIR}/resumen-k6-${FECHA}.md"
{
  echo "# Resumen de ejecución k6 (${FECHA})"
  echo ""
  echo "- **URL objetivo (\`STAGING_URL\`):** \`${STAGING_URL}\`"
  echo "- **Modo reducido (\`MODO_REDUCIDO\`):** \`${MODO_REDUCIDO}\`"
  echo "- **Variables reenviadas:** \`${reenviar[*]:-ninguna}\`"
  echo ""
  echo "| Guion | JSON crudo | Resultado |"
  echo "|---|---|---|"
} > "$RESUMEN_MD"

for guion in "${GUIONES[@]}"; do
  nombre="$(basename "$guion" .js)"
  json_salida="${CRUDOS_DIR}/${nombre}-${FECHA}.json"
  echo "==> Corriendo ${guion} contra ${STAGING_URL} (MODO_REDUCIDO=${MODO_REDUCIDO})..."
  if "${k6_cmd[@]}" run \
    -e STAGING_URL="$STAGING_URL" \
    -e GATEWAY_URL="$STAGING_URL" \
    -e MODO_REDUCIDO="$MODO_REDUCIDO" \
    -e RESUMEN_JSON="$json_salida" \
    "${reenviar[@]}" \
    "$guion"; then
    estado="✅ Cumple umbrales"
  else
    estado="❌ Umbral incumplido (o la corrida se abortó: ver la salida)"
  fi
  echo "| \`${guion}\` | \`${json_salida}\` | ${estado} |" >> "$RESUMEN_MD"
done

echo "Resumen guardado en ${RESUMEN_MD} y crudos en ${CRUDOS_DIR}/"
