#!/usr/bin/env bash
# Arma el contexto de un incidente para sevbot: estado operativo + logs
# sanitizados (sin payloads).
#
# Uso: scripts/incident.sh <app> <issue|ctx>
# Cada fuente es independiente: si una falla, se marca PENDIENTE y sigue.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="${1:?uso: $0 <app> <issue|ctx>}"
eval "$(python3 "$ROOT/scripts/app_config.py" "$APP_ID" --sh)"
REPO="$APP_REPO"
CTX="${2:?uso: $0 <app> <issue|ctx>}"

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/state/incidents/$APP_ID/$STAMP"
mkdir -p "$OUT"

{
  echo "# Incidente: $CTX ($APP_ID)"
  echo
  echo "Fecha: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "## Últimos runs de CI (main)"
  if gh run list --repo "$REPO" --branch main --limit 5 --json status,conclusion,displayTitle,createdAt 2>/dev/null; then
    echo "(ok)"
  else
    echo "PENDIENTE: sin acceso a gh run list"
  fi
  echo
  echo "## Health + versión desplegada (staging)"
  if "$ROOT/scripts/health.sh" "$APP_ID" staging 2>/dev/null; then
    echo "(ok)"
  else
    echo "PENDIENTE: health staging no responde"
  fi
  echo
  echo "## Métricas agregadas (últimas 24h)"
  if "$ROOT/scripts/metrics.sh" "$APP_ID" --hours 24 2>/dev/null; then
    echo "(ok)"
  else
    echo "PENDIENTE: sin acceso a métricas"
  fi
  echo
  echo "## Issues/PRs abiertos"
  gh issue list --repo "$REPO" --limit 10 --state open 2>/dev/null || echo "PENDIENTE"
  gh pr list --repo "$REPO" --limit 10 --state open 2>/dev/null || echo "PENDIENTE"
} > "$OUT/context.md"

echo "==> contexto de incidente en $OUT/context.md"
echo "$OUT"
