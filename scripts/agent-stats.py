#!/usr/bin/env python3
"""Métricas de agentes y skills del harness desde `opencode.db` (read-only).

Lee la SQLite de opencode (`file:...?mode=ro`, timeout=3) y agrega, en una
ventana de días sobre `session.time_created`:

- sesiones por `session.agent` (`NULL` → `(raíz)`): sesiones, hijas, duración
  (Σ `time_updated-time_created` con **cap de 24 h por sesión**, documentado
  abajo), costo y tokens.
- delegaciones: task-parts (`state.input.subagent_type`) con estado ok/error y
  duración (`state.time.end-start`).
- skills: parts `tool=skill` por `state.input.name`.
- errores: parts por `tool` con `state.status=error` (top 10) y ratio
  err/spans.
- `--daily`: matriz día x agente (costo) y día → sesiones.

**Sólo agregados**: jamás se emite contenido (ni prompts, ni comandos, ni
outputs, ni paths de sesión); únicamente nombres de agente/tool/skill/tipo y
números. El cap de 24 h por sesión evita que una sesión huérfana abierta
(históricamente 14 h) infle la duración total.

Uso:

    scripts/agent-stats.py [--days 30] [--app asf] [--project RUTA] [--json] [--daily]

Default `--app asf` → checkout del registry; `--project` apunta a un worktree
arbitrario (y gana sobre `--app`). Si la app no resuelve, no se filtra. Sin
`opencode.db` legible → exit 1 con `PENDIENTE` explícito (nunca inventar).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
CAP_MS = 24 * 60 * 60 * 1000  # cap de duración por sesión (24 h)
ROOT_AGENT = "(raíz)"
UNKNOWN_TYPE = "(sin tipo)"
TOP_ERRORS = 10


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


def _err(text: str) -> None:
    print(f"agent-stats: {text}", file=sys.stderr)


def _pendiente(text: str) -> int:
    _err(f"PENDIENTE — {text}")
    return 1


def _open_db(path: Path) -> sqlite3.Connection | None:
    """Conexión read-only; `None` si el archivo no existe o no es una db válida."""
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        con.execute("SELECT 1 FROM session LIMIT 1")
    except sqlite3.Error:
        return None
    return con


def _resolve_app_checkout(app: str) -> Path | None:
    path = ROOT / "config" / "apps" / f"{app}.json"
    if not path.is_file():
        return None
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    checkout = data.get("checkout")
    if not isinstance(checkout, str) or not checkout:
        return None
    return (ROOT / checkout).resolve()


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _new_agent() -> dict[str, Any]:
    return {
        "sessions": 0,
        "child_sessions": 0,
        "dur_ms": 0,
        "cost": 0.0,
        "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0},
    }


def _load_sessions(
    con: sqlite3.Connection, start: int, end: int, project_dir: str | None
) -> list[tuple[Any, ...]]:
    where = ["s.time_created >= ?", "s.time_created < ?"]
    params: list[Any] = [start, end]
    if project_dir is not None:
        where.append("(p.worktree = ? OR s.directory = ? OR s.directory LIKE ? ESCAPE '\\')")
        params.extend([project_dir, project_dir, f"{_like_escape(project_dir)}/%"])
    query = (
        "SELECT s.id, s.parent_id, s.agent, s.time_created, s.time_updated, s.cost, "
        "s.tokens_input, s.tokens_output, s.tokens_reasoning, s.tokens_cache_read, s.tokens_cache_write "
        "FROM session s LEFT JOIN project p ON p.id = s.project_id "
        f"WHERE {' AND '.join(where)}"
    )
    return list(con.execute(query, params).fetchall())


def _aggregate_sessions(rows: list[tuple[Any, ...]]) -> dict[str, dict[str, Any]]:
    by_agent: dict[str, dict[str, Any]] = {}
    for row in rows:
        agent = row[2] if isinstance(row[2], str) and row[2] else ROOT_AGENT
        entry = by_agent.setdefault(agent, _new_agent())
        entry["sessions"] += 1
        if row[1] is not None:
            entry["child_sessions"] += 1
        created = _as_int(row[3]) or 0
        updated = _as_int(row[4]) or 0
        entry["dur_ms"] += min(max(updated - created, 0), CAP_MS)
        entry["cost"] += float(row[5] or 0.0)
        tokens: dict[str, int] = entry["tokens"]
        for key, index in (
            ("input", 6),
            ("output", 7),
            ("reasoning", 8),
            ("cache_read", 9),
            ("cache_write", 10),
        ):
            tokens[key] += _as_int(row[index]) or 0
    for entry in by_agent.values():
        entry["dur_hours"] = round(entry["dur_ms"] / 3_600_000, 2)
        entry["cost"] = round(entry["cost"], 6)
    return by_agent


def _subagent_type(inp: dict[str, Any]) -> str:
    for key in ("subagent_type", "name"):
        value = inp.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return UNKNOWN_TYPE


def _duration_ms(state: dict[str, Any]) -> int | None:
    raw = state.get("time")
    if not isinstance(raw, dict):
        return None
    start = _as_int(raw.get("start"))
    end = _as_int(raw.get("end"))
    if start is None or end is None:
        return None
    return max(end - start, 0)


def _aggregate_parts(
    rows: list[tuple[Any, ...]],
) -> tuple[dict[str, Any], dict[str, int], dict[str, Any]]:
    delegations: dict[str, dict[str, Any]] = {}
    skills: dict[str, int] = {}
    errors: dict[str, int] = {}
    spans = 0
    for (raw,) in rows:
        if not isinstance(raw, str) or not raw:
            continue
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        tool = data.get("tool")
        if not isinstance(tool, str) or not tool:
            continue
        spans += 1
        state = data.get("state")
        if not isinstance(state, dict):
            state = {}
        inp = state.get("input")
        if not isinstance(inp, dict):
            inp = {}
        status = state.get("status")
        if tool == "task":
            kind = _subagent_type(inp)
            entry = delegations.setdefault(kind, {"total": 0, "ok": 0, "error": 0, "dur_ms": 0})
            entry["total"] += 1
            if status == "error":
                entry["error"] += 1
            else:
                entry["ok"] += 1
            dur = _duration_ms(state)
            if dur is not None:
                entry["dur_ms"] += dur
        elif tool == "skill":
            name = inp.get("name")
            key = name.strip() if isinstance(name, str) and name.strip() else "(sin nombre)"
            skills[key] = skills.get(key, 0) + 1
        if status == "error":
            errors[tool] = errors.get(tool, 0) + 1
    total_tasks = sum(entry["total"] for entry in delegations.values())
    total_errors = sum(errors.values())
    errors_out = {
        "total": total_errors,
        "spans": spans,
        "ratio": round(total_errors / spans, 4) if spans else 0.0,
        "by_tool": [
            {"name": name, "count": count}
            for name, count in sorted(errors.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_ERRORS]
        ],
    }
    delegations_out = {
        "total": total_tasks,
        "ok": sum(entry["ok"] for entry in delegations.values()),
        "error": sum(entry["error"] for entry in delegations.values()),
        "by_type": {name: delegations[name] for name in sorted(delegations)},
    }
    return delegations_out, dict(sorted(skills.items())), errors_out


def _daily_matrix(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    days: dict[str, dict[str, Any]] = {}
    for row in rows:
        agent = row[2] if isinstance(row[2], str) and row[2] else ROOT_AGENT
        created = _as_int(row[3]) or 0
        day = datetime.fromtimestamp(created / 1000, UTC).date().isoformat()
        entry = days.setdefault(day, {"day": day, "sessions": 0, "cost": 0.0, "by_agent": {}})
        entry["sessions"] += 1
        entry["cost"] += float(row[5] or 0.0)
        by_agent: dict[str, float] = entry["by_agent"]
        by_agent[agent] = round(by_agent.get(agent, 0.0) + float(row[5] or 0.0), 6)
    for entry in days.values():
        entry["cost"] = round(entry["cost"], 6)
    return [days[day] for day in sorted(days)]


def collect_agent_stats(
    con: sqlite3.Connection,
    *,
    days: int = 30,
    project_dir: str | None = None,
    daily: bool = False,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Agrega la db (sin contenido) en el objeto determinista de la CLI."""
    end = now_ms if now_ms is not None else int(datetime.now(UTC).timestamp() * 1000)
    start = end - days * 24 * 60 * 60 * 1000
    rows = _load_sessions(con, start, end, project_dir)
    by_agent = _aggregate_sessions(rows)
    part_rows: list[tuple[Any, ...]] = []
    if rows:
        ids = [row[0] for row in rows]
        marks = ",".join("?" for _ in ids)
        part_rows = list(
            con.execute(
                f"SELECT data FROM part WHERE session_id IN ({marks}) "
                "AND time_created >= ? AND time_created < ?",
                [*ids, start, end],
            ).fetchall()
        )
    delegations, skills, errors = _aggregate_parts(part_rows)
    result: dict[str, Any] = {
        "days": days,
        "window": {"start_ms": start, "end_ms": end},
        "by_agent": by_agent,
        "delegations": delegations,
        "skills": skills,
        "errors": errors,
    }
    if daily:
        result["daily"] = _daily_matrix(rows)
    return result


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_markdown(data: dict[str, Any]) -> str:
    """Tabla markdown legible del objeto de `collect_agent_stats`."""
    by_agent: dict[str, Any] = data["by_agent"]
    out: list[str] = [f"# agent-stats — últimos {data['days']} días", ""]

    out.append("## Por agente")
    agent_rows: list[list[str]] = []
    for name in sorted(by_agent):
        a = by_agent[name]
        t = a["tokens"]
        agent_rows.append(
            [
                name,
                str(a["sessions"]),
                str(a["child_sessions"]),
                str(a["dur_hours"]),
                f"{a['cost']:.4f}",
                str(t["input"]),
                str(t["output"]),
                str(t["reasoning"]),
                str(t["cache_read"]),
                str(t["cache_write"]),
            ]
        )
    out.append(
        _md_table(
            [
                "agente",
                "sesiones",
                "hijas",
                "dur (h)",
                "costo",
                "tok in",
                "tok out",
                "reasoning",
                "cache r",
                "cache w",
            ],
            agent_rows,
        )
    )
    out.append("")

    delegations = data["delegations"]
    out.append(f"## Delegaciones (task) — {delegations['total']} en total")
    del_rows = [
        [name, str(entry["total"]), str(entry["ok"]), str(entry["error"]), str(entry["dur_ms"])]
        for name, entry in delegations["by_type"].items()
    ]
    out.append(_md_table(["subagent_type", "total", "ok", "error", "dur (ms)"], del_rows))
    out.append("")

    out.append("## Skills")
    skill_rows = [[name, str(count)] for name, count in data["skills"].items()]
    out.append(_md_table(["skill", "usos"], skill_rows) if skill_rows else "_sin usos en la ventana._")
    out.append("")

    errors = data["errors"]
    out.append(
        f"## Errores por tool — {errors['total']}/{errors['spans']} spans ({errors['ratio'] * 100:.1f}%)"
    )
    err_rows = [[item["name"], str(item["count"])] for item in errors["by_tool"]]
    out.append(_md_table(["tool", "errores"], err_rows) if err_rows else "_sin errores en la ventana._")
    out.append("")

    if "daily" in data:
        out.append("## Por día")
        events: list[dict[str, Any]] = data["daily"]
        agents = sorted({agent for day in events for agent in day["by_agent"]})
        daily_rows = [
            [
                day["day"],
                str(day["sessions"]),
                f"{day['cost']:.4f}",
                *[f"{day['by_agent'].get(a, 0.0):.4f}" for a in agents],
            ]
            for day in events
        ]
        out.append(_md_table(["día", "sesiones", "costo", *agents], daily_rows))
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent-stats.py", description=__doc__)
    ap.add_argument("--days", type=int, default=30, help="ventana en días sobre session.time_created")
    ap.add_argument("--app", default="asf", help="id de app del registry (default asf)")
    ap.add_argument("--project", default=None, help="worktree absoluto (gana sobre --app)")
    ap.add_argument("--json", action="store_true", help="salida JSON en vez de markdown")
    ap.add_argument("--daily", action="store_true", help="matriz día x agente de costo")
    args = ap.parse_args(argv)
    if args.days <= 0:
        _err(f"--days inválido: {args.days}")
        return 2

    db_path = Path(DEFAULT_DB)
    con = _open_db(db_path)
    if con is None:
        return _pendiente(f"db no legible: {db_path}")
    try:
        if args.project:
            project_dir = str(Path(args.project).resolve())
        else:
            checkout = _resolve_app_checkout(args.app)
            if checkout is None:
                _err(f"app sin checkout en el registry: {args.app} (sin filtro de proyecto)")
                project_dir = None
            else:
                project_dir = str(checkout)
        data = collect_agent_stats(con, days=args.days, project_dir=project_dir, daily=args.daily)
    except sqlite3.Error as exc:
        return _pendiente(f"db no legible: {db_path} ({exc})")
    finally:
        con.close()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_markdown(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
