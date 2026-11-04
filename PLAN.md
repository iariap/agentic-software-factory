# Mi Mesa — Harness "Software Factory"

Harness de orquestación para la app [mi-mesa-recetario](https://github.com/iariap/mi-mesa-recetario),
basado en los lineamientos del [OpenAI Software Factory](https://newsletter.pragmaticengineer.com/p/openai-software-factory)
(adaptado a un proyecto de una sola persona con CI ya existente).

## Principio

El harness **no reemplaza** al CI remoto ni al desarrollo directo: orquesta a
agentes (opencode) sobre un checkout de trabajo (`work/`) del repo, usando la
API de GitHub (`gh`) para PR, CI y deploy. El repo de la app sigue siendo la
fuente de verdad; este repo es el "control tower".

## Mapeo de las 9 etapas del factory

| Etapa del factory | Componente en asf |
|---|---|
| 1. Human builder define el outcome | `specs/*.md` + `/factory-build` |
| 2. Contexto | skill `factory-context` + `references` + `work/` sync |
| 3. Implementación | agente implementa en `work/`, corre checks locales |
| 4. Build/test + CI | `/factory-ci` skill: rama → push → PR → `gh run watch` → babysit |
| 5. Review agentica multi-lente + riesgo | `config/risk-policy.json` + subagents `reviewer-*` |
| 6. Deploy agentic | `deploy-agent` + `scripts/health.sh` |
| 7. Observar producción | `deploy-agent` genera dashboard por cambio en `state/dashboards/` |
| 8. Perf factory | `perf-factory` subagent + `scripts/metrics.sh` |
| 9. Sevbot | `sevbot` subagent + `scripts/incident.sh` |

## Estructura

```text
asf/
├── opencode.json                 # config: references, permissions, MCP github
├── AGENTS.md                     # cómo operar el harness
├── PLAN.md                       # este documento
├── .opencode/
│   ├── command/                  # /factory-build, /factory-review, /factory-deploy,
│   │                             #   /factory-perf, /factory-incident, /factory-status
│   ├── skill/
│   │   ├── factory-context/      # carga contexto del repo mi-mesa
│   │   ├── factory-ci/           # build + tests + PR + babysit
│   │   └── factory-report/       # reporte por corrida en state/
│   ├── agent/
│   │   ├── reviewer-security.md  # PHI, secretos, auth
│   │   ├── reviewer-infra.md     # Cloud Run, costos, escalado
│   │   ├── reviewer-frontend.md  # React/TS, UX, PWA
│   │   ├── reviewer-data.md      # catálogo, nutrientes, doshas
│   │   ├── deploy-agent.md       # handhold staging/prod + health checks
│   │   ├── perf-factory.md       # análisis de regresiones sobre métricas
│   │   └── sevbot.md             # incident response (contexto, nunca ejecuta)
│   └── plugin/                   # hooks: risk classifier, estado por corrida
├── config/risk-policy.json       # archivo cambiado → riesgo → reviewers + aprobación humana
├── scripts/
│   ├── sync-work.sh              # clone/checkout de mi-mesa en work/
│   ├── risk.py                   # clasifica por archivo; reviewers = unión de reglas
│   ├── babysit.sh                # babysitter determinista (timeout, SHA, anti-loop)
│   ├── publish-risk.sh           # publica riesgo como check 'factory-risk' (Checks API)
│   ├── check-matrix.sh           # fuerza la matriz de checks según paths modificados
│   ├── cloud_monitoring.py       # métricas agregadas Cloud Monitoring (p50/p95/p99, éxito)
│   ├── ci-status.sh              # estado de CI de PR/commit/rama
│   ├── health.sh                 # health check de un entorno Cloud Run
│   ├── metrics.sh                # wrapper de cloud_monitoring (agregados, sin payloads)
│   ├── incident.sh               # arma el contexto de un incidente (sevbot)
│   └── test_harness.py           # tests independientes del harness (unittest, sin deps)
├── work/                         # checkout editable del repo mi-mesa (gitignored)
├── specs/                        # specs del human builder (etapa 1)
└── state/                        # corridas: reports/, dashboards/, incidents/ (gitignored)
```

## Decisiones de diseño

- **Repo separado**: el harness vive en `asf`, apunta al repo de mi-mesa vía
  `references` (Git) y opera sobre un checkout editable en `work/`.
- **El CI remoto no se toca**: `scripts/ci-status.sh` y `gh` observan; el
  deploy se hace con `deploy/deploy.sh` del repo (que ya existe).
- **Riesgo por archivo** (`config/risk-policy.json`): default `medium`;
  módulos de salud/privacidad (data/, api.py, domain.py, extraction.py,
  observability.py, tracing.py, catalog.py, nutrition.py, policies.py,
  services.py, supabase/migrations/, deploy/, workflows/, PHI.md) → high;
  review.py/config.py/model_settings.py/pantry_agent.py → medium;
  frontend/evals/docs → low. Los **reviewers se unen de TODAS las reglas que
  matchean** (no solo el nivel máximo). Alto riesgo → aprobación humana
  obligatoria (como el artículo: "low risk auto-approve, high risk human
  review").
- **Enforcement de riesgo**: `publish-risk.sh` publica el riesgo como check
  `factory-risk` (Checks API, output como objeto) calculando el nivel con
  `risk.py` sobre el diff inmutable `BASE..HEAD` (nunca recibido a mano), para
  que branch protection lo requiera. Los permisos de opencode niegan
  `gh pr merge`, push a main, deploy/migraciones de producción y comandos
  destructivos; los subagents reviewers tienen `bash: deny` (solo usan
  read/glob/grep y el diff provisto por el orquestador). `deploy-agent` edita
  solo `state/dashboards/**` y observa el deploy automático de main, no
  dispara deploys manuales salvo orden humana.
- **Invariantes de mi-mesa respetadas**: un solo servicio Cloud Run, sin
  Cloud Tasks/Redis/workflows durables, OpenRouter como gateway, gates
  deterministas de recetas/alergias/restricciones siempre activos, telemetría
  sin contenido (PHI.md).
- **Babysit determinista** (`babysit.sh`): espera checks con timeout, valida
  mergeable (`MERGEABLE`/`CONFLICTING`/`UNKNOWN`), revalida el head SHA
  durante la espera, persiste estado en TODA salida y aplica anti-loop
  (mismo SHA + mismo exit ≠ 0 → no reintenta). Los reviewers inspeccionan un
  **worktree `--detach` en el SHA exacto del PR** (`state/worktrees/pr-<n>`,
  path absoluto, limpiado por trap), no una rama distinta.
- **Métricas sin PHI** (`cloud_monitoring.py`): Cloud Monitoring con
  request_count (tasa de éxito) y request_latencies (p50/p95/p99) + logs por
  severidad; nunca payloads. Sin acceso → PENDIENTE explícito, nunca datos
  inventados.
- **Matriz de checks** (`check-matrix.sh`): fuerza los checks obligatorios por
  paths (catálogo → normalize_catalog + check:catalog; API → api:types;
  frontend → lint/typecheck/build; deploy → tests; workflows → actionlint).

## Flujo de una corrida

1. `/factory-build <spec>` → escribe/lee `specs/<id>.md`, corre el pipeline
   build→test→PR.
2. `/factory-review <pr>` → `risk.py` clasifica el diff, `reviewer-*`
   revisan por dominio, se postean comentarios al PR, alto riesgo → humana.
3. `/factory-deploy <pr|commit>` → `deploy-agent` handhold hasta staging,
   verifica `/health`, genera dashboard.
4. `/factory-perf` → `perf-factory` sobre métricas, detecta regresiones,
   propone fix.
5. `/factory-incident <issue>` → `sevbot` arma contexto, sugiere
   mitigaciones (nunca ejecuta).

## Fases

1. **Fundación** — `git init`, `opencode.json`, `sync-work.sh`, `AGENTS.md`.
2. **Pipeline build→CI→review** — `/factory-build`, `risk-policy.json`,
   reviewers, `/factory-review`.
3. **Deploy + observación** — `deploy-agent`, `/factory-deploy`, health,
   dashboards.
4. **Perf factory + sevbot** — `/factory-perf`, `/factory-incident`.