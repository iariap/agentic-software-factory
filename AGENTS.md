# AGENTS.md — Harness Software Factory (control tower multi-app)Este repo es el **harness** que orquesta el desarrollo de **varias apps**
(registry en `config/apps/*.json`; hoy: [mi-mesa-recetario](https://github.com/iariap/mi-mesa-recetario)
y [radio-contest](https://github.com/iariap/radio-contest)) siguiendo los
lineamientos del OpenAI Software Factory. El código de cada app NO vive acá:
`work/<app>/` es el **checkout base** por app (sync), definido por el
registry en `config/apps/<app>.json`; **cada implementación corre en un
worktree** `state/worktrees/<app>/<slug>` (rama `factory/<slug>`, creado con
`scripts/new-worktree.sh`). **El harness es agnóstico a la app y al
cwd**: nada se asume por nombre; todo se resuelve desde el registry y desde
`.factory/` del checkout de cada app.

## Qué es y qué no es

- **Es**: un control tower de agentes (opencode) que mueve un cambio por el
  pipeline: spec → implementación → CI → review agentica → deploy → observación.
- **No es**: un reemplazo del CI remoto ni de los repos de las apps. La fuente
  de verdad de cada app es su repo (main). Acá viven la orquestación, los
  subagents, el registry de apps, las políticas de riesgo y el estado de cada
  corrida. Para sumar una app: crear `config/apps/<id>.json` (puntero) +
  `.factory/app.json` y `.factory/policy.json` en el repo de la app (+
  `scripts/apps/<id>/check-matrix.sh` si hace falta).

## Layout

- `.agents/` — **capa Freebuff/Codebuff** (aditiva; ver `.agents/README.md`):
  `skills/*/SKILL.md` son adaptadores que apuntan a los skills/commands de
  `.opencode/`, y `*.ts` son los mismos subagents como agentes spawnables de
  Codebuff (los reviewers siguen sin editar; `deploy-agent` sólo observa y
  escribe `state/dashboards/**`). La fuente de verdad sigue siendo
  `.opencode/`; cambiar flujos ahí, no en los adaptadores. La regla de policy
  `high` para `.agents/**` queda **propuesta**, no aplicada (requiere orden
  explícita del owner).
- `.opencode/command/*.md` — comandos `/factory-*` (puntos de entrada).
- `.opencode/skill/*/SKILL.md` — workflows reutilizables (contexto, CI,
  reporte, security-audit).
- `.opencode/agent/*.md` — subagents: `reviewer-security`, `reviewer-infra`,
  `reviewer-frontend`, `reviewer-data`, `deploy-agent`, `perf-factory`, `sevbot`,
  `security-guardian` (auditoría defensiva del checkout, grounded),
  `interrogator` (panel adversarial de PRs) y `curation-labeler`,
  `curation-judge`, `curation-council`, `curation-teacher`, `curation-publisher`
  (wrappers finos de curaduría: resuelven el checkout y ejecutan el rol canónico
  en `<checkout>/curation/`; no copian la metodología acá).
- `.pi/` — **entrada pi.dev** (aditiva, ver [README](README.md#usar-asf-desde-pidev)):
  `.pi/settings.json` pinnea `pi-subagents@0.66.0` y fija los subagents a
  `openrouter/deepseek/deepseek-v4.1-flash` (mismo modelo barato que OpenCode,
  distinto id de provider) con `modelScope` estricto y, salvo el `coder` (que
  tiene `fallbackModels: [openrouter/deepseek/deepseek-v4.1-flash-1017]`), sin
  fallbacks; los runners externos (`claude-code`/`codex`/`cursor`) quedan
  `disabled`;
  `.pi/agents/*.md` son wrappers compactos con `name`/`description`/`tools` que
  referencian el prompt canónico de `.opencode/agent/*.md` **sin copiarlo**;
  `.pi/extensions/asf-subagents.ts` es el guard que en `tool_call` autoriza sólo
  la plantilla `runs.run` con child allowlisted y bloquea `agentScope:"user"`,
  `cwd` fuera del harness, `workflowScriptPath`/`workflow`, top-level
  `agent`/`task` y `resume`. La fuente de verdad de los flujos sigue siendo
  `.opencode/`.
- `config/apps/<id>.json` — **puntero** por app: `repo`, `remote`, `checkout`
  (autoridad del harness). La metadata autoritativa vive en el repo de la app:
  `<checkout>/.factory/app.json` (id, aliases, entornos, métricas, checks,
  descripción) y `<checkout>/.factory/policy.json` (reglas de riesgo de esa
  app). `scripts/app_config.py` superpone el overlay del repo sobre el puntero.
  Único resolvedor: `scripts/app_config.py <app> [--sh|--list]` (`mi-receta` →
  `mi-mesa`, `harness` → `asf`). Apps sin migrar pueden declararse en
  `config/policies/<app>.json` como fallback transitorio.
- **CI configurable por app**: el puntero o el overlay pueden declarar
  `"ci": {"provider": "github" | "none"}` (default `github`; un valor
  desconocido se trata como `github`, fail-closed). Con `provider: none` la app
  no corre GitHub Actions en sus PRs —hoy x6200, que valida con su matriz
  local— y `none` **nunca** se lee como verde: `merge.sh` no consulta
  `statusCheckRollup` y su gate pasa a ser que el merge **corra la matriz local
  del registry** (`checks`, p. ej. `scripts/apps/x6200/check-matrix.sh`) contra
  base/head y exija exit 0 — el body del PR no se lee (un `check-matrix` en un
  fence no prueba nada); `babysit.sh` y `ci-status.sh` no hay checks que esperar,
  así que sólo reportan el estado del PR (babysit exige OPEN+mergeable+no draft)
  y declaran que la validación es la matriz local; `auto-merge.py` no la
  considera elegible (la vía rápida necesita CI verde).
  `scripts/app_config.py` lo expone como `APP_CI_PROVIDER` y `APP_CHECKS`
  (`--sh`) y `ci_provider(app)` para los consumidores Python.
- `config/policies/base.json` — reglas comunes **no negables** (tocar
  `.factory/**` nunca es low). La metadata de la app en el checkout requiere
  `sync-work.sh <app>` previo; en un clone fresco sin checkouts, los tests del
  harness que dependen de reglas de una app se saltan (skip), no fallan.
- `scripts/` — helpers core **app-agnósticos** (app_config, sync, risk,
  ci-status, health, metrics, babysit, publish-risk, incident, auto-merge).
  Todos toman `<app>` como primer argumento; no hay default.
- `scripts/apps/<app>/` — helpers específicos de una app (check-matrix,
  generadores de imágenes).
- `work/` — checkout **base** por app (fuente de sync y de worktrees;
  gitignored; `scripts/sync-work.sh <app>`). Nunca se implementa acá.
- `specs/<app>/` — specs del human builder (etapa 1 del factory), por app.
- `state/` — corridas: `reports/<app>/`, `dashboards/<app>/`,
  `incidents/<app>/`, `worktrees/<app>/` (gitignored). Los worktrees son de
  review (`pr-<n>`) **y de build (`<slug>`)**.

## cwd-independiente

Los scripts resuelven su propia raíz (`dirname "$0"` / `__file__`), así que
corren desde cualquier directorio. Reglas:

- Los paths relativos de argumentos (p. ej. `babysit.sh --state`) se anclan a
  la raíz del harness, no al cwd.
- Para invocar scripts desde cualquier cwd: resolver la raíz del harness
  (el directorio que contiene `manifest.json`, p. ej. `$HARNESS` o vía
  `python3 <raíz>/scripts/app_config.py <app> --sh`). Los scripts mismos no
  necesitan cwd: resuelven `ROOT` por su propia ubicación.
- `state/`, `work/`, `specs/` y los reportes se resuelven siempre con
  respecto a la raíz del harness (los scripts python ya lo hacen vía `ROOT`).

## Comandos

```bash
scripts/app_config.py --list     # apps del registry
scripts/app_config.py <app> --sh # vars APP_REPO/APP_CHECKOUT/APP_CI_PROVIDER/... para eval en shell
scripts/sync-work.sh <app>       # clona/actualiza el checkout base de la app en work/
scripts/new-worktree.sh <app> <slug> [base]  # crea/retoma el worktree de una corrida
scripts/risk.py <app> <base> <head>   # clasifica el diff con la policy mergeada (JSON)
scripts/ci-status.sh <app> <pr|sha>   # estado del CI de un PR o commit
scripts/health.sh <app> <env>         # health check de un entorno del registry
scripts/metrics.sh <app> [--hours N]  # métricas del provider del registry (si hay acceso)
scripts/incident.sh <app> <issue|ctx> # arma el contexto de un incidente para sevbot
scripts/wait_until.sh <techo> <iv> -- <cmd> # espera hasta-condición con techo (salida pipeable; -c "..." admite pipes internos)
scripts/auto-merge.py <app> <pr> [--apply]  # vía rápida de merge low-risk (shadow por default)
scripts/cleanup.sh <app> [--apply]          # limpia worktrees stale de build y review (shadow por default)
scripts/trace-report.py <app> <slug> [-o out]  # traza OTel-style: rendimiento por agente + diagrama de ejecución (HTML único, opencode.db read-only)
scripts/run-stats.py [app|--all] [--since YYYY-MM-DD] [--json]  # métricas cross-corrida de state/reports (dos layouts, sin red/gh)
scripts/agent-stats.py [--days 30] [--app asf] [--project RUTA] [--json] [--daily]  # agentes/skills desde opencode.db (read-only, sólo agregados)
```

## Invariantes (no romper)

- El repo de la app es la fuente de verdad: `work/` es el checkout base,
  descartable, se regenera con `sync-work.sh` (push a main del repo de la app
  dispara el CI). La implementación va en el **worktree de la corrida**
  (`state/worktrees/<app>/<slug>`, rama `factory/<slug>`, vía
  `scripts/new-worktree.sh`); **una sola tarea escritora por worktree**;
  `cleanup.sh` recolecta los stale post-merge.
- **El orquestador SOLO orquesta**: contexto, spec, coordinación, inspección
  de diffs, checks deterministas y Git/PR/CI; toda producción de artefactos
  (código, tests, docs, fixes — del harness y de las apps) se delega a
  subagents con `task`. Nunca edita archivos de configuración/app/harness
  directamente. Sin `task`/`coder` disponible: reportar PENDIENTE, sin
  fallback de modelo.
- Respetar las invariantes del `AGENTS.md` de la app objetivo (leerlas del
  checkout: cada app declara las suyas en su repo — p. ej. mi-mesa: un solo
  servicio Cloud Run, sin Cloud Tasks/Redis, gates deterministas siempre
  activos, telemetría sin contenido, secretos solo en Secret Manager).
- **Aprobación humana obligatoria** para cambios de riesgo alto/medio
  (`config/policies/`): el agente propone, nunca mergea/deploya solo.
- **Vía rápida low-risk (activa)**: por orden del owner, los PRs que pasan
  TODOS los gates de `scripts/auto-merge.py` (riesgo low + CI verde + PR
  abierto sin draft/conflictos + sin label `needs-human-approval` ni
  `DO-NOT-MERGE` + sin archivos prohibidos + sección Validación) se fusean
  **sin preguntar**, squash, y se vigila el run de main + `/health` de
  staging; si degrada, se crea el PR de revert (nunca force-push). Nunca
  producción, nunca `--admin`. Alto/medio siguen requiriendo orden explícita.
- **Merge siempre por rama, nunca por número literal**: `scripts/merge.sh <app>
  <head-branch>` resuelve el PR por head y verifica OPEN+CLEAN+CI antes de
  fusionar; tras cualquier `gh pr create`/re-target, resolver el número con
  `gh pr view <head> --json number` — los números son globales del repo y otras
  sesiones los consumen.
- **Gate de confianza para autonomía**: antes de dejar un loop autónomo
  corriendo solo (vía rápida `enabled`, build largos, reintentos), vale
  estos cuatro: (1) la tarea se hizo una vez a mano o viéndola hacer —
  sabés cómo es "bien"; (2) el agente tiene las herramientas y señales de
  verificación que usaría el humano; (3) **cada etapa puede frenar la
  línea** (un "no es bug" del triage, un repro que falla, un fixer que
  juzga el cambio arriesgado: cada "no" útil evita que el error llegue a
  la etapa siguiente, donde cuesta más deshacer); (4) los fallos repetidos
  ya están convertidos en tools/checks, no en esperanzas. Si alguno no
  vale, la corrida va con supervisión. Y en el loop: 1 cambio + 1 check +
  1 fila de trail; el cambio que no ayudó se descarta; plateau → pivotar;
  la finish condition nunca se relaja para declarar victoria.
- Nunca ejecutar acciones destructivas ni pagas sin pedirlo: no hay
  `deploy.sh production` implícito, no hay borrado de datos ni migraciones.
- `state/` y `work/` no se committean. Los specs y políticas sí.
- **No sleep a ciegas**: toda espera es hasta-condición con techo —
  `scripts/wait_until.sh`, `until`-loop con deadline, o
  `gh run watch`/`gh pr checks --watch` para CI (skill `wait-for`).
  Prohibido `sleep N && check`: espera fija que sobra o falta. El
  `sleep` solo vale como intervalo dentro de un poll. Salida pipeable:
  solo la salida final exitosa va a stdout (el resto, a stderr).
- Un subagent reviewer **nunca edita** código: solo revisa y reporta.
- **El harness se auto-gobierna**: los cambios a `asf/` van por PR con el CI
  propio (`.github/workflows/ci.yml`: ruff + ty + test_harness + sintaxis
  shell + fences mermaid). El agente sólo puede fusear por **orden humana
  explícita sobre ese PR en particular** (o por la vía rápida si la policy lo
  permite). Cambios a `config/policies/**`, `config/apps/**`, `.opencode/**`,
  `scripts/**` y `.github/**` son siempre riesgo alto en la policy del
  harness: nunca pasan por vía rápida, requieren la orden explícita.
- `sevbot`, `perf-factory` y `security-guardian` **diagnostican y proponen**,
  nunca aplican fixes ni mitigaciones sin aprobación humana.
- **La curaduría de contenido es de la app**: el sustento (validador
  determinista, prompts de rol y metodología) vive en `<checkout>/curation/` —
  adyacente, testeable, fuera del runtime desplegado. El harness sólo orquesta
  con punteros finos (`curation-*`, `/factory-curate`) y publica con el publisher
  y sus gates de siempre (risk, PR, babysit); no copia roles ni metodología.
  Apps sin `curation/` declarado: `/factory-curate` reporta PENDIENTE.
  `reviewed:true` lo fija el pipeline (`reviewed_by:"pipeline"`) sólo tras gate +
  judge/council approve en el trail; `human` sólo por decisión editorial
  explícita; el publisher promueve, nunca mergea.

## Flujo típico

Todos toman `<app>` como primer argumento (id del registry).

**El orquestador mueve el flujo solo; los comandos son override manual.**
**Implementación delegada obligatoria**: toda modificación de código, tests,
scripts o configuración (también del harness) se encarga con `task` al
subagent `coder`, cuyo modelo fijo es `opencode/deepseek-v4.1-flash-free`
(los demás roles siguen con `opencode/deepseek-v4.1-flash`).
El orquestador prepara contexto/spec, coordina, inspecciona el diff, verifica
checks y gestiona Git/PR/CI; no sustituye al coder implementando él mismo.
También delega los fixes de CI/review y las iteraciones de hillclimb.
Cada encargo incluye el **worktree de la corrida** `$WT`
(`state/worktrees/<app>/<slug>`, vía `scripts/new-worktree.sh`), la rama, el
alcance, las invariantes, los criterios de aceptación y los comandos de
verificación. Una sola tarea escritora por worktree. El coder devuelve
archivos cambiados, checks reales y bloqueos; el orquestador verifica esa
evidencia antes de publicar.
Si `task` o `coder` no están disponibles o el modelo falla, reportar
PENDIENTE: no cambiar silenciosamente de modelo ni implementar como fallback.

**Equivalencia en pi.dev** (ver [README](README.md#usar-asf-desde-pidev)). En
pi-subagents 0.66.0 la ejecución es code-driven por `workflowScript`; ASF
autoriza **una sola plantilla verificable**:

```text
subagent({
  workflowScript: 'return runs.run("asf", { "agent": "coder", "task": "<encargo>", "cwd": "<abs>" })',
  cwd: "<abs>", agentScope: "both"
})
```

- `task` (OpenCode) ↔ `runs.run("asf", { "agent": "coder", "task": ... })`
  (Pi). El `coder` de Pi corre con `openrouter/deepseek/deepseek-v4.1-flash`
  fijo (el chip OpenCode del coder es flash-free): mismo modelo barato que
  OpenCode, distinto id de provider, y con fallback automático
  `openrouter/deepseek/deepseek-v4.1-flash-1017` ante rate-limit. El
  orquestador Pi queda libre (su modelo NO está pinneado en `.pi/settings.json`).
- El child sólo acepta `agent` (rol ASF), `task`, `cwd`, `agentScope`
  (`both`/`project`), `context` (`fresh`) y `model` (sólo el pin). El parser es
  prefijo/sufijo + `JSON.parse` (sin `eval`): JS no literal se rechaza.
- `cwd` (outer y child) es absoluto y **debe quedar dentro de la raíz del
  harness** (los checkouts de app en `work/` y `state/worktrees/` lo están): así
  el child resuelve `.pi/settings.json` y el `modelScope` estricto. La extensión
  `.pi/extensions/asf-subagents.ts` bloquea (fail-closed) `agentScope: "user"`,
  `cwd` externo, `workflowScriptPath`/`workflow`, top-level `agent`/`task`/`model`,
  `resume`, `tools` y `extensions`.
- Management permitido (read-only/control): `status`, `list`, `get`, `models`,
  `guide`, `doctor`, `children.list`, `stop`, `interrupt`, `steer`, `validate`.
  Se bloquean `resume`, `create`, `update`, `eject`, `enable`, `schedule.*`,
  `project.*`, etc.
- `agentScope: "both"` = `user` + `project`; el scope `project` es el que aporta
  los wrappers ASF de `.pi/agents/` (project gana ante colisión de nombre).
- Los reviewers y analistas de Pi son read-only por `tools` (read/grep/find/ls);
  `security-guardian` y `sevbot` no tienen `bash`: el orquestador corre los
  scans/`incident.sh` y les pasa la evidencia.
- Para paralelismo, varias llamadas `subagent` foreground (no se habilitó
  `runs.all`).

El humano no necesita invocar los pasos: el orquestador determina cuándo
corre cada uno según los criterios de cada workflow (Disparadores de
`security-audit`, criterios de `interrogate`, triggers de `correct`) y los
registra con su razón. Por orden del owner: ante cada ticket/issue
identificado para trabajar, el agente usa una rama propia, implementa,
corre checks, pushea, abre el PR automáticamente y babysitea el CI. No
pedir confirmación para crear el PR. El merge sigue sujeto a la política
de riesgo (`high`/`medium` requieren aprobación humana; `low` puede ir por
vía rápida si pasa todos los gates). El merge con orden humana o sobre PRs
no-low pasa por `scripts/merge.sh <app> <head-branch>` (resuelve por rama
head, nunca por número literal).

Automático (decisión del orquestador, siempre registrada):
- **Al arrancar una sesión**: pickup del trabajo en flight (procedimiento
  `factory-resume`): brief de ramas `factory/*`, PRs abiertos, run dirs
  in_progress; retomar solo con orden del humano.
- **En cada review**: auditoría scoped de seguridad cuando valga la pena
  (Disparadores del skill `security-audit`) y panel adversarial para
  riesgo high (o medium cross-cutting, a juicio).
- **Al cerrar cada corrida**: evaluar si hay clases de error repetidas
  (triggers del `factory-correct`); si hay, proponer los fixes con enforcer.
  Generar la traza `trace.html` en el run dir con
  `scripts/trace-report.py <app> <slug> -o <run dir>/trace.html` (falla →
  PENDIENTE documentado, no bloquea el cierre).
- **Post-merge/deploy**: hillclimb solo con fixes aprobados + métricas
  (gate de autonomía).

Manual (override, mismo procedimiento):
1. `/factory-build <app> "spec..."` → escribe `specs/<id>.md`, implementa en un
   worktree `state/worktrees/<id>/<slug>` (vía `scripts/new-worktree.sh`),
   corre checks locales, crea PR y babysit el CI.
2. `/factory-review <app> <pr>` → `risk.py` clasifica, reviewers por dominio
   postean comentarios, alto riesgo → humana.
3. `/factory-merge <app> <pr>` → `auto-merge.py`: vía rápida fall-closed para
   PRs low-risk con CI verde (shadow por default; `mode=enabled` mergea y vigila).
   Con orden humana sobre un PR puntual: `scripts/merge.sh <app> <head-branch>`
   (resuelve por rama head, nunca por número literal).
4. `/factory-deploy <app> <pr|sha>` → `deploy-agent` handhold hasta staging,
   verifica `/health`, genera dashboard en `state/dashboards/`.
5. `/factory-perf <app>` → `perf-factory` sobre métricas, propone fix.
6. `/factory-incident <app> <ctx>` → `sevbot` arma contexto y sugiere mitigaciones.
7. `/factory-sec <app>` → `security-guardian` barre el checkout (secretos,
   dependencias, IaC/CI, web, AI/LLM, supply chain) y reporta con evidencia.
8. `/factory-interrogate <app> <pr>` → panel adversarial: 2-3 `interrogator`
   intentan romper el diff; buckets act-on/dismissed (completa a reviewers).
9. `/factory-correct [app|tema]` → mina clases de error repetidas en
   corridas/PRs y codifica cada lección al nivel más determinista posible
   (tabla en `docs/lessons.md`).
10. `/factory-curate <app> [lote|N|--until-exhausted]` → curaduría editorial de un lote del
    catálogo: verifica el sustento en `<checkout>/curation/` y orquesta
    labeler → gate determinista → judge → council → teacher → publisher
    (PR + babysit, nunca merge). Sin `curation/` declarado: PENDIENTE.

## Identidad de los commits

- Todos los commits (harness y checkout de la app) van con la identidad del
  owner: `Pablo Iaria <iariap@gmail.com>` (config global y local de ambos
  repos). **Nunca** pisarla con `git -c user.name=... -c user.email=...`: un
  commit a nombre de otra persona no queda atribuido al owner en GitHub.
- Si un commit queda mal autorado: si su rama ya se mergeó (squash), borrar la
  rama y purgar reflogs (`git reflog expire --expire=now --all && git gc
  --prune=now`). Nunca reescribir `main`.

## Calidad

- Entorno del harness gestionado con **uv**: `uv sync` (dev deps ruff + ty),
  `uv run ruff check scripts/ && uv run ruff format scripts/ && uv run ty check
  scripts/` antes de commitear.
- Scripts shell con `set -euo pipefail`. Scripts python con tipado y sin deps
  fuera de la stdlib salvo que se documente.
- Cada subagent tiene una sola lente (un solo dominio de review).
- **Modelo por rol**: los subagents (todos los `.opencode/agent/*.md`)
  corren con el modelo **fijo** de su frontmatter (barato, cablereado en cada
  archivo); en particular el `coder` corre
  `opencode/deepseek-v4.1-flash-free`, mientras que los demás roles siguen
  con `opencode/deepseek-v4.1-flash`. Failover del coder (orden del owner,
  2026-10-15; actualizado 2026-10-19: el fallback vuelve a `opencode`): si el
  provider rati-limita el pool `flash-free`, el orquestador reanuda la sesión
  del coder con el override `opencode/deepseek-v4.1-flash` (DeepSeek V4.1 Flash
  non-free, del mismo provider `opencode`). El fallback de `opencode` no
  depende del crédito de OpenRouter (hoy sin saldo), a diferencia del 1017. No
  existe una variante gratuita del 1017: el free pool es la familia V4.1 Flash
  en su tier gratuito. El failover es **sólo** por rate-limit, nunca por
  costo/conveniencia, y el pin del frontmatter sigue `flash-free` (el enforcer
  `TestDelegationModelConfig` verifica ese pin). El
  modelo del orquestador lo fija la
  config default (`~/.config/opencode/opencode.json` → `model`) y puede ser
  más potente: cambiar el default no arrastra a los subagentes, y los
  subagentes no definen el presupuesto del orquestador.
- **Modelo por rol en pi.dev**: `.pi/settings.json` fija
  `subagents.defaultModel` y `agentOverrides` de TODOS los roles a
  `openrouter/deepseek/deepseek-v4.1-flash` (en OpenRouter no hay DeepSeek
  free: es el más barato de la familia), con `modelScope` estricto que rechaza
  cualquier otro modelo (incluidos `inherit` y fallbacks no declarados). El
  `coder` tiene fallback **automático**
  `fallbackModels: ["openrouter/deepseek/deepseek-v4.1-flash-1017"]`, y el
  `modelScope.allow` incluye ese id; los demás roles siguen con
  `fallbackModels: []`. El modelo del orquestador Pi NO se pinnea en
  `.pi/settings.json` (queda libre). Los wrappers `.pi/agents/*.md` declaran
  `tools` explícitos (Pi no mapea el `permission` YAML de OpenCode):
  reviewers/analistas read-only, `coder` read/write/edit/bash.
- Todo reporte queda en `state/reports/<run>/` con fecha y commit/sha de referencia.
- Tests del harness: `python3 scripts/test_harness.py` (unittest, sin deps;
  casos de riesgo literales + fakes de gh/gcloud para babysit/metrics). Correr
  antes de committear cambios en scripts/ o config/.
- Tests del harness: `python3 scripts/test_harness.py` (unittest, sin deps;
  casos de riesgo literales + fakes de gh/gcloud para babysit/metrics). Correr
  antes de committear cambios en scripts/ o config/.
