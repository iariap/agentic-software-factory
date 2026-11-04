# Spec v3: trace-report — hacer visible qué ejecuta cada subagente y cómo delega el orquestador

Fecha: 2026-10-09 · App: `asf` · Rama: `factory/trace-report` (PR #27 abierto; suma un commit).
Vigencia: v1 (CLI/anclas/datos) y v2 (3 vistas, WCAG, sin JS) intactas; esto es la
iteración pedida por el owner: *"que quede claro cómo las trazas muestran lo que
los sub-agentes ejecutan y cómo el orquestador va delegando las tareas para
cada uno"*.

## Requisitos

### 1. Datos nuevos en `trace-data` (calculados en Python, deterministas)

Para cada sesión del árbol:
- `execution` = {
    `by_tool`: counts por tool (ya existe como tool_mix, exponerlo por sesión),
    `commands`: hasta 8 comandos bash **únicos** (los del `state.input.command`
    de los parts bash, truncados a 60 chars, ordenados por apariciones desc y
    luego alfa — estable),
    `files`: hasta 10 paths **únicos** tocados por `edit`/`write`
    (`state.input.filePath`/`path`, sin orden relativo al cwd),
    `skills`: nombres de `skill` usados,
  }
- `delegations` (lista global ordenada por t): {`k` (1..N), `from_agent`,
  `to_agent`, `to_session`, `description` (el campo `description` del task —
  etiqueta corta del orquestador, NO el prompt), `t_ms`, `dur_ms`, `status`}.
  Incluye TODAS las delegaciones del árbol (raíz→hijos e hijos→nietos), no só
  las `rounds` (que siguen siendo sólo desde la raíz).

Con `--no-outputs`: `commands`/`files` se reemplazan por sus counts
(`n_commands`, `n_files`), `description` se mantiene (es etiqueta del
orquestador), el prompt nunca entra.

### 2. Render

- **Diagrama**:
  a. Swimlanes: a cada delegación se le dibuja una **flecha** desde la lane
     del llamante en `t_ms` hasta la lane de la sesión hija (línea + cabeza
     de flecha, `aria-hidden` en el SVG, la info existe en texto), etiquetada
     `Rk → <to_agent>: <description≤40>` en texto del SVG. Si la misma sesión
     hija recibe varias delegaciones, una flecha por delegación (se ve el
     re-invoke explícito).
  b. Al pie de cada lane, línea compacta de texto: `ejecutó: 12 bash
     (git status, uv run pytest, ruff check…) · 8 edit · skill voice-notify`
     (usa `execution`, top-3 comandos).
  c. Nuevo `<details open>` **"Narrativa de delegación"**: una línea por
     delegación en orden temporal:
     `R1 · build → coder "Implementar…" · 14m21s · ✓ · ejecutó: 57 bash
     (bash -n, python3 test_harness, git add…), 21 edit (scripts/x.py…), 0 skills`
     — es la forma legible-sin-SVG de la corrida.
- **Rendimiento**: columna extra **"Qué ejecutó"** en la tabla por agente
  (texto compacto igual a 2b, agregado sobre las sesiones del agente).
- **Traza**: cada nodo-sesión lleva `id="sesion-<id>"`; el task-span muestra
  un badge `→ <agent> (<dur>)` con `href="#sesion-<id>"` (salto a la sesión);
  con `ref: true` el badge enlaza a la primera aparición.

### 3. Tests (extienden `TestTraceReport`, mismo fixture con coder re-invocado)

- `execution` del coder-hijo: `commands` contiene los bash del fixture
  (insertá `git status`, `uv run pytest`, `git status` repetido → único con
  count 2), `files` con los paths de edit/write del fixture.
- `delegations`: k ascendente, `from_agent`/`to_agent` correctos, incluye la
  delegación anidada fixture (coder→explore si la hay; si no, agregala al
  fixture).
- Narrativa: el HTML contiene `R1 → coder` y el top-comando; con
  `--no-outputs` NO aparecen strings de comandos ni paths (assert sobre el
  HTML: `git status` ausente, `n_commands` presente).
- Anclas: `id="sesion-<child>"` presente; el badge task tiene
  `href="#sesion-` y un label `→ coder`.
- Flechas: count de `<line`/`<path` marcadores `data-arrow` == len(delegations).
- Determinismo byte-a-byte con `--now` fijo (no lo rompas: ordenar
  commands/files por (count desc, lexicográfico)).

### 4. Docs
- `factory-report/SKILL.md` (sección trace.html): una línea: "la traza muestra
  qué ejecutó cada subagente (comandos, archivos) y cada delegación del
  orquestador (R1..RN con duración/estado) en el Diagrama y en la Narrativa."
- `specs/asf/trace-report-v3.md` (este archivo) versionado.
- AGENTS/README: no hace falta (ya describen la herramienta).

### 5. Finish condition

```bash
python3 scripts/test_harness.py            # verde, tests nuevos incluidos
uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
python3 scripts/trace-report.py asf trace-report -o /tmp/trace-self-v3.html
python3 scripts/trace-report.py mi-mesa household-measures-146 -o /tmp/trace-146-v3.html
python3 - <<'EOF'
import re
h=open("/tmp/trace-self-v3.html").read()
assert "Narrativa" in h and re.search(r'R\d → coder', h) and 'id="sesion-' in h
print("self OK (narrativa+ancla)")
EOF
```

## Fuera de alcance
- Zoom/pan, links externos, OTLP, run-stats cross-run.
