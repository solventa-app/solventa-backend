#!/usr/bin/env bash
# Corre los guiones de k6 contra staging (o contra el gateway local :8080 por defecto) en modo reducido
# y guarda los resúmenes crudos en tests/k6/resultados-crudos/ y un resumen Markdown en docs/evidencias/.
#
# Uso:
#   STAGING_URL=https://<gateway-url> ./scripts/correr_k6.sh [guion.js ...]
#   MODO_REDUCIDO=true STAGING_URL=http://localhost:8080 ./scripts/correr_k6.sh

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

STAGING_URL="${STAGING_URL:-${GATEWAY_URL:-${BFF_URL:-http://localhost:8080}}}"
MODO_REDUCIDO="${MODO_REDUCIDO:-true}"
FECHA="${FECHA:-$(date -u +%Y-%m-%d)}"
CRUDOS_DIR="tests/k6/resultados-crudos"
EVIDENCIAS_DIR="docs/evidencias"

mkdir -p "$CRUDOS_DIR" "$EVIDENCIAS_DIR"

if ! command -v k6 >/dev/null 2>&1; then
  echo "ERROR: k6 no está instalado en el PATH (https://grafana.com/docs/k6/latest/set-up/install-k6/)." >&2
  exit 1
fi

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
  echo "- **URL objetivo (`STAGING_URL`):** \`${STAGING_URL}\`"
  echo "- **Modo reducido (`MODO_REDUCIDO`):** \`${MODO_REDUCIDO}\`"
  echo ""
  echo "| Guion | JSON crudo | Resultado |"
  echo "|---|---|---|"
} > "$RESUMEN_MD"

for guion in "${GUIONES[@]}"; do
  nombre="$(basename "$guion" .js)"
  json_salida="${CRUDOS_DIR}/${nombre}-${FECHA}.json"
  echo "==> Corriendo ${guion} contra ${STAGING_URL} (MODO_REDUCIDO=${MODO_REDUCIDO})..."
  if k6 run \
    -e STAGING_URL="$STAGING_URL" \
    -e GATEWAY_URL="$STAGING_URL" \
    -e MODO_REDUCIDO="$MODO_REDUCIDO" \
    -e RESUMEN_JSON="$json_salida" \
    "$guion"; then
    estado="✅ Cumple umbrales"
  else
    estado="❌ Umbral incumplido"
  fi
  echo "| \`${guion}\` | \`${json_salida}\` | ${estado} |" >> "$RESUMEN_MD"
done

echo "Resumen guardado en ${RESUMEN_MD} y crudos en ${CRUDOS_DIR}/"
