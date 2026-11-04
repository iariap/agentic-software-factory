---
description: Etapa de merge. Vía rápida de auto-merge (riesgo low + CI verde) y, con orden humana explícita, merge por rama head vía scripts/merge.sh. Con mode=shadow sólo reporta; con mode=enabled mergeea y vigila el deploy, con auto-revert si degrada.
agent: build
---

`$ARGUMENTS` = `<app> <pr>` (id de app del registry primero). Merge con gates del PR.

1. Cargá `factory-context` y sincronizá `work/` si hace falta.
2. Vía rápida (low-risk), con el script:
   ```bash
   scripts/auto-merge.py $ARGUMENTS --apply
   ```
   El script lee `auto_merge` de la policy mergeada (`config/policies/base.json` + overrides de la app):
   - `mode: shadow` → reporta elegibilidad y NO mergea (para desactivar la vía rápida).
   - `mode: enabled` (estado actual, por orden del owner) → mergea si pasa todo.
   - `mode: enabled` → mergea squash si y sólo si: riesgo low, CI verde,
     PR abierto y sin draft, sin label `needs-human-approval`, sin
     `DO-NOT-MERGE` en comentarios, sin archivos prohibidos (migraciones,
     deploy, workflows, env/secrets), y con sección "Validación" en el body.
   - Tras mergear (enabled): vigila el run de main + `/health` de staging.
     Si degrada y `auto_revert` está ON, crea el PR de revert y deja que un
     humano lo fuse. Nunca toca producción.
   - Si la app declara `ci.provider: none` (x6200: valida con su matriz local,
     sin GitHub Actions en PRs) la vía rápida **no aplica**: necesita CI verde.
     En ese caso el gate lo pone `merge.sh`, que **corre la matriz local del
     registry** (`checks`) contra base/head y exige exit 0; el body del PR no se
     lee (un `check-matrix` en un fence no es evidencia).
3. Merge con orden humana (PRs alto/medio o fuera de la vía rápida), por rama
   head —nunca por número literal:
   ```bash
   scripts/merge.sh <app> <head-branch> [--expected-sha SHA] [--squash|--merge] [--apply]
   ```
   El script resuelve el PR por rama head (`gh pr view <rama>`), verifica
   OPEN + MERGEABLE/CLEAN + checks verdes **por nombre** sobre
   `statusCheckRollup` (un nombre con entradas `SKIPPED` y `SUCCESS` cuenta
   verde; sólo `SKIPPED` no: falta la etiqueta `ci`), valida `--expected-sha` y
   el HEAD local, y recién con `--apply` fusiona con el número RESUELTO por rama.
   Con `ci.provider: none` no hay rollup: corre la matriz local del registry
   contra base/head y exige exit 0 (ver arriba).
   Shadow por default. Nunca `--admin`, nunca force-push. Los números de PR son
   globales del repo: resolverlos por rama, no citarlos.
4. Leé el JSON de salida y el reporte en `state/reports/auto-merge/`.
5. Respondé con: riesgo, elegibilidad, bloqueos (si los hay), y acción tomada
   (o qué habría hecho en sombra).

Reglas de merge:
- Vía rápida (`scripts/auto-merge.py`, `mode=enabled`): sólo PRs elegibles low-risk.
- Orden humana explícita: si el humano dice "mergea <pr>", el agente resuelve la
  rama head de ese PR y ejecuta `scripts/merge.sh <app> <head-branch>`; nunca por
  número literal, nunca por iniciativa propia, nunca `--admin`, nunca producción.
