#!/usr/bin/env bash
# Recoge métricas agregadas de rendimiento de una app (perf factory) SIN
# exponer payloads.
#
# Con GCP: consulta Cloud Monitoring por request_count (tasa de éxito) y
# request_latencies (p50/p95/p99) en la ventana, más conteo de logs por
# severidad. Solo agregados: nunca vuelca contenido de logs al agente.
#
# Uso: scripts/metrics.sh <app> [--hours 24]
#   Requiere: métricas configuradas en el registry (metrics.gcp_project) +
#   gcloud autenticado (Cloud Monitoring/Logging).
#   Sin acceso: PENDIENTE explícito (exit 1), nunca datos inventados.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="${1:?uso: $0 <app> [--hours N]}"
eval "$(python3 "$ROOT/scripts/app_config.py" "$APP_ID" --sh)"

HOURS=24
if [ "${2:-}" = "--hours" ]; then
  HOURS="${3:?--hours requiere un valor numérico}"
  case "$HOURS" in
    *[!0-9]*|'' ) echo "--hours debe ser un número positivo: $HOURS" >&2; exit 1 ;;
  esac
  [ "$HOURS" -gt 0 ] 2>/dev/null || { echo "--hours debe ser > 0" >&2; exit 1; }
fi

if command -v gcloud >/dev/null 2>&1 && [ -n "${APP_GCP_PROJECT:-}" ]; then
  export GCP_PROJECT="$APP_GCP_PROJECT" HOURS
  python3 "$ROOT/scripts/cloud_monitoring.py"
  exit 0
fi

echo "metrics.sh: sin acceso a gcloud o sin metrics.gcp_project en el registry de $APP_ID" >&2
echo "Se requiere configuración previa para recolectar métricas. Resultado: PENDIENTE." >&2
exit 1
