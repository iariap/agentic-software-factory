# Spec: pulido trace-report — ruidos en la tabla de Rendimiento

App: `asf` · Rama: `factory/trace-polish` (micro-PR post-#27, base `32fb62e`)

## Qué (hallazgo de validación contra traces reales del 2026-10-09)
En la vista **Rendimiento**, la columna `own_ms` es idéntica a `wall_ms` para
todo subagente que no delega (coder, reviewers, interrogator en datos reales),
porque `own = wall − Σ task anidados` y no tienen. Dos columnas iguales por
fila son ruido visual.

## Cambio (display-only; los datos del JSON no cambian)
- En la tabla por agente: si `own_ms == wall_ms`, la celda own muestra `—`
  (con `title="sin delegaciones propias"`); si difieren, el valor numérico
  como hoy. Lo mismo en la barra SVG de own_ms (si own==wall, barra
  idéntica a wall no aporta: mostrar la barra wall sola, own sin barra
  duplicada). `trace-data` conserva ambos valores intactos (determinismo).

## Tests
- Fixture v3 ya tiene coder re-invocado SIN delegaciones propias → assert en
  el HTML: fila coder contiene `—` con title, fila build (own<wall) conserva
  numérico; JSON embebido sigue teniendo `own_ms` numérico para coder.
- Determinismo byte-a-byte con `--now` fijo: no romperse.

## Finish condition
```bash
python3 scripts/test_harness.py
uv run ruff check scripts/ && uv run ruff format --check scripts/ && uv run ty check scripts/
python3 scripts/trace-report.py mi-mesa household-measures-146 -o /tmp/trace-polish.html
grep -c 'title="sin delegaciones propias"' /tmp/trace-polish.html   # >=4
```

## Fuera de alcance
- Cualquier otra vista/dato; regeneración de trazas viejas (la hace el
  orquestador con el comando, no es código).
