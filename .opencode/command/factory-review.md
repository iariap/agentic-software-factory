---
description: Etapa 5 del factory: clasifica el riesgo de un PR, corre los reviewers de dominio sobre el SHA exacto (worktree) y postea hallazgos. Alto riesgo queda para aprobación humana.
agent: build
---

Revisión agentica multi-lente. `$ARGUMENTS` = `<app> <pr>` (id de app del registry primero).

1. Cargá `factory-context` y sincronizá `work/` si hace falta.
2. Resolvé app/PR, los SHAs, y prepará un **worktree inmutable en el head exacto**:
   ```bash
   read -r APP PR <<< "$ARGUMENTS"          # ej: mi-mesa 42
   eval "$(python3 scripts/app_config.py "$APP" --sh)"   # APP_REPO, APP_CHECKOUT, ...
   BASE_SHA="$(gh pr view "$PR" --repo "$APP_REPO" --json baseRefOid --jq .baseRefOid)"
   HEAD_SHA="$(gh pr view "$PR" --repo "$APP_REPO" --json headRefOid --jq .headRefOid)"
   WT="$(pwd)/state/worktrees/$APP/pr-$PR"  # path absoluto FUERA del repo de la app
   git -C "$APP_CHECKOUT" fetch origin "$HEAD_SHA" --depth 1
   rm -rf "$WT" && mkdir -p "$(dirname "$WT")"
   git -C "$APP_CHECKOUT" worktree add --detach "$WT" "$HEAD_SHA"
   trap 'git -C "$APP_CHECKOUT" worktree remove --force "$WT" 2>/dev/null || true; rm -rf "$WT"' EXIT
   ```
   Usá `$WT` como checkout de referencia para los reviewers. Registrá
   `BASE_SHA` y `HEAD_SHA` en el reporte.
3. Clasificá el riesgo del diff **inmutable** (los SHAs ya capturados, no un
   diff "en vivo" del PR):
   ```bash
   FILES="$(git -C "$APP_CHECKOUT" diff --name-only "$BASE_SHA" "$HEAD_SHA")"
   scripts/risk.py "$APP" --files $FILES
   ```
   Determiná reviewers requeridos y aprobación humana (policy mergeada:
   `config/policies/base.json` + `config/policies/<app>.json`).
4. **Decidí si dispara auditoría de seguridad** (criterios en
   `security-audit/SKILL.md` → "Disparadores"): si el diff toca
   auth/datos sensibles/infra de deploy/pipeline AI/deps/migraciones, o el
   riesgo es high, delegá al subagent `security-guardian` una auditoría
   scoped (`scope_paths` = files del diff, perfil `quick`) sobre `$WT` en
   paralelo con los reviewers. Si no vale la pena, registralo igual:
   `security-audit: skip (razón)`.
5. Pasá a cada reviewer del riesgo (subagents `reviewer-security`,
   `reviewer-infra`, `reviewer-frontend`, `reviewer-data`) el diff
   `BASE_SHA..HEAD_SHA` y la ruta `$WT` como checkout. Usá la herramienta
   `task`. Cada reviewer reporta con su lente.
6. Consolidá los hallazgos **con buckets**: `act-on` (bug o regresión real,
   con evidencia) vs `dismissed` (ruido/nitpick/falso positivo, con la
   razón del descarte). Un hallazgo sin evidencia que lo respalde va a
   `dismissed` o vuelve al reviewer, nunca al PR como act-on. Posteá un
   comentario al PR
   (`gh pr comment "$PR" --repo "$APP_REPO" --body ...`) con los buckets
   por dominio, severidades y veredicto por reviewer. Los buckets no
   silencian: el humano overridea cualquiera de los dos lados.
   Para riesgo **high**: panel adversarial sí o sí; para **medium**, a
   juicio del orquestador (diff que cruza superficies compartidas/contratos
   o ≥3 dismissed con la misma razón). Ver `/factory-interrogate`.
7. **Enforcement de riesgo**:
   - Publicá el resultado como check requerido:
     `scripts/publish-risk.sh "$APP" "$PR"` (requiere GITHUB_TOKEN con checks:write;
     si falla por permisos, registralo como PENDIENTE). El nivel lo calcula
     el script con risk.py, nunca se pasa a mano.
   - Si el riesgo es **high**: marcá el PR como "requiere aprobación humana"
     (comentario + label `needs-human-approval`). NO mergé, NO apliques
     auto-approve.
   - Si es **low** con `auto_approve: true`: dejalo listo para merge sin
     aprobación humana (comentario de confirmación).
   - Si es **medium**: requiere reviewers del nivel; dejá el merge al humano.
8. Guardá el reporte en `state/reports/` con el skill `factory-report`.
   El worktree se limpia automáticamente con el trap al terminar.
9. Respondé con: riesgo, BASE/HEAD SHA, reviewers ejecutados, veredictos,
   si se disparó security-audit (y su veredicto) y si requiere aprobación
   humana.