# Spec v5: trace-report — gaps comprimidos + issues-first + diffs unificados + teclado/dark

Fecha: 2026-10-09 · App: `asf` · Rama: `factory/trace-v5` (base `origin/factory/trace-sentry` `469ea94`; PR **stacked sobre #29**, base automático a main cuando #29 fusee)
Continúa v1–v4 (`specs/asf/trace-report*.md`). Objetivo: llevar el viewer a "world class" (comparación explícita del owner con Sentry + LangSmith).

## 1. Compresión de gaps de inactividad + minimapa + zoom (el problema del "14 h")

**Python (datos deterministas):**
- Nuevas claves de eventos = todos los `start`/`end` reales de spans dentro de la ventana global, ordenados; cada hueco entre claves consecutivas > `--max-gap` (default **120** s; flag en CLI) se **comprime**: en la línea de tiempo *virtual* ocupa exactamente `--gap-render` ms (default **20000**).
- Por span embeber `v_start`,`v_end` (ms virtuales) además de `start`/`end` reales (intactos). Eje/track/barras usan tiempo virtual; **tooltips y `dur_text` siguen reales**.
- `meta.gaps = [{t0,t1,virtual_at,label:"⋯ 3h 42m sin actividad"}]` ordenados.
- Render Traza: entre filas, una fila `class="gap-row"` (alineada al track en `virtual_at`) con el label; en `--no-js` igual (es HTML estático).

**JS de vista (view-js):**
- **Minimapa**: svg estático de 120px con todas las barras (datos ya embebidos) + `<rect id="mm-viewport">` = viewport actual. Arrastrar/click en minimapa mueve el viewport.
- **Zoom**: rueda sobre el área de tracks (o botones `+/−/⤢` reset) ajusta `px_per_vms` global y re-renderiza posiciones/left/width de barras y del viewport (transform de estilo en el contenedor del track; sin tocar datos). Doble click = reset.
- Con `--no-js`: sin minimapa/zoom, pero gaps ya comprimidos en el layout estático (v_scale fijo = ancho total).

## 2. Issues-first (el killer de Sentry)

- Python precomputa por span: `self_errors` (sus parts propios status=error) — ya hay `errors` por agente; ahora **por fila**: badge `⚠N` rojo `class="err-badge"` junto a la etiqueta (sólo si N>0).
- Header Traza: chip **"Issues: N"** (`id="issues-chip"`, aria). Click → modo issues-only: oculta filas sin `self_errors>0` **ni** descendientes con errores (ancestors de errores visibles). Segundo click / `Esc` sale.
- Panel de span con `status=error`: payload `output` expandido por defecto y borde `#B3261E`; badge "✕ error".
- `--no-js`: chips/badges igual se imprimen (sin el filtro).

## 3. Diff unificado para edit/write (panel Attributes)

- Python: en task/spans de tool `edit` y `write`, parsear `state.input` (campos reales: `filePath`, `oldString`, `newString`; `write`: `filePath`/`content`) y generar `attrs.diff = [líneas unified]` con `difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile=path, tofile=path, n=3)`; `write`: contra archivo vacío. Guardar `diff` en el span (truncar a 400 líneas con `…diff truncado`).
- Render panel: bloque `class="diff"` con líneas `+` verde `#14682F`, `-` rojo `#B3261E`, resto neutro, en fuente mono (contrast ≥4.5 AA ya verificado v2).
- `--no-outputs` ⇒ sin `attrs.diff` (sólo counts). Determinismo: difflib orden estable.

## 4. Teclado, deep-link de filtros y dark/light

- **Teclado** (view-js, `keydown`): `j`/`k` mueven la selección entre filas **visibles** (scrollIntoView); `Enter` abre panel de la fila bajo selección; `Esc` cierra panel / issues-only; `/` focus en search; `?` (shift+/) abre `<details id="kbd-help">` (overlay estático con la tabla de teclas).
- **Deep-link**: hash `#span-<id>` sigue = panel abierto; extender a `#trace/<id>`, `#issues` y `#perf`/`#diagram` (tabs) — parsear al load y en `hashchange` (set de formatos pequeño, retrocompatible con v4).
- **Dark/light**: tokens CSS en `:root` (light, igual que hoy) + `@media (prefers-color-scheme: dark)` con variante: bg `#141821`, surface `#1D2430`, texto `#E6EAF2` (≥12:1), paleta aclarada para ≥3:1 sobre dark: azul `#4C9FD4` (naranja `#E8762D` sobre #141821 ratio ~5.3), verde `#2FB483` (≈6.9), morado `#A888D8` (≈6.4), celeste `#5FA8CE` (≈5.5), rojo error `#F0796F` (≈7), ok `#3FBF7F` (≈7). Test de contraste unitario sobre los valores dark también.

## Datos/embebido

Nuevas claves JSON: `v_start/v_end`, `meta.gaps`, `self_errors` por span, `attrs.diff` (edit/write). `avg_ms_by_tool`, `row_count` intactos. `trace-data` sigue siendo el único script de datos; `view-js` sigue siendo texto estático (determinismo byte-a-byte con `--now`).

## CLI

Nuevos flags: `--max-gap SEC` (default 120), `--gap-render MS` (default 20000). Resto intacto.

## Tests (unittest, db fake del fixture v4)

1. **Gaps**: fixture con dos spans separados >120 s → `meta.gaps` no vacío; spans tienen `v_start`; en HTML existe `gap-row` con `sin actividad`; dos spans dentro de un gap virtual (fixture: span de 60 s real sigue ancho proporcional virtual==real para no-err).
2. **Issues**: span con `status=error` → `self_errors>=1` en JSON; HTML tiene `err-badge` e `id="issues-chip"`; el texto del chip coincide con el conteo.
3. **Diff**: fixture edit con `oldString`/`newString` → JSON `attrs.diff` con líneas `+`/`-` correctas; en `--no-outputs` NO hay `attrs.diff`; y HTML contiene `class="diff"` (js) o pre v3 (`--no-js` no aplica diff, está ok sin bloque).
4. **Teclado/hash/dark**: `view-js` contiene `case"j"`/`"k"` y `hashchange`; CSS contiene `prefers-color-scheme: dark` y el bloque `kbd-help` existe.
5. **Contraste dark**: `wcag_contrast(token, "#141821") >= 3.0` para los tokens gráficos de la lista; `>= 4.5` texto/ok/error.
6. **Determinismo**: dos corridas `--now` fijo byte-idénticas (js y no-js) — cubrir que `difflib`/gaps no introducen no-determinismo (dict order!).
7. Enforcer docs: SKILL de factory-report menciona "gaps comprimidos"/"Issues" y `--max-gap`.

## Docs
- `factory-report/SKILL.md` (sección trace.html): 3 bullets (gaps+minimapa+zoom, issues-first, diff unificado; teclado `/ ? j k Enter Esc`; dark automático). Flag nuevo `--max-gap` documentado.
- AGENTS/README: no (genérico).

## Finish condition
```bash
python3 scripts/test_harness.py
uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
python3 scripts/trace-report.py mi-mesa fix-worker-empty-plans --session ses_ee1067a60ffeWUBrMbnIkwe7b3 -o /tmp/v5-worker.html
python3 scripts/trace-report.py mi-mesa household-measures-146 -o /tmp/v5-146.html
python3 - <<'EOF'
import json, re
for f in ("/tmp/v5-worker.html","/tmp/v5-146.html"):
    h=open(f).read(); d=json.loads(re.search(r'<script id="trace-data" type="application/json">(.*?)</script>',h,re.S).group(1))
    assert "gaps" in d["meta"], f
    print(f, "| gaps:", len(d["meta"]["gaps"]), "| err-chip:", 'id="issues-chip"' in h, "| minimapa:", 'id="mm-viewport"' in h, "| dark:", "prefers-color-scheme" in h)
EOF
grep -c 'class="diff"' /tmp/v5-146.html   # >0 (hubo edits)
```

## Fuera de alcance (v6 = próximo PR)
- Spans de turno LLM (step-start/step-finish, time-to-first-token, share thinking vs tool) — spec propia.
- Live/streaming, OTLP, multi-run.
