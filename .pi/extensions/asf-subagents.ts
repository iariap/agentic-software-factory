/**
 * Guard de subagents para ASF en pi.dev (pi-subagents 0.66.0).
 *
 * En 0.66.0 el `subagent` público sólo ejecuta de forma confiable vía
 * `workflowScript` con `runs.run(...)`; la referencia del tool documenta
 * `agent` como target de management. Este guard permite EXACTAMENTE una
 * plantilla verificable y rechaza todo lo demás:
 *
 *   subagent({ workflowScript: 'return runs.run("asf", { "agent": "coder", "task": "..." })' })
 *
 * El parseo es prefijo/sufijo + `JSON.parse("[<args>]")`: no hay `eval`, `vm`
 * ni regex que acepte JavaScript arbitrario, así que sólo sobrevive un objeto
 * JSON literal. El child se limita a la allowlist
 * `agent/task/cwd/agentScope/context/model` (model sólo el pin).
 *
 * También cubre los escapes de scope que el executor no valida:
 * - `agentScope:"user"` omite `.pi/settings.json` (y el `modelScope` estricto).
 * - Un `cwd` fuera del harness no encuentra `.pi/`.
 * - `workflowScriptPath`/`workflow` y los `runs.run` anidados no son
 *   inspeccionables, así que se rechazan.
 * - Los runners externos (`claude-code`/`codex`/`cursor`) no pasan por
 *   `modelScope` nativo: se deshabilitan en `.pi/settings.json`.
 *
 * Frontera declarada: el guard cubre el `tool_call` de `subagent`. Si la
 * extensión no está cargada (proyecto sin trust), no hay enforcement.
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export const PINNED_MODEL = "openrouter/deepseek/deepseek-v4.1-flash";
export const PINNED_PACKAGE = "pi-subagents";
export const PINNED_VERSION = "0.66.0";
export const SUBAGENT_TOOL = "subagent";

export const ASF_AGENT_NAMES: ReadonlySet<string> = new Set([
	"coder",
	"deploy-agent",
	"interrogator",
	"perf-factory",
	"reviewer-data",
	"reviewer-frontend",
	"reviewer-infra",
	"reviewer-security",
	"security-guardian",
	"sevbot",
]);

// Mismos límites que el executor de pi-subagents para la key del workflow.
const KEY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const RUN_PREFIX = "return runs.run(";
const CHILD_ALLOWED: ReadonlySet<string> = new Set(["agent", "task", "cwd", "agentScope", "context", "model"]);
// Management read-only/control que no relanza ni cambia contratos de agente.
const MANAGEMENT_ALLOW: ReadonlySet<string> = new Set([
	"status",
	"list",
	"get",
	"models",
	"guide",
	"doctor",
	"children.list",
	"stop",
	"interrupt",
	"steer",
	"validate",
]);
// Campos de ejecución que nunca se aceptan por fuera de la plantilla.
const FORBIDDEN_EXECUTION_KEYS: readonly string[] = [
	"workflow",
	"workflowScriptPath",
	"resume",
	"tools",
	"extensions",
	"subagentOnlyExtensions",
	"mcpDirectTools",
	"skill",
	"skillPath",
	"tasks",
	"chain",
	"parallel",
	"concurrency",
	"step",
	"config",
	"workflowKey",
	"workflowParentRunId",
	"workflowChildAsyncId",
];

export interface BlockDecision {
	block: true;
	reason: string;
}

/** Raíz del harness ASF derivada del propio archivo: `<root>/.pi/extensions/x.ts`. */
export function resolveAsfRoot(importMetaUrl: string): string {
	const extensionDir = path.dirname(fileURLToPath(importMetaUrl));
	return path.dirname(path.dirname(extensionDir));
}

/** Package de pi-subagents: proyecto `.pi/npm` primero, global después. */
export function resolvePackageDir(root: string): string | null {
	const candidates = [
		process.env.ASF_SUBAGENTS_PACKAGE_DIR,
		path.join(root, ".pi", "npm", "node_modules", PINNED_PACKAGE),
		path.join(os.homedir(), ".pi", "agent", "npm", "node_modules", PINNED_PACKAGE),
	];
	for (const candidate of candidates) {
		if (candidate && fs.existsSync(path.join(candidate, "package.json"))) return candidate;
	}
	return null;
}

function readPackageVersion(packageDir: string): string | null {
	try {
		const parsed = JSON.parse(fs.readFileSync(path.join(packageDir, "package.json"), "utf-8")) as { version?: unknown };
		return typeof parsed.version === "string" ? parsed.version : null;
	} catch {
		return null;
	}
}

interface DiscoveryLike {
	modelScope?: { enforce?: unknown; strict?: unknown; allow?: unknown };
	agents?: Array<{ name?: unknown; model?: unknown }>;
}

/**
 * Verifica con el descubrimiento real de pi-subagents que `cwd` + `scope`
 * mantienen el proyecto ASF: `modelScope` estricto con el modelo pinneado y el
 * subagent `coder` presente. `null` si está OK.
 */
export async function verifyAsfScope(
	root: string,
	cwd: string,
	scope: "project" | "both",
): Promise<string | null> {
	const packageDir = resolvePackageDir(root);
	if (!packageDir) return `no encontré el package ${PINNED_PACKAGE}`;
	const version = readPackageVersion(packageDir);
	if (version !== PINNED_VERSION) {
		return `${PINNED_PACKAGE} versión '${version ?? "desconocida"}' != '${PINNED_VERSION}'`;
	}
	let discoverAgents: ((cwd: string, scope: string, preferredProvider?: string) => DiscoveryLike) | undefined;
	try {
		const module = (await import(`${packageDir}/src/agents/agents.ts`)) as {
			discoverAgents?: (cwd: string, scope: string, preferredProvider?: string) => DiscoveryLike;
		};
		discoverAgents = module.discoverAgents;
	} catch {
		return "no pude cargar discoverAgents de pi-subagents";
	}
	if (typeof discoverAgents !== "function") return "discoverAgents no disponible en pi-subagents";

	const discovered = discoverAgents(cwd, scope, undefined);
	const scopeRule = discovered.modelScope;
	const allow = Array.isArray(scopeRule?.allow) ? (scopeRule?.allow as unknown[]) : [];
	if (scopeRule?.enforce !== true || scopeRule?.strict !== true) {
		return `modelScope ASF no activo desde '${cwd}'`;
	}
	if (!allow.includes(PINNED_MODEL) || allow.includes("inherit")) {
		return `allowlist ASF inválida desde '${cwd}'`;
	}
	const coder = (discovered.agents ?? []).find((agent) => agent.name === "coder");
	if (!coder) return `proyecto ASF no resuelto desde '${cwd}' (falta subagent coder)`;
	if (coder.model !== PINNED_MODEL) return `coder resuelve '${String(coder.model)}' en vez de '${PINNED_MODEL}'`;
	return null;
}

export interface ParsedAsfRun {
	key: string;
	child: Record<string, unknown>;
}

/**
 * Parsea EXACTAMENTE `return runs.run("<key>", <objeto JSON literal>)` (con
 * `;` final opcional). Cualquier otro script devuelve error.
 */
export function parseAsfWorkflowScript(script: string): ParsedAsfRun | { error: string } {
	let trimmed = script.trim();
	if (trimmed.endsWith(";")) trimmed = trimmed.slice(0, -1).trimEnd();
	if (!trimmed.startsWith(RUN_PREFIX)) {
		return { error: `workflowScript debe ser exactamente '${RUN_PREFIX}"<key>", <objeto JSON literal>)'` };
	}
	if (!trimmed.endsWith(")")) return { error: "workflowScript debe cerrar con ')'" };
	const args = trimmed.slice(RUN_PREFIX.length, -1);
	let parsed: unknown;
	try {
		parsed = JSON.parse(`[${args}]`);
	} catch {
		return { error: "los argumentos de runs.run deben ser JSON literal (sin JS, sin eval)" };
	}
	if (!Array.isArray(parsed) || parsed.length !== 2) return { error: "runs.run requiere (key, child)" };
	const [key, child] = parsed;
	if (typeof key !== "string" || !KEY_PATTERN.test(key)) return { error: "key de workflow inválida" };
	if (!child || typeof child !== "object" || Array.isArray(child)) {
		return { error: "el child de runs.run debe ser un objeto JSON" };
	}
	return { key, child: child as Record<string, unknown> };
}

/** Valida la allowlist del child. `null` si está OK. */
export function validateChildObject(child: Record<string, unknown>): string | null {
	for (const key of Object.keys(child)) {
		if (!CHILD_ALLOWED.has(key)) {
			return `campo '${key}' no permitido en el child (sólo ${[...CHILD_ALLOWED].join(", ")})`;
		}
	}
	if (typeof child.agent !== "string" || !ASF_AGENT_NAMES.has(child.agent)) {
		return `agent debe ser un rol ASF: ${[...ASF_AGENT_NAMES].join(", ")}`;
	}
	if (typeof child.task !== "string" || !child.task.trim()) return "task debe ser un string no vacío";
	if (child.cwd !== undefined && typeof child.cwd !== "string") return "cwd debe ser string";
	if (child.agentScope !== undefined && child.agentScope !== "both" && child.agentScope !== "project") {
		return "agentScope debe ser 'both' o 'project'";
	}
	if (child.context !== undefined && child.context !== "fresh") return "context debe ser 'fresh'";
	if (child.model !== undefined && child.model !== PINNED_MODEL) {
		return `model sólo puede ser '${PINNED_MODEL}'`;
	}
	return null;
}

function block(reason: string): BlockDecision {
	return { block: true, reason: `ASF: ${reason}` };
}

/**
 * Decide si un `tool_call` de `subagent` debe bloquearse. Exportada para tests
 * con mock; no inicia ninguna llamada de modelo.
 */
export async function evaluateSubagentCall(
	root: string,
	input: Record<string, unknown>,
	ctxCwd: string,
): Promise<BlockDecision | null> {
	const rawAction = input.action;
	if (typeof rawAction === "string" && rawAction.trim() !== "") {
		const action = rawAction.trim();
		if (!MANAGEMENT_ALLOW.has(action)) {
			return block(
				`action '${action}' no permitida; management permitido: ${[...MANAGEMENT_ALLOW].join(", ")}`,
			);
		}
		if (action === "validate") {
			if (input.workflowScriptPath !== undefined || input.workflow !== undefined) {
				return block("validate sólo acepta workflowScript (no path/named resource)");
			}
			if (typeof input.workflowScript !== "string") return block("validate requiere workflowScript");
			const parsed = parseAsfWorkflowScript(input.workflowScript);
			if ("error" in parsed) return block(parsed.error);
			const childError = validateChildObject(parsed.child);
			return childError ? block(childError) : null;
		}
		return null;
	}

	for (const key of FORBIDDEN_EXECUTION_KEYS) {
		if (input[key] !== undefined) return block(`'${key}' no está permitido; usá la plantilla runs.run`);
	}
	if (input.agent !== undefined || input.task !== undefined) {
		return block("top-level agent/task no es la vía de ejecución soportada; usá workflowScript runs.run");
	}
	if (input.model !== undefined && input.model !== PINNED_MODEL) {
		return block(`model de la corrida sólo puede ser '${PINNED_MODEL}'`);
	}
	if (input.agentScope !== undefined && input.agentScope !== "both" && input.agentScope !== "project") {
		return block("agentScope debe ser 'both' o 'project' (no 'user')");
	}
	if (input.context !== undefined && input.context !== "fresh") {
		return block("context debe ser 'fresh'");
	}
	if (typeof input.workflowScript !== "string") {
		return block(`ejecución requiere workflowScript con '${RUN_PREFIX}"<key>", <objeto JSON literal>)'`);
	}
	const parsed = parseAsfWorkflowScript(input.workflowScript);
	if ("error" in parsed) return block(parsed.error);
	const childError = validateChildObject(parsed.child);
	if (childError) return block(childError);

	const outerScope: "project" | "both" = input.agentScope === "project" ? "project" : "both";
	const outerCwdRaw = typeof input.cwd === "string" ? input.cwd : ctxCwd;
	const outerCwd = path.isAbsolute(outerCwdRaw) ? outerCwdRaw : path.resolve(ctxCwd, outerCwdRaw);
	const outerReason = await verifyAsfScope(root, outerCwd, outerScope);
	if (outerReason) return block(`el cwd '${outerCwd}' no mantiene el proyecto ASF (${outerReason})`);

	const childScope: "project" | "both" = parsed.child.agentScope === "project" ? "project" : outerScope;
	const childCwdRaw = typeof parsed.child.cwd === "string" ? parsed.child.cwd : outerCwd;
	const childCwd = path.isAbsolute(childCwdRaw) ? childCwdRaw : path.resolve(outerCwd, childCwdRaw);
	const childReason = await verifyAsfScope(root, childCwd, childScope);
	if (childReason) return block(`el cwd del child '${childCwd}' no mantiene el proyecto ASF (${childReason})`);
	return null;
}

export const DELEGATION_NOTE = [
	"",
	"## ASF en pi.dev",
	'- Delegá con `subagent({ workflowScript: \'return runs.run("asf", { "agent": "coder", "task": "<encargo>", "cwd": "<abs>" })\', cwd: "<abs>", agentScope: "both" })`.',
	"- El `cwd` (outer y del child) debe quedar dentro del harness ASF para cargar `.pi/settings.json` y el `modelScope` estricto.",
	'- Child permitido: `agent` (rol ASF), `task`, `cwd`, `agentScope` ("both"/"project"), `context` ("fresh"), `model` (sólo el pin).',
	"- Prohibido: top-level `agent`/`task`, `workflowScriptPath`, `workflow`, `resume`, `tools`, `extensions`, `model` alternativo y cualquier otro script.",
	"- Management permitido: status, list, get, models, guide, doctor, children.list, stop, interrupt, steer, validate.",
].join("\n");

export default function asfSubagents(pi: ExtensionAPI): void {
	const root = resolveAsfRoot(import.meta.url);

	// Marca de carga observable en RPC/status (prueba de que la extensión montó).
	pi.on("session_start", (_event, ctx) => {
		ctx.ui.setStatus("asf-subagents-guard", "on");
	});

	pi.on("tool_call", async (event, ctx) => {
		if (event.toolName !== SUBAGENT_TOOL) return;
		const input = event.input as Record<string, unknown>;
		const decision = await evaluateSubagentCall(root, input, ctx.cwd);
		if (decision) return decision;
		return;
	});

	pi.on("before_agent_start", (event) => ({
		systemPrompt: `${event.systemPrompt}\n${DELEGATION_NOTE}`,
	}));
}
