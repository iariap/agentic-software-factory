#!/usr/bin/env bash
# Babysitter determinista del CI de un PR: espera a que los checks terminen,
# con timeout, revalidación de head SHA y estado persistido en toda salida.
# NO arregla código: reporta qué falló para que el agente lo corrija.
#
# Declara (warn, no bloquea la espera) si el head corre gates VIEJOS: en
# `pull_request` GitHub usa el workflow de la rama head, así que un PR sin
# rebasar puede quedar verde sin haber corrido los gates del main actual
# (x6200 #279 → main rojo en silencio, #290). El bloqueo lo hace merge.sh.
#
# Uso: scripts/babysit.sh <app> <pr> [--timeout-min N] [--poll-s N] [--state FILE]
#   Exit 0: todos los checks success y PR mergeable.
#   Exit 2: hay checks en failure (se imprimen).
#   Exit 3: timeout sin resolución.
#   Exit 4: PR no mergeable (conflictos) o mergeable desconocido al salir.
#
# App con `ci.provider: none` (no corre GitHub Actions en PRs): no hay checks
# remotos que esperar, así que babysit NO puede decir "verde" — sólo reporta el
# estado del PR (existe, OPEN, MERGEABLE, no draft: exit 0; si no, exit 4) y
# declara que la validación es la matriz local, que corre `merge.sh` al mergear.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="${1:?uso: $0 <app> <pr> [--timeout-min N] [--poll-s N] [--state FILE]}"
eval "$(python3 "$ROOT/scripts/app_config.py" "$APP_ID" --sh)"
REPO="$APP_REPO"
CI_PROVIDER="${APP_CI_PROVIDER:-github}"

PR="${2:?uso: $0 <app> <pr> ...}"
TIMEOUT_MIN=20
POLL_S=20
STATE=""

shift 2
while [ $# -gt 0 ]; do
  case "$1" in
    --timeout-min) TIMEOUT_MIN="${2:?}"; shift 2 ;;
    --poll-s) POLL_S="${2:?}"; shift 2 ;;
    --state) STATE="${2:?}"; shift 2 ;;
    *) echo "arg desconocido: $1" >&2; exit 1 ;;
  esac
done

# App sin CI de GitHub (`ci.provider: none`, p. ej. x6200): no hay checks
# remotos que esperar, pero "no hay checks" NO es verde. La validación es la
# matriz local y la corre `merge.sh` al mergear; acá sólo se puede validar el
# estado del PR: existe, OPEN, MERGEABLE y no draft. Cualquier otra cosa es
# exit 4 (el mismo que usa la rama normal para "no mergeable").
if [ "$CI_PROVIDER" != "github" ]; then
  if ! pr_json="$(gh pr view "$PR" --repo "$REPO" --json number,state,mergeable,isDraft 2>/dev/null)"; then
    echo "error: no se pudo resolver el PR #$PR en $REPO" >&2
    exit 4
  fi
  IFS=$'\t' read -r NUMBER P_STATE P_MERGEABLE P_DRAFT < <(
    printf '%s' "$pr_json" | python3 -c 'import json, sys
d = json.load(sys.stdin)
print("\t".join(str(d.get(k, "")) for k in ("number", "state", "mergeable"))
      + "\t" + str(bool(d.get("isDraft"))).lower())'
  )
  echo "CI de GitHub deshabilitado para $APP_ID (provider=$CI_PROVIDER): la validación es la matriz local, la corre merge.sh al mergear; babysit sólo reporta el estado del PR"
  echo "==> PR #$NUMBER state=$P_STATE mergeable=$P_MERGEABLE draft=$P_DRAFT"
  if [ "$P_STATE" != "OPEN" ]; then
    echo "error: PR #$NUMBER no está OPEN (state=$P_STATE)" >&2
    exit 4
  fi
  if [ "$P_MERGEABLE" != "MERGEABLE" ]; then
    echo "error: PR #$NUMBER no es mergeable (mergeable=$P_MERGEABLE)" >&2
    exit 4
  fi
  if [ "$P_DRAFT" = "true" ]; then
    echo "error: PR #$NUMBER está en draft" >&2
    exit 4
  fi
  echo "==> PR mergeable: el gate de validación es la matriz local (lo corre merge.sh)."
  exit 0
fi

HEAD_SHA="$(gh pr view "$PR" --repo "$REPO" --json headRefOid --jq .headRefOid)"
START="$(date +%s)"
DEADLINE=$((START + TIMEOUT_MIN * 60))

TMP="$(mktemp /tmp/babysit-XXXX.txt)"
WATCH_PID=""

cleanup() {
  [ -n "$WATCH_PID" ] && kill "$WATCH_PID" 2>/dev/null || true
  rm -f "$TMP"
}
trap cleanup EXIT

# Guarda el estado SIEMPRE, con el código de salida.
persist_state() {
  local code="$1" status="$2" mergeable="$3"
  if [ -n "$STATE" ]; then
    mkdir -p "$(dirname "$STATE")"
    python3 - "$STATE" "$HEAD_SHA" "$mergeable" "$status" "$code" <<'PY'
import json, sys
state, sha, mergeable, status, code = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
with open(state, "w") as f:
    json.dump({"head_sha": sha, "mergeable": mergeable, "checks": status, "exit": int(code)}, f)
PY
  fi
}

# Anti-loop: el estado anterior registra el head SHA y el exit. Si se
# re-invoca con el MISMO SHA y el MISMO exit de fallo, no reintentar: el
# agente debe cambiar algo (fix, base actualizada) antes de volver.
if [ -n "$STATE" ] && [ -f "$STATE" ]; then
  PREV="$(python3 -c "import json; d=json.load(open('$STATE')); print(d.get('head_sha',''), d.get('exit',0))" 2>/dev/null || echo '')"
  if [ -n "$PREV" ]; then
    set -- $PREV
    PREV_SHA="$1"; PREV_EXIT="$2"
    if [ "$PREV_SHA" = "$HEAD_SHA" ] && [ "$PREV_EXIT" != "0" ]; then
      echo "==> anti-loop: mismo head SHA ($HEAD_SHA) y exit $PREV_EXIT previo." >&2
      echo "    NO reintentar el mismo comando: aplicar un fix o integrar main." >&2
      exit "$PREV_EXIT"
    fi
  fi
fi

echo "==> babysit PR #$PR ($APP_ID, head $HEAD_SHA, timeout ${TIMEOUT_MIN}m)"

# Frescura de la base: se declara temprano (warn) para que el agente rebase antes
# de esperar un CI que corre gates viejos. El bloqueo —sólo cuando se PRUEBA que
# el head es stale— vive en merge.sh, que es el último gate de la cadena.
BASE_SHA="$(gh pr view "$PR" --repo "$REPO" --json baseRefOid --jq .baseRefOid 2>/dev/null || echo '')"
base_rc=0
base_verdict="$(python3 "$ROOT/scripts/gate_base.py" "$APP_ID" "$BASE_SHA" "$HEAD_SHA" \
  --checkout "$APP_CHECKOUT" --trunk "$APP_BRANCH" 2>&1)" || base_rc=$?
case "$base_rc" in
  0) echo "==> gates al día: $base_verdict" >&2 ;;
  1)
    echo "warn: el head corre gates viejos — $base_verdict" >&2
    echo "      rebasá sobre origin/$APP_BRANCH y volvé a esperar: merge.sh rechaza un PR con gates viejos." >&2
    ;;
  *)
    echo "warn: no se pudo probar que el head corra los gates de la base actual — $base_verdict" >&2
    echo "      se declara y se sigue: merge.sh avisa y continúa en este caso (la duda no se cuenta como verde)." >&2
    ;;
esac

# Watcher: espera a que los checks terminen de correr.
gh pr checks "$PR" --repo "$REPO" --watch --interval "$POLL_S" >"$TMP" 2>&1 &
WATCH_PID=$!

STATUS=""
MERGEABLE="unknown"
while :; do
  MERGEABLE="$(gh pr view "$PR" --repo "$REPO" --json mergeable --jq .mergeable 2>/dev/null || echo unknown)"
  if [ "$MERGEABLE" = "CONFLICTING" ]; then
    echo "==> PR con conflictos de merge." >&2
    persist_state 4 "CONFLICTING" "$MERGEABLE"
    exit 4
  fi

  # Revalidar que el head no cambió durante la espera.
  CUR_SHA="$(gh pr view "$PR" --repo "$REPO" --json headRefOid --jq .headRefOid 2>/dev/null || echo '')"
  if [ -n "$CUR_SHA" ] && [ "$CUR_SHA" != "$HEAD_SHA" ]; then
    echo "==> head SHA cambió durante la espera ($HEAD_SHA → $CUR_SHA)." >&2
    persist_state 3 "HEAD_CHANGED" "$MERGEABLE"
    exit 3
  fi

  # Checks: verificar si ya terminaron.
  if ! kill -0 "$WATCH_PID" 2>/dev/null; then
    STATUS="$(gh pr checks "$PR" --repo "$REPO" --json state 2>/dev/null || echo '[]')"
    if echo "$STATUS" | grep -q '"FAILURE"\|"ERROR"\|"CANCELLED"\|"ACTION_REQUIRED"\|"STALE"'; then
      echo "==> Checks fallidos:" >&2
      gh pr checks "$PR" --repo "$REPO" 2>/dev/null | grep -i "fail\|error\|cancel\|stale" >&2 || true
      persist_state 2 "$STATUS" "$MERGEABLE"
      exit 2
    fi
    if echo "$STATUS" | grep -q '"SUCCESS"' && ! echo "$STATUS" | grep -q '"PENDING"\|"IN_PROGRESS"\|"QUEUED"\|"NEUTRAL"'; then
      # Todos success: salir solo si mergeable.
      if [ "$MERGEABLE" = "MERGEABLE" ]; then
        persist_state 0 "$STATUS" "$MERGEABLE"
        echo "==> CI verde y PR mergeable."
        exit 0
      fi
      echo "==> CI verde pero PR no mergeable ($MERGEABLE)." >&2
      persist_state 4 "$STATUS" "$MERGEABLE"
      exit 4
    fi
  fi

  if [ "$(date +%s)" -gt "$DEADLINE" ]; then
    echo "==> timeout tras ${TIMEOUT_MIN}m sin resolución." >&2
    persist_state 3 "$STATUS" "$MERGEABLE"
    exit 3
  fi
  sleep "$POLL_S"
done
