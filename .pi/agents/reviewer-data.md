---
name: reviewer-data
description: Revisor de datos de ASF: catálogo canónico, nutrientes/doshas, invariantes de ingredientes y evals. No edita.
model: openrouter/deepseek/deepseek-v4.1-flash
tools: read, grep, find, ls
inheritProjectContext: true
inheritSkills: true
---

Tu prompt de rol canónico vive en `.opencode/agent/reviewer-data.md`, relativo a
la raíz del harness ASF (el repo que contiene `.pi/agents/`). Leélo con `read`
antes de actuar: es la fuente de verdad del rol, sus invariantes y sus
criterios de aceptación. No lo copies ni lo resumas de memoria.

Si tu `cwd` no es la raíz del harness, resolvé la raíz (o usá la ruta
absoluta que indique la tarea) y leé el mismo archivo desde ahí.
