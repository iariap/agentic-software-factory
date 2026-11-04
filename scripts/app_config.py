#!/usr/bin/env python3
"""Resuelve el registro de apps del harness multi-app.

Un harness puede manejar varias aplicaciones a la vez. Cada app se define en
`config/apps/<id>.json`. Este módulo es la única fuente que lean los demás
scripts: resuelve el checkout, el repo de GitHub, los entornos y —con
`merged_policy`— la política de riesgo de la app (base + overrides).

Estructura de la registry (config/apps/<id>.json):
  {
    "id": "mi-mesa",
    "repo": "owner/repo",              # repo de GitHub (gh --repo)
    "remote": "https://...git",        # para clonar
    "branch": "main",                  # rama trunk por defecto
    "checkout": "work/mi-mesa",        # path relativo al harness del checkout editable
    "policy": "mi-mesa",               # config/policies/<policy>.json (overrides)
    "environments": { "staging": {"health_url": "https://.../health"}, ... },
    "metrics": { "provider": "cloud_monitoring", "gcp_project": "..." },  # opcional
    "checks": "scripts/apps/mi-mesa/check-matrix.sh",                       # opcional
    "ci": { "provider": "github" | "none" }                                 # opcional (default github)
  }

Uso:
  scripts/app-config.py <app>                 # imprime la registry resuelta (JSON)
  scripts/app-config.py <app> --print KEY     # imprime un campo (repo, checkout, ...)
  scripts/app-config.py <app> --env NAME      # imprime el bloque de un entorno
  eval "$(scripts/app-config.py mi-mesa --sh)"  # exporta vars para shell

Sin argumentos de app: error (no hay default a propósito: evita operar sobre la
app equivocada).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = ROOT / "config" / "apps"
POLICIES_DIR = ROOT / "config" / "policies"
BASE_POLICY_PATH = POLICIES_DIR / "base.json"


def available_apps() -> list[str]:
    if not APPS_DIR.exists():
        return []
    return sorted(p.stem for p in APPS_DIR.glob("*.json"))


# Campos del puntero que la app NO puede redefinir desde .factory/ (evita que
# un alias de app redirija el repo o el checkout a otro lado).
_POINTER_AUTHORITY = ("repo", "remote", "checkout")


def _read_overlay(checkout_abs: Path) -> dict[str, Any]:
    """Metadata propia de la app: <checkout>/.factory/app.json (si existe)."""
    meta = checkout_abs / ".factory" / "app.json"
    if not meta.exists():
        return {}
    try:
        return json.loads(meta.read_text())
    except json.JSONDecodeError:
        sys.exit(f"metadata inválida en {meta}")
    return {}


def load_registry(app: str) -> dict[str, Any]:
    """Puntero del harness + overlay .factory/app.json del checkout (gana la app).

    La metadata de cada app vive en su propio repo (`.factory/app.json`); el
    harness guarda un puntero mínimo (repo/remote/branch/checkout) que además
    sirve de fallback cuando el checkout aún no tiene `.factory/` (apps sin
    migrar o sin sync).
    """
    path = APPS_DIR / f"{app}.json"
    if not path.exists():
        canonical = _resolve_alias(app)
        if canonical:
            path = APPS_DIR / f"{canonical}.json"
        else:
            apps = ", ".join(available_apps()) or "ninguna"
            sys.exit(f"app desconocida: {app!r} (disponibles: {apps})")
    reg = json.loads(path.read_text())
    checkout_abs = (ROOT / reg.get("checkout", f"work/{path.stem}")).resolve()
    for key, val in _read_overlay(checkout_abs).items():
        if key in _POINTER_AUTHORITY:
            continue
        reg[key] = val
    reg.setdefault("id", path.stem)
    reg["checkout_abs"] = str(checkout_abs)
    return reg


def _resolve_alias(name: str) -> str | None:
    """Si `name` no es un id canónico, busca una app que lo declare en `aliases`
    (en el puntero o en su overlay .factory/app.json)."""
    for path in sorted(APPS_DIR.glob("*.json")):
        try:
            reg = load_registry(path.stem)
        except SystemExit:
            continue
        if name in reg.get("aliases", []):
            return path.stem
    return None


# Provider de CI de la app (`ci.provider`). `github` = GitHub Actions en los PRs
# (default). `none` = la app NO corre CI en GitHub: valida con su matriz local y
# el PR lo declara en la sección `## Validación`. Un valor desconocido se trata
# como `github` (fail-closed: un typo nunca deshabilita el CI por accidente).
CI_PROVIDER_DEFAULT = "github"
CI_PROVIDERS = ("github", "none")


def _ci_provider_from(reg: dict[str, Any]) -> str:
    ci = reg.get("ci")
    if not isinstance(ci, dict):
        return CI_PROVIDER_DEFAULT
    provider = ci.get("provider", CI_PROVIDER_DEFAULT)
    return provider if provider in CI_PROVIDERS else CI_PROVIDER_DEFAULT


def ci_provider(app: str) -> str:
    """Provider de CI de la app: `github` (default) o `none` (matriz local).

    `none` no es un verde: los consumidores lo usan para NO esperar un CI que no
    existe y exigir la matriz local en su lugar (ver merge.sh/babysit.sh).
    """
    return _ci_provider_from(load_registry(app))


def _merge_policy(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge de la policy de la app sobre la base.

    - dict: merge recursivo.
    - `rules`: se concatenan y se reemplaza in-place la regla que repite el
      mismo `pattern` (la de la app gana), para no duplicar patterns.
    - listas simples: la override reemplaza.
    - resto: la override gana.
    """
    out = json.loads(json.dumps(base))
    for key, val in override.items():
        if key == "rules" and isinstance(val, list):
            merged: list[dict[str, Any]] = [dict(r) for r in out.get("rules", [])]
            by_pattern = {r["pattern"]: i for i, r in enumerate(merged)}
            for rule in val:
                pat = rule.get("pattern")
                if pat in by_pattern:
                    merged[by_pattern[pat]] = rule
                else:
                    by_pattern[pat] = len(merged)
                    merged.append(dict(rule))
            out["rules"] = merged
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_policy(out[key], val)
        else:
            out[key] = val
    return out


def merged_policy(app: str) -> dict[str, Any]:
    """base.json (harness, no negable) + reglas de la app.

    Las reglas de la app viven en su repo (`.factory/policy.json`); si el
    checkout aún no las tiene, cae al fallback del harness
    (`config/policies/<policy>.json`) mientras se migra.
    """
    reg = load_registry(app)
    base = json.loads(BASE_POLICY_PATH.read_text())
    override_path = Path(reg["checkout_abs"]) / ".factory" / "policy.json"
    if not override_path.exists():
        override_path = POLICIES_DIR / f"{reg.get('policy', reg['id'])}.json"
    if override_path.exists():
        return _merge_policy(base, json.loads(override_path.read_text()))
    return base


def _sh_quote(v: Any) -> str:
    return "'" + str(v).replace("'", "'\\''") + "'"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("app", nargs="?", help="id de app (config/apps/<id>.json)")
    ap.add_argument("--print", dest="key", help="imprime un campo de la registry")
    ap.add_argument("--env", help="imprime el bloque JSON de un entorno")
    ap.add_argument("--sh", action="store_true", help="export vars para eval en shell")
    ap.add_argument("--list", action="store_true", help="lista apps disponibles")
    a = ap.parse_args()

    if a.list:
        print("\n".join(available_apps()))
        return 0
    if not a.app:
        ap.error("falta <app> (no hay default; --list para ver disponibles)")

    reg = load_registry(a.app)
    if a.env:
        envs = reg.get("environments", {})
        if a.env not in envs:
            sys.exit(f"entorno {a.env!r} no definido para {a.app}")
        print(json.dumps(envs[a.env]))
        return 0
    if a.key:
        val = reg.get(a.key, "")
        print(val if not isinstance(val, (dict, list)) else json.dumps(val))
        return 0
    if a.sh:
        # Nombres fijos y estables: APP_ID/APP_REPO/... + por entorno APP_ENV_<NAME>_URL.
        export = {
            "APP_ID": reg["id"],
            "APP_REPO": reg.get("repo", ""),
            "APP_REMOTE": reg.get("remote", ""),
            "APP_BRANCH": reg.get("branch", "main"),
            "APP_CHECKOUT": reg["checkout_abs"],
            "APP_CHECKS": reg.get("checks", ""),
            "APP_POLICY": reg.get("policy", reg["id"]),
            "APP_CI_PROVIDER": _ci_provider_from(reg),
        }
        for env, blk in reg.get("environments", {}).items():
            url = str(blk.get("health_url", "")).removesuffix("/health")
            key = "APP_ENV_" + "".join(c if c.isalnum() else "_" for c in env).upper() + "_URL"
            export[key] = url
        met = reg.get("metrics", {})
        if met.get("gcp_project"):
            export["APP_GCP_PROJECT"] = met["gcp_project"]
        for k, v in export.items():
            print(f"{k}={_sh_quote(v)}")
        return 0
    print(json.dumps(reg, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
