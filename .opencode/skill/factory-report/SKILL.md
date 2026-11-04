---
name: factory-report
description: Escribe el reporte de una corrida del factory en state/reports/<run>/ con fecha, commit y evidencias. Usar al cerrar /factory-build, /factory-review y /factory-deploy.
---

# Reporte de corrida del factory

Cada corrida genera un reporte reproducible en `state/reports/<run>/`:

```text
state/reports/<run>/
├── run.json          # meta: fecha, comando, refs, riesgo, estado
├── decisions.tsv     # decision trail: ts, fase, decisión, razón, evidencia, resultado
├── report.md         # reporte legible
└── trace.html        # traza OTel-style de la corrida (single-file, gitignored)
```

Donde `<run>` = `YYYYMMDD-HHMMSS-<slug>`.

## trace.html (traza de la corrida)

Al **cerrar la corrida** (CI verde y post-merge) generá la traza:

```bash
scripts/trace-report.py <app> <slug> -o state/reports/<run>/trace.html
```

HTML de archivo único con **tres vistas** (la Traza es **estilo Sentry** con un
único `<script id="view-js">` inline; además del JSON embebido `trace-data`):

- **Traza** (default): filas estilo Sentry (chevron, chip de color por
  categoría, barra en la pista temporal y duración al final), toolbar con
  `Search in trace`, checkboxes por categoría y `Expand all`/`Collapse all`, y
  un panel derecho con la duración, el badge de comparación contra el promedio
  (`avg_ms_by_tool`) y los Attributes agrupados (`span`/`sesión`/`delegación`/
  `input`/`output`/`diff`). Un solo archivo, sin recursos externos.
- **gaps comprimidos + minimapa + zoom**: los huecos de inactividad >
  `--max-gap` (default 120 s) se comprimen en la línea de tiempo virtual
  (cada uno ocupa `--gap-render` ms, default 20000) y se marcan con filas
  `⋯ … sin actividad`; un minimapa de 120 px con todas las barras y un viewport
  arrastrable, zoom con rueda/botones `+/−/⤢` (doble click resetea). Las
  duraciones y tooltips siguen en tiempo real.
- **turnos LLM**: spans `llm` por par step-start/step-finish (pareados por
  `message_id`), intercalados con los tools como hijos de la sesión, con
  tokens/cost/model del turno en el panel (`turno`). Chip de composición
  `Turnos: N · LLM: Xh Ym (Z% del tiempo activo) · Tools: W%` en el header, y
  columna **Tiempo LLM vs tools** (barra stacked LLM vs tools + `% · duración`)
  en Rendimiento. `--no-outputs` los mantiene.
- **Issues-first**: chip `Issues: N` en el header (click filtra a filas con
  errores, sus ancestros y los hijos de la sesión con error; segundo click o
  `Esc` sale) y badges `⚠N` por fila; el panel de un span con error se resalta y
  muestra el `output`.
- **diff unificado** para `edit`/`write` en los Attributes (líneas `+`/`-`,
  `@@`, n=3, truncado a 400 líneas); `--no-outputs` lo omite.
- **teclado**: `/` foco en search, `j`/`k` mueven la selección, `Enter` abre el
  panel, `Esc` cierra, `?` muestra la ayuda (`#kbd-help`). Deep-links
  `#span-<id>`, `#trace/<id>`, `#issues`, `#perf`, `#diagram`.
- **dark automático** vía `@media (prefers-color-scheme: dark)` (paleta
  aclarada WCAG AA; la light queda igual).
- **Rendimiento**: tabla por agente con invocaciones, sesiones,
  `wall_ms`/`own_ms` (tiempo propio sin esperar hijos), costo, tokens y errores,
  más barras SVG de `own_ms`/costo y el mix de herramientas con patrón
  (sólido/rayado/punteado) además de color.
- **Diagrama**: grafo de delegación por capas (contadores `×N` cuando se
  re-invoca una sesión) y swimlanes de sesiones con las rondas `R1..RN` sobre
  una escala en minutos; ambos con equivalente en texto dentro de `<details>`.

La traza muestra qué ejecutó cada subagente (comandos, archivos) y cada
delegación del orquestador (`R1..RN` con duración y estado) en el Diagrama y en
la Narrativa.

Con `--no-js` se emite la vista v3 (sin el script de vista; todo el detalle en
`<details>`), para entornos sin JavaScript.

La paleta es **WCAG 2.x AA** (contraste verificado; ningún estado se comunica
sólo por color). Se lee **read-only** de
`~/.local/share/opencode/opencode.db`. `state/` es gitignored: el HTML no se
commitea. Para compartir el archivo sin payloads, usá `--no-outputs`. Si falla
(sin db o sin anclas), registralo como PENDIENTE en `report.md`; no bloquea el
cierre.

## run-stats (cierre de corrida)

Al cerrar la corrida corré `scripts/run-stats.py <app>` (o `--all`) para leer el
estado agregado de `state/reports/**`: volumen por `status`, riesgo, rework del
loop de CI (`babysit*.json`), autonomía (`decisions.tsv`) y waste. `--json`
emite el mismo objeto. Es el **input del `factory-correct`** al cerrar: sin
números no hay clase de error que corregir. Para costos/tokens por agente y uso
de skills, `scripts/agent-stats.py --days N` lee `opencode.db` read-only y sólo
emite agregados (nunca prompts ni comandos).

## decisions.tsv (decision trail)

Una fila por decisión tomada durante la corrida (qué ruta se eligió, qué
alternativa se descartó y por qué, qué check frenó qué). Columnas tab-
separadas:

```
ts	fase	decisión	razón	evidencia	resultado
```

- `evidencia` = puntero verificable (comando, SHA, path de archivo, PR).
- La corrida que se reanuda (o la audita otra sesión/persona) lee acá el
  "por qué", no en el chat.

## run.json (canónico)

```json
{
  "run": "20260916-101500-add-recipes",
  "command": "/factory-build",
  "date": "2026-09-16T10:15:00Z",
  "repo_sha": "<sha de main>",
  "branch": "factory/<slug>",
  "pr": "<número o null>",
  "risk": "low|medium|high",
  "status": "green|needs_work|blocked"
}
```

## report.md

- Qué se pidió (spec o issue).
- Qué se implementó (archivos, decisiones con puntero a la fila del trail).
- Checks ejecutados y resultados (comandos y salida).
- Evidencia de CI (runs, checks) y health.
- Pendientes y bloqueos.
- **Attention** (última sección, la primera que se lee al retomar): las
  decisiones y filas del trail que merecen escrutinio humano — cambios
  arriesgados, decisiones tomadas con datos parciales, skips dudosos. Con
  puntero a la fila `decisions.tsv` correspondiente. Auditar decisiones,
  no releer la corrida.
- No incluir secretos ni contenido de telemetría.

## Reglas

- Nunca declarar resultados de comandos no ejecutados.
- Diferenciar "implementado" de "verificado con proveedor/entorno real".
- Los reportes son evidencia: si no hay acceso a un dato, marcarlo PENDIENTE.