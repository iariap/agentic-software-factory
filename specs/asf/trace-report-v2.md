# Spec v2: trace-report — rendimiento por agente + diagrama de ejecución + WCAG

Fecha: 2026-10-09 · App: `asf` · Rama: `factory/trace-report` (mismo PR #26; itera la v1 de `specs/asf/trace-report.md`, que sigue vigente para anclas/CLI/datos)

## Feedback del owner (qué cambia)

La v1 es un microscopio: sólo spans por micro-tarea. Falta:

1. **Cómo performan los agentes** (duración propia vs esperando, costo, tokens,
   errores/reintentos, mix de tools, cuántas veces fueron invocados).
2. **Un diagrama para ver cómo se ejecutó la corrida** (estructura de
   delegación + rounds de iteración en el tiempo), no sólo la lista de spans.
3. **Más visual, con colores WCAG 2.x** (contraste AA verificado, nunca
   depender sólo de color).

## Requisitos

### Tabs (CSS-only, sin JS): radios + `:checked`
- **Rendimiento** (default), **Diagrama**, **Traza** (el waterfall v1 sigue,
  con los tool-parts de cada sesión dentro de `<details>` colapsado por
  default — la fila de sesión siempre visible con sus badges).
- Labels `<label>` asociados, foco visible (`outline: 2px solid #005F9E`,
  contrast 6.7:1), `lang="es"`.

### Summary ampliado (datos embebidos en `trace-data`, calculados en Python)
Por agente (agregando sus sesiones):
- `invocations` (N veces que aparece como ancla/hija — task-parts que lo
  llamaron, dedupe por session id), `sessions`, `wall_ms` (suma de duraciones
  de sesiones), `own_ms` (wall − Σ duración de task-spans anidados dentro de
  esas sesiones → tiempo propio, no esperando hijos), `cost`, `tokens`
  (input/output/reasoning por agente), `errors` (status=error), `tool_mix`
  (count por tool), `skills_usados`.
- `rounds`: lista `{k, t_ms, description, child_session}` de los task-parts
  que anclaron desde la raíz — marca las rondas de iteración en el tiempo.
- `palette`: dict token→hex embebido (fuente única, testeable).

### Vista Rendimiento
- Tabla de agentes (caption, `scope="col"`) con las columnas de arriba; los
  números SIEMPRE como texto (las barras son decoración con `aria-hidden`).
- Bajo la tabla: barra horizontal SVG por agente para `own_ms` y para `cost`
  (escala = máx del conjunto), con el valor rotulado en texto.
- Barra stacked del `tool_mix` por agente (segmentos con patrón distinto:
  sólido/rayado/punteado además del color; leyenda textual).

### Vista Diagrama
- **Grafo de delegación** SVG por capas: columna por profundidad (raíz →
  anclas → descendientes), nodo = sesión con etiqueta `agent (id corto)` y
  bordes `build → coder ×2` con contador si la misma sesión llamó varias
  veces a la misma sesión hija (caso real: coder re-invocado). Texto en
  nodos/bordes; colores del palette; `<title>` + `<desc>` en el svg,
  `role="img"`, y debajo una `<details>` con la lista equivalente en texto
  (accessibilidad: la información existe sin SVG).
- **Swimlanes** SVG: una fila por sesión (ordenadas por `start`, indentación
  visual por profundidad vía ancho/offset de etiqueta), barra = [start,end]
  coloreada por rol, líneas verticales punteadas en `rounds.t_ms` etiquetadas
  `R1, R2...`; escala temporal abajo en minutos. Debajo, leyenda de roles
  (cuadrito + nombre + patrón) y `<details>` con la tabla texto
  sesión→[inicio, fin, duración].
- Determinismo: layout 100% derivado de los datos (sin random, sin JS).

### Paleta WCAG (tokens verificados con la fórmula de contraste; enforcer abajo)
- Fondo `#FFFFFF`, surface `#F6F6F6`, texto `#1A1A1A` (17.4:1).
- Gráficos (≥3:1 sobre blanco, todos verificados hoy):
  azul `#0072B2` (5.19 — build), naranja `#D55E00` (3.87 — coder), verde
  `#007B5E` (5.26 — reviewer-*), morado `#7A4FA3` (6.04 — interrogator/delegate),
  celeste `#2E86AB` (4.11 — explore/general/otros).
- Semánticos (también aptos texto ≥4.5): error `#B3261E` (6.54) + icono `✕`,
  ok `#14682F` (6.88) + `✓`, foco `#005F9E` (6.71).
- Regla de diseño: ningún estado se comunica sólo por color (icono/texto
  siempre); barras tienen patrón en tool_mix.

### Tests (reemplazan/extenden `TestTraceReport`; mismo fixture db fake, 2+
rondas: un mismo coder re-invocado 2 veces desde la raíz)
- Contraste: función `wcag_contrast(fg,bg)` unit-testeada contra valores
  conocidos (p. ej. `#7A4FA3` vs `#FFFFFF` ≈ 6.0±0.1) + assert: cada token
  gráfico ≥3.0 vs `#FFFFFF` y cada token de texto ≥4.5; el `palette` embebido
  en `trace-data` coincide con los tokens.
- `Rendimiento`: JSON tiene `by_agent` con `own_ms < wall_ms` (coder hijo del
  task-span), `invocations`, `tool_mix` correctos sobre el fixture; `rounds`
  con k=1..N y t_ms ascendentes.
- `Diagrama`: HTML contiene los dos `<svg role="img">`, los contadores `×2`
  del re-invoke y N líneas `R1..RN`; existe la lista texto equivalente dentro
  de `<details>`.
- `Traza`: tool-parts envueltos en `<details>` (regex sobre el HTML), sesión
  visible fuera del details.
- Tabs sin JS: no hay `<script>` además del JSON embebido (`trace-data`) —
  assert: `h.count("<script") == 1`.
- Determinismo byte-a-byte con `--now` fijo (conservar el caso v1) y
  `--no-outputs`/truncado siguen intactos.

### Docs
- `.opencode/skill/factory-report/SKILL.md`: actualizar la sección `trace.html`
  (tres vistas: Rendimiento / Diagrama / Traza; paleta WCAG AA; sin JS).
- `README.md` y `AGENTS.md`: una línea donde mencionan trace-report ("vista de
  rendimiento por agente y diagrama de ejecución").
- Spec v2 versionada como este archivo; la v1 queda vigente para CLI/anclas.

## Finish condition

```bash
python3 scripts/test_harness.py        # todo verde (incluye tests nuevos)
uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
python3 scripts/trace-report.py asf trace-report -o /tmp/trace-self-v2.html
python3 scripts/trace-report.py mi-mesa household-measures-146 -o /tmp/trace-146-v2.html
# ambos abren con 3 tabs; en Rendimiento se ve coder re-invocado con
# invocations>=2 y own_ms<wall_ms; grep 'R1' presente en Diagrama.
```

## Fuera de alcance
- Gráficos interactivos/zoom (sin JS por diseño), export PNG, OTLP.
- run-stats.py/agent-stats.py cross-run (siguen como corrida separada).
