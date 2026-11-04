#!/usr/bin/env python3
"""Tests independientes del harness (sin deps fuera de la stdlib).

Correr: python3 scripts/test_harness.py
Cubre: risk.py (casos literales), scripts shell (sintaxis), y fakes de
babysit/publish-risk con binarios falsos en PATH.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import app_config  # noqa: E402
import gate_base  # noqa: E402
import risk  # noqa: E402


def _app_metadata_available(app: str) -> bool:
    """True si la policy de la app es resoluble (checkout con .factory/)."""
    try:
        reg = app_config.load_registry(app)
    except SystemExit:
        return False
    return (Path(reg["checkout_abs"]) / ".factory" / "policy.json").exists()


MI_MESA_RULES = _app_metadata_available("mi-mesa")
# Policy mergeada de mi-mesa (base + .factory/ del checkout): la que usan los casos.
POLICY = app_config.merged_policy("mi-mesa")


def _git_test_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="Asf Test",
        GIT_AUTHOR_EMAIL="asf-test@example.com",
        GIT_COMMITTER_NAME="Asf Test",
        GIT_COMMITTER_EMAIL="asf-test@example.com",
    )
    return env


def _seed_repo(tmp: Path) -> tuple[Path, str]:
    """Seed repo local con rama `main` y un commit; devuelve (path, sha)."""
    seed = tmp / "seed"
    seed.mkdir()
    env = _git_test_env()
    subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True, env=env)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "--allow-empty", "-m", "seed"],
        check=True,
        capture_output=True,
        env=env,
    )
    sha = subprocess.run(
        ["git", "-C", str(seed), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    return seed, sha


def _seed_repo_with_submodule(tmp: Path) -> tuple[Path, str]:
    """Seed repo local con un submódulo (repo gemelo local file://), como lvgl.

    El submódulo vive en `vendor/twin`; devuelve (path, sha) del seed con el
    submódulo ya commiteado (el worktree lo tiene que inicializar).
    """
    env = _git_test_env()
    twin = tmp / "twin"
    twin.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(twin)], check=True, capture_output=True, env=env)
    (twin / "lib.txt").write_text("twin\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(twin), "add", "-A"], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(twin), "commit", "-m", "twin"], check=True, capture_output=True, env=env)
    seed, _ = _seed_repo(tmp)
    # URL file:// absoluta: sigue resolviendo cuando el seed se clona a otro dir
    # (work/<app>). git >=2.38 exige protocol.file.allow=always para esto.
    subprocess.run(
        [
            "git",
            "-C",
            str(seed),
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            f"file://{twin}",
            "vendor/twin",
        ],
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "-C", str(seed), "commit", "-m", "add submodule"],
        check=True,
        capture_output=True,
        env=env,
    )
    sha = subprocess.run(
        ["git", "-C", str(seed), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    return seed, sha


def _fake_harness(tmp: Path, app: str = "fakeapp") -> Path:
    """Copia `scripts/` a tmp y registra una app con el seed repo local (sin red)."""
    import shutil

    shutil.copytree(SCRIPTS, tmp / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    seed, _ = _seed_repo(tmp)
    apps_dir = tmp / "config" / "apps"
    apps_dir.mkdir(parents=True)
    (apps_dir / f"{app}.json").write_text(
        json.dumps(
            {
                "id": app,
                "repo": "f/o",
                "remote": str(seed),
                "branch": "main",
                "checkout": f"work/{app}",
            }
        )
    )
    return seed


def _isolated_harness(tmp: Path, app: str = "fakeapp", pointer: dict[str, Any] | None = None) -> Path:
    """Harness aislado en tmp: scripts + policies + una app del registry.

    El checkout de la app es un directorio vacío (sin `.factory/`): el overlay
    sólo aparece si el test lo escribe. Sin red y sin tocar el registry real.
    """
    import shutil

    shutil.copytree(SCRIPTS, tmp / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config" / "policies", tmp / "config" / "policies")
    apps_dir = tmp / "config" / "apps"
    apps_dir.mkdir(parents=True)
    checkout = tmp / "work" / app
    checkout.mkdir(parents=True)
    reg: dict[str, Any] = {
        "id": app,
        "repo": "f/o",
        "remote": "https://example.invalid/f/o.git",
        "branch": "main",
        "checkout": str(checkout),
    }
    reg.update(pointer or {})
    (apps_dir / f"{app}.json").write_text(json.dumps(reg))
    return tmp


class TestRiskLiteral(unittest.TestCase):
    """Valores esperados literales (no derivados del algoritmo)."""

    CASES: ClassVar[list[tuple[list[str], str, list[str]]]] = [
        # (archivos, nivel, reviewers)
        (["frontend/src/Button.tsx"], "low", ["reviewer-frontend"]),
        (["backend/src/omavu/review.py"], "medium", ["reviewer-frontend", "reviewer-infra"]),
        (
            ["backend/src/omavu/data/catalog.json"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["backend/src/omavu/domain.py"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["backend/src/omavu/extraction.py"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["backend/src/omavu/observability.py"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["backend/src/omavu/tracing.py"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["backend/src/omavu/catalog.py"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["backend/src/omavu/nutrition.py"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["backend/src/omavu/services.py"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (["backend/src/omavu/pantry_agent.py"], "medium", ["reviewer-frontend", "reviewer-infra"]),
        (
            ["supabase/migrations/001.sql"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["deploy/service.template.yaml"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["deploy/envs/staging.env"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["deploy/deploy.sh"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["deploy/gcp-setup.sh"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["deploy/migrate.sh"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["deploy/nginx.conf"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["deploy/tests/test-ci-docs-detect.sh"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["deploy/artifact-cleanup.sh"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["deploy/envs/staging-min-zero.env"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["deploy/service.template.yaml", "frontend/src/Button.tsx"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (
            ["backend/src/omavu/review.py", "deploy/service.template.yaml"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
        (["backend/src/omavu/config.py"], "medium", ["reviewer-frontend", "reviewer-infra"]),
        (["backend/src/omavu/model_settings.py"], "medium", ["reviewer-frontend", "reviewer-infra"]),
        (["backend/src/omavu/deadlines.py"], "medium", ["reviewer-frontend", "reviewer-infra"]),
        (["backend/pyproject.toml"], "medium", ["reviewer-frontend", "reviewer-infra"]),
        (["backend/uv.lock"], "medium", ["reviewer-frontend", "reviewer-infra"]),
        (["Dockerfile"], "medium", ["reviewer-frontend", "reviewer-infra"]),
        (["frontend/src/Button.tsx", "frontend/package.json"], "low", ["reviewer-frontend"]),
        (["frontend/package.json"], "low", ["reviewer-frontend"]),
        (["frontend/tsconfig.json"], "low", ["reviewer-frontend"]),
        (["frontend/vite.config.ts"], "low", ["reviewer-frontend"]),
        (["frontend/next.config.ts"], "low", ["reviewer-frontend"]),
        (["frontend/index.html"], "low", ["reviewer-frontend"]),
        (["evals/run.py"], "low", ["reviewer-frontend"]),
        (["evals/dataset.json"], "low", ["reviewer-frontend"]),
        (["evals/harness.py"], "low", ["reviewer-frontend"]),
        (["evals/judge.py"], "low", ["reviewer-frontend"]),
        (["evals/personas.py"], "low", ["reviewer-frontend"]),
        (["evals/metrics.py"], "low", ["reviewer-frontend"]),
        (["evals/sitegen.py"], "low", ["reviewer-frontend"]),
        (["evals/checks.py"], "low", ["reviewer-frontend"]),
        (["docs/mvp-reliability/baseline.md"], "low", ["reviewer-frontend"]),
        (["README.md"], "medium", ["reviewer-frontend", "reviewer-infra"]),
        (["PHI.md"], "high", ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"]),
        (
            ["supabase/migrations/001_init.sql"],
            "high",
            ["reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"],
        ),
    ]

    def setUp(self) -> None:
        if not MI_MESA_RULES:
            self.skipTest(
                "sin checkout de mi-mesa (.factory/policy.json): correr scripts/sync-work.sh mi-mesa"
            )

    def test_cases(self) -> None:
        for files, expected_risk, expected_reviewers in self.CASES:
            with self.subTest(files=files):
                got = risk.classify(files, POLICY)
                self.assertEqual(got["risk"], expected_risk)
                self.assertEqual(set(got["reviewers"]), set(expected_reviewers))

    def test_human_approval(self) -> None:
        high = risk.classify(["backend/src/omavu/domain.py"], POLICY)
        self.assertTrue(high["human_approval"])
        low = risk.classify(["frontend/src/Button.tsx"], POLICY)
        self.assertFalse(low["human_approval"])
        self.assertTrue(low["auto_approve"])

    def test_union_reviewers(self) -> None:
        # frontend (low) + catalog (high): reviewers de todas las reglas.
        got = risk.classify(["frontend/src/Button.tsx", "backend/src/omavu/data/catalog.json"], POLICY)
        self.assertEqual(got["risk"], "high")
        self.assertEqual(
            set(got["reviewers"]),
            {"reviewer-security", "reviewer-infra", "reviewer-data", "reviewer-frontend"},
        )


class TestShellScripts(unittest.TestCase):
    def test_syntax(self) -> None:
        for sh in SCRIPTS.rglob("*.sh"):
            with self.subTest(script=sh.name):
                subprocess.run(["bash", "-n", str(sh)], check=True, capture_output=True)

    def test_risk_self_test(self) -> None:
        # policy base (app '-'); la de mi-mesa solo con checkout sincronizado
        subprocess.run(
            [sys.executable, str(SCRIPTS / "risk.py"), "-", "--self-test"], check=True, capture_output=True
        )
        if MI_MESA_RULES:
            subprocess.run(
                [sys.executable, str(SCRIPTS / "risk.py"), "mi-mesa", "--self-test"],
                check=True,
                capture_output=True,
            )

    def test_auto_merge_self_test(self) -> None:
        subprocess.run(
            [sys.executable, str(SCRIPTS / "auto-merge.py"), "--self-test"], check=True, capture_output=True
        )


class TestAutoMergeCheckRollup(unittest.TestCase):
    """Regresión: `gh --json statusCheckRollup` mezcla StatusContext y CheckRun.

    auto-merge.py leía sólo `state`, campo que existe únicamente en
    StatusContext: todo CheckRun de GitHub Actions caía al default PENDING y el
    veredicto era siempre `eligible: false` (la vía rápida low-risk era un
    no-op). Los casos de abajo fijan las dos formas con payloads falsos, así el
    bug no puede volver a depender de la forma que devuelva `gh`.
    """

    @staticmethod
    def _module() -> Any:
        import importlib.util

        spec = importlib.util.spec_from_file_location("auto_merge", SCRIPTS / "auto-merge.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @staticmethod
    def _check_run(conclusion: str | None = None, status: str = "COMPLETED") -> dict[str, Any]:
        return {
            "__typename": "CheckRun",
            "conclusion": conclusion,
            "status": status,
            "name": "UI A/B Summary",
        }

    @staticmethod
    def _pr(rollup: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "state": "OPEN",
            "isDraft": False,
            "mergeable": "CLEAN",
            "labels": [],
            "comments": [],
            "body": "## Validación\nCI verde",
            "statusCheckRollup": rollup,
        }

    @staticmethod
    def _base_policy() -> dict[str, Any]:
        return json.loads((ROOT / "config" / "policies" / "base.json").read_text())

    def test_check_run_success_is_green(self) -> None:
        mod = self._module()
        rollup = [self._check_run("SUCCESS")]
        self.assertEqual(mod.check_states(rollup), ["SUCCESS"])
        self.assertIsNone(mod.ci_block_reason(rollup))

    def test_check_run_neutral_and_success_are_green(self) -> None:
        mod = self._module()
        rollup = [self._check_run("NEUTRAL"), self._check_run("SUCCESS")]
        self.assertEqual(mod.check_states(rollup), ["NEUTRAL", "SUCCESS"])
        self.assertIsNone(mod.ci_block_reason(rollup))

    def test_skipped_is_not_green(self) -> None:
        # Misma semántica que merge.sh: un check que no corrió no es un gate.
        mod = self._module()
        rollup = [self._check_run("SKIPPED")]
        self.assertEqual(mod.check_states(rollup), ["SKIPPED"])
        self.assertEqual(mod.ci_block_reason(rollup), "CI no está verde (UI A/B Summary=skipped (no corrió))")

    def test_skipped_plus_success_same_name_is_green(self) -> None:
        # x6200 #309: el workflow registra dos corridas por PR (la de `opened`
        # queda SKIPPED, la de `labeled` corre). Agregado por nombre: verde.
        mod = self._module()
        rollup = [self._check_run("SKIPPED"), self._check_run("SUCCESS")]
        self.assertEqual(mod.check_states(rollup), ["SKIPPED", "SUCCESS"])
        self.assertIsNone(mod.ci_block_reason(rollup))
        self.assertTrue(mod.name_verdict(["SKIPPED", "SUCCESS"])[0])

    def test_check_run_unfinished_is_pending(self) -> None:
        mod = self._module()
        for status in ("QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", "PENDING"):
            with self.subTest(status=status):
                rollup = [self._check_run(None, status=status)]
                self.assertEqual(mod.check_states(rollup), ["PENDING"])
                self.assertEqual(mod.ci_block_reason(rollup), "CI no está verde (UI A/B Summary=pending)")

    def test_check_run_bad_conclusions_are_not_green(self) -> None:
        mod = self._module()
        for conclusion in (
            "FAILURE",
            "CANCELLED",
            "TIMED_OUT",
            "ACTION_REQUIRED",
            "STALE",
            "STARTUP_FAILURE",
        ):
            with self.subTest(conclusion=conclusion):
                rollup = [self._check_run(conclusion)]
                self.assertEqual(mod.check_states(rollup), [conclusion])
                reason = mod.ci_block_reason(rollup)
                self.assertIsNotNone(reason)
                self.assertIn(conclusion.lower(), reason or "")
                # el diagnóstico muestra el estado real, no un PENDING genérico
                self.assertNotIn("pending", reason or "")

    def test_status_context_both_outcomes(self) -> None:
        mod = self._module()
        self.assertIsNone(mod.ci_block_reason([{"context": "coverage", "state": "SUCCESS"}]))
        self.assertIsNone(mod.ci_block_reason([{"context": "coverage", "state": "NEUTRAL"}]))
        for state in ("PENDING", "FAILURE", "ERROR"):
            with self.subTest(state=state):
                rollup = [{"context": "coverage", "state": state}]
                self.assertEqual(mod.check_states(rollup), [state])
                self.assertEqual(mod.ci_block_reason(rollup), f"CI no está verde (coverage={state.lower()})")

    def test_mixed_rollup_reports_only_bad_states(self) -> None:
        mod = self._module()
        rollup = [
            {"context": "coverage", "state": "SUCCESS"},
            self._check_run("SUCCESS"),
            self._check_run("FAILURE"),
            self._check_run(None, status="IN_PROGRESS"),
        ]
        self.assertEqual(mod.check_states(rollup), ["SUCCESS", "SUCCESS", "FAILURE", "PENDING"])
        # Un nombre con un fallo se reporta como fallo (no como pending): el
        # primer veredicto que bloquea es el que explica el rojo.
        self.assertEqual(mod.ci_block_reason(rollup), "CI no está verde (UI A/B Summary=failure)")

    def test_empty_rollup_blocks(self) -> None:
        mod = self._module()
        self.assertEqual(mod.check_states([]), [])
        self.assertIsNotNone(mod.ci_block_reason([]))

    def test_real_gh_check_run_payload_is_green(self) -> None:
        # forma exacta de `gh pr view 189 --repo iariap/x6200_gui --json statusCheckRollup`
        mod = self._module()
        rollup: list[dict[str, Any]] = [
            {
                "__typename": "CheckRun",
                "completedAt": "2026-10-21T15:53:19Z",
                "conclusion": "SUCCESS",
                "detailsUrl": "https://github.com/iariap/x6200_gui/actions/runs/39629477286/job/119323472310",
                "name": "UI Functional Tests",
                "startedAt": "2026-10-21T15:51:38Z",
                "status": "COMPLETED",
                "workflowName": "UI A/B testing harness",
            },
            {
                "__typename": "CheckRun",
                "completedAt": "2026-10-21T15:54:55Z",
                "conclusion": "SUCCESS",
                "detailsUrl": "https://github.com/iariap/x6200_gui/actions/runs/39629477286/job/119325253349",
                "name": "UI A/B Summary",
                "startedAt": "2026-10-21T15:54:46Z",
                "status": "COMPLETED",
                "workflowName": "UI A/B testing harness",
            },
        ]
        self.assertEqual(mod.check_states(rollup), ["SUCCESS", "SUCCESS"])
        self.assertIsNone(mod.ci_block_reason(rollup))

    def test_evaluate_eligible_with_actions_check_runs(self) -> None:
        """El gate completo acepta un PR low con CheckRuns verdes (antes: no-op)."""
        mod = self._module()
        eligible, reasons = mod.evaluate(
            self._pr([self._check_run("SUCCESS"), self._check_run("SKIPPED")]),
            ["docs/x.md"],
            self._base_policy(),
        )
        self.assertTrue(eligible, reasons)

    def test_evaluate_still_blocks_red_check_run(self) -> None:
        mod = self._module()
        eligible, reasons = mod.evaluate(
            self._pr([self._check_run("SUCCESS"), self._check_run("FAILURE")]),
            ["docs/x.md"],
            self._base_policy(),
        )
        self.assertFalse(eligible)
        self.assertTrue(any("CI no está verde" in r and "failure" in r for r in reasons), reasons)

    def test_evaluate_blocks_when_every_check_was_skipped(self) -> None:
        # Todos SKIPPED: ningún gate corrió, la vía rápida no aplica.
        mod = self._module()
        eligible, reasons = mod.evaluate(
            self._pr([self._check_run("SKIPPED")]),
            ["docs/x.md"],
            self._base_policy(),
        )
        self.assertFalse(eligible)
        self.assertTrue(any("skipped" in r for r in reasons), reasons)


CHECK_MATRIX_LIB = SCRIPTS / "lib" / "check_matrix.sh"


class TestCheckMatrixHelpers(unittest.TestCase):
    """Regresión de asf#47: un match no se pierde por SIGPIPE bajo pipefail."""

    def _run_lib(self, code: str, changed: str = "") -> subprocess.CompletedProcess[str]:
        # El input va por archivo (no por argv/env): el caso de regresión supera
        # el buffer del pipe y E2BIG si se pasa como argumento.
        with tempfile.TemporaryDirectory() as tmp:
            changed_file = Path(tmp) / "changed"
            changed_file.write_text(changed)
            return subprocess.run(
                [
                    "bash",
                    "-c",
                    'set -euo pipefail\nsource "$1"\nCHANGED="$(cat "$2")"\n' + code,
                    "bash",
                    str(CHECK_MATRIX_LIB),
                    str(changed_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_has_survives_pipefail_200_times(self) -> None:
        # >64KiB y match en la primera línea: grep -q sale enseguida, el productor
        # del pipe se bloquea y muere con SIGPIPE (el bug de asf#47).
        changed = "\n".join(f"src/file_{i:05d}.c" for i in range(8000))
        result = self._run_lib(
            """
falsos=0
for _ in $(seq 1 200); do
  has '^src/file_00000\\.c$' || falsos=$((falsos + 1))
done
echo "$falsos"
""",
            changed,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0", "has() devolvió falso con un match presente")

    def test_has_no_match_is_false(self) -> None:
        result = self._run_lib(
            "if has '^src/inexistente\\.c$'; then echo match; else echo sin-match; fi",
            "src/otro.c",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "sin-match")

    def test_skip_is_loud(self) -> None:
        result = self._run_lib('skip "firmware / nativo" "sin cambios en src/**"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("==> [SKIP] firmware / nativo: sin cambios en src/**", result.stdout)

    def test_no_check_matrix_pipes_into_grep_q(self) -> None:
        for script in sorted((SCRIPTS / "apps").glob("*/check-matrix.sh")):
            for lineno, line in enumerate(script.read_text().splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                with self.subTest(script=script.name, line=lineno):
                    self.assertNotRegex(line, r"\|\s*grep\s+-q")


class TestX6200CheckMatrixStaticChecks(unittest.TestCase):
    """asf#48: la matriz enumera TODOS los tests/check_*.py del repo de la app."""

    SCRIPT: ClassVar[Path] = SCRIPTS / "apps" / "x6200" / "check-matrix.sh"

    @staticmethod
    def _app_checks(checkout: Path, branch: str | None) -> set[str]:
        # El checkout local puede estar atrasado respecto de la rama que trackea
        # el puntero: si origin/<branch> existe, ese es el árbol del repo.
        #
        # Sólo cuentan los check_*.py de la RAÍZ de tests/: la rama de abajo (el
        # glob, no recursivo) y la de arriba tienen que mirar lo mismo, y un
        # runner anidado (p. ej. tests/release/check_types_rule.py, que recibe la
        # ruta del S02update como argumento y lo llama test_safe_update.sh) no es
        # un gate del repo ni se puede enumerar como `python3 tests/check_*.py`.
        if branch:
            tree = subprocess.run(
                [
                    "git",
                    "-C",
                    str(checkout),
                    "ls-tree",
                    "-r",
                    "--name-only",
                    f"origin/{branch}",
                    "--",
                    "tests",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if tree.returncode == 0:
                names = [Path(p).name for p in tree.stdout.splitlines() if Path(p).parent == Path("tests")]
                return {name for name in names if name.startswith("check_") and name.endswith(".py")}
        return {p.name for p in (checkout / "tests").glob("check_*.py")}

    def test_all_static_checks_are_enumerated(self) -> None:
        try:
            reg = app_config.load_registry("x6200")
        except SystemExit:
            self.skipTest("sin registry de x6200")
        checkout = Path(reg["checkout_abs"])
        if not (checkout / "tests").is_dir():
            self.skipTest(f"sin checkout de x6200 ({checkout}): clon fresco o CI")
        static = self._app_checks(checkout, reg.get("branch"))
        if not static:
            self.skipTest("el checkout no tiene tests/check_*.py")
        enumerated = set(re.findall(r"python3 tests/(check_[A-Za-z0-9_]+\.py)", self.SCRIPT.read_text()))
        missing = sorted(static - enumerated)
        self.assertFalse(missing, f"check-matrix.sh de x6200 no enumera: {', '.join(missing)}")


class TestX6200CheckMatrixAbGates(unittest.TestCase):
    """La matriz local espeja los gates de `x6200-ab` que corre el CI.

    El rojo de contratos y de temas llegaba al PR porque la matriz no corría los
    mismos gates que `.github/workflows/ab-testing.yml`; y el pytest del arnés
    A/B (que valida fixtures y waivers versionados) sólo corría si el diff tocaba
    `*.py`. Estos invariantes se fijan acá para que el espejo no se pierda sin
    que nadie lo note.
    """

    SCRIPT: ClassVar[Path] = SCRIPTS / "apps" / "x6200" / "check-matrix.sh"

    @classmethod
    def _trigger_for(cls, needle: str) -> str:
        """El ERE del bloque `has` que envuelve al comando `needle`.

        Se deriva del script (no se copia el literal) para que el test mida el
        trigger real: el más cercano hacia arriba que abre un bloque `if has`.
        Los comentarios no cuentan: el nombre del check se menciona ahí también.
        """
        lines = cls.SCRIPT.read_text().splitlines()
        index = next(
            (i for i, line in enumerate(lines) if needle in line and not line.lstrip().startswith("#")),
            None,
        )
        if index is None:
            raise AssertionError(f"{cls.SCRIPT.name} no corre `{needle}`")
        for line in reversed(lines[:index]):
            match = re.match(r"\s*if has '([^']+)'; then$", line)
            if match:
                return match.group(1)
        raise AssertionError(f"`{needle}` no está dentro de un bloque `has`")

    def _assert_trigger_covers(self, needle: str, paths: Iterable[str]) -> None:
        trigger = self._trigger_for(needle)
        for path in paths:
            with self.subTest(command=needle, path=path):
                self.assertRegex(
                    path,
                    trigger,
                    f"`{needle}` no dispara con {path} (trigger: {trigger})",
                )
        # Y que no dispare por cualquier cambio: un trigger amplio corre el
        # check de más (costo y ruido) y esconde qué paths lo justifican.
        with self.subTest(command=needle, path="docs/README.md"):
            self.assertNotRegex("docs/README.md", trigger, f"trigger demasiado amplio: {trigger}")

    def test_pytest_trigger_covers_the_versioned_inventory(self) -> None:
        # El suite valida el inventario versionado del arnés: los fixtures,
        # golden y waivers de tools/ab_testing/ y la lista EXACTA de fixtures de
        # tools/ui_harness/. Un fixture nuevo o borrado ahí invalida sus listas
        # esperadas y es rojo en el CI aunque el diff no tenga una línea de
        # Python: el PR #237 (fixture locked-feedback.scn) pasó la matriz local y
        # falló en el CI por esto.
        self._assert_trigger_covers(
            "pytest -m 'not e2e' (ab_testing)",
            [
                "tools/ab_testing/tests/test_scenarios.py",
                "tools/ab_testing/quality/theme_pairs.yaml",
                "tools/ui_harness/fixtures/locked-feedback.scn",
                "tools/ui_harness/mocks/mock_main_screen.c",
            ],
        )

    def test_static_inventory_checks_trigger_on_the_paths_they_read(self) -> None:
        # check_dialog_coverage.py ata tests/qemu/dialog-coverage.csv a los mocks
        # y fixtures de tools/ui_harness/ y a las fuentes de src/;
        # check_autotune_v2.py lee src/** y el inventario de fixtures/screens del
        # arnés. Un trigger que no cubra esos paths deja el check sin correr
        # justo cuando el cambio lo invalida.
        self._assert_trigger_covers(
            "check_dialog_coverage",
            [
                "tests/qemu/dialog-coverage.csv",
                "src/remote_server.cpp",
                "tools/ui_harness/mocks/mock_main_screen.c",
                "tools/ui_harness/fixtures/home-cb.scn",
            ],
        )
        self._assert_trigger_covers(
            "check_autotune_v2",
            [
                "src/autotune.c",
                "tests/native/test_autotune.c",
                "tools/ui_harness/src/screens.c",
                "tools/ui_harness/fixtures/autotune.scn",
            ],
        )

    def test_ab_workflow_gates_are_mirrored(self) -> None:
        text = self.SCRIPT.read_text()
        for command in ("uv run x6200-ab themes", "uv run x6200-ab contracts"):
            with self.subTest(command=command):
                self.assertIn(command, text, f"la matriz no corre `{command}` como el CI")

    def test_orphan_gates_of_the_github_workflows_are_mirrored(self) -> None:
        # Con los PRs sin GitHub Actions, cada gate que corría un workflow y no
        # está en la matriz queda huérfano (nadie lo corre). Se fijan el
        # comando real y el trigger de paths del workflow que lo corría.
        text = self.SCRIPT.read_text()
        for command in (
            "bash tests/check_bootanim.sh",
            "bash tests/check_drawbuf_partial.sh",
            "python3 tests/check_widget_text.py",
            "prune_guards",
        ):
            with self.subTest(command=command):
                self.assertIn(command, text, f"la matriz no ejecuta `{command}`")

    def test_bootanim_trigger_covers_the_workflow_paths(self) -> None:
        # boot-rootfs.yml (job `bootanim`): renderer/backend del arranque,
        # cesión del framebuffer, script de init e integración.
        self._assert_trigger_covers(
            "check_bootanim (arranque neo-retro",
            [
                "src/bootanim/renderer.c",
                "src/boot_splash.c",
                "src/boot_splash.h",
                "src/main.c",
                "src/CMakeLists.txt",
                "CMakeLists.txt",
                "tests/bootanim/fake_fb.c",
                "tests/check_bootanim.sh",
                "rootfs/etc/init.d/S01bootanim",
            ],
        )

    def test_drawbuf_trigger_covers_the_ab_workflow_paths(self) -> None:
        # El job `visual` de ab-testing.yml corre check_drawbuf_partial.sh con el
        # trigger de paths de ese workflow.
        self._assert_trigger_covers(
            "check_drawbuf_partial (draw buffer parcial, #147)",
            [
                "tools/ui_harness/probe/disp_buf_probe.c",
                "src/styles.c",
                "tests/check_drawbuf_partial.sh",
                "rootfs/usr/share/x6200/themes/sunlit.json",
                ".github/workflows/ab-testing.yml",
            ],
        )

    def test_prune_guards_trigger_covers_the_workflow_paths(self) -> None:
        # boot-rootfs.yml (job `prune`): el script del prune, el rootfs del port,
        # el install(CODE) de CMake que lo invoca y su propio workflow.
        self._assert_trigger_covers(
            "guards del prune del rootfs (boot-rootfs.yml)",
            [
                "buildroot/prune-vendor-rootfs.sh",
                "rootfs/etc/init.d/S95gui",
                "CMakeLists.txt",
                ".github/workflows/boot-rootfs.yml",
            ],
        )

    def test_widget_text_gate_shares_the_rendered_block_trigger(self) -> None:
        # check_widget_text.py (#287) mide la superficie renderizada igual que
        # check_wcag_rendered.py: mismo trigger y mismo venv (PIL + numpy).
        self._assert_trigger_covers(
            "check_widget_text (texto de los widgets",
            [
                "src/styles.c",
                "tools/ui_harness/src/screens.c",
                "rootfs/usr/share/x6200/themes/sunlit.json",
                "tests/check_widget_text.py",
            ],
        )


class TestX6200CheckMatrixCoverage(unittest.TestCase):
    """Un diff sin bloque no puede salir 0: sólo documentación queda sin gate.

    Antes la matriz salía 0 con todo `[SKIP]`: un cambio de código que ningún
    bloque cubría se leía como validación. Ahora se declara `[SIN GATE]` y, si
    hay cualquier path que no sea documentación, la matriz falla con exit 2.
    """

    SCRIPT: ClassVar[Path] = SCRIPTS / "apps" / "x6200" / "check-matrix.sh"

    def _repo(self, tmp: Path) -> tuple[Path, Path]:
        harness = _isolated_harness(tmp, app="x6200")
        checkout = harness / "work" / "x6200"
        git_env = dict(
            os.environ,
            GIT_AUTHOR_NAME="test",
            GIT_AUTHOR_EMAIL="test@example.invalid",
            GIT_COMMITTER_NAME="test",
            GIT_COMMITTER_EMAIL="test@example.invalid",
        )
        subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True, env=git_env)
        subprocess.run(
            ["git", "-C", str(checkout), "commit", "-q", "--allow-empty", "-m", "base"],
            check=True,
            env=git_env,
        )
        # El head del "PR" va en su propia rama: `main` queda en la base, como en
        # un PR real (si ambos commits cayeran en main, el diff sería vacío).
        subprocess.run(
            ["git", "-C", str(checkout), "checkout", "-q", "-b", "feature"],
            check=True,
            env=git_env,
        )
        self._git_env = git_env
        return harness, checkout

    def _commit(self, checkout: Path, files: dict[str, str]) -> None:
        for name, content in files.items():
            path = checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        subprocess.run(["git", "-C", str(checkout), "add", "-A"], check=True, env=self._git_env)
        subprocess.run(
            ["git", "-C", str(checkout), "commit", "-q", "-m", "head"],
            check=True,
            env=self._git_env,
        )

    def _run(self, harness: Path, checkout: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(harness / "scripts" / "apps" / "x6200" / "check-matrix.sh"),
                "main",
                "HEAD",
                str(checkout),
            ],
            capture_output=True,
            text=True,
            cwd=str(harness),
        )

    def test_docs_only_diff_without_a_block_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, checkout = self._repo(Path(tmp))
            self._commit(checkout, {"docs/x.md": "hola\n", "NOTES.txt": "n\n"})
            r = self._run(harness, checkout)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("==> [SIN GATE] ningún bloque cubre el diff:", r.stdout)
            self.assertIn("sólo documentación", r.stdout)

    def test_code_without_a_block_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, checkout = self._repo(Path(tmp))
            self._commit(checkout, {"lvgl/patched.c": "int x;\n"})
            r = self._run(harness, checkout)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("==> [SIN GATE] ningún bloque cubre el diff:", r.stdout)
            self.assertIn("hay paths sin gate en la matriz", r.stderr)
            self.assertIn("lvgl/patched.c", r.stderr)


class TestX6200CheckMatrixCheckoutPath(unittest.TestCase):
    """El checkout llega a los checks ABSOLUTO aunque la matriz reciba un path relativo.

    Medido: `check-matrix.sh <base> <head> state/worktrees/x6200/<slug>` le pasaba
    `"$APP"` tal cual a `tests/check_cfg_fills.py`, que hace
    `Path(sys.argv[1]).resolve()` DESDE el checkout (`run` hace `cd "$APP"`): el
    path se resolvía dos veces (`<checkout>/<path relativo>`) y el check moría con
    FileNotFoundError. El MISMO check pasaba con el path absoluto, así que el gate
    de merge (`merge.sh`, provider=none) fallaba por una razón espuria.

    La cobertura es de INTEGRACIÓN (no estática) porque el bug no se ve en el
    texto del script —`"$APP"` es correcto en `cd "$APP"`— sino en el cwd con el
    que el check resuelve su argumento: un repo fake mínimo con un
    `tests/check_cfg_fills.py` que falla si su argumento no resuelve reproduce la
    medición real. El resto de los checks de la matriz falla por falta de repo
    (no hay firmware): se asserta el bloque de `check_cfg_fills`, no el exit de la
    matriz.
    """

    SCRIPT: ClassVar[Path] = SCRIPTS / "apps" / "x6200" / "check-matrix.sh"

    # Falla si el argumento no resuelve desde el cwd del check: es lo que hacía
    # check_cfg_fills.py del repo real (`Path(sys.argv[1]).resolve()`).
    FAKE_CHECK = """#!/usr/bin/env python3
import pathlib
import sys

arg = pathlib.Path(sys.argv[1])
if not (arg / "src" / "cfg" / "cfg.h").is_file():
    print(f"FAIL: el argumento no resuelve desde el cwd del check: {sys.argv[1]}")
    sys.exit(1)
print(f"ok: {sys.argv[1]}")
"""

    def _repo(self, tmp: Path) -> tuple[Path, Path]:
        harness = _isolated_harness(tmp, app="x6200")
        checkout = harness / "work" / "x6200"
        git_env = _git_test_env()
        subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True, env=git_env)
        for name, content in {
            "tests/check_cfg_fills.py": self.FAKE_CHECK,
            "src/cfg/cfg.h": "typedef struct { int x; } cfg_t;\n",
            "src/cfg/cfg.c": "int x = 0;\n",
        }.items():
            path = checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(checkout), "add", "-A"], check=True, env=git_env)
        subprocess.run(
            ["git", "-C", str(checkout), "commit", "-q", "-m", "base"],
            check=True,
            env=git_env,
        )
        # El head del "PR" va en su propia rama: `main` queda en la base (si ambos
        # commits cayeran en main, el diff sería vacío y ningún bloque correría).
        subprocess.run(
            ["git", "-C", str(checkout), "checkout", "-q", "-b", "feature"],
            check=True,
            env=git_env,
        )
        (checkout / "src" / "cfg" / "cfg.c").write_text("int x = 1;\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(checkout), "add", "-A"], check=True, env=git_env)
        subprocess.run(
            ["git", "-C", str(checkout), "commit", "-q", "-m", "head"],
            check=True,
            env=git_env,
        )
        return harness, checkout

    def _run(self, harness: Path, checkout_arg: str) -> subprocess.CompletedProcess[str]:
        # cwd = raíz del harness: un path relativo se resuelve contra ahí, que es
        # como lo invoca el factory desde la raíz del harness.
        return subprocess.run(
            [
                "bash",
                str(harness / "scripts" / "apps" / "x6200" / "check-matrix.sh"),
                "main",
                "HEAD",
                checkout_arg,
            ],
            capture_output=True,
            text=True,
            cwd=str(harness),
        )

    def test_relative_checkout_resolves_inside_the_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, _ = self._repo(Path(tmp))
            r = self._run(harness, "work/x6200")
            self.assertIn("==> [check] check_cfg_fills\n    ok", r.stdout, r.stdout + r.stderr)

    def test_absolute_checkout_still_resolves(self) -> None:
        # Control: el path absoluto ya funcionaba; el fix no puede romperlo.
        with tempfile.TemporaryDirectory() as tmp:
            harness, checkout = self._repo(Path(tmp))
            r = self._run(harness, str(checkout))
            self.assertIn("==> [check] check_cfg_fills\n    ok", r.stdout, r.stdout + r.stderr)

    def test_no_check_receives_the_checkout_verbatim(self) -> None:
        # El caso de integración cubre `check_cfg_fills`; esto impide que la MISMA
        # clase de error reaparezca en otra invocación (el prefijo de PATH de los
        # gates renderizados también se resolvía desde el cwd del check).
        for lineno, line in enumerate(self.SCRIPT.read_text().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            with self.subTest(line=lineno):
                if re.match(r"\s*run(_ab|_ab_or_blocked)?\s", line):
                    self.assertNotIn('"$APP"', line, f"línea {lineno}: un check recibe el checkout crudo")
                self.assertNotIn('PATH="$APP/', line, f"línea {lineno}: PATH relativo al cwd del check")


class TestX6200CheckMatrixWorkflows(unittest.TestCase):
    """Un diff que sólo toca `.github/workflows/**` tiene gate (no [SIN GATE]).

    Antes ningún bloque cubría un workflow y la matriz salía 2: el YAML roto o
    el shell embebido inválido de un `run:` llegaban al merge sin validar. El
    bloque nuevo parsea el YAML y corre `bash -n` sobre cada `run:` con las
    expresiones `${{ ... }}` neutralizadas; sin pyyaml se declara BLOCKED, nunca
    PASS.
    """

    SCRIPT: ClassVar[Path] = SCRIPTS / "apps" / "x6200" / "check-matrix.sh"

    def _repo(self, tmp: Path) -> tuple[Path, Path]:
        harness = _isolated_harness(tmp, app="x6200")
        checkout = harness / "work" / "x6200"
        git_env = _git_test_env()
        subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True, env=git_env)
        subprocess.run(
            ["git", "-C", str(checkout), "commit", "-q", "--allow-empty", "-m", "base"],
            check=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "checkout", "-q", "-b", "feature"],
            check=True,
            env=git_env,
        )
        self._git_env = git_env
        return harness, checkout

    def _commit(self, checkout: Path, files: dict[str, str]) -> None:
        for name, content in files.items():
            path = checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(checkout), "add", "-A"], check=True, env=self._git_env)
        subprocess.run(
            ["git", "-C", str(checkout), "commit", "-q", "-m", "head"],
            check=True,
            env=self._git_env,
        )

    def _run(
        self, harness: Path, checkout: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(harness / "scripts" / "apps" / "x6200" / "check-matrix.sh"),
                "main",
                "HEAD",
                str(checkout),
            ],
            capture_output=True,
            text=True,
            cwd=str(harness),
            env=env,
        )

    WORKFLOW = """name: main
on:
  push:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: echo
        run: |
          echo "sha=${{ github.sha }}"
          if [ -n "${{ github.ref }}" ]; then
            echo ok
          fi
"""

    def test_workflow_only_diff_runs_the_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, checkout = self._repo(Path(tmp))
            self._commit(checkout, {".github/workflows/main.yml": self.WORKFLOW})
            r = self._run(harness, checkout)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(
                "==> [check] workflows: YAML parsea + bash -n del shell embebido",
                r.stdout,
            )
            self.assertIn("    ok", r.stdout)
            self.assertNotIn("[SIN GATE]", r.stdout)
            self.assertNotIn("hay paths sin gate", r.stderr)

    def test_broken_embedded_shell_fails(self) -> None:
        # `run: |` sin `fi`: el YAML parsea, el shell no.
        workflow = """name: main
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: |
          if [ -n "x" ]; then
            echo roto
"""
        with tempfile.TemporaryDirectory() as tmp:
            harness, checkout = self._repo(Path(tmp))
            self._commit(checkout, {".github/workflows/main.yml": workflow})
            r = self._run(harness, checkout)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("FAIL:", r.stdout)
            self.assertIn("bash -n del shell embebido", r.stdout)
            self.assertNotIn("[SIN GATE]", r.stdout)

    def test_broken_yaml_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, checkout = self._repo(Path(tmp))
            self._commit(checkout, {".github/workflows/main.yml": "jobs: [sin-cerrar\n"})
            r = self._run(harness, checkout)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("FAIL:", r.stdout)
            self.assertIn("no parsea como YAML", r.stdout)
            self.assertNotIn("[SIN GATE]", r.stdout)

    def test_non_bash_shell_step_is_not_checked_as_shell(self) -> None:
        # Un step con `shell: python` no es bash: validarlo con bash -n sería un
        # FAIL falso del workflow (y el runner sí lo ejecuta).
        workflow = """name: main
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - shell: python
        run: |
          import sys
          print(sys.version)
"""
        with tempfile.TemporaryDirectory() as tmp:
            harness, checkout = self._repo(Path(tmp))
            self._commit(checkout, {".github/workflows/main.yml": workflow})
            r = self._run(harness, checkout)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("    ok", r.stdout)
            self.assertNotIn("[SIN GATE]", r.stdout)

    def test_missing_pyyaml_is_blocked_not_pass(self) -> None:
        # python3 sin pyyaml: no se puede validar el YAML. Se declara BLOCKED con
        # el motivo; nunca "ok" (no poder chequear no es un pase).
        with tempfile.TemporaryDirectory() as tmp:
            harness, checkout = self._repo(Path(tmp))
            self._commit(checkout, {".github/workflows/main.yml": self.WORKFLOW})
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            fake_python = bin_dir / "python3"
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "$1" = "-c" ] && [ "$2" = "import yaml" ]; then\n'
                "  exit 1\n"
                "fi\n"
                f'exec "{sys.executable}" "$@"\n'
            )
            fake_python.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            r = self._run(harness, checkout, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("BLOCKED: falta entorno para validar el YAML", r.stdout)
            self.assertIn("no es PASS", r.stdout)
            self.assertNotIn("[SIN GATE]", r.stdout)


class TestRegistryAliases(unittest.TestCase):
    """Los aliases del registry resuelven a la app canónica."""

    def test_alias_resolves(self) -> None:
        expected = {"mi-receta": ("mi-mesa", "iariap/mi-mesa-recetario"), "harness": ("asf", "iariap/asf")}
        for alias, (canon, repo) in expected.items():
            if alias == "mi-receta" and not MI_MESA_RULES:
                continue
            with self.subTest(alias=alias):
                reg = app_config.load_registry(alias)
                self.assertEqual(reg["id"], canon)
                self.assertEqual(reg["repo"], repo)

    def test_policy_via_alias(self) -> None:
        if not MI_MESA_RULES:
            self.skipTest("sin checkout de mi-mesa")
        pol = app_config.merged_policy("mi-receta")
        got = risk.classify(["backend/src/omavu/domain.py"], pol)
        self.assertEqual(got["risk"], "high")
        self.assertTrue(got["human_approval"])

    def test_asf_governance_paths_high(self) -> None:
        got = risk.classify(
            [
                "config/policies/base.json",
                "scripts/auto-merge.py",
                ".pi/settings.json",
                ".pi/agents/coder.md",
                ".pi/extensions/asf-subagents.ts",
            ],
            app_config.merged_policy("asf"),
        )
        self.assertEqual(got["risk"], "high")
        self.assertTrue(got["human_approval"])
        low = risk.classify(["README.md"], app_config.merged_policy("asf"))
        self.assertEqual(low["risk"], "low")

    def test_metadata_in_repo_overlay(self) -> None:
        """La metadata del harness (asf) se resuelve desde SU .factory/, no del puntero."""
        reg = app_config.load_registry("asf")
        pointer = json.loads((ROOT / "config" / "apps" / "asf.json").read_text())
        # el puntero ya no declara aliases/description: vienen del overlay del repo
        self.assertNotIn("aliases", pointer)
        self.assertEqual(reg["aliases"], ["harness"])
        self.assertTrue(reg.get("description"))
        # campos de autoridad del puntero
        self.assertEqual(reg["repo"], "iariap/asf")
        self.assertEqual(reg["checkout_abs"], str(ROOT))

    def test_factory_change_never_low(self) -> None:
        """Tocar .factory/** (metadata/policy de la app) no puede clasificar low."""
        for app in ("mi-mesa", "asf"):
            with self.subTest(app=app):
                got = risk.classify([".factory/policy.json"], app_config.merged_policy(app))
                self.assertNotEqual(got["risk"], "low")

    def test_x6200_pointer_resolves(self) -> None:
        """El puntero de x6200 resuelve repo/branch/checkout (ROM, sin entornos)."""
        reg = app_config.load_registry("x6200")
        self.assertEqual(reg["id"], "x6200")
        self.assertEqual(reg["repo"], "iariap/x6200_gui")
        self.assertEqual(reg["branch"], "main")
        self.assertEqual(reg["checkout_abs"], str(ROOT / "work" / "x6200_gui"))

    def test_x6200_policy_fallback_without_checkout(self) -> None:
        """Sin checkout sincronizado, merged_policy('x6200') cae al fallback del harness."""
        checkout = ROOT / "work" / "x6200_gui"
        if (checkout / ".factory" / "policy.json").exists():
            self.skipTest("checkout de x6200 sincronizado: gana el overlay, no el fallback")
        pol = app_config.merged_policy("x6200")
        # La regla del framework A/B solo vive en config/policies/x6200.json: si
        # aparece, el fallback se cargó (la base no la tiene).
        self.assertIn("tools/ab_testing/**", {r["pattern"] for r in pol["rules"]})
        firmware = risk.classify(["src/main_screen.c"], pol)
        self.assertEqual(firmware["risk"], "high")
        self.assertTrue(firmware["human_approval"])
        self.assertEqual(risk.classify(["tools/ab_testing/src/x6200_ab/cli.py"], pol)["risk"], "low")
        self.assertNotEqual(risk.classify([".factory/policy.json"], pol)["risk"], "low")


class TestCiProviderConfig(unittest.TestCase):
    """`ci.provider` configurable por app: `github` (default) | `none`.

    `none` no degrada a verde: los consumidores lo leen para no esperar un CI
    que no existe y exigir la matriz local en su lugar. Un valor desconocido
    cae a `github` (fail-closed): un typo no deshabilita el CI por accidente.
    """

    def _sh(self, harness: Path, app: str = "fakeapp") -> dict[str, str]:
        r = subprocess.run(
            [sys.executable, str(harness / "scripts" / "app_config.py"), app, "--sh"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        out: dict[str, str] = {}
        for line in r.stdout.splitlines():
            key, sep, val = line.partition("=")
            if sep:
                out[key] = val.strip("'")
        return out

    def test_default_is_github(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = _isolated_harness(Path(tmp))
            self.assertEqual(self._sh(harness)["APP_CI_PROVIDER"], "github")

    def test_pointer_declares_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = _isolated_harness(Path(tmp), pointer={"ci": {"provider": "none"}})
            self.assertEqual(self._sh(harness)["APP_CI_PROVIDER"], "none")

    def test_overlay_wins_over_pointer(self) -> None:
        # El overlay del repo de la app gana, como el resto de la metadata.
        with tempfile.TemporaryDirectory() as tmp:
            harness = _isolated_harness(Path(tmp), pointer={"ci": {"provider": "github"}})
            meta = harness / "work" / "fakeapp" / ".factory"
            meta.mkdir()
            (meta / "app.json").write_text(json.dumps({"ci": {"provider": "none"}}))
            self.assertEqual(self._sh(harness)["APP_CI_PROVIDER"], "none")

    def test_invalid_provider_falls_back_to_github(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = _isolated_harness(Path(tmp), pointer={"ci": {"provider": "circleci"}})
            self.assertEqual(self._sh(harness)["APP_CI_PROVIDER"], "github")

    def test_public_helper_matches_the_registry(self) -> None:
        # Los consumidores Python usan app_config.ci_provider(app), no el JSON.
        self.assertEqual(app_config.ci_provider("asf"), "github")
        self.assertEqual(app_config.ci_provider("x6200"), "none")

    def test_x6200_pointer_declares_local_matrix(self) -> None:
        pointer = json.loads((ROOT / "config" / "apps" / "x6200.json").read_text())
        self.assertEqual(pointer.get("ci"), {"provider": "none"})
        self.assertIn("matriz local", pointer.get("note", ""))


class TestBabysitAntiLoop(unittest.TestCase):
    def test_antiloop_same_sha_same_exit(self) -> None:
        """Mismo head SHA + mismo exit != 0 previo → rechaza el reintento."""
        sha = "a" * 40
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "babysit.json"
            state.write_text(json.dumps({"head_sha": sha, "mergeable": "MERGEABLE", "checks": "", "exit": 2}))
            fake_gh = Path(tmp) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'if [[ "$*" == *--json* ]]; then\n'
                '  if [[ "$*" == *headRefOid* ]]; then\n'
                "    echo '" + sha + "'\n"
                "  else\n"
                "    echo 'MERGEABLE'\n"
                "  fi\n"
                "  exit 0\n"
                "fi\n"
                "echo '{}'\n"
            )
            fake_gh.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{tmp}:{env['PATH']}"
            env.pop("MI_MESA_REPO", None)
            result = subprocess.run(
                ["bash", str(SCRIPTS / "babysit.sh"), "mi-mesa", "1", "--state", str(state)],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("anti-loop", result.stderr)


class TestProviderNoneConsumers(unittest.TestCase):
    """Consumidores con `ci.provider: none`: no esperan un CI que no existe.

    babysit.sh y ci-status.sh no tienen checks remotos que leer, así que NO
    pueden decir "verde": reportan el estado del PR (babysit sale 4 si no es
    OPEN/MERGEABLE/no draft) y declaran que la validación es la matriz local,
    que corre merge.sh. auto-merge.py no puede ser elegible (la vía rápida exige
    CI verde) y nunca mergea. `none` no se convierte en un verde silencioso.
    """

    HEAD = "a" * 40

    FAKE_GH = """#!/usr/bin/env bash
set -u
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr view")
    cat "$BODY_JSON"
    exit 0
    ;;
  "pr diff")
    printf 'diff --git a/docs/x.md b/docs/x.md\\n+++ b/docs/x.md\\n+hola\\n'
    exit 0
    ;;
  "pr merge")
    echo merged > "$MERGE_LOG"
    exit 0
    ;;
esac
echo "fake gh: args no manejados: $*" >&2
exit 1
"""

    def _fixture(
        self,
        tmp: Path,
        pointer: dict[str, Any],
        *,
        mergeable: str = "MERGEABLE",
        state: str = "OPEN",
        draft: bool = False,
    ) -> tuple[Path, dict[str, str]]:
        harness = _isolated_harness(tmp, pointer=pointer)
        state_dir = tmp / "ghstate"
        state_dir.mkdir()
        (state_dir / "pr.json").write_text(
            json.dumps(
                {
                    "number": 7,
                    "state": state,
                    "isDraft": draft,
                    "labels": [],
                    "comments": [],
                    "body": "## Validación\ncheck-matrix ok",
                    "headRefOid": self.HEAD,
                    "baseRefOid": self.HEAD,
                    "mergeable": mergeable,
                    "statusCheckRollup": [{"state": "SUCCESS"}],
                }
            )
        )
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "gh"
        fake.write_text(self.FAKE_GH)
        fake.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["BODY_JSON"] = str(state_dir / "pr.json")
        env["GH_LOG"] = str(tmp / "gh.log")
        env["MERGE_LOG"] = str(tmp / "merged.log")
        return harness, env

    def _run(
        self, harness: Path, env: dict[str, str], script: str, *args: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                *([sys.executable] if script.endswith(".py") else ["bash"]),
                str(harness / "scripts" / script),
                *args,
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(harness),
        )

    def test_babysit_validates_the_pr_and_exits_zero_without_waiting(self) -> None:
        # Sin checks remotos no hay "verde": se valida el PR y se declara que el
        # gate es la matriz local (la corre merge.sh).
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), {"ci": {"provider": "none"}})
            r = self._run(harness, env, "babysit.sh", "fakeapp", "7")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("CI de GitHub deshabilitado para fakeapp (provider=none)", r.stdout)
            self.assertIn("la validación es la matriz local", r.stdout)
            self.assertIn("==> PR #7 state=OPEN mergeable=MERGEABLE draft=false", r.stdout)
            self.assertTrue(Path(env["GH_LOG"]).exists(), "babysit.sh no miró el PR")

    def test_babysit_conflicting_pr_exits_4(self) -> None:
        # Un PR con conflictos no puede salir "verde" por no tener CI remoto.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), {"ci": {"provider": "none"}}, mergeable="CONFLICTING")
            r = self._run(harness, env, "babysit.sh", "fakeapp", "7")
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertIn("no es mergeable (mergeable=CONFLICTING)", r.stderr)

    def test_babysit_draft_pr_exits_4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), {"ci": {"provider": "none"}}, draft=True)
            r = self._run(harness, env, "babysit.sh", "fakeapp", "7")
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertIn("está en draft", r.stderr)

    def test_ci_status_reports_the_pr_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), {"ci": {"provider": "none"}})
            r = self._run(harness, env, "ci-status.sh", "fakeapp", "7")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(
                "CI de GitHub deshabilitado para fakeapp (provider=none): la validación es la matriz local",
                r.stdout,
            )
            self.assertIn("==> PR #7 (fakeapp)", r.stdout)
            self.assertIn('"mergeable": "MERGEABLE"', r.stdout)
            self.assertTrue(Path(env["GH_LOG"]).exists(), "ci-status.sh no miró el PR")

    def test_ci_status_without_ref_is_a_usage_error(self) -> None:
        # `ci-status.sh x6200` (sin ref) no puede salir 0 por ser provider=none.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), {"ci": {"provider": "none"}})
            r = self._run(harness, env, "ci-status.sh", "fakeapp")
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("uso:", r.stderr)
            self.assertNotIn("CI de GitHub deshabilitado", r.stdout)
            self.assertFalse(Path(env["GH_LOG"]).exists(), "llamó a gh sin ref")

    def test_auto_merge_blocks_and_never_merges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), {"ci": {"provider": "none"}, "policy": "fakeapp"})
            (harness / "config" / "policies" / "fakeapp.json").write_text(
                json.dumps({"auto_merge": {"mode": "enabled"}})
            )
            r = self._run(harness, env, "auto-merge.py", "fakeapp", "7", "--apply")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            verdict = json.loads(r.stdout)
            self.assertFalse(verdict["eligible"])
            self.assertEqual(verdict["ci_provider"], "none")
            self.assertIn(
                "CI de GitHub deshabilitado (provider=none): la vía rápida necesita CI verde",
                str(verdict["bloqueos"]),
            )
            self.assertFalse(Path(env["MERGE_LOG"]).exists(), "auto-merge mergeó sin CI de GitHub")


class TestPublishRisk(unittest.TestCase):
    def test_requires_token(self) -> None:
        env = dict(os.environ)
        env.pop("GITHUB_TOKEN", None)
        result = subprocess.run(
            ["bash", str(SCRIPTS / "publish-risk.sh"), "mi-mesa", "1"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("PENDIENTE", result.stderr)


class TestCloudMonitoring(unittest.TestCase):
    def test_aggregates(self) -> None:
        """Con gcloud fake: success rate, percentiles y severidad, sin payloads."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_gcloud = Path(tmp) / "gcloud"
            fake_gcloud.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                "  *request_count*)\n"
                '    echo \'[{"metric":{"labels":{"response_code_class":"200"}},"points":[{"value":{"int64Value":"100"}}]},{"metric":{"labels":{"response_code_class":"500"}},"points":[{"value":{"int64Value":"5"}}]}]\'\n'
                "    ;;\n"
                "  *request_latencies*)\n"
                '    echo \'[{"points":[{"value":{"distributionValue":{"bucketCounts":[10,20,70],"explicitBounds":{"bounds":[0.1,0.5,1.0]}}}}]}]\'\n'
                "    ;;\n"
                "  *logging*)\n"
                "    printf 'INFO\\nERROR\\nINFO\\n'\n"
                "    ;;\n"
                "esac\n"
            )
            fake_gcloud.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{tmp}:{env['PATH']}"
            env["GCP_PROJECT"] = "test"
            env["HOURS"] = "24"
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "cloud_monitoring.py")],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0)
            data = json.loads(result.stdout)
            self.assertEqual(data["status"], "OK")
            self.assertAlmostEqual(data["success_rate_pct"], 95.24, places=1)
            self.assertIn("p95_ms", data["latency"])
            self.assertEqual(data["logs_by_severity"], {"INFO": 2, "ERROR": 1})


class TestCleanupWorktrees(unittest.TestCase):
    """cleanup.sh: shadow lista sin borrar; --apply borra sólo los stale.

    Cubre worktrees de review (`pr-<n>`) y de build (`<slug>`): para los build
    la clasificación pregunta por el PR de `factory/<slug>` y sólo cae al
    `show-ref`/`merge-base` si el PR no existe.
    """

    @unittest.skipUnless(MI_MESA_RULES, "requiere registry de mi-mesa")
    def test_shadow_and_apply(self) -> None:
        import shutil

        app = "mi-mesa"
        wt_dir = ROOT / "state" / "worktrees" / app
        stale = wt_dir / "pr-9999991"
        active = wt_dir / "pr-9999992"
        build_stale = wt_dir / "b-9999991"
        build_active = wt_dir / "b-9999992"
        dirs = (stale, active, build_stale, build_active)
        try:
            for d in dirs:
                d.mkdir(parents=True, exist_ok=True)
                (d / "marker.txt").write_text("x")
            with tempfile.TemporaryDirectory() as tmp:
                fake_gh = Path(tmp) / "gh"
                fake_gh.write_text(
                    "#!/usr/bin/env bash\n"
                    'case "$*" in\n'
                    "  *9999991*) echo 'MERGED' ;;\n"
                    "  *) echo 'OPEN' ;;\n"
                    "esac\n"
                )
                fake_gh.chmod(0o755)
                env = dict(os.environ)
                env["PATH"] = f"{tmp}:{env['PATH']}"
                # shadow: lista stale, no borra
                r1 = subprocess.run(
                    ["bash", str(SCRIPTS / "cleanup.sh"), app],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(r1.returncode, 0, r1.stderr)
                self.assertIn("stale: pr-9999991", r1.stdout)
                self.assertIn("activo: pr-9999992", r1.stdout)
                self.assertIn("stale: b-9999991", r1.stdout)
                self.assertIn("activo: b-9999992", r1.stdout)
                self.assertIn("modo: shadow", r1.stdout)
                for d in dirs:
                    self.assertTrue(d.exists())
                # --apply: borra los stale, deja los activos
                r2 = subprocess.run(
                    ["bash", str(SCRIPTS / "cleanup.sh"), app, "--apply"],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(r2.returncode, 0, r2.stderr)
                self.assertFalse(stale.exists())
                self.assertFalse(build_stale.exists())
                self.assertTrue(active.exists())
                self.assertTrue(build_active.exists())
        finally:
            for d in dirs:
                shutil.rmtree(d, ignore_errors=True)

    @unittest.skipUnless(MI_MESA_RULES, "requiere registry de mi-mesa")
    def test_marker_skips_active_run(self) -> None:
        """El incidente de hoy: una corrida EN VUELO (rama sin PR, sin merge, con
        trabajo sin commitear) NO se toca, ni con `--apply`. Sin marker, el
        worktree se sigue recolectando como siempre.
        """
        import shutil

        app = "mi-mesa"
        wt_dir = ROOT / "state" / "worktrees" / app
        active = wt_dir / "m-9999991"  # corrida en vuelo: marcada
        stale = wt_dir / "m-9999992"  # sin marker: stale como antes
        dirs = (active, stale)
        try:
            for d in dirs:
                d.mkdir(parents=True, exist_ok=True)
                (d / "sin-commit.txt").write_text("trabajo de un coder en vuelo")
            (active / ".asf-active").touch()
            with tempfile.TemporaryDirectory() as tmp:
                fake_gh = Path(tmp) / "gh"
                # Sin PR para la rama: exactamente el caso que se borró de más.
                fake_gh.write_text("#!/usr/bin/env bash\nexit 1\n")
                fake_gh.chmod(0o755)
                env = dict(os.environ)
                env["PATH"] = f"{tmp}:{env['PATH']}"
                # shadow: la corrida en vuelo se lista como activa, no como stale.
                r1 = subprocess.run(
                    ["bash", str(SCRIPTS / "cleanup.sh"), app],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(r1.returncode, 0, r1.stderr)
                self.assertIn("activo: m-9999991 (marker .asf-active)", r1.stdout)
                self.assertIn("stale: m-9999992", r1.stdout)
                # --apply: ídem — la marcada sobrevive con su trabajo sin commitear.
                r2 = subprocess.run(
                    ["bash", str(SCRIPTS / "cleanup.sh"), app, "--apply"],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(r2.returncode, 0, r2.stderr)
                self.assertIn("activo: m-9999991 (marker .asf-active)", r2.stdout)
                self.assertTrue(active.is_dir(), "cleanup borró una corrida activa")
                self.assertTrue((active / ".asf-active").is_file())
                self.assertTrue((active / "sin-commit.txt").is_file(), "se perdió trabajo en vuelo")
                self.assertFalse(stale.exists())
        finally:
            for d in dirs:
                shutil.rmtree(d, ignore_errors=True)


class TestCleanupMarkerLifecycle(unittest.TestCase):
    """Ciclo de vida del marker con un harness fake (sin red): new-worktree.sh
    marca -> merge.sh --apply desmarca -> cleanup.sh --apply recolecta.

    Y la regla dura del incidente: `cleanup.sh --apply` NO remueve un worktree
    marcado aunque su rama ya figure MERGED (el merge todavía puede estar
    publicando; la corrida sigue viva hasta que merge.sh la desmarca).
    """

    HEAD = "c" * 40
    MERGE = "d" * 40

    FAKE_GH = """#!/usr/bin/env bash
# fake gh para TestCleanupMarkerLifecycle
case "$1 $2" in
  "pr view")
    case "$*" in
      *"--json state"*)
        case "$*" in
          *factory/wt-nopr*) echo "no pull requests found for branch" >&2; exit 1 ;;
        esac
        echo MERGED
        exit 0
        ;;
      *mergeCommit*) printf '{"mergeCommit":{"oid":"%s"},"headRefOid":"%s"}\\n' "$MERGE_SHA" "$HEAD_SHA"; exit 0 ;;
      *statusCheckRollup*) printf '{"statusCheckRollup":[{"name":"checks","status":"COMPLETED","conclusion":"SUCCESS"}]}\\n'; exit 0 ;;
    esac
    printf '{"number":77,"state":"OPEN","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","headRefOid":"%s","headRefName":"factory/wt-test","baseRefOid":"%s"}\\n' "$HEAD_SHA" "$HEAD_SHA"
    exit 0
    ;;
  "pr checks")
    printf 'checks\\tpass\\t20s\\thttps://example/checks\\n'
    exit 0
    ;;
  "pr merge")
    echo merged > "$MERGE_LOG"
    exit 0
    ;;
esac
echo "fake gh: args no manejados: $*" >&2
exit 1
"""

    def _setup(self, tmp: Path) -> dict[str, str]:
        """Harness fake en tmp (seed repo + gh fake + env); devuelve el env."""
        seed = _fake_harness(tmp)
        sha = subprocess.run(
            ["git", "-C", str(seed), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        fake_gh = tmp / "gh"
        fake_gh.write_text(self.FAKE_GH)
        fake_gh.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{tmp}:{env['PATH']}"
        env["HEAD_SHA"] = sha
        env["MERGE_SHA"] = self.MERGE
        env["MERGE_LOG"] = str(tmp / "merged.log")
        return env

    def _run(self, tmp: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(tmp / "scripts" / args[0]), *args[1:]],
            capture_output=True,
            text=True,
            cwd=tmp,
            env=env,
        )

    def test_marker_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            env = self._setup(tmp)

            wt = tmp / "state" / "worktrees" / "fakeapp" / "wt-test"
            marker = wt / ".asf-active"
            # La corrida del incidente: rama sin PR (NOT_FOUND), marcada.
            wt_nopr = tmp / "state" / "worktrees" / "fakeapp" / "wt-nopr"
            marker_nopr = wt_nopr / ".asf-active"
            base = tmp / "work" / "fakeapp"

            # 1) la corrida nace marcada.
            r1 = self._run(tmp, env, "new-worktree.sh", "fakeapp", "wt-test")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertTrue(marker.is_file(), "la corrida no quedó marcada")
            r1b = self._run(tmp, env, "new-worktree.sh", "fakeapp", "wt-nopr")
            self.assertEqual(r1b.returncode, 0, r1b.stderr)
            self.assertTrue(marker_nopr.is_file())

            # 2) MERGED pero viva: cleanup --apply la lista activa y no la toca.
            r2 = self._run(tmp, env, "cleanup.sh", "fakeapp", "--apply")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertIn("activo: wt-test (marker .asf-active)", r2.stdout)
            self.assertTrue(wt.is_dir(), "cleanup borró la corrida activa")
            self.assertTrue(marker.is_file())
            # La rama sin PR (el caso exacto del incidente) tampoco se toca.
            self.assertIn("activo: wt-nopr (marker .asf-active)", r2.stdout)
            self.assertTrue(wt_nopr.is_dir(), "cleanup borró una corrida con rama sin PR")
            self.assertTrue(marker_nopr.is_file())
            self.assertIn(
                str(wt),
                subprocess.run(
                    ["git", "-C", str(base), "worktree", "list", "--porcelain"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout,
            )

            # 3) shadow de merge.sh: valida sin mergear y sin desmarcar.
            r3 = self._run(tmp, env, "merge.sh", "fakeapp", "factory/wt-test")
            self.assertEqual(r3.returncode, 0, r3.stderr)
            self.assertIn("ok:", r3.stdout)
            self.assertTrue(marker.is_file(), "el shadow no debe desmarcar")
            self.assertFalse(Path(env["MERGE_LOG"]).exists())

            # 4) merge.sh --apply: fusiona y retira el marker (sin borrar el worktree).
            r4 = self._run(tmp, env, "merge.sh", "fakeapp", "factory/wt-test", "--apply")
            self.assertEqual(r4.returncode, 0, r4.stderr)
            self.assertIn(f"merged: 77 {self.MERGE}", r4.stdout)
            self.assertTrue(Path(env["MERGE_LOG"]).exists())
            self.assertFalse(marker.exists(), "merge.sh --apply no retiró el marker")
            self.assertTrue(wt.is_dir(), "merge.sh no debe borrar el worktree")
            # El detalle va a stderr: stdout queda con el contrato `merged:`.
            self.assertIn("marker de corrida activa retirado", r4.stderr)
            self.assertNotIn("marker", r4.stdout)
            # merge-when-green.sh captura 2>&1 y corta con `${out##*merged: }`:
            # el detalle previo en stderr no debe romper ese parseo.
            parse_tail = (r4.stderr + r4.stdout).rsplit("merged: ", 1)[1]
            self.assertEqual(parse_tail.split()[-1], self.MERGE)

            # 5) el cleanup del próximo round la recolecta (ya sin marker), y
            #    sigue sin tocar la corrida que no mergeó (rama sin PR).
            r5 = self._run(tmp, env, "cleanup.sh", "fakeapp", "--apply")
            self.assertEqual(r5.returncode, 0, r5.stderr)
            self.assertIn("stale: wt-test", r5.stdout)
            self.assertFalse(wt.exists(), "el cleanup post-merge no recolectó el worktree")
            self.assertIn("activo: wt-nopr (marker .asf-active)", r5.stdout)
            self.assertTrue(wt_nopr.is_dir(), "el cleanup se llevó una corrida sin PR")

    def test_merge_without_marker_is_silent(self) -> None:
        """El marker vive por corrida: si no existe, merge.sh no se queja."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            env = self._setup(tmp)
            r1 = self._run(tmp, env, "new-worktree.sh", "fakeapp", "wt-test")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            # El worktree existe pero sin marker (p. ej. worktree de review o
            # marker ya retirado): el merge sigue igual y no reporta nada.
            (tmp / "state" / "worktrees" / "fakeapp" / "wt-test" / ".asf-active").unlink()
            r2 = self._run(tmp, env, "merge.sh", "fakeapp", "factory/wt-test", "--apply")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertIn(f"merged: 77 {self.MERGE}", r2.stdout)
            self.assertNotIn("marker", r2.stderr)


class TestNewWorktree(unittest.TestCase):
    """new-worktree.sh con un harness 100% fake (sin red).

    Copia `scripts/` a un tmp, arma un registry con un seed repo local y
    verifica: stdout pipeable (sólo la ruta), cwd-independencia, idempotencia y
    rechazo de slugs reservados/vacíos.
    """

    def test_create_idempotent_and_invalid_slugs(self) -> None:
        import shutil

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            shutil.copytree(SCRIPTS, tmp / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
            seed, sha = _seed_repo(tmp)
            apps_dir = tmp / "config" / "apps"
            apps_dir.mkdir(parents=True)
            (apps_dir / "fakeapp.json").write_text(
                json.dumps(
                    {
                        "id": "fakeapp",
                        "repo": "f/o",
                        "remote": str(seed),
                        "branch": "main",
                        "checkout": "work/fakeapp",
                    }
                )
            )
            script = tmp / "scripts" / "new-worktree.sh"
            wt = tmp / "state" / "worktrees" / "fakeapp" / "wt-test"
            base = tmp / "work" / "fakeapp"
            marker = wt / ".asf-active"

            # 1) creación: cwd arbitrario (tmp), stdout = sólo la ruta.
            r1 = subprocess.run(
                ["bash", str(script), "fakeapp", "wt-test"],
                capture_output=True,
                text=True,
                cwd=tmp,
            )
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertEqual(r1.stdout.strip(), str(wt))
            self.assertTrue(base.is_dir(), "el clone base no se creó (sync-work)")
            self.assertTrue(marker.is_file(), "new-worktree.sh no marcó la corrida (.asf-active)")
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                "factory/wt-test",
            )
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(wt), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                sha,
            )

            # 2) re-run idempotente: misma ruta, un solo worktree extra, marcada.
            r2 = subprocess.run(
                ["bash", str(script), "fakeapp", "wt-test"],
                capture_output=True,
                text=True,
                cwd=tmp,
            )
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertEqual(r2.stdout.strip(), str(wt))
            self.assertTrue(marker.is_file(), "retomar la corrida debe re-marcarla")
            worktree_list = subprocess.run(
                ["git", "-C", str(base), "worktree", "list"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(len(worktree_list.splitlines()), 2)

            # 2b) el marker perdido (git clean, merge previo) se re-arma al retomar.
            marker.unlink()
            r2b = subprocess.run(
                ["bash", str(script), "fakeapp", "wt-test"],
                capture_output=True,
                text=True,
                cwd=tmp,
            )
            self.assertEqual(r2b.returncode, 0, r2b.stderr)
            self.assertTrue(marker.is_file(), "retomar una corrida sin marker debe re-marcarla")

            # 3) slugs inválidos: reservado (review) y vacío.
            for bad in ("pr-9", ""):
                with self.subTest(slug=bad):
                    rb = subprocess.run(
                        ["bash", str(script), "fakeapp", bad],
                        capture_output=True,
                        text=True,
                        cwd=tmp,
                    )
                    self.assertEqual(rb.returncode, 2, rb.stderr)


class TestNewWorktreeSubmodules(unittest.TestCase):
    """new-worktree.sh inicializa los submódulos declarados en .gitmodules.

    El repo de la app puede traer submódulos (el X6200: lvgl) y una corrida sin
    inicializarlos arranca rota ("Unable to find current revision in submodule
    path 'lvgl'"). El script los inicializa de forma genérica y el contrato de
    stdout (sólo la ruta del worktree) queda intacto.
    """

    def _env(self) -> dict[str, str]:
        """Env con protocol.file.allow=always: git >=2.38 bloquea por defecto
        clonar submódulos desde file:// (CVE-2022-39253)."""
        env = dict(os.environ)
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "protocol.file.allow"
        env["GIT_CONFIG_VALUE_0"] = "always"
        return env

    def _registry(self, tmp: Path, seed: Path) -> None:
        apps_dir = tmp / "config" / "apps"
        apps_dir.mkdir(parents=True)
        (apps_dir / "fakeapp.json").write_text(
            json.dumps(
                {
                    "id": "fakeapp",
                    "repo": "f/o",
                    "remote": str(seed),
                    "branch": "main",
                    "checkout": "work/fakeapp",
                }
            ),
            encoding="utf-8",
        )

    def test_submodule_initialized_in_worktree(self) -> None:
        import shutil

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            shutil.copytree(SCRIPTS, tmp / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
            seed, sha = _seed_repo_with_submodule(tmp)
            self._registry(tmp, seed)
            script = tmp / "scripts" / "new-worktree.sh"
            wt = tmp / "state" / "worktrees" / "fakeapp" / "wt-sub"

            r1 = subprocess.run(
                ["bash", str(script), "fakeapp", "wt-sub"],
                capture_output=True,
                text=True,
                cwd=tmp,
                env=self._env(),
            )
            self.assertEqual(r1.returncode, 0, r1.stderr)
            # Contrato: stdout = sólo la ruta (el init de submódulos va a stderr).
            self.assertEqual(r1.stdout.strip(), str(wt))
            self.assertEqual(r1.stdout.count("\n"), 1)

            status = subprocess.run(
                ["git", "-C", str(wt), "submodule", "status"],
                check=True,
                capture_output=True,
                text=True,
                env=self._env(),
            ).stdout
            lines = [ln for ln in status.splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1, status)
            self.assertIn("vendor/twin", lines[0])
            # Inicializado = sha presente sin el prefijo '-' de "no inicializado".
            self.assertNotEqual(lines[0][0], "-", f"submódulo sin inicializar: {lines[0]!r}")
            self.assertTrue(
                (wt / "vendor" / "twin" / "lib.txt").is_file(),
                "el contenido del submódulo no se materializó en el worktree",
            )
            # El worktree está en la base del seed (con el submódulo commiteado).
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(wt), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                sha,
            )

    def test_no_submodule_seed_is_noop(self) -> None:
        """Seed sin .gitmodules: sin error, sin WARN y el stdout sigue igual."""
        import shutil

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            shutil.copytree(SCRIPTS, tmp / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
            seed, _ = _seed_repo(tmp)
            self._registry(tmp, seed)
            wt = tmp / "state" / "worktrees" / "fakeapp" / "wt-plain"

            r1 = subprocess.run(
                ["bash", str(tmp / "scripts" / "new-worktree.sh"), "fakeapp", "wt-plain"],
                capture_output=True,
                text=True,
                cwd=tmp,
                env=self._env(),
            )
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertEqual(r1.stdout.strip(), str(wt))
            self.assertFalse((wt / ".gitmodules").exists())
            self.assertNotIn("submódulo", r1.stderr)
            self.assertNotIn("WARN", r1.stderr)


class TestSyncWorkSubmodules(unittest.TestCase):
    """sync-work.sh inicializa los submódulos declarados en .gitmodules.

    Medido en x6200: con `work/x6200_gui` sin el submódulo lvgl inicializado, el
    bloque check_drawbuf_partial falla por entorno ("/src/lvgl does not contain
    a CMakeLists.txt") y ese rojo se lee como regresión del PR. El checkout base
    lo clona/actualiza sync-work.sh, así que es ahí donde se inicializan.
    """

    def _env(self) -> dict[str, str]:
        """Env con protocol.file.allow=always: git >=2.38 bloquea por defecto
        clonar submódulos desde file:// (CVE-2022-39253)."""
        env = dict(os.environ)
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "protocol.file.allow"
        env["GIT_CONFIG_VALUE_0"] = "always"
        return env

    def _script(self, tmp: Path, seed: Path) -> Path:
        import shutil

        shutil.copytree(SCRIPTS, tmp / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        apps_dir = tmp / "config" / "apps"
        apps_dir.mkdir(parents=True)
        (apps_dir / "fakeapp.json").write_text(
            json.dumps(
                {
                    "id": "fakeapp",
                    "repo": "f/o",
                    "remote": str(seed),
                    "branch": "main",
                    "checkout": "work/fakeapp",
                }
            ),
            encoding="utf-8",
        )
        return tmp / "scripts" / "sync-work.sh"

    def test_submodule_initialized_in_the_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            seed, _ = _seed_repo_with_submodule(tmp)
            script = self._script(tmp, seed)
            checkout = tmp / "work" / "fakeapp"

            r1 = subprocess.run(
                ["bash", str(script), "fakeapp"],
                capture_output=True,
                text=True,
                cwd=tmp,
                env=self._env(),
            )
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertIn("submódulos inicializados", r1.stderr)
            self.assertTrue(
                (checkout / "vendor" / "twin" / "lib.txt").is_file(),
                "sync-work.sh dejó el checkout con el submódulo sin inicializar",
            )
            status = subprocess.run(
                ["git", "-C", str(checkout), "submodule", "status"],
                check=True,
                capture_output=True,
                text=True,
                env=self._env(),
            ).stdout
            lines = [ln for ln in status.splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1, status)
            self.assertNotEqual(lines[0][0], "-", f"submódulo sin inicializar: {lines[0]!r}")

            # Idempotente: actualizar un checkout ya inicializado no falla.
            r2 = subprocess.run(
                ["bash", str(script), "fakeapp"],
                capture_output=True,
                text=True,
                cwd=tmp,
                env=self._env(),
            )
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertIn("submódulos inicializados", r2.stderr)

    def test_no_gitmodules_is_a_noop(self) -> None:
        """App sin submódulos: ni error ni ruido (el script es app-agnóstico)."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            seed, _ = _seed_repo(tmp)
            script = self._script(tmp, seed)
            r1 = subprocess.run(
                ["bash", str(script), "fakeapp"],
                capture_output=True,
                text=True,
                cwd=tmp,
                env=self._env(),
            )
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertNotIn("submódulo", r1.stderr)
            self.assertFalse((tmp / "work" / "fakeapp" / ".gitmodules").exists())


class TestWorktreeFlowDocs(unittest.TestCase):
    """Enforcer: el flujo de implementación exige worktree y orquestador solo."""

    def test_new_worktree_script_mentioned(self) -> None:
        files = (
            ROOT / "AGENTS.md",
            ROOT / ".opencode" / "command" / "factory-build.md",
            ROOT / ".opencode" / "skill" / "factory-ci" / "SKILL.md",
            ROOT / ".opencode" / "skill" / "factory-context" / "SKILL.md",
        )
        for f in files:
            with self.subTest(file=str(f)):
                self.assertIn("new-worktree.sh", f.read_text(encoding="utf-8"))

    def test_orchestrator_only_orchestrates(self) -> None:
        files = (
            ROOT / "AGENTS.md",
            ROOT / ".opencode" / "command" / "factory-build.md",
        )
        for f in files:
            with self.subTest(file=str(f)):
                self.assertIn("SOLO orquesta", f.read_text(encoding="utf-8"))

    def test_factory_build_does_not_implement_in_work(self) -> None:
        text = (ROOT / ".opencode" / "command" / "factory-build.md").read_text(encoding="utf-8")
        self.assertNotIn("implementa en work/", text)

    def test_coder_uses_worktree(self) -> None:
        text = (ROOT / ".opencode" / "agent" / "coder.md").read_text(encoding="utf-8")
        self.assertIn("worktree", text)
        self.assertIn("state/worktrees/", text)

    def test_trace_report_documented(self) -> None:
        files = (
            ROOT / ".opencode" / "skill" / "factory-report" / "SKILL.md",
            ROOT / ".opencode" / "command" / "factory-build.md",
            ROOT / "AGENTS.md",
            ROOT / "README.md",
        )
        for f in files:
            with self.subTest(file=str(f)):
                self.assertIn("trace-report.py", f.read_text(encoding="utf-8"))

    def test_trace_report_documents_three_views(self) -> None:
        skill = (ROOT / ".opencode" / "skill" / "factory-report" / "SKILL.md").read_text(encoding="utf-8")
        for view in ("Rendimiento", "Diagrama", "Traza"):
            with self.subTest(view=view):
                self.assertIn(view, skill)
        self.assertIn("WCAG", skill)
        for f in (ROOT / "README.md", ROOT / "AGENTS.md"):
            text = f.read_text(encoding="utf-8").lower()
            with self.subTest(file=str(f)):
                self.assertIn("rendimiento", text)
                self.assertIn("diagrama", text)

    def test_trace_report_documents_sentry_style(self) -> None:
        skill = (ROOT / ".opencode" / "skill" / "factory-report" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("estilo Sentry", skill)
        self.assertIn("--no-js", skill)

    def test_trace_report_documents_v5(self) -> None:
        skill = (ROOT / ".opencode" / "skill" / "factory-report" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("gaps comprimidos", skill)
        self.assertIn("Issues", skill)
        self.assertIn("--max-gap", skill)

    def test_trace_report_documents_v6(self) -> None:
        skill = (ROOT / ".opencode" / "skill" / "factory-report" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("turnos LLM", skill)
        self.assertIn("Tiempo LLM vs tools", skill)


class TestTraceReport(unittest.TestCase):
    """trace-report.py v4 contra una db sqlite falsa (sin red ni db real).

    Fixture: un mismo `coder` re-invocado dos veces desde la raíz (misma sesión
    hija) y que a su vez delega en `explore`, para probar invocations, contador
    de aristas, rondas R1/R2 y own_ms < wall_ms. La vista Traza v4 es estilo
    Sentry (filas + panel + buscador) con un único `<script id="view-js">`;
    `--no-js` emite la v3 sin ese script.
    """

    NOW = "2026-01-01T00:00:00Z"
    ANCHOR = "/x/wt/mi-slug"
    MODEL = json.dumps({"id": "m", "providerID": "p"})

    @staticmethod
    def _module() -> Any:
        import importlib.util

        spec = importlib.util.spec_from_file_location("trace_report_v2", SCRIPTS / "trace-report.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _make_db(self, tmp: Path) -> Path:
        db = tmp / "fake.db"
        con = sqlite3.connect(str(db))
        con.executescript(
            """
            CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT);
            CREATE TABLE session (
                id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, model TEXT, title TEXT,
                cost REAL, tokens_input INTEGER, tokens_output INTEGER,
                tokens_reasoning INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER,
                time_created INTEGER, time_updated INTEGER
            );
            CREATE TABLE part (
                id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
                time_created INTEGER, time_updated INTEGER, data TEXT
            );
            CREATE TABLE message (
                id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
                time_updated INTEGER, data TEXT
            );
            """
        )
        sessions = [
            ("root", None, "build", self.MODEL, "orchestrator", 0.5, 0, 0, 0, 0, 0, 900_000, 1_100_000),
            (
                "coder",
                "root",
                "coder",
                self.MODEL,
                "Implement",
                1.25,
                100,
                200,
                0,
                0,
                0,
                1_002_000,
                1_009_600,
            ),
            ("sub", "coder", "explore", self.MODEL, "Explore", 0.1, 10, 20, 0, 0, 0, 1_003_000, 1_007_000),
        ]
        con.executemany("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", sessions)
        parts: list[tuple[str, str, dict[str, Any]]] = [
            ("p-task1", "root", self._task("Implement X", 1_000_000, 1_010_000, "coder")),
            ("p-task2", "root", self._task("Fix X", 1_030_000, 1_040_000, "coder")),
            ("p-ctask", "coder", self._task("Explore Y", 1_002_000, 1_008_000, "sub")),
            ("p-sub", "sub", self._tool("bash", "completed", "echo sub", "sub ok", 1_003_000, 1_007_000)),
            (
                "p-bash",
                "coder",
                self._tool("bash", "completed", "git status", "SECRET_OUTPUT_TOKEN", 1_008_500, 1_009_000),
            ),
            (
                "p-bash2",
                "coder",
                self._tool("bash", "completed", "git status", "clean", 1_009_050, 1_009_100),
            ),
            ("p-err", "coder", self._tool("read", "error", "missing.py", "boom", 1_009_150, 1_009_200)),
            (
                "p-bash3",
                "coder",
                self._tool("bash", "completed", "uv run pytest", "ok", 1_009_250, 1_009_300),
            ),
            (
                "p-edit",
                "coder",
                self._edit_tool("scripts/x.py", "linea vieja\n", "linea nueva\n", 1_009_350, 1_009_400),
            ),
            ("p-write", "coder", self._file_tool("write", "scripts/y.py", 1_009_450, 1_009_500)),
            (
                "p-skill",
                "coder",
                {
                    "type": "tool",
                    "tool": "skill",
                    "state": {
                        "status": "completed",
                        "input": {"name": "wait-for"},
                        "output": "ok",
                        "time": {"start": 1_009_550, "end": 1_009_600},
                    },
                },
            ),
            ("p-big", "coder", self._tool("bash", "completed", "cat big", "A" * 500, 1_009_650, 1_009_700)),
            # hueco de 160 s (> max-gap 120 s) para probar la compresión virtual.
            ("p-gap", "root", self._tool("bash", "completed", "sleep 200", "ok", 1_200_000, 1_200_500)),
        ]
        con.executemany(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            [(pid, "m-" + pid, sid, 0, 0, json.dumps(data)) for pid, sid, data in parts],
        )
        # Mensaje asistente + steps de turno LLM en `coder`: 2 pares cerrados y
        # un step-start sin finish (span abierto). Intercalados con p-bash.
        message = {
            "role": "assistant",
            "cost": 0.005,
            "tokens": {
                "total": 385,
                "input": 300,
                "output": 50,
                "reasoning": 5,
                "cache": {"write": 5, "read": 30},
            },
            "modelID": "openrouter/deepseek/deepseek-v4.1-flash",
            "providerID": "openrouter",
            "variant": "xhigh",
            "mode": "build",
            "time": {"created": 1_008_000},
        }
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("m-llm", "coder", 1_008_000, 1_009_950, json.dumps(message)),
        )
        steps: list[tuple[str, str, int, int, dict[str, Any]]] = [
            ("s1", "coder", 1_008_100, 1_008_100, {"type": "step-start"}),
            (
                "f1",
                "coder",
                1_008_300,
                1_008_300,
                {
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": 0.002,
                    "tokens": {
                        "input": 100,
                        "output": 20,
                        "reasoning": 5,
                        "cache": {"write": 0, "read": 10},
                    },
                },
            ),
            ("s2", "coder", 1_008_700, 1_008_700, {"type": "step-start"}),
            (
                "f2",
                "coder",
                1_008_900,
                1_008_900,
                {
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0.003,
                    "tokens": {
                        "input": 200,
                        "output": 30,
                        "reasoning": 0,
                        "cache": {"write": 5, "read": 20},
                    },
                },
            ),
            ("s3", "coder", 1_009_800, 1_009_900, {"type": "step-start"}),
        ]
        con.executemany(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            [(pid, "m-llm", sid, tc, tu, json.dumps(data)) for pid, sid, tc, tu, data in steps],
        )
        con.commit()
        con.close()
        return db

    def _task(self, description: str, start: int, end: int, child: str) -> dict[str, Any]:
        return {
            "type": "tool",
            "tool": "task",
            "state": {
                "status": "completed",
                "input": {
                    "description": description,
                    "prompt": f"worktree absoluto {self.ANCHOR} (trabajá sólo acá)",
                    "subagent_type": "coder" if child == "coder" else "explore",
                },
                "output": "done",
                "metadata": {"sessionId": child, "parentSessionId": "root"},
                "time": {"start": start, "end": end},
            },
        }

    def _tool(
        self, tool: str, status: str, command: str, output: str, start: int, end: int
    ) -> dict[str, Any]:
        return {
            "type": "tool",
            "tool": tool,
            "state": {
                "status": status,
                "input": {"command": command},
                "output": output,
                "title": command,
                "time": {"start": start, "end": end},
            },
        }

    def _edit_tool(self, path: str, old: str, new: str, start: int, end: int) -> dict[str, Any]:
        return {
            "type": "tool",
            "tool": "edit",
            "state": {
                "status": "completed",
                "input": {"filePath": path, "oldString": old, "newString": new},
                "output": "ok",
                "title": path,
                "time": {"start": start, "end": end},
            },
        }

    def _file_tool(self, tool: str, path: str, start: int, end: int) -> dict[str, Any]:
        return {
            "type": "tool",
            "tool": tool,
            "state": {
                "status": "completed",
                "input": {"filePath": path},
                "output": "ok",
                "title": path,
                "time": {"start": start, "end": end},
            },
        }

    def _run(self, db: Path, out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "trace-report.py"),
                "asf",
                "mi-slug",
                "--grep",
                self.ANCHOR,
                "--db",
                str(db),
                "-o",
                str(out),
                *extra,
            ],
            capture_output=True,
            text=True,
        )

    def _embed(self, html: str) -> dict[str, Any]:
        import re

        m = re.search(r'<script id="trace-data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(m, "no se encontró el JSON embebido")
        assert m is not None
        return json.loads(m.group(1))

    def test_contrast_tokens(self) -> None:
        mod = self._module()
        contrast = mod.wcag_contrast
        palette = mod.PALETTE
        self.assertAlmostEqual(contrast("#7A4FA3", "#FFFFFF"), 6.04, delta=0.1)
        for token in mod.GRAPHIC_TOKENS:
            with self.subTest(token=token):
                self.assertGreaterEqual(contrast(palette[token], "#FFFFFF"), 3.0)
        for token in mod.TEXT_TOKENS:
            with self.subTest(token=token):
                self.assertGreaterEqual(contrast(palette[token], "#FFFFFF"), 4.5)

    def test_rendimiento(self) -> None:
        mod = self._module()
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            r = self._run(self._make_db(tmp), out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), str(out))
            data = self._embed(out.read_text(encoding="utf-8"))
            by_agent = data["summary"]["by_agent"]
            self.assertEqual(sorted(by_agent), ["build", "coder", "explore"])
            coder = by_agent["coder"]
            self.assertEqual(coder["invocations"], 2)
            self.assertEqual(coder["sessions"], 1)
            self.assertLess(coder["own_ms"], coder["wall_ms"])
            self.assertEqual(coder["tool_mix"]["task"], 1)
            self.assertEqual(coder["tool_mix"]["bash"], 4)
            self.assertEqual(coder["skills_usados"], {"wait-for": 1})
            self.assertEqual(by_agent["explore"]["invocations"], 1)
            rounds = data["summary"]["rounds"]
            self.assertEqual([r["k"] for r in rounds], [1, 2])
            self.assertEqual([r["t_ms"] for r in rounds], [1_000_000, 1_030_000])
            self.assertEqual(data["summary"]["palette"], mod.PALETTE)

    @staticmethod
    def _table_rows(html: str) -> dict[str, str]:
        import re

        m = re.search(r'<table class="perf">.*?</table>', html, re.S)
        assert m is not None, "no se encontró la tabla de Rendimiento"
        rows: dict[str, str] = {}
        for tr in re.findall(r"<tr>(.*?)</tr>", m.group(0), re.S):
            header = re.search(r'<th scope="row">([^<]+)</th>', tr)
            if header:
                rows[header.group(1)] = tr
        return rows

    def test_perf_own_sin_delegaciones(self) -> None:
        """own==wall (sin delegaciones propias) se muestra `—`; el JSON no cambia."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            rows = self._table_rows(html)
            # explore no delega: own==wall → `—` con title, sin número.
            self.assertIn("sin delegaciones propias", rows["explore"])
            self.assertIn("—", rows["explore"])
            # build y coder delegan: own<wall → numérico, sin title.
            self.assertNotIn("sin delegaciones propias", rows["build"])
            self.assertNotIn("sin delegaciones propias", rows["coder"])
            # El JSON embebido conserva own_ms numérico (display-only).
            by_agent = self._embed(html)["summary"]["by_agent"]
            self.assertEqual(by_agent["explore"]["own_ms"], by_agent["explore"]["wall_ms"])
            self.assertIsInstance(by_agent["explore"]["own_ms"], int)
            self.assertLess(by_agent["coder"]["own_ms"], by_agent["coder"]["wall_ms"])
            # Barra de own_ms: sin rect duplicado para explore; coder sí lo tiene.
            chart = html[html.index("Tiempo propio (own_ms)") : html.index("Costo (USD)")]
            explore_bar = chart[chart.index(">explore<") : chart.index("</li>", chart.index(">explore<"))]
            self.assertNotIn("<rect", explore_bar)
            coder_bar = chart[chart.index(">coder<") : chart.index("</li>", chart.index(">coder<"))]
            self.assertIn("<rect", coder_bar)

    @staticmethod
    def _session_nodes(data: dict[str, Any]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []

        def walk(obj: Any) -> None:
            if isinstance(obj, dict):
                if obj.get("kind") == "session":
                    found.append(obj)
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                for value in obj:
                    walk(value)

        walk(data["roots"])
        return found

    def test_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            data = self._embed(out.read_text(encoding="utf-8"))
            coder = next(s for s in self._session_nodes(data) if s["agent"] == "coder")
            execution = coder["execution"]
            commands = {c["name"]: c["count"] for c in execution["commands"]}
            self.assertEqual(commands.get("git status"), 2)
            self.assertIn("uv run pytest", commands)
            files = {f["name"] for f in execution["files"]}
            self.assertEqual(files, {"scripts/x.py", "scripts/y.py"})
            self.assertEqual(execution["by_tool"]["bash"], 4)
            self.assertEqual(execution["by_tool"]["edit"], 1)
            self.assertEqual(execution["by_tool"]["write"], 1)
            self.assertEqual([s["name"] for s in execution["skills"]], ["wait-for"])

    def test_delegations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            data = self._embed(out.read_text(encoding="utf-8"))
            dels = data["summary"]["delegations"]
            self.assertEqual([d["k"] for d in dels], [1, 2, 3])
            self.assertEqual([d["t_ms"] for d in dels], [1_000_000, 1_002_000, 1_030_000])
            pairs = [(d["from_agent"], d["to_agent"]) for d in dels]
            self.assertEqual(pairs, [("build", "coder"), ("coder", "explore"), ("build", "coder")])
            self.assertEqual(dels[1]["to_session"], "sub")
            self.assertEqual(dels[0]["description"], "Implement X")

    def test_narrativa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            self.assertIn("Narrativa de delegación", html)
            self.assertRegex(html, r"R\d → coder")
            self.assertIn("git status", html)
            self.assertIn('class="narrative" open', html)

    def test_no_outputs_v3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "n.html"
            r = self._run(self._make_db(tmp), out, "--no-outputs")
            self.assertEqual(r.returncode, 0, r.stderr)
            html = out.read_text(encoding="utf-8")
            self.assertNotIn("git status", html)
            self.assertNotIn("scripts/x.py", html)
            self.assertIn("n_commands", html)
            self.assertIn("Implement X", html)  # la description se mantiene

    def test_ancas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            self.assertIn('id="span-coder"', html)
            self.assertIn('href="#span-coder"', html)
            self.assertIn("→ coder", html)

    def test_flechas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            data = self._embed(html)
            self.assertEqual(html.count('data-arrow="1"'), len(data["summary"]["delegations"]))

    def test_diagrama(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            self.assertEqual(html.count('<svg role="img"'), 2)
            self.assertIn("\u00d72", html)
            self.assertIn(">R1<", html)
            self.assertIn(">R2<", html)
            self.assertGreaterEqual(html.count('<details class="equivalent"'), 2)
            self.assertIn("delegación", html)

    def test_traza_details(self) -> None:
        """La vista Traza v4 son filas `.row` (no `<details>` salvo `--no-js`)."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            self.assertIn('<div id="trace-rows" class="trace-rows">', html)
            self.assertIn('class="row cat-', html)
            self.assertIn('<span class="chev', html)
            self.assertIn('<span class="rtrack">', html)

    def test_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            self.assertEqual(html.count("<script"), 2)
            self.assertEqual(html.count('name="view"'), 3)
            for view in ("Rendimiento", "Diagrama", "Traza"):
                self.assertIn(view, html)
            # Traza es la tab default.
            self.assertIn('id="view-trace" checked', html)

    # --- v4: vista estilo Sentry ------------------------------------------

    def test_v4_scripts_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            self.assertEqual(html.count("<script"), 2)
            self.assertEqual(html.count('id="view-js"'), 1)
            self.assertEqual(html.count('id="trace-data"'), 1)
            data = self._embed(html)
            self.assertEqual(data["summary"]["row_count"], html.count('class="row cat-'))
            self.assertGreater(data["summary"]["row_count"], 0)

    def test_v4_panel_template(self) -> None:
        import re

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            self.assertIn('id="span-panel"', html)
            data = self._embed(html)
            self.assertIn("avg_ms_by_tool", data["summary"])
            row_tags = re.findall(r"<a class=\"row[^>]*>", html)
            self.assertTrue(row_tags)
            for tag in row_tags:
                span_id = re.search(r'data-span-id="([^"]+)"', tag)
                with self.subTest(tag=tag[:80]):
                    self.assertIsNotNone(span_id)
                    assert span_id is not None
                    self.assertIn(f'href="#span-{span_id.group(1)}"', tag)

    def test_v4_avg_badge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            data = self._embed(out.read_text(encoding="utf-8"))
            avg = data["summary"]["avg_ms_by_tool"]
            self.assertTrue(avg)
            self.assertGreater(avg["bash"], 0)
            self.assertGreater(avg["task"], 0)

    def test_v4_toolbar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            self.assertIn('type="search"', html)
            self.assertIn("Search in trace", html)
            for category in ("sesion", "llm", "task", "bash", "files", "skill", "web", "memory", "otros"):
                with self.subTest(category=category):
                    self.assertIn(f'data-category="{category}"', html)
            self.assertEqual(html.count('type="checkbox"'), 9)
            self.assertIn("Expand all", html)
            self.assertIn("Collapse all", html)

    def test_v4_no_js(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "n.html"
            r = self._run(self._make_db(tmp), out, "--no-js")
            self.assertEqual(r.returncode, 0, r.stderr)
            html = out.read_text(encoding="utf-8")
            self.assertEqual(html.count("<script"), 1)
            self.assertNotIn('id="view-js"', html)
            self.assertIn("<details", html)
            self.assertIn('id="sesion-coder"', html)
            self.assertNotIn('id="trace-rows"', html)

    def test_v4_deterministic_js(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            db = self._make_db(tmp)
            out1, out2 = tmp / "a.html", tmp / "b.html"
            self.assertEqual(self._run(db, out1, "--now", self.NOW).returncode, 0)
            self.assertEqual(self._run(db, out2, "--now", self.NOW).returncode, 0)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())

    def test_v4_deterministic_no_js(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            db = self._make_db(tmp)
            out1, out2 = tmp / "a.html", tmp / "b.html"
            self.assertEqual(self._run(db, out1, "--now", self.NOW, "--no-js").returncode, 0)
            self.assertEqual(self._run(db, out2, "--now", self.NOW, "--no-js").returncode, 0)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())

    def test_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            db = self._make_db(tmp)
            out1, out2 = tmp / "a.html", tmp / "b.html"
            self.assertEqual(self._run(db, out1, "--now", self.NOW).returncode, 0)
            self.assertEqual(self._run(db, out2, "--now", self.NOW).returncode, 0)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())

    # --- v5: gaps + minimapa/zoom, issues-first, diff, teclado/dark ---------

    @staticmethod
    def _find_node(data: dict[str, Any], node_id: str) -> dict[str, Any] | None:
        found: list[dict[str, Any]] = []

        def walk(obj: Any) -> None:
            if isinstance(obj, dict):
                if obj.get("id") == node_id:
                    found.append(obj)
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                for value in obj:
                    walk(value)

        walk(data["roots"])
        return found[0] if found else None

    def test_v5_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            data = self._embed(html)
            gaps = data["meta"]["gaps"]
            self.assertTrue(gaps, "el fixture tiene un hueco de 160 s")
            self.assertIn("sin actividad", gaps[0]["label"])
            self.assertIn("gap-row", html)
            self.assertIn("sin actividad", html)
            # Un span lejos del hueco conserva virtual==real (sin distorsión).
            span = self._find_node(data, "p-bash")
            assert span is not None
            self.assertEqual(span["v_end"] - span["v_start"], span["end"] - span["start"])
            # El total virtual es menor que el real por la compresión.
            self.assertLess(data["meta"]["virtual_total"], 1_200_500 - 1_000_000)

    def test_v5_issues(self) -> None:
        import re

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            data = self._embed(html)
            err = self._find_node(data, "p-err")
            assert err is not None
            self.assertGreaterEqual(err["self_errors"], 1)
            self.assertIn("err-badge", html)
            self.assertIn('id="issues-chip"', html)
            chip = re.search(r'id="issues-chip"[^>]*>Issues: <b>(\d+)</b>', html)
            self.assertIsNotNone(chip)
            assert chip is not None
            self.assertEqual(int(chip.group(1)), data["summary"]["issues"])
            self.assertGreaterEqual(data["summary"]["issues"], 1)

    def test_v5_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            db = self._make_db(tmp)
            out = tmp / "trace.html"
            self.assertEqual(self._run(db, out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            data = self._embed(html)
            edit = self._find_node(data, "p-edit")
            assert edit is not None
            diff = edit["diff"]
            self.assertTrue(any(line.startswith("+") for line in diff))
            self.assertTrue(any(line.startswith("-") for line in diff))
            self.assertIn("linea nueva", "".join(diff))
            self.assertIn('class="diff"', html)
            # --no-outputs: sin diff (ni en datos ni en HTML).
            out2 = tmp / "n.html"
            r = self._run(db, out2, "--no-outputs")
            self.assertEqual(r.returncode, 0, r.stderr)
            html2 = out2.read_text(encoding="utf-8")
            data2 = self._embed(html2)
            edit2 = self._find_node(data2, "p-edit")
            assert edit2 is not None
            self.assertNotIn("diff", edit2)
            self.assertNotIn('class="diff"', html2)

    def test_v5_keyboard_dark(self) -> None:
        import re

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            js = re.search(r'<script id="view-js">\n(.*?)</script>', html, re.S)
            self.assertIsNotNone(js)
            assert js is not None
            self.assertIn('case "j"', js.group(1))
            self.assertIn('case "k"', js.group(1))
            self.assertIn("hashchange", js.group(1))
            self.assertIn("prefers-color-scheme: dark", html)
            self.assertIn('id="kbd-help"', html)

    def test_v5_dark_contrast(self) -> None:
        mod = self._module()
        for token in mod.GRAPHIC_TOKENS:
            with self.subTest(token=token):
                self.assertGreaterEqual(mod.wcag_contrast(mod.DARK_PALETTE[token], mod.DARK_BG), 3.0)
        for token in mod.TEXT_TOKENS:
            with self.subTest(token=token):
                self.assertGreaterEqual(mod.wcag_contrast(mod.DARK_PALETTE[token], mod.DARK_BG), 4.5)

    def test_v5_deterministic_js(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            db = self._make_db(tmp)
            out1, out2 = tmp / "a.html", tmp / "b.html"
            self.assertEqual(self._run(db, out1, "--now", self.NOW).returncode, 0)
            self.assertEqual(self._run(db, out2, "--now", self.NOW).returncode, 0)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())

    def test_v5_deterministic_no_js(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            db = self._make_db(tmp)
            out1, out2 = tmp / "a.html", tmp / "b.html"
            self.assertEqual(self._run(db, out1, "--now", self.NOW, "--no-js").returncode, 0)
            self.assertEqual(self._run(db, out2, "--now", self.NOW, "--no-js").returncode, 0)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())

    # --- v6: spans de turno LLM -------------------------------------------

    def test_v6_llm_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            data = self._embed(out.read_text(encoding="utf-8"))
            coder = next(s for s in self._session_nodes(data) if s["agent"] == "coder")
            llm = [s for s in coder["spans"] if s.get("category") == "llm"]
            self.assertEqual([s["id"] for s in llm], ["llm-m-llm-0", "llm-m-llm-1", "llm-m-llm-2"])
            self.assertFalse(llm[0]["open"])
            self.assertFalse(llm[1]["open"])
            self.assertTrue(llm[2]["open"])
            self.assertEqual(llm[0]["reason"], "tool-calls")
            self.assertEqual(llm[1]["reason"], "stop")
            self.assertEqual(llm[0]["tokens"]["input"], 100)
            self.assertEqual(llm[1]["tokens"]["cache_write"], 5)
            # Intercalado cronológico con el tool p-bash (1_008_500).
            order = [s["id"] for s in coder["spans"]]
            self.assertLess(order.index("llm-m-llm-0"), order.index("p-bash"))
            self.assertLess(order.index("p-bash"), order.index("llm-m-llm-1"))
            # v_start/v_end presentes (compresión de gaps de v5).
            self.assertIn("v_start", llm[0])

    def test_v6_chips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            db = self._make_db(tmp)
            out = tmp / "trace.html"
            self.assertEqual(self._run(db, out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            data = self._embed(html)
            split = data["summary"]["time_split"]
            self.assertEqual(split["turns"], 3)
            self.assertEqual(split["llm_ms"], 500)
            self.assertGreater(split["tool_ms"], 0)
            self.assertIn("Turnos:", html)
            # no-js imprime el chip estático.
            out2 = tmp / "n.html"
            self.assertEqual(self._run(db, out2, "--no-js").returncode, 0)
            self.assertIn("Turnos:", out2.read_text(encoding="utf-8"))

    def test_v6_perf_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            html = out.read_text(encoding="utf-8")
            data = self._embed(html)
            coder = data["summary"]["by_agent"]["coder"]
            self.assertEqual(coder["turns"], 3)
            self.assertEqual(coder["llm_ms"], 500)
            self.assertGreater(coder["tool_ms"], 0)
            self.assertGreater(coder["llm_share_pct"], 0)
            self.assertIn("Tiempo LLM vs tools", html)

    def test_v6_panel_turno(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "trace.html"
            self.assertEqual(self._run(self._make_db(tmp), out).returncode, 0)
            data = self._embed(out.read_text(encoding="utf-8"))
            span = self._find_node(data, "llm-m-llm-0")
            assert span is not None
            groups = {g["name"]: dict(g["items"]) for g in span["attrs"]}
            self.assertIn("turno", groups)
            self.assertIn("deepseek-v4.1-flash", groups["turno"]["model"])
            self.assertEqual(groups["turno"]["provider"], "openrouter")
            self.assertIn("sesión", groups)
            self.assertNotIn("input", span)
            self.assertNotIn("output", span)

    def test_v6_dark_contrast(self) -> None:
        mod = self._module()
        self.assertGreaterEqual(mod.wcag_contrast(mod.DARK_PALETTE["llm"], mod.DARK_BG), 3.0)
        self.assertGreaterEqual(mod.wcag_contrast(mod.PALETTE["llm"], "#FFFFFF"), 3.0)

    def test_v6_no_outputs_keeps_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            out = tmp / "n.html"
            r = self._run(self._make_db(tmp), out, "--no-outputs")
            self.assertEqual(r.returncode, 0, r.stderr)
            data = self._embed(out.read_text(encoding="utf-8"))
            self.assertIsNotNone(self._find_node(data, "llm-m-llm-0"))

    def test_v6_deterministic_no_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            db = self._make_db(tmp)
            out1, out2 = tmp / "a.html", tmp / "b.html"
            self.assertEqual(self._run(db, out1, "--now", self.NOW, "--no-outputs").returncode, 0)
            self.assertEqual(self._run(db, out2, "--now", self.NOW, "--no-outputs").returncode, 0)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())

    def test_no_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            r = self._run(self._make_db(tmp), tmp / "n.html", "--no-outputs")
            self.assertEqual(r.returncode, 0, r.stderr)
            html = (tmp / "n.html").read_text(encoding="utf-8")
            self.assertNotIn("SECRET_OUTPUT_TOKEN", html)
            self.assertIn("omitido (--no-outputs)", html)

    def test_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            r = self._run(self._make_db(tmp), tmp / "t.html", "--max-bytes", "50")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("truncado", (tmp / "t.html").read_text(encoding="utf-8"))

    def test_no_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            db = self._make_db(tmp)
            r = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "trace-report.py"),
                    "asf",
                    "mi-slug",
                    "--grep",
                    "/nope/no-match",
                    "--db",
                    str(db),
                    "-o",
                    str(tmp / "x.html"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("PENDIENTE", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_missing_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            r = self._run(tmp / "missing.db", tmp / "x.html")
            self.assertEqual(r.returncode, 1)
            self.assertIn("PENDIENTE", r.stderr)


class TestRunStats(unittest.TestCase):
    """run-stats.py: ambos layouts de state/reports contra un fixture temporal."""

    @staticmethod
    def _load() -> Any:
        import importlib.util

        spec = importlib.util.spec_from_file_location("run_stats", SCRIPTS / "run-stats.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _fixture(self, tmp: Path) -> Path:
        reports = tmp / "reports"
        # Layout actual: reports/<app>/<run>/
        self._write(
            reports / "asf" / "runA" / "run.json",
            json.dumps(
                {
                    "run": "runA",
                    "app": "asf",
                    "date": "2026-01-10T00:00:00Z",
                    "status": "merged",
                    "risk": "low",
                }
            ),
        )
        self._write(
            reports / "asf" / "runA" / "babysit.json",
            json.dumps({"head_sha": "a" * 40, "exit": 0}),
        )
        self._write(
            reports / "asf" / "runA" / "babysit-v2.json",
            json.dumps({"head_sha": "b" * 40, "exit": 0}),
        )
        self._write(
            reports / "asf" / "runA" / "decisions.tsv",
            "ts\tfase\tdecisión\trazón\tevidence\tresultado\n"
            "2026-01-10T00:00:00Z\tbuild\tx\ty\tz\tok\n"
            "2026-01-10T00:01:00Z\tbuild\tx\ty\tz\tFalló\n",
        )
        # run.json incompleto (sin status, con date)
        self._write(
            reports / "asf" / "runB" / "run.json",
            json.dumps({"run": "runB", "app": "asf", "date": "2026-01-20T00:00:00Z"}),
        )
        # Layout legacy con run.json.app
        self._write(
            reports / "legacy-run" / "run.json",
            json.dumps(
                {
                    "run": "legacy-run",
                    "app": "radio-contest",
                    "date": "2026-01-01T00:00:00Z",
                    "status": "green",
                    "risk": "high",
                }
            ),
        )
        # Dir sin run.json (sólo report.md), mtime viejo.
        self._write(reports / "sin-json" / "report.md", "# sin schema\n")
        old = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
        os.utime(reports / "sin-json", (old, old))
        return reports

    def test_aggregates_both_layouts(self) -> None:
        mod = self._load()
        with tempfile.TemporaryDirectory() as tmp_str:
            reports = self._fixture(Path(tmp_str))
            data = mod.collect_reports(reports)

        self.assertEqual(set(data["apps"]), {"asf", "radio-contest", "(legacy)"})
        asf = data["apps"]["asf"]
        self.assertEqual(asf["runs"], 2)
        self.assertEqual(asf["sin_schema"], 1)
        self.assertEqual(asf["by_status"], {"merged": 1, "sin-schema": 1})
        self.assertEqual(asf["risk"], {"low": 1, "medium": 0, "high": 0, "unknown": 1})
        self.assertEqual(asf["merged"], 1)
        self.assertEqual(asf["merged_pct"], 100.0)
        self.assertEqual(
            asf["ci"],
            {"runs": 1, "files": 2, "distinct_shas_avg": 2.0, "first_exit_ok": 1, "last_exit_ok": 1},
        )
        self.assertEqual(asf["decisions"], {"total": 2, "ok": 1, "no_util": 1, "no_util_pct": 50.0})

        radio = data["apps"]["radio-contest"]
        self.assertEqual(radio["runs"], 1)
        self.assertEqual(radio["by_status"], {"green": 1})
        self.assertEqual(radio["risk"]["high"], 1)

        legacy = data["apps"]["(legacy)"]
        self.assertEqual(legacy["runs"], 1)
        self.assertEqual(legacy["sin_schema"], 1)
        self.assertIn("sin-schema", legacy["by_status"])

        self.assertEqual(len(data["runs"]), 4)
        self.assertEqual(
            [r["run"] for r in data["runs"]],
            ["sin-json", "runA", "runB", "legacy-run"],
        )
        run_a = next(r for r in data["runs"] if r["run"] == "runA")
        self.assertEqual(run_a["babysit"]["distinct_shas"], 2)
        self.assertIsNotNone(run_a["babysit"]["first_sha"])
        self.assertIsNotNone(run_a["babysit"]["last_sha"])

    def test_since_filters_by_date_or_mtime(self) -> None:
        mod = self._load()
        with tempfile.TemporaryDirectory() as tmp_str:
            reports = self._fixture(Path(tmp_str))
            filtered = mod.collect_reports(reports, since="2026-01-05")
        self.assertEqual(set(filtered["apps"]), {"asf"})
        self.assertEqual([r["run"] for r in filtered["runs"]], ["runA", "runB"])
        self.assertEqual(filtered["since"], "2026-01-05")

    def test_markdown_and_json_render(self) -> None:
        mod = self._load()
        with tempfile.TemporaryDirectory() as tmp_str:
            reports = self._fixture(Path(tmp_str))
            data = mod.collect_reports(reports)
        markdown = mod.render_markdown(data)
        self.assertIn("## Volumen", markdown)
        self.assertIn("## Riesgo", markdown)
        self.assertIn("sin-schema", markdown)
        self.assertEqual(json.loads(json.dumps(data))["apps"]["asf"]["runs"], 2)


class TestAgentStats(unittest.TestCase):
    """agent-stats.py: db fake con el esquema real (project/session/part)."""

    PROMPT = "SECRET_PROMPT_CONTENT_XYZ"
    NOW_MS = 1_800_000_000_000

    @staticmethod
    def _load() -> Any:
        import importlib.util

        spec = importlib.util.spec_from_file_location("agent_stats", SCRIPTS / "agent-stats.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _make_db(self, tmp: Path) -> tuple[Path, str, int]:
        proj_dir = str(tmp / "wt" / "asf")
        db = tmp / "fake.db"
        t0 = self.NOW_MS - 3_600_000
        cap_session = t0 + 30 * 3_600_000  # 30 h > cap
        con = sqlite3.connect(str(db))
        con.executescript(
            """
            CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT);
            CREATE TABLE session (
                id TEXT PRIMARY KEY, parent_id TEXT, project_id TEXT, agent TEXT, directory TEXT,
                time_created INTEGER, time_updated INTEGER, cost REAL,
                tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,
                tokens_cache_read INTEGER, tokens_cache_write INTEGER
            );
            CREATE TABLE part (
                id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT
            );
            """
        )
        con.execute("INSERT INTO project VALUES (?,?)", ("p1", proj_dir))
        con.executemany(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("root", None, "p1", None, proj_dir, t0, cap_session, 0.5, 1000, 2000, 0, 0, 0),
                ("coder", "root", "p1", "coder", proj_dir, t0, t0 + 2 * 3_600_000, 1.0, 100, 200, 50, 10, 5),
            ],
        )
        parts = [
            ("p1", "root", self._task("coder", "completed")),
            ("p2", "root", self._task("reviewer", "error")),
            (
                "p3",
                "coder",
                {
                    "type": "tool",
                    "tool": "skill",
                    "state": {"status": "completed", "input": {"name": "wait-for"}},
                },
            ),
            (
                "p4",
                "coder",
                {"type": "tool", "tool": "read", "state": {"status": "error", "input": {"filePath": "x"}}},
            ),
        ]
        con.executemany(
            "INSERT INTO part VALUES (?,?,?,?)",
            [(pid, sid, t0, json.dumps(data)) for pid, sid, data in parts],
        )
        con.commit()
        con.close()
        return db, proj_dir, t0

    def _task(self, subagent_type: str, status: str) -> dict[str, Any]:
        return {
            "type": "tool",
            "tool": "task",
            "state": {
                "status": status,
                "input": {"subagent_type": subagent_type, "prompt": self.PROMPT},
                "output": self.PROMPT,
                "time": {"start": 0, "end": 60_000},
            },
        }

    def test_aggregates_by_agent_and_cap(self) -> None:
        mod = self._load()
        with tempfile.TemporaryDirectory() as tmp_str:
            db, proj_dir, _ = self._make_db(Path(tmp_str))
            con = sqlite3.connect(str(db))
            try:
                data = mod.collect_agent_stats(con, days=30, project_dir=proj_dir, now_ms=self.NOW_MS)
            finally:
                con.close()

        by_agent = data["by_agent"]
        self.assertIn("(raíz)", by_agent)
        self.assertEqual(by_agent["(raíz)"]["sessions"], 1)
        self.assertEqual(by_agent["(raíz)"]["dur_ms"], 24 * 3_600_000)
        self.assertEqual(by_agent["coder"]["sessions"], 1)
        self.assertEqual(by_agent["coder"]["child_sessions"], 1)
        self.assertEqual(by_agent["coder"]["dur_ms"], 2 * 3_600_000)
        self.assertEqual(
            by_agent["coder"]["tokens"],
            {"input": 100, "output": 200, "reasoning": 50, "cache_read": 10, "cache_write": 5},
        )

        delegations = data["delegations"]
        self.assertEqual((delegations["total"], delegations["ok"], delegations["error"]), (2, 1, 1))
        self.assertEqual(delegations["by_type"]["coder"]["ok"], 1)
        self.assertEqual(delegations["by_type"]["reviewer"]["error"], 1)

        self.assertEqual(data["skills"], {"wait-for": 1})
        self.assertEqual(data["errors"]["total"], 2)
        self.assertEqual(data["errors"]["spans"], 4)
        self.assertEqual(data["errors"]["ratio"], 0.5)
        self.assertEqual([e["name"] for e in data["errors"]["by_tool"]], ["read", "task"])

    def test_no_content_in_output(self) -> None:
        mod = self._load()
        with tempfile.TemporaryDirectory() as tmp_str:
            db, proj_dir, _ = self._make_db(Path(tmp_str))
            con = sqlite3.connect(str(db))
            try:
                data = mod.collect_agent_stats(con, days=30, project_dir=proj_dir, now_ms=self.NOW_MS)
            finally:
                con.close()
        blob = json.dumps(data, ensure_ascii=False)
        self.assertNotIn(self.PROMPT, blob)
        self.assertNotIn(proj_dir, blob)
        self.assertNotIn("prompt", blob.lower())

    def test_daily_matrix(self) -> None:
        mod = self._load()
        with tempfile.TemporaryDirectory() as tmp_str:
            db, proj_dir, _ = self._make_db(Path(tmp_str))
            con = sqlite3.connect(str(db))
            try:
                data = mod.collect_agent_stats(
                    con, days=30, project_dir=proj_dir, daily=True, now_ms=self.NOW_MS
                )
            finally:
                con.close()
        self.assertEqual(len(data["daily"]), 1)
        day = data["daily"][0]
        self.assertEqual(day["sessions"], 2)
        self.assertEqual(day["by_agent"]["coder"], 1.0)
        self.assertEqual(day["by_agent"]["(raíz)"], 0.5)

    def test_missing_db_is_pendiente(self) -> None:
        import contextlib
        import io

        mod = self._load()
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            mod.DEFAULT_DB = tmp / "missing.db"
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = mod.main(["--project", str(tmp / "wt")])
        self.assertEqual(code, 1)
        self.assertIn("PENDIENTE", err.getvalue())


class TestMetricsDocs(unittest.TestCase):
    """Enforcer: los scripts de métricas quedan documentados."""

    def test_metrics_scripts_documented(self) -> None:
        files = (
            ROOT / ".opencode" / "skill" / "factory-report" / "SKILL.md",
            ROOT / "AGENTS.md",
            ROOT / "README.md",
        )
        for f in files:
            text = f.read_text(encoding="utf-8")
            for script in ("run-stats.py", "agent-stats.py"):
                with self.subTest(file=str(f), script=script):
                    self.assertIn(script, text)


BLIND_SLEEP = r"(?:^|[;&|(]\s*)sleep\s+\d+\s*&&"


class TestWaitUntil(unittest.TestCase):
    """wait_until.sh: salida temprana, techo duro, stdout pipeable limpio."""

    def _fake_cmd(self, tmp: Path, ready_after: int) -> Path:
        counter = tmp / "counter"
        script = tmp / "fake.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            f'c="$(cat "{counter}" 2>/dev/null || echo 0)"\n'
            "c=$((c + 1))\n"
            f'echo "$c" > "{counter}"\n'
            f'if [ "$c" -ge {ready_after} ]; then echo READY; else echo "attempt $c"; exit 1; fi\n'
        )
        script.chmod(0o755)
        return script

    def test_early_exit_and_clean_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = self._fake_cmd(Path(tmp), ready_after=3)
            result = subprocess.run(
                ["bash", str(SCRIPTS / "wait_until.sh"), "15", "1", "--", "bash", str(script)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "READY\n")
            self.assertIn("ok tras", result.stderr)
            self.assertIn("attempt", result.stderr)

    def test_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = self._fake_cmd(Path(tmp), ready_after=9999)
            result = subprocess.run(
                ["bash", str(SCRIPTS / "wait_until.sh"), "3", "1", "--", "bash", str(script)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("TIMEOUT", result.stderr)
            self.assertNotIn("READY", result.stdout)

    def test_usage_error(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPTS / "wait_until.sh"), "5", "1"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)


class TestNoBlindSleep(unittest.TestCase):
    """Enforcer: el anti-patrón `sleep N && check` no entra al harness."""

    def test_detector_self_test(self) -> None:
        import re

        pattern = re.compile(BLIND_SLEEP)
        self.assertIsNotNone(pattern.search("sleep 30 && gh pr checks"))
        self.assertIsNotNone(pattern.search("x || (sleep 10 && retry)"))
        self.assertIsNone(pattern.search('sleep "$IV"'))  # intervalo de un poll
        self.assertIsNone(pattern.search("sleep 10\ndone"))  # intervalo solo
        self.assertIsNone(pattern.search("sleep 10; done"))  # intervalo en loop

    def test_repo_clean(self) -> None:
        import re

        pattern = re.compile(BLIND_SLEEP)
        targets = (
            list((ROOT / ".opencode").rglob("*.md"))
            + [p for p in SCRIPTS.rglob("*.sh") if p.name != "test_harness.py"]
            + [p for p in SCRIPTS.rglob("*.py") if p.name != "test_harness.py"]
        )
        self.assertTrue(targets)
        for f in targets:
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if "anti-patrón" in line:
                    continue  # ejemplos didácticos marcados
                with self.subTest(file=str(f), line=i):
                    self.assertIsNone(pattern.search(line), line)


class TestMergeGuard(unittest.TestCase):
    """merge.sh: resuelve el PR por rama head y falla cerrado.

    Clase de error que codifica: un merge con número literal fusionó el PR de
    otra sesión. El fake gh emula `pr view <rama>` (metadata y
    `statusCheckRollup`) y `pr merge`. El texto de `pr checks` reproduce el caso
    #309 (SKIPPED tapando a la SUCCESS hermana) y merge.sh no debe usarlo: si
    vuelve a parsear texto, el test de duplicados falla.
    La frescura de la base se modela con `baseRefOid == headRefOid` (head ==
    base ⇒ gates frescos): los escenarios stale/unknown con git real viven en
    TestBaseGateMergeChain.
    """

    HEAD = "a" * 40
    MERGE = "b" * 40

    # Rollups de `gh pr view --json statusCheckRollup`. `__typename` distingue
    # CheckRun (status+conclusion) de StatusContext (state), igual que la API.
    ROLLUP_GREEN = '[{"__typename":"CheckRun","name":"tests","status":"COMPLETED","conclusion":"SUCCESS"}]'
    # x6200 #309: el workflow A/B registra DOS corridas por PR; la de
    # opened/synchronize queda SKIPPED (gate de la etiqueta `ci`) y la de
    # labeled corre. Mismo nombre, entradas SKIPPED y SUCCESS.
    ROLLUP_DUPS = (
        '[{"__typename":"CheckRun","name":"UI Visual Regression",'
        '"status":"COMPLETED","conclusion":"SKIPPED"},'
        '{"__typename":"CheckRun","name":"UI Visual Regression",'
        '"status":"COMPLETED","conclusion":"SUCCESS"},'
        '{"__typename":"CheckRun","name":"UI Performance (render benchmark, host)",'
        '"status":"COMPLETED","conclusion":"SKIPPED"},'
        '{"__typename":"CheckRun","name":"UI Performance (render benchmark, host)",'
        '"status":"COMPLETED","conclusion":"SUCCESS"}]'
    )
    ROLLUP_ALL_SKIPPED = (
        '[{"__typename":"CheckRun","name":"UI Visual Regression",'
        '"status":"COMPLETED","conclusion":"SKIPPED"}]'
    )
    ROLLUP_FAILURE = (
        '[{"__typename":"CheckRun","name":"tests",'
        '"status":"COMPLETED","conclusion":"SUCCESS"},'
        '{"__typename":"StatusContext","context":"coverage","state":"FAILURE"}]'
    )
    ROLLUP_PENDING = '[{"__typename":"CheckRun","name":"tests","status":"IN_PROGRESS","conclusion":""}]'

    FAKE_GH = """#!/usr/bin/env bash
# fake gh para TestMergeGuard
case "$1 $2" in
  "pr view")
    if [[ "$*" == *statusCheckRollup* ]]; then
      case "$3" in
        factory/dups-skipped) printf '{"statusCheckRollup":%s}\\n' "$ROLLUP_DUPS" ;;
        factory/all-skipped) printf '{"statusCheckRollup":%s}\\n' "$ROLLUP_ALL_SKIPPED" ;;
        factory/failing) printf '{"statusCheckRollup":%s}\\n' "$ROLLUP_FAILURE" ;;
        factory/pending) printf '{"statusCheckRollup":%s}\\n' "$ROLLUP_PENDING" ;;
        factory/no-checks) printf '{"statusCheckRollup":[]}\\n' ;;
        *) printf '{"statusCheckRollup":%s}\\n' "$ROLLUP_GREEN" ;;
      esac
      exit 0
    fi
    if [[ "$*" == *mergeCommit* ]]; then
      printf '{"mergeCommit":{"oid":"%s"},"headRefOid":"%s"}\\n' "$MERGE_SHA" "$HEAD_SHA"
      exit 0
    fi
    if [ "$3" = factory/mala ]; then
      echo "no pull requests found for branch \\"$3\\"" >&2
      exit 1
    fi
    if [ "$3" = factory/unknown ]; then
      if [[ "$*" == *number,state,mergeable* ]]; then
        printf '{"number":77,"state":"OPEN","mergeable":"UNKNOWN","mergeStateStatus":"UNKNOWN","headRefOid":"%s","headRefName":"factory/unknown","baseRefOid":"%s"}\\n' "$HEAD_SHA" "$HEAD_SHA"
      else
        echo '{"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN"}'
      fi
      exit 0
    fi
    if [ "$3" = factory/dirty ]; then
      printf '{"number":77,"state":"OPEN","mergeable":"MERGEABLE","mergeStateStatus":"DIRTY","headRefOid":"%s","headRefName":"factory/dirty","baseRefOid":"%s"}\\n' "$HEAD_SHA" "$HEAD_SHA"
      exit 0
    fi
    printf '{"number":77,"state":"OPEN","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","headRefOid":"%s","headRefName":"%s","baseRefOid":"%s"}\\n' "$HEAD_SHA" "$3" "$HEAD_SHA"
    exit 0
    ;;
  "pr checks")
    # Texto real del caso x6200 #309: la corrida SKIPPED del workflow A/B tapa
    # a la SUCCESS hermana. merge.sh NO debe parsear esto; si vuelve a hacerlo,
    # test_duplicate_skipped_plus_success_is_green falla (el bug vuelve).
    printf 'UI Visual Regression\\tskipping\\t0\\thttps://example/1\\n'
    printf 'UI Performance (render benchmark, host)\\tskipping\\t0\\thttps://example/2\\n'
    printf 'UI Functional Tests\\tpass\\t20s\\thttps://example/3\\n'
    exit 0
    ;;
  "pr merge")
    echo merged > "$MERGE_LOG"
    exit 0
    ;;
esac
echo "fake gh: args no manejados: $*" >&2
exit 1
"""

    def _fake_env(self, tmp: Path) -> dict[str, str]:
        fake = tmp / "gh"
        fake.write_text(self.FAKE_GH)
        fake.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{tmp}:{env['PATH']}"
        env["HEAD_SHA"] = self.HEAD
        env["MERGE_SHA"] = self.MERGE
        env["MERGE_LOG"] = str(tmp / "merged.log")
        env["ROLLUP_GREEN"] = self.ROLLUP_GREEN
        env["ROLLUP_DUPS"] = self.ROLLUP_DUPS
        env["ROLLUP_ALL_SKIPPED"] = self.ROLLUP_ALL_SKIPPED
        env["ROLLUP_FAILURE"] = self.ROLLUP_FAILURE
        env["ROLLUP_PENDING"] = self.ROLLUP_PENDING
        return env

    def _run(self, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPTS / "merge.sh"), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_shadow_validates_without_merging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fake_env(Path(tmp))
            r = self._run(env, "asf", "factory/buena")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("ok:", r.stdout)
            self.assertNotIn("merged:", r.stdout)
            self.assertFalse(Path(env["MERGE_LOG"]).exists())

    def test_apply_merges_by_resolved_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fake_env(Path(tmp))
            r = self._run(env, "asf", "factory/buena", "--apply")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(f"merged: 77 {self.MERGE}", r.stdout)
            self.assertTrue(Path(env["MERGE_LOG"]).exists())

    def test_branch_without_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fake_env(Path(tmp))
            r = self._run(env, "asf", "factory/mala")
            self.assertEqual(r.returncode, 1)
            self.assertIn("sin PR abierto para la rama", r.stderr)

    def test_expected_sha_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fake_env(Path(tmp))
            r = self._run(env, "asf", "factory/buena", "--expected-sha", "deadbeef")
            self.assertEqual(r.returncode, 1)
            self.assertIn("expected-sha", r.stderr)

    def test_numeric_literal_rejected(self) -> None:
        # ni siquiera toca gh: el número literal es el anti-patrón.
        r = subprocess.run(
            ["bash", str(SCRIPTS / "merge.sh"), "asf", "42"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("número de PR", r.stderr)
        self.assertIn("uso: merge.sh", r.stderr)

    def test_dirty_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fake_env(Path(tmp))
            r = self._run(env, "asf", "factory/dirty")
            self.assertEqual(r.returncode, 1)
            self.assertIn("MERGEABLE/CLEAN", r.stderr)

    def test_unknown_waits_then_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fake_env(Path(tmp))
            r = self._run(env, "asf", "factory/unknown")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("ok:", r.stdout)

    def test_duplicate_skipped_plus_success_is_green(self) -> None:
        # x6200 #309: el workflow A/B registra dos corridas por PR; la SKIPPED
        # (sin etiqueta `ci`) tapaba a la SUCCESS hermana en el texto de
        # `gh pr checks` y el merge se trababa en falso.
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fake_env(Path(tmp))
            r = self._run(env, "asf", "factory/dups-skipped")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("ok:", r.stdout)
            self.assertNotIn("no verdes", r.stderr)

    def test_only_skipped_blocks(self) -> None:
        # SKIPPED no es verde: un check que no corrió no es un gate.
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fake_env(Path(tmp))
            r = self._run(env, "asf", "factory/all-skipped")
            self.assertEqual(r.returncode, 1)
            self.assertIn("con checks no verdes", r.stderr)
            self.assertIn("UI Visual Regression=skipped", r.stderr)
            self.assertIn("etiqueta ci", r.stderr)
            self.assertNotIn("ok:", r.stdout)

    def test_failure_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fake_env(Path(tmp))
            r = self._run(env, "asf", "factory/failing")
            self.assertEqual(r.returncode, 1)
            self.assertIn("con checks no verdes", r.stderr)
            self.assertIn("coverage=failure", r.stderr)
            self.assertNotIn("ok:", r.stdout)

    def test_pending_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fake_env(Path(tmp))
            r = self._run(env, "asf", "factory/pending")
            self.assertEqual(r.returncode, 1)
            self.assertIn("con checks no verdes", r.stderr)
            self.assertIn("tests=pending", r.stderr)
            self.assertNotIn("ok:", r.stdout)

    def test_no_checks_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fake_env(Path(tmp))
            r = self._run(env, "asf", "factory/no-checks")
            self.assertEqual(r.returncode, 1)
            self.assertIn("sin checks reportados", r.stderr)
            self.assertNotIn("ok:", r.stdout)


class TestMergeGuardLocalMatrix(unittest.TestCase):
    """merge.sh con `ci.provider: none`: el gate es la matriz local CORRIDA.

    Sin CI de GitHub no hay rollup: leerlo sería inventar un veredicto. El gate
    es la matriz del registry (`checks`), CORRIDA contra base/head con el
    contrato `<base> <head> <checkout>`. El body del PR no se lee: es prosa, y un
    fence con `check-matrix` (o un "no corrí check-matrix") lo satisfacía sin
    haber corrido nada. `none` nunca se cuenta como verde: sin `checks`
    declarado, con la matriz en rojo o sin poder traer los refs, el merge se
    bloquea y el rollup ni se consulta.
    """

    FAKE_MATRIX = """#!/usr/bin/env bash
# matriz local falsa: registra sus argumentos y falla si MATRIX_FAIL
printf '%s\\n' "$*" > "$MATRIX_ARGS"
echo "==> [check] fake matrix"
if [ -n "${MATRIX_FAIL:-}" ]; then
  echo "FAIL: gate de prueba rojo"
  echo "    BLOCKED: entorno de prueba ausente"
  exit "$MATRIX_FAIL"
fi
echo "    ok"
exit 0
"""

    # Si merge.sh consulta el rollup o el body con provider=none, el fake lo
    # registra y falla: el test lo detecta por el archivo y por el exit code.
    FAKE_GH = """#!/usr/bin/env bash
set -u
case "$1 $2" in
  "pr view")
    if [[ "$*" == *statusCheckRollup* ]]; then
      echo "statusCheckRollup" >> "$ROLLUP_LOG"
      if [ -n "${ROLLUP_JSON:-}" ]; then
        printf '{"statusCheckRollup":%s}\\n' "$ROLLUP_JSON"
        exit 0
      fi
      exit 1
    fi
    if [[ "$*" == *--json*body* ]]; then
      echo "body" >> "$BODY_LOG"
      exit 1
    fi
    printf '{"number":77,"state":"OPEN","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","headRefOid":"%s","headRefName":"factory/local","baseRefOid":"%s"}\\n' "$HEAD_SHA" "$BASE_SHA"
    exit 0
    ;;
  "pr merge")
    echo merged > "$MERGE_LOG"
    exit 0
    ;;
esac
echo "fake gh: args no manejados: $*" >&2
exit 1
"""

    def _fixture(self, tmp: Path, pointer: dict[str, Any] | None = None) -> tuple[Path, dict[str, str]]:
        """Harness aislado con un checkout git REAL (merge.sh hace fetch) y una
        matriz local falsa en el path que declara el registry."""
        harness = _isolated_harness(tmp, pointer=pointer)
        checkout = harness / "work" / "fakeapp"
        git_env = dict(
            os.environ,
            GIT_AUTHOR_NAME="test",
            GIT_AUTHOR_EMAIL="test@example.invalid",
            GIT_COMMITTER_NAME="test",
            GIT_COMMITTER_EMAIL="test@example.invalid",
        )

        def git(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=git_env)

        origin = tmp / "origin.git"
        git("init", "-q", "--bare", str(origin))
        git("init", "-q", "-b", "main", str(checkout))
        git("-C", str(checkout), "commit", "-q", "--allow-empty", "-m", "base")
        git("-C", str(checkout), "remote", "add", "origin", str(origin))
        git("-C", str(checkout), "push", "-q", "origin", "main")
        sha = git("-C", str(checkout), "rev-parse", "HEAD").stdout.strip()

        matrix = harness / "scripts" / "apps" / "fakeapp" / "check-matrix.sh"
        matrix.parent.mkdir(parents=True)
        matrix.write_text(self.FAKE_MATRIX)
        matrix.chmod(0o755)

        state = tmp / "ghstate"
        state.mkdir()
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "gh"
        fake.write_text(self.FAKE_GH)
        fake.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        # base == head: los gates de la base son frescos (la frescura se prueba
        # aparte, en TestBaseGateMergeChain).
        env["HEAD_SHA"] = sha
        env["BASE_SHA"] = sha
        env["MATRIX_ARGS"] = str(state / "matrix.args")
        env["ROLLUP_LOG"] = str(state / "rollup.log")
        env["BODY_LOG"] = str(state / "body.log")
        env["MERGE_LOG"] = str(state / "merged.log")
        return harness, env

    def _run(self, harness: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(harness / "scripts" / "merge.sh"), "fakeapp", "factory/local", *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(harness),
        )

    def _rollup_was_read(self, env: dict[str, str]) -> bool:
        return Path(env["ROLLUP_LOG"]).exists()

    def _body_was_read(self, env: dict[str, str]) -> bool:
        return Path(env["BODY_LOG"]).exists()

    def test_matrix_green_allows_the_shadow_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(
                Path(tmp),
                {"ci": {"provider": "none"}, "checks": "scripts/apps/fakeapp/check-matrix.sh"},
            )
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(
                "ok: checks=matriz-local (fakeapp: CI de GitHub deshabilitado, la matriz corrió)",
                r.stdout,
            )
            self.assertIn("ok: PR #77", r.stdout)
            self.assertIn("checks=matriz-local", r.stdout)
            self.assertNotIn("merged:", r.stdout)
            self.assertFalse(Path(env["MERGE_LOG"]).exists())
            self.assertFalse(self._rollup_was_read(env), "consultó statusCheckRollup con provider=none")
            self.assertFalse(self._body_was_read(env), "leyó el body del PR con provider=none")
            # La matriz se invoca con el contrato del factory: base head checkout.
            args = Path(env["MATRIX_ARGS"]).read_text().split()
            self.assertEqual(args[:2], [env["BASE_SHA"], env["HEAD_SHA"]])
            self.assertEqual(len(args), 3)
            self.assertEqual(Path(args[2]).name, "fakeapp")
            # Y el checkout va ABSOLUTO: los checks de la matriz lo reciben como
            # argumento y lo resuelven desde su propio cwd (un path relativo se
            # resolvía dos veces y el check moría con FileNotFoundError).
            self.assertTrue(os.path.isabs(args[2]), f"checkout relativo: {args[2]}")

    def test_matrix_runs_on_the_app_checkout_by_default(self) -> None:
        # Sin --checkout ni worktree de la corrida, el árbol es el checkout del
        # registry (comportamiento previo, explícito para no depender del nombre).
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(
                Path(tmp),
                {"ci": {"provider": "none"}, "checks": "scripts/apps/fakeapp/check-matrix.sh"},
            )
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            args = Path(env["MATRIX_ARGS"]).read_text().split()
            self.assertEqual(args[2], str(harness / "work" / "fakeapp"))
            self.assertIn(f"info: matriz sobre {harness / 'work' / 'fakeapp'}", r.stderr)

    def test_explicit_checkout_wins(self) -> None:
        # Los bloques de la matriz EJECUTAN scripts del repo: validarlos en el
        # checkout base validaría la versión vieja. --checkout manda.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(
                Path(tmp),
                {"ci": {"provider": "none"}, "checks": "scripts/apps/fakeapp/check-matrix.sh"},
            )
            pr_tree = Path(tmp) / "pr-tree"
            pr_tree.mkdir()
            r = self._run(harness, env, "--checkout", str(pr_tree))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            args = Path(env["MATRIX_ARGS"]).read_text().split()
            self.assertEqual(args[2], str(pr_tree))
            self.assertIn(f"info: matriz sobre {pr_tree}", r.stderr)

    def test_run_worktree_is_used_when_it_exists(self) -> None:
        # La rama head es factory/local: si existe el worktree de esa corrida, es
        # el árbol del PR y no el checkout base.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(
                Path(tmp),
                {"ci": {"provider": "none"}, "checks": "scripts/apps/fakeapp/check-matrix.sh"},
            )
            wt = harness / "state" / "worktrees" / "fakeapp" / "local"
            wt.mkdir(parents=True)
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            args = Path(env["MATRIX_ARGS"]).read_text().split()
            self.assertEqual(args[2], str(wt))
            self.assertIn(f"info: matriz sobre {wt}", r.stderr)

    def test_explicit_checkout_must_exist(self) -> None:
        # Un path inexistente no cae al base en silencio: sería validar otro árbol.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(
                Path(tmp),
                {"ci": {"provider": "none"}, "checks": "scripts/apps/fakeapp/check-matrix.sh"},
            )
            missing = Path(tmp) / "no-existe"
            r = self._run(harness, env, "--checkout", str(missing))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn(f"error: --checkout no existe: {missing}", r.stderr)
            self.assertNotIn("ok:", r.stdout)
            self.assertFalse(Path(env["MATRIX_ARGS"]).exists(), "corrió la matriz sobre otro árbol")

    def test_missing_matrix_checkout_blocks(self) -> None:
        # El checkout que se le pasa a la matriz tiene que existir para poder
        # absolutizarlo: sin el árbol, los checks resolverían contra otro cwd y el
        # error aparecería adentro de un check (FileNotFoundError opaco) en vez de
        # bloquear el merge con el motivo.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(
                Path(tmp),
                {"ci": {"provider": "none"}, "checks": "scripts/apps/fakeapp/check-matrix.sh"},
            )
            import shutil

            shutil.rmtree(harness / "work" / "fakeapp")
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("error: el checkout de la matriz no existe:", r.stderr)
            self.assertNotIn("ok:", r.stdout)
            self.assertFalse(Path(env["MATRIX_ARGS"]).exists(), "corrió la matriz sobre otro árbol")

    def test_matrix_failure_blocks_the_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(
                Path(tmp),
                {"ci": {"provider": "none"}, "checks": "scripts/apps/fakeapp/check-matrix.sh"},
            )
            env["MATRIX_FAIL"] = "1"
            r = self._run(harness, env, "--apply")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("error: PR #77: la matriz local de fakeapp falló (exit 1)", r.stderr)
            # El veredicto real de la matriz queda visible, no sólo su exit.
            self.assertIn("FAIL: gate de prueba rojo", r.stderr)
            self.assertIn("BLOCKED: entorno de prueba ausente", r.stderr)
            self.assertNotIn("ok:", r.stdout)
            self.assertFalse(Path(env["MERGE_LOG"]).exists(), "mergeó con la matriz en rojo")

    def test_missing_checks_declaration_blocks(self) -> None:
        # Control negativo: sin matriz declarada en el registry no hay gate.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), {"ci": {"provider": "none"}})
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn(
                "error: fakeapp no declara matriz local (checks) y no tiene CI de GitHub",
                r.stderr,
            )
            self.assertNotIn("ok:", r.stdout)
            self.assertFalse(Path(env["MATRIX_ARGS"]).exists())
            self.assertFalse(self._rollup_was_read(env), "consultó statusCheckRollup con provider=none")

    def test_body_declaring_check_matrix_no_longer_enables_the_gate(self) -> None:
        # El panel adversarial: un `## Validación` con `check-matrix` en un fence
        # (o un "no corrí check-matrix") satisfacía el gate viejo sin correr nada.
        # Ahora el body no se lee: si la matriz falla, el merge se bloquea igual.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(
                Path(tmp),
                {"ci": {"provider": "none"}, "checks": "scripts/apps/fakeapp/check-matrix.sh"},
            )
            env["MATRIX_FAIL"] = "2"
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("la matriz local de fakeapp falló (exit 2)", r.stderr)
            self.assertNotIn("ok:", r.stdout)
            self.assertFalse(self._body_was_read(env), "el gate todavía depende del body del PR")

    def test_unfetchable_refs_block(self) -> None:
        # Sin poder traer base/head al checkout la matriz correría sobre otro
        # árbol: se bloquea en vez de reportar un verde que no probó el PR.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(
                Path(tmp),
                {"ci": {"provider": "none"}, "checks": "scripts/apps/fakeapp/check-matrix.sh"},
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(harness / "work" / "fakeapp"),
                    "remote",
                    "set-url",
                    "origin",
                    "/nonexistent/origin.git",
                ],
                check=True,
                capture_output=True,
            )
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("no se pudo actualizar", r.stderr)
            self.assertFalse(Path(env["MATRIX_ARGS"]).exists(), "corrió la matriz sin traer los refs")

    def test_github_provider_still_requires_the_rollup(self) -> None:
        # provider=github (default) no cambia: sigue leyendo el rollup y no corre
        # ninguna matriz local.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp))
            env["ROLLUP_JSON"] = (
                '[{"__typename":"CheckRun","name":"tests","status":"COMPLETED","conclusion":"SUCCESS"}]'
            )
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("ok:", r.stdout)
            self.assertIn("checks=pass", r.stdout)
            self.assertTrue(self._rollup_was_read(env), "provider=github no leyó statusCheckRollup")
            self.assertFalse(Path(env["MATRIX_ARGS"]).exists(), "corrió la matriz con provider=github")


class TestMergeGuardDocs(unittest.TestCase):
    """Enforcer: el camino de merge del harness se resuelve por rama, no por número."""

    FILES: ClassVar[tuple[Path, ...]] = (
        ROOT / "AGENTS.md",
        ROOT / ".opencode" / "command" / "factory-merge.md",
    )

    def test_merge_script_mentioned(self) -> None:
        for f in self.FILES:
            with self.subTest(file=str(f)):
                self.assertIn("merge.sh", f.read_text(encoding="utf-8"))

    def test_nunca_por_numero(self) -> None:
        for f in self.FILES:
            with self.subTest(file=str(f)):
                self.assertIn("nunca por número", f.read_text(encoding="utf-8").lower())


# ---------------------------------------------------------------------------
# merge-when-green.sh (babysit reparador, issue asf#41)
# ---------------------------------------------------------------------------

# Fake de `gh` para el wrapper reparador. El escenario activo sale de
# `SD/seq`: una línea por ronda de babysit, consumida por cada llamada
# `pr checks --json state` (la única que hace babysit para decidir). La
# clasificación corre DESPUÉS de la ronda que falló, así que lee la consumida
# (`prev_scenario`); los consumidores posteriores (merge.sh) leen la actual y,
# agotada la lista, persiste la última línea.
MWG_FAKE_GH = """#!/usr/bin/env bash
set -u
SD="__STATE__"
CO="__CHECKOUT__"
OR="__ORIGIN__"

idx() { cat "$SD/idx" 2>/dev/null || echo 0; }
bump() { echo "$(( $(idx) + 1 ))" > "$SD/idx"; }
last_scenario() { awk 'NF { last = $0 } END { print last }' "$SD/seq"; }
scenario() {
  local i line
  i="$(idx)"
  line="$(sed -n "$((i + 1))p" "$SD/seq" 2>/dev/null || true)"
  if [ -z "$line" ]; then last_scenario; else printf '%s' "$line"; fi
}
prev_scenario() {
  local i line
  i="$(idx)"
  if [ "$i" -lt 1 ]; then i=1; fi
  line="$(sed -n "${i}p" "$SD/seq" 2>/dev/null || true)"
  if [ -z "$line" ]; then last_scenario; else printf '%s' "$line"; fi
}
sha() { git -C "$OR" rev-parse feature 2>/dev/null || echo ""; }
entry() { cat "$SD/entry" 2>/dev/null || echo open; }

case "$1 $2" in
  "pr view")
    if [[ "$*" == *statusCheckRollup* ]]; then
      # merge.sh evalúa el rollup por nombre. La cadena sólo llega acá cuando
      # decidió mergear (CI verde), así que el rollup es verde.
      printf '{"statusCheckRollup":[{"name":"ci","status":"COMPLETED","conclusion":"SUCCESS"}]}\\n'
      exit 0
    fi
    if [[ "$*" == *mergeCommit* ]]; then
      printf '{"mergeCommit":{"oid":"%s"},"headRefOid":"%s"}\\n' "$(sha)" "$(sha)"
      exit 0
    fi
    if [[ "$*" == *number,state,mergeable* ]]; then
      st=OPEN
      ms=CLEAN
      if [ "$(entry)" = closed ]; then st=CLOSED; fi
      if [ "$(entry)" = dirty ]; then ms=DIRTY; fi
      printf '{"number":77,"state":"%s","mergeable":"MERGEABLE","mergeStateStatus":"%s","headRefOid":"%s","headRefName":"feature","baseRefOid":"%s"}\\n' "$st" "$ms" "$(sha)" "$(git -C "$OR" rev-parse main 2>/dev/null || echo "")"
      exit 0
    fi
    if [[ "$*" == *headRefOid* ]]; then
      sha
      exit 0
    fi
    if [[ "$*" == *baseRefOid* ]]; then
      git -C "$OR" rev-parse main 2>/dev/null || echo ""
      exit 0
    fi
    if [[ "$*" == *mergeable,mergeStateStatus* ]]; then
      printf '{"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN"}\\n'
      exit 0
    fi
    if [[ "$*" == *--jq* ]]; then
      printf 'MERGEABLE\\n'
      exit 0
    fi
    printf '{"mergeable":"MERGEABLE"}\\n'
    exit 0
    ;;
  "pr checks")
    if [[ "$*" == *--watch* ]]; then exit 0; fi
    if [[ "$*" == *"--json state"* ]]; then
      if [ "$(scenario)" = pass ]; then
        printf '[{"state":"SUCCESS"}]\\n'
      else
        printf '[{"state":"FAILURE"}]\\n'
      fi
      bump
      exit 0
    fi
    if [[ "$*" == *"--json name,state,link"* ]]; then
      case "$(prev_scenario)" in
        pass) printf '[{"name":"ci","state":"SUCCESS","link":"https://gh.example/runs/1000"}]' ;;
        flake) printf '[{"name":"Performance","state":"FAILURE","link":"https://gh.example/runs/1001"},{"name":"Performance (replica)","state":"SUCCESS","link":"https://gh.example/runs/1001"}]' ;;
        pixels) printf '[{"name":"Visual","state":"FAILURE","link":"https://gh.example/runs/1002"},{"name":"Unit tests","state":"SUCCESS","link":"https://gh.example/runs/1002"}]' ;;
        pytest) printf '[{"name":"pytest","state":"FAILURE","link":"https://gh.example/runs/1003"}]' ;;
        *) printf '[]' ;;
      esac
      exit 0
    fi
    case "$(scenario)" in
      flake) printf 'Performance\\tfail\\t20s\\thttps://gh.example/runs/1001\\nPerformance (replica)\\tpass\\t20s\\thttps://gh.example/runs/1001\\n' ;;
      pixels) printf 'Visual\\tfail\\t20s\\thttps://gh.example/runs/1002\\nUnit tests\\tpass\\t20s\\thttps://gh.example/runs/1002\\n' ;;
      pytest) printf 'pytest\\tfail\\t20s\\thttps://gh.example/runs/1003\\n' ;;
      *) printf 'ci\\tpass\\t20s\\thttps://gh.example/runs/1000\\n' ;;
    esac
    exit 0
    ;;
  "pr merge")
    echo "$*" >> "$SD/merge.log"
    exit 0
    ;;
  "run rerun")
    echo "$*" >> "$SD/rerun.log"
    exit 0
    ;;
  "run view")
    if [[ "$*" == *--json*status* ]]; then
      printf 'in_progress\\n'
      exit 0
    fi
    if [[ "$*" == *--log* ]]; then
      cat "$SD/log-$3" 2>/dev/null || true
      exit 0
    fi
    exit 0
    ;;
esac
echo "fake gh: args no manejados: $*" >&2
exit 1
"""

MWG_LOGS = {
    "log-1001": "Performance\trender-cost proxy over budget: 1.42 > 1.20\nBench\tp95_delta_pct=18.4 (budget 10)\n",
    "log-1002": "Visual\tchanged_pixels: 3.2% (umbral 0.5%)\n",
    "log-1003": "pytest\tFAILED tests/test_x.py::test_y - AssertionError: boom\n",
}


@dataclass
class MwgFixture:
    """Harness fake en tmp: registry `fakeapp` + repo git real + fake de gh."""

    tmp: Path
    origin: Path
    checkout: Path
    ghstate: Path
    trunk_sha: str
    feature_sha: str
    worktree: Path | None


class TestMergeWhenGreen(unittest.TestCase):
    """merge-when-green.sh: babysit reparador (clase asf#41).

    El fake de `gh` maneja el escenario de CI plano a plano; el git es REAL
    (origin bare + clone), así el probe `merge-tree`, el rebase y el
    `push --force-with-lease` de la CLASE B se ejercitan de verdad. El harness
    se copia a un tmp (scripts/ + registry `fakeapp`) y el `state/` de las
    corridas queda aislado ahí.
    """

    IDENTITY: ClassVar[dict[str, str]] = {
        "GIT_AUTHOR_NAME": "Asf Test",
        "GIT_AUTHOR_EMAIL": "asf-test@example.com",
        "GIT_COMMITTER_NAME": "Asf Test",
        "GIT_COMMITTER_EMAIL": "asf-test@example.com",
    }

    # --- helpers de fixture ------------------------------------------------

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self.IDENTITY)
        return env

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=self._env())

    def _rev(self, repo: Path, ref: str) -> str:
        return self._git("-C", str(repo), "rev-parse", ref).stdout.strip()

    def _commit(self, repo: Path, message: str) -> None:
        self._git("-C", str(repo), "add", "-A")
        self._git("-C", str(repo), "commit", "-m", message)

    def _seed(self, tmp: Path, *, conflict: bool) -> tuple[Path, Path, str, str]:
        """origin bare + clone con `main` (avanzado) y `feature` divergente."""
        origin = tmp / "origin.git"
        self._git("init", "--bare", "-b", "main", str(origin))
        seed = tmp / "seed"
        seed.mkdir()
        self._git("init", "-b", "main", str(seed))
        (seed / "app.txt").write_text("line1\nline2\n")
        self._commit(seed, "base")
        self._git("-C", str(seed), "remote", "add", "origin", str(origin))
        self._git("-C", str(seed), "push", "-u", "origin", "main")

        self._git("-C", str(seed), "checkout", "-b", "feature")
        (seed / "feature.txt").write_text("feature\n")
        if conflict:
            (seed / "app.txt").write_text("line1-feature\nline2\n")
        self._commit(seed, "feature")
        self._git("-C", str(seed), "push", "-u", "origin", "feature")
        feature_sha = self._rev(seed, "feature")

        self._git("-C", str(seed), "checkout", "main")
        (seed / "trunk.txt").write_text("trunk\n")
        if conflict:
            (seed / "app.txt").write_text("line1-trunk\nline2\n")
        self._commit(seed, "trunk")
        self._git("-C", str(seed), "push", "origin", "main")
        trunk_sha = self._rev(seed, "main")

        checkout = tmp / "work" / "fakeapp"
        checkout.parent.mkdir(parents=True, exist_ok=True)
        self._git("clone", str(origin), str(checkout))
        # El harness real crea la rama local de la corrida (new-worktree.sh):
        # el head vive checked out en un worktree, no sólo como ref remota.
        self._git("-C", str(checkout), "branch", "feature", "origin/feature")
        return origin, checkout, trunk_sha, feature_sha

    def _fixture(
        self,
        tmp: Path,
        *,
        seq: tuple[str, ...] = ("pass",),
        entry: str = "open",
        conflict: bool = False,
        run_worktree: bool = False,
        local_branch: bool = True,
    ) -> MwgFixture:
        import shutil

        shutil.copytree(SCRIPTS, tmp / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        apps_dir = tmp / "config" / "apps"
        apps_dir.mkdir(parents=True)
        origin, checkout, trunk_sha, feature_sha = self._seed(tmp, conflict=conflict)
        (apps_dir / "fakeapp.json").write_text(
            json.dumps(
                {
                    "id": "fakeapp",
                    "repo": "f/o",
                    "remote": str(origin),
                    "branch": "main",
                    "checkout": "work/fakeapp",
                }
            )
        )

        ghstate = tmp / "ghstate"
        ghstate.mkdir()
        (ghstate / "seq").write_text("\n".join(seq) + "\n")
        (ghstate / "idx").write_text("0\n")
        (ghstate / "entry").write_text(entry + "\n")
        for name, body in MWG_LOGS.items():
            (ghstate / name).write_text(body)

        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        fake_gh = bin_dir / "gh"
        fake_gh.write_text(
            MWG_FAKE_GH.replace("__STATE__", str(ghstate))
            .replace("__CHECKOUT__", str(checkout))
            .replace("__ORIGIN__", str(origin))
        )
        fake_gh.chmod(0o755)

        wt = None
        if run_worktree:
            wt = tmp / "wt-feature"
            self._git("-C", str(checkout), "worktree", "add", str(wt), "feature")
        if not local_branch:
            self._git("-C", str(checkout), "branch", "-D", "feature")

        return MwgFixture(
            tmp=tmp,
            origin=origin,
            checkout=checkout,
            ghstate=ghstate,
            trunk_sha=trunk_sha,
            feature_sha=feature_sha,
            worktree=wt,
        )

    def _run(self, fx: MwgFixture, *args: str) -> subprocess.CompletedProcess[str]:
        env = self._env()
        env["PATH"] = f"{fx.tmp / 'bin'}:{env['PATH']}"
        return subprocess.run(
            ["bash", str(fx.tmp / "scripts" / "merge-when-green.sh"), "fakeapp", *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(fx.tmp),
            timeout=180,
        )

    @staticmethod
    def _rows(result: subprocess.CompletedProcess[str]) -> list[list[str]]:
        return [line.split("\t") for line in result.stdout.splitlines() if line.strip()]

    @staticmethod
    def _row(rows: list[list[str]], action: str) -> list[str]:
        for row in rows:
            if len(row) > 3 and row[3] == action:
                return row
        raise AssertionError(f"sin fila de trail con acción {action!r}: {rows}")

    def _remote_head(self, fx: MwgFixture) -> str:
        return self._rev(fx.origin, "feature")

    # --- escenario verde ---------------------------------------------------

    def test_shadow_green_prints_plan_without_executing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            fx = self._fixture(tmp)
            r = self._run(fx, "77", "--timeout-min", "1", "--poll-s", "1")
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = self._rows(r)
            self.assertEqual(rows[0][2:4], ["-", "validate"])
            self.assertIn("shadow", self._row(rows, "merge")[4])
            self.assertFalse((fx.ghstate / "merge.log").exists(), "shadow no debe mergear")

    def test_enabled_green_merges_and_writes_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            fx = self._fixture(tmp)
            state = tmp / "ghstate" / "mwg-state.json"
            r = self._run(
                fx,
                "77",
                "--mode",
                "enabled",
                "--timeout-min",
                "1",
                "--poll-s",
                "1",
                "--state",
                str(state),
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = self._rows(r)
            merge_row = self._row(rows, "merge")
            self.assertIn("merged:", merge_row[4])
            self.assertEqual(merge_row[5], "sha=" + fx.feature_sha)
            self.assertTrue((fx.ghstate / "merge.log").exists())
            doc = json.loads(state.read_text())
            self.assertEqual(doc["exit"], 0)
            self.assertEqual(doc["mode"], "enabled")
            self.assertEqual(doc["repairs_used"], 0)
            self.assertEqual(doc["classes"], [])
            self.assertEqual(doc["rounds"], 1)

    # --- CLASE A -----------------------------------------------------------

    def test_class_a_reruns_failed_run_then_merges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            fx = self._fixture(tmp, seq=("flake", "pass"))
            state = tmp / "ghstate" / "mwg-state.json"
            r = self._run(
                fx,
                "77",
                "--mode",
                "enabled",
                "--timeout-min",
                "2",
                "--poll-s",
                "1",
                "--state",
                str(state),
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = self._rows(r)
            rerun = self._row(rows, "rerun")
            self.assertEqual(rerun[2], "A")
            self.assertIn("1001", rerun[4])
            self.assertIn("--failed", (fx.ghstate / "rerun.log").read_text())
            self.assertTrue((fx.ghstate / "merge.log").exists())
            doc = json.loads(state.read_text())
            self.assertEqual(doc["classes"], ["A"])
            self.assertEqual(doc["repairs_used"], 1)
            self.assertEqual(doc["rounds"], 2)

    # --- CLASE B -----------------------------------------------------------

    def test_class_b_rebases_in_run_worktree_then_merges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            fx = self._fixture(tmp, seq=("pixels", "pass"), run_worktree=True)
            r = self._run(fx, "77", "--mode", "enabled", "--timeout-min", "2", "--poll-s", "1")
            self.assertEqual(r.returncode, 0, r.stderr)
            rebase = self._row(self._rows(r), "rebase")
            self.assertEqual(rebase[2], "B")
            self.assertNotIn("shadow", rebase[4])
            # El trunk quedó integrado: el rebase + push realmente ocurrieron.
            self.assertEqual(
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(fx.origin),
                        "merge-base",
                        "--is-ancestor",
                        str(fx.trunk_sha),
                        "feature",
                    ],
                    capture_output=True,
                    env=self._env(),
                ).returncode,
                0,
            )
            self.assertTrue((fx.ghstate / "merge.log").exists())

    def test_class_b_creates_temp_worktree_when_no_local_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            fx = self._fixture(tmp, seq=("pixels", "pass"), local_branch=False)
            r = self._run(fx, "77", "--mode", "enabled", "--timeout-min", "2", "--poll-s", "1")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self._row(self._rows(r), "rebase")[2], "B")
            self.assertEqual(self._rev(fx.checkout, "feature"), self._remote_head(fx))
            self.assertTrue((fx.ghstate / "merge.log").exists())

    def test_class_b_rebase_conflict_exits_5_without_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            fx = self._fixture(tmp, seq=("pixels",), conflict=True)
            r = self._run(fx, "77", "--mode", "enabled", "--timeout-min", "1", "--poll-s", "1")
            self.assertEqual(r.returncode, 5, r.stderr)
            rebase = self._row(self._rows(r), "rebase")
            self.assertEqual(rebase[2], "B")
            self.assertIn("conflicto", rebase[4])
            # Nada se pusheó: el head remoto sigue en el sha original.
            self.assertEqual(self._remote_head(fx), fx.feature_sha)
            self.assertFalse((fx.ghstate / "merge.log").exists())

    # --- CLASE C -----------------------------------------------------------

    def test_class_c_writes_evidence_and_exits_5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            fx = self._fixture(tmp, seq=("pytest",))
            r = self._run(fx, "77", "--mode", "enabled", "--timeout-min", "1", "--poll-s", "1")
            self.assertEqual(r.returncode, 5, r.stderr)
            row = self._row(self._rows(r), "evidence")
            self.assertEqual(row[2], "C")
            ev = Path(row[4].split(" (")[0])
            self.assertTrue(ev.exists(), f"falta la evidencia {ev}")
            self.assertIn("FAILED tests/test_x.py::test_y", ev.read_text())
            self.assertIn("head:", ev.read_text())
            self.assertEqual(ev.name, "repair-evidence-77-1.txt")
            self.assertEqual(ev.parent, tmp / "state" / "reports" / "fakeapp" / "merge-when-green-pr-77")
            self.assertFalse((fx.ghstate / "rerun.log").exists())
            self.assertFalse((fx.ghstate / "merge.log").exists())

    # --- techo y validación de entrada -------------------------------------

    def test_max_repairs_exhausted_exits_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            fx = self._fixture(tmp, seq=("flake", "flake"))
            r = self._run(
                fx,
                "77",
                "--mode",
                "enabled",
                "--timeout-min",
                "2",
                "--poll-s",
                "1",
                "--max-repairs",
                "1",
            )
            self.assertEqual(r.returncode, 3, r.stderr)
            rows = self._rows(r)
            self.assertEqual(self._row(rows, "budget")[2], "A")
            self.assertEqual(len((fx.ghstate / "rerun.log").read_text().splitlines()), 1)
            self.assertFalse((fx.ghstate / "merge.log").exists())

    def test_entry_not_mergeable_exits_4(self) -> None:
        for entry, expected in (("closed", "no está OPEN"), ("dirty", "MERGEABLE/CLEAN")):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as tmp_str:
                tmp = Path(tmp_str)
                fx = self._fixture(tmp, entry=entry)
                r = self._run(fx, "77", "--timeout-min", "1", "--poll-s", "1")
                self.assertEqual(r.returncode, 4, r.stderr)
                self.assertIn(expected, r.stderr)
                self.assertEqual(r.stdout, "")

    def test_usage_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            fx = self._fixture(Path(tmp_str))
            for args, expected in (
                (("factory/rama",), "no es un número de PR"),
                (("77", "--mode", "turbo"), "shadow|enabled"),
                (("77", "--max-repairs", "dos"), "enteros"),
            ):
                with self.subTest(args=args):
                    r = self._run(fx, *args)
                    self.assertEqual(r.returncode, 2, r.stdout)
                    self.assertIn(expected, r.stderr)


AGENT_MODEL = "opencode/deepseek-v4.1-flash"
# El coder corre con la variante free (200k) del mismo provider, por orden del
# owner; es el único subagent que no usa el pin barato común.
CODER_MODEL = "opencode/deepseek-v4.1-flash-free"
# Failover del coder sólo por rate-limit del free pool: DeepSeek V4.1 Flash
# non-free del mismo provider `opencode` (no depende del crédito de OpenRouter).
# No existe una variante gratuita del 1017.
CODER_FAILOVER_MODEL = "opencode/deepseek-v4.1-flash"
# Los 5 wrappers finos de curaduría en `.opencode/agent/`: resuelven el checkout
# de la app y ejecutan el rol canónico en `<checkout>/curation/` sin copiarlo.
# No tienen espejo `.pi` todavía (pendiente documentado, fuera de esta corrida).
CURATION_AGENTS: tuple[str, ...] = (
    "curation-labeler",
    "curation-judge",
    "curation-council",
    "curation-teacher",
    "curation-publisher",
)


def _frontmatter(path: Path) -> dict[str, str]:
    """Campos top-level del frontmatter YAML, sin depender de PyYAML.

    Sólo interesan escalares de primer nivel (`model`, `mode`); las claves
    anidadas (indentadas) se ignoran.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line or line[0].isspace():
            continue
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


class TestDelegationModelConfig(unittest.TestCase):
    """Regresión de configuración: implementa el coder DeepSeek barato; el
    orquestador coordina.

    - Los subagents de `.opencode/agent/*.md` fijan un DeepSeek barato en su
      frontmatter: `opencode/deepseek-v4.1-flash`, salvo `coder`, que fija
      `opencode/deepseek-v4.1-flash-free`.
    - El failover del coder (sólo rate-limit) apunta a
      `opencode/deepseek-v4.1-flash` (non-free, mismo provider).
    - `opencode.json` no fija el modelo del orquestador ni un provider Ollama.
    - `general`/`explore` corren con el modelo barato.
    - `build` puede delegar en el subagent `coder`.
    - `factory-correct` delega los fixes al coder en vez de implementarlos.
    """

    def test_every_subagent_pinned_to_deepseek(self) -> None:
        agents = sorted((ROOT / ".opencode" / "agent").glob("*.md"))
        self.assertTrue(agents, "no hay subagents en .opencode/agent/")
        for agent in agents:
            with self.subTest(agent=agent.name):
                fm = _frontmatter(agent)
                expected = CODER_MODEL if agent.name == "coder.md" else AGENT_MODEL
                self.assertEqual(fm.get("mode"), "subagent", f"{agent.name}: mode")
                self.assertEqual(fm.get("model"), expected, f"{agent.name}: model")

    def test_coder_exists_and_pinned(self) -> None:
        coder = ROOT / ".opencode" / "agent" / "coder.md"
        self.assertTrue(coder.exists(), "falta el subagent coder")
        self.assertEqual(_frontmatter(coder).get("model"), CODER_MODEL)

    def test_coder_failover_points_to_opencode_non_free(self) -> None:
        body = _md_body(ROOT / ".opencode" / "agent" / "coder.md")
        self.assertIn(CODER_FAILOVER_MODEL, body)
        self.assertRegex(body, r"(?i)rate-limit")

    def test_project_config_has_no_orchestrator_model_or_ollama(self) -> None:
        cfg = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
        self.assertNotIn("model", cfg, "opencode.json no debe fijar el modelo del orquestador")
        self.assertNotIn("ollama", cfg.get("provider", {}), "no re-introducir provider Ollama")
        for name, agent_cfg in cfg.get("agent", {}).items():
            model = agent_cfg.get("model") if isinstance(agent_cfg, dict) else None
            with self.subTest(agent=name):
                self.assertNotIn("ollama/", str(model), f"agent {name} con modelo Ollama")

    def test_general_and_explore_are_cheap(self) -> None:
        cfg = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
        for name in ("general", "explore"):
            with self.subTest(agent=name):
                self.assertEqual(cfg["agent"][name]["model"], AGENT_MODEL)

    def test_build_can_delegate_to_coder(self) -> None:
        cfg = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["agent"]["build"]["permission"]["task"]["coder"], "allow")

    def test_factory_correct_delegates_fixes_to_coder(self) -> None:
        text = (ROOT / ".opencode" / "command" / "factory-correct.md").read_text(encoding="utf-8")
        self.assertIn("`task`", text)
        self.assertIn("`coder`", text)
        self.assertRegex(text, r"(?i)deleg")

    def test_curation_wrappers_are_thin_pointers(self) -> None:
        # Los 5 roles de curaduría son wrappers finos: modelo fijo y, en el
        # cuerpo, puntero al sustento que vive en el repo de la app (`curation/`),
        # no copia del rol. Sin el checkout, el contrato es PENDIENTE explícito.
        for name in CURATION_AGENTS:
            agent = ROOT / ".opencode" / "agent" / f"{name}.md"
            with self.subTest(agent=name):
                self.assertTrue(agent.exists(), f"falta {name}")
                fm = _frontmatter(agent)
                self.assertEqual(fm.get("mode"), "subagent", f"{name}: mode")
                self.assertEqual(fm.get("model"), AGENT_MODEL, f"{name}: model")
                body = _md_body(agent)
                self.assertIn("/curation/", body)
                self.assertIn("PENDIENTE", body)
                self.assertIn("reviewed:true", body)

    def test_curation_role_wrappers_reference_canonical_role(self) -> None:
        roles = {
            "curation-labeler": "labeler",
            "curation-judge": "judge",
            "curation-council": "council",
            "curation-teacher": "teacher",
        }
        for name, canon in roles.items():
            wrapper = ROOT / ".opencode" / "agent" / f"{name}.md"
            with self.subTest(agent=name):
                self.assertIn(f"curation/agents/{canon}.md", _md_body(wrapper))

    def test_curation_publisher_is_harness_defined_and_never_merges(self) -> None:
        # El publisher no tiene prompt externo: sus límites viven en el harness.
        body = _md_body(ROOT / ".opencode" / "agent" / "curation-publisher.md")
        self.assertNotIn("curation/agents/", body)
        self.assertRegex(body, r"(?i)nunca merge")
        self.assertIn("--admin", body)

    def test_curation_publisher_promotes_only_from_approve_trail(self) -> None:
        # La promoción a reviewed:true sale del trail de veredictos, no del
        # criterio del publisher: sólo approve, con reviewed_by:"pipeline", y
        # re-corriendo el gate antes de publicar.
        body = _md_body(ROOT / ".opencode" / "agent" / "curation-publisher.md")
        self.assertRegex(body, r"(?i)promov")
        self.assertRegex(body, r"(?i)verdict")
        self.assertIn("trail", body)
        self.assertRegex(body, r"(?i)approve")
        self.assertRegex(body, r"(?i)reject")
        self.assertIn('reviewed_by:"pipeline"', body)
        self.assertRegex(body, r"(?i)gate")

    def test_factory_curate_documents_until_exhausted_mode(self) -> None:
        text = (ROOT / ".opencode" / "command" / "factory-curate.md").read_text(encoding="utf-8")
        self.assertIn("--until-exhausted", text)
        self.assertRegex(text, r"(?i)plateau")
        self.assertRegex(text, r"(?i)pausa")


PI_AGENT_MODEL = "openrouter/deepseek/deepseek-v4.1-flash"
# Fallback automático del coder en pi ante rate-limit: DeepSeek V4.1 Flash 1017
# (non-free). En OpenRouter no hay DeepSeek free; el pin sigue siendo el más
# barato de la familia.
PI_CODER_FALLBACK_MODEL = "openrouter/deepseek/deepseek-v4.1-flash-1017"
PI_SETTINGS = ROOT / ".pi" / "settings.json"
PI_AGENTS_DIR = ROOT / ".pi" / "agents"
PI_PACKAGE = "npm:pi-subagents@0.66.0"
# Los 10 subagents de ASF con espejo en `.pi/agents/`, en el mismo orden que
# `.opencode/agent/`. Los `curation-*` viven sólo en `.opencode/` (espejo `.pi`
# pendiente, fuera de esta corrida).
ASF_AGENTS: tuple[str, ...] = (
    "coder",
    "deploy-agent",
    "interrogator",
    "perf-factory",
    "reviewer-data",
    "reviewer-frontend",
    "reviewer-infra",
    "reviewer-security",
    "security-guardian",
    "sevbot",
)
# Reviewers/analistas que sólo leen: sin write/edit/bash en Pi.
PI_READONLY_AGENTS: tuple[str, ...] = (
    "interrogator",
    "perf-factory",
    "reviewer-data",
    "reviewer-frontend",
    "reviewer-infra",
    "reviewer-security",
    "security-guardian",
    "sevbot",
)
# Runners externos (claude-code/codex/cursor): no pasan por modelScope nativo.
PI_EXTERNAL_CLI_AGENTS: tuple[str, ...] = (
    "claude-code",
    "claude-code-writer",
    "codex-exec",
    "codex-exec-writer",
    "cursor-agent",
    "cursor-agent-writer",
)


def _pi_settings() -> dict[str, Any]:
    return json.loads(PI_SETTINGS.read_text(encoding="utf-8"))


def _md_body(path: Path) -> str:
    """Cuerpo de un markdown con frontmatter: todo lo que sigue al 2do `---`."""
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    return parts[2] if len(parts) == 3 else text


class TestPiDelegationConfig(unittest.TestCase):
    """Regresión de la config de pi.dev: subagents siempre DeepSeek barato y
    coder obligatorio, sin escapes a modelos caros o locales.

    La fuente de verdad son `.pi/settings.json` (overrides + modelScope) y los
    wrappers `.pi/agents/*.md` (prompt canónico referenciado, no copiado).
    pi-subagents ignora silenciosamente un agente sin `name`/`description`, y
    NO mapea el `permission` YAML de OpenCode a una allowlist de tools: por eso
    los wrappers declaran `tools` y no `permission`.
    """

    def test_pi_package_pinned(self) -> None:
        settings = _pi_settings()
        self.assertIn(PI_PACKAGE, settings.get("packages", []), "pi-subagents debe quedar pinneado")

    def test_project_root_resolution_git_root(self) -> None:
        subagents = _pi_settings()["subagents"]
        self.assertEqual(subagents.get("projectRootResolution"), "git-root")

    def test_shared_skills_point_to_opencode(self) -> None:
        settings = _pi_settings()
        self.assertIn("../.opencode/skill", settings.get("skills", []))
        self.assertTrue((ROOT / ".opencode" / "skill").is_dir())

    def test_orchestrator_model_not_pinned(self) -> None:
        settings = _pi_settings()
        self.assertNotIn("defaultProvider", settings, "el modelo del orquestador es libre")
        self.assertNotIn("defaultModel", settings, "el modelo del orquestador es libre")

    def test_subagent_default_model_is_cheap(self) -> None:
        subagents = _pi_settings()["subagents"]
        self.assertEqual(subagents.get("defaultModel"), PI_AGENT_MODEL)

    def test_model_scope_strict_allows_pin_and_coder_fallback(self) -> None:
        scope = _pi_settings()["subagents"]["modelScope"]
        self.assertIs(scope.get("enforce"), True)
        self.assertIs(scope.get("strict"), True)
        self.assertEqual(scope.get("allow"), [PI_AGENT_MODEL, PI_CODER_FALLBACK_MODEL])
        self.assertNotIn("inherit", scope.get("allow", []))
        # Sin allowlists por agente: una sola política global, no debilitable.
        self.assertNotIn("agents", scope)

    def test_coder_fallback_is_1017_non_free(self) -> None:
        subagents = _pi_settings()["subagents"]
        coder = subagents["agentOverrides"]["coder"]
        self.assertEqual(coder.get("fallbackModels"), [PI_CODER_FALLBACK_MODEL])
        self.assertIn(PI_CODER_FALLBACK_MODEL, subagents["modelScope"]["allow"])
        # El fallback es exclusivo del coder: el resto sigue sin fallbacks.
        for name, override in subagents["agentOverrides"].items():
            if name == "coder" or override.get("disabled") is True:
                continue
            with self.subTest(agent=name):
                self.assertEqual(override.get("fallbackModels"), [], f"{name}: fallbackModels")

    def test_every_override_is_cheap_without_fallbacks(self) -> None:
        overrides = _pi_settings()["subagents"]["agentOverrides"]
        self.assertTrue(overrides, "sin agentOverrides no hay pinneo de roles")
        for name, override in overrides.items():
            with self.subTest(agent=name):
                if override.get("disabled") is True:
                    continue  # un rol deshabilitado no resuelve modelo
                expected = [PI_CODER_FALLBACK_MODEL] if name == "coder" else []
                self.assertEqual(override.get("model"), PI_AGENT_MODEL, f"{name}: model")
                self.assertEqual(override.get("fallbackModels"), expected, f"{name}: fallbackModels")

    def test_external_cli_agents_disabled(self) -> None:
        # Los runners externos no pasan por modelScope: se deshabilitan.
        overrides = _pi_settings()["subagents"]["agentOverrides"]
        for agent in PI_EXTERNAL_CLI_AGENTS:
            with self.subTest(agent=agent):
                self.assertIs(overrides.get(agent, {}).get("disabled"), True)

    def test_all_asf_agents_have_overrides(self) -> None:
        overrides = _pi_settings()["subagents"]["agentOverrides"]
        for agent in ASF_AGENTS:
            with self.subTest(agent=agent):
                self.assertIn(agent, overrides, f"falta override de {agent}")
                self.assertEqual(overrides[agent].get("model"), PI_AGENT_MODEL)

    def test_wrapper_set_matches_asf_agents(self) -> None:
        wrappers = {p.stem for p in PI_AGENTS_DIR.glob("*.md")}
        self.assertEqual(wrappers, set(ASF_AGENTS))

    def test_wrappers_have_name_and_description(self) -> None:
        # Sin name/description pi-subagents descarta el agente en silencio.
        for agent in ASF_AGENTS:
            wrapper = PI_AGENTS_DIR / f"{agent}.md"
            with self.subTest(agent=agent):
                self.assertTrue(wrapper.exists(), f"falta wrapper {agent}")
                fm = _frontmatter(wrapper)
                self.assertEqual(fm.get("name"), agent)
                self.assertTrue(fm.get("description"), f"{agent}: description")
                self.assertEqual(fm.get("model"), PI_AGENT_MODEL)

    def test_wrappers_reference_canonical_without_copying(self) -> None:
        for agent in ASF_AGENTS:
            wrapper = PI_AGENTS_DIR / f"{agent}.md"
            canonical = ROOT / ".opencode" / "agent" / f"{agent}.md"
            with self.subTest(agent=agent):
                self.assertTrue(canonical.exists(), f"falta canónico {agent}")
                body = _md_body(wrapper)
                canonical_body = _md_body(canonical).strip()
                self.assertIn(f".opencode/agent/{agent}.md", body)
                # No copia el cuerpo canónico y se mantiene compacto.
                self.assertNotIn(canonical_body, body, f"{agent}: el wrapper copia el prompt canónico")
                self.assertLess(len(body), len(canonical_body), f"{agent}: wrapper no compacto")

    def test_wrappers_do_not_use_opencode_permission_yaml(self) -> None:
        # Pi parsea `permission`/`permissions` con OTRO schema (no tools); el
        # scope de tools se declara con `tools`, no con el YAML de OpenCode.
        for agent in ASF_AGENTS:
            fm = _frontmatter(PI_AGENTS_DIR / f"{agent}.md")
            with self.subTest(agent=agent):
                self.assertNotIn("permission", fm)
                self.assertNotIn("permissions", fm)

    def test_readonly_agents_have_only_read_tools(self) -> None:
        for agent in PI_READONLY_AGENTS:
            fm = _frontmatter(PI_AGENTS_DIR / f"{agent}.md")
            tools = {t.strip() for t in fm.get("tools", "").split(",") if t.strip()}
            with self.subTest(agent=agent):
                self.assertEqual(tools, {"read", "grep", "find", "ls"})
                self.assertFalse(tools & {"write", "edit", "bash"}, f"{agent}: tool de escritura")

    def test_coder_can_write_and_run(self) -> None:
        fm = _frontmatter(PI_AGENTS_DIR / "coder.md")
        tools = {t.strip() for t in fm.get("tools", "").split(",") if t.strip()}
        self.assertTrue({"read", "write", "edit", "bash"} <= tools)

    def test_deploy_agent_scope_documented_by_instruction(self) -> None:
        # Pi no tiene sandbox de escritura por ruta: el límite va por instrucción.
        wrapper = PI_AGENTS_DIR / "deploy-agent.md"
        fm = _frontmatter(wrapper)
        tools = {t.strip() for t in fm.get("tools", "").split(",") if t.strip()}
        self.assertIn("write", tools)
        self.assertIn("bash", tools)
        self.assertIn("state/dashboards/**", _md_body(wrapper))

    def test_extension_file_exists(self) -> None:
        self.assertTrue((ROOT / ".pi" / "extensions" / "asf-subagents.ts").exists())


class TestPiExtensionHooks(unittest.TestCase):
    """La extensión guard se prueba con bun y un mock de ExtensionAPI.

    Cubre el bloqueo de `agentScope:'user'`, cwd fuera del harness, workflows
    scriptados y management que reescribe tools de un rol ASF. Se salta si no
    hay bun o el package instalado.
    """

    def test_bun_hook_suite(self) -> None:
        import shutil

        bun = shutil.which("bun")
        if bun is None:
            self.skipTest("bun no instalado")
        pkg = Path.home() / ".pi" / "agent" / "npm" / "node_modules" / "pi-subagents"
        if not (pkg / "package.json").exists():
            self.skipTest("pi-subagents no instalado")
        result = subprocess.run(
            [bun, "test", str(SCRIPTS / "pi-extension.test.ts")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pi_discover_verifies_config(self) -> None:
        import shutil

        bun = shutil.which("bun")
        if bun is None:
            self.skipTest("bun no instalado")
        pkg = Path.home() / ".pi" / "agent" / "npm" / "node_modules" / "pi-subagents"
        if not (pkg / "package.json").exists():
            self.skipTest("pi-subagents no instalado")
        result = subprocess.run(
            [bun, str(SCRIPTS / "pi-discover.ts"), str(ROOT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pi_rpc_smoke_offline(self) -> None:
        """Arranque real de Pi en RPC offline: monta la extensión y responde
        get_state sin llamadas LLM. No imprime auth ni credenciales."""
        import shutil

        pi = shutil.which("pi")
        if pi is None:
            self.skipTest("pi no instalado")
        try:
            result = subprocess.run(
                [pi, "--offline", "--approve", "--no-session", "--mode", "rpc"],
                input='{"type":"get_state"}\n',
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            self.fail("pi RPC no terminó con stdin cerrado")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        statuses: list[str] = []
        get_state = False
        for line in result.stdout.splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("method") == "setStatus":
                statuses.append(str(obj.get("statusKey")))
            if obj.get("type") == "response" and obj.get("command") == "get_state":
                get_state = bool(obj.get("success"))
        self.assertIn("asf-subagents-guard", statuses, "la extensión no se montó en Pi")
        self.assertTrue(get_state, "get_state no respondió success")

    def test_pi_package_not_duplicated(self) -> None:
        packages = _pi_settings().get("packages", [])
        pinned = [p for p in packages if isinstance(p, str) and p.startswith("npm:pi-subagents")]
        self.assertEqual(pinned, ["npm:pi-subagents@0.66.0"], "package pi-subagents duplicado o sin pin")


@dataclass
class GateRepo:
    """origin bare + clone con `feature` saliendo de `main` y `main` avanzando."""

    origin: Path
    checkout: Path
    base: str
    head: str
    stale: bool


def _seed_gate_repo(tmp: Path, *, main_change: str) -> GateRepo:
    """Repo git real para la frescura de la base (sin red: origin es local).

    `main_change` es qué toca `main` DESPUÉS de que salga `feature`:
      "workflow" -> la definición de gates cambia en la base (el head corre gates
                    viejos: es el caso de x6200 #279);
      "docs"     -> la base avanza sin tocar gates (el head está detrás, pero
                    corre los mismos gates).
    """
    env = _git_test_env()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True, env=env
        ).stdout.strip()

    origin = tmp / "origin.git"
    git("init", "--bare", "-b", "main", str(origin))
    seed = tmp / "seed"
    seed.mkdir()
    git("init", "-b", "main", str(seed))
    workflows = seed / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: ci\non: pull_request\n", encoding="utf-8")
    (seed / "app.txt").write_text("base\n", encoding="utf-8")
    git("-C", str(seed), "add", "-A")
    git("-C", str(seed), "commit", "-m", "base")
    git("-C", str(seed), "remote", "add", "origin", str(origin))
    git("-C", str(seed), "push", "-u", "origin", "main")

    git("-C", str(seed), "checkout", "-b", "feature")
    (seed / "feature.txt").write_text("feature\n", encoding="utf-8")
    git("-C", str(seed), "add", "-A")
    git("-C", str(seed), "commit", "-m", "feature")
    git("-C", str(seed), "push", "-u", "origin", "feature")
    head = git("-C", str(seed), "rev-parse", "feature")

    git("-C", str(seed), "checkout", "main")
    if main_change == "workflow":
        (workflows / "ci.yml").write_text(
            "name: ci\non: pull_request\njobs:\n  gate:\n    steps: []\n", encoding="utf-8"
        )
    else:
        (seed / "docs.md").write_text("docs\n", encoding="utf-8")
    git("-C", str(seed), "add", "-A")
    git("-C", str(seed), "commit", "-m", f"main {main_change}")
    git("-C", str(seed), "push", "origin", "main")
    base = git("-C", str(seed), "rev-parse", "main")

    checkout = tmp / "work" / "fakeapp"
    checkout.parent.mkdir(parents=True, exist_ok=True)
    git("clone", str(origin), str(checkout))
    return GateRepo(origin=origin, checkout=checkout, base=base, head=head, stale=main_change == "workflow")


class TestGateBaseFreshness(unittest.TestCase):
    """gate_base.py: el head corre los gates de la base ACTUAL (x6200 #279).

    En `pull_request` GitHub usa el workflow de la rama head: una rama sin
    rebasar corre gates viejos y su verde no dice nada del main actual. #279 se
    mergeó sin el gate de contraste (#277) y main quedó rojo en silencio (#290).
    """

    def test_classify_table(self) -> None:
        cases: list[tuple[tuple[str, str, str, tuple[str, ...]], str]] = [
            # sin base/head no se puede probar nada
            (("", "h", "mb", ()), gate_base.UNKNOWN),
            (("b", "h", "", ()), gate_base.UNKNOWN),
            # head == base: corre los gates de la base por definición
            (("b", "b", "mb", ()), gate_base.FRESH),
            # el head ya contiene la base
            (("b", "h", "b", (".github/workflows/ci.yml",)), gate_base.FRESH),
            # detrás de la base pero los gates no cambiaron
            (("b", "h", "mb", ("docs/x.md",)), gate_base.FRESH),
            # la base cambió la definición de gates: gates viejos
            (("b", "h", "mb", (".github/workflows/ci.yml",)), gate_base.STALE),
        ]
        for (base, head, merge_base, changed), want in cases:
            with self.subTest(base=base, head=head, merge_base=merge_base, changed=changed):
                self.assertEqual(gate_base.classify(base, head, merge_base, changed).status, want)

    def test_stale_workflow_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _seed_gate_repo(Path(tmp), main_change="workflow")
            verdict = gate_base.base_freshness(repo.checkout, repo.base, repo.head, "main")
            self.assertEqual(verdict.status, gate_base.STALE, verdict.detail)
            self.assertIn(".github/workflows/ci.yml", verdict.workflows)
            self.assertIn("gates viejos", verdict.detail)

    def test_head_behind_with_same_workflows_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _seed_gate_repo(Path(tmp), main_change="docs")
            verdict = gate_base.base_freshness(repo.checkout, repo.base, repo.head, "main")
            self.assertEqual(verdict.status, gate_base.FRESH, verdict.detail)
            self.assertIn("workflows son idénticos", verdict.detail)

    def test_missing_checkout_is_unknown_not_fresh(self) -> None:
        verdict = gate_base.base_freshness(None, "b" * 40, "h" * 40, "main")
        self.assertEqual(verdict.status, gate_base.UNKNOWN)
        self.assertIn("sin checkout", verdict.detail)

    def test_cli_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _seed_gate_repo(Path(tmp), main_change="workflow")
            stale = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "gate_base.py"),
                    "asf",
                    repo.base,
                    repo.head,
                    "--checkout",
                    str(repo.checkout),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
            self.assertIn("stale:", stale.stdout)
            unknown = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "gate_base.py"),
                    "asf",
                    repo.base,
                    repo.head,
                    "--checkout",
                    str(Path(tmp) / "no-existe"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(unknown.returncode, 2, unknown.stdout + unknown.stderr)
            self.assertIn("unknown:", unknown.stdout)


class TestBaseGateMergeChain(unittest.TestCase):
    """El gate de frescura en la cadena de merge (merge.sh y auto-merge.py).

    Política (asf#65): `stale` —probado— BLOQUEA en los dos caminos; `unknown`
    —no se pudo probar— sólo AVISA en el merge manual (`merge.sh` sigue) y
    BLOQUEA en la automación (`auto-merge.py`). Los escenarios corren con git
    REAL (origin local) y fake de gh.
    """

    FAKE_GH = """#!/usr/bin/env bash
set -u
case "$1" in
  pr)
    case "$2" in
      view)
        if [[ "$*" == *mergeCommit* ]]; then
          printf '{"mergeCommit":{"oid":"%s"},"headRefOid":"%s"}\\n' "$MERGE_SHA" "$HEAD_SHA"
          exit 0
        fi
        if [[ "$*" == *statusCheckRollup* ]]; then
          printf '{"state":"OPEN","isDraft":false,"labels":[],"comments":[],"body":"## Validación (tests)","headRefOid":"%s","baseRefOid":"%s","mergeable":"MERGEABLE","statusCheckRollup":[{"state":"SUCCESS"}]}\\n' "$HEAD_SHA" "$BASE_SHA"
          exit 0
        fi
        printf '{"number":77,"state":"OPEN","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","headRefOid":"%s","headRefName":"feature","baseRefOid":"%s"}\\n' "$HEAD_SHA" "$BASE_SHA"
        exit 0
        ;;
      checks)
        printf 'checks\\tpass\\t20s\\thttps://example/checks\\n'
        exit 0
        ;;
      merge)
        echo merged > "$MERGE_LOG"
        exit 0
        ;;
      diff)
        printf 'diff --git a/docs/x.md b/docs/x.md\\n+++ b/docs/x.md\\n+hola\\n'
        exit 0
        ;;
    esac
    ;;
esac
echo "fake gh: args no manejados: $*" >&2
exit 1
"""

    def _fixture(
        self, tmp: Path, *, main_change: str, base_override: str = ""
    ) -> tuple[Path, dict[str, str]]:
        import shutil

        shutil.copytree(SCRIPTS, tmp / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        apps_dir = tmp / "config" / "apps"
        apps_dir.mkdir(parents=True)
        # auto-merge.py resuelve la policy base del harness: el fixture la copia.
        policies_dir = tmp / "config" / "policies"
        policies_dir.mkdir(parents=True)
        shutil.copy2(ROOT / "config" / "policies" / "base.json", policies_dir / "base.json")
        repo = _seed_gate_repo(tmp, main_change=main_change)
        (apps_dir / "fakeapp.json").write_text(
            json.dumps(
                {
                    "id": "fakeapp",
                    "repo": "f/o",
                    "remote": str(repo.origin),
                    "branch": "main",
                    "checkout": "work/fakeapp",
                }
            )
        )
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        fake_gh = bin_dir / "gh"
        fake_gh.write_text(self.FAKE_GH)
        fake_gh.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["HEAD_SHA"] = repo.head
        env["BASE_SHA"] = base_override or repo.base
        env["MERGE_SHA"] = "b" * 40
        env["MERGE_LOG"] = str(tmp / "merged.log")
        return tmp, env

    def _merge(self, harness: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(harness / "scripts" / "merge.sh"), "fakeapp", "feature", *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(harness),
        )

    def test_stale_base_blocks_the_merge(self) -> None:
        # Control negativo del gate: el escenario de #279 (la base cambió los
        # gates y el head no los tiene) NO puede mergear.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), main_change="workflow")
            r = self._merge(harness, env, "--apply")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("gates viejos", r.stderr)
            self.assertIn(".github/workflows/ci.yml", r.stderr)
            self.assertFalse(Path(env["MERGE_LOG"]).exists(), "mergeó un PR con gates viejos")

    def test_fresh_base_allows_the_shadow_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), main_change="docs")
            r = self._merge(harness, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("ok:", r.stdout)
            self.assertIn("gates al día", r.stderr)

    def test_unprovable_base_warns_and_continues(self) -> None:
        # Política del owner (asf#65): `unknown` (no se pudo PROBAR) avisa y
        # continúa. Bloquear por no poder verificar no compra seguridad y agrega
        # una forma nueva de quedarse trabado. Se prueba que SIGUE: con --apply
        # el merge ocurre, con el warn en stderr.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), main_change="docs", base_override="c" * 40)
            r = self._merge(harness, env, "--apply")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("merged: 77", r.stdout)
            self.assertTrue(Path(env["MERGE_LOG"]).exists(), "unknown bloqueó el merge")
            self.assertIn("warn: no se pudo probar", r.stderr)
            self.assertIn("se continúa — revisá a mano", r.stderr)
            self.assertNotIn("gates viejos", r.stderr)

    def test_auto_merge_blocks_a_stale_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), main_change="workflow")
            r = subprocess.run(
                [sys.executable, str(harness / "scripts" / "auto-merge.py"), "fakeapp", "7"],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(harness),
            )
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            verdict = json.loads(r.stdout)
            self.assertFalse(verdict["eligible"])
            self.assertIn("base no actualizada (stale)", str(verdict["bloqueos"]))

    def test_auto_merge_blocks_an_unprovable_base(self) -> None:
        # La automación SÍ es fail-closed ante la duda (asf#65): `unknown` no es
        # elegible, a diferencia del merge manual, que avisa y continúa.
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), main_change="docs", base_override="c" * 40)
            r = subprocess.run(
                [sys.executable, str(harness / "scripts" / "auto-merge.py"), "fakeapp", "7"],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(harness),
            )
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            verdict = json.loads(r.stdout)
            self.assertFalse(verdict["eligible"], "auto-merge eligió un PR con la base no probada")
            self.assertIn("base no actualizada (unknown)", str(verdict["bloqueos"]))
            self.assertTrue(verdict["base"].startswith("unknown:"), verdict["base"])

    def test_auto_merge_allows_a_fresh_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), main_change="docs")
            r = subprocess.run(
                [sys.executable, str(harness / "scripts" / "auto-merge.py"), "fakeapp", "7"],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(harness),
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            verdict = json.loads(r.stdout)
            self.assertTrue(verdict["eligible"], verdict["bloqueos"])
            self.assertTrue(verdict["base"].startswith("fresh:"), verdict["base"])


CI_STATUS_CHECKS_FAIL = """[
  {"name": "UI Visual Regression", "state": "FAILURE", "link": "https://github.com/f/o/actions/runs/4242/job/1"},
  {"name": "ci", "state": "SUCCESS", "link": "https://github.com/f/o/actions/runs/4243/job/2"}
]"""

CI_STATUS_RUN = """{
  "headSha": "%(sha)s",
  "conclusion": "failure",
  "jobs": [
    {
      "name": "UI Visual Regression",
      "conclusion": "failure",
      "steps": [
        {"name": "A vs B visual comparison", "conclusion": "success"},
        {"name": "Rendered WCAG contrast gate (issue #277)", "conclusion": "failure"}
      ]
    }
  ]
}"""


class TestCiStatusEvidence(unittest.TestCase):
    """ci-status.sh lee el STEP fallido y el artefacto, no el nombre del job.

    Caso real (x6200): el job se llama "UI Visual Regression" y falla por el gate
    de contraste o por Performance; el veredicto medido está en visual.json. Y un
    check que no corrió (o que corrió sobre otro sha) no es un gate verde.
    """

    FAKE_GH = """#!/usr/bin/env bash
set -u
SD="__STATE__"
case "$1" in
  pr)
    case "$2" in
      view)
        printf '{"state":"OPEN","mergeable":"MERGEABLE","headRefName":"feature","headRefOid":"%s","baseRefOid":"%s","title":"prueba"}\\n' "$HEAD_SHA" "$BASE_SHA"
        exit 0
        ;;
      checks)
        cat "$SD/checks.json"
        exit 0
        ;;
    esac
    ;;
  run)
    case "$2" in
      view)
        rid="$3"
        if [[ "$*" == *--log-failed* ]]; then
          cat "$SD/log-$rid.txt" 2>/dev/null || true
          exit 0
        fi
        cat "$SD/run-$rid.json"
        exit 0
        ;;
      download)
        rid="$3"
        name=""
        dir=""
        while [ "$#" -gt 0 ]; do
          case "$1" in
            -n) name="$2"; shift 2 ;;
            -D) dir="$2"; shift 2 ;;
            *) shift ;;
          esac
        done
        mkdir -p "$dir"
        cp "$SD/$name.json" "$dir/$name.json"
        exit 0
        ;;
    esac
    ;;
  api)
    cat "$SD/artifacts.json"
    exit 0
    ;;
esac
echo "fake gh: args no manejados: $*" >&2
exit 1
"""

    HEAD = "a" * 40
    BASE = "b" * 40

    def _fixture(
        self,
        tmp: Path,
        *,
        checks: str = CI_STATUS_CHECKS_FAIL,
        run_sha: str | None = None,
        artifacts: str = '{"artifacts": [{"name": "ui-visual-regression", "size_in_bytes": 1234}]}',
    ) -> tuple[Path, dict[str, str]]:
        import shutil

        shutil.copytree(SCRIPTS, tmp / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        apps_dir = tmp / "config" / "apps"
        apps_dir.mkdir(parents=True)
        (apps_dir / "fakeapp.json").write_text(
            json.dumps({"id": "fakeapp", "repo": "f/o", "remote": "https://example/f/o.git", "checkout": "."})
        )
        state = tmp / "ghstate"
        state.mkdir()
        (state / "checks.json").write_text(checks)
        (state / "run-4242.json").write_text(CI_STATUS_RUN % {"sha": run_sha or self.HEAD})
        (state / "log-4242.txt").write_text(
            "Rendered WCAG contrast gate\tbt-dialog /37/2 '76%': 3,80 -> 3,78: regresión del baseline\n"
        )
        (state / "artifacts.json").write_text(artifacts)
        (state / "ui-visual-regression.json").write_text(
            json.dumps({"status": "FAIL", "exit_code": 1, "gates": [{"gate": "contrast", "status": "FAIL"}]})
        )
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        fake_gh = bin_dir / "gh"
        fake_gh.write_text(self.FAKE_GH.replace("__STATE__", str(state)))
        fake_gh.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["HEAD_SHA"] = self.HEAD
        env["BASE_SHA"] = self.BASE
        return tmp, env

    def _run(self, harness: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(harness / "scripts" / "ci-status.sh"), "fakeapp", "7"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(harness),
        )

    def test_failed_step_and_artifact_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp))
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # el nombre del job NO alcanza: se declara el step fallido
            self.assertIn("step fallido: Rendered WCAG contrast gate (issue #277)", r.stdout)
            self.assertNotIn("step fallido: A vs B visual comparison", r.stdout)
            # el veredicto medido sale del artefacto, no del nombre del job
            self.assertIn("ui-visual-regression", r.stdout)
            self.assertIn('"status": "FAIL"', r.stdout)
            self.assertIn("regresión del baseline", r.stdout)
            self.assertIn("1 fail", r.stdout)

    def test_run_on_another_sha_is_declared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), run_sha="d" * 40)
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("datos VIEJOS", r.stdout)

    def test_pr_without_checks_is_declared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(Path(tmp), checks="[]")
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("no tiene checks reportados", r.stdout)
            self.assertIn("NO es un gate verde", r.stdout)

    def test_large_artifact_is_not_downloaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness, env = self._fixture(
                Path(tmp),
                artifacts='{"artifacts": [{"name": "ui-visual-regression", "size_in_bytes": 5000000}]}',
            )
            r = self._run(harness, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("sin artefactos chicos", r.stdout)
            self.assertNotIn("se baja para leer el veredicto medido", r.stdout)


def quick_shortcut_problems(script: str, ci_text: str) -> list[str]:
    """Problemas del atajo local de una suite (`--quick`) frente al CI.

    Clase de error (2026-10-23): el atajo local no es el CI. `tests/run_all_tests.sh
    --quick` deselecciona tests y el CI corre la suite completa, así que un verde
    local no dice nada del PR (el caso pagó un test deseleccionado, test_natural.py).
    Regla: el atajo no puede deseleccionar nada que un workflow corra, y si no se
    puede verificar (modo `--quick` que no se puede interpretar) se declara, nunca
    se asume verde.
    """
    lines = script.splitlines()
    quick_index = next(
        (i for i, line in enumerate(lines) if "--quick" in line and re.match(r"^\s*if\b", line)),
        None,
    )
    if quick_index is None:
        if "--quick" in script:
            return ["el script declara --quick pero no se pudo delimitar el bloque (no verificable)"]
        return []
    block: list[str] = []
    depth = 0
    for line in lines[quick_index:]:
        stripped = line.strip()
        if re.match(r"^if\b", stripped):
            depth += 1
        if stripped == "fi":
            depth -= 1
            if depth == 0:
                block.append(line)
                break
        block.append(line)
    else:
        return ["el bloque --quick no cierra con fi (no verificable)"]

    omitted: list[str] = []
    for line in block:
        match = re.match(r'^\s*skipped="\$skipped\s+([^"]+)"', line)
        if match:
            omitted.extend(match.group(1).split())
    problems: list[str] = []
    for name in omitted:
        runner = ""
        for line in lines:
            match = re.match(rf"^\s*run\s+{re.escape(name)}\s+(.*)$", line)
            if not match:
                continue
            tokens = match.group(1).split()
            for token in reversed(tokens):
                candidate = token.strip("\"'")
                candidate = candidate.replace("$DIR/", "tests/").replace("${DIR}/", "tests/")
                if "/" in candidate and candidate.endswith((".sh", ".py")):
                    runner = candidate
                    break
            if runner:
                break
        if not runner:
            problems.append(
                f"la suite '{name}' que --quick deselecciona no se pudo mapear a su runner (no verificable)"
            )
            continue
        if runner in ci_text:
            problems.append(
                f"el atajo --quick deselecciona '{name}' ({runner}) y un workflow del CI lo corre:"
                f" el verde local no cubre lo que el CI exige"
            )
    return problems


class TestLocalShortcutNotCI(unittest.TestCase):
    """El atajo local no es el CI: se verifica contra lo que el CI corre."""

    BAD_SCRIPT = """#!/bin/sh
QUICK=${1:-}
pass=0
run() { name=$1; shift; "$@"; }
run config python3 "$DIR/check_cfg_fills.py"
run natural sh "$DIR/native/run_natural_tests.sh"
if [ "$QUICK" = "--quick" ]; then
    skipped="$skipped natural"
else
    run natural sh "$DIR/native/run_natural_tests.sh"
fi
printf '%d OK\\n' "$pass"
"""

    GOOD_SCRIPT = """#!/bin/sh
QUICK=${1:-}
run() { name=$1; shift; "$@"; }
if [ "$QUICK" = "--quick" ]; then
    skipped="$skipped js8"
else
    run js8 sh "$DIR/native/run_js8_tests.sh"
fi
"""

    UNPARSABLE = """#!/bin/sh
# se puede pedir --quick pero no hay bloque if que lo implemente
echo hola
"""

    def test_shortcut_that_skips_a_ci_suite_is_a_problem(self) -> None:
        # Control negativo: el caso real (test deseleccionado que el CI corre).
        problems = quick_shortcut_problems(self.BAD_SCRIPT, "run: tests/native/run_natural_tests.sh\n")
        self.assertTrue(problems, "el check no detectó el atajo que deselecciona un test del CI")
        self.assertIn("deselecciona 'natural'", problems[0])

    def test_shortcut_that_skips_a_non_ci_suite_is_ok(self) -> None:
        self.assertEqual(
            quick_shortcut_problems(self.GOOD_SCRIPT, "run: tests/native/run_native_tests.sh"), []
        )

    def test_unparsable_shortcut_is_declared(self) -> None:
        problems = quick_shortcut_problems(self.UNPARSABLE, "")
        self.assertTrue(problems)
        self.assertIn("no verificable", problems[0])

    def test_registered_apps_have_no_hidden_deselection(self) -> None:
        # App-agnóstico: se audita cada app del registry que tenga un atajo local
        # (`tests/run_all_tests.sh`) y workflows. En un clone fresco sin checkouts
        # no hay nada que auditar: skip, nunca verde silencioso.
        checked = 0
        for app in app_config.available_apps():
            try:
                reg = app_config.load_registry(app)
            except SystemExit:
                continue
            checkout = Path(reg["checkout_abs"])
            script_path = checkout / "tests" / "run_all_tests.sh"
            workflows_dir = checkout / ".github" / "workflows"
            if not script_path.is_file() or not workflows_dir.is_dir():
                continue
            ci_text = "".join(w.read_text(encoding="utf-8") for w in sorted(workflows_dir.glob("*.yml")))
            with self.subTest(app=app):
                self.assertTrue(ci_text, f"{app}: workflows vacíos, no se puede verificar el atajo local")
                problems = quick_shortcut_problems(script_path.read_text(encoding="utf-8"), ci_text)
                self.assertEqual(problems, [], f"{app}: el atajo local no espeja al CI: {problems}")
            checked += 1
        if not checked:
            self.skipTest("ningún checkout con tests/run_all_tests.sh + workflows (clon fresco o CI)")


class TestGateNegativeControls(unittest.TestCase):
    """Ningún gate se declara bueno sin verlo fallar (control negativo).

    Caso real (2026-10-23, x6200 gate de contraste #277): el gate tenía un falso
    verde (reusaba un binario viejo) y un agujero (tinta del color del fondo ⇒ no
    había píxeles que muestrear y se clasificaba "sin tinta" en vez de violación).
    Los dos aparecieron SÓLO al inyectar la regresión (#290). Regla del harness:
    cada gate de la cadena de merge tiene un test registrado acá que lo hace
    FALLAR; si el test desaparece o el gate se borra, el CI se pone rojo.
    """

    GATES: ClassVar[tuple[tuple[str, str], ...]] = (
        ("risk.py", "TestShellScripts.test_risk_self_test"),
        ("risk.py", "TestRiskLiteral.test_cases"),
        ("auto-merge.py", "TestAutoMergeCheckRollup.test_evaluate_still_blocks_red_check_run"),
        ("auto-merge.py", "TestProviderNoneConsumers.test_auto_merge_blocks_and_never_merges"),
        ("merge.sh", "TestMergeGuard.test_dirty_rejected"),
        ("merge.sh", "TestMergeGuardLocalMatrix.test_missing_checks_declaration_blocks"),
        ("merge.sh", "TestMergeGuardLocalMatrix.test_matrix_failure_blocks_the_merge"),
        ("merge.sh", "TestBaseGateMergeChain.test_stale_base_blocks_the_merge"),
        ("gate_base.py", "TestGateBaseFreshness.test_stale_workflow_is_detected"),
        ("merge-when-green.sh", "TestMergeWhenGreen.test_class_c_writes_evidence_and_exits_5"),
    )

    def test_every_gate_has_a_negative_control(self) -> None:
        module: Any = sys.modules[__name__]
        for gate, test_id in self.GATES:
            with self.subTest(gate=gate, control=test_id):
                self.assertTrue((SCRIPTS / gate).is_file(), f"el gate {gate} no existe")
                obj: Any = module
                for part in test_id.split("."):
                    self.assertTrue(hasattr(obj, part), f"falta el control negativo {test_id} de {gate}")
                    obj = getattr(obj, part)
                self.assertTrue(callable(obj), f"{test_id} no es un test ejecutable")

    def test_merge_chain_gates_are_registered(self) -> None:
        registered = {gate for gate, _ in self.GATES}
        for gate in ("gate_base.py", "merge.sh", "auto-merge.py", "merge-when-green.sh"):
            with self.subTest(gate=gate):
                self.assertIn(gate, registered, f"el gate {gate} no tiene control negativo registrado")


if __name__ == "__main__":
    unittest.main(verbosity=2)
