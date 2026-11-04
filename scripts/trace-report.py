#!/usr/bin/env python3
"""Traza estilo OpenTelemetry de una corrida del harness, en un HTML único.

Lee la SQLite de opencode (tablas `session` y `part`) **read-only** y arma el
árbol orquestador → subagents de una corrida, delimitada por anclas:

- `--session SES_ID` (repetible): sesión ancla directa.
- `--grep TEXTO` (repetible): task-parts cuyo `data` contiene TEXTO; el ancla
  es el hijo (`state.metadata.sessionId`).
- Default (sin anclas explícitas): el path absoluto del worktree de la corrida
  (`state/worktrees/<app>/<slug>`), que aparece literal en los prompts de
  delegación.

Salida: un HTML de archivo único con **tres vistas**: **Traza** (default, estilo
Sentry: filas con chevron/chip/barra/duración, toolbar de búsqueda y filtros por
categoría, panel derecho de detalle con diff unificado, minimapa/zoom sobre la
línea de tiempo **virtual** y gaps de inactividad comprimidos), **Rendimiento**
(tabla + barras por agente) y **Diagrama** (grafo de delegación + swimlanes de
sesiones con las rondas R1..RN). La paleta es WCAG 2.x AA (light) y tiene
variante dark WCAG AA bajo `@media (prefers-color-scheme: dark)`; ningún estado
se comunica sólo por color.

La vista Traza usa un único `<script id="view-js">` inline (sin recursos
externos, sin `eval`, sin `innerHTML` con datos: render por `textContent`,
`createElement` y atributos); el resto son las vistas v2/v3. Con `--no-js` se
emite la v3 sin minimapa/zoom/teclado, pero con gaps comprimidos, err-badges,
issues-chip, diffs y turnos LLM (todo HTML estático).

Gaps/tiempos: los huecos entre eventos > `--max-gap` (default 120 s) se
comprimen a `--gap-render` ms (default 20000) en la línea virtual
(`v_start`/`v_end`); `dur_text` y tooltips siguen en tiempo real.

Turnos LLM: cada par step-start/step-finish de un `message_id` es un span `llm`
(pareado FIFO por `(time_created, type, id)`; start sin finish → `open`), con
tokens/cost/model del turno. Se intercalan con los tools como hijos de sesión y
alimentan el chip `Turnos` (`summary.time_split`) y la columna "Tiempo LLM vs
tools". `llm_ms`/`tool_ms` son uniones de intervalos (tiempo de pared, ≤100%
del activo); `gap_ms` es el hueco **sin actividad real** (no los internos de un
span largo).

Render: si la traza supera `MAX_TRACE_ROWS` (20000) filas se renderiza sólo la
cabeza con una nota (el JSON conserva todo); el worker real (~1.8k filas) queda
lejos y genera en <2 s / <30 MB.

El JSON embebido (`<script id="trace-data" type="application/json">`) es la
fuente de datos de las vistas; el archivo es determinista para los mismos datos
y args (`--now` fija el `generated_at`). Nunca se commitea: vive en `state/`
(gitignored).

stdout: sólo la ruta del archivo escrito (pipeable); el progreso va a stderr.
Sin anclas o sin db → exit 1 con `PENDIENTE` explícito (nunca inventar datos).
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import difflib
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_PAD_SECONDS = 300.0
DEFAULT_MAX_BYTES = 8192
DEFAULT_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
DEFAULT_MAX_GAP_SECONDS = 120.0
DEFAULT_GAP_RENDER_MS = 20000
DIFF_MAX_LINES = 400
DIFF_TRUNC_MARK = "…diff truncado"
TRUNC_MARK = "… truncado"
OMIT_MARK = "<omitido (--no-outputs)>"
_MISSING_START = 1 << 62

# Paleta WCAG 2.x AA verificada con `wcag_contrast` (contraste sobre #FFFFFF):
# gráficos ≥3:1 y texto ≥4.5:1. Fuente única: se embebe en `summary.palette`.
PALETTE: dict[str, str] = {
    "bg": "#FFFFFF",
    "surface": "#F6F6F6",
    "text": "#1A1A1A",
    "build": "#0072B2",
    "coder": "#D55E00",
    "reviewer": "#007B5E",
    "interrogator": "#7A4FA3",
    "other": "#2E86AB",
    "error": "#B3261E",
    "ok": "#14682F",
    "focus": "#005F9E",
    "llm": "#555555",
}
GRAPHIC_TOKENS = ("build", "coder", "reviewer", "interrogator", "other")
TEXT_TOKENS = ("text", "error", "ok", "focus")
# `llm` es una categoría de fila, no un rol de agente: se testea aparte (light
# 7.46 vs blanco; dark 5.97 vs #141821, ver DARK_PALETTE).

# Paleta dark (sólo bajo `@media (prefers-color-scheme: dark)`). Verificada con
# `wcag_contrast` contra `DARK_BG`: gráficos ≥3:1 y texto/ok/error ≥4.5:1. Los
# hex propuestos por la spec v5 pasan tal cual (azul 6.10, naranja 5.97, verde
# 6.75, morado 6.07, celeste 6.75, error 6.49, ok 7.59, texto 14.73); `focus`
# no venía en la lista y se usa el celeste (6.75). No hubo que ajustar ninguno.
DARK_BG = "#141821"
DARK_PALETTE: dict[str, str] = {
    "bg": "#141821",
    "surface": "#1D2430",
    "text": "#E6EAF2",
    "build": "#4C9FD4",
    "coder": "#E8762D",
    "reviewer": "#2FB483",
    "interrogator": "#A888D8",
    "other": "#5FA8CE",
    "error": "#F0796F",
    "ok": "#3FBF7F",
    "focus": "#5FA8CE",
    # gris claro de turnos LLM; la spec v6 proponía #9AA6B8 → pasa tal cual.
    "llm": "#9AA6B8",
}
PATTERN_KINDS = ("solid", "hatch", "dot")
PATTERN_LABELS = {"solid": "sólido", "hatch": "rayado", "dot": "punteado"}

# Categorías de fila de la vista Traza (orden fijo: checkboxes del toolbar).
CATEGORY_ORDER = ("sesion", "llm", "task", "bash", "files", "skill", "web", "memory", "otros")
CATEGORY_LABELS = {
    "sesion": "sesión",
    "llm": "llm",
    "task": "task",
    "bash": "bash",
    "files": "edit/write/read/glob/grep",
    "skill": "skill",
    "web": "web",
    "memory": "memory",
    "otros": "otros",
}
# Token de paleta por categoría (sesión usa el token del rol del agente).
CATEGORY_TOKEN = {
    "llm": "llm",
    "task": "coder",
    "bash": "other",
    "files": "reviewer",
    "skill": "interrogator",
    "web": "build",
    "memory": "other",
    "otros": "other",
}
LABEL_MAX = 100
# Corte de seguridad de render: si la traza supera este número de filas se
# renderiza sólo la cabeza con una nota (el JSON conserva todo). El worker real
# (~1.8k filas) queda lejos; se documenta en el docstring.
MAX_TRACE_ROWS = 20000


def _srgb_channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb_channel(r) + 0.7152 * _srgb_channel(g) + 0.0722 * _srgb_channel(b)


def wcag_contrast(fg: str, bg: str) -> float:
    """Contraste WCAG 2.x entre dos colores hex (#RRGGBB)."""
    l1, l2 = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _role_token(agent: str | None) -> str:
    if not agent:
        return "other"
    if agent == "build" or agent == "orchestrator":
        return "build"
    if agent == "coder":
        return "coder"
    if agent.startswith("reviewer"):
        return "reviewer"
    if agent.startswith("interrogator") or agent.startswith("delegate"):
        return "interrogator"
    return "other"


def _fmt_ms(ms: int | float | None) -> str:
    if ms is None:
        return ""
    value = int(ms)
    if value < 1000:
        return f"{value} ms"
    if value < 60000:
        return f"{value / 1000:.1f} s"
    return f"{value // 60000}m{round((value % 60000) / 1000)}s"


def _short_id(sid: str) -> str:
    return sid[-6:] if len(sid) > 6 else sid


def _iso(ms: Any) -> str:
    if not isinstance(ms, int):
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _truncate(text: str, limit: int = LABEL_MAX) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _dur_text(ms: Any) -> str:
    """Formato Sentry: ms si <1s, s con 2 decimales si ≥1s, m/h si es largo."""
    if not isinstance(ms, (int, float)):
        return ""
    value = int(ms)
    if value < 0:
        value = 0
    if value < 1000:
        return f"{value:.2f}ms"
    if value < 60000:
        return f"{value / 1000:.2f}s"
    total_s = value / 1000.0
    if value < 3600000:
        return f"{int(total_s // 60)}m {int(total_s % 60)}s"
    return f"{int(total_s // 3600)}h {int((total_s % 3600) // 60)}m"


def _gap_label(delta_ms: int) -> str:
    return f"⋯ {_dur_text(delta_ms)} sin actividad"


def _unified_diff(tool: str, raw_input: Any) -> list[str] | None:
    """Diff unificado n=3 de un edit/write; `None` si no aplica o no hay cambios.

    Campos reales verificados en la db: `edit` = {filePath, oldString, newString};
    `write` = {filePath, content}. En `write` se compara contra archivo vacío.
    """
    if not isinstance(raw_input, dict):
        return None
    path = raw_input.get("filePath") or raw_input.get("path") or ""
    path = str(path)
    if tool == "edit":
        old = raw_input.get("oldString")
        new = raw_input.get("newString")
    elif tool == "write":
        old = ""
        new = raw_input.get("content")
    else:
        return None
    if old is None and new is None:
        return None
    old_text = old if isinstance(old, str) else ""
    new_text = new if isinstance(new, str) else ""
    lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=path,
            tofile=path,
            n=3,
            lineterm="",
        )
    )
    if not lines:
        return []
    if len(lines) > DIFF_MAX_LINES:
        lines = [*lines[:DIFF_MAX_LINES], DIFF_TRUNC_MARK]
    return lines


def _diff_html(lines: list[str], span_id: str | None = None) -> str:
    rows: list[str] = []
    for line in lines:
        if line.startswith("+"):
            cls = "d-add"
        elif line.startswith("-"):
            cls = "d-del"
        elif line.startswith("@@"):
            cls = "d-hunk"
        else:
            cls = "d-ctx"
        rows.append(f'<span class="{cls}">{_esc(line)}</span>\n')
    attr = f' data-span-id="{_esc(span_id)}"' if span_id else ""
    return f'<pre class="diff"{attr}>' + "".join(rows) + "</pre>"


def _category_for(tool: str) -> str:
    if tool == "task":
        return "task"
    if tool == "bash":
        return "bash"
    if tool in ("edit", "write", "read", "glob", "grep"):
        return "files"
    if tool == "skill":
        return "skill"
    if tool in ("webfetch", "websearch"):
        return "web"
    if tool == "memory":
        return "memory"
    return "otros"


def _row_id(raw: Any, fallback: str) -> str:
    """Id único de fila: el del part/sesión o un `part-<hash>` determinista."""
    rid = str(raw) if raw else ""
    if rid:
        return rid
    digest = hashlib.sha1(fallback.encode("utf-8")).hexdigest()[:12]
    return f"part-{digest}"


def _model_str(model: Any) -> str:
    if isinstance(model, dict):
        return str(model.get("id") or model.get("modelID") or "")
    return str(model) if model else ""


def _span_label(span: dict[str, Any]) -> str:
    tool = str(span.get("tool") or "?")
    if span.get("kind") == "llm":
        model_raw = span.get("model")
        model: dict[str, Any] = model_raw if isinstance(model_raw, dict) else {}
        label = f"LLM · {_short_model(model.get('modelID'))}"
        if span.get("open"):
            label += " (abierto)"
        return _truncate(label)
    if tool == "task":
        child = span.get("child") if isinstance(span.get("child"), dict) else None
        to_agent = (child.get("agent") if child else None) or span.get("subagent_type") or "?"
        desc = str(span.get("description") or "")
        base = f"task → {to_agent}"
        if desc:
            base += f" — {desc}"
        return _truncate(base)
    title = str(span.get("title") or "")
    return _truncate(f"{tool} — {title}" if title else tool)


def _kv_items(items: list[list[Any]]) -> list[list[str]]:
    return [[str(key), str(value)] for key, value in items]


def _llm_attrs(span: dict[str, Any]) -> list[dict[str, Any]]:
    model_raw = span.get("model")
    model: dict[str, Any] = model_raw if isinstance(model_raw, dict) else {}
    tokens_raw = span.get("tokens")
    tokens: dict[str, Any] = tokens_raw if isinstance(tokens_raw, dict) else {}
    turn_items: list[list[Any]] = [
        ["model", model.get("modelID") or ""],
        ["provider", model.get("providerID") or ""],
        ["reason", span.get("reason") or ""],
        ["open", "sí" if span.get("open") else "no"],
        ["tokens_input", tokens.get("input", 0)],
        ["tokens_output", tokens.get("output", 0)],
        ["tokens_reasoning", tokens.get("reasoning", 0)],
        ["tokens_cache_read", tokens.get("cache_read", 0)],
        ["tokens_cache_write", tokens.get("cache_write", 0)],
        ["cost", f"${float(span.get('cost') or 0):.6f}"],
        ["message_id", span.get("message_id") or ""],
        ["variant", span.get("variant") or ""],
        ["mode", span.get("mode") or ""],
    ]
    groups = [{"name": "turno", "items": _kv_items(turn_items)}]
    sess_items: list[list[Any]] = [
        ["agent", span.get("agent") or "?"],
        ["session_id", span.get("session") or ""],
    ]
    groups.append({"name": "sesión", "items": _kv_items(sess_items)})
    return groups


def _span_attrs(span: dict[str, Any], k_by: dict[tuple[Any, Any], int]) -> list[dict[str, Any]]:
    if span.get("kind") == "llm":
        return _llm_attrs(span)
    items: list[list[Any]] = [
        ["tool", span.get("tool") or "?"],
        ["status", span.get("status") or ""],
        ["callID", span.get("id") or ""],
        ["t_inicio", _iso(span.get("start"))],
        ["t_fin", _iso(span.get("end"))],
    ]
    groups = [{"name": "span", "items": _kv_items(items)}]
    if span.get("tool") == "task":
        child = span.get("child") if isinstance(span.get("child"), dict) else None
        k = k_by.get((span.get("start"), child.get("id") if child else None))
        deleg: list[list[Any]] = []
        if k is not None:
            deleg.append(["k", k])
        deleg.append(["to_agent", span.get("subagent_type") or ""])
        if child:
            deleg.append(["to_session", child.get("id") or ""])
        deleg.append(["description", span.get("description") or ""])
        deleg.append(["dur", span.get("dur_text") or ""])
        groups.append({"name": "delegación", "items": _kv_items(deleg)})
    return groups


def _session_attrs(node: dict[str, Any]) -> list[dict[str, Any]]:
    tokens = node.get("tokens") or {}
    execution = node.get("execution") or {}
    items: list[list[Any]] = [
        ["agent", node.get("agent") or "?"],
        ["model", _model_str(node.get("model"))],
        ["title", node.get("title") or ""],
        ["cost", f"${float(node.get('cost') or 0):.4f}"],
        ["tokens_input", tokens.get("input", 0)],
        ["tokens_output", tokens.get("output", 0)],
        ["tokens_reasoning", tokens.get("reasoning", 0)],
        ["wall_ms", node.get("duration_ms", 0)],
        ["own_ms", node.get("own_ms", node.get("duration_ms", 0))],
    ]
    if node.get("windowed"):
        items.append(["windowed", "sí"])
    groups = [{"name": "sesión", "items": _kv_items(items)}]
    exec_items: list[list[Any]] = []
    for tool, count in sorted((execution.get("by_tool") or {}).items()):
        exec_items.append([tool, count])
    if "n_commands" in execution:
        exec_items.append(["n_commands", execution["n_commands"]])
    if "n_files" in execution:
        exec_items.append(["n_files", execution["n_files"]])
    if exec_items:
        groups.append({"name": "ejecución", "items": _kv_items(exec_items)})
    return groups


def _decorate_rows(
    roots: list[dict[str, Any]],
    palette: dict[str, str],
    k_by: dict[tuple[Any, Any], int],
) -> tuple[dict[str, int], int]:
    """Anota cada nodo/span con label/dur_text/category/color/attrs y agrega ids.

    Devuelve `(avg_ms_by_tool, row_count)`: promedio de duración por tool
    (sesiones keyed por agente) y cantidad de filas de la vista Traza.
    """
    durations: dict[str, list[int]] = {}
    seen_sessions: set[str] = set()
    used_ids: set[str] = set()
    row_count = 0

    def add_duration(tool: str, ms: Any) -> None:
        durations.setdefault(tool, []).append(int(ms) if isinstance(ms, (int, float)) else 0)

    def visit_span(span: dict[str, Any], depth: int) -> None:
        nonlocal row_count
        tool = str(span.get("tool") or "?")
        is_llm = span.get("kind") == "llm"
        category = str(span.get("category") or _category_for(tool))
        span["id"] = _row_id(span.get("id"), f"span:{tool}:{depth}:{len(used_ids)}")
        used_ids.add(str(span["id"]))
        span["category"] = category
        span["depth"] = depth
        span["label"] = _span_label(span)
        span["dur_text"] = _dur_text(span.get("duration_ms"))
        span["color"] = palette[CATEGORY_TOKEN.get(category, "other")]
        # Un turno LLM nunca es "error" a los fines de issues-only (el status
        # error sólo pinta el ✕ por reason=="error").
        span["self_errors"] = 0 if is_llm else (1 if span.get("status") == "error" else 0)
        span["attrs"] = _span_attrs(span, k_by)
        row_count += 1
        add_duration(tool, span.get("duration_ms"))
        child = span.get("child")
        if isinstance(child, dict) and not child.get("ref"):
            visit_session(child, depth + 1)

    def visit_session(node: dict[str, Any], depth: int) -> None:
        nonlocal row_count
        sid = str(node.get("id") or "")
        if sid in seen_sessions:
            return
        seen_sessions.add(sid)
        used_ids.add(sid)
        agent = str(node.get("agent") or "?")
        node["category"] = "sesion"
        node["depth"] = depth
        node["tool"] = agent
        node["label"] = f"{agent} (sesión)"
        node["dur_text"] = _dur_text(node.get("duration_ms"))
        node["color"] = palette[_role_token(agent)]
        node["self_errors"] = int(node.get("errors") or 0)
        node["attrs"] = _session_attrs(node)
        row_count += 1
        add_duration(agent, node.get("duration_ms"))
        for span in node.get("spans", []):
            visit_span(span, depth + 1)

    for root in roots:
        visit_session(root, 0)
    avg = {tool: round(sum(values) / len(values)) for tool, values in durations.items() if values}
    return avg, row_count


def _virtual_timeline(
    roots: list[dict[str, Any]],
    max_gap_ms: int,
    gap_render_ms: int,
) -> tuple[dict[int, int], list[dict[str, Any]], int]:
    """Mapea tiempo real → virtual comprimiendo huecos > `max_gap_ms`.

    Devuelve `(mapping, gaps, total_virtual)`. Cada hueco ocupa exactamente
    `gap_render_ms` en la línea virtual; el resto conserva el delta real.
    """
    events: set[int] = set()

    def collect(node: dict[str, Any]) -> None:
        for key in ("start", "end"):
            value = node.get(key)
            if isinstance(value, int):
                events.add(value)
        for span in node.get("spans", []):
            for key in ("start", "end"):
                value = span.get(key)
                if isinstance(value, int):
                    events.add(value)
            child = span.get("child")
            if isinstance(child, dict) and not child.get("ref"):
                collect(child)

    for root in roots:
        collect(root)
    keys = sorted(events)
    if not keys:
        return {}, [], 0
    mapping: dict[int, int] = {keys[0]: 0}
    gaps: list[dict[str, Any]] = []
    virtual = 0
    prev = keys[0]
    for key in keys[1:]:
        delta = key - prev
        if delta > max_gap_ms:
            gaps.append({"t0": prev, "t1": key, "virtual_at": virtual, "label": _gap_label(delta)})
            virtual += gap_render_ms
        else:
            virtual += delta
        mapping[key] = virtual
        prev = key
    return mapping, gaps, virtual


def _assign_virtual(roots: list[dict[str, Any]], mapping: dict[int, int]) -> None:
    def assign(node: dict[str, Any]) -> None:
        node["v_start"] = mapping.get(node.get("start"), 0)
        node["v_end"] = mapping.get(node.get("end"), node["v_start"])
        for span in node.get("spans", []):
            assign_span(span)

    def assign_span(span: dict[str, Any]) -> None:
        span["v_start"] = mapping.get(span.get("start"), 0)
        span["v_end"] = mapping.get(span.get("end"), span["v_start"])
        child = span.get("child")
        if isinstance(child, dict) and not child.get("ref"):
            assign(child)

    for root in roots:
        assign(root)


def _resolve_root() -> Path:
    """Raíz del harness desde `__file__`.

    Si el script corre dentro de un worktree (`<harness>/state/worktrees/<app>/<slug>`)
    sube a la raíz del harness: `state/`, `work/` y el registry viven ahí, no en
    cada worktree. Fuera de un worktree devuelve la raíz del repo del script.
    """
    root = Path(__file__).resolve().parent.parent
    parts = root.parts
    for i, part in enumerate(parts):
        if part == "state" and i + 1 < len(parts) and parts[i + 1] == "worktrees":
            if i == 0:
                return Path(parts[0])
            return Path(*parts[:i])
    return root


ROOT = _resolve_root()


def _safe_stdout(text: str) -> None:
    print(text)


def _err(text: str) -> None:
    print(f"trace-report: {text}", file=sys.stderr)


def _pendiente(text: str) -> int:
    _err(f"PENDIENTE — {text}")
    return 1


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _fmt_payload(value: Any, max_bytes: int, omit: bool) -> str:
    if omit:
        return OMIT_MARK
    if value is None:
        return ""
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    cut = raw[:max_bytes].decode("utf-8", errors="ignore")
    return f"{cut} {TRUNC_MARK} ({len(raw)} bytes)"


def _top_counts(counter: dict[str, int], limit: int) -> list[dict[str, Any]]:
    ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": name, "count": count} for name, count in ordered[:limit]]


def _execution_from_parts(parts: list[dict[str, Any]], omit: bool) -> dict[str, Any]:
    """Qué ejecutó una sesión/agente: counts por tool, comandos/files/skills.

    `commands` (bash, `state.input.command`) y `files` (edit/write, `filePath`/`path`)
    son únicos, ordenados por apariciones desc y luego alfa. Con `omit` se
    reemplazan por sus counts (`n_commands`/`n_files`); el `description` de las
    delegaciones nunca sale del prompt.
    """
    by_tool: dict[str, int] = {}
    commands: dict[str, int] = {}
    files: dict[str, int] = {}
    skills: dict[str, int] = {}
    for part in parts:
        tool = str(part.get("tool") or "?")
        by_tool[tool] = by_tool.get(tool, 0) + 1
        raw_input = part.get("input")
        if not isinstance(raw_input, dict):
            raw_input = {}
        if tool == "bash":
            command = raw_input.get("command")
            if isinstance(command, str) and command:
                commands[command[:60]] = commands.get(command[:60], 0) + 1
        elif tool in ("edit", "write"):
            path = raw_input.get("filePath") or raw_input.get("path")
            if isinstance(path, str) and path:
                files[path] = files.get(path, 0) + 1
        elif tool == "skill":
            name = raw_input.get("name")
            if isinstance(name, str) and name:
                skills[name] = skills.get(name, 0) + 1
    if omit:
        return {
            "by_tool": by_tool,
            "n_commands": len(commands),
            "n_files": len(files),
            "skills": _top_counts(skills, 8),
        }
    return {
        "by_tool": by_tool,
        "commands": _top_counts(commands, 8),
        "files": _top_counts(files, 10),
        "skills": _top_counts(skills, 8),
    }


def _task_parts(con: sqlite3.Connection, text: str) -> list[dict[str, Any]]:
    pattern = f"%{_like_escape(text)}%"
    rows = con.execute(
        "SELECT id, session_id, data FROM part "
        "WHERE json_extract(data, '$.tool') = 'task' AND data LIKE ? ESCAPE '\\'",
        (pattern,),
    ).fetchall()
    found: list[dict[str, Any]] = []
    for row in rows:
        try:
            data = json.loads(row[2])
        except (TypeError, json.JSONDecodeError):
            continue
        state = data.get("state") or {}
        metadata = state.get("metadata") or {}
        child = metadata.get("sessionId")
        if not isinstance(child, str) or not child:
            continue
        raw_input = state.get("input")
        description = raw_input.get("description") if isinstance(raw_input, dict) else None
        found.append(
            {
                "id": str(row[0]),
                "session": str(row[1]),
                "child": child,
                "start": _as_int((state.get("time") or {}).get("start")),
                "description": description if isinstance(description, str) else None,
            }
        )
    return found


def _find_anchors(
    con: sqlite3.Connection,
    sessions: list[str],
    greps: list[str],
    defaults: list[str],
) -> tuple[list[str], dict[str, int], list[dict[str, Any]]]:
    anchors: set[str] = set(sessions)
    starts: dict[str, int] = {}
    matches: list[dict[str, Any]] = []
    seen_parts: set[str] = set()
    texts = list(greps) if greps else list(defaults)
    for text in texts:
        for match in _task_parts(con, text):
            part_id = str(match["id"])
            if part_id not in seen_parts:
                seen_parts.add(part_id)
                matches.append(match)
            child = match["child"]
            anchors.add(child)
            start = match["start"]
            if start is not None:
                starts[child] = min(starts.get(child, start), start)
    return sorted(anchors), starts, matches


def _load_sessions(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = con.execute(
        "SELECT id, parent_id, agent, model, title, cost, tokens_input, tokens_output, "
        "tokens_reasoning, tokens_cache_read, tokens_cache_write, time_created, time_updated "
        "FROM session"
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        model: Any = row[3]
        if isinstance(model, str) and model:
            with contextlib.suppress(json.JSONDecodeError):
                model = json.loads(model)
        out[str(row[0])] = {
            "id": str(row[0]),
            "parent_id": row[1] if isinstance(row[1], str) else None,
            "agent": row[2] if isinstance(row[2], str) else None,
            "model": model,
            "title": row[4] if isinstance(row[4], str) else None,
            "cost": float(row[5]) if isinstance(row[5], (int, float)) else 0.0,
            "tokens_input": _as_int(row[6]) or 0,
            "tokens_output": _as_int(row[7]) or 0,
            "tokens_reasoning": _as_int(row[8]) or 0,
            "tokens_cache_read": _as_int(row[9]) or 0,
            "tokens_cache_write": _as_int(row[10]) or 0,
            "time_created": _as_int(row[11]) or 0,
            "time_updated": _as_int(row[12]) or 0,
        }
    return out


def _load_parts(con: sqlite3.Connection, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT id, session_id, data FROM part WHERE session_id IN ({placeholders})",
        tuple(sorted(ids)),
    ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            data = json.loads(row[2])
        except (TypeError, json.JSONDecodeError):
            continue
        if data.get("type") != "tool":
            continue
        state = data.get("state") or {}
        time = state.get("time") or {}
        part = {
            "id": str(row[0]),
            "session": str(row[1]),
            "tool": data.get("tool") if isinstance(data.get("tool"), str) else "?",
            "status": state.get("status") if isinstance(state.get("status"), str) else None,
            "title": state.get("title") if isinstance(state.get("title"), str) else None,
            "input": state.get("input"),
            "output": state.get("output"),
            "metadata": state.get("metadata"),
            "start": _as_int(time.get("start")),
            "end": _as_int(time.get("end")),
        }
        out.setdefault(part["session"], []).append(part)
    return out


def _short_model(model_id: Any) -> str:
    text = str(model_id or "")
    return text.rsplit("/", 1)[-1] if text else "?"


def _load_llm_turns(con: sqlite3.Connection, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Spans de turno LLM por sesión, pareando step-start/step-finish.

    Agrupa por `message_id`, ordena por `(time_created, type, id)` y parea
    FIFO: k-ésimo start con el siguiente finish disponible. Start sin finish →
    `open: true` (fin = `time_updated` del start); finish huérfano → se ignora.
    """
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT id, message_id, session_id, time_created, time_updated, data FROM part "
        f"WHERE session_id IN ({placeholders}) "
        "AND json_extract(data,'$.type') IN ('step-start','step-finish')",
        tuple(sorted(ids)),
    ).fetchall()
    by_msg: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            data = json.loads(row[5])
        except (TypeError, json.JSONDecodeError):
            continue
        typ = data.get("type")
        if typ not in ("step-start", "step-finish"):
            continue
        by_msg.setdefault(str(row[1]), []).append(
            {
                "id": str(row[0]),
                "session": str(row[2]),
                "type": str(typ),
                "tc": _as_int(row[3]),
                "tu": _as_int(row[4]),
                "data": data,
            }
        )
    msg_meta: dict[str, dict[str, Any]] = {}
    msg_ids = sorted(by_msg)
    if msg_ids:
        mph = ",".join("?" * len(msg_ids))
        for row in con.execute(f"SELECT id, data FROM message WHERE id IN ({mph})", tuple(msg_ids)):
            try:
                parsed = json.loads(row[1])
            except (TypeError, json.JSONDecodeError):
                parsed = {}
            msg_meta[str(row[0])] = parsed if isinstance(parsed, dict) else {}
    out: dict[str, list[dict[str, Any]]] = {}
    for message_id, events in by_msg.items():
        events.sort(
            key=lambda e: (e["tc"] if isinstance(e["tc"], int) else _MISSING_START, e["type"], e["id"])
        )
        open_starts: list[dict[str, Any]] = []
        pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        for event in events:
            if event["type"] == "step-start":
                open_starts.append(event)
            elif open_starts:
                pairs.append((open_starts.pop(0), event))
        for start in open_starts:
            pairs.append((start, None))
        meta = msg_meta.get(message_id) or {}
        model = {
            "providerID": meta.get("providerID"),
            "modelID": meta.get("modelID"),
        }
        for k, (start, finish) in enumerate(pairs):
            start_ms = start["tc"] if isinstance(start["tc"], int) else 0
            if finish is not None:
                end_ms = finish["tc"] if isinstance(finish["tc"], int) else start_ms
                open_span = False
            else:
                end_ms = start["tu"] if isinstance(start["tu"], int) else start_ms
                open_span = True
            finish_data = finish["data"] if finish else {}
            tokens = finish_data.get("tokens")
            if not isinstance(tokens, dict):
                tokens = {}
            cache = tokens.get("cache")
            if not isinstance(cache, dict):
                cache = {}
            out.setdefault(start["session"], []).append(
                {
                    "id": f"llm-{message_id}-{k}",
                    "message_id": message_id,
                    "session": start["session"],
                    "start": start_ms,
                    "end": max(start_ms, end_ms),
                    "open": open_span,
                    "reason": finish_data.get("reason") if finish else None,
                    "cost": float(finish_data.get("cost") or 0) if finish else 0.0,
                    "tokens": {
                        "input": _as_int(tokens.get("input")) or 0,
                        "output": _as_int(tokens.get("output")) or 0,
                        "reasoning": _as_int(tokens.get("reasoning")) or 0,
                        "cache_write": _as_int(cache.get("write")) or 0,
                        "cache_read": _as_int(cache.get("read")) or 0,
                    },
                    "model": model,
                    "msg_cost": meta.get("cost"),
                    "msg_tokens": meta.get("tokens"),
                    "variant": meta.get("variant"),
                    "mode": meta.get("mode"),
                }
            )
    for turns in out.values():
        turns.sort(key=lambda t: (t["start"], t["id"]))
    return out


def _closure(sessions: dict[str, dict[str, Any]], anchors: list[str]) -> tuple[set[str], set[str], set[str]]:
    """(descendientes con anclas, ancestros, raíces) del cierre de cada ancla."""
    children: dict[str, list[str]] = {}
    for sid, sess in sessions.items():
        parent = sess.get("parent_id")
        if parent:
            children.setdefault(parent, []).append(sid)
    down: set[str] = set()
    queue = sorted(anchors)
    while queue:
        sid = queue.pop(0)
        if sid in down or sid not in sessions:
            continue
        down.add(sid)
        queue.extend(sorted(children.get(sid, [])))
    ancestors: set[str] = set()
    roots: set[str] = set()
    for anchor in anchors:
        current = sessions.get(anchor, {}).get("parent_id")
        while isinstance(current, str) and current in sessions:
            ancestors.add(current)
            current = sessions[current].get("parent_id")
        top = anchor
        while sessions.get(top, {}).get("parent_id") in sessions:
            parent = sessions[top]["parent_id"]
            if not isinstance(parent, str):
                break
            top = parent
        roots.add(top)
    return down, ancestors, roots


class _Builder:
    def __init__(
        self,
        sessions: dict[str, dict[str, Any]],
        parts: dict[str, list[dict[str, Any]]],
        llm_turns: dict[str, list[dict[str, Any]]],
        anchors: set[str],
        down: set[str],
        closure: set[str],
        win_start: int,
        win_end: int,
        max_bytes: int,
        omit: bool,
    ) -> None:
        self.sessions = sessions
        self.parts = parts
        self.llm_turns = llm_turns
        self.anchors = anchors
        self.down = down
        self.closure = closure
        self.win_start = win_start
        self.win_end = win_end
        self.max_bytes = max_bytes
        self.omit = omit
        self.included_parts: list[dict[str, Any]] = []
        self.included_sessions: list[str] = []
        self.nodes: dict[str, dict[str, Any]] = {}
        self.wall: dict[str, int] = {}
        self.nested_ms: dict[str, int] = {}
        self.invocations: dict[str, int] = {}
        self.delegations: list[dict[str, Any]] = []

    def _ref_node(self, sid: str) -> dict[str, Any]:
        node = dict(self.nodes[sid])
        node["spans"] = []
        node["ref"] = True
        return node

    def _in_window(self, part: dict[str, Any]) -> bool:
        start = part.get("start")
        return isinstance(start, int) and self.win_start <= start <= self.win_end

    def _turn_in_window(self, turn: dict[str, Any]) -> bool:
        start = turn.get("start")
        return isinstance(start, int) and self.win_start <= start <= self.win_end

    def _direct_anchor_task(self, part: dict[str, Any]) -> bool:
        if part.get("tool") != "task":
            return False
        metadata = part.get("metadata") or {}
        child = metadata.get("sessionId") if isinstance(metadata, dict) else None
        return isinstance(child, str) and child in self.anchors

    def session_node(self, sid: str, visited: set[str]) -> dict[str, Any]:
        if sid in self.nodes:
            return self._ref_node(sid)
        sess = self.sessions[sid]
        windowed = sid not in self.down
        tool_parts = self.parts.get(sid, [])
        if windowed:
            selected = [p for p in tool_parts if self._in_window(p) or self._direct_anchor_task(p)]
        else:
            selected = list(tool_parts)
        turns = self.llm_turns.get(sid, [])
        if windowed:
            turns = [t for t in turns if self._turn_in_window(t)]
        # Intercalado cronológico de tool-parts y turnos LLM como hijos directos.
        items: list[tuple[str, dict[str, Any]]] = [("tool", p) for p in selected]
        items += [("llm", t) for t in turns]
        items.sort(
            key=lambda it: (
                it[1].get("start") if isinstance(it[1].get("start"), int) else _MISSING_START,
                str(it[1].get("id")),
            )
        )
        next_visited = visited | {sid}
        spans: list[dict[str, Any]] = []
        for kind, item in items:
            if kind == "llm":
                spans.append(self._llm_span_for(item, sid))
            else:
                spans.append(self._span_for(item, sid, next_visited))
        starts = [it[1]["start"] for it in items if isinstance(it[1].get("start"), int)]
        ends = [it[1]["end"] for it in items if isinstance(it[1].get("end"), int)]
        node_start = min(starts) if starts else sess["time_created"]
        node_end = max(ends) if ends else max(sess["time_updated"], node_start)
        self.wall[sid] = max(0, node_end - node_start)
        duration_ms = max(0, node_end - node_start)
        own_ms = max(0, duration_ms - self.nested_ms.get(sid, 0))
        self.included_sessions.append(sid)
        node: dict[str, Any] = {
            "kind": "session",
            "id": sid,
            "agent": sess["agent"],
            "execution": _execution_from_parts(selected, self.omit),
            "model": sess["model"],
            "title": sess["title"],
            "cost": sess["cost"],
            "tokens": {
                "input": sess["tokens_input"],
                "output": sess["tokens_output"],
                "reasoning": sess["tokens_reasoning"],
                "cache_read": sess["tokens_cache_read"],
                "cache_write": sess["tokens_cache_write"],
            },
            "start": node_start,
            "end": node_end,
            "duration_ms": duration_ms,
            "own_ms": own_ms,
            "windowed": windowed,
            "spans": spans,
        }
        node["errors"] = sum(1 for s in spans if s.get("status") == "error")
        node["status"] = "error" if node["errors"] else "completed"
        self.nodes[sid] = node
        return node

    def _span_for(self, part: dict[str, Any], sid: str, visited: set[str]) -> dict[str, Any]:
        start = part.get("start")
        end = part.get("end")
        duration = end - start if isinstance(start, int) and isinstance(end, int) and end >= start else 0
        span: dict[str, Any] = {
            "kind": "span",
            "id": part["id"],
            "tool": part["tool"],
            "status": part.get("status"),
            "title": None if self.omit else part.get("title"),
            "start": start,
            "end": end,
            "duration_ms": duration,
            "input": _fmt_payload(part.get("input"), self.max_bytes, self.omit),
            "output": _fmt_payload(part.get("output"), self.max_bytes, self.omit),
            "metadata": _fmt_payload(part.get("metadata"), self.max_bytes, self.omit),
        }
        if not self.omit and part["tool"] in ("edit", "write"):
            diff = _unified_diff(str(part["tool"]), part.get("input"))
            if diff is not None:
                span["diff"] = diff
        if part["tool"] == "task":
            raw_input = part.get("input")
            if not isinstance(raw_input, dict):
                raw_input = {}
            span["description"] = raw_input.get("description")
            span["subagent_type"] = raw_input.get("subagent_type")
            metadata = part.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            child = metadata.get("sessionId")
            child_agent = str(span.get("subagent_type") or "?")
            if isinstance(child, str) and child in self.sessions:
                child_agent = str(self.sessions[child].get("agent") or child_agent)
            self.invocations[child_agent] = self.invocations.get(child_agent, 0) + 1
            self.nested_ms[sid] = self.nested_ms.get(sid, 0) + duration
            delegable = isinstance(child, str) and child in self.closure and child in self.sessions
            if delegable:
                self.delegations.append(
                    {
                        "from_session": sid,
                        "from_agent": str(self.sessions.get(sid, {}).get("agent") or "?"),
                        "to_agent": child_agent,
                        "to_session": child,
                        "description": span.get("description"),
                        "t_ms": start,
                        "dur_ms": duration,
                        "status": span.get("status"),
                        "id": part["id"],
                    }
                )
            if delegable and child not in visited:
                span["child"] = self.session_node(child, visited)
            else:
                span["child_missing"] = True
        self.included_parts.append(part)
        return span

    def _llm_span_for(self, turn: dict[str, Any], sid: str) -> dict[str, Any]:
        """Span de turno LLM: tokens/cost/model son agregados (nunca payloads)."""
        start = turn.get("start")
        end = turn.get("end")
        duration = end - start if isinstance(start, int) and isinstance(end, int) and end >= start else 0
        reason = turn.get("reason")
        return {
            "kind": "llm",
            "id": turn["id"],
            "tool": "llm",
            "status": "error" if reason == "error" else None,
            "title": None,
            "start": start,
            "end": end,
            "duration_ms": duration,
            "category": "llm",
            "reason": reason,
            "open": bool(turn.get("open")),
            "cost": float(turn.get("cost") or 0.0),
            "tokens": dict(turn.get("tokens") or {}),
            "model": dict(turn.get("model") or {}),
            "message_id": turn.get("message_id"),
            "session": sid,
            "agent": str(self.sessions.get(sid, {}).get("agent") or "?"),
            "msg_cost": turn.get("msg_cost"),
            "msg_tokens": turn.get("msg_tokens"),
            "variant": turn.get("variant"),
            "mode": turn.get("mode"),
        }

    def roots(self, roots: list[str]) -> list[dict[str, Any]]:
        nodes = [self.session_node(sid, visited=set()) for sid in sorted(roots) if sid in self.sessions]
        nodes.sort(key=lambda n: (n["start"], n["id"]))
        return nodes


def _summary(
    sessions: dict[str, dict[str, Any]],
    builder: _Builder,
    rounds: list[dict[str, Any]],
    total_start: int,
    total_end: int,
) -> dict[str, Any]:
    agent_counts: dict[str, int] = {}
    by_agent: dict[str, dict[str, Any]] = {}
    nested_by_agent: dict[str, int] = {}
    tokens = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0, "total": 0}
    for sid in sorted(set(builder.included_sessions)):
        sess = sessions.get(sid)
        if not sess:
            continue
        agent = sess["agent"] or "?"
        agent_counts[agent] = agent_counts.get(agent, 0) + 1
        entry = by_agent.setdefault(
            agent,
            {
                "invocations": 0,
                "sessions": 0,
                "wall_ms": 0,
                "own_ms": 0,
                "cost": 0.0,
                "tokens": {"input": 0, "output": 0, "reasoning": 0, "total": 0},
                "errors": 0,
                "tool_mix": {},
                "skills_usados": {},
            },
        )
        entry["sessions"] += 1
        entry["wall_ms"] += builder.wall.get(sid, 0)
        nested_by_agent[agent] = nested_by_agent.get(agent, 0) + builder.nested_ms.get(sid, 0)
        entry["cost"] = round(entry["cost"] + sess["cost"], 6)
        subtotal = (
            sess["tokens_input"]
            + sess["tokens_output"]
            + sess["tokens_reasoning"]
            + sess["tokens_cache_read"]
            + sess["tokens_cache_write"]
        )
        entry["tokens"]["input"] += sess["tokens_input"]
        entry["tokens"]["output"] += sess["tokens_output"]
        entry["tokens"]["reasoning"] += sess["tokens_reasoning"]
        entry["tokens"]["total"] += subtotal
        tokens["input"] += sess["tokens_input"]
        tokens["output"] += sess["tokens_output"]
        tokens["reasoning"] += sess["tokens_reasoning"]
        tokens["cache_read"] += sess["tokens_cache_read"]
        tokens["cache_write"] += sess["tokens_cache_write"]
        tokens["total"] += subtotal
    for agent, entry in by_agent.items():
        entry["own_ms"] = max(0, entry["wall_ms"] - nested_by_agent.get(agent, 0))
        entry["invocations"] = builder.invocations.get(agent, 0)
    tools: dict[str, int] = {}
    skills: dict[str, int] = {}
    errors = 0
    for part in sorted(builder.included_parts, key=lambda p: p["id"]):
        agent = sessions.get(part["session"], {}).get("agent") or "?"
        tools[part["tool"]] = tools.get(part["tool"], 0) + 1
        if agent in by_agent:
            mix = by_agent[agent]["tool_mix"]
            mix[part["tool"]] = mix.get(part["tool"], 0) + 1
            if part["tool"] == "skill":
                raw_input = part.get("input")
                name = raw_input.get("name") if isinstance(raw_input, dict) else None
                if isinstance(name, str) and name:
                    used = by_agent[agent]["skills_usados"]
                    used[name] = used.get(name, 0) + 1
                    skills[name] = skills.get(name, 0) + 1
        if part.get("status") == "error":
            errors += 1
            if agent in by_agent:
                by_agent[agent]["errors"] += 1
    parts_by_agent: dict[str, list[dict[str, Any]]] = {}
    for part in builder.included_parts:
        agent = sessions.get(part["session"], {}).get("agent") or "?"
        parts_by_agent.setdefault(agent, []).append(part)
    for agent, entry in by_agent.items():
        entry["execution"] = _execution_from_parts(parts_by_agent.get(agent, []), builder.omit)
    return {
        "duration_ms": max(0, total_end - total_start),
        "agents": agent_counts,
        "by_agent": by_agent,
        "tools": tools,
        "errors": errors,
        "skills": skills,
        "tokens": tokens,
        "rounds": rounds,
        "palette": dict(PALETTE),
    }


def _idle_gap_ms(gaps: list[dict[str, Any]], intervals: list[tuple[int, int]]) -> int:
    """Tiempo de hueco *realmente inactivo* (gaps no cubiertos por ningún span).

    Un hueco de la línea de eventos está totalmente cubierto o no por un span
    (no hay eventos adentro), así que alcanza con el máximo `end` de los spans
    que arrancan antes del hueco.
    """
    if not gaps:
        return 0
    ordered = sorted(intervals)
    starts = [start for start, _ in ordered]
    prefix_end: list[int] = []
    running = -(1 << 62)
    for _, end in ordered:
        running = max(running, end)
        prefix_end.append(running)
    total = 0
    for gap in gaps:
        t0 = gap.get("t0")
        t1 = gap.get("t1")
        if not isinstance(t0, int) or not isinstance(t1, int):
            continue
        idx = bisect.bisect_right(starts, t0) - 1
        if idx >= 0 and prefix_end[idx] >= t1:
            continue
        total += t1 - t0
    return total


def _union_ms(intervals: list[tuple[int, int]]) -> int:
    """Largo de la unión de intervalos (tiempo de pared, sin doble conteo)."""
    if not intervals:
        return 0
    ordered = sorted(intervals)
    total = 0
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += max(0, cur_end - cur_start)
            cur_start, cur_end = start, end
    return total + max(0, cur_end - cur_start)


def _compute_time_split(
    roots: list[dict[str, Any]],
    t0: int,
    t1: int,
    gaps: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, int]]]:
    """`time_split` global + llm/tool/turns por agente.

    `llm_ms`/`tool_ms` son la **unión** de intervalos (tiempo de pared): las
    sesiones paralelas no se suman dos veces, así el % vs activo queda ≤100.
    """
    llm_intervals: list[tuple[int, int]] = []
    tool_intervals: list[tuple[int, int]] = []
    per_agent_iv: dict[str, dict[str, Any]] = {}
    turns = 0
    min_event: int | None = None
    max_event: int | None = None

    def note(value: Any) -> None:
        nonlocal min_event, max_event
        if isinstance(value, int):
            min_event = value if min_event is None else min(min_event, value)
            max_event = value if max_event is None else max(max_event, value)

    def walk(node: dict[str, Any]) -> None:
        nonlocal turns
        note(node.get("start"))
        note(node.get("end"))
        agent = str(node.get("agent") or "?")
        acc = per_agent_iv.setdefault(agent, {"llm": [], "tool": [], "turns": 0})
        for span in node.get("spans", []):
            start = span.get("start")
            end = span.get("end")
            note(start)
            note(end)
            interval = (
                (start, end) if isinstance(start, int) and isinstance(end, int) and end > start else None
            )
            if span.get("kind") == "llm":
                turns += 1
                acc["turns"] += 1
                if interval is not None:
                    llm_intervals.append(interval)
                    acc["llm"].append(interval)
            elif interval is not None:
                tool_intervals.append(interval)
                acc["tool"].append(interval)
            child = span.get("child")
            if isinstance(child, dict) and not child.get("ref"):
                walk(child)

    for root in roots:
        walk(root)
    # `window_ms` = rango real de eventos del árbol (incluye sesiones raíz
    # windowed), no sólo el de las anclas.
    window_ms = (
        max(0, max_event - min_event) if min_event is not None and max_event is not None else max(0, t1 - t0)
    )
    # `gap_ms` = huecos sin actividad real (los internos de un span largo no
    # cuentan como inactividad). `meta.gaps` conserva la compresión visual v5.
    gap_ms = _idle_gap_ms(gaps, llm_intervals + tool_intervals)
    split = {
        "llm_ms": _union_ms(llm_intervals),
        "tool_ms": _union_ms(tool_intervals),
        "gap_ms": gap_ms,
        "window_ms": window_ms,
        "turns": turns,
    }
    per_agent = {
        agent: {
            "llm_ms": _union_ms(iv["llm"]),
            "tool_ms": _union_ms(iv["tool"]),
            "turns": int(iv["turns"]),
        }
        for agent, iv in per_agent_iv.items()
    }
    return split, per_agent


def _build_trace(
    app: str,
    slug: str,
    wt: str,
    db_path: Path,
    anchors: list[str],
    sessions: dict[str, dict[str, Any]],
    parts: dict[str, list[dict[str, Any]]],
    llm_turns: dict[str, list[dict[str, Any]]],
    anchor_starts: dict[str, int],
    matches: list[dict[str, Any]],
    pad_seconds: float,
    max_bytes: int,
    omit: bool,
    generated_at: str,
    max_gap_s: float = DEFAULT_MAX_GAP_SECONDS,
    gap_render_ms: int = DEFAULT_GAP_RENDER_MS,
) -> dict[str, Any] | None:
    down, ancestors, roots = _closure(sessions, anchors)
    closure = down | ancestors | roots
    if not closure:
        return None

    starts: list[int] = []
    for anchor in anchors:
        if anchor in anchor_starts:
            starts.append(anchor_starts[anchor])
            continue
        anchor_parts = [p["start"] for p in parts.get(anchor, []) if isinstance(p["start"], int)]
        if anchor_parts:
            starts.append(min(anchor_parts))
        elif anchor in sessions:
            starts.append(sessions[anchor]["time_created"])
    for sid in sorted(down):
        for part in parts.get(sid, []):
            if isinstance(part["start"], int):
                starts.append(part["start"])
    if not starts:
        return None
    t0 = min(starts)
    ends = [p["end"] for sid in sorted(down) for p in parts.get(sid, []) if isinstance(p["end"], int)]
    t1 = max(ends) if ends else t0
    pad_ms = int(pad_seconds * 1000)
    win_start = t0 - pad_ms
    win_end = t1 + pad_ms

    builder = _Builder(
        sessions, parts, llm_turns, set(anchors), down, closure, win_start, win_end, max_bytes, omit
    )
    root_nodes = builder.roots(sorted(roots))
    root_matches = [m for m in matches if m.get("session") in roots]
    ordered_matches = sorted(
        root_matches,
        key=lambda m: (m["start"] if isinstance(m["start"], int) else _MISSING_START, str(m["id"])),
    )
    rounds = [
        {
            "k": i,
            "t_ms": m["start"],
            "description": m["description"],
            "child_session": m["child"],
        }
        for i, m in enumerate(ordered_matches, start=1)
    ]
    ordered_delegations = sorted(
        builder.delegations,
        key=lambda d: (d["t_ms"] if isinstance(d["t_ms"], int) else _MISSING_START, str(d["id"])),
    )
    delegations = [
        {"k": i, **{key: value for key, value in d.items() if key != "id"}}
        for i, d in enumerate(ordered_delegations, start=1)
    ]
    summary = _summary(sessions, builder, rounds, t0, t1)
    summary["delegations"] = delegations
    k_by = {
        (d["t_ms"], d["to_session"]): d["k"]
        for d in delegations
        if isinstance(d.get("t_ms"), int) and isinstance(d.get("to_session"), str)
    }
    avg_ms_by_tool, row_count = _decorate_rows(root_nodes, summary["palette"], k_by)
    summary["avg_ms_by_tool"] = avg_ms_by_tool
    summary["row_count"] = row_count
    summary["issues"] = summary["errors"]
    mapping, gaps, total_virtual = _virtual_timeline(root_nodes, int(max_gap_s * 1000), gap_render_ms)
    _assign_virtual(root_nodes, mapping)
    time_split, per_agent_time = _compute_time_split(root_nodes, t0, t1, gaps)
    summary["time_split"] = time_split
    for agent, entry in summary["by_agent"].items():
        stats = per_agent_time.get(agent, {"llm_ms": 0, "tool_ms": 0, "turns": 0})
        entry["llm_ms"] = stats["llm_ms"]
        entry["tool_ms"] = stats["tool_ms"]
        entry["turns"] = stats["turns"]
        denom = stats["llm_ms"] + stats["tool_ms"]
        entry["llm_share_pct"] = round(100 * stats["llm_ms"] / denom) if denom > 0 else 0
    return {
        "meta": {
            "app": app,
            "slug": slug,
            "wt": wt,
            "db": str(db_path),
            "generated_at": generated_at,
            "anchors": anchors,
            "gaps": gaps,
            "virtual_total": total_virtual,
            "max_gap_s": max_gap_s,
            "gap_render_ms": gap_render_ms,
            "window": {
                "start": win_start,
                "end": win_end,
                "pad_seconds": pad_seconds,
                "roots_windowed": sorted(sid for sid in roots if sid not in down),
            },
        },
        "roots": root_nodes,
        "summary": summary,
    }


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _exec_summary(execution: dict[str, Any]) -> str:
    by_tool = execution.get("by_tool", {})
    bash = by_tool.get("bash", 0)
    edit = by_tool.get("edit", 0) + by_tool.get("write", 0)
    skills = execution.get("skills") or []
    commands = execution.get("commands")
    bash_txt = f"{bash} bash"
    if commands:
        top = _esc(", ".join(str(c.get("name") or "")[:40] for c in commands[:3]))
        bash_txt += f" ({top}{'…' if len(commands) > 3 else ''})"
    files = execution.get("files")
    edit_txt = f"{edit} edit"
    if files:
        top = _esc(", ".join(str(f.get("name") or "") for f in files[:2]))
        edit_txt += f" ({top}{'…' if len(files) > 2 else ''})"
    skill_names = [str(s.get("name") or "") for s in skills]
    skill_txt = f"{len(skills)} skills"
    if skill_names:
        skill_txt += f" ({_esc(', '.join(skill_names[:3]))})"
    return " · ".join([bash_txt, edit_txt, skill_txt])


OWN_SIN_DELEGACIONES = "sin delegaciones propias"


def _own_cell(v: dict[str, Any]) -> str:
    """Celda own: `—` si own==wall (el agente no delega), numérico si difieren."""
    if v["own_ms"] == v["wall_ms"]:
        return f'<span title="{OWN_SIN_DELEGACIONES}">—</span>'
    return _fmt_ms(v["own_ms"])


def _llm_tool_bar(agent: str, v: dict[str, Any], palette: dict[str, str]) -> str:
    """Mini-barra stacked LLM (gris rayado) vs tools (color del agente) + texto."""
    llm = float(v.get("llm_ms") or 0)
    tools = float(v.get("tool_ms") or 0)
    total = llm + tools
    if total <= 0:
        return '<span class="muted">—</span>'
    width = 120
    llm_w = max(0, round(width * llm / total))
    tool_w = width - llm_w
    agent_color = palette[_role_token(agent)]
    segments: list[str] = []
    if llm_w:
        segments.append(f'<rect x="0" y="0" width="{llm_w}" height="14" fill="{palette["llm"]}"></rect>')
        segments.append(f'<rect x="0" y="0" width="{llm_w}" height="14" fill="url(#trace-hatch)"></rect>')
    if tool_w:
        segments.append(f'<rect x="{llm_w}" y="0" width="{tool_w}" height="14" fill="{agent_color}"></rect>')
    pct = round(100 * llm / total)
    return (
        f'<svg class="barcell" width="{width}" height="14" aria-hidden="true">{"".join(segments)}</svg>'
        f'<span class="bv">{pct}% · {_esc(_fmt_ms(llm))}</span>'
    )


def _render_perf(summary: dict[str, Any], palette: dict[str, str]) -> str:
    by_agent = summary["by_agent"]
    agents = sorted(by_agent)
    rows: list[str] = []
    for agent in agents:
        v = by_agent[agent]
        tokens = v["tokens"]
        err = (
            f'<span class="st err">✕ {v["errors"]}</span>'
            if v["errors"]
            else '<span class="st ok">✓ 0</span>'
        )
        own = _own_cell(v)
        llm_bar = _llm_tool_bar(agent, v, palette)
        rows.append(
            "<tr>"
            f'<th scope="row">{_esc(agent)}</th>'
            f"<td>{v['invocations']}</td>"
            f"<td>{v['sessions']}</td>"
            f"<td>{_fmt_ms(v['wall_ms'])}</td>"
            f"<td>{own}</td>"
            f"<td>${v['cost']:.4f}</td>"
            f"<td>{tokens['input']} / {tokens['output']} / {tokens['reasoning']}</td>"
            f'<td class="exec">{_exec_summary(v.get("execution", {}))}</td>'
            f'<td class="llmcell">{llm_bar}</td>'
            f"<td>{err}</td>"
            "</tr>"
        )
    table = (
        '<table class="perf"><caption>Rendimiento por agente '
        "(wall = duración de sus sesiones; own = wall menos el tiempo esperando hijos)</caption>"
        '<thead><tr><th scope="col">agente</th><th scope="col">invocaciones</th>'
        '<th scope="col">sesiones</th><th scope="col">wall</th><th scope="col">own</th>'
        '<th scope="col">costo</th><th scope="col">tokens (in/out/rea)</th>'
        '<th scope="col">Qué ejecutó</th>'
        '<th scope="col">Tiempo LLM vs tools</th>'
        '<th scope="col">errores</th></tr></thead><tbody>' + "\n".join(rows) + "</tbody></table>"
    )
    own_bars = _metric_bars(
        by_agent, "own_ms", "Tiempo propio (own_ms)", palette, lambda x: _fmt_ms(x), equal_key="wall_ms"
    )
    cost_bars = _metric_bars(by_agent, "cost", "Costo (USD)", palette, lambda x: f"${x:.4f}", "ok")
    mix = _tool_mix_chart(by_agent, palette)
    return f'{_pattern_defs()}{table}<div class="charts">{own_bars}{cost_bars}</div>{mix}'


def _metric_bars(
    by_agent: dict[str, dict[str, Any]],
    key: str,
    title: str,
    palette: dict[str, str],
    fmt: Any,
    color_token: str = "",
    equal_key: str = "",
) -> str:
    agents = sorted(by_agent)
    values = [float(by_agent[a].get(key, 0) or 0) for a in agents]
    top = max(values) if values else 0.0
    bars: list[str] = []
    for agent in agents:
        value = float(by_agent[agent].get(key, 0) or 0)
        dup = bool(equal_key) and value == float(by_agent[agent].get(equal_key, 0) or 0)
        if dup:
            rect = ""
            bv = f'<span class="bv" title="{OWN_SIN_DELEGACIONES}">—</span>'
        else:
            width = 0 if top <= 0 else max(0, round(180 * value / top))
            color = palette[color_token] if color_token else palette[_role_token(agent)]
            rect = f'<rect width="{width}" height="14" rx="2" fill="{color}"></rect>'
            bv = f'<span class="bv">{_esc(fmt(value))}</span>'
        bars.append(
            f'<li><span class="bl">{_esc(agent)}</span>'
            f'<svg class="barcell" width="180" height="14" aria-hidden="true">{rect}</svg>'
            f"{bv}</li>"
        )
    return f'<div class="metric"><h3>{_esc(title)}</h3><ul class="bars">{"".join(bars)}</ul></div>'


def _tool_order(by_agent: dict[str, dict[str, Any]]) -> list[str]:
    totals: dict[str, int] = {}
    for v in by_agent.values():
        for tool, count in v.get("tool_mix", {}).items():
            totals[tool] = totals.get(tool, 0) + count
    return sorted(totals, key=lambda t: (-totals[t], t))


def _pattern_defs() -> str:
    return (
        '<svg class="defs" width="0" height="0" aria-hidden="true" focusable="false"><defs>'
        '<pattern id="trace-hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)">'
        '<line x1="0" y1="0" x2="0" y2="6" stroke="#1A1A1A" stroke-opacity="0.5" stroke-width="2"/>'
        "</pattern>"
        '<pattern id="trace-dot" width="6" height="6" patternUnits="userSpaceOnUse">'
        '<circle cx="2" cy="2" r="1.2" fill="#1A1A1A" fill-opacity="0.55"/>'
        "</pattern>"
        "</defs></svg>"
    )


def _pattern_fill(kind: str) -> str:
    if kind == "hatch":
        return "#trace-hatch"
    if kind == "dot":
        return "#trace-dot"
    return ""


def _tool_mix_chart(by_agent: dict[str, dict[str, Any]], palette: dict[str, str]) -> str:
    order = _tool_order(by_agent)
    if not order:
        return '<div class="metric"><h3>Mix de herramientas</h3><p>sin herramientas</p></div>'
    style = {tool: GRAPHIC_TOKENS[i % len(GRAPHIC_TOKENS)] for i, tool in enumerate(order)}
    kind = {tool: PATTERN_KINDS[i % len(PATTERN_KINDS)] for i, tool in enumerate(order)}
    charts: list[str] = []
    for agent in sorted(by_agent):
        mix = by_agent[agent].get("tool_mix", {})
        total = sum(mix.values())
        segments: list[str] = []
        x = 0.0
        for tool in order:
            count = mix.get(tool, 0)
            if not count:
                continue
            width = round(240 * count / total) if total else 0
            color = palette[style[tool]]
            segments.append(f'<rect x="{x:.0f}" y="0" width="{width}" height="16" fill="{color}"></rect>')
            fill = _pattern_fill(kind[tool])
            if fill:
                segments.append(
                    f'<rect x="{x:.0f}" y="0" width="{width}" height="16" fill="url({fill})"></rect>'
                )
            x += width
        charts.append(
            f'<li><span class="bl">{_esc(agent)}</span>'
            f'<svg width="240" height="16" aria-hidden="true">{"".join(segments)}</svg>'
            f'<span class="bv">{total} usos</span></li>'
        )
    legend: list[str] = []
    for tool in order:
        fill = _pattern_fill(kind[tool])
        overlay = f'<rect width="18" height="12" fill="url({fill})"></rect>' if fill else ""
        legend.append(
            f'<li><svg width="18" height="12" aria-hidden="true">'
            f'<rect width="18" height="12" fill="{palette[style[tool]]}"></rect>{overlay}</svg>'
            f'<span>{_esc(tool)}</span><span class="muted">patrón {PATTERN_LABELS[kind[tool]]}</span></li>'
        )
    return (
        '<div class="metric"><h3>Mix de herramientas por agente</h3><ul class="bars">'
        + "".join(charts)
        + '</ul><ul class="legend">'
        + "".join(legend)
        + "</ul></div>"
    )


def _graph_data(
    roots: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    info: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}

    def visit(node: dict[str, Any], depth: int) -> None:
        sid = node["id"]
        if sid in info:
            return
        info[sid] = {"agent": node.get("agent"), "depth": depth, "start": node.get("start")}
        for span in node.get("spans", []):
            child = span.get("child")
            if span.get("tool") != "task" or not isinstance(child, dict):
                continue
            key = (sid, child["id"])
            edge = edges.setdefault(
                key, {"parent": sid, "child": child["id"], "count": 0, "agent": child.get("agent")}
            )
            edge["count"] += 1
            visit(child, depth + 1)

    for root in roots:
        visit(root, 0)
    return info, edges


def _svg_graph(roots: list[dict[str, Any]], palette: dict[str, str]) -> str:
    info, edges = _graph_data(roots)
    layers: dict[int, list[str]] = {}
    for sid, meta in info.items():
        layers.setdefault(meta["depth"], []).append(sid)
    for depth in layers:
        layers[depth].sort(key=lambda s: (info[s]["start"] or 0, s))
    max_depth = max(layers) if layers else 0
    margin, colw, rowh, node_w, node_h = 20, 190, 60, 150, 32
    positions: dict[str, tuple[int, int]] = {}
    for depth, sids in layers.items():
        for i, sid in enumerate(sids):
            positions[sid] = (margin + depth * colw, margin + i * rowh)
    width = margin * 2 + (max_depth + 1) * colw
    height = margin * 2 + (max(len(s) for s in layers.values()) - 1) * rowh + node_h if layers else 60
    parts = [
        f'<svg role="img" class="graph" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<title>Grafo de delegación de la corrida</title>",
        "<desc>Nodos por profundidad de delegación con el agente y el id de sesión; "
        "las aristas indican cuántas veces se invocó a la sesión hija.</desc>",
        '<defs><marker id="trace-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" '
        'orient="auto"><path d="M0,0 L7,3 L0,6 z" fill="#1A1A1A"></path></marker></defs>',
    ]
    for edge in sorted(edges.values(), key=lambda e: (e["parent"], e["child"])):
        if edge["parent"] not in positions or edge["child"] not in positions:
            continue
        px, py = positions[edge["parent"]]
        cx, cy = positions[edge["child"]]
        x1, y1 = px + node_w, py + node_h // 2
        x2, y2 = cx, cy + node_h // 2
        color = palette[_role_token(edge["agent"])]
        parts.append(
            f'<path d="M{x1},{y1} C{(x1 + x2) // 2},{y1} {(x1 + x2) // 2},{y2} {x2},{y2}" '
            f'fill="none" stroke="{color}" stroke-width="2" marker-end="url(#trace-arrow)"></path>'
        )
        label = f"→ {_esc(edge['agent'] or '?')}" + (f" \u00d7{edge['count']}" if edge["count"] > 1 else "")
        parts.append(
            f'<text x="{(x1 + x2) // 2}" y="{(y1 + y2) // 2 - 4}" text-anchor="middle" '
            f'font-size="11" fill="#1A1A1A">{label}</text>'
        )
    for sid, meta in sorted(info.items(), key=lambda kv: (kv[1]["depth"], kv[1]["start"] or 0, kv[0])):
        x, y = positions[sid]
        color = palette[_role_token(meta["agent"])]
        parts.append(
            f'<rect x="{x}" y="{y}" width="{node_w}" height="{node_h}" rx="6" fill="#F6F6F6" '
            f'stroke="{color}" stroke-width="2"></rect>'
        )
        parts.append(
            f'<text x="{x + node_w // 2}" y="{y + node_h // 2 + 4}" text-anchor="middle" '
            f'font-size="12" fill="#1A1A1A">{_esc(meta["agent"] or "?")} ({_esc(_short_id(sid))})</text>'
        )
    parts.append("</svg>")
    text_lines = [
        f"<li>nodo: {_esc(meta['agent'] or '?')} ({_esc(_short_id(sid))}) — profundidad {meta['depth']}</li>"
        for sid, meta in sorted(info.items(), key=lambda kv: (kv[1]["depth"], kv[1]["start"] or 0, kv[0]))
    ]
    text_lines += [
        f"<li>delegación: {_esc(info.get(e['parent'], {}).get('agent') or '?')} → "
        f"{_esc(e['agent'] or '?')}" + (f" \u00d7{e['count']}" if e["count"] > 1 else "") + "</li>"
        for e in sorted(edges.values(), key=lambda e: (e["parent"], e["child"]))
    ]
    text = _details("Equivalente en texto del grafo", "<ul>" + "".join(text_lines) + "</ul>")
    return '<div class="diagram-block"><h3>Grafo de delegación</h3>' + "".join(parts) + text + "</div>"


def _collect_sessions(roots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    info: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def visit(node: dict[str, Any], depth: int) -> None:
        sid = node["id"]
        if sid in info:
            return
        info[sid] = {
            "id": sid,
            "agent": node.get("agent"),
            "depth": depth,
            "start": node.get("start") or 0,
            "end": node.get("end") or 0,
            "execution": node.get("execution") or {},
        }
        order.append(sid)
        for span in node.get("spans", []):
            child = span.get("child")
            if isinstance(child, dict):
                visit(child, depth + 1)

    for root in roots:
        visit(root, 0)
    return [info[sid] for sid in order]


def _svg_swimlanes(
    roots: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    delegations: list[dict[str, Any]],
    palette: dict[str, str],
) -> str:
    sessions = sorted(_collect_sessions(roots), key=lambda s: (s["start"], s["id"]))
    if not sessions:
        return '<div class="diagram-block"><h3>Swimlanes</h3><p>sin sesiones</p></div>'
    by_id = {s["id"]: s for s in sessions}
    t_min = min(s["start"] for s in sessions)
    t_max = max(s["end"] for s in sessions)
    left, right, top, rowh = 210, 14, 34, 24
    width = 1000
    height = top + len(sessions) * rowh + 44
    track = width - left - right
    scale = track / max(1, t_max - t_min)
    lane_y: dict[str, float] = {s["id"]: top + i * rowh for i, s in enumerate(sessions)}
    parts = [
        f'<svg role="img" class="swim" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<title>Swimlanes de sesiones, delegaciones y rondas</title>",
        "<desc>Una fila por sesión con su barra temporal, una flecha por delegación "
        "(etiquetada Rk → agente), las rondas como líneas punteadas y una escala en minutos.</desc>",
        '<defs><marker id="trace-swim-arrow" markerWidth="7" markerHeight="7" refX="6" refY="2.5" '
        'orient="auto"><path d="M0,0 L6,2.5 L0,5 z" fill="#1A1A1A"></path></marker></defs>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height - 30}" stroke="#1A1A1A" '
        'stroke-opacity="0.25"></line>',
    ]
    for s in sessions:
        y = lane_y[s["id"]]
        color = palette[_role_token(s["agent"])]
        indent = 8 + s["depth"] * 14
        parts.append(
            f'<text x="{indent}" y="{y + 15}" font-size="12" fill="#1A1A1A">'
            f"{_esc(s['agent'] or '?')} ({_esc(_short_id(s['id']))})</text>"
        )
        bx = left + (s["start"] - t_min) * scale
        bw = max(2.0, (s["end"] - s["start"]) * scale)
        parts.append(
            f'<rect x="{bx:.1f}" y="{y + 3}" width="{bw:.1f}" height="14" rx="2" fill="{color}"></rect>'
        )
    for rnd in rounds:
        t = rnd.get("t_ms")
        if not isinstance(t, int):
            continue
        x = left + (t - t_min) * scale
        parts.append(
            f'<line x1="{x:.1f}" y1="{top - 4}" x2="{x:.1f}" y2="{height - 30}" stroke="#005F9E" '
            'stroke-width="1.5" stroke-dasharray="4 3"></line>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{top - 10}" text-anchor="middle" font-size="11" fill="#005F9E">'
            f"R{rnd['k']}</text>"
        )
    for d in delegations:
        from_id = d.get("from_session")
        to_id = d.get("to_session")
        if not isinstance(from_id, str) or not isinstance(to_id, str):
            continue
        if from_id not in lane_y or to_id not in lane_y:
            continue
        t = d.get("t_ms")
        x1 = left + ((t - t_min) * scale if isinstance(t, int) else 0.0)
        y1 = lane_y[from_id] + rowh / 2
        y2 = lane_y[to_id] + rowh / 2
        x2 = left + (by_id[to_id]["start"] - t_min) * scale
        color = palette[_role_token(d.get("to_agent"))]
        mid = (x1 + x2) / 2
        parts.append(
            f'<path data-arrow="1" aria-hidden="true" d="M{x1:.1f},{y1:.1f} '
            f'C{mid:.1f},{y1:.1f} {mid:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" fill="none" '
            f'stroke="{color}" stroke-width="1.5" stroke-dasharray="3 2" '
            'marker-end="url(#trace-swim-arrow)"></path>'
        )
        desc = str(d.get("description") or "")[:40]
        label = f"R{int(d.get('k') or 0)} → {d.get('to_agent') or '?'}" + (f": {desc}" if desc else "")
        label_y = (y1 + y2) / 2 - 3 + ((int(d.get("k") or 0) % 3) - 1) * 9
        parts.append(
            f'<text x="{x1:.1f}" y="{label_y:.1f}" font-size="10" fill="#1A1A1A" '
            f'aria-hidden="true">{_esc(label)}</text>'
        )
    total_min = (t_max - t_min) / 60000.0
    step_min = 1 if total_min <= 10 else 5 if total_min <= 60 else 10 if total_min <= 180 else 30
    ticks = int(total_min // step_min) + 1
    axis_y = height - 26
    parts.append(
        f'<line x1="{left}" y1="{axis_y}" x2="{width - right}" y2="{axis_y}" stroke="#1A1A1A" '
        'stroke-opacity="0.4"></line>'
    )
    for t in range(ticks + 1):
        minutes = t * step_min
        x = left + (minutes * 60000) * scale
        if x > width - right + 1:
            break
        parts.append(
            f'<line x1="{x:.1f}" y1="{axis_y}" x2="{x:.1f}" y2="{axis_y + 5}" '
            'stroke="#1A1A1A" stroke-opacity="0.5"></line>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{axis_y + 18}" text-anchor="middle" font-size="10" '
            f'fill="#1A1A1A">{minutes}m</text>'
        )
    parts.append("</svg>")
    legend_roles = []
    for token in GRAPHIC_TOKENS:
        if any(_role_token(s["agent"]) == token for s in sessions):
            legend_roles.append(
                f'<li><span class="swatch" style="background:{palette[token]}"></span>{_esc(token)}</li>'
            )
    legend = '<ul class="legend">' + "".join(legend_roles) + "</ul>"
    rows = "".join(
        f"<tr><td>{_esc(s['agent'] or '?')} ({_esc(_short_id(s['id']))})</td>"
        f"<td>{s['start']}</td><td>{s['end']}</td><td>{_fmt_ms(s['end'] - s['start'])}</td></tr>"
        for s in sessions
    )
    text = _details(
        "Equivalente en texto de los swimlanes",
        "<table><caption>Sesión, inicio, fin y duración (epoch ms)</caption>"
        '<thead><tr><th scope="col">sesión</th><th scope="col">inicio</th>'
        '<th scope="col">fin</th><th scope="col">duración</th></tr></thead><tbody>'
        + rows
        + "</tbody></table>",
    )
    lane_exec = "".join(
        f"<li><b>{_esc(s['agent'] or '?')} ({_esc(_short_id(s['id']))})</b> — ejecutó: "
        f"{_exec_summary(s.get('execution') or {})}</li>"
        for s in sessions
    )
    return (
        '<div class="diagram-block"><h3>Swimlanes (sesiones, delegaciones y rondas)</h3>'
        + "".join(parts)
        + legend
        + f'<ul class="lane-exec">{lane_exec}</ul>'
        + _narrativa(delegations, by_id)
        + text
        + "</div>"
    )


def _narrativa(delegations: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> str:
    lines: list[str] = []
    for d in delegations:
        to_id = d.get("to_session")
        execution = by_id.get(to_id, {}).get("execution") or {} if isinstance(to_id, str) else {}
        desc = str(d.get("description") or "")
        line = (
            f"<li>R{int(d.get('k') or 0)} · {_esc(str(d.get('from_agent') or '?'))} → "
            f'{_esc(str(d.get("to_agent") or "?"))} "{_esc(desc)}" · {_fmt_ms(d.get("dur_ms"))} · '
            f"{_status_glyph(d.get('status'))} · ejecutó: {_exec_summary(execution)}</li>"
        )
        lines.append(line)
    return (
        f'<details class="narrative" open><summary>Narrativa de delegación '
        f"({len(delegations)})</summary><ol>" + "".join(lines) + "</ol></details>"
    )


def _details(summary: str, body: str) -> str:
    return f'<details class="equivalent"><summary>{_esc(summary)}</summary>{body}</details>'


def _status_glyph(status: Any) -> str:
    if status == "error":
        return '<span class="st err" aria-label="error">✕</span>'
    if status == "completed":
        return '<span class="st ok" aria-label="ok">✓</span>'
    return f'<span class="st muted">{_esc(str(status or ""))}</span>'


def _bar_pct(start: Any, end: Any, window: dict[str, Any]) -> tuple[float, float] | None:
    ws = window.get("start")
    we = window.get("end")
    if not isinstance(ws, int) or not isinstance(we, int) or we <= ws or not isinstance(start, int):
        return None
    finish = end if isinstance(end, int) else start
    left = max(0.0, (start - ws) * 100.0 / (we - ws))
    width = max(0.4, (finish - start) * 100.0 / (we - ws))
    return left, min(width, max(0.0, 100.0 - left))


def _track(start: Any, end: Any, window: dict[str, Any], cls: str) -> str:
    pct = _bar_pct(start, end, window)
    if pct is None:
        return ""
    return (
        f'<span class="track"><span class="bar {cls}" '
        f'style="left:{pct[0]:.2f}%;width:{pct[1]:.2f}%"></span></span>'
    )


def _bar_pct_v(v_start: Any, v_end: Any, total: int) -> tuple[float, float] | None:
    if not isinstance(v_start, int) or total <= 0:
        return None
    finish = v_end if isinstance(v_end, int) else v_start
    left = max(0.0, v_start * 100.0 / total)
    width = max(0.4, (finish - v_start) * 100.0 / total)
    return left, min(width, max(0.0, 100.0 - left))


def _track_v(v_start: Any, v_end: Any, total: int, cls: str) -> str:
    pct = _bar_pct_v(v_start, v_end, total)
    if pct is None:
        return ""
    return (
        f'<span class="track"><span class="bar {cls}" '
        f'style="left:{pct[0]:.2f}%;width:{pct[1]:.2f}%"></span></span>'
    )


def _span_bar_cls(tool: str, status: Any) -> str:
    if status == "error":
        return "bar-error"
    if tool == "llm":
        return "bar-llm"
    if tool in ("edit", "write"):
        return "bar-write"
    if tool == "task":
        return "bar-task"
    if tool == "bash":
        return "bar-bash"
    return "bar-tool"


def _llm_turn_payload(span: dict[str, Any]) -> str:
    model_raw = span.get("model")
    model: dict[str, Any] = model_raw if isinstance(model_raw, dict) else {}
    tokens_raw = span.get("tokens")
    tokens: dict[str, Any] = tokens_raw if isinstance(tokens_raw, dict) else {}
    lines = [
        f"model={model.get('modelID') or ''} provider={model.get('providerID') or ''}",
        f"reason={span.get('reason') or ''} open={'sí' if span.get('open') else 'no'}",
        (
            f"tokens in/out/rea={tokens.get('input', 0)}/{tokens.get('output', 0)}/"
            f"{tokens.get('reasoning', 0)} cache r/w={tokens.get('cache_read', 0)}/"
            f"{tokens.get('cache_write', 0)}"
        ),
        f"cost=${float(span.get('cost') or 0):.6f} message_id={span.get('message_id') or ''}",
    ]
    return '<div class="sec">turno</div><pre>' + _esc("\n".join(lines)) + "</pre>"


def _span_block(span: dict[str, Any], palette: dict[str, str], vtotal: int) -> str:
    tool = str(span.get("tool") or "?")
    if span.get("kind") == "llm":
        name_html = _esc(_span_label(span))
        cls = "k-llm"
    elif tool == "task":
        child = span.get("child") if isinstance(span.get("child"), dict) else None
        to_agent = (child.get("agent") if child else None) or span.get("subagent_type") or "task"
        dur = child.get("duration_ms") if child else span.get("duration_ms")
        if isinstance(child, dict):
            badge = (
                f'<a class="jump" href="#sesion-{_esc(str(child["id"]))}">'
                f"→ {_esc(str(to_agent))} ({_fmt_ms(dur)})</a>"
            )
        else:
            badge = f'<span class="jump">→ {_esc(str(to_agent))} ({_fmt_ms(dur)})</span>'
        name_html = f"{badge} — {_esc(str(span.get('description') or ''))}"
        cls = "k-task"
    else:
        title = str(span.get("title") or "")
        name_html = _esc(f"{tool} {title}".strip())
        cls = "k-tool"
    track = _track_v(span.get("v_start"), span.get("v_end"), vtotal, _span_bar_cls(tool, span.get("status")))
    if span.get("kind") == "llm":
        payload = _llm_turn_payload(span)
    else:
        payload = (
            f'<div class="sec">input</div><pre>{_esc(span.get("input") or "")}</pre>'
            f'<div class="sec">output</div><pre>{_esc(span.get("output") or "")}</pre>'
        )
        if span.get("metadata"):
            payload += f'<div class="sec">metadata</div><pre>{_esc(span.get("metadata") or "")}</pre>'
        if span.get("diff"):
            payload += '<div class="sec">diff</div>' + _diff_html(span["diff"])
    self_err = int(span.get("self_errors") or 0)
    err_badge = f'<span class="err-badge" title="{self_err} error(es)">⚠{self_err}</span>' if self_err else ""
    return (
        f'<details class="span {cls}"><summary>{_status_glyph(span.get("status"))}'
        f'<span class="sname">{name_html}</span>{err_badge}{track}'
        f'<span class="tag">{_fmt_ms(span.get("duration_ms"))}</span></summary>'
        f"{payload}</details>"
    )


def _sess_block(node: dict[str, Any], depth: int, palette: dict[str, str], vtotal: int) -> str:
    spans = node.get("spans", [])
    errs = int(node.get("self_errors") or 0)
    tokens = node.get("tokens", {})
    role = _role_token(node.get("agent"))
    track = _track_v(node.get("v_start"), node.get("v_end"), vtotal, f"bar-{role}")
    badges = (
        f'<span class="badge" style="border-color:{palette[role]}">{_esc(node.get("agent") or "?")}</span>'
        f'<span class="muted">{_esc(_short_id(node["id"]))}</span>{track}'
        f'<span class="tag">{_fmt_ms(node.get("duration_ms"))}</span>'
        f'<span class="tag">${node.get("cost", 0):.4f}</span>'
        f'<span class="tag">tok {tokens.get("input", 0)}/{tokens.get("output", 0)}</span>'
    )
    if node.get("windowed"):
        badges += '<span class="tag win-tag">windowed</span>'
    if errs:
        badges += f'<span class="err-badge" title="{errs} error(es)">⚠{errs}</span>'
    else:
        badges += '<span class="st ok" aria-label="sin errores">✓</span>'
    parts = "".join(_span_block(s, palette, vtotal) for s in spans)
    children = "".join(
        _sess_block(s["child"], depth + 1, palette, vtotal)
        for s in spans
        if isinstance(s.get("child"), dict) and not s["child"].get("ref")
    )
    return (
        f'<div class="sess" id="sesion-{_esc(str(node["id"]))}" style="margin-left:{depth * 16}px">'
        f'<div class="sess-row"><span class="dot" style="background:{palette[role]}"></span>{badges}</div>'
        f'<details class="sess-parts"><summary>{len(spans)} herramientas</summary>{parts}</details>'
        f"</div>{children}"
    )


def _flat_rows(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Filas de la vista Traza en orden de árbol (pre-order), con datos de layout."""
    out: list[dict[str, Any]] = []

    def emit_session(node: dict[str, Any], depth: int, parent_id: str) -> None:
        row_id = str(node.get("id") or "")
        out.append(
            {
                "id": row_id,
                "depth": depth,
                "parent": parent_id,
                "category": "sesion",
                "color": str(node.get("color") or "#2E86AB"),
                "status": node.get("status"),
                "label": str(node.get("label") or ""),
                "v_start": node.get("v_start"),
                "v_end": node.get("v_end"),
                "dur_text": str(node.get("dur_text") or ""),
                "has_children": bool(node.get("spans")),
                "self_errors": int(node.get("self_errors") or 0),
            }
        )
        for span in node.get("spans", []):
            emit_span(span, depth + 1, row_id)

    def emit_span(span: dict[str, Any], depth: int, parent_id: str) -> None:
        child = span.get("child") if isinstance(span.get("child"), dict) else None
        has_children = bool(child) and not child.get("ref")
        row_id = str(span.get("id") or "")
        out.append(
            {
                "id": row_id,
                "depth": depth,
                "parent": parent_id,
                "category": str(span.get("category") or "otros"),
                "color": str(span.get("color") or "#2E86AB"),
                "status": span.get("status"),
                "label": str(span.get("label") or ""),
                "v_start": span.get("v_start"),
                "v_end": span.get("v_end"),
                "dur_text": str(span.get("dur_text") or ""),
                "has_children": has_children,
                "self_errors": int(span.get("self_errors") or 0),
                "diff": span.get("diff") or [],
            }
        )
        if child and has_children:
            emit_session(child, depth + 1, row_id)

    for root in trace["roots"]:
        emit_session(root, 0, "")
    return out


def _trace_row(row: dict[str, Any], total: int) -> str:
    depth = int(row["depth"])
    row_id = str(row["id"])
    has_children = bool(row["has_children"])
    guides = '<span class="guides">' + "<i></i>" * depth + "</span>"
    if has_children:
        chev = '<span class="chev" role="button" tabindex="0" aria-expanded="true">▾</span>'
    else:
        chev = '<span class="chev empty" aria-hidden="true"></span>'
    pct = _bar_pct_v(row.get("v_start"), row.get("v_end"), total)
    bar = ""
    if pct is not None:
        bar = f'<span class="rbar" style="left:{pct[0]:.2f}%;width:{pct[1]:.2f}%"></span>'
    self_err = int(row["self_errors"])
    err_badge = f'<span class="err-badge" title="{self_err} error(es)">⚠{self_err}</span>' if self_err else ""
    return (
        f'<a class="row cat-{_esc(str(row["category"]))}" id="span-{_esc(row_id)}"'
        f' href="#span-{_esc(row_id)}"'
        f' data-span-id="{_esc(row_id)}" data-depth="{depth}"'
        f' data-parent="{_esc(str(row["parent"]))}"'
        f' data-category="{_esc(str(row["category"]))}"'
        f' data-label="{_esc(str(row["label"]))}"'
        f' data-v-start="{row.get("v_start") if isinstance(row.get("v_start"), int) else 0}"'
        f' data-v-end="{row.get("v_end") if isinstance(row.get("v_end"), int) else 0}"'
        f' data-self-errors="{self_err}"'
        f' data-has-children="{"1" if has_children else "0"}">'
        f"{guides}{chev}"
        f'<span class="cchip" style="background:{_esc(str(row["color"]))}"></span>'
        f"{_status_glyph(row.get('status'))}{err_badge}"
        f'<span class="rlabel">{_esc(str(row["label"]))}</span>'
        f'<span class="rtrack">{bar}</span>'
        f'<span class="rdur">{_esc(str(row["dur_text"]))}</span>'
        "</a>"
    )


def _gap_row(gap: dict[str, Any], total: int) -> str:
    virtual_at = int(gap.get("virtual_at") or 0)
    pct = (virtual_at * 100.0 / total) if total > 0 else 0.0
    left = 42.0 + 0.58 * pct
    return (
        '<div class="gap-row" data-gap="1">'
        f'<span class="glabel" style="left:{left:.2f}%">{_esc(str(gap.get("label") or ""))}</span>'
        "</div>"
    )


def _render_minimap(rows: list[dict[str, Any]], total: int) -> str:
    if not rows or total <= 0:
        return ""
    height = 120
    width = 1000
    count = len(rows)
    slot = height / count
    bars: list[str] = []
    for i, row in enumerate(rows):
        v_start = row.get("v_start")
        v_end = row.get("v_end")
        if not isinstance(v_start, int):
            continue
        finish = v_end if isinstance(v_end, int) else v_start
        x = v_start * width / total
        w = max(1.0, (finish - v_start) * width / total)
        y = i * slot
        h = max(0.6, slot)
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.2f}" width="{w:.1f}" height="{h:.2f}"'
            f' fill="{_esc(str(row["color"]))}"></rect>'
        )
    return (
        f'<svg id="mm" class="minimap" width="100%" height="{height}" viewBox="0 0 {width} {height}"'
        ' preserveAspectRatio="none" aria-label="Minimapa de la corrida">'
        + "".join(bars)
        + f'<rect id="mm-viewport" class="mm-vp" x="0" y="0" width="{width}" height="{height}"></rect>'
        "</svg>"
    )


def _render_traza_js(trace: dict[str, Any]) -> str:
    total = int((trace.get("meta") or {}).get("virtual_total") or 0)
    rows = _flat_rows(trace)
    note = ""
    if len(rows) > MAX_TRACE_ROWS:
        note = (
            f'<div class="note">… {len(rows) - MAX_TRACE_ROWS} filas omitidas '
            f"(row_count {len(rows)} &gt; {MAX_TRACE_ROWS})</div>"
        )
        rows = rows[:MAX_TRACE_ROWS]
    entries: list[tuple[int, str]] = [(int(row.get("v_start") or 0), _trace_row(row, total)) for row in rows]
    gaps = (trace.get("meta") or {}).get("gaps") or []
    gap_entries: list[tuple[int, str]] = [
        (int(gap.get("virtual_at") or 0), _gap_row(gap, total)) for gap in gaps if total > 0
    ]
    merged: list[str] = []
    gi = 0
    for v_start, html in entries:
        while gi < len(gap_entries) and gap_entries[gi][0] <= v_start:
            merged.append(gap_entries[gi][1])
            gi += 1
        merged.append(html)
    while gi < len(gap_entries):
        merged.append(gap_entries[gi][1])
        gi += 1
    templates = "".join(
        _diff_html([str(line) for line in row["diff"]], str(row["id"])) for row in rows if row.get("diff")
    )
    template_block = f'<div id="diff-templates" hidden>{templates}</div>' if templates else ""
    return (
        _render_minimap(rows, total)
        + '<div id="trace-rows" class="trace-rows">'
        + "".join(merged)
        + "</div>"
        + note
        + template_block
    )


def _gap_rows_static(gaps: list[dict[str, Any]], total: int) -> str:
    if not gaps:
        return ""
    return '<div class="gap-list">' + "".join(_gap_row(gap, total) for gap in gaps) + "</div>"


def _trace_toolbar() -> str:
    boxes = "".join(
        f'<label class="catbox"><input type="checkbox" class="cat" data-category="{_esc(cat)}" checked>'
        f" {_esc(CATEGORY_LABELS[cat])}</label>"
        for cat in CATEGORY_ORDER
    )
    kbd = (
        '<details id="kbd-help" class="kbd-help"><summary>Teclado</summary>'
        "<table><caption>Atajos de la vista Traza</caption>"
        '<thead><tr><th scope="col">tecla</th><th scope="col">acción</th></tr></thead><tbody>'
        "<tr><td>/</td><td>foco en Search in trace</td></tr>"
        "<tr><td>j / k</td><td>bajar / subir la selección entre filas visibles</td></tr>"
        "<tr><td>Enter</td><td>abrir el panel de la fila seleccionada</td></tr>"
        "<tr><td>Esc</td><td>cerrar panel / salir de issues-only</td></tr>"
        "<tr><td>?</td><td>mostrar/ocultar esta ayuda</td></tr>"
        "</tbody></table></details>"
    )
    return (
        '<div class="toolbar">'
        '<input type="search" id="trace-search" placeholder="Search in trace" '
        'aria-label="Search in trace">'
        f'<div class="cats">{boxes}</div>'
        '<span class="zoom">'
        '<button type="button" id="zoom-in" aria-label="Acercar">+</button>'
        '<button type="button" id="zoom-out" aria-label="Alejar">&minus;</button>'
        '<button type="button" id="zoom-reset" aria-label="Reset zoom">⤢</button>'
        "</span>"
        '<button type="button" id="expand-all">Expand all</button>'
        '<button type="button" id="collapse-all">Collapse all</button>'
        f"{kbd}"
        "</div>"
    )


def _trace_panel() -> str:
    return (
        '<div id="span-panel" class="span-panel" hidden aria-label="Detalle del span">'
        '<div class="panel-head">'
        '<button type="button" id="panel-close" class="panel-close">&times; Close</button>'
        '<h2 id="panel-title"></h2>'
        '<div id="panel-status" class="panel-status"></div>'
        '<div id="panel-dur" class="panel-dur"></div>'
        '<div id="panel-badge" class="panel-badge"></div>'
        "</div>"
        '<div class="panel-id"><code id="panel-id"></code>'
        '<button type="button" id="copy-id">Copiar</button></div>'
        '<input type="search" id="attr-search" placeholder="Buscar atributos" '
        'aria-label="Buscar atributos">'
        '<div id="panel-attrs"></div>'
        "</div>"
    )


_VIEW_JS = """
(function(){
  "use strict";
  var dataEl = document.getElementById("trace-data");
  var rowsEl = document.getElementById("trace-rows");
  var panel = document.getElementById("span-panel");
  if (!dataEl || !rowsEl || !panel) { return; }
  var trace;
  try { trace = JSON.parse(dataEl.textContent); } catch (e) { return; }
  var meta = trace.meta || {};
  var summary = trace.summary || {};
  var avg = summary.avg_ms_by_tool || {};
  var totalV = meta.virtual_total || 0;
  var panelTitle = document.getElementById("panel-title");
  var panelStatus = document.getElementById("panel-status");
  var panelDur = document.getElementById("panel-dur");
  var panelBadge = document.getElementById("panel-badge");
  var panelId = document.getElementById("panel-id");
  var panelAttrs = document.getElementById("panel-attrs");
  var attrSearch = document.getElementById("attr-search");
  var search = document.getElementById("trace-search");
  var catBoxes = Array.prototype.slice.call(document.querySelectorAll(".cat"));
  var copyBtn = document.getElementById("copy-id");
  var issuesChip = document.getElementById("issues-chip");
  var rows = Array.prototype.slice.call(rowsEl.querySelectorAll(".row"));
  var dataById = {};
  var parentOf = {};
  var collapsed = {};
  var selected = null;
  var cursorEl = null;
  var issuesOnly = false;
  var viewStart = 0;
  var viewSpan = totalV > 0 ? totalV : 1;
  var pxPerVms = 1;
  function walkSession(node) {
    dataById[node.id] = node;
    (node.spans || []).forEach(walkSpan);
  }
  function walkSpan(span) {
    dataById[span.id] = span;
    if (span.child && !span.child.ref) { walkSession(span.child); }
  }
  (trace.roots || []).forEach(walkSession);
  rows.forEach(function(el){
    parentOf[el.getAttribute("data-span-id")] = el.getAttribute("data-parent") || "";
  });
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = String(text); }
    return e;
  }
  function markInto(node, text, query) {
    node.textContent = "";
    if (!query) { node.textContent = text; return; }
    var lower = text.toLowerCase();
    var q = query.toLowerCase();
    var i = 0;
    while (true) {
      var j = lower.indexOf(q, i);
      if (j < 0) { node.appendChild(document.createTextNode(text.slice(i))); break; }
      if (j > i) { node.appendChild(document.createTextNode(text.slice(i, j))); }
      var m = document.createElement("mark");
      m.textContent = text.slice(j, j + q.length);
      node.appendChild(m);
      i = j + q.length;
    }
  }
  function renderDiff(lines) {
    var pre = document.createElement("pre");
    pre.className = "diff";
    lines.forEach(function(line){
      var s = document.createElement("span");
      var head = line.charAt(0);
      s.className = head === "+" ? "d-add" : head === "-" ? "d-del" : head === "@" ? "d-hunk" : "d-ctx";
      s.textContent = line + "\\n";
      pre.appendChild(s);
    });
    return pre;
  }
  function renderAttrs(d) {
    panelAttrs.textContent = "";
    var groups = (d.attrs || []).slice();
    if (d.diff && d.diff.length) { groups.push({name: "diff", diff: true}); }
    if (d.input) { groups.push({name: "input", pre: d.input}); }
    if (d.output) { groups.push({name: "output", pre: d.output}); }
    groups.forEach(function(g){
      var det = document.createElement("details");
      det.open = true;
      det.className = "attr-group";
      det.setAttribute("data-group", String(g.name).toLowerCase());
      var sum = document.createElement("summary");
      sum.textContent = g.name;
      det.appendChild(sum);
      if (g.diff) {
        var tmpl = document.querySelector('#diff-templates pre[data-span-id="' + d.id + '"]');
        det.appendChild(tmpl ? tmpl.cloneNode(true) : renderDiff(d.diff));
      } else if (g.pre !== undefined) {
        var pre = document.createElement("pre");
        pre.textContent = g.pre;
        det.appendChild(pre);
      } else {
        var dl = document.createElement("dl");
        (g.items || []).forEach(function(it){
          var dt = document.createElement("dt");
          dt.textContent = it[0];
          dt.setAttribute("data-key", String(it[0]).toLowerCase());
          var dd = document.createElement("dd");
          dd.textContent = it[1];
          dl.appendChild(dt);
          dl.appendChild(dd);
        });
        det.appendChild(dl);
      }
      panelAttrs.appendChild(det);
    });
    filterAttrs();
  }
  function filterAttrs() {
    if (!attrSearch) { return; }
    var q = attrSearch.value.trim().toLowerCase();
    Array.prototype.forEach.call(panelAttrs.querySelectorAll(".attr-group"), function(g){
      var name = g.getAttribute("data-group") || "";
      var any = !q || name.indexOf(q) >= 0;
      Array.prototype.forEach.call(g.querySelectorAll("dt"), function(dt){
        var hit = !q || (dt.getAttribute("data-key") || "").indexOf(q) >= 0;
        dt.hidden = !hit;
        if (dt.nextElementSibling) { dt.nextElementSibling.hidden = !hit; }
        if (hit) { any = true; }
      });
      g.hidden = !any;
    });
  }
  function select(id) {
    var node = dataById[id];
    var row = document.getElementById("span-" + id);
    if (!node || !row) { return; }
    if (selected) { selected.classList.remove("sel"); }
    row.classList.add("sel");
    selected = row;
    panelTitle.textContent = node.title ? (node.tool + " — " + node.title) : (node.label || "");
    var isErr = node.status === "error";
    if (panelStatus) { panelStatus.textContent = isErr ? "✕ error" : ""; }
    panel.classList.toggle("err", isErr);
    panelDur.textContent = node.dur_text || "";
    var a = avg[node.tool];
    if (a > 0 && typeof node.duration_ms === "number") {
      var pct = Math.round((node.duration_ms - a) * 100 / a);
      panelBadge.textContent = Math.abs(pct) + "% " + (pct <= 0 ? "más rápido" : "más lento") + " que el avg";
    } else {
      panelBadge.textContent = "";
    }
    panelId.textContent = node.id || "";
    renderAttrs(node);
    panel.hidden = false;
  }
  function ancestorCollapsed(id) {
    var p = parentOf[id];
    while (p) {
      if (collapsed[p]) { return true; }
      p = parentOf[p] || "";
    }
    return false;
  }
  function ancestorHasErrors(id) {
    var p = parentOf[id];
    while (p) {
      var el = document.getElementById("span-" + p);
      if (el && parseInt(el.getAttribute("data-self-errors") || "0", 10) > 0) { return true; }
      p = parentOf[p] || "";
    }
    return false;
  }
  function setIssues(on) {
    issuesOnly = on;
    if (issuesChip) { issuesChip.setAttribute("aria-pressed", on ? "true" : "false"); }
    applyFilters();
  }
  function applyFilters() {
    var query = search ? search.value.trim() : "";
    var lq = query.toLowerCase();
    var enabled = {};
    catBoxes.forEach(function(cb){ enabled[cb.getAttribute("data-category")] = cb.checked; });
    var match = {};
    var visible = {};
    var errVisible = {};
    rows.forEach(function(el){
      var id = el.getAttribute("data-span-id");
      var label = el.getAttribute("data-label") || "";
      match[id] = lq === "" || label.toLowerCase().indexOf(lq) >= 0;
      if (match[id]) {
        visible[id] = true;
        var p = parentOf[id];
        while (p) { visible[p] = true; p = parentOf[p] || ""; }
      }
      if (parseInt(el.getAttribute("data-self-errors") || "0", 10) > 0) {
        errVisible[id] = true;
        var q = parentOf[id];
        while (q) { errVisible[q] = true; q = parentOf[q] || ""; }
      }
    });
    rows.forEach(function(el){
      var id = el.getAttribute("data-span-id");
      var cat = el.getAttribute("data-category");
      var ok = enabled[cat] !== false;
      if (ok && lq !== "") { ok = !!visible[id]; }
      if (ok && issuesOnly) { ok = !!errVisible[id] || ancestorHasErrors(id); }
      if (ok && ancestorCollapsed(id)) { ok = false; }
      el.hidden = !ok;
      var labelEl = el.querySelector(".rlabel");
      if (labelEl) { markInto(labelEl, el.getAttribute("data-label") || "", query); }
    });
    if (cursorEl && cursorEl.hidden) { cursorEl.classList.remove("cursor"); cursorEl = null; }
  }
  function setChevron(el) {
    var chev = el.querySelector(".chev");
    if (!chev || chev.classList.contains("empty")) { return; }
    var id = el.getAttribute("data-span-id");
    var isCollapsed = !!collapsed[id];
    chev.textContent = isCollapsed ? "▸" : "▾";
    chev.setAttribute("aria-expanded", isCollapsed ? "false" : "true");
  }
  function flashCopy(ok) {
    if (!copyBtn) { return; }
    var original = copyBtn.textContent;
    copyBtn.textContent = ok ? "Copiado" : "Seleccioná el ID";
    window.setTimeout(function(){ copyBtn.textContent = original; }, 1500);
  }
  function copyId() {
    var text = panelId.textContent || "";
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function(){ flashCopy(true); }, function(){ flashCopy(false); });
    } else {
      flashCopy(false);
    }
  }
  rows.forEach(function(el){
    setChevron(el);
    var chev = el.querySelector(".chev");
    function toggle(ev) {
      ev.preventDefault();
      ev.stopPropagation();
      var id = el.getAttribute("data-span-id");
      if (collapsed[id]) { delete collapsed[id]; } else { collapsed[id] = true; }
      setChevron(el);
      applyFilters();
    }
    if (chev && !chev.classList.contains("empty")) {
      chev.addEventListener("click", toggle);
      chev.addEventListener("keydown", function(ev){
        if (ev.key === "Enter" || ev.key === " ") { toggle(ev); }
      });
    }
    el.addEventListener("click", function(){ select(el.getAttribute("data-span-id")); });
  });
  if (search) { search.addEventListener("input", applyFilters); }
  catBoxes.forEach(function(cb){ cb.addEventListener("change", applyFilters); });
  if (attrSearch) { attrSearch.addEventListener("input", filterAttrs); }
  if (copyBtn) { copyBtn.addEventListener("click", copyId); }
  if (issuesChip) {
    issuesChip.addEventListener("click", function(){ setIssues(!issuesOnly); });
  }
  var expandAll = document.getElementById("expand-all");
  var collapseAll = document.getElementById("collapse-all");
  if (expandAll) {
    expandAll.addEventListener("click", function(){
      collapsed = {};
      rows.forEach(setChevron);
      applyFilters();
    });
  }
  if (collapseAll) {
    collapseAll.addEventListener("click", function(){
      rows.forEach(function(el){
        var chev = el.querySelector(".chev");
        if (chev && !chev.classList.contains("empty")) {
          collapsed[el.getAttribute("data-span-id")] = true;
        }
        setChevron(el);
      });
      applyFilters();
    });
  }
  function closePanel() {
    panel.hidden = true;
    if (selected) { selected.classList.remove("sel"); selected = null; }
    var h = window.location.hash;
    if (h.indexOf("#span-") === 0 || h.indexOf("#trace/") === 0) {
      history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }
  var closeBtn = document.getElementById("panel-close");
  if (closeBtn) { closeBtn.addEventListener("click", closePanel); }

  function firstTrack() {
    for (var i = 0; i < rows.length; i++) {
      var t = rows[i].querySelector(".rtrack");
      if (t) { return t; }
    }
    return null;
  }
  function trackWidth() {
    var t = firstTrack();
    return t && t.clientWidth ? t.clientWidth : 600;
  }
  function clampView() {
    if (totalV <= 0) { viewStart = 0; viewSpan = 1; return; }
    if (viewSpan > totalV) { viewSpan = totalV; }
    if (viewStart < 0) { viewStart = 0; }
    if (viewStart + viewSpan > totalV) { viewStart = Math.max(0, totalV - viewSpan); }
  }
  function applyZoom() {
    if (totalV <= 0) { return; }
    var width = trackWidth();
    pxPerVms = width / viewSpan;
    rows.forEach(function(el){
      var bar = el.querySelector(".rbar");
      if (!bar) { return; }
      var vs = parseFloat(el.getAttribute("data-v-start") || "0");
      var ve = parseFloat(el.getAttribute("data-v-end") || "0");
      var left = (vs - viewStart) * pxPerVms;
      var w = (ve - vs) * pxPerVms;
      if (left + w < 0 || left > width) { bar.style.display = "none"; return; }
      bar.style.display = "";
      bar.style.left = Math.max(0, left) + "px";
      bar.style.width = Math.max(1, w) + "px";
    });
    updateViewport();
  }
  function updateViewport() {
    var vp = document.getElementById("mm-viewport");
    if (!vp || totalV <= 0) { return; }
    var W = 1000;
    var x = (viewStart / totalV) * W;
    var w = (viewSpan / totalV) * W;
    vp.setAttribute("x", String(Math.max(0, x)));
    vp.setAttribute("width", String(Math.min(W - x, w)));
  }
  function zoomAt(factor, clientX) {
    if (totalV <= 0) { return; }
    var t = firstTrack();
    var rect = t ? t.getBoundingClientRect() : {left: 0, width: trackWidth()};
    var frac = rect.width > 0 ? (clientX - rect.left) / rect.width : 0.5;
    frac = Math.max(0, Math.min(1, frac));
    var anchor = viewStart + frac * viewSpan;
    var span = Math.max(totalV / 500, Math.min(totalV, viewSpan / factor));
    viewStart = anchor - frac * span;
    viewSpan = span;
    clampView();
    applyZoom();
  }
  function zoomReset() {
    viewStart = 0;
    viewSpan = totalV > 0 ? totalV : 1;
    clampView();
    applyZoom();
  }
  function zoomCenter(factor) {
    var t = firstTrack();
    var x = t ? t.getBoundingClientRect().left + trackWidth() / 2 : trackWidth() / 2;
    zoomAt(factor, x);
  }
  var zoomIn = document.getElementById("zoom-in");
  var zoomOut = document.getElementById("zoom-out");
  var zoomResetBtn = document.getElementById("zoom-reset");
  if (zoomIn) { zoomIn.addEventListener("click", function(){ zoomCenter(1.5); }); }
  if (zoomOut) { zoomOut.addEventListener("click", function(){ zoomCenter(1 / 1.5); }); }
  if (zoomResetBtn) { zoomResetBtn.addEventListener("click", zoomReset); }
  rows.forEach(function(el){
    var track = el.querySelector(".rtrack");
    if (track) {
      track.addEventListener("wheel", function(ev){
        ev.preventDefault();
        zoomAt(ev.deltaY < 0 ? 1.25 : 1 / 1.25, ev.clientX);
      }, {passive: false});
    }
  });
  rowsEl.addEventListener("dblclick", zoomReset);
  var mm = document.getElementById("mm");
  if (mm) {
    var dragging = false;
    var suppressClick = false;
    var dragX = 0;
    var dragView = 0;
    mm.addEventListener("pointerdown", function(ev){
      dragging = true;
      suppressClick = false;
      dragX = ev.clientX;
      dragView = viewStart;
      if (mm.setPointerCapture) {
        try { mm.setPointerCapture(ev.pointerId); } catch (e) { dragging = false; }
      }
    });
    mm.addEventListener("pointermove", function(ev){
      if (!dragging) { return; }
      var r = mm.getBoundingClientRect();
      if (r.width <= 0) { return; }
      if (Math.abs(ev.clientX - dragX) > 2) { suppressClick = true; }
      viewStart = dragView + (ev.clientX - dragX) / r.width * totalV;
      clampView();
      applyZoom();
    });
    mm.addEventListener("pointerup", function(){ dragging = false; });
    mm.addEventListener("pointercancel", function(){ dragging = false; });
    mm.addEventListener("click", function(ev){
      if (suppressClick) { suppressClick = false; return; }
      var r = mm.getBoundingClientRect();
      if (r.width <= 0) { return; }
      var frac = (ev.clientX - r.left) / r.width;
      viewStart = frac * totalV - viewSpan / 2;
      clampView();
      applyZoom();
    });
  }
  ["view-perf", "view-diagram", "view-trace"].forEach(function(id){
    var radio = document.getElementById(id);
    if (radio) {
      radio.addEventListener("change", function(){ if (radio.checked) { applyZoom(); } });
    }
  });

  function visibleRows() {
    return rows.filter(function(el){ return !el.hidden; });
  }
  function moveCursor(dir) {
    var vis = visibleRows();
    if (!vis.length) { return; }
    var idx = cursorEl ? vis.indexOf(cursorEl) : -1;
    var next = idx + dir;
    if (next < 0) { next = 0; }
    if (next >= vis.length) { next = vis.length - 1; }
    if (cursorEl) { cursorEl.classList.remove("cursor"); }
    cursorEl = vis[next];
    cursorEl.classList.add("cursor");
    cursorEl.scrollIntoView({block: "nearest"});
  }
  function escapeAction() {
    if (!panel.hidden) { closePanel(); return; }
    if (issuesOnly) { setIssues(false); }
  }
  document.addEventListener("keydown", function(ev){
    var tag = ev.target && ev.target.tagName ? ev.target.tagName.toLowerCase() : "";
    if (tag === "input" || tag === "textarea" || tag === "select") {
      if (ev.key === "Escape" && ev.target.blur) { ev.target.blur(); }
      return;
    }
    switch (ev.key) {
      case "j":
        ev.preventDefault();
        moveCursor(1);
        break;
      case "k":
        ev.preventDefault();
        moveCursor(-1);
        break;
      case "Enter":
        if (cursorEl) { select(cursorEl.getAttribute("data-span-id")); }
        break;
      case "Escape":
        escapeAction();
        break;
      case "/":
        ev.preventDefault();
        if (search) { search.focus(); }
        break;
      case "?": {
        var kh = document.getElementById("kbd-help");
        if (kh) { kh.open = !kh.open; }
        break;
      }
      default:
        break;
    }
  });

  function setTab(id) {
    var radio = document.getElementById(id);
    if (radio) { radio.checked = true; }
  }
  function applyHash() {
    var h = window.location.hash;
    if (h === "#issues") { setTab("view-trace"); setIssues(true); return; }
    if (h === "#perf") { setTab("view-perf"); return; }
    if (h === "#diagram") { setTab("view-diagram"); return; }
    if (h === "#trace") { setTab("view-trace"); return; }
    if (h.indexOf("#trace/") === 0) { setTab("view-trace"); select(h.slice(7)); return; }
    if (h.indexOf("#span-") === 0) { setTab("view-trace"); select(h.slice(6)); }
  }
  window.addEventListener("hashchange", applyHash);
  applyHash();
  applyFilters();
  applyZoom();
})();
"""


_CSS = """
:root{--bg:#FFFFFF;--surface:#F6F6F6;--text:#1A1A1A;--focus:#005F9E;--error:#B3261E;--ok:#14682F;
--line:#D0D0D0;--build:#0072B2;--coder:#D55E00;--reviewer:#007B5E;--interrogator:#7A4FA3;
--other:#2E86AB;--llm:#555555;--muted:#5A5A5A;--dim:#3A3A3A;--track:#ECECEC;--rowline:#EFEFEF;
--sel:#FFF3CD;--mark:#FFE08A;--add:#14682F;--del:#B3261E}
@media (prefers-color-scheme: dark){
:root{--bg:#141821;--surface:#1D2430;--text:#E6EAF2;--focus:#5FA8CE;--error:#F0796F;--ok:#3FBF7F;
--line:#2A3242;--build:#4C9FD4;--coder:#E8762D;--reviewer:#2FB483;--interrogator:#A888D8;
--other:#5FA8CE;--llm:#9AA6B8;--muted:#A6B0C2;--dim:#C3CBD9;--track:#2A3242;--rowline:#232B39;
--sel:#3A3320;--mark:#6B5A1E;--add:#3FBF7F;--del:#F0796F}
}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
h1{font-size:18px;margin:0 0 8px}h3{font-size:14px;margin:14px 0 6px}
header{padding:16px 20px;border-bottom:1px solid var(--line)}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:4px}
.chip{background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:2px 10px}
.tab{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}
.tabs{display:flex;gap:4px;padding:10px 20px 0;border-bottom:1px solid var(--line)}
.tabs label{padding:6px 14px;border:1px solid var(--line);border-bottom:none;border-radius:6px 6px 0 0;
cursor:pointer;background:var(--surface)}
main{padding:14px 20px 30vh}
.panel{display:none}
#view-perf:checked ~ main #panel-perf,
#view-diagram:checked ~ main #panel-diagram,
#view-trace:checked ~ main #panel-trace{display:block}
#view-perf:checked ~ .tabs label[for=view-perf],
#view-diagram:checked ~ .tabs label[for=view-diagram],
#view-trace:checked ~ .tabs label[for=view-trace]{background:var(--bg);font-weight:600;
border-color:var(--text)}
#view-perf:focus-visible ~ .tabs label[for=view-perf],
#view-diagram:focus-visible ~ .tabs label[for=view-diagram],
#view-trace:focus-visible ~ .tabs label[for=view-trace]{outline:2px solid var(--focus);outline-offset:2px}
table{border-collapse:collapse;font-size:13px;margin-top:8px}
caption{text-align:left;color:var(--muted);padding:4px 0}
th,td{padding:4px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th[scope=col]{font-weight:600}
.charts{display:flex;flex-wrap:wrap;gap:24px}
.metric h3{margin-top:10px}
ul.bars,ul.legend{list-style:none;margin:0;padding:0}
ul.bars li{display:flex;align-items:center;gap:8px;margin:2px 0}
.bl{min-width:110px;display:inline-block}.bv{color:var(--muted)}
.barcell{background:var(--surface);border-radius:2px}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px}
.legend li{display:flex;align-items:center;gap:5px}
.swatch{width:14px;height:14px;display:inline-block;border-radius:2px;border:1px solid var(--line)}
.muted{color:var(--muted)}
.st{font-weight:700}.st.err{color:var(--error)}.st.ok{color:var(--ok)}
.diagram-block{margin:10px 0 26px;overflow-x:auto}
.equivalent{margin-top:8px}.equivalent summary{cursor:pointer;color:var(--focus)}
.defs{position:absolute}
.sess{margin:6px 0}
.sess-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.badge{border:2px solid var(--line);border-radius:999px;padding:1px 8px;font-weight:600}
.tag{font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:3px;padding:0 5px}
.win-tag{color:var(--focus);border-color:var(--focus)}
.track{position:relative;display:inline-block;width:180px;height:10px;background:var(--track);
border-radius:3px;vertical-align:middle}
.bar{position:absolute;top:0;height:10px;border-radius:3px;min-width:2px}
.bar-build{background:var(--build)}.bar-coder{background:var(--coder)}
.bar-reviewer{background:var(--reviewer)}
.bar-interrogator{background:var(--interrogator)}.bar-other{background:var(--other)}
.bar-bash{background:var(--muted)}.bar-write{background:var(--reviewer)}
.bar-task{background:var(--coder)}
.bar-tool{background:var(--muted)}.bar-llm{background:var(--llm)}.bar-error{background:var(--error)}
details.sess-parts{margin:4px 0 4px 18px}
details.sess-parts > summary{cursor:pointer;color:var(--focus)}
details.span{margin:3px 0 3px 10px}
details.span > summary{display:flex;align-items:center;gap:8px;cursor:pointer}
.sname{flex:1 1 auto;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
pre{white-space:pre-wrap;word-break:break-word;background:var(--surface);padding:8px;border-radius:4px;
margin:4px 0 10px}
.sec{color:var(--muted);margin-top:6px;font-size:12px}
.jump{color:var(--focus);text-decoration:none;font-weight:600}
.jump:hover{text-decoration:underline}
td.exec{white-space:normal;max-width:340px;font-size:12px;color:var(--dim)}
ul.lane-exec{list-style:none;margin:8px 0;padding:0;font-size:12px}
ul.lane-exec li{margin:2px 0}
details.narrative{margin:8px 0}
details.narrative > summary{cursor:pointer;color:var(--focus);font-weight:600}
details.narrative ol{margin:6px 0 6px 18px;padding:0}
details.narrative li{margin:3px 0;font-size:12px}
.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:0 0 10px}
.toolbar input[type=search]{padding:4px 8px;border:1px solid var(--line);border-radius:4px;min-width:200px}
.cats{display:flex;flex-wrap:wrap;gap:8px;font-size:12px}
.catbox{display:inline-flex;align-items:center;gap:3px}
.toolbar button{padding:3px 10px;border:1px solid var(--line);border-radius:4px;background:var(--surface);
cursor:pointer}
.trace-rows{border:1px solid var(--line);border-radius:6px;overflow:hidden}
.row{display:flex;align-items:center;gap:6px;padding:2px 8px;text-decoration:none;color:var(--text);
border-bottom:1px solid var(--rowline);min-height:24px}
.row[hidden]{display:none}
.row:hover{background:var(--surface)}
.row.sel,.row:target{background:var(--sel);outline:2px solid var(--focus);outline-offset:-2px}
.guides{display:inline-flex;align-self:stretch}
.guides i{display:inline-block;width:14px;border-left:1px solid var(--line)}
.chev{display:inline-block;width:14px;text-align:center;cursor:pointer;user-select:none;color:var(--muted)}
.chev.empty{cursor:default}
.chev:focus-visible{outline:2px solid var(--focus);outline-offset:1px}
.cchip{display:inline-block;width:10px;height:10px;border-radius:2px;flex:0 0 auto}
.rlabel{flex:0 0 42%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rlabel mark{background:var(--mark);color:inherit}
.rtrack{position:relative;flex:1 1 auto;height:10px;background:var(--track);border-radius:3px;
min-width:60px;overflow:hidden}
.rbar{position:absolute;top:0;height:10px;border-radius:3px;min-width:2px;background:var(--muted)}
.cat-task .rbar{background:var(--coder)}.cat-bash .rbar{background:var(--other)}
.cat-files .rbar{background:var(--reviewer)}.cat-skill .rbar{background:var(--interrogator)}
.cat-web .rbar{background:var(--build)}.cat-sesion .rbar{background:var(--build)}
.cat-llm .rbar{background:var(--llm)}
.cat-memory .rbar,.cat-otros .rbar{background:var(--other)}
td.llmcell{white-space:nowrap}
.note{margin:6px 0;padding:4px 8px;background:var(--surface);border:1px solid var(--line);
border-radius:4px;color:var(--muted);font-size:12px}
.rdur{flex:0 0 auto;font-variant-numeric:tabular-nums;color:var(--dim);font-size:12px;min-width:64px;
text-align:right}
.err-badge{color:var(--error);border:1px solid var(--error);border-radius:3px;padding:0 4px;
font-size:11px;font-weight:700}
.cursor{outline:2px dashed var(--focus);outline-offset:-2px}
.gap-row{position:relative;height:20px;border-bottom:1px solid var(--rowline)}
.gap-row .glabel{position:absolute;top:2px;font-size:11px;color:var(--muted);font-style:italic;
white-space:nowrap;transform:translateX(-50%)}
.gap-list{border:1px solid var(--line);border-radius:6px;margin-bottom:10px}
.minimap{display:block;width:100%;height:120px;background:var(--surface);border:1px solid var(--line);
border-radius:6px;margin-bottom:8px}
.mm-vp{fill:rgba(0,95,158,0.12);stroke:var(--focus);stroke-width:2;pointer-events:none}
.zoom{display:inline-flex;gap:4px}
.kbd-help{margin-left:auto}
.kbd-help>summary{cursor:pointer;color:var(--focus)}
.kbd-help table{margin-top:6px}
.span-panel{position:fixed;top:0;right:0;width:400px;max-width:92vw;height:100vh;overflow:auto;
background:var(--bg);border-left:1px solid var(--line);box-shadow:-2px 0 8px rgba(0,0,0,0.12);
padding:14px;z-index:10}
.span-panel[hidden]{display:none}
.panel-head{border-bottom:1px solid var(--line);padding-bottom:8px;margin-bottom:8px}
.panel-close{float:right;border:1px solid var(--line);background:var(--surface);border-radius:4px;
cursor:pointer;padding:2px 8px}
.panel-head h2{font-size:14px;margin:0 0 6px}
.panel-dur{font-size:22px;font-weight:700}
.panel-badge{font-size:12px;color:var(--focus)}
.panel-id{display:flex;align-items:center;gap:8px;margin:8px 0}
.panel-id code{font-size:12px;word-break:break-all}
.panel-id button{padding:3px 8px;border:1px solid var(--line);border-radius:4px;
background:var(--surface);cursor:pointer}
.span-panel input[type=search]{padding:3px 8px;border:1px solid var(--line);border-radius:4px;
background:var(--surface);width:100%;margin-bottom:8px}
.attr-group{margin:6px 0;border:1px solid var(--line);border-radius:4px;padding:4px 8px}
.attr-group>summary{cursor:pointer;font-weight:600;color:var(--focus)}
.attr-group dl{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:6px 0;font-size:12px}
.attr-group dt{font-weight:600}
.attr-group dd{margin:0;word-break:break-word}
.attr-group dt[hidden],.attr-group dd[hidden]{display:none}
.attr-group pre{max-height:240px;overflow:auto}
.span-panel.err{border-left:4px solid var(--error)}
.panel-status{font-size:12px;font-weight:700;color:var(--error);min-height:14px}
.diff{margin:4px 0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;
line-height:1.4;white-space:pre-wrap;word-break:break-word}
.diff span{display:block}
.diff .d-add{color:var(--add)}
.diff .d-del{color:var(--del)}
.diff .d-hunk{color:var(--focus)}
.diff .d-ctx{color:var(--muted)}
"""


def _time_split_chip(summary: dict[str, Any]) -> str:
    split = summary.get("time_split") or {}
    turns = int(split.get("turns") or 0)
    llm_ms = int(split.get("llm_ms") or 0)
    tool_ms = int(split.get("tool_ms") or 0)
    active = max(0, int(split.get("window_ms") or 0) - int(split.get("gap_ms") or 0))
    llm_pct = round(100 * llm_ms / active) if active > 0 else 0
    tool_pct = round(100 * tool_ms / active) if active > 0 else 0
    return (
        '<span class="chip time-split">'
        f"Turnos: <b>{turns}</b> · LLM: <b>{_esc(_dur_text(llm_ms))}</b> (<b>{llm_pct}%</b> del tiempo activo)"
        f" · Tools: <b>{tool_pct}%</b></span>"
    )


def _header_v4(meta: dict[str, Any], summary: dict[str, Any]) -> str:
    window = meta.get("window") or {}
    span_count = sum(summary.get("tools", {}).values())
    return (
        f"<header><h1>Corrida {_esc(str(meta['app']))}/{_esc(str(meta['slug']))}</h1>"
        '<div class="chips">'
        f'<span class="chip">Spans: <b>{span_count}</b></span>'
        f'<span class="chip">Sesiones: <b>{sum(summary["agents"].values())}</b></span>'
        f'<span class="chip">Root Duration: <b>{_fmt_ms(summary["duration_ms"])}</b></span>'
        f'<span class="chip">Costo: <b>${sum(v["cost"] for v in summary["by_agent"].values()):.4f}</b></span>'
        f'<span class="chip">Errores: <b>{summary["errors"]}</b></span>'
        f"{_time_split_chip(summary)}"
        f'<button type="button" id="issues-chip" class="chip issues-chip" aria-pressed="false"'
        f' aria-label="Filtrar por issues">Issues: <b>{summary.get("issues", summary["errors"])}</b></button>'
        f'<span class="chip">Ventana: <b>{_esc(_iso(window.get("start")))}..{_esc(_iso(window.get("end")))}</b></span>'
        "</div></header>"
    )


def _header_v3(meta: dict[str, Any], summary: dict[str, Any]) -> str:
    return (
        f"<header><h1>Corrida {_esc(str(meta['app']))}/{_esc(str(meta['slug']))}</h1>"
        '<div class="chips">'
        f'<span class="chip">duración <b>{_fmt_ms(summary["duration_ms"])}</b></span>'
        f'<span class="chip">sesiones <b>{sum(summary["agents"].values())}</b></span>'
        f'<span class="chip">costo <b>${sum(v["cost"] for v in summary["by_agent"].values()):.4f}</b></span>'
        f'<span class="chip">errores <b>{summary["errors"]}</b></span>'
        f'<span class="chip">rondas <b>{len(summary["rounds"])}</b></span>'
        f"{_time_split_chip(summary)}"
        f'<button type="button" id="issues-chip" class="chip issues-chip" aria-pressed="false"'
        f' aria-label="Filtrar por issues">Issues: <b>{summary.get("issues", summary["errors"])}</b></button>'
        "</div></header>"
    )


def _render_html(trace: dict[str, Any], no_js: bool = False) -> str:
    meta = trace["meta"]
    summary = trace["summary"]
    palette = summary["palette"]
    perf = _render_perf(summary, palette)
    diagram = _svg_graph(trace["roots"], palette) + _svg_swimlanes(
        trace["roots"], summary["rounds"], summary.get("delegations", []), palette
    )
    vtotal = int(meta.get("virtual_total") or 0)
    if no_js:
        header = _header_v3(meta, summary)
        gap_block = _gap_rows_static(meta.get("gaps") or [], vtotal)
        traza = gap_block + "".join(_sess_block(r, 0, palette, vtotal) for r in trace["roots"])
        trace_checked, perf_checked = "", " checked"
    else:
        header = _header_v4(meta, summary)
        traza = _trace_toolbar() + _render_traza_js(trace) + _trace_panel()
        trace_checked, perf_checked = " checked", ""
    data = json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True).replace("<", "\\u003c")
    scripts = f'<script id="trace-data" type="application/json">{data}</script>\n'
    if not no_js:
        scripts += f'<script id="view-js">\n{_VIEW_JS}</script>\n'
    return (
        "<!doctype html>\n"
        '<html lang="es">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Corrida {_esc(str(meta['app']))}/{_esc(str(meta['slug']))}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        f'<input class="tab" type="radio" name="view" id="view-perf"{perf_checked}>\n'
        '<input class="tab" type="radio" name="view" id="view-diagram">\n'
        f'<input class="tab" type="radio" name="view" id="view-trace"{trace_checked}>\n'
        f"{header}\n"
        '<nav class="tabs" aria-label="Vistas">'
        '<label for="view-perf">Rendimiento</label>'
        '<label for="view-diagram">Diagrama</label>'
        '<label for="view-trace">Traza</label></nav>\n'
        "<main>\n"
        f'<section id="panel-perf" class="panel" aria-label="Rendimiento">{perf}</section>\n'
        f'<section id="panel-diagram" class="panel" aria-label="Diagrama">{diagram}</section>\n'
        f'<section id="panel-trace" class="panel" aria-label="Traza">{traza}</section>\n'
        "</main>\n"
        f"{scripts}"
        "</body>\n</html>\n"
    )


def _open_db(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        con.execute("SELECT 1 FROM session LIMIT 1")
    except sqlite3.Error:
        return None
    return con


def _default_texts(app_id: str, checkout: str, slug: str) -> list[str]:
    dirs: list[str] = []
    for candidate in (app_id, Path(checkout).name if checkout else ""):
        if candidate and candidate not in dirs:
            dirs.append(candidate)
    return [str(ROOT / "state" / "worktrees" / d / slug) for d in dirs]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="trace-report.py", description=__doc__)
    ap.add_argument("app", help="id de app del registry")
    ap.add_argument("slug", help="slug de la corrida")
    ap.add_argument("-o", "--out", help="archivo HTML de salida")
    ap.add_argument("--session", action="append", default=[], help="sesión ancla (repetible)")
    ap.add_argument("--grep", action="append", default=[], help="texto ancla en task-parts (repetible)")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="ruta de opencode.db (read-only)")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="truncado de payloads")
    ap.add_argument("--no-outputs", action="store_true", help="reemplaza input/output")
    ap.add_argument("--window-pad", type=float, default=DEFAULT_PAD_SECONDS, help="padding de ventana (s)")
    ap.add_argument(
        "--max-gap",
        type=float,
        default=DEFAULT_MAX_GAP_SECONDS,
        help="huecos de inactividad > SEC se comprimen en la línea virtual",
    )
    ap.add_argument(
        "--gap-render",
        type=int,
        default=DEFAULT_GAP_RENDER_MS,
        help="ms virtuales que ocupa cada hueco comprimido",
    )
    ap.add_argument(
        "--no-js",
        action="store_true",
        help="emite la vista v3 (sin el script de vista; todo <details>)",
    )
    ap.add_argument("--now", help="timestamp ISO fijo para generated_at (determinismo)")
    ap.add_argument("--list-anchors", action="store_true", help="sólo imprime los ids ancla")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import app_config

    try:
        reg = app_config.load_registry(args.app)
    except SystemExit as exc:
        _err(str(exc) if exc.code else f"app desconocida: {args.app}")
        return 1
    app_id = str(reg.get("id", args.app))
    checkout = str(reg.get("checkout_abs", ""))
    defaults = _default_texts(app_id, checkout, args.slug)

    db_path = Path(args.db)
    con = _open_db(db_path)
    if con is None:
        return _pendiente(f"db no legible: {db_path}")
    try:
        anchors, anchor_starts, matches = _find_anchors(con, list(args.session), list(args.grep), defaults)
        if not anchors:
            return _pendiente(f"sin anclas para {args.app}/{args.slug} (probá --session <id> o --grep)")
        if args.list_anchors:
            for anchor in anchors:
                _safe_stdout(anchor)
            return 0
        sessions = _load_sessions(con)
        down, ancestors, _roots = _closure(sessions, anchors)
        needed = sorted(set(anchors) | down | ancestors)
        parts = _load_parts(con, needed)
        llm_turns = _load_llm_turns(con, needed)
        generated_at = args.now if args.now else datetime.now(UTC).isoformat()
        wt = defaults[0] if defaults else ""
        trace = _build_trace(
            args.app,
            args.slug,
            wt,
            db_path,
            anchors,
            sessions,
            parts,
            llm_turns,
            anchor_starts,
            matches,
            args.window_pad,
            args.max_bytes,
            args.no_outputs,
            generated_at,
            args.max_gap,
            args.gap_render,
        )
    except sqlite3.Error as exc:
        return _pendiente(f"db no legible: {db_path} ({exc})")
    finally:
        con.close()
    if trace is None:
        return _pendiente(f"sin spans para {args.app}/{args.slug} en la ventana")

    out = Path(args.out) if args.out else ROOT / "state" / "reports" / args.app / f"trace-{args.slug}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render_html(trace, no_js=args.no_js), encoding="utf-8")
    n_spans = sum(len(r["spans"]) for r in trace["roots"])
    _err(f"{len(trace['roots'])} raíz(es), {n_spans} span(s) directos, {len(anchors)} ancla(s)")
    _safe_stdout(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
