# Fase 1 — Reconocimiento

Objetivo: mapas de superficies y trust boundaries de la app + ledger inicial
de cobertura. Sin esto, la caza es a ciegas.

## Pasos

1. **Resolver el target**: app del registry (`scripts/app_config.py <app>
   --sh`), checkout (`APP_CHECKOUT` o worktree indicado), `source_ref`
   (SHA de git + flag de working tree dirty).
2. **Contexto de la app**: `AGENTS.md` (invariantes), `.factory/app.json`
   (checks, entornos, health), docs de seguridad/privacidad declarados
   (p. ej. `PHI.md`). Lo que la app no declare no se infiere: se pregunta
   al orquestador o queda `blocked`.
3. **Corridas previas**: leer `state/reports/<app>/<run anterior>/`
   (`coverage-ledger.json` + `findings.json`):
   - Hallazgo previo `confirmed` + código sin cambios → carry-over al run
     actual (re-verificación final igual igual).
   - Hallazgo previo `confirmed` + código cambiado → unidad de
     re-validación planificada en el run actual.
   - `needs_validation`/`deferred`/`blocked` previos → trabajo del run
     actual. Los estados previos **nunca** suprimen una unidad del run.
   - Sin corridas previas: declararlo en el reporte.
4. **Mapear** (leyendo código, no ejecutando):
   - Rutas/API expuestas (backend y frontend) y qué auth protege cada una.
   - Trust boundaries: qué dato cruza de dónde a dónde (usuario→modelo,
     usuario→DB, config→código, CI→deploy, terceros→app).
   - Superficies de entrada: inputs de usuario, webhooks, file uploads,
     parsing de emails/documentos, parámetros de AI (tools, RAG).
   - Secretos y config: dónde se esperan (`Secret Manager`, env) y dónde
     no.
   - Deploy/IaC/CI: Dockerfile, workflows, servicios públicos, service
     accounts, migrations.
   - Dependencias: manifiestos y lockfiles reales (pyproject/uv.lock,
     package.json/bun.lock).
5. **Escribir** `run-metadata.json` y `coverage-ledger.json` inicial.

## Unidad de cobertura

```json
{ "id": "U1", "surface": "POST /api/v1/plans", "boundary": "usuario→DB",
  "attack_class": "injection", "subsystem": "backend", "status": "planned" }
```

- `quick`: subsistema fijo `"all"`; las unidades son superficie × boundary
  × clase.
- `standard`: subsistema real (backend/frontend/deploy/ci/ai/...).
- `deep`: además, split por modo de ciclo de vida (creación/lectura/
  mutación/borrado) cuando aplique.

Superficies que nadie cazará no van como `done`: quedan `planned` o
`out_of_scope` (con razón registrada en el metadata `scope_paths`).
