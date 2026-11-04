#!/usr/bin/env python3
"""Métricas cross-corrida del harness a partir de `state/reports/**`.

Agrega el estado de todas las corridas sin red y sin `gh`: volumen por app y
`status`, distribución de riesgo, rework del loop de CI (archivos
`babysit*.json`), autonomía (`decisions.tsv`) y waste (corridas abiertas / PRs
huérfanos). Sólo stdlib; salida determinista (todo se ordena explícitamente).

Dos layouts conviven y se detectan por estructura:

- actual:   `state/reports/<app>/<run>/…`
- legacy:   `state/reports/<run>/…` (app inferida de `run.json.app`, si no
  `(legacy)`).

Reglas de robustez (nunca romper, nunca inventar):

- `run.json` con esquema variable: cualquier campo puede faltar. Si el dir no
  tiene `run.json` parseable, o el `status` está ausente, el run cae en el
  bucket `sin-schema` (no en cero).
- `babysit*.json`: un archivo por intento/estado; el nº de archivos es proxy de
  reintentos y el nº de `head_sha` distintos proxy de rework (no hay timestamps
  por sha en todos los formatos).
- `decisions.tsv`: match de `resultado` tolerante a acentos/case; un resultado
  negativo (`falló`/`descartado`/`pivot`/…) cuenta como "no-útil" y el resto
  como útil (los `resultado` históricos también pueden ser prosa). Algunos
  archivos separan con la secuencia literal `\\t` en vez de tabs reales: se
  normaliza.

Uso:

    scripts/run-stats.py [app|--all] [--since YYYY-MM-DD] [--json]

Por defecto `--all`. `--json` emite el mismo objeto que la tabla markdown.
`--since` filtra por `date`/`merged_at` de `run.json` o, si no existe, por el
mtime del directorio. El merge-status vive en `run.json` (`merged_at`); este
script NO consulta `gh` (eso es `ci-status.sh`).

stdout: markdown (default) o JSON; errores a stderr. Sin `state/reports/**` →
exit 1 con `PENDIENTE` explícito.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

RUN_FILES = ("run.json", "report.md", "decisions.tsv")
RISKS = ("low", "medium", "high")
NEGATIVE_RESULTS = ("fall", "descart", "discart", "pivot", "rechaz", "abort", "bloque")


def _resolve_root() -> Path:
    """Raíz del harness desde `__file__` (sube desde `state/worktrees/...`)."""
    root = Path(__file__).resolve().parent.parent
    parts = root.parts
    for i, part in enumerate(parts):
        if part == "state" and i + 1 < len(parts) and parts[i + 1] == "worktrees":
            if i == 0:
                return Path(parts[0])
            return Path(*parts[:i])
    return root


ROOT = _resolve_root()
REPORTS_DIR = ROOT / "state" / "reports"


def _err(text: str) -> None:
    print(f"run-stats: {text}", file=sys.stderr)


def _norm(value: str) -> str:
    """Normaliza para comparar resultados: sin acentos, minúsculas, sin bordes."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.strip().casefold()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _is_run_dir(path: Path) -> bool:
    """Un dir es corrida si contiene al menos un marcador de reporte."""
    if not path.is_dir():
        return False
    try:
        names = [p.name for p in path.iterdir()]
    except OSError:
        return False
    for name in names:
        if name in RUN_FILES:
            return True
        if name.startswith("babysit") and name.endswith(".json"):
            return True
    return False


def _iter_runs(reports_root: Path) -> list[tuple[str | None, Path]]:
    """(app_container, run_dir); `None` para runs legacy a nivel raíz.

    Un hijo directo se trata como contenedor de app (layout actual) si su
    nombre está en el registry o si tiene sub-dirs que son corridas; en ese
    caso **todos** sus sub-dirs son corridas (aunque no tengan `run.json` y
    caigan en `sin-schema`). Si no, el propio hijo es una corrida legacy.
    """
    found: list[tuple[str | None, Path]] = []
    if not reports_root.is_dir():
        return found
    registered = _registered_apps()
    for child in sorted(reports_root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        subdirs = [g for g in sorted(child.iterdir(), key=lambda p: p.name) if g.is_dir()]
        is_container = child.name in registered or any(_is_run_dir(g) for g in subdirs)
        if is_container and subdirs:
            found.extend((child.name, run_dir) for run_dir in subdirs)
        else:
            found.append((None, child))
    return found


def _registered_apps() -> set[str]:
    """Ids de app del registry (`config/apps/*.json`), para el layout actual."""
    apps_dir = ROOT / "config" / "apps"
    if not apps_dir.is_dir():
        return set()
    return {p.stem for p in apps_dir.glob("*.json")}


def _app_of(container: str | None, run_dir: Path) -> str:
    if container is not None:
        return container
    schema = _load_json(run_dir / "run.json")
    if schema is not None:
        app = schema.get("app")
        if isinstance(app, str) and app.strip():
            return app.strip()
    return "(legacy)"


def _iso_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) < 10:
        return None
    candidate = text[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


def _mtime_date(run_dir: Path) -> str | None:
    try:
        stamp = run_dir.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, UTC).date().isoformat()


def _run_date(schema: dict[str, Any] | None, run_dir: Path) -> str | None:
    if schema is not None:
        for key in ("date", "merged_at"):
            parsed = _iso_date(schema.get(key))
            if parsed is not None:
                return parsed
    return _mtime_date(run_dir)


def _read_babysit(run_dir: Path) -> dict[str, Any]:
    files = sorted(
        (
            p
            for p in run_dir.iterdir()
            if p.is_file() and p.name.startswith("babysit") and p.name.endswith(".json")
        ),
        key=lambda p: p.name,
    )
    shas: set[str] = set()
    first_sha: str | None = None
    last_sha: str | None = None
    first_exit: int | None = None
    last_exit: int | None = None
    for path in files:
        data = _load_json(path)
        if data is None:
            continue
        sha = data.get("head_sha")
        if isinstance(sha, str) and sha:
            shas.add(sha)
            if first_sha is None:
                first_sha = sha
            last_sha = sha
        exit_code = data.get("exit")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            if first_exit is None:
                first_exit = exit_code
            last_exit = exit_code
    return {
        "files": len(files),
        "distinct_shas": len(shas),
        "first_sha": first_sha,
        "last_sha": last_sha,
        "first_exit": first_exit,
        "last_exit": last_exit,
    }


def _read_decisions(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "decisions.tsv"
    empty: dict[str, Any] = {"total": 0, "ok": 0, "no_util": 0, "by_result": {}}
    if not path.is_file():
        return empty
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return empty
    # Archivos históricos con la secuencia literal `\t` en vez de tab real.
    lines = [ln for ln in text.replace("\\t", "\t").splitlines() if ln.strip()]
    if len(lines) <= 1:
        return empty
    by_result: dict[str, int] = {}
    total = 0
    ok = 0
    for line in lines[1:]:
        cols = line.split("\t")
        if not cols:
            continue
        result = cols[-1].strip()
        if not result:
            continue
        key = _norm(result)
        by_result[key] = by_result.get(key, 0) + 1
        total += 1
        if not any(token in key for token in NEGATIVE_RESULTS):
            ok += 1
    return {"total": total, "ok": ok, "no_util": total - ok, "by_result": by_result}


def _status_of(schema: dict[str, Any] | None) -> str:
    if schema is not None:
        raw = schema.get("status")
        if isinstance(raw, str) and raw.strip():
            return _norm(raw)
    return "sin-schema"


def _run_record(app: str, run_dir: Path, now_date: date) -> dict[str, Any]:
    schema = _load_json(run_dir / "run.json")
    status = _status_of(schema)
    raw_risk = schema.get("risk") if schema is not None else None
    risk = raw_risk.strip().casefold() if isinstance(raw_risk, str) else ""
    if risk not in RISKS:
        risk = "unknown"
    merged = "merged" in status
    pr = schema.get("pr") if schema is not None else None
    has_pr = pr is not None and pr != "" and pr != 0
    run_date = _run_date(schema, run_dir)
    age_days: int | None = None
    if run_date is not None:
        age_days = (now_date - date.fromisoformat(run_date)).days
    return {
        "app": app,
        "run": run_dir.name,
        "status": status,
        "risk": risk,
        "merged": merged,
        "has_pr": has_pr,
        "date": run_date,
        "age_days": age_days,
        "babysit": _read_babysit(run_dir),
        "decisions": _read_decisions(run_dir),
        "open_pr": has_pr and not merged,
        "in_progress_or_awaiting": status.startswith("in_progress") or status.startswith("awaiting"),
    }


def _new_tokens() -> dict[str, int]:
    return {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}


def _aggregate_app(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    risk: dict[str, int] = {name: 0 for name in (*RISKS, "unknown")}
    merged = 0
    sin_schema = 0
    ci_runs = 0
    ci_files = 0
    ci_shas = 0
    first_ok = 0
    last_ok = 0
    dec_total = 0
    dec_ok = 0
    dec_no_util = 0
    waste_open = 0
    waste_pr = 0
    for record in records:
        status = record["status"]
        by_status[status] = by_status.get(status, 0) + 1
        if status == "sin-schema":
            sin_schema += 1
        risk[record["risk"]] += 1
        if record["merged"]:
            merged += 1
        babysit = record["babysit"]
        if babysit["files"]:
            ci_runs += 1
            ci_files += babysit["files"]
            ci_shas += babysit["distinct_shas"]
            if babysit["first_exit"] == 0:
                first_ok += 1
            if babysit["last_exit"] == 0:
                last_ok += 1
        decisions = record["decisions"]
        dec_total += decisions["total"]
        dec_ok += decisions["ok"]
        dec_no_util += decisions["no_util"]
        if record["in_progress_or_awaiting"]:
            waste_open += 1
        if record["open_pr"]:
            waste_pr += 1
    status_runs = len(records) - sin_schema
    return {
        "runs": len(records),
        "sin_schema": sin_schema,
        "by_status": dict(sorted(by_status.items())),
        "risk": risk,
        "merged": merged,
        "merged_pct": round(100 * merged / status_runs, 1) if status_runs else 0.0,
        "ci": {
            "runs": ci_runs,
            "files": ci_files,
            "distinct_shas_avg": round(ci_shas / ci_runs, 2) if ci_runs else 0.0,
            "first_exit_ok": first_ok,
            "last_exit_ok": last_ok,
        },
        "decisions": {
            "total": dec_total,
            "ok": dec_ok,
            "no_util": dec_no_util,
            "no_util_pct": round(100 * dec_no_util / dec_total, 1) if dec_total else 0.0,
        },
        "waste": {"in_progress_or_awaiting": waste_open, "open_pr": waste_pr},
    }


def collect_reports(
    reports_root: Path,
    *,
    since: str | None = None,
    app: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Agrega `reports_root` en el objeto determinista de la CLI."""
    now_date = (now or datetime.now(UTC)).date()
    runs: list[dict[str, Any]] = []
    for container, run_dir in _iter_runs(reports_root):
        app_name = _app_of(container, run_dir)
        if app is not None and app_name != app:
            continue
        record = _run_record(app_name, run_dir, now_date)
        if since is not None:
            run_day = record["date"]
            if run_day is None or run_day < since:
                continue
        runs.append(record)
    runs.sort(key=lambda r: (r["app"], r["run"]))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in runs:
        grouped.setdefault(record["app"], []).append(record)
    apps = {name: _aggregate_app(grouped[name]) for name in sorted(grouped)}
    return {"scope": app or "all", "since": since, "apps": apps, "runs": runs}


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_markdown(data: dict[str, Any]) -> str:
    """Tabla markdown legible del objeto de `collect_reports`."""
    apps: dict[str, Any] = data["apps"]
    n_runs = len(data["runs"])
    n_sin = sum(a["sin_schema"] for a in apps.values())
    out: list[str] = [
        f"# run-stats — scope={data['scope']} · {n_runs} run(s) · sin-schema {n_sin}",
        "",
    ]
    if data.get("since"):
        out.append(f"Filtrado desde `{data['since']}`.")
        out.append("")

    out.append("## Volumen")
    volume_rows: list[list[str]] = []
    statuses = sorted({s for a in apps.values() for s in a["by_status"] if s != "sin-schema"})
    for name in sorted(apps):
        a = apps[name]
        cells = [str(a["by_status"].get(s, 0)) for s in statuses]
        volume_rows.append([name, str(a["runs"]), str(a["sin_schema"]), *cells])
    out.append(_md_table(["app", "runs", "sin-schema", *statuses], volume_rows))
    out.append("")

    out.append("## Riesgo")
    risk_rows = [
        [
            name,
            str(apps[name]["risk"]["low"]),
            str(apps[name]["risk"]["medium"]),
            str(apps[name]["risk"]["high"]),
            str(apps[name]["risk"]["unknown"]),
            f"{apps[name]['merged_pct']}%",
        ]
        for name in sorted(apps)
    ]
    out.append(_md_table(["app", "low", "medium", "high", "unknown", "merged"], risk_rows))
    out.append("")

    out.append("## Loop CI (babysit)")
    ci_rows = [
        [
            name,
            str(apps[name]["ci"]["runs"]),
            str(apps[name]["ci"]["files"]),
            str(apps[name]["ci"]["distinct_shas_avg"]),
            str(apps[name]["ci"]["first_exit_ok"]),
            str(apps[name]["ci"]["last_exit_ok"]),
        ]
        for name in sorted(apps)
    ]
    out.append(
        _md_table(
            ["app", "runs con babysit", "archivos", "shas distintos (media)", "1er exit 0", "último exit 0"],
            ci_rows,
        )
    )
    out.append("")

    out.append("## Rework / autonomía (decisions.tsv)")
    dec_rows = [
        [
            name,
            str(apps[name]["decisions"]["total"]),
            str(apps[name]["decisions"]["ok"]),
            str(apps[name]["decisions"]["no_util"]),
            f"{apps[name]['decisions']['no_util_pct']}%",
        ]
        for name in sorted(apps)
    ]
    out.append(_md_table(["app", "total", "ok", "no-útil", "no-útil%"], dec_rows))
    out.append("")

    out.append("## Waste")
    waste_rows = [
        [name, str(apps[name]["waste"]["in_progress_or_awaiting"]), str(apps[name]["waste"]["open_pr"])]
        for name in sorted(apps)
    ]
    out.append(_md_table(["app", "in_progress/awaiting", "PR abierto"], waste_rows))
    out.append("")

    out.append("## Runs")
    run_rows = [
        [
            r["app"],
            r["run"],
            r["status"],
            r["risk"],
            r["date"] or "—",
            f"{r['babysit']['files']}/{r['babysit']['distinct_shas']}",
            f"{r['decisions']['no_util']}/{r['decisions']['total']}",
        ]
        for r in data["runs"]
    ]
    out.append(
        _md_table(["app", "run", "status", "risk", "date", "babysit (files/shas)", "no-útil/total"], run_rows)
    )
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run-stats.py", description=__doc__)
    ap.add_argument("app", nargs="?", default=None, help="id de app (default: todas)")
    ap.add_argument("--all", action="store_true", help="todas las apps (default)")
    ap.add_argument("--since", default=None, help="filtra corridas desde YYYY-MM-DD")
    ap.add_argument("--json", action="store_true", help="salida JSON en vez de markdown")
    args = ap.parse_args(argv)

    if args.since is not None:
        try:
            date.fromisoformat(args.since[:10])
        except ValueError:
            _err(f"--since inválido: {args.since} (esperado YYYY-MM-DD)")
            return 2

    if not REPORTS_DIR.is_dir():
        return _pendiente(f"sin reports: {REPORTS_DIR}")

    app = None if args.all else args.app
    data = collect_reports(REPORTS_DIR, since=args.since, app=app)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_markdown(data))
    return 0


def _pendiente(text: str) -> int:
    _err(f"PENDIENTE — {text}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
