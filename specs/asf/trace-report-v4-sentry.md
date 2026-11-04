# Spec v4: trace-report — vista estilo Sentry (waterfall por fila + panel de detalle + buscador)

Fecha: 2026-10-09 · App: `asf` · Rama: `factory/trace-sentry` (base `0d43e45`)
Referencia visual: screenshot de Sentry "Trace viewer" aportado por el owner:
árbol de spans con chevrons, barra de duración por fila con etiqueta al final,
clic → panel derecho con nombre+duración+badge de comparación contra el
promedio y **Attributes agrupados** (span, browser, environment…), buscador
"Search in trace", header con Spans/Age/Root Duration.

## Cambio de regla mayor (explícito, consensuado con el owner)

v1–v3 eran "single-file **sin JS**". Para lograr esta interacción se permite
**un único `<script>` de vista inline** (además del `<script id="trace-data">`
JSON). Condiciones no negociables:

- Un solo archivo, sin recursos externos (nada de `<link>`, CDN ni fetch).
- El JS sólo reordena/muestra lo ya embebido: **sin `eval`, sin `innerHTML`
  con datos** (render por `textContent`/atributos), payloads del JSON nunca se
  interpretan como código (se mantiene el escape `<`→`\u003c` de v2).
- **Determinismo**: mismos datos + `--now` fijo → bytes idénticos (el JS es
  texto estático fijo, no genera contenido nuevo; todo lo dinámico es CSS/
  visibilidad).
- Flag `--no-js`: emite la v3 actual (sin el script, todo `<details>` como
  hoy). El default pasa a ser la vista nueva.
- Tests: `h.count("<script") == 2` con `id="trace-data"` y `id="view-js"`.

## Estructura de la vista "Traza" (tab por default pasa a ser Traza;
Rendimiento y Diagrama siguen como en v2/v3)

### Fila-span estilo Sentry (por span, una fila `<div class="row">`)
- Columna izquierda ~50%: chevron (si tiene hijos) + **chip de color por
  categoría** + etiqueta `tool — título` o `<agent> (sesión)`; indentación con
  guías verticales (borders por profundidad, como Sentry).
- Columna derecha: pista temporal (`class="track"`) + barra posicionada
  `[start,end]` sobre la ventana global, coloreada por categoría (paleta v2
  WCAG), y **duración como texto al final de la fila** (`1.28s`, `943.60ms` —
  formato como Sentry: ms si <1s, s con 2 dec si ≥1s, m/h para sesiones).
- Fila clickeable (`<a href="#span-<id>"`), la seleccionada queda resaltada
  (CSS `:target`/clase gestionada por el JS).
- Estado: `✕` rojo (error) / `✓` ok como hoy, nunca sólo color.

### Panel derecho "Span" (el corazón de Sentry)
- Fijo a la derecha (o superpuesto) cuando hay selección; "× Close" la limpia.
- Contenido (desde el JSON embebido, por `data-span-id`):
  - Header: `tool — título` + **duración grande** + badge
    `<pct>% más rápido/lento que el avg` (comparado contra el promedio de
    spans de la MISMA categoría/tool — precomputado en Python como
    `avg_ms_by_tool`, embebido, el JS sólo formatea).
  - `ID: <span_id>` con botón copiar (JS `navigator.clipboard`, fallback:
    seleccionable).
  - Sección **Attributes** con buscador propio (filtra keys client-side) y
    grupos `<details open>` por dominio:
    - `span`: {tool, status, callID, t_inicio/t_fin ISO, windowed?}
    - `sesión` (si el span es sesión): {agent, model, title, cost, tokens_*,
      wall/own_ms}
    - `delegación` (task): {k, to_agent, to_session, description, dur, inv?}
    - `input` / `output`: payloads truncados (igual que v1) en `<pre>`; con
      `--no-outputs` se muestran counts.
- Con `--no-js`: el panel no existe; el detalle queda como `<details>` inline
  v3 (comportamiento actual, intacto).

### Header de la vista Traza (chips tipo Sentry arriba a la derecha)
`Spans: N · Sesiones: N · Root Duration: Xs · Costo: $Y · Errores: Z ·
Ventana: t0..t1` — precomputados en Python (summary existente), render server-side.

### Toolbar
- `Search in trace`: `<input>` que filtra filas por etiqueta (JS: oculta
  no-coincidentes y conserva ancestros visibles; resalta coincidencias con
  `<mark>` vía textContent-safe — si es difícil, sólo filtrar sin resaltar).
- Checkboxes por categoría (sesión, task, bash, edit/write/read/glob/grep,
  skill, web, memory, otros) → ocultan filas (JS; `--no-js`: sin toolbar).
- `Expand all` / `Collapse all` (chevrons).

## Datos nuevos a embeber (Python, deterministas)
- `avg_ms_by_tool`, `row_count`, y `id` único por span (existing session id /
  callID; generar `part-<hash>` si falta).
- Cada span del JSON con `label` (etiqueta visible ya formateada por Python:
  `tool — título≤N`), `dur_text` formateada, `category`.

## Tests (unittest; el JS no se ejecuta en tests — se prueba el HTML)
- `row_count` == filas `.row` presentes; `id="view-js"` y `id="trace-data"`
  únicos; `count("<script") == 2`.
- Panel template: existe `div#span-panel` con `data-attrs` (o el JSON contiene
  `avg_ms_by_tool`); cada fila clickeable tiene `href="#span-` y `data-span-id`.
- Badge avg: `avg_ms_by_tool` presente y >0 para el tool del fixture.
- `--no-js`: `count("<script")==1` (sólo JSON) y `<details>` v3 presentes
  (regresión cubierta).
- Search/toolbar/categorias: inputs `type="search"` + checkbox por categoría
  presentes (assert estático).
- Determinismo byte-a-byte con `--now` en ambas variantes (js y no-js).
- Enforcer docs: factory-report SKILL menciona "estilo Sentry" y `--no-js`.

## Docs
- `factory-report/SKILL.md`: la vista Traza es estilo Sentry (panel de detalle,
  search, filtros); `--no-js` para entorno sin JS; sigue single-file/offline.
- README/AGENTS: no hace falta (descripción genérica ya dada).

## Finish condition
```bash
python3 scripts/test_harness.py
uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
python3 scripts/trace-report.py mi-mesa fix-worker-empty-plans --session ses_ee1067a60ffeWUBrMbnIkwe7b3 -o /tmp/sentry-style.html
grep -c 'data-span-id' /tmp/sentry-style.html   # >50
grep -c 'avg_ms_by_tool' /tmp/sentry-style.html # 1+
python3 - <<'EOF'
import re
h=open("/tmp/sentry-style.html").read()
assert h.count("<script")==2 and 'id="view-js"' in h and 'Search in trace' in h
EOF
python3 scripts/trace-report.py mi-mesa fix-worker-empty-plans --session ses_ee1067a60ffeWUBrMbnIkwe7b3 --no-js -o /tmp/nojs.html
grep -c '<details' /tmp/nojs.html
```

## Fuera de alcance
- Logs/Issues/Replays de Sentry, zoom/pan de la pista, export OTLP, dark
  theme (sólo si es trivial: `prefers-color-scheme` con la misma paleta AA).
