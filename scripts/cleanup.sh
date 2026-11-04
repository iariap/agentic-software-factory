#!/usr/bin/env bash
# Limpieza safety-gated de worktrees stale del harness:
#   - review: state/worktrees/<app>/pr-<n>  (PR por número)
#   - build:  state/worktrees/<app>/<slug>  (rama factory/<slug>)
#
# Uso: scripts/cleanup.sh <app> [--apply]
#   Sin --apply: sólo lista (shadow), no borra nada.
#   Con --apply: remueve los stale.
# Stale (review) = worktree pr-<n> cuyo PR en APP_REPO está MERGED/CLOSED o no
# existe. Stale (build) = PR de `factory/<slug>` MERGED/CLOSED, o rama ya
# mergeada en origin/<branch>. Nunca se borran ramas (local ni remote).
#
# Guard de corrida activa (incidente 2026-10-15): el criterio de arriba NO
# distingue una corrida en vuelo (rama sin PR, sin merge) de un stale — así se
# borraron 3 worktrees con trabajo sin commitear. `new-worktree.sh` marca cada
# corrida con `<worktree>/.asf-active` y `merge.sh --apply` retira el marker al
# fusionar: un worktree marcado se saltea SIEMPRE (también con --apply).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="${1:?uso: $0 <app> [--apply]}"
MODE="${2:-}"
if [ -n "$MODE" ] && [ "$MODE" != "--apply" ]; then
  echo "uso: $0 <app> [--apply]" >&2
  exit 2
fi

eval "$(python3 "$ROOT/scripts/app_config.py" "$APP_ID" --sh)"

WT_DIR="$ROOT/state/worktrees/$APP_ID"
if [ ! -d "$WT_DIR" ]; then
  echo "cleanup.sh: sin worktrees para $APP_ID en $WT_DIR"
  exit 0
fi

removed=0
kept=0
shopt -s nullglob
for wt in "$WT_DIR"/*/; do
  wt="${wt%/}"
  name="$(basename "$wt")"
  stale=""
  detail=""
  # Corrida activa: el marker lo escribe new-worktree.sh y sólo lo retira
  # merge.sh --apply (ya fusionada). Se saltea antes de clasificar: ni el dir
  # ni la rama se tocan, y --apply no lo fuerza.
  if [ -f "$wt/.asf-active" ]; then
    echo "activo: $name (marker .asf-active)"
    kept=$((kept + 1))
    continue
  fi
  case "$name" in
    pr-*)
      pr="${name#pr-}"
      state="$(gh pr view "$pr" --repo "${APP_REPO:?}" --json state --jq .state 2>/dev/null || echo NOT_FOUND)"
      detail="pr $pr: $state"
      case "$state" in
        MERGED|CLOSED|NOT_FOUND) stale=1 ;;
      esac
      ;;
    *)
      branch="factory/$name"
      state="$(gh pr view "$branch" --repo "${APP_REPO:?}" --json state --jq .state 2>/dev/null || echo NOT_FOUND)"
      detail="branch $branch: $state"
      case "$state" in
        MERGED|CLOSED)
          stale=1
          ;;
        NOT_FOUND)
          if [ -z "${APP_CHECKOUT:-}" ] || [ ! -d "${APP_CHECKOUT:-}" ]; then
            stale=1
          elif ! git -C "$APP_CHECKOUT" show-ref --verify --quiet "refs/heads/$branch"; then
            stale=1
          elif git -C "$APP_CHECKOUT" merge-base --is-ancestor "$branch" "origin/$APP_BRANCH"; then
            stale=1
          fi
          ;;
      esac
      ;;
  esac

  if [ -n "$stale" ]; then
    echo "stale: $name ($detail)"
    if [ "$MODE" = "--apply" ]; then
      if [ -d "${APP_CHECKOUT:-}" ] && git -C "$APP_CHECKOUT" worktree remove --force "$wt" 2>/dev/null; then
        :
      else
        rm -rf "$wt"
      fi
      removed=$((removed + 1))
    fi
  else
    echo "activo: $name ($detail)"
    kept=$((kept + 1))
  fi
done

if [ -n "${APP_CHECKOUT:-}" ] && [ -d "$APP_CHECKOUT" ]; then
  git -C "$APP_CHECKOUT" worktree prune
fi
echo "resultado: ${removed} removidos, ${kept} activos (modo: ${MODE:-shadow})"
