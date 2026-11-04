---
name: wait-for
description: Cómo esperar salidas de comandos en el harness (patrón hasta-condición con techo, nunca sleep a ciegas). Usar siempre que un comando tenga que esperar una salida futura CI, deploy, health, lock, servicio que arranca, archivo que aparece, job remoto. Cubre el piping/redirección de la salida esperada.
---

# Esperar salidas (wait-for)

Toda espera del factory es **hasta-condición con techo**: sondear cada
`<intervalo>`, salir apenas la condición se cumple, cortar con timeout si no.
El `sleep` suelto solo vale como intervalo dentro de un poll — nunca como
espera fija antes de un chequeo.

## Cuándo (toda espera futura)

CI en curso, deploy/build remoto, health de un servicio que arranca,
lock/colas, archivo o job que aparece, cualquier `sleep N && check`.

## Cómo (elegir la forma)

1. **CI de GitHub**: `gh run watch <id> --exit-status` o
   `gh pr checks <pr> --watch` — esperan hasta terminar solos.
2. **Helper del harness** (cualquier otra condición):
   ```bash
   # forma args (sin pipes internos):
   scripts/wait_until.sh 60 5 -- gh pr view "$PR" --json mergeable --jq .mergeable
   # forma shell-string (cuando el comando lleva pipes/redirects internos):
   scripts/wait_until.sh 60 5 -c "gh run list --json status --jq '.[0].status' | grep -q COMPLETED"
   ```
   - Salida temprana garantizada: corta apenas el check pasa (reporta el
     tiempo esperado por stderr).
   - Techo duro: al agotarse, exit 1 — no esperar infinito.
   - **Piping/redirección**: SOLO la salida final (exitosa) sale por
     stdout; los intentos fallidos y el progreso van por stderr. Así
     ```bash
     scripts/wait_until.sh 60 5 -- gh pr view 5 --json url --jq .url > run.json
     ```
     escribe el resultado limpio (sin ruido de reintentos) en el destino.
     Si el destino es otro comando: `... | jq ...`, y el stderr sigue
     mostrando el progreso.
3. **Babysit de PR** (CI + mergeabilidad con anti-loop):
   `scripts/babysit.sh <app> <pr> --state ...` — es la versión completa
   del patrón para babysitear un PR; no reinventarla con `wait_until`.

## Prohibido (el CI lo marca)

- `sleep N && <check>` o `sleep N; <check>`: espera fija que casi siempre
  sobra (pérdida de tiempo) o falta (falso fallo).
- Esperas sin techo (`while ! check; do sleep 1; done` sin deadline).

## Disciplina

- Techo e intervalo con criterio: techo = el doble del tiempo razonable
  que declarás; intervalo ≥ 2s para no martillar la API.
- Antes de sondear un comando que puede fallar **permanentemente** (PR
  inexistente, arg mal formado), corré el check una vez: un fallo
  permanente no se "espera", se reporta.
- Cada espera larga (>30s) en tareas con trail: una fila en
  `decisions.tsv` (qué se esperó, cuánto tardó).
