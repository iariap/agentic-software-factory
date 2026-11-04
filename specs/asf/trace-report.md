# Spec: trace-report — traza estilo OTel de cada corrida en un HTML de archivo único

Fecha: 2026-10-09 · App: `asf` · Rama: `factory/trace-report` · Worktree: `state/worktrees/asf/trace-report` (creado con `scripts/new-worktree.sh` — dogfood del flujo #25)

## Qué se quiere y por qué (outcome)

El pipeline produce `run.json`/`decisions.tsv`/`report.md`, pero no hay forma de
**ver** cómo transcurrió una corrida: quién llamó a quién (orquestador →
coder/reviewers/etc.), con qué prompt/parámetros, qué hizo cada span (bash,
edit, skill), cuánto duró, qué costó y dónde falló.

Al cerrar una corrida (CI verde / post-merge) el orquestador genera
`state/reports/<app>/<run>/trace.html`: un **único archivo HTML autocontenido**
(inline CSS/JS, sin CDN, abre offline en el navegador) con la traza estilo
OpenTelemetry: árbol de spans con waterfall por tiempo, payloads expandibles,
resumen de costo/tokens por agente. Determinista: mismos datos de db + mismos
args → mismo archivo.

Fuente: `~/.local/share/opencode/opencode.db` (SQLite de opencode, tablas
`session`, `part`, `project`). **Read-only** (`file:...?mode=ro`, `timeout=3`).
Nunca se sube a git: vive en `state/` (gitignored). Para compartir existe
`--no-outputs`.

## Hechos de la db (verificados 2026-10-09, no asumir otros)

- Todas las sesiones de un proyecto figuran con `session.directory` = raíz del
  proyecto (p. ej. `/home/pablo/desa/asf`), NO el worktree → el directorio no
  sirve como ancla de corrida.
- Los subagents son sesiones hijas: `session.parent_id` = sesión padre;
  `session.agent` = rol (`coder`, `reviewer-*`, `interrogator`, ...); columnas
  útiles: `id, project_id, parent_id, agent, model, title, cost,
  tokens_input, tokens_output, tokens_reasoning, tokens_cache_read,
  tokens_cache_write, time_created, time_updated`.
- Cada delegación `task` es un part `type=tool`, `tool=task` con
  `state.input = {description, prompt, subagent_type, task_id}` (el prompt trae
  el path absoluto del worktree de la corrida), `state.metadata =
  {parentSessionId, sessionId, model:{providerID,modelID}, truncated}`,
  `state.status` (`completed`/`error`/...) y `state.time = {start, end}`
  (epoch ms). Los hijos aparecen con `sessionId == child.id`.
- Parts de tools normales: `state = {status, input, output, title, metadata,
  time:{start,end}}`; `tool` ∈ {bash, edit, write, read, grep, glob, skill,
  task, todowrite, ...}; skill: `state.input.name`.
- `part.data` es JSON; `part` tiene `session_id`, `time_created`.
- Sesiones raíz del orquestador Mezclan varias corridas: la traza de una corrida
  se delimita por anclas + ventana temporal (abajo).

## Contrato CLI

```
scripts/trace-report.py <app> <slug> [-o OUT.html]
                        [--session SES_ID]... [--grep TEXTO]
                        [--db RUTA] [--max-bytes N] [--no-outputs] [--window-pad SEG]
scripts/trace-report.py --list-anchors <app> <slug>   # solo los ids de sesión ancla
```

- `<app>` se valida contra el registry (`app_config.py`); sin default de app.
- Ruta por defecto del worktree: `WT="$ROOT/state/worktrees/$APP_ID/$slug"`
  (`ROOT` resuelto por `__file__`; cwd-independiente).
- Anclas (union, dedupe):
  1. `--session SES_ID` repetible: sesión directa.
  2. `--grep TEXTO` repetible: `part` con `json tool='task'` cuyo `data LIKE
     %TEXTO%` → `state.metadata.sessionId` (hijo).
  3. Default (sin session/grep): `TEXTO = $WT` (el path absoluto del worktree
     aparece literal en los prompts de delegación).
- Cero anclas → exit 1, stderr: `trace-report: PENDIENTE — sin anclas para
  <app>/<slug> (probá --session <id> o --grep)`.
- DB ausente o no legible → exit 1 con `PENDIENTE` explícito (nunca inventar).
- Salida por defecto: `-o` requerido implícito → si falta `-o`, escribir en
  `$ROOT/state/reports/<app>/trace-<slug>.html` y crear el dir. stdout: sólo la
  ruta del archivo escrito (pipeable); el resto a stderr.

## Construcción de la traza

1. `sessions_anc = sesiones ancla`. Cierre hacia abajo: todos los descendientes
   por `parent_id` (BFS). Hacia arriba: cadena de `parent_id` hasta sesión sin
   padre (raíz orquestadora). `raíces = {raíz de cada ancla}`.
2. Ventana global: `t0 = min(time.time.start de los task-parts que anclaron las
   anclas; si --session: min part start del ancla)`, `t1 = max(part end de todo
   el cierre descendiente)`. `--window-pad` default 300 s, para captar los spans
   del orquestador (babysit, gh, commit) que rodean las delegaciones.
3. Spans incluidos:
   - Sesiones del cierre descendiente (raíz inclusive): **todos** sus parts
     tool.
   - Raíces que no son ancla: sólo sus parts tool con `time.start` en
     `[t0-pad, t1+pad]` (marcadas `windowed: true` en los datos, y el span raíz
     muestra esa etiqueta). Ancla-directa de una raíz (la tarea delegada por el
     usuario en la sesión raíz) no se filtra.
   - Parts no-tool (text/reasoning/step-*) se ignoran para el árbol, pero sus
     bytes se resumen: `tokens_*` y `cost` salen de las columnas de `session`.
4. Árbol: nodo = sesión (span contenedor, start=min part start, end=max part
   end, attrs: agent, model, title, cost, tokens); hijo directo del nodo sesión
   padre = el part `task` que lo invocó (attrs: description, subagent_type,
   status, time). Dentro de cada sesión, los demás parts tool son hijos en
   orden temporal. Un part `task` cuyo hijo NO está en el cierre (p. ej. sesión
   borrada) se marca `child missing`.

## Salida: JSON embebido + render

`trace.html`: `<script id="trace-data" type="application/json">{...}</script>`
con el árbol + summary, seguido de ~150 líneas de CSS+JS inline que lo renderizan.
El JSON incluye: `meta` {app, slug, wt, db, generated_at (arg `--now ISO`,
default actual), anchors, window}, `roots[]` con `spans` anidados, `summary`
{duración total, n sesiones por agent, n spans por tool, n errores (status
error), costos por agent (suma `session.cost`), skills usados (count por
`input.name`), tokens totales}.

Render (determinista, sin dependencias externas):
- Header: título `Corrida <app>/<slug>` + chips (duración, sesiones, costo,
  errores) y tabla resumen por agente (N, duración, cost, tokens, errores).
- Waterfall en árbol: filas indentadas por profundidad, barra horizontal
  posicionada contra la escala de la ventana (offset/duración en ms), color por
  kind: sesión-rol (coder azul, reviewers verde, interrogator amarillo,
  orquestador gris, otros violeta), tool bash gris, edit/write verde, task
  resaltado con `→ <agent>` link, rojo en status=error, borde punteado si
  `windowed`.
- Clic en fila → panel `pre` con attrs + input + output (JSON pretty si parsea,
  texto si no). Payload truncado a `--max-bytes` (default 8192) con aviso
  `… truncado (N bytes)`; `--no-outputs` reemplaza input/output por
  `<omitido (--no-outputs)>`.
- Tooltips nativos (title) con formato ISO de inicio/fin.

## Wiring al pipeline

- `.opencode/skill/factory-report/SKILL.md`: agregar `trace.html` a la lista de
  artefactos del run dir + la línea de comando `scripts/trace-report.py <app>
  <slug> -o state/reports/<run>/trace.html` con nota: se corre **al cerrar la
  corrida** (CI verde y post-merge); `state/` es gitignored; para compartir el
  archivo, `--no-outputs`.
- `.opencode/command/factory-build.md`: paso 7 (reporte) se extiende: "...y
  generá la traza de la corrida con `scripts/trace-report.py <app> <slug> -o
  <run dir>/trace.html`; si falla (sin db/sin anclas), registrar como PENDIENTE
  en el reporte, no bloquear el cierre".
- `AGENTS.md`: Comandos → `scripts/trace-report.py <app> <slug> [-o out]  # traza OTel-style de la corrida (HTML único, desde opencode.db read-only)`;
  en el bloque Automático "Al cerrar cada corrida" agregar: generar
  `trace.html` en el run dir (falla → PENDIENTE documentado).
- `README.md`: árbol de scripts + fila de `state/` (trace.html en los run dirs).
  No desbalancear fences mermaid.

## Tests (`scripts/test_harness.py`, stdlib, sin red ni db real)

`TestTraceReport`: db sqlite falsa en tmp con las tablas/columnas reales que
usa el script (crear `project, session, part` con los cols necesarios; sin
esquema completo). Fixture: raíz `build` (cost 0.5), task-part cuyo prompt
incluye `/x/wt/mi-slug` → hijo `coder` (cost 1.25), bash parts con tiempos
conocidos, un part con `status=error`, output grande para truncar. Casos:
1. happy: `--grep /x/wt/mi-slug --db tmpdb -o out.html` → exit 0; el JSON
   embebido parsea; `summary.agents == {"build":1,"coder":1}`; el nodo coder es
   hijo del task-span; el span error presente; stdout == ruta de out.html.
2. determinismo: dos corridas con `--now` fijo → bytes idénticos.
3. `--no-outputs`: no aparece el contenido del output (buscar el string).
4. truncamiento: `--max-bytes 50` → `truncado` presente.
5. sin anclas → exit 1 con `PENDIENTE` en stderr; db inexistente → exit 1.
6. `TestWorktreeFlowDocs` (extensión): `factory-report/SKILL.md`,
   `factory-build.md` y `AGENTS.md` mencionan `trace-report.py`; README también.

## Finish condition

```bash
python3 scripts/test_harness.py            # verde con los casos nuevos
uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
python3 scripts/trace-report.py mi-mesa household-measures-146 -o /tmp/trace-146.html   # genera y abre OK (probar que el HTML contiene 'household-measures-146' y spans coder)
```

## Decisiones documentadas

- Ancla = contenido del prompt del task-part (path del worktree), no el
  directorio de la sesión: verificado, `session.directory` es la raíz del
  proyecto y no distingue corridas.
- Raíces windowed por default: el orquestador atiende varias corridas por
  sesión; la ventana t0..t1+pad delimita "lo de esta corrida". `--session` fino
  para casos ajenos.
- Se incluyen outputs de tools en el HTML default: es un archivo local en
  `state/` (gitignored); para compartir, `--no-outputs`. Nunca se commitea.
- Sin OTLP/Jaeger: un HTML determinista sin infra cumple "ver la traza en el
  navegador"; el JSON embebido (`<script type="application/json">`) deja la
  exportación OTel como follow-up trivial.
- v1 omite mensajes de texto del LLM (parts no-tool) para no inflar; se puede
  agregar después.

## Fuera de alcance

- Plugin OTLP live, collector/Jaeger, run-stats.py/agent-stats.py (siguen
  pendientes como otra corrida), cambios en `.pi/**`, `config/**`, `.github/**`.
- Push/merge (los gestiona el orquestador).
