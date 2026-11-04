---
description: Auditoría defensiva de seguridad de la app vía security-guardian (metodología security-audit, 6 fases). Perfiles quick/standard/deep. Reporte grounded en state/reports/<app>/<run>/. Nunca aplica fixes. App-agnóstico.
agent: build
---

Corré el security guardian sobre la app indicada (`<app>`, default: la única
registrada si hay una sola; si hay varias, pedirla). Perfil opcional como
segundo argumento (`quick` | `standard` | `deep`; default `standard`).

1. Cargá `factory-context` y resolvé el checkout a auditar (`APP_CHECKOUT`
   del registry o el worktree que indique la tarea).
2. Corré los scanners pasivos instalados (`pip-audit`, `osv-scanner`,
   `trivy fs`) sobre el checkout y pasá los outputs al subagent
   `security-guardian` como evidencia de entrada.
3. Delegá la auditoría completa al subagent `security-guardian`:
   app, checkout, perfil y las 6 fases del skill `security-audit`
   (reconocimiento → caza por olas → validación adversarial → critic de
   cobertura → reporte → cierre). El run dir es
   `state/reports/<app>/<run>/` con `run-metadata.json`,
   `coverage-ledger.json`, `findings.json` y `REPORT.md`.
4. Verificá que el reporte sea grounded: sin hallazgo sin evidencia
   literal, `confirmed` separado de `needs_validation`, cobertura parcial
   declarada. Un hallazgo sin evidencia: mandarlo de vuelta a validación.
5. Guardá el reporte con `factory-report` y respondé el resumen priorizado
   (veredicto + hallazgos confirmados + lo por validar + cobertura).
   Los fixes quedan para orden humana: nunca aplicar.
