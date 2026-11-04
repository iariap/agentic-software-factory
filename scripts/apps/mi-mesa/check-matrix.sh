#!/usr/bin/env bash
# Matriz de checks de mi-mesa según paths modificados en el checkout de la app.
# Fuerza los comandos obligatorios por tipo de cambio (catálogo, backend,
# API, frontend, deploy, workflows). Cada check informa su exit code.
#
# Uso: scripts/apps/mi-mesa/check-matrix.sh <base> <head> [checkout]
#   base/head: refs comparadas (ej: main, origin/main, <sha>).
#   checkout: worktree donde correr los checks (default: APP_CHECKOUT).
#   El checkout base se resuelve desde config/apps/mi-mesa.json.
#   Requiere backend/.venv y frontend con bun instalados.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
# shellcheck source=../../lib/check_matrix.sh
source "$ROOT/scripts/lib/check_matrix.sh"
eval "$(python3 "$ROOT/scripts/app_config.py" mi-mesa --sh)"
BASE="${1:?uso: $0 <base> <head> [checkout]}"
HEAD="${2:?uso: $0 <base> <head> [checkout]}"
APP="${3:-$APP_CHECKOUT}"

CHANGED="$(git -C "$APP" diff --name-only "$BASE" "$HEAD" || true)"
if [ -z "$CHANGED" ]; then
  echo "==> sin cambios entre $BASE y $HEAD"
  exit 0
fi

FAILED=0
run() {
  local label="$1"; shift
  echo "==> [check] $label"
  if (cd "$APP" && "$@") >/tmp/check-matrix.log 2>&1; then
    echo "    ok"
  else
    echo "    FAIL:"; sed 's/^/    /' /tmp/check-matrix.log | tail -20
    FAILED=1
  fi
}

# Catálogo: normalizar + validación de catálogo (invariantes doshas/nutrientes).
if has '^backend/src/omavu/data/'; then
  run "normalize_catalog" .venv/bin/python backend/scripts/normalize_catalog.py
  run "frontend check:catalog" bun run --cwd frontend check:catalog
else
  skip "catálogo (normalize + check:catalog)" "sin cambios en backend/src/omavu/data/"
fi

# Backend python.
if has '\.py$'; then
  run "pytest" .venv/bin/pytest backend/tests -q
  run "ruff check" .venv/bin/ruff check backend/src backend/tests
  run "ruff format" .venv/bin/ruff format --check backend/src backend/tests
  run "ty check" .venv/bin/ty check src
else
  skip "backend python (pytest + ruff + ty)" "sin cambios en archivos .py"
fi

# Contrato de API: regenerar tipos y verificar que no diverge.
if has '^backend/src/omavu/api.py'; then
  run "api:types" bun run --cwd frontend api:types
  if (cd "$APP" && git diff --exit-code -- frontend/lib/api-schema.d.ts) >/tmp/check-matrix.log 2>&1; then
    echo "==> [check] api-schema.d.ts sin diff: ok"
  else
    echo "==> [check] api-schema.d.ts CON diff (types no regeneradas)"; FAILED=1
  fi
else
  skip "contrato de API (api:types + schema)" "sin cambios en backend/src/omavu/api.py"
fi

# Frontend.
if has '^frontend/'; then
  run "lint" bun run --cwd frontend lint
  run "typecheck" bun run --cwd frontend typecheck
  run "build" bun run --cwd frontend build
  run "check:pwa" bun run --cwd frontend check:pwa
else
  skip "frontend (lint + typecheck + build + check:pwa)" "sin cambios en frontend/"
fi

# Deploy: tests shell.
if has '^deploy/'; then
  deploy_tests=0
  for t in "$APP"/deploy/tests/*.sh; do
    [ -e "$t" ] || continue
    deploy_tests=1
    run "deploy test: $(basename "$t")" bash "$t"
  done
  if [ "$deploy_tests" = 0 ]; then
    skip "deploy tests" "no hay deploy/tests/*.sh en el checkout"
  fi
else
  skip "deploy tests" "sin cambios en deploy/"
fi

# Workflows: actionlint si está disponible.
if has '^\.github/workflows/'; then
  if command -v actionlint >/dev/null 2>&1; then
    run "actionlint" actionlint
  else
    skip "actionlint" "no instalado; validación de workflows manual (PENDIENTE)"
  fi
else
  skip "actionlint" "sin cambios en .github/workflows/"
fi

exit "$FAILED"