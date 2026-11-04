#!/usr/bin/env bash
# Estado del CI de un PR, commit o rama de una app del registry — con la
# EVIDENCIA de los fallos.
#
# Clase de error que codifica (2026-10-23): el NOMBRE del job no es el veredicto.
# El job "UI Visual Regression" de x6200 falla por el gate de contraste o por
# Performance, y el veredicto real está en el step fallido y en el artefacto
# (`visual.json`). Leer el nombre del job (o sólo "hay un FAILURE") hace
# diagnosticar el gate equivocado. Y un check que NO corrió (o que corrió sobre
# otro sha) no es un gate verde: se declara, nunca se infiere.
#
# Por eso, para un PR con checks fallidos, este script imprime:
#   - el resumen de estados de los checks,
#   - por cada run fallido: el/los STEP fallidos de cada job, el extracto del log
#     fallido y los artefactos (los JSON chicos se bajan y se muestran: ahí está
#     el veredicto medido, p. ej. visual.json),
#   - DECLARACIÓN explícita si el PR no tiene checks o si un run corrió sobre un
#     sha distinto del head del PR (datos viejos).
#
# Uso: scripts/ci-status.sh <app> <pr|sha|rama>
#   numérico        -> número de PR
#   rama            -> rama (ej: main, factory/x)
#   sha de 40 chars -> commit
#
# Es un comando de observación (no mergea ni bloquea): exit 0 siempre que pueda
# leer el estado; las declaraciones van a stdout para que queden en el reporte.
#
# App con `ci.provider: none` (no corre GitHub Actions en PRs): no hay checks
# remotos. Se reporta el estado/mergeable del PR y se declara que la validación
# es la matriz local (la corre merge.sh al mergear). El `<ref>` sigue siendo
# obligatorio: sin ref no hay nada que reportar.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="${1:?uso: $0 <app> <pr|sha|rama>}"
eval "$(python3 "$ROOT/scripts/app_config.py" "$APP_ID" --sh)"
REPO="$APP_REPO"
CI_PROVIDER="${APP_CI_PROVIDER:-github}"

# El `<ref>` se valida ANTES de la excepción del provider: sin ref no hay estado
# que reportar y `ci-status.sh <app>` no puede salir 0 por ser `none`.
REF="${2:?uso: $0 <app> <pr|sha|rama>}"

# App sin CI de GitHub (`ci.provider: none`): no hay estado de checks remoto que
# leer. Se reporta el estado/mergeable del PR (la validación es la matriz local,
# que corre merge.sh al mergear) y se declara explícitamente que no hay CI.
if [ "$CI_PROVIDER" != "github" ]; then
  echo "CI de GitHub deshabilitado para $APP_ID (provider=$CI_PROVIDER): la validación es la matriz local"
  if [[ "$REF" =~ ^[0-9]+$ ]]; then
    echo "==> PR #$REF ($APP_ID)"
    gh pr view "$REF" --repo "$REPO" \
      --json number,state,mergeable,isDraft,headRefName,headRefOid,baseRefOid,title
  elif [[ "$REF" =~ ^[0-9a-f]{40}$ ]]; then
    echo "==> commit $REF ($APP_ID): PRs asociados"
    gh api "repos/$REPO/commits/$REF/pulls"
  else
    echo "==> rama $REF ($APP_ID)"
    gh pr view "$REF" --repo "$REPO" \
      --json number,state,mergeable,isDraft,headRefName,headRefOid,baseRefOid,title
  fi
  exit 0
fi

TMPD="$(mktemp -d "${TMPDIR:-/tmp}/ci-status-XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

# Artefactos que se bajan para leer el veredicto: chicos (no son videos ni
# screenshots gigantes) y con un JSON adentro. El tope está en bytes.
ARTIFACT_MAX_BYTES=1000000

if [[ "$REF" =~ ^[0-9]+$ ]]; then
  echo "==> PR #$REF ($APP_ID)"
  pr_json="$(gh pr view "$REF" --repo "$REPO" \
    --json state,mergeable,headRefName,headRefOid,baseRefOid,title)"
  printf '%s\n' "$pr_json"
  HEAD_SHA="$(printf '%s' "$pr_json" | python3 -c \
    'import json, sys; print(json.load(sys.stdin).get("headRefOid", ""))')"

  # `gh pr checks` sale != 0 cuando hay checks en failure: se captura sin
  # appendear nada (un `|| echo` rompería el JSON).
  checks_json="$(gh pr checks "$REF" --repo "$REPO" --json name,state,link 2>/dev/null || true)"
  printf '%s' "$checks_json" >"$TMPD/checks.json"

  # Resumen + declaraciones + ids de los runs fallidos (una fuente: el JSON).
  python3 - "$TMPD/checks.json" "$HEAD_SHA" "$TMPD/failed-ids.txt" <<'PY'
import json
import re
import sys

path, head, out = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path, encoding="utf-8") as fh:
        checks = json.load(fh)
except (json.JSONDecodeError, OSError):
    checks = []
if not isinstance(checks, list):
    checks = []
bad = {"FAILURE", "ERROR", "CANCELLED", "STALE", "ACTION_REQUIRED"}
pending = {"PENDING", "IN_PROGRESS", "QUEUED", "WAITING", "EXPECTED"}
runs: list[str] = []
n_bad = n_pending = 0
print("--- checks")
for c in checks:
    if not isinstance(c, dict):
        continue
    state = str(c.get("state", ""))
    name = str(c.get("name", ""))
    print(f"  {state:<12} {name}")
    if state in bad:
        n_bad += 1
        m = re.search(r"/runs/(\d+)", str(c.get("link") or ""))
        if m and m.group(1) not in runs:
            runs.append(m.group(1))
    elif state in pending:
        n_pending += 1
with open(out, "w", encoding="utf-8") as fh:
    fh.write("\n".join(runs))
if not checks:
    print("DECLARACIÓN: el PR no tiene checks reportados: un gate que no corre NO es un gate verde.")
    print("  (¿el workflow no dispara para este PR? ¿falta la etiqueta que lo habilita? ¿el push no llegó?)")
else:
    print(f"--- veredicto: {len(checks) - n_bad - n_pending} ok, {n_bad} fail, {n_pending} pending")
    if not head:
        print("DECLARACIÓN: la API no devolvió el head del PR; no se puede saber si los checks son de este commit.")
PY

  FAILED_IDS="$(tr '\n' ' ' <"$TMPD/failed-ids.txt")"
  for rid in $FAILED_IDS; do
    echo "--- evidencia del run $rid (el nombre del job no es el veredicto)"
    echo "    run: https://github.com/$REPO/actions/runs/$rid"
    if ! gh run view "$rid" --repo "$REPO" --json headSha,conclusion,jobs >"$TMPD/run-$rid.json" 2>/dev/null; then
      echo "  (no se pudo leer el run $rid: gh run view falló)"
      continue
    fi
    python3 - "$TMPD/run-$rid.json" "$HEAD_SHA" <<'PY'
import json
import sys

path, head = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
except (json.JSONDecodeError, OSError):
    print("  (run sin JSON legible)")
    raise SystemExit(0)
run_sha = str(doc.get("headSha") or "")
if run_sha and head and run_sha != head:
    print(
        f"  DECLARACIÓN: el run corrió sobre {run_sha[:12]}, no sobre el head del PR"
        f" {head[:12]}: son datos VIEJOS, no el veredicto de este PR."
    )
print(f"  conclusion del run: {doc.get('conclusion')}")
for job in doc.get("jobs") or []:
    conclusion = str(job.get("conclusion") or "")
    if conclusion in {"", "success", "skipped", "neutral"}:
        continue
    steps = [s.get("name", "") for s in job.get("steps") or [] if str(s.get("conclusion") or "") == "failure"]
    print(f"  job: {job.get('name')} ({conclusion})")
    if steps:
        for step in steps:
            print(f"    step fallido: {step}")
    else:
        print("    step fallido: el job no declara steps; se lee el log de abajo")
PY
    echo "  log fallido (últimas líneas con firma de fallo):"
    gh run view "$rid" --repo "$REPO" --log-failed >"$TMPD/log-$rid.txt" 2>"$TMPD/log-$rid.err" || true
    if [ -s "$TMPD/log-$rid.txt" ]; then
      log_excerpt="$(grep -iE "fail|error|assert|regres|threshold|umbral|violaci|traceback|no cumple" \
        "$TMPD/log-$rid.txt" | tail -15)" || true
      if [ -n "$log_excerpt" ]; then
        printf '%s\n' "$log_excerpt" | sed 's/^/    /'
      else
        echo "    (el log no tiene líneas con firma de fallo: leerlo completo en el run de arriba)"
      fi
    else
      echo "    (sin log: el run no dejó log fallido — revisá la config del workflow en el run de arriba)"
    fi
    echo "  artefactos:"
    if gh api "repos/$REPO/actions/runs/$rid/artifacts" >"$TMPD/art-$rid.json" 2>/dev/null; then
      python3 - "$TMPD/art-$rid.json" "$ARTIFACT_MAX_BYTES" >"$TMPD/art-list-$rid.tsv" <<'PY'
import json
import sys

path, cap = sys.argv[1], int(sys.argv[2])
try:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
except (json.JSONDecodeError, OSError):
    doc = {}
arts = doc.get("artifacts") if isinstance(doc, dict) else []
for art in arts or []:
    if not isinstance(art, dict):
        continue
    name = str(art.get("name") or "")
    size = art.get("size_in_bytes")
    if not name or not isinstance(size, int):
        continue
    if size > cap:
        continue
    print(f"{name}\t{size}")
PY
      if [ -s "$TMPD/art-list-$rid.tsv" ]; then
        while IFS=$'\t' read -r art_name art_size; do
          [ -n "$art_name" ] || continue
          echo "    - $art_name (${art_size} bytes): se baja para leer el veredicto medido"
          art_dir="$TMPD/art/$rid/$art_name"
          mkdir -p "$art_dir"
          if ! gh run download "$rid" --repo "$REPO" -n "$art_name" -D "$art_dir" >/dev/null 2>&1; then
            echo "      (no se pudo bajar; a mano: gh run download $rid -n $art_name -D /tmp/$art_name)"
            continue
          fi
          found=0
          while IFS= read -r json_file; do
            found=1
            echo "      ${json_file#"$art_dir"/}:"
            head -40 "$json_file" | sed 's/^/        /'
          done < <(find "$art_dir" -maxdepth 3 -type f -name "*.json" | sort)
          [ "$found" -eq 1 ] || echo "      (el artefacto no trae JSON)"
        done <"$TMPD/art-list-$rid.tsv"
      else
        echo "    (sin artefactos chicos: nada que bajar; se lee el log de arriba)"
      fi
    else
      echo "    (sin artefactos o sin permiso para leerlos)"
    fi
  done
elif [[ "$REF" =~ ^[0-9a-f]{40}$ ]]; then
  echo "==> commit $REF ($APP_ID)"
  gh run list --repo "$REPO" --commit "$REF" --limit 5 --json status,conclusion,displayTitle,workflowName,createdAt
else
  echo "==> rama $REF ($APP_ID)"
  gh run list --repo "$REPO" --branch "$REF" --limit 5 --json status,conclusion,displayTitle,workflowName,createdAt
fi
