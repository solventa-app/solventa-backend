#!/usr/bin/env bash
# Regenera los requirements.txt (con hashes) a partir de los requirements.in.
#
#   scripts/compilar-requirements.sh              # todos
#   scripts/compilar-requirements.sh services/auth # uno (o varios) por directorio
#
# Por qué: el .in lleva solo las dependencias directas (es lo que se edita); el .txt las fija TODAS, transitivas
# incluidas, con el hash de cada archivo. Así un build instala siempre lo mismo y pip rechaza cualquier archivo
# distinto del esperado. Se compila en Linux con Python 3.14, la misma imagen base que los Dockerfiles, para que
# el resultado coincida con lo que se despliega (compilar en Windows/macOS cambiaría los marcadores de plataforma).
#
# Dependabot actualiza el .in y recompila el .txt solo; este script es para cambios manuales.
set -euo pipefail
cd "$(dirname "$0")/.."

# La imagen sale del Dockerfile de auth: una sola fuente, sin copiarla aquí.
imagen="$(grep -m1 '^FROM ' services/auth/Dockerfile | tr -d '\r' | cut -d' ' -f2)"

if [ "$#" -gt 0 ]; then
  directorios=("$@")
else
  directorios=(. services/* stubs/*)
fi

for d in "${directorios[@]}"; do
  d="${d%/}"
  entrada="requirements.in"
  [ "$d" = "." ] && entrada="requirements-dev.in"
  [ -f "$d/$entrada" ] || continue
  salida="${entrada%.in}.txt"
  echo ">> $d/$salida"
  # MSYS_NO_PATHCONV evita que Git Bash en Windows reescriba las rutas /repo.
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/repo" -w "/repo/$d" "$imagen" sh -c \
    "pip install --quiet --disable-pip-version-check --root-user-action=ignore pip-tools==7.6.2 && pip-compile --quiet --strip-extras --generate-hashes --output-file=$salida $entrada"
done
