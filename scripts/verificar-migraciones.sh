#!/usr/bin/env bash
# Verifica las migraciones de un servicio contra el almacén REAL de docker compose, usando la imagen del
# servicio (la misma que se despliega). Es lo que corre el CI; también sirve en local:
#
#   scripts/verificar-migraciones.sh auth|payments|risk
#
# Postgres (Alembic): una sola cabeza, subir, bajar a base y volver a subir (reversibilidad).
# MongoDB (runner propio): aplicar dos veces (idempotencia) y comprobar el registro.
set -euo pipefail
cd "$(dirname "$0")/.."

servicio="${1:?uso: $0 auth|payments|risk}"
correr() { docker compose --profile servicios run --rm -T "migrar-$servicio" "$@"; }

case "$servicio" in
  auth | payments)
    salida="$(correr alembic heads)" # sin `|| true`: un fallo de Docker o de Alembic debe verse como tal
    cabezas="$(printf '%s\n' "$salida" | grep -c '(head)' || true)"
    if [ "$cabezas" != "1" ]; then
      echo "ERROR: $servicio tiene $cabezas cabezas de migración (debe ser 1). ¿Dos ramas crearon la misma revisión? Rebasea y renumera." >&2
      exit 1
    fi
    correr alembic upgrade head
    correr alembic downgrade base
    correr alembic upgrade head
    correr alembic current
    ;;
  risk)
    correr python -m migrations
    correr python -m migrations # idempotente: la segunda corrida no debe aplicar nada
    esperadas="$(find services/risk/migrations/versiones -name '[0-9][0-9][0-9][0-9]_*.py' | wc -l | tr -d ' ')"
    registradas="$(docker compose exec -T mongo1 mongosh --quiet --eval 'db.getSiblingDB("risk").getCollection("_migraciones").countDocuments({})' | tr -d '[:space:]')"
    if [ "$registradas" != "$esperadas" ]; then
      echo "ERROR: risk tiene $esperadas migraciones en el repo pero $registradas registradas en MongoDB." >&2
      exit 1
    fi
    echo "OK: $registradas migraciones registradas en MongoDB."
    ;;
  *)
    echo "Servicio sin migraciones: $servicio (usa auth, payments o risk)" >&2
    exit 2
    ;;
esac
echo "Migraciones de $servicio verificadas."
