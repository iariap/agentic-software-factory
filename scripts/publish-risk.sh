#!/usr/bin/env bash
# Publica el resultado de riesgo de un PR como check-run de GitHub, para que
# branch protection pueda requerirlo antes de mergear.
#
# Uso: scripts/publish-risk.sh <app> <pr>
#   El nivel de riesgo lo calcula risk.py sobre el diff inmutable del PR;
#   nunca se recibe como argumento.
#   Requiere GITHUB_TOKEN (GitHub App o token con checks:write).
#   Si no hay token de checks, el script falla con PENDIENTE (no inventa).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="${1:?uso: $0 <app> <pr>}"
eval "$(python3 "$ROOT/scripts/app_config.py" "$APP_ID" --sh)"
REPO="$APP_REPO"
PR="${2:?uso: $0 <app> <pr>}"

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "publish-risk.sh: se requiere GITHUB_TOKEN con permiso checks:write" >&2
  echo "    (gh auth de usuario no basta para check-runs). Resultado: PENDIENTE." >&2
  exit 1
fi

# Nivel calculado por risk.py sobre el diff inmutable base..head.
BASE_SHA="$(gh pr view "$PR" --repo "$REPO" --json baseRefOid --jq .baseRefOid)"
HEAD_SHA="$(gh pr view "$PR" --repo "$REPO" --json headRefOid --jq .headRefOid)"
git -C "$APP_CHECKOUT" fetch origin "$HEAD_SHA" --depth 1 >/dev/null 2>&1 || true
FILES="$(git -C "$APP_CHECKOUT" diff --name-only "$BASE_SHA" "$HEAD_SHA" 2>/dev/null || true)"
RISK="$(python3 "$ROOT/scripts/risk.py" "$APP_ID" --files $FILES | python3 -c 'import json,sys; print(json.load(sys.stdin)["risk"])')"

case "$RISK" in
  high)
    CONCLUSION="failure"
    TITLE="Risk: high — requiere aprobación humana"
    SUMMARY="Cambio de riesgo alto según la policy de $APP_ID (config/policies/). No mergear sin aprobación humana explícita y review de todos los reviewers requeridos."
    ;;
  medium)
    CONCLUSION="neutral"
    TITLE="Risk: medium — review agentica requerida"
    SUMMARY="Cambio de riesgo medio. Requiere review de los reviewers del nivel antes de mergear."
    ;;
  low)
    CONCLUSION="success"
    TITLE="Risk: low — auto-approve"
    SUMMARY="Cambio de riesgo bajo. Elegible para auto-approve."
    ;;
  *)
    echo "nivel de riesgo inválido del clasificador: $RISK" >&2
    exit 1
    ;;
esac

# Payload con output como objeto anidado (formato Checks API).
python3 - "$HEAD_SHA" "$CONCLUSION" "$TITLE" "$SUMMARY" <<'PY' | gh api --method POST "repos/$REPO/check-runs" --input - >/dev/null
import json, sys
sha, conclusion, title, summary = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
print(json.dumps({
    "name": "factory-risk",
    "head_sha": sha,
    "status": "completed",
    "conclusion": conclusion,
    "output": {"title": title, "summary": summary},
}))
PY

echo "==> check 'factory-risk' publicado en PR #$PR ($APP_ID) como $CONCLUSION (nivel $RISK)"
