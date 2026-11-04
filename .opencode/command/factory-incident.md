---
description: Etapa 9 del factory: sevbot responde un incidente de mi-mesa con contexto y mitigaciones propuestas (nunca ejecuta).
agent: build
---

Atendé el incidente: `$ARGUMENTS`.

1. Cargá `factory-context`. Ejecutá `scripts/incident.sh $ARGUMENTS` (`$ARGUMENTS` = `<app> <issue|ctx>`) para
   armar el contexto base (CI main, issues/PRs abiertos).
2. Delegá el análisis al subagent `sevbot`: alcance, datos a juntar (logs sin
   contenido, health, versión desplegada), y mitigaciones posibles con riesgo
   e impacto, todas marcadas como **requieren aprobación humana**.
3. Revisá que el reporte no exponga datos sensibles (PHI.md).
4. Guardá el contexto y la propuesta en `state/incidents/` y respondé con:
   contexto resumido, mitigaciones propuestas y qué necesita aprobación
   humana para ejecutarse.