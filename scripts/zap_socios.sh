#!/usr/bin/env bash
# OWASP ZAP sobre la API de socios /v1 (KAN-223). Pide el token de un socio de PRUEBAS y corre el
# plan `tests/zap/socios-v1.yaml` con ZAP instalado (ZAP_HOME) o con la imagen oficial de Docker.
#
#   OBJETIVO=http://localhost:8000 SOCIO_CLIENT_ID=... SOCIO_CLAVE=... scripts/zap_socios.sh
#
# Deja el informe en tests/zap/resultados/ (ignorado por git). Sale con error solo si ZAP falla, no
# por las alertas: el resumen de alertas se revisa a mano y se anota en docs/evidencias/.
set -euo pipefail

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
OBJETIVO="${OBJETIVO:-http://localhost:8000}"
OBJETIVO="${OBJETIVO%/}"
SALIDA="${SALIDA:-$RAIZ/tests/zap/resultados}"
: "${SOCIO_CLIENT_ID:?falta SOCIO_CLIENT_ID (credencial de PRUEBAS)}"
: "${SOCIO_CLAVE:?falta SOCIO_CLAVE}"
mkdir -p "$SALIDA"

TOKEN="$(curl -fsS -X POST "$OBJETIVO/v1/token" -H 'Content-Type: application/json' \
  -d "{\"clientId\":\"$SOCIO_CLIENT_ID\",\"clave\":\"$SOCIO_CLAVE\"}" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["accessToken"])')"
export ZAP_AUTH_HEADER=Authorization
export ZAP_AUTH_HEADER_VALUE="Bearer $TOKEN"
export OBJETIVO SALIDA

if [[ -n "${ZAP_HOME:-}" ]]; then
  "$ZAP_HOME/zap.sh" -cmd -silent -port "${ZAP_PUERTO:-8095}" -autorun "$RAIZ/tests/zap/socios-v1.yaml"
else
  docker run --rm --network host -v "$RAIZ/tests/zap:/zap/wrk/plan:ro" -v "$SALIDA:/zap/wrk/salida:rw" \
    -e ZAP_AUTH_HEADER -e ZAP_AUTH_HEADER_VALUE -e OBJETIVO -e SALIDA=/zap/wrk/salida \
    ghcr.io/zaproxy/zaproxy:stable zap.sh -cmd -silent -autorun /zap/wrk/plan/socios-v1.yaml
fi
echo "Informe: $SALIDA/zap-socios-v1.html"
