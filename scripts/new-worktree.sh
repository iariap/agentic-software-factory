#!/usr/bin/env bash
# Crea (o retoma) el worktree desechable de una corrida del factory:
#   state/worktrees/<app>/<slug> con rama factory/<slug>.
#
# Uso: scripts/new-worktree.sh <app> <slug> [base]
#   <app>  id de app del registry (sin default; ver `app_config.py --list`)
#   <slug> nombre de la corrida (sin '/', sin '~', no `pr-<n>`)
#   [base] referencia base (default: origin/<branch> del registry)
#
# El checkout base work/<app> sólo se sincroniza (sync-work.sh); nunca se
# implementa ahí. stdout: SÓLO la ruta absoluta del worktree (pipeable, p.ej.
# para pasársela al coder); el progreso va a stderr.
#
# Inicializa los submódulos declarados en .gitmodules tras crear el worktree
# (timeout SUBMODULE_TIMEOUT, default 180s): si fallan, WARN a stderr y el
# stdout sigue siendo sólo el path.
#
# Deja el marker `<worktree>/.asf-active` (corrida ACTIVA): cleanup.sh no
# recolecta un worktree marcado y merge.sh --apply lo retira al fusionar.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="${1:-}"
SLUG="${2:-}"

usage() {
  echo "uso: $0 <app> <slug> [base]" >&2
  echo "apps: $(python3 "$ROOT/scripts/app_config.py" --list | tr '\n' ' ')" >&2
}

[ -n "$APP_ID" ] || { usage; exit 2; }
[ -n "$SLUG" ] || { usage; exit 2; }
case "$SLUG" in
  */*|*"~"*) usage; exit 2 ;;
esac
case "$SLUG" in
  pr-[0-9]*) usage; exit 2 ;;
esac

eval "$(python3 "$ROOT/scripts/app_config.py" "$APP_ID" --sh)"

BASE="${3:-origin/$APP_BRANCH}"
WT="$ROOT/state/worktrees/$APP_ID/$SLUG"
BRANCH="factory/$SLUG"
# Techo para `git submodule update --init` (los submódulos pueden tardar; la
# red puede colgar): override con SUBMODULE_TIMEOUT=<segundos>.
SUBMODULE_TIMEOUT="${SUBMODULE_TIMEOUT:-180}"
# Marker de corrida activa: `cleanup.sh` NUNCA recolecta un worktree marcado.
# Una corrida en vuelo (coder escribiendo, rama sin PR y sin merge) es
# indistinguible de un worktree stale por el criterio de la rama; la marca es
# la única señal de que hay alguien trabajando ahí. Lo retira `merge.sh --apply`
# al fusionar (y recién ahí el cleanup la recolecta).
MARKER="$WT/.asf-active"

# Idempotente: marca si falta (retomar una corrida desmarcada la re-arma) y no
# toca el marker existente.
mark_active() {
  [ -e "$MARKER" ] || touch "$MARKER"
}

# Corre "$@" con techo de segundos: `timeout(1)` si está, si no el patrón
# background+poll+kill (sin depender de coreutils). Devuelve el exit del
# comando, o 124 si expiró.
run_with_timeout() {
  local secs="$1"
  shift
  local rc=0
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@" || rc=$?
    return "$rc"
  fi
  "$@" &
  local pid=$!
  local deadline=$(( $(date +%s) + secs ))
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 124
    fi
    sleep 1
  done
  wait "$pid" || rc=$?
  return "$rc"
}

# Inicializa los submódulos que el repo de la app declara en .gitmodules (p.ej.
# lvgl en la rama del X6200): una corrida sin inicializar arranca rota
# ("Unable to find current revision in submodule path"). Genérico: sin
# .gitmodules es no-op. Nunca bloquea: si falla (red/timeout) o queda algo sin
# inicializar, WARN a stderr con el comando manual y el stdout sigue siendo sólo
# el path.
init_submodules() {
  [ -f "$WT/.gitmodules" ] || return 0
  echo "==> inicializando submódulos (timeout ${SUBMODULE_TIMEOUT}s)" >&2
  if ! run_with_timeout "$SUBMODULE_TIMEOUT" git -C "$WT" submodule update --init --recursive >&2; then
    echo "WARN: no se pudieron inicializar los submódulos de $WT (timeout ${SUBMODULE_TIMEOUT}s o red)." >&2
    echo "      reintentá a mano: git -C $WT submodule update --init --recursive" >&2
    return 0
  fi
  local status
  status="$(git -C "$WT" submodule status 2>/dev/null || true)"
  if grep -q '^-' <<<"$status"; then
    echo "WARN: quedaron submódulos sin inicializar en $WT." >&2
    echo "      reintentá a mano: git -C $WT submodule update --init --recursive" >&2
  fi
  return 0
}

if [ ! -d "$APP_CHECKOUT/.git" ]; then
  echo "==> checkout base ausente; sync-work.sh $APP_ID" >&2
  bash "$ROOT/scripts/sync-work.sh" "$APP_ID" >&2
else
  git -C "$APP_CHECKOUT" fetch --all --prune >&2
fi

# Idempotencia: si el worktree ya está registrado, se reusa tal cual.
if git -C "$APP_CHECKOUT" worktree list --porcelain | grep -qxF "worktree $WT"; then
  mark_active
  init_submodules
  echo "$WT"
  exit 0
fi

mkdir -p "$(dirname "$WT")"
if git -C "$APP_CHECKOUT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git -C "$APP_CHECKOUT" worktree add "$WT" "$BRANCH" >&2
else
  git -C "$APP_CHECKOUT" worktree add -b "$BRANCH" "$WT" "$BASE" >&2
fi
mark_active
init_submodules

echo "$WT"
