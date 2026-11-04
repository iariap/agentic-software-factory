# Spec: enforcer anti-"número de PR literal" — scripts/merge.sh

Fecha: 2026-10-09 · App: `asf` · Rama: `factory/merge-guard` · Base: origin/main `6a6d7c1`

## Incidente que codifica (clase de error, triggers de factory-correct)
Hoy, una cadena de merge con números de PR literales (`gh pr merge 33`) fusionó el PR **de otra sesión** (#33 curation) porque el PR propio que se esperaba en ese número había sido cerrado/re-creado como #34 por un auto-cierre de GitHub al borrarse la base. El daño fue acotado (CI verde, MERGEABLE) pero el gate humano de esa corrida se saltó sin querer. La lección vive en memoria; hay que codificarla al nivel más determinista posible (tool + enforcer), no en esperanza.

## scripts/merge.sh (nuevo)

`scripts/merge.sh <app> <head-branch> [--expected-sha SHA7] [--squash|--merge] [--apply]`

- `set -euo pipefail`, ROOT propia, app vía `app_config.py <app> --sh` (APP_REPO); **nunca acepta un número de PR como identificador principal**: el argumento es la rama head. (Si se le pasa algo que matchea `^[0-9]+$` → error con el uso correcto; anti-pattern explícito.)
- Shadow por default (sin `--apply`): sólo valida y muestra qué haría.
- Verificaciones fail-closed con `gh pr view <head-branch> --repo "$APP_REPO"` (resuelve PR **por rama**, nunca por número):
  1. existe un PR OPEN con esa head → si no (o CLOSED/MERGED): exit 1 con el estado.
  2. `mergeable == MERGEABLE` y `mergeStateStatus == CLEAN` (UNKNOWN → retry via `wait_until.sh` techo 120 s; si sigue: exit 1).
  3. `headRefOid` empieza con `--expected-sha` si se pasó (protege force-pushes entre el chequeo y el merge); además **compara `headRefOid` con `git -C "$APP_CHECKOUT" rev-parse <head-branch>` cuando la rama existe localmente** → discrepancia = exit 1 (avisá que el HEAD local difiere del remoto).
  4. checks de ese PR: `gh pr view <rama> --json statusCheckRollup`, agrupado **por nombre** (un workflow puede registrar dos corridas por PR): falla si alguna entrada falló, bloquea si el nombre está pending, y es verde si tiene al menos un `SUCCESS`/`NEUTRAL`. `SKIPPED` solo **no** es verde (falta la etiqueta `ci`: un check que no corrió no es un gate). Sin checks → exit 1.
- Con `--apply`: `gh pr merge <NUMERO_RESUELTO> --repo ... --squash` (o `--merge` según flag) **usando el número que se resolvió por rama**, print final `merged: <pr> <sha>`. Nunca `--admin`, nunca force-push.
- stdout: líneas `ok: ...` / `merged: ...`; errores a stderr. Salida estable para tests.

## Invariante en docs (nivel "texto de agente" complementando la tool)
- `AGENTS.md` → Invariantes: nueva bala: "**Merge siempre por rama, nunca por número literal**: `scripts/merge.sh <app> <head-branch>` resuelve el PR por head y verifica OPEN+CLEAN+CI antes de fusionar; tras cualquier `gh pr create`/re-target, resolver el número con `gh pr view <head> --json number` — los números son globales del repo y otras sesiones los consumen." En "Flujo típico" manual: paso `/factory-merge` y nota en el párrafo "por orden del owner" que el merge automatizado usa merge.sh.
- `.opencode/command/factory-merge.md`: documentar `merge.sh` como el camino del paso "alto/medio con orden humana" (auto-merge.py low-risk unchanged).

## Tests (test_harness.py, stdlib, fakes de gh como los existentes)
- `TestMergeGuard`:
  - gh fake: `pr view factory/buena` → OPEN+MERGEABLE+CLEAN+checks pass → shadow imprime `ok:` y **no** mergea; con `--apply` mergea y muestra `merged:`.
  - gh fake: PR con número alto pero head branch NO coincide con el arg → exit 1 "sin PR abierto para la rama".
  - expected-sha que no matchea → exit 1.
  - `gh pr view 42` (número literal como arg) → exit 2 con mensaje de uso.
  - mergeStateStatus DIRTY → exit 1.
- Enforcer docs: `AGENTS.md` y `factory-merge.md` mencionan `merge.sh` y "nunca por número" (o frase equivalente estable).

## Finish condition
```bash
python3 scripts/test_harness.py
uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
bash -n scripts/merge.sh
python3 scripts/merge.sh asf 42 2>&1; echo $?   # 2, uso
```

## Fuera de alcance
- Cambiar auto-merge.py (ya valida gates propios; puede delegar a merge.sh en otra corrida).
- OTLP/Jaeger en trazas: **descartado por el owner** — no proponer.
