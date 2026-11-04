---
name: factory-ci
description: Ejecuta build, tests y el flujo de PR sobre el worktree de la corrida (state/worktrees/<app>/<slug>), y hace babysit determinista del CI remoto hasta verde. Usar en /factory-build.
---

# CI del factory (mi-mesa)

Sobre el **worktree de la corrida** (`WT="state/worktrees/<app>/<slug>"`, rama
`factory/<slug>`, creado con `scripts/new-worktree.sh`), no sobre el checkout
base `work/<app>`.

## 0. Matriz de checks por archivos modificados

Corré el script determinista que detecta paths y fuerza los checks
obligatorios (no es opcional):

```bash
scripts/apps/<app>/check-matrix.sh <base> <head> "$WT"   # ej: origin/main HEAD
```

Se corre **antes del push**, no después del primer rojo (el push cambia el SHA y
reinicia el CI). La salida real —cada `[check] ... ok`/`FAIL:`, cada `[SKIP]`
con su motivo y cada `BLOCKED` con su razón— se adjunta al PR como evidencia de
qué se validó en local. Un check que el CI corre y que no pudo correr local
(**`BLOCKED`**, o un gate que la matriz no cubre) se **declara** en el PR con su
razón: nunca se declara PASS ni se omite en silencio.

La matriz por tipo de cambio:

| Cambio | Checks obligatorios |
|---|---|
| `backend/src/omavu/data/**` | `normalize_catalog.py`, `frontend check:catalog`, luego backend completo |
| `backend/**` (`.py`) | `pytest`, `ruff check`, `ruff format --check`, `ty check src` |
| `api.py` (contrato) | `api:types` + `git diff --exit-code -- frontend/lib/api-schema.d.ts` |
| `frontend/**` | `lint`, `typecheck`, `build`, `check:pwa` |
| `deploy/**` | `deploy/tests/*.sh` |
| `.github/workflows/**` | `actionlint` (si está instalado) |

Si el check-matrix de la app (`APP_CHECKS`) no está disponible (sin venv/bun), corré los comandos a
mano según la tabla; nunca omitas los checks de la fila que aplica.
`normalize_catalog.py` se invoca como `.venv/bin/python
backend/scripts/normalize_catalog.py` (no tiene shebang).

## 1. Checks locales

Corré `scripts/apps/<app>/check-matrix.sh <base> <head> "$WT"` (matriz por
paths). Si un check de la matriz falla, delegá el fix con `task` al subagent
`coder` **sobre el mismo worktree**, con el error literal y comando que falla.
El orquestador verifica el diff y repite el check, no implementa el fix.

Un worktree recién creado no trae tooling gitignoreado (`.venv` de backend,
`node_modules` de frontend): instalalo primero o registrá el check como
PENDIENTE, **nunca** lo declares pasado. Un check que el CI corre y que la
matriz no pudo correr queda como `BLOCKED`/PENDIENTE en el PR con su razón: el
PR no puede decir "checks verdes" si falta uno.

## 2. Commit y PR

```bash
git -C "$WT" add -A
git -C "$WT" commit -m "<tipo>(<area>): <descripción>"
git -C "$WT" push -u origin factory/<slug>
gh pr create --repo "$APP_REPO" --title "..." --body "..."
```

## 3. Babysit determinista del CI

Si la app declara `ci.provider: none` (x6200: valida con su matriz local y no
corre GitHub Actions en sus PRs) no hay checks remotos que esperar, así que
`babysit.sh` y `ci-status.sh` **no pueden declarar verde**: sólo reportan el
estado del PR (babysit sale 4 si no está OPEN, no es MERGEABLE o es draft) y
declaran que la validación es la matriz local. El gate real es que `merge.sh`
**corre la matriz local del registry** (`checks`, p. ej.
`scripts/apps/x6200/check-matrix.sh <base> <head> <checkout>`) y exige exit 0:
el body del PR no se lee (un `check-matrix` en un fence no es evidencia).
`auto-merge.py` no aplica con `none` (la vía rápida exige CI verde).

- `scripts/babysit.sh <app> <pr> --state state/reports/<run>/babysit.json` → espera
  los checks con timeout, valida mergeable, persiste el head SHA.
  - Exit 0 → verde y mergeable.
  - Exit 2 → checks fallidos impresos; encargá al `coder` el fix acotado
    y verificá antes de re-pushear (el SHA cambia → babysit se reinicia).
  - Exit 3 → timeout; diagnosticá, no reintentes el mismo comando en bucle.
  - Exit 4 → conflictos; integrá main primero.
- `scripts/ci-status.sh <app> <pr>` para snapshots puntuales.

### Anti-loop (invariante de la app)

- Máximo UN reintento de un comando fallido, y solo si algo cambió entre
  corridas (fix aplicado, SHA nuevo, server matado).
- Dos corridas con el mismo fallo y la misma salida ≠ flake: diagnosticá de
  otra forma (logs del CI, `babysit.json`, probe aislado), no re-corras.
- No tocar el workflow de CI ni `ci.yml` para hacer pasar tests.
- Un fallo que parece ajeno al PR: primero `git pull --ff-only` contra main
  (otro PR pudo arreglarlo) y reintentar una vez.

## Reglas

- No mergear ni hacer push a `main` desde el harness sin aprobación.
- El PR queda en "merge-ready" solo tras `babysit.sh` exit 0: verde +
  comentarios revisados + sin conflictos.
