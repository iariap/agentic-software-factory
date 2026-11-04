---
description: Panel adversarial sobre un PR: N spawns del interrogator con focos de ataque distintos, consolidados en buckets act-on/dismissed. Lo dispara el orquestador por criterios (riesgo high, medium cross-cutting); comando manual para override. Completa a los reviewers de dominio, no los reemplaza. App-agnóstico.
agent: build
---

Lo dispara el orquestador según criterios (registrados con razón):
riesgo **high** siempre; **medium** si el diff cruza superficies
compartidas/contratos (API, schema, config compartida) o el review
produjo ≥3 dismissed con la misma razón; en `low` no corre salvo pedido
explícito. Como comando, override manual
(`$ARGUMENTS` = `<app> <pr>`).

1. Cargá `factory-context` y resolvé SHAs y worktree inmutable del head
   (mismo procedimiento que `/factory-review` paso 2: `BASE_SHA..HEAD_SHA`
   sobre `$WT`).
2. Spawneá al subagent `interrogator` **2-3 veces** con briefs de ataque
   distintos (p. ej.: "regresiones en callers existentes", "casos borde de
   entrada no previstos", "supuestos implícitos que el cambio viola").
   Pasale el diff, los SHAs y la ruta `$WT`. Contextos frescos por spawn:
   no compartas tu teoría con ellos.
3. Consolidá: deduplicá hallazgos, llevá todo a buckets `act-on`/
   `dismissed` con razón, y contrastá contra los findings de los reviewers
   de dominio si hay corrida previa (los dismissed de un panel pueden ser
   act-on de otro).
4. Posteá el comentario al PR con los buckets consolidados
   (`gh pr comment`), marcando cuáles vinieron del panel adversarial.
5. Guardá el reporte con `factory-report` y respondé el resumen.
   Nunca mergees ni apliques fixes: si aparece un act-on grave, escalalo
   para decisión humana.
