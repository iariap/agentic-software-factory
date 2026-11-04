#!/usr/bin/env bash
# Merge de un PR de una app del registry, resuelto SIEMPRE por rama head.
#
# Anti-patrón que codifica: un `gh pr merge <NUMERO>` con número literal fusionó
# el PR de OTRA sesión porque el PR propio se había re-creado con otro número.
# Los números de PR son globales del repo y otras sesiones los consumen; la rama
# head es la identidad estable.
#
# Uso:
#   scripts/merge.sh <app> <head-branch> [--checkout DIR] [--expected-sha SHA] [--squash|--merge] [--apply]
#
# - Shadow por default: sólo valida e imprime qué haría.
# - --apply: fusiona con el número RESUELTO por rama (nunca con el argumento).
# - Fail-closed: PR OPEN, mergeable MERGEABLE, mergeStateStatus CLEAN, el head
#   no es probadamente stale (corre los gates de hoy, no los de su fork — ver la
#   política de frescura abajo), checks verdes POR NOMBRE (un nombre con
#   SKIPPED+SUCCESS cuenta verde; sólo SKIPPED no, porque un check que no
#   corrió no es un gate), y head remoto ==
#   --expected-sha / HEAD local si la rama existe.
# - App con `ci.provider: none` (x6200: su validación es la matriz local, no
#   corre GitHub Actions en PRs): NO se lee `statusCheckRollup`. El gate es la
#   MATRIZ LOCAL del registry (`checks`), CORRIDA acá contra base/head: sin
#   exit 0 el merge se bloquea. El body del PR no se lee: es prosa, y un bloque
#   de código con `check-matrix` lo satisfacía sin haber corrido nada.
#   El árbol donde corre la matriz NO es el checkout base: sus bloques ejecutan
#   scripts del repo (drawbuf, bootanim, guards del prune), así que validarlos
#   en el base validaría la versión vieja. Precedencia: `--checkout DIR`, el
#   worktree de la corrida `state/worktrees/<app>/<slug>` (si la rama head es
#   `factory/<slug>` y existe) y, si no, el checkout del registry. El checkout
#   usado se informa por stderr (`info: matriz sobre <path>`) y un path
#   inexistente es error, no fallback silencioso.
# - Frescura de la base: `stale` (se probó que el head NO tiene los workflows de
#   la base actual) BLOQUEA; `unknown` (no se pudo probar) AVISA y CONTINÚA —
#   bloquear por no poder verificar no compra seguridad y agrega una forma nueva
#   de quedarse trabado. La automación (auto-merge.py) sí es fail-closed en los
#   dos casos.
# - Nunca --admin, nunca force-push.
# stdout: líneas `ok:`/`merged:`; errores a stderr.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  cat <<'EOF'
uso: merge.sh <app> <head-branch> [--checkout DIR] [--expected-sha SHA] [--squash|--merge] [--apply]
  El PR se resuelve por rama head, NUNCA por número literal de PR:
  los números son globales del repo y otras sesiones los consumen.
  --checkout DIR      árbol donde corre la matriz local (default: el worktree
                      de la corrida state/worktrees/<app>/<slug> si existe;
                      si no, el checkout del registry)
  --expected-sha SHA  exige que headRefOid empiece con SHA (protege force-pushes)
  --squash|--merge    método de merge (default: squash)
  --apply             fusiona (default: shadow)
EOF
}

usage_err() {
  echo "error: $1" >&2
  usage >&2
  exit 2
}

# Modo interno (lo invoca wait_until.sh): resuelve mergeable/mergeStateStatus por
# rama y sale 0 sólo cuando ninguno es UNKNOWN (imprime "MERGEABLE CLEAN").
if [ "${1:-}" = "--probe-state" ]; then
  repo="$2"
  branch="$3"
  json="$(gh pr view "$branch" --repo "$repo" --json mergeable,mergeStateStatus)" || exit 1
  state="$(printf '%s' "$json" | python3 -c 'import json, sys
d = json.load(sys.stdin)
print(d.get("mergeable", ""), d.get("mergeStateStatus", ""))')" || exit 1
  case "$state" in
    *UNKNOWN*) exit 1 ;;
    *) printf '%s\n' "$state" ;;
  esac
  exit 0
fi

APP="${1:-}"
HEAD_BRANCH="${2:-}"
if [ -z "$APP" ] || [ -z "$HEAD_BRANCH" ]; then
  usage_err "faltan <app> y/o <head-branch>"
fi
shift 2

# El argumento es la rama head. Un número literal es el anti-patrón que esta
# herramienta existe para evitar: los números de PR no identifican la sesión.
if [[ "$HEAD_BRANCH" =~ ^[0-9]+$ ]]; then
  usage_err "'$HEAD_BRANCH' es un número de PR: resolvé el PR por rama head, no por número"
fi

EXPECTED_SHA=""
METHOD=""
APPLY="false"
CHECKOUT_OVERRIDE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --checkout)
      [ "$#" -ge 2 ] || usage_err "--checkout requiere un directorio"
      CHECKOUT_OVERRIDE="$2"
      shift 2
      ;;
    --checkout=*)
      CHECKOUT_OVERRIDE="${1#*=}"
      shift
      ;;
    --expected-sha)
      [ "$#" -ge 2 ] || usage_err "--expected-sha requiere un SHA"
      EXPECTED_SHA="$2"
      shift 2
      ;;
    --expected-sha=*)
      EXPECTED_SHA="${1#*=}"
      shift
      ;;
    --squash)
      if [ -n "$METHOD" ] && [ "$METHOD" != "squash" ]; then
        usage_err "--squash y --merge son mutuamente excluyentes"
      fi
      METHOD="squash"
      shift
      ;;
    --merge)
      if [ -n "$METHOD" ] && [ "$METHOD" != "merge" ]; then
        usage_err "--squash y --merge son mutuamente excluyentes"
      fi
      METHOD="merge"
      shift
      ;;
    --apply)
      APPLY="true"
      shift
      ;;
    *)
      usage_err "argumento desconocido: '$1'"
      ;;
  esac
done
[ -n "$METHOD" ] || METHOD="squash"

eval "$(python3 "$ROOT/scripts/app_config.py" "$APP" --sh)"
REPO="$APP_REPO"
CHECKOUT="$APP_CHECKOUT"
CI_PROVIDER="${APP_CI_PROVIDER:-github}"

# 1) Resolver el PR por rama head (nunca por número).
if ! pr_json="$(gh pr view "$HEAD_BRANCH" --repo "$REPO" \
  --json number,state,mergeable,mergeStateStatus,headRefOid,headRefName,baseRefOid 2>/dev/null)"; then
  echo "error: sin PR abierto para la rama '$HEAD_BRANCH' en $REPO" >&2
  exit 1
fi

IFS=$'\t' read -r NUMBER STATE MERGEABLE MERGE_STATE HEAD_OID HEAD_REF BASE_OID < <(
  printf '%s' "$pr_json" | python3 -c 'import json, sys
d = json.load(sys.stdin)
keys = ("number", "state", "mergeable", "mergeStateStatus", "headRefOid", "headRefName", "baseRefOid")
print("\t".join(str(d.get(k, "")) for k in keys))'
)

if [ "$STATE" != "OPEN" ]; then
  echo "error: PR #$NUMBER ($HEAD_BRANCH) no está OPEN (state=$STATE)" >&2
  exit 1
fi

# 2) mergeable/clean, con reintento hasta-condición si GitHub aún computa UNKNOWN.
if [ "$MERGEABLE" = "UNKNOWN" ] || [ "$MERGE_STATE" = "UNKNOWN" ]; then
  echo "info: mergeable/mergeStateStatus=UNKNOWN; reintentando vía wait_until.sh (techo 120s)" >&2
  if ! merge_state="$(bash "$ROOT/scripts/wait_until.sh" 120 5 \
    -- bash "$ROOT/scripts/merge.sh" --probe-state "$REPO" "$HEAD_BRANCH")"; then
    echo "error: el estado de merge siguió UNKNOWN tras 120s" >&2
    exit 1
  fi
  MERGEABLE="${merge_state%% *}"
  MERGE_STATE="${merge_state##* }"
fi

if [ "$MERGEABLE" != "MERGEABLE" ] || [ "$MERGE_STATE" != "CLEAN" ]; then
  echo "error: PR #$NUMBER no es MERGEABLE/CLEAN (mergeable=$MERGEABLE mergeStateStatus=$MERGE_STATE)" >&2
  exit 1
fi

# 3) head esperado: --expected-sha y HEAD local (si la rama existe).
if [ -n "$EXPECTED_SHA" ] && [[ "$HEAD_OID" != "$EXPECTED_SHA"* ]]; then
  echo "error: headRefOid ($HEAD_OID) no empieza con --expected-sha ($EXPECTED_SHA)" >&2
  exit 1
fi

if git -C "$CHECKOUT" show-ref --verify --quiet "refs/heads/$HEAD_BRANCH"; then
  local_oid="$(git -C "$CHECKOUT" rev-parse "$HEAD_BRANCH")"
  if [ "$local_oid" != "$HEAD_OID" ]; then
    echo "error: HEAD local de '$HEAD_BRANCH' ($local_oid) difiere del remoto ($HEAD_OID); no se mergea" >&2
    exit 1
  fi
fi

# 4) Frescura de la base: en `pull_request` GitHub corre el workflow del HEAD,
# así que un PR sin rebasar corre los gates VIEJOS y su verde no dice nada del
# main actual (x6200 #279 se mergeó sin el gate de contraste #277 y main quedó
# rojo en silencio hasta #290). Política: `stale` (probado) bloquea; `unknown`
# (no se pudo probar) avisa y continúa — la duda se declara, no se convierte en
# un bloqueo que no compra seguridad.
base_rc=0
base_verdict="$(python3 "$ROOT/scripts/gate_base.py" "$APP" "$BASE_OID" "$HEAD_OID" \
  --checkout "$CHECKOUT" --trunk "$APP_BRANCH" 2>&1)" || base_rc=$?
case "$base_rc" in
  0) echo "info: gates al día — $base_verdict" >&2 ;;
  1)
    echo "error: el PR corre gates viejos — $base_verdict" >&2
    exit 1
    ;;
  *)
    echo "warn: no se pudo probar que el PR corra los gates de la base actual; se continúa — revisá a mano — $base_verdict" >&2
    ;;
esac

# 5) checks del PR. Dos caminos, fail-closed en ambos:
#    - provider=github (default): verdes POR NOMBRE, evaluados sobre el rollup
#      JSON. `gh pr checks` imprime una fila por nombre y la salida es texto:
#      cuando un workflow registra dos corridas por PR (x6200: la de
#      `opened`/`synchronize` queda SKIPPED por el gate de la etiqueta `ci` y la
#      de `labeled` corre), la SKIPPED tapa a la SUCCESS hermana y el merge se
#      traba en falso (#309). Agrupamos por nombre: falla si alguna entrada del
#      nombre falló, bloquea si el nombre está pending, y es verde si tiene al
#      menos un SUCCESS/NEUTRAL. SKIPPED NO es verde: un check que no corrió no
#      es un gate (docs/lessons.md, "un check que no corre (o corre sobre otro
#      sha) miente"). Fail-closed: sin checks tampoco se avanza.
#    - provider=none (x6200: valida con su matriz local, sin GitHub Actions en
#      los PRs): no hay rollup que leer y `none` NUNCA se cuenta como verde. El
#      gate es la MATRIZ LOCAL del registry (`checks`), corrida acá contra
#      base/head con el mismo contrato que usa el factory (`<base> <head>
#      <checkout>`). No se lee el body del PR: un fence con `check-matrix` (o un
#      "no corrí check-matrix") satisfacía el gate sin correr nada.
CHECKS_LABEL="pass"
if [ "$CI_PROVIDER" != "github" ]; then
  if [ -z "${APP_CHECKS:-}" ]; then
    echo "error: $APP no declara matriz local (checks) y no tiene CI de GitHub (si falta el checkout de la app: scripts/sync-work.sh $APP)" >&2
    exit 1
  fi
  # El árbol de la matriz: los bloques que EJECUTAN scripts del repo los corren
  # desde su working tree, así que el checkout base validaría la versión vieja
  # de esos scripts. Precedencia: --checkout, el worktree de la corrida
  # (factory/<slug> -> state/worktrees/<app>/<slug>) y el checkout del registry.
  if [ -n "$CHECKOUT_OVERRIDE" ]; then
    if [ ! -d "$CHECKOUT_OVERRIDE" ]; then
      echo "error: --checkout no existe: $CHECKOUT_OVERRIDE" >&2
      exit 1
    fi
    MATRIX_CHECKOUT="$(cd "$CHECKOUT_OVERRIDE" && pwd)"
  elif [ "${HEAD_REF#factory/}" != "$HEAD_REF" ] \
    && [ -d "$ROOT/state/worktrees/$APP/${HEAD_REF#factory/}" ]; then
    MATRIX_CHECKOUT="$ROOT/state/worktrees/$APP/${HEAD_REF#factory/}"
  else
    MATRIX_CHECKOUT="$CHECKOUT"
  fi
  # El contrato con la matriz es un path que resuelva desde CUALQUIER cwd: sus
  # checks lo reciben como argumento y como prefijo de PATH, y `run` ya hace
  # `cd "$APP"` (un path relativo se resolvería dos veces). Las tres ramas de
  # arriba ya salen absolutas (--checkout y $ROOT lo son; $APP_CHECKOUT lo
  # resuelve app_config con .resolve()), pero se absolutiza acá igual: es el
  # borde del contrato, y un cambio futuro en la resolución no puede reintroducir
  # el path relativo sin que este test lo vea. Sin el árbol no se puede correr la
  # matriz: fail-closed con el motivo, no un fallo opaco adentro.
  if [ ! -d "$MATRIX_CHECKOUT" ]; then
    echo "error: el checkout de la matriz no existe: $MATRIX_CHECKOUT" >&2
    exit 1
  fi
  MATRIX_CHECKOUT="$(cd "$MATRIX_CHECKOUT" && pwd)"
  echo "info: matriz sobre $MATRIX_CHECKOUT" >&2
  if ! git -C "$CHECKOUT" fetch --quiet origin; then
    echo "error: no se pudo actualizar $CHECKOUT desde origin; la matriz local no puede correr contra $BASE_OID..$HEAD_OID" >&2
    exit 1
  fi
  matrix_log="$(mktemp "${TMPDIR:-/tmp}/merge-matrix-XXXXXX.log")"
  matrix_rc=0
  bash "$ROOT/$APP_CHECKS" "$BASE_OID" "$HEAD_OID" "$MATRIX_CHECKOUT" >"$matrix_log" 2>&1 || matrix_rc=$?
  if [ "$matrix_rc" -ne 0 ]; then
    echo "error: PR #$NUMBER: la matriz local de $APP falló (exit $matrix_rc)" >&2
    # El veredicto está en las líneas de fallo de la matriz, no en su exit solo.
    if ! grep -E 'FAIL:|BLOCKED' "$matrix_log" | sed 's/^/  - /' >&2; then
      echo "  (la matriz no dejó líneas FAIL:/BLOCKED; últimas líneas:)" >&2
      tail -15 "$matrix_log" | sed 's/^/  | /' >&2 || true
    fi
    rm -f "$matrix_log"
    exit 1
  fi
  # Salida resumida a stderr: el `ok:` va a stdout para que el veredicto del
  # merge quede en una línea. Un BLOCKED de la matriz (falta entorno, p. ej.
  # docker) no cambia su exit por contrato: se deja visible, no se convierte en
  # fallo acá.
  grep -E '^==>|^    (ok|FAIL|BLOCKED)' "$matrix_log" | sed 's/^/  | /' >&2 || true
  rm -f "$matrix_log"
  CHECKS_LABEL="matriz-local"
  echo "ok: checks=matriz-local ($APP: CI de GitHub deshabilitado, la matriz corrió)"
else
  if ! rollup_json="$(gh pr view "$HEAD_BRANCH" --repo "$REPO" --json statusCheckRollup)"; then
    echo "error: PR #$NUMBER sin statusCheckRollup legible; no se puede confirmar CI verde" >&2
    exit 1
  fi
  checks_rc=0
  bad_checks="$(printf '%s' "$rollup_json" | python3 -c 'import json, sys
GREEN = {"SUCCESS", "NEUTRAL"}
FAIL = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}


def state(c):
    if "state" in c:
        return str(c["state"])
    if str(c.get("status", "")) != "COMPLETED":
        return "PENDING"
    return str(c.get("conclusion") or "NO_CONCLUSION")


try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(2)
if not isinstance(data, dict):
    sys.exit(2)
rollup = data.get("statusCheckRollup") or []

by_name = {}
for c in rollup:
    name = str(c.get("name") or c.get("context") or "?")
    by_name.setdefault(name, []).append(state(c))
if not by_name:
    print("sin-checks")
    sys.exit(1)

bad = []
for name in sorted(by_name):
    states = by_name[name]
    fails = [s for s in states if s in FAIL]
    if fails:
        bad.append((name, fails[0].lower()))
    elif "PENDING" in states:
        bad.append((name, "pending"))
    elif any(s in GREEN for s in states):
        continue
    elif all(s == "SKIPPED" for s in states):
        bad.append((name, "skipped (no corrió: falta la etiqueta ci)"))
    else:
        bad.append((name, states[0].lower()))
for name, label in bad:
    print(name + "\t" + label)
sys.exit(1 if bad else 0)')" || checks_rc=$?
  if [ "$checks_rc" -ne 0 ]; then
    case "$bad_checks" in
      sin-checks)
        echo "error: PR #$NUMBER sin checks reportados; no se puede confirmar CI verde" >&2
        ;;
      "")
        echo "error: PR #$NUMBER: no se pudo evaluar el statusCheckRollup" >&2
        ;;
      *)
        echo "error: PR #$NUMBER con checks no verdes:" >&2
        printf '%s\n' "$bad_checks" | awk -F'\t' '{printf "  - %s=%s\n", $1, $2}' >&2
        ;;
    esac
    exit 1
  fi
fi

# 6) shadow vs apply.
if [ "$APPLY" != "true" ]; then
  printf 'ok: PR #%s rama=%s state=%s mergeable=%s/%s checks=%s head=%s (shadow; usar --apply para mergear)\n' \
    "$NUMBER" "$HEAD_REF" "$STATE" "$MERGEABLE" "$MERGE_STATE" "$CHECKS_LABEL" "$HEAD_OID"
  exit 0
fi

if ! merge_err="$(gh pr merge "$NUMBER" --repo "$REPO" "--$METHOD" 2>&1)"; then
  echo "error: merge de PR #$NUMBER falló: $merge_err" >&2
  exit 1
fi

merged_json="$(gh pr view "$NUMBER" --repo "$REPO" --json mergeCommit,headRefOid)" || true
merge_sha="$(printf '%s' "$merged_json" | python3 -c 'import json, sys
d = json.load(sys.stdin)
mc = d.get("mergeCommit") or {}
print(mc.get("oid") or d.get("headRefOid", ""))')"

# 7) Guard del cleanup: la corrida ya fusionó, así que se retira su marker de
# corrida activa (`state/worktrees/<app>/<slug>/.asf-active`). El cleanup la
# recolecta recién en su próximo round, nunca acá. Sin marker: silencio (hay
# corridas que no pasan por new-worktree.sh, p. ej. los worktrees de review).
MERGED_REF="${HEAD_REF:-$HEAD_BRANCH}"
case "$MERGED_REF" in
  factory/?*)
    wt_marker="$ROOT/state/worktrees/$APP/${MERGED_REF#factory/}/.asf-active"
    if [ -f "$wt_marker" ]; then
      rm -f "$wt_marker"
      echo "info: marker de corrida activa retirado (${wt_marker#"$ROOT"/})" >&2
    fi
    ;;
esac

printf 'merged: %s %s\n' "$NUMBER" "$merge_sha"
