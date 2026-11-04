---
description: Estado actual del factory: últimos reportes, dashboards, incidentes, PRs abiertos de mi-mesa y health de staging.
agent: build
---

Resumí el estado del harness y de mi-mesa.

1. `state/reports/` y `state/dashboards/`: últimos runs (fecha, comando, riesgo, estado).
2. `state/incidents/`: incidentes abiertos.
3. `specs/`: specs pendientes y completados.
4. `gh pr list --repo iariap/mi-mesa-recetario` y `gh run list --repo iariap/mi-mesa-recetario --branch main --limit 5`.
5. `scripts/health.sh <app> staging` (si responde, incluir versión).

Respondé con un resumen conciso: qué está en vuelo, qué quedó bloqueado y qué
requiere decisión humana.