---
name: factory-context
description: Reúne el contexto real de mi-mesa antes de implementar, revisar o desplegar (AGENTS.md, PHI.md, estado de CI/deploy/evals, PRs abiertos). Usar al inicio de /factory-build, /factory-review, /factory-deploy, /factory-perf y /factory-incident.
---

# Contexto de mi-mesa para el factory

Antes de tocar código, reuní el contexto real del repo de la app:

## 1. Checkout de trabajo

- `work/<app>` es el checkout **base** (path en `APP_CHECKOUT` de
  `scripts/app_config.py <app> --sh`): sólo se sincroniza con
  `scripts/sync-work.sh <app>` (apps: `scripts/app_config.py --list`).
  **Nunca** se implementa ahí.
- Cada implementación corre en un **worktree por corrida**:
  `WT="$(scripts/new-worktree.sh <app> <slug>)"` (rama `factory/<slug>` en
  `state/worktrees/<app>/<slug>`). Verificá `git -C "$WT" status` antes de
  delegar.
- Para revisar un PR, usá el **worktree en el SHA exacto** que crea
  `/factory-review` en `state/worktrees/<app>/pr-<n>`; no inspecciones
  `work/` si está en otra rama.

## 2. Documentación canónica

Leer (o confirmar que están leídos) en el checkout de la app (`$APP_CHECKOUT`):

- `AGENTS.md` — invariantes y comandos de la app (no romper).
- `PHI.md` — riesgos de privacidad de datos de salud.
- `docs/mvp-reliability/` — estado del plan de fiabilidad (baseline, informe
  de lanzamiento, min-cero-staging).
- `evals/AGENTS.md` — cómo correr/interpretar la suite robotizada.

## 3. Estado operativo

- `scripts/ci-status.sh <app> main` → último estado de CI en main.
- `scripts/health.sh <app> staging` → health del entorno staging.
- `gh pr list --repo iariap/mi-mesa-recetario` → PRs abiertos.
- `gh issue list --repo iariap/mi-mesa-recetario` → issues abiertos.

## 4. Estado del harness

- `state/reports/` — corridas previas (fecha y commit de referencia).
- `specs/` — specs del human builder pendientes/completados.

## Reglas

- El contexto de producción (métricas, costos) solo llega con acceso real;
  si no hay acceso, marcarlo como PENDIENTE, nunca inventarlo.
- Reportar qué contexto se cargó y qué faltó (acceso, credenciales).