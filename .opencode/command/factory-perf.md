---
description: Etapa 8 del factory: perf factory sobre la app del registry. Detecta regresiones (números veteados), root-cause y propone fixes (no aplica). Si hay fixes aprobados y acceso a métricas, ejecuta hillclimb. App-agnóstico.
agent: build
---

Corré el perf factory sobre la app indicada (`<app>`, default: la única
registrada si hay una sola; si hay varias, pedirla).

1. Cargá `factory-context` y verificá si hay acceso a métricas reales
   (`scripts/metrics.sh <app> --hours 24`). Sin acceso, el reporte queda PENDIENTE.
2. Delegá el análisis al subagent `perf-factory`: señales → deduplicación →
   root-cause (con refs archivo:línea) → propuestas de fix acotadas,
   cada número **veteado** (limiter, repetición, qué midió, relevancia
   end-to-end) o PENDIENTE.
3. Revisá que el reporte distinga claramente hallazgos con evidencia de
   hipótesis sin métricas.
4. **Hillclimb (solo con fixes aprobados + métricas accesibles)**: si el
   humano aprobó un fix, ejecutá el loop de mejora contra esa métrica
   veteda como target: (1) medir before; (2) 1 cambio (vía `factory/<slug>`
   y `coder`); (3) medir after y vetear; (4) win aceptado → 1 commit con
   before/after en el mensaje; no ayudó → **descartar el cambio**;
   (5) plateau (2 iteraciones sin win) → pivotar hipótesis, no insistir;
   (6) la finish condition (target pactado con el humano) **nunca se
   relaja**. Cada iteración: una fila en `decisions.tsv` del run.
   Sin métricas reales: hillclimb no arranca (el gate de autonomía no
   se cumple).
5. Guardá en `state/reports/<app>/` con `factory-report` y respondé con el
   resumen de hallazgos priorizados y las propuestas de fix (que quedan para
   aprobación humana), o el estado del hillclimb (wins, descartes, target).