# Spec v6: trace-report — spans de turno LLM (el diferencial de trazas de agentes)

Fecha: 2026-10-09 · App: `asf` · Rama: `factory/trace-v6` (base `origin/factory/trace-v5` `831f34f`; PR **stacked sobre #30** → #29)
Quinta iteración (v1 HTML traza; v2 rendimiento+diagrama+WCAG; v3 narrativa delegación; v4 Sentry-style; v5 gaps/issues/diff/teclado/dark). Este es el paso LangSmith-style que pedimos como v6.

## Forma de datos reales (sondeada hoy, no asumir otra)

- `message` (role assistant): `data = {parentID, role, mode, agent, variant, path, cost, tokens:{input,output,reasoning,cache{write,read}}, modelID, providerID, time:{created}}` — `time` sólo tiene `created`.
- `part`: `type ∈ {tool, step-start, step-finish, text, reasoning, patch, compaction, file}`; `step-start = {snapshot,type}`; `step-finish = {reason, snapshot, tokens{...}, cost, type}` (reason: `stop`/`tool-calls`/`error`/...). Ambos tienen `time_created`/`time_updated` (epoch ms reales). Un mensaje asistente con N API-calls tiene N pares step-start/step-finish por su `message_id`.

## Qué construir

### 1. Spans `llm` por sesión (Python, deterministas)
- Leer `part` `step-start`/`step-finish` de todas las sesiones del árbol, agrupar por `message_id`, ordenar cada grupo por `(time_created, type, id)` y **parear** k-ésimo start con k-ésimo finish *siguiente* (start sin finish posterior → span abierto: fin = time_updated, `open: true`; finish huérfano → ignorar).
- Span llm = {`id`: `llm-<message_id>-<k>` (k entero, estable), `start`,`end` reales, `v_start`/`v_end` según la compresión de gaps de v5, `category: "llm"`, `label`: `LLM · <modelID>` (modelID corto: último segmento tras `/`), `dur_text` formateado, `cost`, `tokens` (input/output/reasoning/cache{write,read}) y `reason`, y `model: {providerID, modelID}`, `message_id`, `agent` (el de la sesión)}.
- Metadata del mensaje (`message.data` para ese message_id): `cost`/`tokens` del mensaje NO reemplazan los del step-finish (por-turno); agregar al span `msg_cost`, `msg_tokens`, `variant`, `mode` si existen.
- Los spans llm se **intercalan cronológicamente** como hijos del nodo-sesión (junto con los tool-parts, ya ordenados por start), con el badge de categoria `llm` gris claro (light `#555555`, contrast 7.46; dark `#9AA6B8` — verificar ≥3 vs `#141821`, ajustar y documentar).
- `--no-outputs`: los spans llm **se mantienen** (tokens/cost/model son agregados, no payloads).

### 2. Chip de composición (header Traza, junto a Issues)
`Turnos: N · LLM: Xh Ym (Z% del tiempo activo) · Tools: ...% · (activo = t1−t0−gaps)` — precomputado en Python (`summary.time_split: {llm_ms, tool_ms, gap_ms, window_ms, turns}`); con `--no-js` igual se imprime el chip estático.

### 3. Rendimiento (v2) ampliado
- `by_agent` agrega: `llm_ms`, `tool_ms`, `turns`, `llm_share_pct`.
- Columna nueva "Tiempo LLM vs tools": mini-barra stacked 2 segmentos (llm gris vs tools color-agente) + texto `"34% · 12m"` (nunca sólo color), con patrón sólido/rayado como tool_mix.

### 4. Panel de detalle
Span llm → Attributes: `turno` {model, provider, reason, open?, tokens desglosados, cost} + `sesión` (igual que hoy). Sin secciones input/output (no existen).

### 5. Teclado/deep-link/dark: heredan v5 (categoria `llm` entra al filtro por checkbox y a issues-only si su sesión tiene errores — el span llm mismo nunca es `error`; `reason=="error"` muestra badge `✕` pero self_errors sigue contando parts tool).

### 6. Performance de generación
El worker real tiene ~700 turnos → +700 spans: medir tiempo de script (objetivo < 2 s) y tamaño (worker < ~30 MB). Si `row_count` supera 20k, cortar con nota (no esperar en v6: informar en docstring/decisiones).

## Tests (fixture v4/v5 + mensajes/step-finish fake)
1. Pairs: fixture con 1 mensaje asistente, 2 step-start + 2 step-finish (times intercalados con un tool) → JSON: 2 spans `category:"llm"` ordenados con el tool por start; `llm-<msg>-0/1`; el último span `open: true` si se deja un start sin finish (caso extra).
2. Chips: `summary.time_split.turns==N`, `llm_ms>0`, chip `Turnos:` en HTML (js y no-js).
3. `by_agent` del agente del fixture trae `llm_ms`/`tool_ms`/`turns` y la columna stacked aparece con `llm_share_pct`.
4. Panel attrs `turno` contiene modelID del fixture.
5. Dark/contrast: token llm dark ≥3 vs `#141821` (test unitario con la misma función).
6. `--no-outputs`: spans llm presentes; determinismo byte-a-byte `--now` js+no-js.
7. Docs: SKILL factory-report menciona "turnos LLM" + "Tiempo LLM vs tools".

## Docs
- `factory-report/SKILL.md`: bullet v6 (spans de turno LLM, chip de composición, columna LLM vs tools).

## Finish condition
```bash
python3 scripts/test_harness.py
uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
python3 scripts/trace-report.py mi-mesa fix-worker-empty-plans --session ses_ee1067a60ffeWUBrMbnIkwe7b3 -o /tmp/v6-worker.html
python3 scripts/trace-report.py mi-mesa household-measures-146 -o /tmp/v6-146.html
python3 - <<'EOF'
import json, re
for f in ("/tmp/v6-worker.html","/tmp/v6-146.html"):
    h=open(f).read(); d=json.loads(re.search(r'<script id="trace-data" type="application/json">(.*?)</script>',h,re.S).group(1))
    ts=d["summary"]["time_split"]
    assert ts["turns"]>0 and "Turnos:" in h
    print(f, "| turns:", ts["turns"], "| llm_ms:", ts["llm_ms"], "| tool_ms:", ts["tool_ms"], "| llm%:", round(100*ts["llm_ms"]/max(1,ts["window_ms"]-ts["gap_ms"])))
EOF
du -h /tmp/v6-worker.html   # reportar tamaño
```

## Fuera de alcance
- Texto de reasoning/contenido de turnos (vive en db; no va al HTML), OTLP, multi-run, paneles LangSmith de calidad/eval.
