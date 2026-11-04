#!/usr/bin/env bash
# merge-when-green.sh — babysit REPARADOR: espera el CI, clasifica el fallo,
# aplica el remedio mínimo y mergea cuando queda verde.
#
# Clase asf#41 (orden del owner: "arregla el babysit, si falla un check se debe
# corregir lo que genera el fallo"). Aditivo: reutiliza scripts/babysit.sh
# (espera con techo) y scripts/merge.sh (merge por rama head, fail-closed); no
# reemplaza auto-merge.py, merge.sh ni risk.py.
#
# Uso:
#   scripts/merge-when-green.sh <app> <pr> [--mode shadow|enabled]
#     [--timeout-min N] [--poll-s N] [--max-repairs N] [--state FILE]
#
#   --mode shadow|enabled  default shadow: imprime el plan y NO ejecuta nada
#                          (ni rerun, ni rebase, ni push, ni merge). `enabled`
#                          requiere orden explícita del orquestador.
#   --timeout-min N        PRESUPUESTO GLOBAL de pared del wrapper (default 30).
#                          Cada invocación de babysit recibe el tiempo restante
#                          como su propio techo: un cruce de espera (babysit
#                          exit 3) NO consume --max-repairs.
#   --poll-s N             intervalo de poll que se pasa a babysit (default 20).
#   --max-repairs N        techo de reparaciones (default 2). Agotado: exit 3.
#   --state FILE           persiste el trail + contadores en JSON (path relativo
#                          anclado a la raíz del harness).
#
# Clases de fallo (deterministas, por firmas en los logs del run caído):
#   A  flake de bench/carga: el check caído es de medición (Performance/Visual/
#      Bench) y su firma es `render-cost proxy over budget` / `p95_delta_pct`,
#      y OTRA medición de la misma familia pasó en la misma corrida (el patrón
#      de #52). Remedio: `gh run rerun --failed` (máx 1 por ronda; cuenta como rep).
#   B  base-skew: el check caído es de medición con deltas de píxeles
#      (`changed_pixels` / `flushed_pixels` con %) y el trunk avanzó respecto
#      del merge-base de la rama. Remedio: rebase del head sobre el trunk +
#      `push --force-with-lease`, SÓLO si el rebase es conflict-free
#      (`git merge-tree --write-tree` exit 0). Con conflicto: exit 5 con el
#      dato (el rebase lo resuelve el humano/orquestador). Cuenta como rep.
#   C  fallo real (pytest/nativos/estáticos/otros): NO se repara; se extrae la
#      evidencia a un archivo y se sale 5 para que el orquestador delegue el fix
#      al coder (la ruta del flujo vigente).
#
# Trail (stdout, TSV pipeable — una línea por acción; el resto va a stderr):
#   <ts>\tR<ronda>\t<clase|->\t<acción>\t<detalle>\tsha=<sha>
#   acciones: validate | babysit | rerun | rebase | evidence | budget | merge
#   clases: A | B | C | - (sin fallo)
#
# Shadow y git: shadow no toca la rama, el PR, ni archivos; para clasificar la
# CLASE B sí corre `git fetch` (sólo actualiza refs remote-tracking) y el probe
# `merge-tree`, que no mueven ninguna ref.
#
# Exit:
#   0  CI verde (enabled: mergeado; shadow: plan impreso)
#   2  error de uso
#   3  --max-repairs agotado, o deadline global del wrapper cumplido
#   4  PR no OPEN o no MERGEABLE/CLEAN
#   5  CLASE C (fallo real; la evidencia queda en el path del trail) o
#      reparación imposible (rebase con conflicto)
#   1  error inesperado
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  cat <<'EOF'
uso: merge-when-green.sh <app> <pr> [--mode shadow|enabled] [--timeout-min N]
     [--poll-s N] [--max-repairs N] [--state FILE]

  <pr> es el número del PR (el merge se hace por rama head resuelta del PR,
  nunca por número literal: scripts/merge.sh <app> <rama> --apply).
  --mode shadow|enabled  default shadow (imprime el plan, no ejecuta nada)
  --timeout-min N        presupuesto global del wrapper en minutos (default 30)
  --poll-s N             poll que se pasa a babysit (default 20)
  --max-repairs N        techo de reparaciones (default 2); agotado: exit 3
  --state FILE           persiste el trail + contadores (JSON) en FILE

Exit: 0 ok | 2 uso | 3 techo de reparaciones/deadline | 4 PR no mergeable
      | 5 fallo real (CLASE C) o rebase con conflicto | 1 inesperado.
EOF
}

usage_err() {
  echo "error: $1" >&2
  usage >&2
  exit 2
}

is_int() { [[ "$1" =~ ^[0-9]+$ ]]; }

APP="${1:-}"
PR="${2:-}"
if [ -z "$APP" ] || [ -z "$PR" ]; then
  usage_err "faltan <app> y/o <pr>"
fi
shift 2

MODE="shadow"
TIMEOUT_MIN=30
POLL_S=20
MAX_REPAIRS=2
STATE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:?--mode requiere shadow|enabled}"; shift 2 ;;
    --timeout-min) TIMEOUT_MIN="${2:?--timeout-min requiere un entero}"; shift 2 ;;
    --poll-s) POLL_S="${2:?--poll-s requiere un entero}"; shift 2 ;;
    --max-repairs) MAX_REPAIRS="${2:?--max-repairs requiere un entero}"; shift 2 ;;
    --state) STATE="${2:?--state requiere un path}"; shift 2 ;;
    *) usage_err "argumento desconocido: '$1'" ;;
  esac
done

case "$MODE" in
  shadow | enabled) ;;
  *) usage_err "--mode debe ser shadow|enabled (dado: '$MODE')" ;;
esac
for v in "$TIMEOUT_MIN" "$POLL_S" "$MAX_REPAIRS"; do
  is_int "$v" || usage_err "--timeout-min/--poll-s/--max-repairs deben ser enteros (dado: '$v')"
done
[ "$TIMEOUT_MIN" -ge 1 ] || usage_err "--timeout-min debe ser >= 1"
[ "$POLL_S" -ge 1 ] || usage_err "--poll-s debe ser >= 1"
# El PR entra por número, pero el merge sale por rama head (invariante del
# harness): un argumento con forma de rama es un error del caller.
[[ "$PR" =~ ^[0-9]+$ ]] || usage_err "'$PR' no es un número de PR; para mergear por rama usá scripts/merge.sh <app> <rama>"

eval "$(python3 "$ROOT/scripts/app_config.py" "$APP" --sh)"
REPO="$APP_REPO"
CHECKOUT="$APP_CHECKOUT"
TRUNK="$APP_BRANCH"

# Los paths relativos de argumentos se anclan a la raíz del harness, no al cwd.
case "$STATE" in
  "" | /*) ;;
  *) STATE="$ROOT/$STATE" ;;
esac

TMPD="$(mktemp -d "${TMPDIR:-/tmp}/merge-when-green-XXXXXX")"
TRAIL_FILE="$TMPD/trail.tsv"
: >"$TRAIL_FILE"

ROUND=0
REPAIRS_USED=0
HEAD_SHA=""
NUMBER="$PR"
BRANCH=""
REBASE_WT=""

now_ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# trail <ronda> <clase|-> <acción> <detalle> <sha>
trail() {
  printf '%s\tR%s\t%s\t%s\t%s\tsha=%s\n' \
    "$(now_ts)" "$1" "$2" "$3" "$4" "$5" | tee -a "$TRAIL_FILE"
}

persist_state() { # <exit-code>
  [ -n "$STATE" ] || return 0
  mkdir -p "$(dirname "$STATE")"
  python3 - "$STATE" "$1" "$PR" "$APP_ID" "$MODE" "$REPAIRS_USED" "$MAX_REPAIRS" "$ROUND" "$HEAD_SHA" "$TRAIL_FILE" <<'PY'
import json
import sys

state, code, number, app, mode, used, mx, rnd, sha, trail_path = sys.argv[1:]
with open(trail_path, encoding="utf-8") as fh:
    rows = [line.rstrip("\n").split("\t") for line in fh if line.strip()]
doc = {
    "app": app,
    "pr": int(number),
    "mode": mode,
    "exit": int(code),
    "rounds": int(rnd),
    "repairs_used": int(used),
    "max_repairs": int(mx),
    "head_sha": sha,
    "classes": [row[2] for row in rows if len(row) > 2 and row[2] != "-"],
    "actions": [row[3] for row in rows if len(row) > 3],
    "trail": rows,
}
with open(state, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
}

drop_rebase_wt() {
  if [ -n "$REBASE_WT" ] && [ -d "$REBASE_WT" ]; then
    git -C "$CHECKOUT" worktree remove --force "$REBASE_WT" >/dev/null 2>&1 || true
  fi
  REBASE_WT=""
}

cleanup() {
  local code=$?
  persist_state "$code" || true
  drop_rebase_wt
  rm -rf "$TMPD"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1) Validación de entrada (fail-closed, como merge.sh): PR OPEN + CLEAN.
# ---------------------------------------------------------------------------
STATE_V=""
MERGEABLE=""
MERGE_STATE=""
pr_json=""
if ! pr_json="$(gh pr view "$PR" --repo "$REPO" \
  --json number,state,mergeable,mergeStateStatus,headRefOid,headRefName 2>/dev/null)"; then
  echo "error: no se pudo resolver el PR #$PR en $REPO" >&2
  exit 4
fi

IFS=$'\t' read -r NUMBER STATE_V MERGEABLE MERGE_STATE HEAD_SHA BRANCH < <(
  printf '%s' "$pr_json" | python3 -c 'import json, sys
d = json.load(sys.stdin)
keys = ("number", "state", "mergeable", "mergeStateStatus", "headRefOid", "headRefName")
print("\t".join(str(d.get(k, "")) for k in keys))'
)

if [ "$STATE_V" != "OPEN" ]; then
  echo "error: PR #$NUMBER no está OPEN (state=$STATE_V)" >&2
  exit 4
fi
if [ -z "$BRANCH" ]; then
  echo "error: PR #$NUMBER sin headRefName; no se puede mergear por rama" >&2
  exit 4
fi

# GitHub computa mergeable/mergeStateStatus de forma asincrónica: reintento
# hasta-condición reutilizando el probe de merge.sh (resuelve por rama head).
if [ "$MERGEABLE" = "UNKNOWN" ] || [ "$MERGE_STATE" = "UNKNOWN" ]; then
  echo "info: mergeable/mergeStateStatus=UNKNOWN; reintentando vía wait_until.sh (techo 120s)" >&2
  if ! merge_state="$(bash "$ROOT/scripts/wait_until.sh" 120 5 \
    -- bash "$ROOT/scripts/merge.sh" --probe-state "$REPO" "$BRANCH")"; then
    echo "error: el estado de merge siguió UNKNOWN tras 120s" >&2
    exit 4
  fi
  MERGEABLE="${merge_state%% *}"
  MERGE_STATE="${merge_state##* }"
fi

if [ "$MERGEABLE" != "MERGEABLE" ] || [ "$MERGE_STATE" != "CLEAN" ]; then
  echo "error: PR #$NUMBER no es MERGEABLE/CLEAN (mergeable=$MERGEABLE mergeStateStatus=$MERGE_STATE)" >&2
  exit 4
fi

trail 0 - validate "PR #$NUMBER OPEN MERGEABLE/CLEAN rama=$BRANCH modo=$MODE" "$HEAD_SHA"

# ---------------------------------------------------------------------------
# Helpers de git (CLASE B: base-skew)
# ---------------------------------------------------------------------------

# 0 = base-skew confirmado y rebase conflict-free
# 2 = el trunk NO avanzó respecto del merge-base (no es base-skew)
# 3 = conflicto de rebase (lo resuelve el humano/orquestador)
# 1 = no se pudo probar (fetch/objetos)
probe_base_skew() {
  git -C "$CHECKOUT" fetch --prune origin "$TRUNK" "$BRANCH" >/dev/null 2>&1 || return 1
  local mb th
  mb="$(git -C "$CHECKOUT" merge-base "origin/$BRANCH" "origin/$TRUNK" 2>/dev/null)" || return 1
  th="$(git -C "$CHECKOUT" rev-parse "origin/$TRUNK" 2>/dev/null)" || return 1
  if [ -z "$mb" ] || [ -z "$th" ]; then
    return 1
  fi
  if [ "$mb" = "$th" ]; then
    return 2
  fi
  if ! git -C "$CHECKOUT" merge-tree --write-tree "origin/$TRUNK" "origin/$BRANCH" >/dev/null 2>&1; then
    return 3
  fi
  return 0
}

worktree_for_branch() {
  git -C "$CHECKOUT" worktree list --porcelain 2>/dev/null | awk -v b="refs/heads/$BRANCH" '
    /^worktree / { wt = $2 }
    /^branch / { if ($2 == b) { print wt; exit } }'
}

# Imprime el sha nuevo en stdout; el ruido de git va a stderr.
apply_rebase() {
  local wt tmpwt
  wt="$(worktree_for_branch)"
  tmpwt=""
  if [ -z "$wt" ]; then
    # El head puede estar checked out en el worktree de la corrida; si no,
    # se usa uno temporal para poder mover la ref local (merge.sh compara
    # el head local con el remoto) sin pisar ningún árbol de trabajo en uso.
    tmpwt="$TMPD/rebase-wt"
    if git -C "$CHECKOUT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
      git -C "$CHECKOUT" worktree add "$tmpwt" "$BRANCH" >/dev/null 2>&1 || return 1
    else
      git -C "$CHECKOUT" worktree add -b "$BRANCH" "$tmpwt" "origin/$BRANCH" >/dev/null 2>&1 || return 1
    fi
    wt="$tmpwt"
    REBASE_WT="$tmpwt"
  fi
  if ! git -C "$wt" rebase "origin/$TRUNK" >&2; then
    drop_rebase_wt
    return 1
  fi
  if ! git -C "$wt" push --force-with-lease origin "HEAD:refs/heads/$BRANCH" >&2; then
    drop_rebase_wt
    return 1
  fi
  drop_rebase_wt
  git -C "$CHECKOUT" rev-parse "refs/heads/$BRANCH"
}

# ---------------------------------------------------------------------------
# 2) Clasificación del fallo + 3) reparación
# ---------------------------------------------------------------------------

write_evidence() { # <motivo> <checks fallidos> <runs> <logs> -> imprime el path
  local dir ev
  dir="$ROOT/state/reports/$APP_ID/merge-when-green-pr-$PR"
  mkdir -p "$dir"
  ev="$dir/repair-evidence-$PR-$ROUND.txt"
  {
    printf 'clase: C (fallo real; no se repara automáticamente)\n'
    printf 'motivo: %s\n' "$1"
    printf 'app: %s\npr: %s\nrama: %s\nhead: %s\nronda: %s\nmodo: %s\n' \
      "$APP_ID" "$PR" "$BRANCH" "$HEAD_SHA" "$ROUND" "$MODE"
    printf 'checks fallidos: %s\nruns: %s\n' "$2" "$3"
    printf -- '--- extracto de los logs del run caído ---\n'
    printf '%s\n' "$4" | grep -iE 'fail|error|assert|traceback|✗' | head -n 200 || true
  } >"$ev"
  printf '%s' "$ev"
}

CLASSIFY_PY='import json
import re
import sys

try:
    checks = json.load(sys.stdin)
except Exception:
    checks = []
if not isinstance(checks, list):
    checks = []
bad = {"FAILURE", "ERROR", "CANCELLED", "STALE", "ACTION_REQUIRED"}
meas = re.compile(r"performance|visual|bench|medici", re.I)
failed = []
failed_measure = False
measure_pass = False
run_ids = []
for c in checks:
    if not isinstance(c, dict):
        continue
    st = str(c.get("state", ""))
    name = str(c.get("name", ""))
    link = str(c.get("link") or "")
    is_meas = bool(meas.search(name))
    if st in bad:
        failed.append(name)
        if is_meas:
            failed_measure = True
        m = re.search(r"/runs/(\d+)", link)
        if m and m.group(1) not in run_ids:
            run_ids.append(m.group(1))
    elif st == "SUCCESS" and is_meas:
        measure_pass = True
names = ";".join(n.replace("\t", " ") for n in failed)
print("true" if failed_measure else "false",
      "true" if measure_pass else "false",
      ",".join(run_ids), names)'

has_sig() { # <regex> <texto>
  printf '%s\n' "$2" | grep -qE "$1"
}

budget_guard() { # <clase>
  if [ "$REPAIRS_USED" -ge "$MAX_REPAIRS" ]; then
    trail "$ROUND" "$1" budget "max-repairs agotado ($MAX_REPAIRS)" "$HEAD_SHA"
    echo "==> techo --max-repairs agotado ($MAX_REPAIRS): no se repara más." >&2
    exit 3
  fi
  REPAIRS_USED=$((REPAIRS_USED + 1))
}

# gh re-encola los check runs del rerun: esperar (techo corto) a que el run deje
# de estar `completed` evita que el babysit siguiente lea el fallo viejo y queme
# una reparación en un race. Si no cambia, el babysit decide igual.
wait_rerun_queued() { # <run-id>
  bash "$ROOT/scripts/wait_until.sh" 120 5 -c \
    "! gh run view '$1' --repo '$REPO' --json status --jq .status 2>/dev/null | grep -q completed" \
    >/dev/null 2>&1 || true
}

repair() {
  local checks_json failed_measure measure_pass run_ids failed_names logs rid
  local clase motivo probe new_sha ev ids

  # OJO: `gh pr checks --json ...` sale != 0 cuando hay checks en failure; hay
  # que capturar sin appendear nada (un `|| echo '[]'` rompería el JSON).
  checks_json="$(gh pr checks "$PR" --repo "$REPO" --json name,state,link 2>/dev/null || true)"
  failed_measure=false
  measure_pass=false
  run_ids=""
  failed_names=""
  IFS=$' \t' read -r failed_measure measure_pass run_ids failed_names < <(
    printf '%s' "$checks_json" | python3 -c "$CLASSIFY_PY"
  ) || true

  logs=""
  IFS=',' read -r -a ids <<<"$run_ids"
  for rid in "${ids[@]:-}"; do
    [ -n "$rid" ] || continue
    logs+="$(gh run view "$rid" --repo "$REPO" --log-failed 2>/dev/null || true)"$'\n'
  done

  clase="C"
  motivo="fallo real"
  # La CLASE A exige un run id: sin él el remedio (`gh run rerun`) no es
  # aplicable y cae a CLASE C con evidencia.
  if [ -n "$run_ids" ] && [ "$failed_measure" = "true" ] &&
    has_sig 'render-cost proxy over budget|p95_delta_pct' "$logs" &&
    [ "$measure_pass" = "true" ]; then
    clase="A"
    motivo="flake de bench: la misma familia de medición pasó en la corrida"
  elif [ "$failed_measure" = "true" ] && has_sig '(changed_pixels|flushed_pixels).*%' "$logs"; then
    probe=0
    probe_base_skew || probe=$?
    case "$probe" in
      0)
        clase="B"
        motivo="base-skew: el trunk avanzó y el rebase es conflict-free"
        ;;
      3)
        clase="B-conflict"
        motivo="base-skew con conflicto de rebase"
        ;;
      2)
        motivo="firma de píxeles pero el trunk no avanzó: no es base-skew"
        ;;
      *)
        motivo="no se pudo probar base-skew (fetch/objetos)"
        ;;
    esac
  fi

  case "$clase" in
    A)
      budget_guard A
      IFS=',' read -r -a ids <<<"$run_ids"
      for rid in "${ids[@]:-}"; do
        [ -n "$rid" ] || continue
        if [ "$MODE" = "enabled" ]; then
          if ! gh run rerun "$rid" --failed --repo "$REPO" >&2; then
            echo "error: 'gh run rerun $rid --failed' falló" >&2
            exit 1
          fi
          trail "$ROUND" A rerun "gh run rerun $rid --failed" "$HEAD_SHA"
          wait_rerun_queued "$rid"
        else
          trail "$ROUND" A rerun "shadow: gh run rerun $rid --failed" "$HEAD_SHA"
        fi
      done
      ;;
    B)
      budget_guard B
      if [ "$MODE" = "enabled" ]; then
        new_sha="$(apply_rebase)" || {
          echo "error: rebase/push sobre origin/$TRUNK falló" >&2
          exit 1
        }
        trail "$ROUND" B rebase "rebase sobre origin/$TRUNK + push --force-with-lease" "$new_sha"
        HEAD_SHA="$new_sha"
      else
        trail "$ROUND" B rebase "shadow: rebase sobre origin/$TRUNK + push --force-with-lease" "$HEAD_SHA"
      fi
      ;;
    B-conflict)
      trail "$ROUND" B rebase "conflicto de rebase sobre origin/$TRUNK: lo resuelve el humano/orquestador" "$HEAD_SHA"
      echo "==> $motivo: no se aplica un rebase con conflicto." >&2
      exit 5
      ;;
    *)
      ev="$(write_evidence "$motivo" "$failed_names" "$run_ids" "$logs")"
      trail "$ROUND" C evidence "$ev ($motivo)" "$HEAD_SHA"
      echo "==> CLASE C ($motivo): evidencia en $ev; el fix lo delega el orquestador." >&2
      exit 5
      ;;
  esac
}

# ---------------------------------------------------------------------------
# 4) Loop: babysit → reparación → merge
# ---------------------------------------------------------------------------
DEADLINE=$(($(date +%s) + TIMEOUT_MIN * 60))

while :; do
  remaining=$((DEADLINE - $(date +%s)))
  if [ "$remaining" -le 0 ]; then
    echo "==> deadline global del wrapper cumplido (${TIMEOUT_MIN}m)." >&2
    trail "$ROUND" - budget "deadline global (${TIMEOUT_MIN}m) cumplido" "$HEAD_SHA"
    exit 3
  fi

  ROUND=$((ROUND + 1))
  bmin=$(((remaining + 59) / 60))
  [ "$bmin" -ge 1 ] || bmin=1
  # Estado de babysit por ronda: su anti-loop rechazaría el reintento con el
  # MISMO head SHA y el MISMO exit de fallo (caso exacto del rerun de CLASE A),
  # y este wrapper ES el loop de reintentos.
  baby_state="$TMPD/babysit-r$ROUND.json"

  rc=0
  bash "$ROOT/scripts/babysit.sh" "$APP" "$PR" --timeout-min "$bmin" --poll-s "$POLL_S" \
    --state "$baby_state" >&2 || rc=$?

  if [ -f "$baby_state" ]; then
    round_sha="$(python3 -c "import json, sys
print(json.load(open(sys.argv[1])).get('head_sha', ''))" "$baby_state" 2>/dev/null || true)"
    if [ -n "$round_sha" ]; then
      HEAD_SHA="$round_sha"
    fi
  fi

  case "$rc" in
    0)
      trail "$ROUND" - babysit "ci verde" "$HEAD_SHA"
      break
      ;;
    2)
      trail "$ROUND" - babysit "checks fallidos: clasificando" "$HEAD_SHA"
      repair
      ;;
    3)
      # La CI sigue corriendo: cruce de espera, no consume --max-repairs.
      trail "$ROUND" - babysit "timeout de espera (no consume reparación)" "$HEAD_SHA"
      ;;
    4)
      echo "error: PR #$PR no mergeable durante el babysit (exit 4)." >&2
      exit 4
      ;;
    *)
      echo "error: babysit.sh falló inesperadamente (exit $rc)." >&2
      exit 1
      ;;
  esac
done

# 5) Merge (siempre por rama head resuelta del PR, fail-closed).
if [ "$MODE" = "enabled" ]; then
  merge_out=""
  if ! merge_out="$(bash "$ROOT/scripts/merge.sh" "$APP" "$BRANCH" --apply 2>&1)"; then
    echo "error: el merge de $BRANCH falló: $merge_out" >&2
    exit 1
  fi
  # El detalle del trail es UNA línea (TSV pipeable): se colapsan los saltos.
  # `merge.sh --apply` ya imprime `merged: <pr> <sha>`; se usa tal cual.
  merged_sha="${merge_out##*merged: }"
  merged_sha="${merged_sha##* }"
  trail "$ROUND" - merge "${merge_out//$'\n'/ | }" "$merged_sha"
else
  trail "$ROUND" - merge "shadow: merge.sh $APP $BRANCH --apply (no ejecutado)" "$HEAD_SHA"
fi
exit 0
