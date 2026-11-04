# Spec: docs-ci — CI para PRs doc-only en el harness (asf#39, opción B del owner)

**Fecha**: 2026-10-15 · **Corrida**: `factory/docs-ci` (en el REPO asf) · **Worktree**: `state/worktrees/asf/docs-ci`
**Issue**: asf#39 (`merge.sh: PRs doc-only sin checks no se pueden fusionar`). El owner eligió
**B**: un workflow chico `docs-ci.yml` para que los PRs doc-only SÍ tengan checks (mantener
el fail-closed uniforme de `merge.sh`, sin agujeros).

## Tarea
1. **`.github/workflows/docs-ci.yml`** en el repo asf:
   - `on: pull_request` con `paths: ['**.md', 'docs/**', 'specs/**', 'config/policies/**']`
     (los Cambios doc-shaped de la casa; si querés otra lista, la decidís y la documentás).
   - Un job corto (ubuntu-22.04) que corra un linter de markdown SENCILLO:
     `npx markdownlint-cli2 "**/*.md" "#node_modules" "#.factory"` básico, con un
     `.markdownlint.yml` MÍNIMO y explicable (la casa escribe con frases largas en es-AR —
     si las reglas default generan 200 avisos, configuralas para no hacer ruido: por
     ejemplo desactivar `MD013` (long-line) y mantener los útiles. La meta es que un PR
     doc QUEBRARA solo si tiene errores reales (formato/estructura), no por estilo).
   - Si `markdownlint` vía npx se resulta pesado (instalación), alternar a un python
     linter sin dep (`python3 -m pymarkdown`?) — elegí la opción que el runner default
     correría SIN caché de instalación menuda. Documentá la letra que elegiste con
     el por qué en el header del yaml.
   - `workflow_dispatch` también (dispara manual sobre un branch si conviene re-validar).
2. **Ver que funciona en la práctica**: el propio PR de este cambio es el PR doc-only que
   MATRIA el defecto — después de este, la CI de docs le debería tomar los checks (
   `mdfiles Changed → docs-ci run`). Comprobarlo con las runs de este PR mismo (el body
   es doc-only: si el workflow sale verde, y el `merge.sh` ya ve checks>0 → pasa).
   Si el filtro paths caso el PR de este change debe trigger del workflow (se decidía simown).
3. **No tocar** `scripts/merge.sh` (sin chequeo del path; queda fail-closed igual).

## Gates (el propio harness)
```bash
uv sync && uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
python3 scripts/test_harness.py        # testests del harness
# (nuestro cambio es solo .github/workflows + config: el resto no la tocan; CI accélérator)
```
Si el change-paper incluye un script Python nuevo (no debería — es CI y config), entonces sí seguir.

## Invariantes
- NO tocar `config/apps/**`, `config/policies/**`, `.opencode/**`, `.agents/**`, scripts (nada).
- Commits `Pablo Iaria <iariap@gmail.com>` — **Sin push/PR** (el orquestador publica).
- El CI de asf van con .github (high risk): el merge va por la orden explícita (given) del owner
  (la misma orden permanente verde+mergeable que cubre asf#37/38).

## Reporte final
1. El yaml + config con la elección fundada del linter.
2. La practica real: el check de este mismo PR obteniendo verde (o el demoscopio: cualess checks
   esperado un PR doc-only).
3. Gates del harness con salidas reales.
4. PENDIENTES honestos.
