#!/usr/bin/env bash
# Matriz de checks de FarmaRuta (PWA offline-first: Svelte 5 + Vite + TS + bun)
# según paths modificados en el checkout de la app. Fuerza los checks
# obligatorios por tipo de cambio: instalación reproducible con lockfile, gate
# estático (lint + typecheck + unit + build), e2e de Playwright, migraciones+seed
# de Supabase local y sintaxis de los workflows tocados.
# Cada check informa su exit code (salida estilo mi-mesa: "[check] label ... ok / FAIL:").
# Un check que no puede correr por falta de entorno se reporta como BLOCKED con
# el motivo (y el output real del preflight si lo hay) y NO marca FAILED (no
# cambia el exit de la matriz): hoy el browser de Playwright y el daemon de
# Docker que pide Supabase local, que pueden faltar en el runner. actionlint sin
# instalar se reporta PENDIENTE (validación manual de workflows), como mi-mesa.
#
# Uso: scripts/apps/farmaruta/check-matrix.sh <base> <head> [checkout]
#   base/head: refs LOCALES ya presentes en el checkout (ej: main, HEAD, un
#              <sha>). No se fetchea: un ref ausente es un FAIL explícito (exit
#              2), no un "sin cambios" verde.
#   checkout: worktree donde correr los checks (default: APP_CHECKOUT).
#   El checkout base se resuelve desde config/apps/farmaruta.json (o su overlay .factory/).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
# shellcheck source=../../lib/check_matrix.sh
source "$ROOT/scripts/lib/check_matrix.sh"
eval "$(python3 "$ROOT/scripts/app_config.py" farmaruta --sh)"
BASE="${1:?uso: $0 <base> <head> [checkout]}"
HEAD="${2:?uso: $0 <base> <head> [checkout]}"
APP="${3:-$APP_CHECKOUT}"

LOG=/tmp/check-matrix-farmaruta.log

# Un ref ausente en un clone fresco no puede degradar a "sin cambios": fail-closed.
for ref in "$BASE" "$HEAD"; do
  if ! git -C "$APP" rev-parse --verify --quiet "$ref^{commit}" >/dev/null; then
    echo "FAIL: ref inexistente en $APP: $ref" >&2
    echo "      (no se fetchea; traé el ref localmente o usá un sha/base presentes)" >&2
    exit 2
  fi
done

# Cambios introducidos por HEAD respecto del merge-base con BASE (estilo PR).
CHANGED="$(git -C "$APP" diff --name-only --merge-base "$BASE" "$HEAD")"
if [ -z "$CHANGED" ]; then
  echo "==> sin cambios entre $BASE y $HEAD"
  exit 0
fi

FAILED=0
# Ejecuta un comando dentro del checkout de la app, con tail del log si falla.
run() {
  local label="$1"; shift
  echo "==> [check] $label"
  if (cd "$APP" && "$@") >"$LOG" 2>&1; then
    echo "    ok"
  else
    echo "    FAIL:"; sed 's/^/    /' "$LOG" | tail -20
    FAILED=1
  fi
}

# Igual formato que run(), pero para un check que NO puede correr por falta de
# entorno (browser de Playwright, daemon de Docker). Muestra el motivo y el
# output real del preflight, y NO marca FAILED: no es un fallo del cambio y no
# tiene que romper la matriz ni el CI del runner.
blocked() {
  local label="$1"; shift
  echo "==> [check] $label"
  echo "    BLOCKED: $*"
  if [ -s "$LOG" ]; then
    sed 's/^/    /' "$LOG" | tail -30
  fi
}

# --- Instalación reproducible ------------------------------------------------
# Siempre (con cambios en el diff): el lockfile manda. Si `--frozen-lockfile`
# rompe, el checkout no refleja las deps declaradas y ningún otro check vale.
run "bun install --frozen-lockfile" bun install --frozen-lockfile

# --- Gate estático (src, tests, e2e, configs del build) ----------------------
# Todo lo que compila/empaqueta la PWA pasa por lint (eslint), typecheck
# (svelte-check), unit (vitest) y build (vite). Configs que cambian el resultado
# del build/typecheck también disparan el gate completo.
if has '^(src/|tests/|e2e/|public/|index\.html$|package\.json$|bun\.lockb?$|tsconfig|vite\.config|svelte\.config)'; then
  run "bun run lint" bun run lint
  run "bun run typecheck" bun run typecheck
  run "bun run test" bun run test
  run "bun run build" bun run build
else
  skip "gate estático (lint + typecheck + unit + build)" "sin cambios en src/, tests/, e2e/, configs ni lockfile"
fi

# --- E2E de Playwright --------------------------------------------------------
# El humo e2e corre el build de producción servido por `vite preview`
# (playwright.config.ts). Necesita el chromium de Playwright en el cache del
# runner: si falta, BLOCKED con la acción concreta (nada de skip silencioso).
if has '^(src/|e2e/|index\.html$|vite\.config|playwright\.config)'; then
  if ls "$HOME"/.cache/ms-playwright/chromium* >"$LOG" 2>&1; then
    run "bun run test:e2e (Playwright)" bun run test:e2e
  else
    blocked "bun run test:e2e (Playwright)" \
      "browser de Playwright no instalado; acción: bunx playwright install chromium"
  fi
else
  skip "bun run test:e2e (Playwright)" "sin cambios en src/, e2e/, index.html ni configs de build/Playwright"
fi

# --- Supabase local (start + migraciones/seed + pgTAP) ------------------------
# `supabase/` guarda migraciones/config del proyecto y su suite pgTAP: el gate
# levanta el stack local (start es idempotente; --ignore-health-check evita el
# false FAIL StatusDbNotReadyError mientras la DB arranca, y db reset + test db
# validan de verdad: si el stack no queda sano, el gate igual falla), rehace la
# base desde cero (db reset = migraciones + seed) y corre los asserts de dominio
# (test db). Supabase local levanta contenedores, así que sin daemon de Docker
# no puede correr: BLOCKED. Si el stack no estaba corriendo antes del gate, se
# apaga al final (stop best-effort: es limpieza, no un check, no marca FAILED).
# Concurrencia: TODOS los worktrees de farmaruta comparten project_id/puertos
# 544xx. Si otra sesión tiene el stack arriba, este gate le destruye la DB con
# db reset (was_running=1 evita el stop, no el reset) y puede colisionar
# puertos al arrancar: coordinar antes de correrlo.
if has '^supabase/'; then
  if (cd "$APP" && bunx supabase status) >/dev/null 2>&1; then
    was_running=1
  else
    was_running=0
  fi
  if docker info >"$LOG" 2>&1; then
    run "supabase start" bunx supabase start --ignore-health-check
    run "supabase db reset (migraciones+seed)" bunx supabase db reset
    run "supabase test db (pgTAP)" bunx supabase test db
    if [ "$was_running" = 0 ]; then
      echo "==> [check] supabase stop (limpieza)"
      if (cd "$APP" && bunx supabase stop) >"$LOG" 2>&1; then
        echo "    ok"
      else
        echo "    BLOCKED: stop falló (limpieza, no un check); el stack puede haber quedado levantado:"
        sed 's/^/    /' "$LOG" | tail -20
      fi
    else
      skip "supabase stop (limpieza)" "el stack ya estaba corriendo: no lo apaga este gate"
    fi
  else
    blocked "supabase db reset (migraciones+seed)" \
      "Docker no disponible (docker info falla); Supabase local lo necesita"
  fi
else
  skip "supabase local (start + migraciones/seed + pgTAP)" "sin cambios en supabase/"
fi

# --- Workflows: actionlint si está disponible ---------------------------------
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
