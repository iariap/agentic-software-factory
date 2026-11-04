# Spec: onboarding-x6200 — alta de x6200_gui como app del registry

**Fecha**: 2026-10-14 · **Corrida**: `factory/onboarding-x6200` · **App**: asf (harness)

## Qué se quiere y por qué (outcome)

Incorporar la ROM del Xiegu X6200 (`iariap/x6200_gui`, trunk `lu2dsx/x6200-rom`)
como aplicación de primera clase del harness, para que las corridas
`/factory-build|review|deploy` operen sobre ella con las mismas garantías que
mi-mesa y radio-contest: registry resoluble, política de riesgo por path, y
matriz de checks que reaprovecha el **arnés existente de UI A/B de LVGL 8**
(mergeado en `lu2dsx/x6200-rom` vía PR #1: `tools/ui_harness/`,
`tools/ab_testing/`, `tests/` nativas y estáticas).

El trabajo es **governance del harness** (cambio high-risk por policy; requiere
aprobación humana en el merge) más el archivo de metadata en el repo de la app.

## Alcance

1. **Puntero harness**: `config/apps/x6200.json` (mínimo, patrón de los otros:
   id/repo/remote/branch/checkout/note). Checkout base: `work/x6200_gui`;
   trunk: `lu2dsx/x6200-rom`; remote: `https://github.com/iariap/x6200_gui.git`.
2. **Metadata de la app**: `.factory/app.json` + `.factory/policy.json` en el
   repo de la app (overlay: gana sobre el puntero; `checks` vive acá, patrón
   mi-mesa). Policy por path con foco ROM: `src/**` firmware = high;
   submódulo `lvgl` pineado v8.3.11 = high (no actualizar); `lv_conf.h`,
   drivers y buildroot = high/medium; arnés A/B (`tools/ab_testing/**`,
   `tests/**`, `docs/**`) = low; `tools/ui_harness/**` = medium (C + Docker).
3. **Matriz de checks**: `scripts/apps/x6200/check-matrix.sh <base> <head>
   [checkout]` (interfaz mi-mesa), selectiva por paths: cambios de firmware →
   tests nativas (`tests/run_native_tests.sh`) + chequeos estáticos del repo;
   cambios Python del framework → ruff + pytest `-m "not e2e"` (uv); siempre
   que el arnés toque camino de render → gate de determinismo
   (`tests/check_harness_determinism.sh`). Sin X11/GPU ni fixtures e2e pesadas
   en la matriz local.
4. **Fallback transitorio**: `config/policies/x6200.json` con la misma policy
   (AGENTS.md: apps sin checkout sincronizado caen acá).
5. **PRs**: (a) harness `factory/onboarding-x6200` → `main` con 1–4 + tests
   del harness verdes; (b) app `factory/onboarding-x6200` → `lu2dsx/x6200-rom`
   en el fork con 2. Review/merge: humano.

## No hacer

- No tocar `src/` del firmware ni el submódulo `lvgl` (pinned `74d0a816`).
- No crear entornos Cloud Run ni `metrics`: la app corre en hardware; no hay
  health_URL real (si algo, se documenta como PENDIENTE).
- No prometer cobertura de M7 (hardware/energía): el onboarding cubre lo que
  ya existe en la ROM mergeada.
- No implementar en `work/x6200_gui` (checkout base, sólo sync).

## Criterios de aceptación (finish condition verificable)

Desde la raíz del harness con `config/apps/x6200.json` en su lugar:

```bash
# 1. El registry resuelve la app (rutas absolutas, trunk, checks).
python3 scripts/app_config.py x6200 --sh          # exit 0, exporta APP_*

# 2. Overlay de la app gana sobre el puntero (con checkout sincronizado).
scripts/sync-work.sh x6200 && python3 scripts/app_config.py x6200 --print checks
#   → scripts/apps/x6200/check-matrix.sh

# 3. La matriz corre y es verde sobre lu2dsx/x6200-rom.
scripts/apps/x6200/check-matrix.sh origin/lu2dsx/x6200-rom HEAD "$APP_CHECKOUT"

# 4. Gates del harness sin frame nuevo roto.
uv run ruff check scripts/ && uv run ty check scripts/
python3 scripts/test_harness.py                    # suite completa, 0 fallos
```

Duración no cuenta como finish. Todo lo anterior pasa o falla.
