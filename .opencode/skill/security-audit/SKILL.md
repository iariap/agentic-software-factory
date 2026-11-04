---
name: security-audit
description: Metodología completa de auditoría defensiva de seguridad sobre un checkout de app (6 fases, perfiles quick/standard/deep, cobertura determinista, hallazgos validados adversarialmente). La ejecuta el subagent security-guardian vía /factory-sec; para preguntas puntuales de seguridad usar sólo la parte relevante, no el workflow completo.
---

# Security Audit (workflow del harness)

Encontrar vulnerabilidades que violan una **trust boundary real**, y a los
owners darles: evidencia del código fuente, reproducción segura, prioridad y
**fix mínimo efectivo**. Un candidato sin principal/recurso/outcome de
seguridad afectado concreto **no es un hallazgo confirmado**.

Adaptado de `cloudflare/security-audit-skill` (fases, cobertura, validación
adversarial) y de los skills defensivos de
`Masriyan/Claude-Code-CyberSecurity-Skill` (checklists por dominio). Fuente de
verdad del agente que lo ejecuta: `.opencode/agent/security-guardian.md`.

## Modos

- **Focal**: preguntas o revisión puntual de seguridad → usar sólo la parte
  relevante de este skill. NO crear run dir ni correr las 6 fases.
- **Auditoría completa**: sólo cuando se invoca por `/factory-sec <app>`
  (o pedido explícito de auditoría completa). Correr todas las fases y
  escribir los archivos del run dir.
- **Automática (scoped)**: disparada por el orquestador cuando vale la pena
  (ver Disparadores). Correr las 6 fases con `scope_paths` = los paths del
  cambio relevante y perfil `quick`; el reporte se declara como cobertura
  parcial del target completo.

Si el pedido puede ser cualquiera de los dos, preguntar una sola cosa antes
de empezar.

## Disparadores (los determina el orquestador, no un script)

El agente orquestador evalúa estos señales tras clasificar un PR/diff
(`/factory-review`) o al mergear/deployar, y lanza una auditoría scoped
(perfil `quick`) cuando la relación señal/costo da:

**Vale la pena (cualquiera de éstas):**
1. El diff toca **auth/sesiones/permisos** (guards, middleware, tokens,
   OAuth, session storage).
2. El diff toca **datos sensibles o su borde**: modelos con PII/PHI,
   validación de entrada, logs/telemetría, export/import.
3. El diff toca **infra del despliegue**: `deploy/**`, Dockerfile,
   `.github/workflows/**`, service accounts/IAM, servicios públicos.
4. El diff toca **el pipeline AI** (prompts, tools del modelo, RAG,
   fallback de proveedor) en apps que usan modelos.
5. El diff toca **manifiestos/lockfiles de dependencias** (nueva dep o
   versión mayor) o introduce ejecución de código de terceros.
6. El diff toca **migraciones** o el esquema de datos en producción.
7. Riesgo clasificado **high** por la policy (auditoría scoped sí o sí,
   además de los reviewers).
8. `sevbot`/incidente con señales de seguridad, o hallazgo
   `needs_validation` viejo cuya validación ahora es posible.

**No vale la pena (skip explícito, una línea de por qué):**
- Diff sólo de docs/copy/comentarios o tests sin datos reales.
- Cambios de estilo/format/rename sin cambio de flujo.
- Superficie ajena a seguridad y riesgo `low`/`medium`.
- Auditoría scoped reciente (misma superficie, sin cambios de código
  desde el SHA auditable) — re-reportar duplicado no aporta.

La decisión del orquestador queda registrada en el reporte de review:
`security-audit: disparada (razón) | skip (razón)`. En duda, dispara
`quick`: es barato y fail-open a auditoría, no a silencio.

## Perfiles (elegir uno, declararlo en el run)

- `quick` — pasada acotada: unidades de cobertura gruesas
  (superficie × boundary × clase, subsistema = `all`), **una** ola de caza
  + una pasada final de cobertura. Lo que no entre: `deferred` explícito.
- `standard` (default) — el workflow completo como está escrito.
- `deep` — targets grandes o de alto riesgo: unidades por subsistema, olas
  de caza hasta pasada limpia del critic, verificación final independiente
  de cada hallazgo confirmado.

Los perfiles cambian amplitud y redundancia, **nunca el estándar de
evidencia**: la puerta de confirmación y las reglas de grounding valen igual
en `quick`.

## Archivos del run (en `state/reports/<app>/<run>/`, 4 archivos, nada más)

| Archivo            | Contenido | Dueño |
|--------------------|-----------|-------|
| `run-metadata.json` | `run_id`, `app`, `target` (ruta del checkout), `source_ref` (SHA + dirty), `profile`, `scope_paths`, `started_at`, `run_status` (`in_progress`/`completed`) | guardian |
| `coverage-ledger.json` | unidades de cobertura y su estado | guardian |
| `findings.json` | candidatos y hallazgos con estados | guardian |
| `REPORT.md` | el reporte final human-readable | guardian |

Estados del ledger: `planned` → `done` / `blocked` (con por qué).
Estados del finding: `confirmed` / `needs_validation` / `rejected` /
`out_of_scope` / `deferred`. `needs_validation` **no es hallazgo**: es
trabajo con comando exacto pendiente.

## Fases

1. **Preparación + reconocimiento** (`RECONNAISSANCE.md`): resolver
   app/checkout, leer `AGENTS.md` + `.factory/app.json` + docs de seguridad
   de la app, leer corridas previas (carry-over), mapear superficies y
   trust boundaries, escribir `run-metadata.json` y el ledger inicial.
2. **Caza por olas** (`HUNTING.md`): una ola por clase de vulnerabilidad,
   con los checklists por dominio. Cada candidato entra a `findings.json`
   como mínimo con evidencia literal y candidato de trust boundary.
3. **Validación adversarial** (`VALIDATION-AND-REPORTING.md`): intentar
   **desconfirmar** cada candidato leyendo el código que podría invalidarlo
   (middleware aguas arriba, checks previos, config). Sólo lo que sobrevive
   con evidencia pasa a `confirmed`.
4. **Critic de cobertura**: releer el ledger; unidades sin ejecutar →
   `blocked` con razón; gaps hallados en la validación → re-caza puntual
   (en `quick` se registran como `deferred`, no re-caza).
5. **Reporte**: escribir `REPORT.md` (formato en
   `VALIDATION-AND-REPORTING.md`) y actualizar `findings.json`/ledger.
6. **Cierre**: veredicto (`LIMPIO` / `RIESGOS` / `SIN_DATOS`), statement de
   cobertura (qué NO se cubrió: explícito, nunca "el resto está bien"
   implícito), `run_status: completed`.

## Límites duros (en todas las fases)

- Sólo lectura del checkout y scanners pasivos. **Nunca** sondear endpoints
  desplegados (staging/producción), servicios compartidos ni cuota pagada.
- Sin scanner/acceso → `blocked`/`needs_validation` con el comando exacto.
- Sin datos inventados: CVEs, hashes y outputs de herramientas se
  corroboran contra los manifiestos/lockfiles reales o no se reportan.
- Diagnóstica y propone; nunca edita código ni aplica fixes.
