---
description: Retoma trabajo en flight: detecta ramas factory/* activas, PRs abiertos, run dirs sin cerrar y worktrees huérfanos, y entrega un brief apretado para continuar (o descartar con orden). Lo corre el orquestador al arrancar cada sesión; comando manual para override. App-agnóstico.
agent: build
---

Lo corre el orquestador **al arrancar cada sesión** (ver Flujo típico en
AGENTS.md) — antes de aceptar cualquier tarea nueva: el trabajo en flight
tiene prioridad de lectura. Como comando, es el override manual
(`$ARGUMENTS` opcional: `<app>`; sin app, escanear todas las del registry).

1. Inventariá (sólo lectura, sin empezar nada):
   ```bash
   git branch --list 'factory/*' && git branch -r --list 'origin/factory/*'
   gh pr list --repo "$APP_REPO" --state open
   ```
   Por cada app: PRs abiertos y su estado de CI
   (`scripts/ci-status.sh <app> <pr>`); run dirs con
   `run.json` con `status` != completado (o `run_status: in_progress` en
   `state/reports/<app>/*/run-metadata.json`); worktrees vivos en
   `state/worktrees/<app>/`; stash entries del repo con etiqueta.
2. Para cada trabajo en flight, reconstruí el estado leyendo el reporte del
   run (`report.md`, `decisions.tsv` — la sección Attention primero) y el
   último commit del PR: dónde quedó, qué sigue, qué quedó PENDIENTE.
3. Respondé un **brief por trabajo**: app, rama/PR, estado de CI, último
   paso completado, próximo paso, pendientes y riesgos. Si el reporte del
   run falta o está incompleto, marcalo (es señal de corte abrupto).
4. No retomes ni mergees nada solo: presentá los briefs y dejá la orden
   (continuar X, descartar Y, mergear Z) al humano. Un pickup que empieza
   a trabajar solo rompe el punto del protocolo.
