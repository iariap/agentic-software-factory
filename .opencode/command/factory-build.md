---
description: Etapa 1-4 del factory: escribe la spec del human builder, implementa en un worktree por corrida, corre checks, crea PR y babysit determinista del CI.
agent: build
---

Ejecutá el pipeline build→CI del factory para mi-mesa.

1. Cargá el skill `factory-context` y reuní el contexto (AGENTS.md de la app,
   estado de CI/main, PRs abiertos).
2. Escribí la spec del human builder en `specs/<slug>.md`: qué se quiere y
   por qué (outcome), con criterios de aceptación. Usá `$ARGUMENTS` como
   entrada; si es muy corto, profundizá el qué/por qué sin inventar requisitos
   del producto. La spec declara una **finish condition verificable**: un
   comando o artefacto que pasa o falla (tests, check, `/health` con versión
   esperada). Una duración ("trabajar 2 hs") **no es** una finish condition.
3. El primer token de `$ARGUMENTS` es el id de app (ej: `mi-mesa`); el resto es
   el spec. Creá el **worktree de la corrida**:
   `WT="$(scripts/new-worktree.sh <app> <slug>)"`. Crea (o reutiliza si ya
   existe) `state/worktrees/<app>/<slug>` con rama `factory/<slug>` desde el
   trunk del registry; sólo sincroniza el checkout base `work/<app>`, nunca se
   implementa ahí.
4. Delegá la implementación con `task`, `subagent_type: coder`. Pasale **`$WT`
   como worktree absoluto**, rama, spec, alcance, AGENTS.md de la app y
   comandos de verificación. El orquestador **SOLO orquesta**: nunca edita
   archivos él mismo — inspecciona el diff y los checks devueltos y gestiona
   Git/PR/CI. El coder corre con su modelo fijo `opencode/deepseek-v4.1-flash-free`.
   Si no está disponible, reportá PENDIENTE sin fallback al modelo del
   orquestador.
5. Seguí el skill `factory-ci`: corré la **matriz de checks** con
   `scripts/apps/<app>/check-matrix.sh <base> <head> "$WT"` (o el path en `checks` del registry) (catálogo → normalize_catalog +
   check:catalog; backend → pytest/ruff/ty; API → api:types; frontend →
   lint/typecheck/build; deploy → tests) → commit → push → `gh pr create`.
6. Babysit determinista: `scripts/babysit.sh <app> <pr> --state
   state/reports/<run>/babysit.json` con su anti-loop. No reintentes el mismo
   comando en bucle; cada reintento requiere un cambio (fix, SHA nuevo).
   Disciplina del loop de implementación: una iteración = 1 cambio + 1 check
   + 1 fila en `decisions.tsv`; el cambio que no ayudó se **descarta** (no se
   acumula); plateau → pivotar, no seguir; la finish condition **nunca se
   relaja** para declarar victoria.
7. Escribí el reporte con el skill `factory-report` en `state/reports/` y
   generá la traza de la corrida con
   `scripts/trace-report.py <app> <slug> -o <run dir>/trace.html`; si falla
   (sin db/sin anclas), registralo como PENDIENTE en el reporte, no bloquea el
   cierre.
8. Respondé con: spec, rama/PR, checks y su resultado, estado del CI y
   pendientes. Dejá la decisión de merge/review al humano.
