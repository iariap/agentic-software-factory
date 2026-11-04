---
description: Loop de aprendizaje del harness (reflect + correct): mina corridas/PRs recientes por clases de error repetidas y codifica cada lección al nivel más determinista posible, con tabla regla↔enforcer. Nunca solo texto: check/lint/script primero. Lo dispara el orquestador al cerrar corridas según triggers; comando manual para override. App-agnóstico.
agent: build
---

## Cuándo lo corre el orquestador (triggers, siempre con la razón registrada)

1. Corrida cerrada con **≥2 reintentos por la misma causa** (`babysit.json`
   del run) — la causa es candidata a lección.
2. PR mergeado/cerrado con **≥2 dismissed que repiten la misma razón** en
   comentarios de reviewers/interrogate.
3. Incidente con mitigación propuesta **parecida a la de un incidente
   previo** (patrón no codificado).
4. **N corridas** (≥5) desde la última pasada de aprendizaje.
5. Corrección que el humano hizo a mano **más de una vez**.
Si no hay trigger, la decisión "skip (sin clases nuevas)" queda registrada
en el reporte de la corrida. Como comando: override manual
(`$ARGUMENTS` opcional: `<app>` o tema foco; sin argumentos, los últimos
~5 runs en `state/reports/` y los PRs mergeados/cerrados recientes).

1. **Minar clases de error** (evidencia, no memoria): correr correcciones
   repetidas (reintentos en `babysit.json`, descartes en `decisions.tsv`),
   findings repetidos en comentarios de reviewers/interrogate
   (dismissed con la misma razón dos veces = patrón), incidents/sevbot con
   mitigaciones parecidas, y correcciones que el humano hizo a mano más de
   una vez.
2. **Clasificar cada clase** por su nivel de codificación — el más
   determinista que aplique:
   1. **Arquitectura/estructura**: el error desaparece si el estado o la
      forma cambia (p. ej. un solo writer, un schema estricto).
   2. **Check/script/test determinista**: regla en `check-matrix.sh`,
      `test_harness.py`, lint config o CI — el harness ejecuta la regla.
   3. **Policy**: regla de riesgo en `config/policies/**` (si es de
      gobernanza).
   4. **Texto de agente** (último recurso): una línea en el rol/skill
      relevante.
3. **Codificar**: un fix por clase, en el nivel elegido, vía
   `scripts/new-worktree.sh <app> <slug>` + PR. Delegá la implementación con `task` al subagent
   `coder` (modelo fijo `opencode/deepseek-v4.1-flash-free`); el orquestador
   inspecciona el diff y verifica los checks, no implementa. Si ya existe un
   enforcer, no duplicar: mejorarlo.
4. **Tabla de lecciones**: mantener `docs/lessons.md` — una fila por
   lección: `clase de error | lección | enforcer (archivo:qué) | origen
   (run/PR) | estado`. Al actualizar, verificar que cada fila siga tendo
   enforcer vivo (el que desapareció: re-codificar o marcar deprecada).
5. Respondé con: clases detectadas (con evidencia), nivel elegido y PR(s)
   con los fixes. Una lección sin enforcer concreto queda rechazada:
   "deberíamos tener cuidado" no es una corrección.
