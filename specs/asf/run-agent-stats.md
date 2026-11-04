# Spec: run-stats.py + agent-stats.py — métricas cross-corrida del harness y de agentes/skills

Fecha: 2026-10-09 · App: `asf` · Rama: `factory/run-agent-stats` (base main `0d43e45`)
Scope congelado en el hilo original del owner (antes del desvío a trazas): *"métricas de la ejecución del harness y de los skills"*. Dos scripts stdlib, read-only, sin red, salida determinista.

## Datos reales (verificados hoy; no asumir otro layout)

- Reports conviven en **dos layouts**: `state/reports/<app>/<run>/` (actual, p. ej. `mi-mesa/20261009-023131-issue139/`) y `state/reports/<run>/` legacy (p. ej. `2026-09-18-radio-contest-mvp/`, `20261009-102452-worktree-implementations/`). `<run>` con dos naming schemes (`YYYY-MM-DD-slug` y `YYYYMMDD-HHMMSS-slug`).
- `run.json` con schemas variables: campos vistos `run, command, date, app, repo_sha, head_sha, branch, pr, risk, ci, status, merged_at, merge_sha, pr_v2, trace`; puede faltar cualquiera. Algunos runs sóó `report.md`/`decisions.tsv`/`babysit*.json`.
- `babysit*.json`: `{head_sha, mergeable, checks, exit, polls?}`; un archivo por intento/estado (puede haber varios `babysit-vN.json`).
- `decisions.tsv`: columnas `ts fase decisión razón evidencia resultado` (histórico con menos columnas); resultado ∈ ok/falló/discartado/pivot/…
- `opencode.db` (SQLite, leer con `file:...?mode=ro` `timeout=3`): tablas `project(worktree)`, `session(id,parent_id,agent,model,directory,cost,tokens_input/tokens_output/tokens_reasoning/tokens_cache_read/tokens_cache_write,time_created,time_updated)`, `part(data JSON: tool, state.status, state.input{subagent_type|name}, state.time{start,end})`. Sin contenido en la salida: sólo agregados.

## scripts/run-stats.py

`scripts/run-stats.py [app|--all] [--since YYYY-MM-DD] [--json]` (default: `--all`, tabla markdown a stdout; `--json` el mismo objeto).

Desde `state/reports/**` (ambos layouts; app inferida: `<app>/` en path si existe, si no `run.json.app`, si no `(legacy)`):

- **Volumen**: N runs por app × `status` (valores vistos: merged, awaiting-human-merge, in_progress, cancelled, …; `sin-schema` para dirs sin run.json parseable → contados, nunca rompe).
- **Riesgo**: distribución low/medium/high (campo `risk`) + % mergeados (status contiene "merged").
- **Loop CI**: por run con ≥1 `babysit*.json`: primer/último exit y sha, nº de shas distintos (proxy de rework), nº de archivos babysit (proxy de reintentos); agregado: media de shas/run.
- **Rework/autonomía**: filas de `decisions.tsv` por `resultado` (ok vs falló/discartado/pivot, match tolerante acentos/case); % decisiones "no-útiles".
- **Waste**: runs con status in_progress/awaiting + runs con `pr` y status sin merge (PRs abiertos/huérfanos).
- **Edad**: días desde `date`/`merged_at` si existen (`--since` filtra por `date` o por mtime del dir).

Orden determinista (app, run name). Sin red, sin gh (eso lo hace `ci-status`; nota en help).

## scripts/agent-stats.py

`scripts/agent-stats.py [--days 30] [--app asf] [--project <path-worktree>] [--json]` (default `--app asf` → `project.worktree` del registry; `--project` arbitrary; salida markdown/stdout).

Desde `opencode.db` **read-only**, ventana `--days` sobre `session.time_created` (epoch ms):

- **Por `session.agent`** (NULL → `(raíz)`): N sesiones, sesiones-hijas (parent no null), `dur_ms` (Σ time_updated−time_created, cap por sesión a 24 h para no contar sesiones huérfanas; documentado), costo Σ, tokens Σ (input/output/reasoning/cache).
- **Delegaciones**: task-parts en ventana → por `subagent_type`: N, `state.status` ok vs error; duración de task (`time.end−time.start`).
- **Skills**: parts `tool=skill` → count por `state.input.name`.
- **Errores**: parts por `tool` con `status=error` (top 10) y total; ratio err/spans.
- **Por día** (opcional `--daily`): matriz día × agente (costo) y día → total sesiones.
- Nada de contenido: ni prompts, ni comandos, ni paths de sesión (sólo agregados).

## Enforcer de docs + wiring

- `AGENTS.md` (Comandos): dos líneas nuevas.
- `README.md` (árbol scripts): dos líneas.
- `.opencode/skill/factory-report/SKILL.md`: al cerrar corrida, `scripts/run-stats.py <app>` como input del `factory-correct`.
- Tests nuevos:
  - `TestRunStats`: fixture temporal de state/reports (dos layouts, un run.json incompleto, un dir sin run.json, dos babysit.json con shas distintos, decisions.tsv con resultado falló) → asserts sobre el JSON (`--json`, salida parseable): conteos exactos y "sin-schema" detectado; `--since` filtra.
  - `TestAgentStats`: db fake con schema `project/session/part` mínimo + window: agrega por agente (NULL incluido), skills, delegaciones ok/error, cap de 24 h; no contiene strings de contenido (assert ausencia del texto de un prompt puesto en el fixture).
  - `TestMetricsDocs`: AGENTS.md, README.md y factory-report SKILL mencionan ambos scripts.

## Finish condition

```bash
python3 scripts/test_harness.py
uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
python3 scripts/run-stats.py --all | head -40
python3 scripts/run-stats.py --all --json | python3 -m json.tool >/dev/null && echo json-ok
python3 scripts/agent-stats.py --days 14 | head -40
python3 scripts/agent-stats.py --days 14 --json | python3 -m json.tool >/dev/null && echo json-ok
```

## Decisiones de diseño (documentar en docstrings)

- Cap de 24 h por sesión para `dur_ms` en agent-stats (sesión huérfana abierta inflaba todo; el worker midió 14 h).
- Proxy de rework con shas distintos de babysit (no hay timestamps por sha en todos los formatos).
- Nunca inventar datos: campo ausente → bucket `sin-schema`, no cero.
- run-stats no toca gh: merge-status ya vive en run.json (`merged_at`); los que no, quedan como `awaiting/otro`.

## Fuera de alcance

- Dashboards, cron/automatización, costo cross-run por PR (join db↔reports queda para después si pide), cambios a trace-report.py, `.pi/**`.
