# Fase 2 — Caza por olas

Una **ola** = recorrer el ledger por una clase de vulnerabilidad, con las
evidencias exigidas de cada dominio. Cada candidato entra a `findings.json`
con: evidencia literal (archivo:línea o comando+output), principal/recurso
afectado propuesto, y estado `needs_validation` (aún sin validar).

## Olas (clases)

1. **Inyección**: SQL/command/template/XSS — puntos donde input de usuario
   alcanza intérpretes sin parametrizar/escapar.
2. **AuthN/AuthZ**: rutas sin guard que deban tenerlo, IDOR (acceso por id
   sin chequeo de dueño), sesiones/tokens, permisos por rol.
3. **Exposición de datos/secretos**: secretos commiteados, datos sensibles
   en logs/telemetría/errores (echo del input), PII en URLs.
4. **SSRF / deserialización / recursos**: fetch de URLs controladas por
   usuario, deserialización insegura (pickle/yaml.load), límites de
   tamaño/tiempo ausentes.
5. **Config/IaC/CI**: permisos excesivos, servicios públicos sin
   justificación, actions sin pin por SHA, secretos en texto plano,
   imágenes/base sin pin.
6. **Supply chain**: dependencias con CVE (scanner contra lockfile real),
   typosquatting (hipótesis con comparación), lockfiles divergentes del
   manifiesto, código de terceros ejecutado sin pin.
7. **AI/LLM** (si la app usa modelos): inyección de prompt (input→modelo→
   herramienta/acción), exfiltración por el modelo, guardrails ausentes,
   telemetría de contenido, fallback de proveedor ante errores de
   credenciales.

Dominios con checklist (adaptados de `Masriyan/Claude-Code-CyberSecurity-Skill`,
sólo defensivos: 02 vuln-scan, 09 web, 10 cloud, 16 AI/LLM, 20 supply chain):

## Web/API (09)
- [ ] Cada ruta: ¿qué auth exige? Citar el guard o su ausencia.
- [ ] Validación de entrada en el borde (schemas estrictos, `extra="forbid"`).
- [ ] Errores que reflejan input o stack traces al cliente.
- [ ] CORS, security headers, cookies (Secure/HttpOnly/SameSite).
- [ ] JWT/session: algoritmo, expiración, revocación, almacenamiento.

## Cloud/IaC (10)
- [ ] Dockerfile: base pinneada, usuario no-root, secretos en capas.
- [ ] Cloud Run/IAM: cuentas de servicio con menos privilegio posible;
      servicios públicos justificados; env con secretos resueltos.
- [ ] Workflows: pin por SHA, token de GITHUB_SCOPE mínimo, artefactos.
- [ ] Migrations: idempotentes, sin DDL destructivo implícito.

## AI/LLM (16)
- [ ] Prompt de sistema: ¿contamina con datos de otros usuarios?
- [ ] Tools del modelo: ¿validan entrada y alcance antes de ejecutar?
- [ ] Output del modelo: ¿se confía sin validar (HTML/SQL/código)?
- [ ] Datos sensibles hacia el proveedor: ¿qué sale y qué se loguea?

## Dependencias / supply chain (02 + 20)
- [ ] Scanner de CVEs contra el **lockfile real** (no de memoria).
- [ ] Deps directas: pin de versión, proveniencia razonable.
- [ ] CI: qué scripts corren código de terceros sin pin.

## Reglas de la caza

- Presupuesto por ola: cortar cuando la ola agotó su clase en el ledger
  (las unidades `planned` restantes → siguiente ola o `blocked`).
- Duplicados: un mismo root-cause en varias superficies = **un** finding
  con `surfaces: [...]`.
- Severidad preliminar (se ajusta en validación): Critical = acceso no
  autenticado a datos sensibles o RCE; High = bypass de auth/inyección
  confirmable; Medium = exposición limitada/misconfig; Low = endurecimiento.
- **Modo táctil con la realidad**: sin ejecutar nada contra servicios
  vivos — la confirmación es por lectura de código y scanners pasivos.
