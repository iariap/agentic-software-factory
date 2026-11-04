#!/usr/bin/env python3
"""Clasifica el riesgo de un diff según la policy de la app (base + overrides).

Cada archivo se clasifica individualmente (regla más alta que le corresponda,
o el default si no matchea ninguna). El nivel final es el máximo entre
archivos; los reviewers son la unión de los reviewers de todas las
clasificaciones por archivo.

Salida: JSON con nivel de riesgo, reviewers requeridos, aprobación humana y
los archivos que dispararon cada regla.

Uso:
  scripts/risk.py <app> <base> <head>       # diff entre refs en el checkout de la app
  scripts/risk.py <app> --files f1 f2 ...   # clasifica una lista de archivos
  scripts/risk.py --self-test               # casos de la policy base (sin app)
  scripts/risk.py mi-mesa --self-test-app   # casos de la policy mergeada de mi-mesa
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import app_config  # noqa: E402

RISK_ORDER = ["low", "medium", "high"]


def load_policy(app: str) -> dict:
    return app_config.merged_policy(app)


def checkout_dir(app: str) -> Path:
    return Path(app_config.load_registry(app)["checkout_abs"])


def changed_files(app: str, base: str, head: str) -> list[str]:
    repo = checkout_dir(app)
    if not repo.exists():
        sys.exit(f"falta checkout en {repo} — correr scripts/sync-work.sh {app}")
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", base, head],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in diff.stdout.splitlines() if line.strip()]


def classify_file(path: str, rules: list[dict], levels: dict, default_risk: str) -> tuple[str, list[str]]:
    """Nivel y reviewers de UN archivo: regla más alta que matchea, o default.

    El default solo aplica si el archivo no matchea NINGUNA regla: una regla
    low (ej: frontend/**) debe poder clasificar low aunque el default sea
    medium.
    """
    best = None
    for rule in rules:
        if fnmatchcase(path, rule["pattern"]):
            rule_risk = rule.get("risk", "low")
            if best is None or RISK_ORDER.index(rule_risk) > RISK_ORDER.index(best):
                best = rule_risk
    if best is None:
        best = default_risk
    return best, levels.get(best, {}).get("reviewers", [])


def classify(files: list[str], policy: dict) -> dict:
    rules = policy["rules"]
    levels = policy["levels"]
    default_risk = policy.get("default_risk", "medium")

    level = None
    reviewers: set[str] = set()
    matches: list[dict] = []

    for path in files:
        file_level, file_reviewers = classify_file(path, rules, levels, default_risk)
        if level is None or RISK_ORDER.index(file_level) > RISK_ORDER.index(level):
            level = file_level
        reviewers.update(file_reviewers)
        # Registrar solo las reglas que realmente matchearon este archivo.
        for rule in rules:
            if fnmatchcase(path, rule["pattern"]):
                matches.append(
                    {
                        "pattern": rule["pattern"],
                        "risk": rule.get("risk", "low"),
                        "reason": rule.get("reason", ""),
                        "files": [path],
                    }
                )

    if level is None:
        level = default_risk
        reviewers.update(levels.get(default_risk, {}).get("reviewers", []))

    cfg = levels.get(level, {"reviewers": [], "human_approval": False})
    return {
        "risk": level,
        "reviewers": sorted(reviewers),
        "human_approval": cfg.get("human_approval", False) or level == "high",
        "auto_approve": cfg.get("auto_approve", False),
        "files": files,
        "matches": matches,
    }


def self_test(app: str | None) -> None:
    policy = load_policy(app) if app else json.loads((ROOT / "config" / "policies" / "base.json").read_text())
    # (nombre, archivos, nivel esperado, reviewers esperados)
    if app:
        # Casos de la policy mergeada de la app (mi-mesa).
        cases: list[tuple[str, list[str], str, list[str]]] = [
            (
                "low_puro",
                ["frontend/src/components/Button.tsx", "frontend/package.json"],
                "low",
                ["reviewer-frontend"],
            ),
            (
                "medium",
                ["backend/src/omavu/review.py", "backend/src/omavu/config.py"],
                "medium",
                ["reviewer-frontend", "reviewer-infra"],
            ),
            (
                "high",
                ["backend/src/omavu/data/catalog.json", "backend/src/omavu/api.py", "PHI.md"],
                "high",
                ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
            ),
            (
                "domain",
                ["backend/src/omavu/domain.py"],
                "high",
                ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
            ),
            (
                "extraction",
                ["backend/src/omavu/extraction.py"],
                "high",
                ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
            ),
            (
                "observability",
                ["backend/src/omavu/observability.py"],
                "high",
                ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
            ),
            (
                "catalog_module",
                ["backend/src/omavu/catalog.py"],
                "high",
                ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
            ),
            (
                "nutrition",
                ["backend/src/omavu/nutrition.py"],
                "high",
                ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
            ),
            (
                "workflows",
                ["deploy/service.template.yaml", ".github/workflows/ci.yml"],
                "high",
                ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
            ),
            (
                "pantry_agent",
                ["backend/src/omavu/pantry_agent.py"],
                "medium",
                ["reviewer-frontend", "reviewer-infra"],
            ),
            ("deps", ["backend/uv.lock", "Dockerfile"], "medium", ["reviewer-frontend", "reviewer-infra"]),
            (
                "mixed_union",
                ["backend/src/omavu/data/catalog.json", "frontend/src/components/Button.tsx"],
                "high",
                ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
            ),
            ("low_unmatched", ["docs/foo.md", "frontend/README.md"], "low", ["reviewer-frontend"]),
            ("root_unmatched", ["README.md"], "medium", ["reviewer-frontend", "reviewer-infra"]),
            (
                "frontend_plus_unmatched",
                ["frontend/src/x.tsx", "LEEME.txt"],
                "medium",
                ["reviewer-frontend", "reviewer-infra"],
            ),
        ]
    else:
        # Casos de la policy base (genéricos, sin paths de app).
        cases = [
            (
                "base_deploy_high",
                ["deploy/run.sh"],
                "high",
                ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
            ),
            ("base_docs_low", ["docs/x.md"], "low", ["reviewer-frontend"]),
            ("base_default_medium", ["random.txt"], "medium", ["reviewer-frontend", "reviewer-infra"]),
        ]
    failures = 0
    for name, files, expected_risk, expected_rev in cases:
        got = classify(files, policy)
        ok = got["risk"] == expected_risk and set(got["reviewers"]) == set(expected_rev)
        if not ok:
            failures += 1
        print(
            f"  [{'ok' if ok else 'FAIL'}] {name}: risk={got['risk']} reviewers={got['reviewers']} (esperado {expected_risk} {expected_rev})"
        )
    sys.exit(1 if failures else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", help="id de app (config/apps/<id>.json)")
    parser.add_argument("refs", nargs="*", help="base head (revisa git diff)")
    parser.add_argument("--files", nargs="+", help="lista de archivos a clasificar")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="casos de la policy (base con app '-', mergeada con app dada)",
    )
    args = parser.parse_args()

    if args.self_test:
        self_test(None if args.app == "-" else args.app)
        return 0

    policy = load_policy(args.app)
    if args.files:
        result = classify(args.files, policy)
    elif len(args.refs) == 2:
        result = classify(changed_files(args.app, args.refs[0], args.refs[1]), policy)
    else:
        parser.print_usage()
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
