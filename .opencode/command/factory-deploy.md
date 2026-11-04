---
description: Etapa 6-7 del factory: deploy handhold de un PR/commit a staging con health check y dashboard. Producción requiere aprobación humana.
agent: build
---

Deploy handhold del cambio `$ARGUMENTS`.

1. Cargá `factory-context`. Verificá que el cambio está en `main` del repo de
   la app (push a main dispara staging automáticamente) o confirmá el estado
   del PR/commit con `scripts/ci-status.sh <app> <pr|sha>`.
2. Delegá el handhold al subagent `deploy-agent` con la tarea completa:
   verificar CI verde, esperar el run `deploy-staging`, `scripts/health.sh <app>
   staging`, y generar el dashboard en `state/dashboards/`.
3. Revisá el dashboard generado. Si el health no confirma la versión
   esperada, investigá (logs de CI, run fallido) antes de declarar éxito.
4. Reportá en `state/reports/` con `factory-report`.
5. **Producción**: si `$ARGUMENTS` pide producción, NO deployes: producí el
   resumen "listo para producción" y pedí aprobación humana explícita.
6. Respondé con: commit/versión desplegada en staging, health, señales
   observadas y pendientes.