# Spec: cada implementación en un worktree; el orquestador SOLO orquesta

Fecha: 2026-10-09 · App: `asf` (el harness se auto-gobierna) · Rama: `factory/worktree-implementations`

## Qué se quiere y por qué (outcome)

Hoy la etapa de implementación del factory escribe directo en `work/<app>`, un único
checkout compartido por app: las corridas en paralelo chocan entre sí, el checkout
base queda sucio con ramas `factory/*`, y `cleanup.sh` sólo sabe limpiar worktrees
de review (`pr-<n>`). En la práctica las corridas ya se mudaron informalmente a
worktrees `state/worktrees/<app>/<slug>` (ej. household-measures-146): falta
formalizarlo.

Cambios buscados:

1. **Toda implementación corre en un worktree desechable por corrida**:
   `state/worktrees/<app>/<slug>` con rama `factory/<slug>`, creado con un script
   nuevo. `work/<app>` queda sólo como checkout *base* (sync + fuente de
   worktrees); nunca se implementa ahí. El merge se hace vía PR contra main.
2. **Reforzar el rol del orquestador**: el agente principal SOLO orquesta —
   prepara contexto/spec, delega TODA producción de artefactos (código, tests,
   docs, fixes — de las apps y del propio harness) a subagents vía `task`,
   inspecciona diffs, corre checks deterministas y gestiona Git/PR/CI. Nunca
   edita archivos directamente. Si `task`/`coder` no está disponible o su modelo
   falla: reportar PENDIENTE, sin fallback.

## Finish condition verificable

Desde la raíz del checkout de trabajo (el worktree de esta corrida):

```bash
bash -n scripts/new-worktree.sh scripts/cleanup.sh scripts/apps/mi-mesa/check-matrix.sh
python3 scripts/test_harness.py            # verde, con los tests nuevos
uv run ruff check scripts/                 # sin observaciones
uv run ruff format --check scripts/        # sin observaciones
uv run ty check scripts/                   # sin observaciones
```

## Cambios

### 1. `scripts/new-worktree.sh` (nuevo)

`scripts/new-worktree.sh <app> <slug> [base]` — crea/retoma el worktree de la
corrida `state/worktrees/<app>/<slug>` con rama `factory/<slug>`.

- `set -euo pipefail`; `ROOT` resuelto por su propia ubicación (cwd-independiente);
  app resuelta SIEMPRE con `python3 "$ROOT/scripts/app_config.py" "$APP_ID" --sh`
  (vars `APP_ID`, `APP_CHECKOUT`, `APP_BRANCH`). Sin default de app.
- `base` default: `origin/$APP_BRANCH`.
- Checkout base: si no existe `$APP_CHECKOUT/.git` → correr
  `bash "$ROOT/scripts/sync-work.sh" "$APP_ID"`; si existe →
  `git -C "$APP_CHECKOUT" fetch --all --prune`. Todo el ruido a stderr.
- Validación de slug: vacío, con `/`, con `~`, o match `pr-[0-9]*` (namespace de
  los worktrees de review) → uso a stderr y `exit 2`.
- Idempotencia: si el worktree ya está registrado
  (`git -C "$APP_CHECKOUT" worktree list --porcelain` contiene la línea
  `worktree <WT>`) → imprimir la ruta y `exit 0`, sin recrear.
- Creación: si la rama `factory/$SLUG` existe localmente
  (`show-ref --verify --quiet "refs/heads/factory/$SLUG"`) →
  `git -C "$APP_CHECKOUT" worktree add "$WT" "factory/$SLUG"` (retomar); si no →
  `git -C "$APP_CHECKOUT" worktree add -b "factory/$SLUG" "$WT" "$BASE"`.
- **stdout: sólo la ruta absoluta del worktree** (pipeable, para pasarla al
  coder); mensajes de progreso a stderr.

### 2. `scripts/apps/mi-mesa/check-matrix.sh`

Aceptar un **tercer argumento opcional `<checkout>`** (default `$APP_CHECKOUT`)
para poder correr la matriz contra un worktree de corrida: usarlo en el
`git diff --name-only`, en `run()` (el `cd`), en el diff de `api-schema.d.ts`
y en el glob de `deploy/tests`. Retrocompatible: sin el arg, comportamiento
idéntico a hoy.

### 3. `scripts/cleanup.sh`

Además de `pr-<n>`, limpiar **worktrees de build** (nombre sin prefijo `pr-`):
- Rama asociada: `factory/<nombre>`.
- Estado: `gh pr view "factory/<nombre>" --repo "$APP_REPO" --json state --jq .state`
  (stderr al fondo, `|| echo NOT_FOUND`).
  - `MERGED|CLOSED` → stale.
  - `NOT_FOUND` → stale si la rama local no existe
    (`show-ref --verify --quiet "refs/heads/factory/<nombre>"` falla); si existe,
    stale sólo si `git -C "$APP_CHECKOUT" merge-base --is-ancestor "factory/<nombre>" "origin/$APP_BRANCH"`
    sale 0 (ya mergeada). Si `$APP_CHECKOUT` no existe → stale.
  - resto (OPEN/UNKNOWN) → activo.
- Remoción: igual que `pr-` (`worktree remove --force` con fallback `rm -rf`);
  **no** borrar la rama local ni remote.
- Actualizar el comentario de uso: cubre worktrees de review (`pr-<n>`) y de
  build (`<slug>`). Mantener shadow por default.

### 4. `.opencode/command/factory-build.md`

- `description`: "...implementa en un worktree..." (hoy dice `work/`).
- Paso 3: reemplazar "Creá una rama `factory/<slug>` en el checkout" por crear el
  worktree de la corrida: `WT="$(scripts/new-worktree.sh <app> <slug>)"` (rama
  `factory/<slug>` desde el trunk del registry; reutiliza si ya existe;
  `work/<app>` no se toca).
- Paso 4: reforzar: el orquestador **SOLO orquesta** — le pasa `$WT` como
  worktree absoluto al `coder` y nunca edita archivos él mismo.
- Paso 5: `scripts/apps/<app>/check-matrix.sh <base> <head> "$WT"`.

### 5. `.opencode/skill/factory-ci/SKILL.md`

- `description` y cuerpo: los checks/commits corren sobre el **worktree de la
  corrida** `state/worktrees/<app>/<slug>` (rama `factory/<slug>`, creado con
  `scripts/new-worktree.sh`), no sobre `work/`.
- Los `git -C "$APP_CHECKOUT"` de §2 pasar a `git -C "$WT"`; `gh pr create` con
  `--repo "$APP_REPO"` (hoy hardcodea `iariap/mi-mesa-recetario`; el skill debe
  ser app-agnóstico en los comandos).
- §1: el fix delegado va "sobre el mismo worktree" (hoy: "mismo checkout/rama").
- Nota nueva: un worktree recién creado no trae tooling gitignoreado
  (`.venv` de backend, `node_modules` de frontend): instalarlo primero o
  registrar el check como PENDIENTE, nunca declararlo pasado.

### 6. `.opencode/skill/factory-context/SKILL.md` (§1)

- `work/<app>` = checkout **base** (`APP_CHECKOUT`): sólo sync; **nunca**
  implementar ahí.
- Implementación: `WT="$(scripts/new-worktree.sh <app> <slug>)"` por corrida.
- Corrección de path: el worktree de review vive en `state/worktrees/<app>/pr-<n>`
  (hoy el texto dice `state/worktrees/pr-<n>` sin app).

### 7. `.opencode/agent/coder.md`

Primer bullet: "Trabajá en el **worktree de la corrida** que te pasa el
orquestador (`state/worktrees/<app>/<slug>`, rama `factory/<slug>`). Nunca
escribas en el checkout base `work/<app>` ni sobre `main`." (reemplaza el bullet
actual que manda la rama al checkout).

### 8. `.opencode/command/factory-correct.md` (paso 3)

"vía rama `factory/<slug>` + PR" → "vía `scripts/new-worktree.sh <app> <slug>` +
PR"; mantener la delegación al `coder`.

### 9. `AGENTS.md`

- Intro: "se trabaja sobre un checkout por app en `work/<app>/`" → aclarar que
  `work/<app>` es el checkout base y **cada implementación corre en un worktree**
  `state/worktrees/<app>/<slug>`.
- Layout: bullet de `work/` → "checkout base por app (fuente de sync y de
  worktrees; nunca se implementa acá)". Bullet de `state/` → los worktrees son
  de review (`pr-<n>`) **y de build (`<slug>`)**.
- Comandos: agregar `scripts/new-worktree.sh <app> <slug> [base]`; en la línea de
  `cleanup.sh` decir "limpia worktrees stale de build y review".
- Invariantes:
  - Actualizar la de `work/`: descartable vía `sync-work.sh`; la implementación
    va en el worktree de la corrida (rama `factory/<slug>`); una sola tarea
    escritora por worktree; `cleanup.sh` recolecta los stale post-merge.
  - Agregar invariante nueva: "**El orquestador SOLO orquesta**: contexto,
    spec, coordinación, inspección de diffs, checks deterministas y Git/PR/CI;
    toda producción de artefactos (código, tests, docs, fixes — del harness y
    de las apps) se delega a subagents con `task`. Nunca edita archivos de
    configuración/app/harness directamente. Sin `task`/`coder` disponible:
    PENDIENTE, sin fallback de modelo."
- Flujo típico: párrafo de delegación obligatoria (worktree, `$WT`) y paso
  manual 1 de `/factory-build`: "implementa en un worktree
  `state/worktrees/<id>/<slug>`".

### 10. `README.md`

- Mermaid "Los 9 flujos": nodo `I[Implementación<br/>work/ + rama factory/*]` →
  `I[Implementación<br/>worktree state/worktrees/<app>/<slug>]`.
- Tabla de 9 flujos: fila 2 (contexto) y fila 3 (implementación: rama
  `factory/<slug>` en `work/` → **worktree por corrida** `state/worktrees/<app>/<slug>`
  con `scripts/new-worktree.sh`).
- Mermaid "Flujo A": nodo `C[Synca work/ y crea rama factory/pasta]` →
  `C[Worktree de corrida<br/>new-worktree.sh · factory/pasta]`.
- Árbol de `scripts/`: agregar `new-worktree.sh <app> <slug> [base] # worktree por corrida`
  y ajustar `cleanup.sh` ("worktrees stale de build y review"); `work/` →
  "checkout base por app (nunca se implementa acá)".
- Quickstart (línea de `sync-work.sh`): mencionar `new-worktree.sh` como paso de
  implementación.
- Sección de invariantes (~línea 498): same refuerzo que AGENTS.md (worktree por
  corrida + orquestador SOLO orquesta).
- No romper el sanity de fences mermaid del CI.

### 11. `scripts/test_harness.py`

- `TestNewWorktree` (nuevo, sin red, sin tocar repos reales): harness falso en
  `tempfile`: copiar `scripts/` al tmp root (sin `__pycache__`), crear
  `config/apps/fakeapp.json` (`id: fakeapp`, `repo: f/o`, `remote`: path de un
  seed repo local con rama `main` y un commit, `branch: main`,
  `checkout: work/fakeapp`), commits del seed con identidad git de test vía env
  (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`; no la del owner). Correr
  `bash <tmp>/scripts/new-worktree.sh fakeapp wt-test` con cwd arbitrario:
  - exit 0 y stdout == ruta `<tmp>/state/worktrees/fakeapp/wt-test`; la rama
    checkouteada es `factory/wt-test` sobre el commit del seed; el clone base se
    creó solo (sync-work).
  - re-run idémpotente: exit 0, misma ruta, un único `git worktree list`.
  - slug `pr-9` → exit 2; slug vacío → exit 2.
- `TestCleanupWorktrees`: agregar dos worktrees de build al fixture existente:
  `b-9999991` (fake gh responde MERGED por contener `9999991` → stale) y
  `b-9999992` (OPEN → activo); asserts en shadow y en `--apply` (sólo desaparece
  el stale).
- `TestWorktreeFlowDocs` (enforcer, nuevo):
  - `AGENTS.md`, `.opencode/command/factory-build.md`,
    `.opencode/skill/factory-ci/SKILL.md`, `.opencode/skill/factory-context/SKILL.md`
    mencionan `new-worktree.sh`.
  - `AGENTS.md` y `.opencode/command/factory-build.md` contienen `SOLO orquesta`.
  - `.opencode/command/factory-build.md` NO contiene `implementa en work/`.
  - `coder.md` (`.opencode/agent/`) menciona `worktree` y `state/worktrees/`.

## Decisiones documentadas

- Base default `origin/<branch>` del registry; el arg `[base]` permite correr
  desde otra referencia (ej. retomar un PR con base distinta).
- Slug = nombre del directorio del worktree; se usa el **id canónico** `APP_ID`
  del registry (no aliases tipo `mi-mesa-recetario`); no se migran los
  worktrees informales ya existentes.
- `cleanup.sh` no borra ramas: un PR cerrado puede necesitar re-push humano.
- El tooling del worktree (venv/node_modules) se instala o queda PENDIENTE;
  esta corrida no automatiza provisiones.
- `.pi/**` no se toca: los wrappers referencian el prompt canónico de
  `.opencode/`, que sí cambia.

## Fuera de alcance

- Cambios en `config/**`, `config/policies/**`, `.github/**`, `.pi/**`.
- Push, merge ni deploy (los gestiona el orquestador después de verificar).
