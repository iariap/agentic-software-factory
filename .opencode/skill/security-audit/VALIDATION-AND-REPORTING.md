# Fases 3-6 — Validación adversarial y reporte

## Fase 3: Validación adversarial

Para cada `needs_validation`, el objetivo es **desconfirmarlo**, no
confirmarlo:

1. Leé todo el camino del dato: desde la superficie de entrada hasta el
   intérprete/recurso. Buscá lo que lo invalide: middleware/guards aguas
   arriba, validaciones previas, sanitización, config que cierra la ruta.
2. Verificá la **trust boundary**: principal afectado (quién), recurso
   (qué), outcome (qué consigue que no debe). Sin los tres concretos → no
   es `confirmed`: queda `needs_validation` o `rejected`.
3. Reproducción segura: comandos **pasivos** sobre el checkout (grep,
   parser, scanner local). Prohibido tocar staging/prod o servicios
   compartidos; si el hecho decisivo está fuera del código →
   `needs_validation` con el plan de validación seguro que sugerís.
4. Corroborá CVEs/números contra los manifiestos/lockfiles reales. Sin
   corroboration → `needs_validation`, nunca inventado.
5. Fix: **mínimo efectivo** (el cambio más chico que cierra la boundary),
   propuesto, no aplicado.

## Esquema de `findings.json`

```json
{ "id": "F1", "title": "...", "severity": "high",
  "status": "confirmed", "surfaces": ["POST /x"],
  "boundary": { "principal": "...", "resource": "...", "outcome": "..." },
  "evidence": [ { "kind": "code|command", "location": "archivo:línea",
                  "excerpt": "literal", "command": "..." } ],
  "smallest_fix": "...", "how_to_reproduce": ["..."],
  "origin_run": "<run_id o null>", "note": "..." }
```

Estados: `confirmed` (validado adversarialmente con los 3 elementos de
boundary), `needs_validation` (falta el hecho decisivo), `rejected`
(desconfirmado — queda registrado para no re-cazar), `out_of_scope`,
`deferred`.

## Fase 4: Critic de cobertura

- Releer el ledger: cada unidad `planned` que no corrió → `blocked` con
  razón, o re-asignada a una ola puntual.
- En `standard`/`deep`: gaps detectados durante la validación → ola
  puntual de re-caza. En `quick`: `deferred` (sin re-caza).
- Carry-over de corridas previas re-verificado acá (¿el código sigue igual?).

## Fase 5-6: Reporte y cierre (`REPORT.md`)

Formato:

```markdown
# Security Audit — <app> — <run_id>
Perfil: <quick|standard|deep> · Target: <ruta> · Ref: <sha>[ +dirty]
## Veredicto: LIMPIO | RIESGOS | SIN_DATOS
<una línea de fundamento>
## Hallazgos confirmados
### [<severidad>] <título>  (F<n>)
- Boundary: principal → recurso → outcome
- Evidencia: archivo:línea + extracto literal / comando + output real
- Fix mínimo efectivo (propuesto, no aplicado)
- Cómo reproducir: <comandos>
## Por validar (needs_validation) — no cuentan como hallazgos
<id, título, qué falta, comando exacto para confirmar>
## Cobertura
- Unidades: N planned → done/blocked/out_of_scope (desglose)
- Carry-over de corridas previas: <cuáles y estado>
- Lo que NO se cubrió: <explícito>
## PENDIENTE
<checks no corridos por falta de herramienta/acceso + comando exacto>
```

- Severidad final sólo en `confirmed`; `needs_validation` va aparte y
  **no** eleva el veredicto a `RIESGOS` por sí solo (pero se lista).
- Veredicto: `LIMPIO` = 0 confirmed; `RIESGOS` = ≥1 confirmed;
  `SIN_DATOS` = los blocked/pendientes impiden concluir (se declara).
- Actualizar `run-metadata.json` (`run_status: completed`) y el ledger
  final. Todo lo PENDIENTE queda explícito para la próxima corrida.
