---
description: Curaduría editorial de un lote del catálogo de una app. El sustento (roles, prompts, validador, metodología) vive en el repo de la app, en <checkout>/curation/; el harness sólo orquesta con punteros finos. Apps sin curation/ declarado → PENDIENTE. App-agnóstico.
agent: build
---

Corré la curaduría editorial de un lote del catálogo de una app.

`$ARGUMENTS`: `<app> [lote|N|--until-exhausted [--size N]]`. Primer token: id de
app (ej. `mi-mesa`). Segundo, opcional: nombre del lote, tamaño `N` (default
10–20) o `--until-exhausted` (modo escala; `--size N` default 10). Sin app,
pedila; no hay default.

1. Cargá `factory-context` y resolvé `eval "$(scripts/app_config.py <app> --sh)"`.
2. **Precondición (fail-closed)**: verificá el sustento en el repo de la app:
   `ls "$APP_CHECKOUT/curation/"` debe traer `SKILL.md`, `agents/` (labeler,
   judge, council, teacher) y el validador determinista. Si falta → **PENDIENTE**
   con la ruta exacta e instrucción: el sistema de curaduría (roles, prompts,
   metodología y validador) vive en el repo de cada app (`<checkout>/curation/`,
   adyacente, fuera del runtime) y se incorpora por su propio PR. No improvises
   la metodología en el harness.
3. Leé la metodología `<APP_CHECKOUT>/curation/SKILL.md` y seguila. Estado y
   trail del lote en `state/curation/<app>/<batch>/` del harness.
4. Delegá con `task` a los subagents finos, en el orden de la metodología:
   `curation-labeler` → gate `<APP_CHECKOUT>/curation/validate_coordination.py`
   → `curation-judge` → `curation-council` (pendings) → `curation-teacher`
   (huecos de fuente, sólo a `human-queue/`). Cada etapa puede frenar la línea:
   un gate rojo o un `reject` no se relaja (vuelve al labeler o va a humana).
5. Cierre con `curation-publisher`: PR de datos del catálogo a la app + babysit
   del CI. El publisher **promueve** `reviewed:true` + `reviewed_by:"pipeline"`
   desde el trail (judge/council `approve`) y re-corre el gate; **nunca mergea**
   ni aprueba su propio gate.
6. Guardá el reporte con `factory-report` en `state/reports/<app>/<run>/`.

## Modo escala (`--until-exhausted`)

Encadena lotes priorizados por **citabilidad** (el score de duraciones citables
que ya usa el labeler según la metodología de la app) hasta agotar el pool, con
`--size N` (default 10). Cada lote corre labeler → gate → judge → council →
publisher (con promoción) y deja una fila de trail por etapa en
`state/curation/<app>/<batch>/`.

- **Terminación honesta**: se corta al agotarse el pool citable o al alcanzar el
  techo de lotes acordado; no se agranda el pool para "llegar" a un número.
- **Plateau**: un lote con >50% `reject` **pausa** el loop y se reporta; no se
  baja el estándar para seguirlo. Es señal para revisar fuente/criterio.
- Entre lotes, el humano sólo revisa los PRs-resumen (1 approve por lote). El
  merge sigue sujeto a la policy de riesgo de la app: los PRs de catálogo salen
  con la etiqueta/indicación `curation` en la body para que se vean en la vía
  rápida si el owner la habilita (el agente **no** la habilita).

## Reglas

- El orquestador **sólo orquesta**: no edita el catálogo, las propuestas ni el
  checkout; todo artefacto lo producen los subagents con `task`.
- La curaduría es de la app: el harness no copia roles ni metodología.
- El merge del PR del catálogo siempre requiere orden humana explícita (riesgo alto).
