#!/usr/bin/env python3
"""Vía rápida de auto-merge para PRs de riesgo mínimo, fall-closed.

Un PR es elegible SOLO si se cumplen TODAS estas condiciones:
  1. riesgo == low según la policy de la app (config/policies/base.json +
     config/policies/<app>.json): todos los archivos del diff clasifican low;
     medium/high/default quedan afuera;
  2. `auto_merge.levels` lo permite (low por defecto);
  3. CI verde: los checks del PR verdes POR NOMBRE (ver `ci_block_reason`:
     cubre StatusContext y CheckRun, nada pending/fail; un nombre con SKIPPED +
     SUCCESS cuenta verde, uno con sólo SKIPPED bloquea porque no corrió — misma
     semántica que `merge.sh`). Si la app declara `ci.provider: none` (no corre
     GitHub Actions en PRs) la vía rápida NO aplica: no hay CI que leer y `none`
     nunca se cuenta como verde;
  4. PR abierto y mergeable (no draft, sin bloqueadores);
  5. el PR no tiene el label needs-human-approval ni comentarios pendientes
     del tipo "DO-NOT-MERGE";
  6. sin archivos de datos/migraciones/secrets (patrón prohibido explícito);
  7. el head desciende de la base actual: en `pull_request` GitHub corre el
     workflow del HEAD, así que un PR sin rebasar corre gates VIEJOS y su verde
     no dice nada del main actual (x6200 #279 se mergeó sin el gate de
     contraste #277 y main quedó rojo en silencio hasta #290). `stale` o
     `unknown` (no se puede probar) bloquean la vía rápida.

Modos (`auto_merge.mode` en la policy):
  shadow  -> evalúa y deja reporte en state/reports/; NO mergea (default).
  enabled -> mergea con merge_method (squash).
  off     -> se niega todo.

Tras mergear (enabled): si `post_deploy.watch_main_run`, espera el run de
main y verifica health de staging; ante degrada, si `auto_revert` está ON,
revierte el squash con un commit revert + PR revert-<sha> y reporta. Nunca
toca producción.

Uso:
  scripts/auto-merge.py <app> <pr> [--apply]  # --apply sólo tiene efecto si mode=enabled
  scripts/auto-merge.py --self-test           # casos de elegibilidad sin red
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "state" / "reports"
sys.path.insert(0, str(ROOT / "scripts"))
import app_config  # noqa: E402
import gate_base  # noqa: E402
import risk as risk_mod  # noqa: E402

FORBIDDEN_PATTERNS = [
    "supabase/migrations/*",
    "deploy/*",
    ".github/*",
    "*.env*",
    "*secret*",
]


def gh(args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def classify_diff(files: list[str], policy: dict) -> str:
    levels = policy["levels"]
    rules = policy["rules"]
    default = policy.get("default_risk", "medium")
    order = ["low", "medium", "high"]
    worst = "low"
    for f in files:
        level, _ = risk_mod.classify_file(f, rules, levels, default)
        if order.index(level) > order.index(worst):
            worst = level
    return worst


def is_forbidden(path: str) -> bool:
    import fnmatch

    return any(fnmatch.fnmatch(path, p) for p in FORBIDDEN_PATTERNS)


# `gh pr view --json statusCheckRollup` devuelve DOS formas en la misma lista:
#   - StatusContext: {"state": "SUCCESS"|"FAILURE"|"ERROR"|"PENDING"}
#   - CheckRun (GitHub Actions): {"status": "COMPLETED"|"IN_PROGRESS"|...,
#                                 "conclusion": "SUCCESS"|...}  (sin `state`)
# Leer sólo `state` mandaba todo CheckRun a un default y la vía rápida low-risk
# nunca era elegible. La normalización vive acá, en un solo lugar puro.
GREEN_CHECK_STATES = frozenset({"SUCCESS", "NEUTRAL"})
FAIL_CHECK_STATES = frozenset(
    {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR", "STALE"}
)


def check_state(check: dict[str, Any]) -> str:
    """Estado normalizado de una entrada de `statusCheckRollup`.

    SKIPPED se devuelve como SKIPPED (no como SUCCESS): un check que no corrió
    no es un gate. Si el MISMO nombre tiene además un SUCCESS, la agregación por
    nombre (`name_verdict`) lo cuenta verde, igual que merge.sh.
    """
    if "state" in check:
        return str(check["state"])
    # CheckRun: el veredicto está en (status, conclusion).
    if str(check.get("status", "")) != "COMPLETED":
        return "PENDING"
    return str(check.get("conclusion") or "NO_CONCLUSION")


def check_states(rollup: list[dict[str, Any]] | None) -> list[str]:
    return [check_state(c) for c in rollup or []]


def check_name(check: dict[str, Any]) -> str:
    return str(check.get("name") or check.get("context") or "?")


def checks_by_name(rollup: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    """Agrupa los estados normalizados por nombre (un workflow puede correr 2x)."""
    by_name: dict[str, list[str]] = {}
    for check in rollup or []:
        by_name.setdefault(check_name(check), []).append(check_state(check))
    return by_name


def name_verdict(states: list[str]) -> tuple[bool, str]:
    """(verde, etiqueta_del_bloqueo) de UN nombre, con sus estados agregados.

    Es la misma semántica que `merge.sh`: falla si alguna entrada falló, bloquea
    si está pending, y es verde si tiene al menos un SUCCESS/NEUTRAL. Un nombre
    con sólo SKIPPED bloquea: no corrió.
    """
    fails = [s for s in states if s in FAIL_CHECK_STATES]
    if fails:
        return False, fails[0].lower()
    if "PENDING" in states:
        return False, "pending"
    if any(s in GREEN_CHECK_STATES for s in states):
        return True, ""
    if all(s == "SKIPPED" for s in states):
        return False, "skipped (no corrió)"
    return False, (states[0].lower() if states else "sin estado")


def ci_block_reason(rollup: list[dict[str, Any]] | None) -> str | None:
    """None si el CI está verde; si no, el motivo con los nombres y sus estados."""
    by_name = checks_by_name(rollup)
    if not by_name:
        return "CI no está verde (sin checks)"
    bad: list[str] = []
    for name in sorted(by_name):
        ok, label = name_verdict(by_name[name])
        if not ok:
            bad.append(f"{name}={label}")
    if bad:
        return f"CI no está verde ({', '.join(bad)})"
    return None


def evaluate(
    pr_data: dict, files: list[str], policy: dict, ci_provider: str = "github"
) -> tuple[bool, list[str]]:
    """(elegible, razones_de_bloqueo). Puro: no toca la red.

    `ci_provider` es el `ci.provider` de la app: con `none` la vía rápida no
    aplica (no hay CI de GitHub que leer, y `none` no es un verde).
    """
    am = policy.get("auto_merge", {})
    reasons: list[str] = []
    mode = am.get("mode", "shadow")
    if mode == "off":
        reasons.append("auto_merge.mode=off")
        return False, reasons
    if pr_data.get("state") != "OPEN":
        reasons.append(f"PR no abierto (state={pr_data.get('state')})")
    if pr_data.get("isDraft"):
        reasons.append("PR en draft")
    if pr_data.get("mergeable") == "CONFLICTING":
        reasons.append("PR con conflictos de merge")
    labels = [lab["name"] for lab in pr_data.get("labels", [])]
    if "needs-human-approval" in labels:
        reasons.append("label needs-human-approval")
    if any("DO-NOT-MERGE" in c.get("body", "") for c in pr_data.get("comments", [])):
        reasons.append("comentario DO-NOT-MERGE")
    bad_files = [f for f in files if is_forbidden(f)]
    if bad_files:
        reasons.append(f"archivos prohibidos: {bad_files}")
    level = classify_diff(files, policy)
    eligible = am.get("eligible_risk", ["low"])
    if level not in eligible:
        reasons.append(f"riesgo={level} no está en eligible_risk={eligible}")
    if am.get("require_ci_green", True):
        if ci_provider != "github":
            reasons.append("CI de GitHub deshabilitado (provider=none): la vía rápida necesita CI verde")
        else:
            block = ci_block_reason(pr_data.get("statusCheckRollup") or [])
            if block:
                reasons.append(block)
    body = pr_data.get("body") or ""
    if am.get("require_validation_section", True) and "Validación" not in body:
        reasons.append("sin sección 'Validación' en el body")
    return len(reasons) == 0, reasons


def _report(pr: str, verdict: dict) -> Path:
    d = REPORTS / "auto-merge"
    d.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    p = d / f"pr{pr}-{stamp}.md"
    lines = [f"# auto-merge PR {pr}", "", f"fecha: {dt.datetime.now().isoformat()}", ""]
    for k, v in verdict.items():
        lines.append(f"- **{k}**: {v}")
    p.write_text("\n".join(lines) + "\n")
    return p


def fetch_pr(repo: str, pr: str) -> dict:
    r = gh(
        [
            "pr",
            "view",
            pr,
            "--repo",
            repo,
            "--json",
            "state,isDraft,labels,comments,body,headRefOid,baseRefOid,statusCheckRollup,mergeable",
        ],
        check=True,
    )
    return json.loads(r.stdout)


def pr_files(repo: str, pr: str) -> list[str]:
    r = gh(["pr", "diff", pr, "--repo", repo], check=True)
    names: list[str] = []
    for line in r.stdout.splitlines():
        m = re.match(r"^\+\+\+ b/(.+)$", line)
        if m and m.group(1) != "/dev/null":
            names.append(m.group(1))
    return sorted(set(names))


def post_deploy_watch(app: str, repo: str, policy: dict) -> tuple[bool, str]:
    am = policy["auto_merge"]
    pd = am.get("post_deploy", {})
    timeout = int(pd.get("watch_timeout_s", 900))
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        r = gh(
            [
                "run",
                "list",
                "--repo",
                repo,
                "--branch",
                "main",
                "--limit",
                "1",
                "--json",
                "status,conclusion,headSha",
            ]
        )
        try:
            run = json.loads(r.stdout)[0]
        except (json.JSONDecodeError, IndexError):
            time.sleep(30)
            continue
        if run["status"] == "completed":
            if run["conclusion"] != "success":
                return False, f"run de main falló: {run}"
            env = pd.get("health_env", "staging")
            h = subprocess.run(
                [str(ROOT / "scripts" / "health.sh"), app, env],
                capture_output=True,
                text=True,
            )
            if h.returncode != 0:
                return False, f"health {env} falló: {h.stdout}{h.stderr}"
            return True, f"main verde + health {env} OK"
        last = json.dumps(run)
        time.sleep(45)
    return False, f"timeout esperando run de main ({timeout}s, último: {last})"


def auto_revert(app: str, repo: str, merge_sha: str) -> bool:
    branch = f"revert-{merge_sha[:7]}"
    work = Path(app_config.load_registry(app)["checkout_abs"])
    steps = [
        ["git", "-C", str(work), "fetch", "origin", "main"],
        ["git", "-C", str(work), "checkout", "-B", branch, "origin/main"],
        ["git", "-C", str(work), "revert", "--no-edit", merge_sha],
        ["git", "-C", str(work), "push", "-f", "origin", branch],
    ]
    for s in steps:
        if subprocess.run(s, capture_output=True, text=True).returncode != 0:
            return False
    gh(
        [
            "pr",
            "create",
            "--repo",
            repo,
            "--head",
            branch,
            "--base",
            "main",
            "--title",
            f"revert {merge_sha[:7]} (auto-revert post-deploy)",
            "--body",
            "Auto-revert del harness: el deploy post-merge degradó staging."
            " Requiere aprobación humana para mergear.",
        ]
    )
    return True


def decide(app: str, pr: str, apply_requested: bool) -> int:
    reg = app_config.load_registry(app)
    policy = app_config.merged_policy(app)
    am = policy.get("auto_merge", {})
    repo = reg.get("repo", "")
    mode = am.get("mode", "shadow")
    provider = app_config.ci_provider(app)
    try:
        data = fetch_pr(repo, pr)
        files = pr_files(repo, pr)
    except subprocess.CalledProcessError as e:
        print(f"error leyendo PR {pr}: {e.stderr.strip()}", file=sys.stderr)
        return 2
    eligible, reasons = evaluate(data, files, policy, ci_provider=provider)
    # Frescura de la base: la vía rápida también mergea, así que también la exige.
    # Fail-closed: `stale` y `unknown` bloquean (un gate que no se puede probar no
    # es un gate verde).
    freshness = gate_base.base_freshness(
        Path(reg["checkout_abs"]),
        str(data.get("baseRefOid") or ""),
        str(data.get("headRefOid") or ""),
        str(reg.get("branch", "main")),
    )
    if freshness.status != gate_base.FRESH:
        eligible = False
        reasons.append(f"base no actualizada ({freshness.status}): {freshness.detail}")
    verdict = {
        "mode": mode,
        "ci_provider": provider,
        "riesgo": classify_diff(files, policy),
        "archivos": len(files),
        "eligible": eligible,
        "bloqueos": reasons or "ninguno",
        "head": data.get("headRefOid", ""),
        "base": f"{freshness.status}: {freshness.detail}",
    }
    acted = "no (--apply no pedido)" if not apply_requested else "no (evaluación)"
    if eligible and apply_requested:
        if mode == "enabled":
            method = am.get("merge_method", "squash")
            m = gh(
                ["pr", "merge", pr, "--repo", repo, f"--{method}"],
            )
            if m.returncode == 0:
                acted = "MERGEADO"
                merge_sha = gh(
                    ["pr", "view", pr, "--repo", repo, "--json", "mergeCommit", "--jq", ".mergeCommit.oid"]
                ).stdout.strip()
                ok, detail = post_deploy_watch(app, repo, policy)
                verdict["post_deploy"] = detail
                if not ok and am.get("post_deploy", {}).get("auto_revert"):
                    rolled = auto_revert(app, repo, merge_sha)
                    verdict["auto_revert"] = "PR de revert creado" if rolled else "FALLÓ el revert: humano"
            else:
                acted = f"merge falló: {m.stderr.strip()[:200]}"
        else:
            acted = f"no se mergea (mode={mode}: sombra)"
    verdict["accion"] = acted
    path = _report(f"{app}-{pr}", verdict)
    print(json.dumps(verdict, indent=2, ensure_ascii=False))
    print(f"reporte: {path}", file=sys.stderr)
    return 0 if eligible else 1


SELF_TESTS: list[tuple[str, dict, list[str], bool]] = [
    (
        "docs low + CI verde (StatusContext y CheckRun) => elegible",
        {
            "state": "OPEN",
            "isDraft": False,
            "labels": [],
            "comments": [],
            "body": "## Validación\ntests",
            "statusCheckRollup": [
                {"state": "SUCCESS"},
                {
                    "__typename": "CheckRun",
                    "name": "UI A/B Summary",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                },
            ],
        },
        ["docs/x.md"],
        True,
    ),
    (
        "SKIPPED+SUCCESS del mismo nombre => verde (no corre 2x: una corrió)",
        {
            "state": "OPEN",
            "isDraft": False,
            "labels": [],
            "comments": [],
            "body": "## Validación\ntests",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "UI Visual Regression",
                    "status": "COMPLETED",
                    "conclusion": "SKIPPED",
                },
                {
                    "__typename": "CheckRun",
                    "name": "UI Visual Regression",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                },
            ],
        },
        ["docs/x.md"],
        True,
    ),
    (
        "todos los checks SKIPPED => no elegible (no corrió ningún gate)",
        {
            "state": "OPEN",
            "isDraft": False,
            "labels": [],
            "comments": [],
            "body": "## Validación\ntests",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "UI Visual Regression",
                    "status": "COMPLETED",
                    "conclusion": "SKIPPED",
                },
                {
                    "__typename": "CheckRun",
                    "name": "UI Performance",
                    "status": "COMPLETED",
                    "conclusion": "SKIPPED",
                },
            ],
        },
        ["docs/x.md"],
        False,
    ),
    (
        "CheckRun con conclusion FAILURE => no elegible",
        {
            "state": "OPEN",
            "isDraft": False,
            "labels": [],
            "comments": [],
            "body": "## Validación\ntests",
            "statusCheckRollup": [
                {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"},
            ],
        },
        ["docs/x.md"],
        False,
    ),
    (
        "CheckRun sin completar (IN_PROGRESS) => no elegible",
        {
            "state": "OPEN",
            "isDraft": False,
            "labels": [],
            "comments": [],
            "body": "## Validación\ntests",
            "statusCheckRollup": [
                {"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None},
            ],
        },
        ["docs/x.md"],
        False,
    ),
    (
        "default medium => no elegible",
        {
            "state": "OPEN",
            "isDraft": False,
            "labels": [],
            "comments": [],
            "body": "## Validación\ntests",
            "statusCheckRollup": [{"state": "SUCCESS"}],
        },
        ["backend/pyproject.toml"],
        False,
    ),
    (
        "CI pendiente => no elegible",
        {
            "state": "OPEN",
            "isDraft": False,
            "labels": [],
            "comments": [],
            "body": "## Validación\ntests",
            "statusCheckRollup": [{"state": "PENDING"}],
        },
        ["docs/x.md"],
        False,
    ),
    (
        "label needs-human-approval => no elegible",
        {
            "state": "OPEN",
            "isDraft": False,
            "labels": [{"name": "needs-human-approval"}],
            "comments": [],
            "body": "## Validación\ntests",
            "statusCheckRollup": [{"state": "SUCCESS"}],
        },
        ["docs/readme.md"],
        False,
    ),
    (
        "sin sección Validación => no elegible",
        {
            "state": "OPEN",
            "isDraft": False,
            "labels": [],
            "comments": [],
            "body": "arreglo chiquito",
            "statusCheckRollup": [{"state": "SUCCESS"}],
        },
        ["docs/x.md"],
        False,
    ),
    (
        "migración => prohibida aunque low",
        {
            "state": "OPEN",
            "isDraft": False,
            "labels": [],
            "comments": [],
            "body": "## Validación",
            "statusCheckRollup": [{"state": "SUCCESS"}],
        },
        ["supabase/migrations/002.sql"],
        False,
    ),
]


def self_test() -> int:
    # usa solo la base: los casos citan paths cubiertos por reglas comunes,
    # asi el self-test anda igual en un clone fresco sin checkouts de apps
    policy = json.loads((ROOT / "config" / "policies" / "base.json").read_text())
    failed = 0
    for name, pr_data, files, want in SELF_TESTS:
        got, reasons = evaluate(pr_data, files, policy)
        ok = got == want
        print(f"{'OK ' if ok else 'FAIL'} {name}" + ("" if ok else f" -> {reasons}"))
        failed += 0 if ok else 1
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("app", nargs="?", help="id de app (config/apps/<id>.json)")
    ap.add_argument("pr", nargs="?", help="número de PR")
    ap.add_argument(
        "--apply", action="store_true", help="mergear si elegible (requiere mode=enabled en la policy)"
    )
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.app or not a.pr:
        ap.error("uso: auto-merge.py <app> <pr> [--apply] (o --self-test)")
    return decide(a.app, a.pr, a.apply)


if __name__ == "__main__":
    sys.exit(main())
