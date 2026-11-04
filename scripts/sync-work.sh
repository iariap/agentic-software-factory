#!/usr/bin/env bash
# Clona o actualiza el checkout editable de una app del registry en work/.
#
# Uso: scripts/sync-work.sh <app> [branch]
#   branch por defecto: el del registry (o main)
#
# Si el checkout declara .gitmodules, inicializa los submódulos
# (`git submodule update --init`): sin ellos la matriz local de la app falla por
# entorno ausente y ese rojo se confunde con una regresión del PR.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="${1:?uso: $0 <app> [branch] — apps: $(python3 "$ROOT/scripts/app_config.py" --list | tr '\n' ' ')}"
eval "$(python3 "$ROOT/scripts/app_config.py" "$APP_ID" --sh)"
BRANCH="${2:-$APP_BRANCH}"

if [ ! -d "$APP_CHECKOUT/.git" ]; then
  mkdir -p "$(dirname "$APP_CHECKOUT")"
  git clone --branch "$BRANCH" "$APP_REMOTE" "$APP_CHECKOUT"
else
  git -C "$APP_CHECKOUT" fetch --all --prune
fi

git -C "$APP_CHECKOUT" checkout "$BRANCH" >/dev/null 2>&1 \
  || git -C "$APP_CHECKOUT" checkout -B "$BRANCH" "origin/$BRANCH"
git -C "$APP_CHECKOUT" pull --ff-only

# Submódulos declarados en .gitmodules (p. ej. lvgl en el X6200): sin ellos la
# matriz local falla por ENTORNO ausente ("/src/lvgl does not contain a
# CMakeLists.txt") y el rojo se lee como regresión del PR. Genérico: un repo sin
# .gitmodules no hace nada. Sin --recursive: alcanza para el árbol de trabajo y
# los submódulos anidados los resuelve quien los necesite.
if [ -f "$APP_CHECKOUT/.gitmodules" ]; then
  echo "==> inicializando submódulos (git submodule update --init)" >&2
  if ! git -C "$APP_CHECKOUT" submodule update --init; then
    echo "error: no se pudieron inicializar los submódulos de $APP_CHECKOUT" >&2
    echo "      reintentá a mano: git -C $APP_CHECKOUT submodule update --init" >&2
    exit 1
  fi
  echo "==> submódulos inicializados" >&2
fi

echo "==> work/$APP_ID actualizado en $(git -C "$APP_CHECKOUT" log -1 --format='%h %s')"
