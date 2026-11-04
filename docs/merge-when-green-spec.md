# Spec: merge-when-green — babysit REPARADOR (clase asf#41, orden del owner)

**Fecha**: 2026-10-15 · **Corrida**: `factory/merge-when-green` (repo asf) · **Base**: main `3cb6908`
**Orden del owner** (literal): *"arregla el babysit, si falla un check se debe corregir lo que
genera el fallo"*.

## Qué
`scripts/merge-when-green.sh <app> <pr> [--mode shadow|enabled] [--timeout-min N] [--poll-s N]
[--max-repairs N] [--state FILE]`: el flujo completo CI→reparación→merge, con la clase de cada
fallo detectada y el remedio mínimo aplicado. `--mode` default **shadow** (no toca nada,
imprime el plan) — idéntico fail-closed que auto-merge.py.

## Flujo
1. **Validación de entrada** (fail-closed, como `merge.sh`): PR OPEN + MERGEABLE/CLEAN; si no,
   exit 4 con el dato.
2. **Babysit** con `scripts/babysit.sh` (reutilizar; es fail-fast y se probó con fakes):
   - **exit 0** → `merge.sh --apply` (shadow lo reporta; enabled lo ejecuta) → trail del
     squash sha → fin.
   - **exit 3** (timeout) → re-loop con el mismo PR (la CI sigue corriendo; los cruces de
     espera NO consumen `--max-repairs`) hasta el deadline global del wrapper.
   - **exit 2** (checks muertos) → **clasificar** y reparar (§3).
3. **Clasificación del fallo** (determinista, firmas en los logs del run fallido):
   - **CLASE A — flake de bench/carga**: el job caído es Performance/Visual con firma
     `render-cost proxy over budget` / `p95_delta_pct` y el MISMO check de medición en el
     otro step de la corrida está PASS (el patrón conocido de #52). Remedio:
     `gh run rerun --failed` (máx 1 por ronda; cuenta como rep).
   - **CLASE B — base-skew**: el job caído es Visual/Performance con deltas de píxeles
     (`changed_pixels|flushed_pixels` con %) y el **trunk avanzó** respecto del merge-base
     de la rama (`git merge-base` en el repo de la app). Remedio: rebase del head sobre el
     trunk + push `--force-with-lease`, SÓLO si el rebase es conflict-free
     (`git merge-tree --write-tree` exit 0); si no, exit 5 con el dato (el rebase con
     conflicto lo hace el humano/orquestador). Cuenta como rep.
   - **CLASE C — fallo real** (el resto: pytest/nativos/estáticos/otros): **NO se repara
     automáticamente**; extraer evidencia (tests fallidos o líneas de error del log a
     `state/.../repair-evidence-<pr>-<round>.txt`) y **exit 5** con el path — el orquestador
     luego delega el fix al coder (la ruta del flujo vigente).
4. **Techo**: `--max-repairs` (default 2). Agotado: exit 3 con el trail.
5. **Trail** siempre: una línea por acción (ronda, clase, remedio, sha nuevo) a stdout
   (pipeable) + `--state FILE`.

## Estilo del script
- Bash con `set -euo pipefail`; app-agnóstico vía `app_config.py` (primer arg); nada de
  sleep ciego: polls cortos con deadline (los loops existentes de `babysit.sh` y
  `wait_until.sh` son la referencia).
- "Logs del run caído": `gh run view <id> --log-failed` + grep de firmas — todo testeable
  con los fakes.

## Tests (en scripts/test_harness.py, con fakes)
- Escenario verde → exit 0 + merge (enabled) / sólo plan (shadow).
- CLASE A → rerun llamado 1 vez → segunda babysit verde → merge.
- CLASE B → rebase "exitoso" (spy) → CI verde → merge. Con conflicto → exit 5 + dato.
- CLASE C → exit 5 + archivo de evidencia escrito con los fallos.
- `--max-repairs` agotado → exit 3 con trail.
- El fake de gh usa los mismos patrones de fakes que el test_harness ya tiene (hoy: babysit).

## Fuera de alcance
- No tocar babysit.sh / merge.sh / auto-merge.py / risk.py / workflows en este PR (se
  reutilizan; el wrapper es aditivo).
- Clase C no delega automáticamente a subagentes desde bash: el orquestador es quien
  conecta la evidencia con el coder.

## Gates (salida real)
```bash
uv sync && uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
uv run python3 scripts/test_harness.py    # + tus casos nuevos, todos ok
bash -n scripts/merge-when-green.sh
```
(El ciclo real con un PR propio lo corre el orquestador; los fakes cubren el resto en CI.)

## Reporte final
1. El wrapper: uso, exit codes, formato del trail (por clase).
2. Tests con fakes + salida real.
3. Gates del harness.
4. PENDIENTES honestos.
