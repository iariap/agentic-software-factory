#!/usr/bin/env bash
# Health check de un entorno de una app del registry.
#
# Uso: scripts/health.sh <app> <env>
#   env: los definidos en config/apps/<app>.json (environments).
#   Un health_url vacío requiere la variable <PRE>_<ENV>_URL explícita
#   (p. ej. producción: MI_MESA_PRODUCTION_URL) — nunca se inventa.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="${1:?uso: $0 <app> <env>}"
ENV="${2:-staging}"
eval "$(python3 "$ROOT/scripts/app_config.py" "$APP_ID" --sh)"

var="APP_ENV_$(echo "$ENV" | tr '[:lower:].-' '[:upper:]__')_URL"
URL="${!var:-}"
if [ -z "$URL" ]; then
  # permitir override manual tipo MI_MESA_PRODUCTION_URL
  override="$(echo "${APP_ID//-/_}" | tr '[:lower:]' '[:upper:]')_$(echo "$ENV" | tr '[:lower:]' '[:upper:]')_URL"
  URL="${!override:-}"
fi
if [ -z "$URL" ]; then
  echo "health.sh: entorno '$ENV' sin health_url para $APP_ID (definilo en el registry o en $override). Resultado: PENDIENTE." >&2
  exit 1
fi

echo "==> GET $URL/health ($APP_ID/$ENV)"
curl -fsS -w '\nhttp=%{http_code} time=%{time_total}s\n' "$URL/health"
