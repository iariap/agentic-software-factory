#!/usr/bin/env bash
# Matriz de checks de x6200 (ROM de radio en C, Xiegu X6200) según paths
# modificados en el checkout de la app. Fuerza los checks obligatorios por tipo
# de cambio: firmware nativo + chequeos estáticos del repo, framework Python del
# arnés A/B, los gates de `x6200-ab` que corre el workflow ab-testing.yml
# (themes + contracts), el gate de contraste WCAG y el de texto de widgets sobre
# la superficie renderizada, el arranque neo-retro (bootanim), el draw buffer
# parcial, los guards del prune del rootfs, sintaxis de los shell tocados, gate
# de determinismo del arnés de UI y los workflows de GitHub (YAML + `bash -n`
# del shell embebido en `run:`).
# Es la ÚNICA validación de un PR de x6200 desde que sus PRs no corren GitHub
# Actions: cada gate que el CI corría y no esté acá queda huérfano, así que un
# diff que no matchea NINGÚN bloque no sale 0 (ver "Cobertura del diff" al final:
# sólo documentación puede quedar sin gate).
# Cada check informa su exit code (salida estilo mi-mesa: "[check] label ... ok / FAIL:").
# Un check que no puede correr por falta de entorno se reporta como BLOCKED con el
# output real y NO marca FAILED (no cambia el exit de la matriz): el banco QEMU
# (host del owner, no del runner) y los gates que necesitan docker.
# Un bloque que no corre por su condición se reporta [SKIP] con el motivo: "no
# corrió" nunca tiene que parecer "pasó".
#
# El banco QEMU (binario ARM real sin radio: preflight, idempotencia del rebuild y
# captura) tiene su fuente de verdad en el skill de la app
# .agents/pruebas-sin-radio/SKILL.md (del repo de la app, no de este harness).
#
# Uso: scripts/apps/x6200/check-matrix.sh <base> <head> [checkout]
#   base/head: refs LOCALES ya presentes en el checkout (ej: main, HEAD,
#              un <sha>). No se fetchea: un ref ausente es un FAIL explícito, no un
#              "sin cambios" verde.
#   checkout: worktree donde correr los checks (default: APP_CHECKOUT).
#   El checkout base se resuelve desde config/apps/x6200.json (o su overlay .factory/).
#
# Costos medidos: los tests nativos compilan ~25 fuentes C con gcc y corren en
# ~6s (todo el branch de firmware con los 6 chequeos estáticos). El gate de
# determinismo levanta Docker, compila el arnés dentro de la imagen
# x6200-uiharness y lo corre 2x por fixture: ~4s con la imagen ya construida y
# el submódulo lvgl inicializado (el primer build, sin imagen, puede tardar más).
# El framework del arnés A/B corre sin e2e (sin Docker): ~3s con el venv listo;
# los e2e se omiten a propósito (marker e2e en tools/ab_testing/pyproject.toml).
# Los gates de x6200-ab: `themes` es offline y tarda ~0.3s; `contracts` materializa
# el worktree del sha, compila el arnés dentro de la imagen x6200-uiharness y
# captura los escenarios (~39s medidos con 64 fixtures y la imagen ya construida;
# sin imagen, el build `--target ci` que hace el CI puede tardar varios minutos).
# El gate de contraste WCAG renderizado compila el arnés en x6200-uiharness,
# renderiza 24 fixtures (6 temas x 4 estados) y mide con PIL + numpy: ~10s medidos
# con la imagen y el build del arnés ya presentes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
# shellcheck source=../../lib/check_matrix.sh
source "$ROOT/scripts/lib/check_matrix.sh"
eval "$(python3 "$ROOT/scripts/app_config.py" x6200 --sh)"
BASE="${1:?uso: $0 <base> <head> [checkout]}"
HEAD="${2:?uso: $0 <base> <head> [checkout]}"
APP="${3:-$APP_CHECKOUT}"

LOG=/tmp/check-matrix-x6200.log

# Cobertura del diff: `has` (del lib) marca que al menos un bloque se disparó.
# Al final, un diff que no matchea NINGÚN bloque no puede leerse como validación
# (antes salía 0 con todo [SKIP]: un cambio de código sin gate parecía verde).
MATCHED=0
has() { grep -qE "$1" <<<"$CHANGED" && MATCHED=1; }

# Un ref ausente en un clone fresco no puede degradar a "sin cambios": fail-closed.
for ref in "$BASE" "$HEAD"; do
  if ! git -C "$APP" rev-parse --verify --quiet "$ref^{commit}" >/dev/null; then
    echo "FAIL: ref inexistente en $APP: $ref" >&2
    echo "      (no se fetchea; traé el ref localmente o usá un sha/base presentes)" >&2
    exit 2
  fi
done

# Cambios introducidos por HEAD respecto del merge-base con BASE (estilo PR).
CHANGED="$(git -C "$APP" diff --name-only --merge-base "$BASE" "$HEAD")"
if [ -z "$CHANGED" ]; then
  echo "==> sin cambios entre $BASE y $HEAD"
  exit 0
fi

# Path absoluto y commit resuelto: los gates de más abajo se los pasan a
# herramientas que corren desde otro cwd (`x6200-ab contracts --repo/--revision`
# evalúa el commit, como el head sha del PR en el CI, no el árbol sucio del
# checkout).
#
# TODO check que recibe el checkout como ARGUMENTO (o como prefijo de PATH) usa
# "$APP_ABS", nunca "$APP": `run`/`run_ab` hacen `cd "$APP"` antes de invocar, así
# que un path relativo se resolvía DOS veces (`<checkout>/<path relativo>`) y el
# check moría con FileNotFoundError —medido con
# `check-matrix.sh <base> <head> state/worktrees/x6200/<slug>` en check_cfg_fills—
# aunque el mismo check pasaba con el path absoluto. El gate de merge
# (merge.sh, provider=none) fallaba por esa razón espuria.
# Test: TestX6200CheckMatrixCheckoutPath (scripts/test_harness.py).
APP_ABS="$(cd "$APP" && pwd)"
HEAD_SHA="$(git -C "$APP" rev-parse "$HEAD^{commit}")"

FAILED=0
# Ejecuta un comando dentro del checkout de la app, con tail del log si falla.
run() {
  local label="$1"; shift
  echo "==> [check] $label"
  if (cd "$APP" && "$@") >"$LOG" 2>&1; then
    echo "    ok"
  else
    echo "    FAIL:"; sed 's/^/    /' "$LOG" | tail -20
    FAILED=1
  fi
}

# Igual que run(), pero anclado a tools/ab_testing/ (entorno uv del framework A/B).
run_ab() {
  local label="$1"; shift
  echo "==> [check] $label"
  if (cd "$APP/tools/ab_testing" && "$@") >"$LOG" 2>&1; then
    echo "    ok"
  else
    echo "    FAIL:"; sed 's/^/    /' "$LOG" | tail -20
    FAILED=1
  fi
}

# Igual formato que run(), pero para un check que NO puede correr por falta de
# entorno (p. ej. el banco QEMU, que necesita el docker/imagen/volumen/chroot del
# host del owner). Deja el output real visible como BLOCKED y NO marca FAILED: no es
# un fallo del cambio y no tiene que romper la matriz ni el CI del runner.
blocked() {
  local label="$1"
  echo "==> [check] $label"
  echo "    BLOCKED: falta entorno (corre en el host del owner, no en el runner):"
  sed 's/^/    /' "$LOG" | tail -30
}

# Igual que run_ab(), pero un exit 2 del CLI (BLOCKED: "no evaluado", p. ej. el
# arnés no compila o falta docker) se reporta como BLOCKED con el output real en
# vez de FAIL. No es un pase: queda visible en la salida con su razón, y una
# violación de verdad (exit 1) sí marca FAILED.
run_ab_or_blocked() {
  local label="$1"; shift
  echo "==> [check] $label"
  local rc=0
  (cd "$APP/tools/ab_testing" && "$@") >"$LOG" 2>&1 || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "    ok"
  elif [ "$rc" -eq 2 ]; then
    echo "    BLOCKED: el gate no evaluó (exit 2). NO es PASS:"
    sed 's/^/    /' "$LOG" | tail -20
  else
    echo "    FAIL:"; sed 's/^/    /' "$LOG" | tail -20
    FAILED=1
  fi
}

# Crea el venv del framework A/B con uv sync si falta (uv run auto-sincroniza,
# pero el CI lo hace explícito y acá el primer sync puede tardar).
ensure_ab_venv() {
  [ -d "$APP/tools/ab_testing/.venv" ] || run_ab "uv sync (ab_testing)" uv sync
}

# --- Firmware / nativo ------------------------------------------------------
# src/** (firmware C/C++), lv_conf.h + lv_drv_conf.h (config LVGL del build) y el
# CMakeLists.txt raíz (entrypoint del cross-compile) afectan lo que se compila en
# la radio. Los chequeos estáticos del repo leen, cada uno, paths de src/ (o del
# tema), así que se corren juntos y no "a ciegas":
#   check_cfg_fills.py     -> src/cfg/{cfg.h,cfg.c} (struct cfg sin inicializar mata la GUI)
#   check_actions.py       -> src/dialog_settings.c + src/*.{c,cpp} (acción sin case)
#   check_settings_focus.py-> src/dialog_settings.c (widget sin grupo de foco)
#   check_apps_menu.py     -> src/main_screen.c + src/buttons.cpp (app fuera del menú)
#   check_app_handlers.py  -> src/*.{c,cpp} + src/cfg/*.c (botón sin handler)
#   check_manual_apps.py   -> src/main_screen.c + docs/manual/MANUAL.md (app sin sección)
#   check_wcag.py          -> src/theme.{h,c} + rootfs/.../themes/*.json (contraste WCAG)
#   check_cw_tune_feed.py  -> src/cw.cpp + src/cw_tune_ui.{c,h} (feed del tono de CW)
#   check_settings_grid.py -> src/dialog_settings.c (un solo esquema de grilla de Ajustes)
#   check_info_bar.py      -> src/ui_tokens.h + src/info.c + src/styles.c (barra izquierda de HOME, #251)
#   check_build_identity.py -> tools/compila-gui.sh + CMakeLists + src/main.c + tools/fuente-md5.sh (sello de fuente)
#                             (corre en su bloque, más abajo: también lee tools/, no sólo src/)
#   check_autotune_v2.py y check_dialog_coverage.py también leen src/, pero además el
#                             inventario del arnés (tools/ui_harness/) y, el segundo,
#                             tests/qemu/: corren en su propio bloque, más abajo.
# El path real de la suite nativa es tests/native/run_native_tests.sh (no
# tests/run_native_tests.sh; tests/run_all_tests.sh la incluye junto al resto).
if has '^(src/|lv_conf\.h$|lv_drv_conf\.h$|CMakeLists\.txt$)'; then
  run "native tests (tests/native/run_native_tests.sh)" sh tests/native/run_native_tests.sh
  run "check_cfg_fills" python3 tests/check_cfg_fills.py "$APP_ABS"
  run "check_actions" python3 tests/check_actions.py
  run "check_settings_focus" python3 tests/check_settings_focus.py
  run "check_apps_menu" python3 tests/check_apps_menu.py
  run "check_app_handlers" python3 tests/check_app_handlers.py
  run "check_manual_apps" python3 tests/check_manual_apps.py
  run "check_wcag" python3 tests/check_wcag.py
  run "check_cw_tune_feed" python3 tests/check_cw_tune_feed.py
  run "check_settings_grid" python3 tests/check_settings_grid.py
  run "check_info_bar" python3 tests/check_info_bar.py
else
  skip "firmware / nativo" "sin cambios en src/**, lv_conf.h, lv_drv_conf.h ni CMakeLists.txt"
fi

# --- Inventario del arnés de host (checks estáticos que leen tools/ y tests/) ---
# check_autotune_v2.py fija el contrato de Auto Tune sobre src/ (textos, ids y
# pureza del núcleo) y ADEMÁS exige que los fixtures de la familia existan en
# tools/ui_harness/fixtures/ y tengan rama en tools/ui_harness/src/screens.c (un
# fixture sin rama sería un verde falso). check_dialog_coverage.py ata
# tests/qemu/dialog-coverage.csv a los mocks de tools/ui_harness/mocks/, a las
# fuentes reales de src/ y a las 25 apps de remote_server.cpp::app_names[]
# (issue #222). Ninguno de los dos entra en el bloque de firmware (que sólo
# dispara con src/): un fixture, un mock o la tabla de tests/qemu/
# desactualizados son rojo en el CI aunque el diff no tenga una línea de C. Por
# eso tienen su propio bloque `has`, como check_build_identity.
if has '^(src/|tests/|tools/ui_harness/)'; then
  run "check_autotune_v2" python3 tests/check_autotune_v2.py
  run "check_dialog_coverage" python3 tests/check_dialog_coverage.py
else
  skip "inventario del arnés de host (autotune_v2 + dialog_coverage)" "sin cambios en src/**, tests/** ni tools/ui_harness/**"
fi

# --- Identidad del build (issue #165) --------------------------------------
# El sello de fuente tiene que estar en la copia, el binario y la radio: el check
# lee tools/ (compila-gui.sh, fuente-md5.sh, captura-banco.sh) además de src/ y
# CMakeLists.txt, así que no entra en el bloque de firmware (que sólo dispara con
# src/). Por eso tiene su propio bloque `has`.
if has '^(src/|CMakeLists\.txt$|tools/compila-gui\.sh$|tools/fuente-md5\.sh$|tools/captura-banco\.sh$)'; then
  run "check_build_identity" python3 tests/check_build_identity.py
else
  skip "check_build_identity" "sin cambios en la identidad del build"
fi

# Temas de la imagen: solo el chequeo de contraste los lee.
if has '^rootfs/.*themes/.*\.json$'; then
  if has '^src/'; then
    skip "check_wcag (temas de la imagen)" "ya corre en el bloque de firmware (src/ cambió)"
  else
    run "check_wcag (temas de la imagen)" python3 tests/check_wcag.py
  fi
else
  skip "check_wcag (temas de la imagen)" "sin cambios en rootfs/**/themes/*.json"
fi

# --- Framework Python del arnés A/B ----------------------------------------
# ruff mira sólo código Python del framework (host-side, nunca en la ROM). pytest
# valida además el INVENTARIO versionado del arnés: los fixtures .scn, los golden
# y las tablas de quality/*.yaml (waivers de temas incluidos), y — en
# tools/ab_testing/tests/test_scenarios.py::test_repo_fixtures_are_all_parsable —
# la lista EXACTA de fixtures de tools/ui_harness/fixtures/. Por eso ruff dispara
# con `*.py` y pytest con CUALQUIER archivo bajo tools/ab_testing/ **o
# tools/ui_harness/**: un fixture nuevo o borrado en tools/ui_harness/fixtures/
# es rojo en el CI aunque el diff no toque ni una línea de Python ni nada bajo
# tools/ab_testing/ (el PR #237 del fixture locked-feedback.scn pasó la matriz
# local y falló en el CI justo por esto). Pytest va sin los markers e2e (piden
# Docker/git y son lentos).
if has '^tools/ab_testing/.*\.py$'; then
  ensure_ab_venv
  run_ab "ruff check (ab_testing)" uv run ruff check .
  run_ab "ruff format --check (ab_testing)" uv run ruff format --check .
else
  skip "ruff del framework A/B" "sin cambios en tools/ab_testing/**/*.py"
fi

if has '^(tools/ab_testing/|tools/ui_harness/)'; then
  ensure_ab_venv
  run_ab "pytest -m 'not e2e' (ab_testing)" uv run pytest -m "not e2e" -q
else
  skip "pytest del framework A/B" "sin cambios en tools/ab_testing/** ni tools/ui_harness/**"
fi

# --- Gates de x6200-ab (espejo de .github/workflows/ab-testing.yml) --------
# El workflow corre en cada PR que toca tools/**, src/**, tests/**, los temas de
# la imagen o su propio archivo, y el CI es quien decide: `x6200-ab themes` en el
# job UI Functional Tests (offline: src/theme.h + temas servidos +
# quality/theme_pairs.yaml) y `x6200-ab contracts` en UI Quality Contracts
# (materializa el sha, compila el arnés en x6200-uiharness y evalúa los
# contratos). Antes esta matriz no los corría, así que un rol de tema nuevo sin
# declarar o un contrato violado sólo se veía como rojo del CI. Se espejan el
# trigger (los paths del workflow) y el comando del CI; no hay versión local del
# gate.
if has '^(tools/|src/|tests/|rootfs/usr/share/x6200/themes/|\.github/workflows/ab-testing\.yml$)'; then
  ensure_ab_venv
  run_ab "x6200-ab themes" uv run x6200-ab themes
  # El CI corre contracts sobre el head sha del PR; localmente, sobre el commit
  # de $HEAD (no sobre el árbol sucio). Sin docker el gate no puede correr: se
  # declara BLOCKED, nunca PASS.
  if ! command -v docker >/dev/null 2>&1; then
    echo "==> [check] x6200-ab contracts ($HEAD_SHA)"
    echo "    BLOCKED: docker no está disponible en este host (el CI lo tiene):"
    echo "    acción: instalá docker o corré el gate en un host con docker"
  else
    # El job construye la imagen antes de evaluar (target ci). Si falta, se
    # construye acá con el mismo comando en vez de fallar con un error opaco.
    if ! docker image inspect x6200-uiharness >/dev/null 2>&1; then
      run "docker build (x6200-uiharness, target ci)" docker build -t x6200-uiharness -t x6200-uiharness-ci --target ci tools/ui_harness
    fi
    if docker image inspect x6200-uiharness >/dev/null 2>&1; then
      run_ab_or_blocked "x6200-ab contracts ($HEAD_SHA)" uv run x6200-ab contracts --revision "$HEAD_SHA" --repo "$APP_ABS"
    else
      echo "==> [check] x6200-ab contracts ($HEAD_SHA)"
      echo "    BLOCKED: falta la imagen x6200-uiharness (el build con target ci falló arriba)"
    fi
  fi
else
  skip "gates de x6200-ab (themes + contracts)" "sin cambios en tools/**, src/**, tests/**, los temas de la imagen ni ab-testing.yml"
fi

# --- Contraste WCAG sobre la superficie RENDERIZADA (issues #277 y #287) ------
# El CI lo corre en el job `visual` de ab-testing.yml ("Rendered WCAG contrast
# gate (issue #277)" y "Rendered widget text gate (issue #287)") con `uv run
# python tests/check_wcag_rendered.py` y `... tests/check_widget_text.py`. A
# diferencia de check_wcag.py (roles ESTÁTICOS del tema), estos gates renderizan
# la matriz de temas x estados con el arnés REAL y miden el par que el usuario
# ve: check_wcag_rendered compara contra el baseline enumerado
# (tests/wcag_rendered_baseline.json) y check_widget_text mide la tinta y el
# contraste del texto que dibujan los WIDGETS de la ventana modal (el valor de un
# dropdown/spinbox no es un lv_label y el gate anterior no lo veía: en Sunlit
# quedó blanco sobre blanco, 7 widgets con 0 px de tinta). Se disparan con lo que
# invalida su medición: los fuentes que compila el arnés (src/**,
# tools/ui_harness/**), los colores de los temas servidos
# (rootfs/**/themes/*.json) y los propios gates/baseline. Necesitan Docker y la
# imagen x6200-uiharness (es lo único que resuelve los bitmaps images/*.bin);
# donde falta ese entorno se declara BLOCKED, nunca PASS ni SKIP mudo.
if has '^(src/|tools/ui_harness/|rootfs/.*themes/.*\.json$|tests/wcag_rendered_baseline\.json$|tests/check_wcag_rendered\.py$|tests/check_widget_text\.py$)'; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "==> [check] check_wcag_rendered (contraste renderizado)"
    echo "    BLOCKED: docker no está disponible en este host (el CI sí):"
    echo "    acción: instalá docker o corré el gate en un host con docker"
  else
    # El job construye la imagen antes de medir (target ci). Si falta, se
    # construye acá con el mismo comando en vez de fallar con un error opaco.
    if ! docker image inspect x6200-uiharness >/dev/null 2>&1; then
      run "docker build (x6200-uiharness, target ci)" docker build -t x6200-uiharness -t x6200-uiharness-ci --target ci tools/ui_harness
    fi
    if docker image inspect x6200-uiharness >/dev/null 2>&1; then
      # Los gates miden con PIL + numpy: el CI los corre con `uv run python`
      # sobre el entorno del framework A/B (que los trae). Acá se antepone ese
      # venv al PATH para que `python3` resuelva a él, sin dejar de ser el
      # comando `python3 tests/check_*.py` que la matriz enumera.
      ensure_ab_venv
      run "check_wcag_rendered (contraste renderizado)" \
        env PATH="$APP_ABS/tools/ab_testing/.venv/bin:$PATH" python3 tests/check_wcag_rendered.py
      run "check_widget_text (texto de los widgets, #287)" \
        env PATH="$APP_ABS/tools/ab_testing/.venv/bin:$PATH" python3 tests/check_widget_text.py
    else
      echo "==> [check] check_wcag_rendered (contraste renderizado)"
      echo "    BLOCKED: falta la imagen x6200-uiharness (el build con target ci falló arriba)"
    fi
  fi
else
  skip "check_wcag_rendered + check_widget_text (contraste renderizado)" "sin cambios en src/**, tools/ui_harness/**, los temas de la imagen, el baseline ni los gates de texto/contraste"
fi

# --- Arranque neo-retro (issue #192, espejo del job `bootanim` de boot-rootfs.yml)
# El workflow corre `bash tests/check_bootanim.sh`: tests de host del renderer y
# del backend de framebuffer, el binario REAL x6200_bootanim contra un /dev/fb0
# sintético (shim LD_PRELOAD) y los chequeos del script de init. Con el CI de
# GitHub apagado en PRs ese job no lo corre nadie: el gate quedaría huérfano y un
# cambio del arranque se mergearía sin validar. Se espeja el trigger de paths del
# workflow y se EJECUTA el gate (un `bash -n` no dice nada del framebuffer). No
# necesita docker ni /dev/fb0 real.
if has '^(src/bootanim/|src/boot_splash\.|src/main\.c$|src/CMakeLists\.txt$|CMakeLists\.txt$|tests/bootanim/|tests/check_bootanim\.sh$|rootfs/etc/init\.d/)'; then
  run "check_bootanim (arranque neo-retro, #192)" bash tests/check_bootanim.sh
else
  skip "check_bootanim (arranque neo-retro)" "sin cambios en src/bootanim/**, src/boot_splash.*, src/main.c, src/CMakeLists.txt, CMakeLists.txt, tests/bootanim/**, tests/check_bootanim.sh ni rootfs/etc/init.d/**"
fi

# --- Draw buffer parcial con rotación 90 (issue #147) -----------------------
# El job `visual` de ab-testing.yml lo corre con `tests/check_drawbuf_partial.sh`
# después de construir la imagen; el trigger del workflow es el de paths de
# ab-testing.yml (tools/**, src/**, tests/**, los temas de la imagen y el propio
# workflow). Con el CI apagado en PRs, sin este bloque nadie verifica que LVGL
# produzca el MISMO framebuffer físico con cualquier tamaño de draw buffer. El
# script exige docker + la imagen: donde falta ese entorno se declara BLOCKED
# (nunca SKIP mudo ni PASS).
if has '^(tools/|src/|tests/|rootfs/usr/share/x6200/themes/|\.github/workflows/ab-testing\.yml$)'; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "==> [check] check_drawbuf_partial (draw buffer parcial, #147)"
    echo "    BLOCKED: docker no está disponible en este host (el CI sí):"
    echo "    acción: instalá docker o corré el gate en un host con docker"
  else
    # El job construye la imagen antes de correr el gate (target ci).
    if ! docker image inspect x6200-uiharness >/dev/null 2>&1; then
      run "docker build (x6200-uiharness, target ci)" docker build -t x6200-uiharness -t x6200-uiharness-ci --target ci tools/ui_harness
    fi
    if docker image inspect x6200-uiharness >/dev/null 2>&1; then
      run "check_drawbuf_partial (draw buffer parcial, #147)" bash tests/check_drawbuf_partial.sh
    else
      echo "==> [check] check_drawbuf_partial (draw buffer parcial, #147)"
      echo "    BLOCKED: falta la imagen x6200-uiharness (el build con target ci falló arriba)"
    fi
  fi
else
  skip "check_drawbuf_partial (draw buffer parcial)" "sin cambios en tools/**, src/**, tests/**, los temas de la imagen ni ab-testing.yml"
fi

# --- Guards del prune del rootfs (espejo del job `prune` de boot-rootfs.yml) --
# El workflow corre, en bash sin docker ni buildroot, los tests deterministas del
# prune (issue #89): sintaxis del script, que la lista explícita saque el S95gui
# del vendor sin tocar los otros init del vendor y que los cuatro guards
# fail-closed rechacen comodín / path absoluto / traversal / path que instala el
# port SIN borrar nada. Se replica el comando real del workflow paso por paso.
prune_guards() {
  set -euo pipefail

  # El build invoca el script con el rootfs destino como $1:
  #   install(CODE ...)  ->  sh <script> "$ENV{DESTDIR}"   (CMakeLists.txt)
  # El test lo llama IGUAL: `sh <script> <rootfs>`. PORT_ROOTFS es el rootfs/
  # del port (el guard de "no borrar lo propio" lo lee).
  local SCRIPT=buildroot/prune-vendor-rootfs.sh
  local PORT_ROOTFS="$PWD/rootfs"

  echo "== 1) sintaxis =="
  bash -n "$SCRIPT"
  echo "ok: $SCRIPT parsea"

  echo "== 2) rootfs de prueba =="
  local TMP
  TMP=$(mktemp -d)
  # La variable del trap es GLOBAL a propósito: el subshell de `run` dispara el
  # EXIT después de que la función retorna, y un `local` ya no existiría ahí
  # (con `set -u` el trap moriría con "TMP: unbound variable").
  PRUNE_TMP="$TMP"
  trap 'rm -rf "$PRUNE_TMP"' EXIT
  mkdir -p "$TMP/etc/init.d"
  printf 'S20gui!!\n'   > "$TMP/etc/init.d/S20gui"    # 9 bytes, del port
  printf 'vendor-dup\n' > "$TMP/etc/init.d/S95gui"    # el duplicado del vendor
  printf 'vendor-ok\n'  > "$TMP/etc/init.d/S99vendor" # otro init del vendor: NO se toca
  ls -l "$TMP/etc/init.d/"

  echo "== 3) prune (como lo llama el build: sh <script> <rootfs>) =="
  sh "$SCRIPT" "$TMP"

  echo "== 4) asserts before/after =="
  [ -f "$TMP/etc/init.d/S20gui" ]    || { echo "FAIL: S20gui desaparecio"; exit 1; }
  [ ! -e "$TMP/etc/init.d/S95gui" ]  || { echo "FAIL: S95gui sigue en el rootfs"; exit 1; }
  [ -f "$TMP/etc/init.d/S99vendor" ] || { echo "FAIL: S99vendor desaparecio (la lista no es explicita)"; exit 1; }
  echo "ok: S20gui queda, S95gui no, S99vendor queda (lista explicita, no patrones)"

  echo "== 5) guards fail-closed (rc != 0 y NO borrar nada) =="
  guard_must_fail() {
    local label="$1" bad="$2"
    local dir badscript rc=0
    dir=$(mktemp -d)
    mkdir -p "$dir/etc/init.d"
    printf 'S20gui!!\n'   > "$dir/etc/init.d/S20gui"
    printf 'vendor-dup\n' > "$dir/etc/init.d/S95gui"
    badscript=$(mktemp)
    sed "s#^etc/init.d/S95gui#${bad}#" "$SCRIPT" > "$badscript"
    PORT_ROOTFS="$PORT_ROOTFS" sh "$badscript" "$dir" >/dev/null 2>&1 || rc=$?
    if [ "$rc" -eq 0 ]; then
      echo "FAIL: el guard '$label' acepto '$bad' (rc=0)"
      exit 1
    fi
    if [ ! -f "$dir/etc/init.d/S20gui" ] || [ ! -f "$dir/etc/init.d/S95gui" ]; then
      echo "FAIL: el guard '$label' borro algo antes de fallar"
      exit 1
    fi
    echo "ok: '$label' rechazado (rc=$rc), no borro nada"
    rm -rf "$dir" "$badscript"
  }
  guard_must_fail "comodin"                  'etc/init.d/*gui'
  guard_must_fail "path absoluto"            '/etc/init.d/S95gui'
  guard_must_fail "traversal (..)"           '../etc/init.d/S95gui'
  guard_must_fail "path que instala el port" 'etc/init.d/S20gui'

  echo "ko: prune OK — S20gui queda, S95gui no, S99vendor queda; los 4 guards rechazados sin borrar"
  rm -rf "$PRUNE_TMP"
  trap - EXIT
}

# El workflow dispara con buildroot/**, el rootfs del port, CMakeLists.txt (el
# install(CODE) que invoca el script) y su propio archivo. Esos paths no los
# cubre ningún otro bloque: sin este, el prune quedaría sin gate al apagar el CI.
if has '^(buildroot/|rootfs/etc/init\.d/|CMakeLists\.txt$|\.github/workflows/boot-rootfs\.yml$)'; then
  run "guards del prune del rootfs (boot-rootfs.yml)" prune_guards
else
  skip "guards del prune del rootfs" "sin cambios en buildroot/**, rootfs/etc/init.d/**, CMakeLists.txt ni boot-rootfs.yml"
fi

# --- Sintaxis de los shell tocados -----------------------------------------
# Cualquier .sh bajo tests/ o tools/ que el diff modifique tiene que parsear.
TOUCHED_SH="$(grep -E '^(tests|tools)/.*\.sh$' <<<"$CHANGED" || true)"
if [ -n "$TOUCHED_SH" ]; then
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    run "bash -n $f" bash -n "$f"
  done <<< "$TOUCHED_SH"
else
  skip "bash -n de los .sh tocados" "sin cambios en tests/**/*.sh ni tools/**/*.sh"
fi

# --- Gate de determinismo (camino de render) -------------------------------
# El arnés de UI (tools/ui_harness/**) es la base de render del pipeline A/B:
# dos corridas del mismo escenario deben dar PNG idénticos. El gate exige Docker
# y la imagen x6200-uiharness; si falta, falla con la acción de build explícita
# (nunca skip silencioso).
if has '^tools/ui_harness/'; then
  # El arnés compila el LVGL del submódulo pineado: sin inicializar, cmake falla
  # con un error críptico ("/src/lvgl does not contain a CMakeLists.txt"). Se
  # chequea antes y se falla con la acción concreta (nada de skip silencioso).
  if [ ! -f "$APP/lvgl/CMakeLists.txt" ]; then
    echo "==> [check] harness determinism gate"
    echo "    FAIL: submódulo lvgl sin inicializar ($APP/lvgl)"
    echo "    acción: git -C $APP submodule update --init lvgl"
    FAILED=1
  else
    run "harness determinism gate" bash tests/check_harness_determinism.sh
  fi
else
  skip "harness determinism gate" "sin cambios en tools/ui_harness/"
fi

# --- Banco QEMU (binario ARM real, sin radio) --------------------------------
# El banco corre el x6200_gui ARM de la imagen con qemu-arm-static en el HOST del
# owner (docker + imagen x6200-buildroot + volumen x6200-build-output + chroot). En
# un runner sin ese entorno no puede correr: por eso el preflight que falla se
# reporta BLOCKED (visible en el resumen, sin marcar FAILED) y sólo si pasa se corre
# el banco de verdad. Los pasos y sus garantías están en el skill de la app
# .agents/pruebas-sin-radio/SKILL.md (preflight, idempotencia, captura).
if has '^(src/|lv_conf\.h$|tools/ui_harness/)'; then
  # 1) Preflight (tools/captura-banco.sh --check): no toca nada y dice con nombre y
  #    comando qué falta. Si falla es entorno ausente -> BLOCKED con el output real.
  if ! (cd "$APP" && tools/captura-banco.sh --check) >"$LOG" 2>&1; then
    blocked "banco QEMU: preflight (tools/captura-banco.sh --check)"
  else
    echo "==> [check] banco QEMU: preflight (tools/captura-banco.sh --check)"
    echo "    ok"
    # 2) Idempotencia del rebuild: cmake regenera params.db sobre el db ya generado
    #    y make muere con "UNIQUE constraint failed: digital_modes.id". Se borra ese
    #    params.db del build dir del volumen ANTES de reconstruir (el target está en
    #    sql/CMakeLists.txt de la app: MAIN_DB_FN=${CMAKE_BINARY_DIR}/params.db).
    echo "==> [check] banco QEMU: params.db generado (idempotencia del rebuild)"
    if (cd "$APP" && docker run --rm --entrypoint "" \
          -v "${VOLUMEN:-x6200-build-output}":/b "${IMAGEN:-x6200-buildroot}" \
          sh -c 'find /b/build -maxdepth 2 -name params.db -print -delete') >"$LOG" 2>&1; then
      if [ -s "$LOG" ]; then
        echo "    ok (borrado el params.db generado):"; sed 's/^/    /' "$LOG"
      else
        echo "    ok (no había params.db generado)"
      fi
    else
      echo "    FAIL:"; sed 's/^/    /' "$LOG" | tail -20
      FAILED=1
    fi
    # 3) Captura de humo de la home: reconstruye si los fuentes cambiaron y deja el
    #    md5 del binario que corrió como evidencia de qué versión se probó.
    echo "==> [check] banco QEMU: captura de humo (home)"
    if (cd "$APP" && tools/captura-banco.sh /tmp/banco-qemu-home.png) >"$LOG" 2>&1; then
      echo "    ok"
      grep -E 'reconstruyendo|captura:|version:|binario:' "$LOG" | sed 's/^/    /' || true
    else
      echo "    FAIL:"; sed 's/^/    /' "$LOG" | tail -20
      FAILED=1
    fi
  fi
else
  skip "banco QEMU" "sin cambios en src/**, lv_conf.h ni tools/ui_harness/"
fi

# --- Workflows de GitHub (.github/workflows/*.yml) --------------------------
# Un cambio que sólo toca un workflow no matcheaba ningún bloque: la matriz caía
# en [SIN GATE] y salía 2. Un workflow es código (el shell embebido en `run: |`)
# y su YAML roto rompe el CI entero, así que se valida acá: el YAML parsea y
# cada bloque `run` parsea como shell con `bash -n`. Las expresiones `${{ ... }}`
# no son sintaxis de shell: se neutralizan con un placeholder (lo mismo que se
# hizo a mano para validar #310) para que `bash -n` mida sólo el shell que corre
# el runner. Sin pyyaml no se puede parsear el YAML: se declara BLOCKED con el
# motivo (nunca PASS) y no se marca FAILED, porque es entorno, no el cambio.
workflows_check() {
  set -euo pipefail
  python3 -c 'import yaml' 2>/dev/null || {
    echo "pyyaml no está disponible en python3: no se puede validar que el YAML parsee"
    return 2
  }
  local f path dir script
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    # Los paths del diff son relativos a la raíz del repo: se resuelven contra
    # $APP_ABS (la matriz puede invocarse desde cualquier cwd y el checkout
    # relativo no resuelve fuera de su raíz).
    path="$APP_ABS/$f"
    [ -f "$path" ] || { echo "FAIL: $f no existe en el árbol del PR"; return 1; }
    echo "== $f =="
    dir="$(mktemp -d)"
    if ! python3 - "$path" "$dir" <<'PY'
import pathlib
import re
import sys

import yaml

src = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
try:
    doc = yaml.safe_load(src.read_text(encoding="utf-8"))
except yaml.YAMLError as exc:
    print(f"FAIL: {src} no parsea como YAML: {exc}")
    sys.exit(1)
if not isinstance(doc, dict):
    print(f"FAIL: {src} no es un mapping YAML")
    sys.exit(1)

scripts = []


def walk(node):
    if isinstance(node, dict):
        run = node.get("run")
        if isinstance(run, str):
            # Un step con `shell` explícito que no es bash/sh (python, pwsh) no
            # se valida con bash -n: sería un FAIL falso del workflow.
            shell = str(node.get("shell") or "")
            if not shell or re.match(r"^(ba)?sh\b", shell):
                scripts.append(run)
        for key, value in node.items():
            if key != "run":
                walk(value)
    elif isinstance(node, list):
        for item in node:
            walk(item)


walk(doc)
for index, script in enumerate(scripts, 1):
    neutral = re.sub(r"\$\{\{.*?\}\}", "PLACEHOLDER", script, flags=re.DOTALL)
    (out / f"{index:02d}.sh").write_text(neutral + "\n", encoding="utf-8")
print(f"    {len(scripts)} bloque(s) run extraídos")
PY
    then
      rm -rf "$dir"
      return 1
    fi
    for script in "$dir"/*.sh; do
      [ -e "$script" ] || continue
      if ! bash -n "$script"; then
        echo "FAIL: bash -n del shell embebido de $f ($(basename "$script"))"
        rm -rf "$dir"
        return 1
      fi
    done
    rm -rf "$dir"
    echo "ok: $f parsea (YAML + shell embebido)"
  done <<<"$WORKFLOWS"
}

WORKFLOWS="$(grep -E '^\.github/workflows/.*\.ya?ml$' <<<"$CHANGED" || true)"
if has '^\.github/workflows/.*\.ya?ml$'; then
  echo "==> [check] workflows: YAML parsea + bash -n del shell embebido"
  wf_rc=0
  workflows_check >"$LOG" 2>&1 || wf_rc=$?
  case "$wf_rc" in
    0) echo "    ok" ;;
    2)
      echo "    BLOCKED: falta entorno para validar el YAML (no es PASS):"
      sed 's/^/    /' "$LOG" | tail -5
      ;;
    *)
      echo "    FAIL:"; sed 's/^/    /' "$LOG" | tail -20
      FAILED=1
      ;;
  esac
else
  skip "workflows de GitHub (YAML + shell embebido)" "sin cambios en .github/workflows/*.yml"
fi

# --- Cobertura del diff ------------------------------------------------------
# Un diff que no matchea NINGÚN bloque salía 0 con todo [SKIP]: un cambio de
# código sin gate se leía como validación. Ahora: se declara [SIN GATE] y sólo
# se sale 0 si TODOS los paths cambiados son documentación; cualquier otro path
# sin bloque es exit 2 (falta agregar el gate a la matriz).
if [ "$MATCHED" -eq 0 ]; then
  echo "==> [SIN GATE] ningún bloque cubre el diff: $(tr '\n' ' ' <<<"$CHANGED" | sed 's/ $//')"
  NOT_DOCS="$(grep -vE '(^docs/|\.md$|\.txt$|(^|/)LICENSE)' <<<"$CHANGED" || true)"
  if [ -n "$NOT_DOCS" ]; then
    echo "FAIL: hay paths sin gate en la matriz (un cambio de código sin gate no es una validación):" >&2
    printf '%s\n' "$NOT_DOCS" | sed 's/^/  - /' >&2
    echo "      acción: agregá el bloque que corresponda a scripts/apps/x6200/check-matrix.sh" >&2
    exit 2
  fi
  echo "    (sólo documentación: no hay gate que correr)"
fi

exit "$FAILED"
