# Spec: worktree-subinit — inicializar el submódulo lvgl DENTRO de new-worktree.sh

**Fecha**: 2026-10-15 · Corrida: `factory/worktree-subinit` (repo asf) · Base: main
**Feedback del owner**: "en el repo que se hace el worktree hay un submódulo" — hoy el
init de `lvgl` lo hace el orquestador a mano en CADA invocación (`git submodule update --init
lvgl` de chain); si olvida, la corrida empieza rota (ya pasó una vez: "Fatal: Unable to find
current revision in submodule path 'lvgl'" en el relitigation del incidente del cleanup).

## The fix (mechanismo en el script, no en el orquestador)
1. **`scripts/new-worktree.sh`**: después de crear el worktree (`git worktree add ...`), y
   ANTES del echo final, correr `git -C "$WT" submodule update --init --recursive` con
   time-bound razonable (los submódulos pueden tardar: opción `SUBMODULE_TIMEOUT=${...180}`):
   si falla la red, WARN no-bloqueante (stderr) con el comando que el orquestador debe correr
   a mano — el stdout sigue siendo SOLO el path (contrato mantené).
2. **Detalles**:
   - Idempotente (re-crear un existente no debe romper).
   - La verificación de submodule están el actual del `.gitmodules` del repo de la app (no
     hardcode `lvgl`: genérico).
   - Fail-closed proper: si un submódulo queda en estado no-verificado, WARN el stdout, SIN
     cambiar la salida actual (el contrato es o exit 0/1 actual — verificá y mantené).
   - Agregar al header del script 1 línea de doc.
3. **`scripts/test_harness.py`** (fake-harness test pattern): un test que corra el script con
   un seed repo que TENGA submódulo (seed local con un repo gemelo, npm... the test fakes ya
   lo hacen con git local: `git submodule add` de un repo local file:// en el seed) →
   aserción: post-new-worktree.sh, el submódulo del worktree está initialized (y el stdout
   que queda SOLO el path). Y otro test sin submódulo (no rompe, stdout igual).

## Gates (salida real)
```bash
uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
python3 scripts/test_harness.py          # incl. test nuevo; vec respetado
bash -n scripts/new-worktree.sh
```

## Fuera de alcance
- NO tocar merge.sh/cleanup.sh/babysit.sh; NO tocar el output contrato del script.

## Reporte
1. Diff del script (init + timeout guard).
2. Tests con salida real (seed con submódulo/seed sin).
3. Gates. 4. PENDIENTES honestos (por ejemplo si no probaste con red).
