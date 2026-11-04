/**
 * Verificación de la config de pi.dev para ASF (sin llamadas a la API).
 *
 * Corre el descubrimiento real de pi-subagents desde su source instalado y
 * falla (exit != 0) si la config de proyecto no garantiza:
 *   - el package pinneado (versión exacta),
 *   - los 10 subagents ASF con tools y modelo correctos,
 *   - `modelScope` estricto con el pin y el fallback del coder (sin inherit),
 *   - los runners externos (claude-code/codex/cursor) deshabilitados,
 *   - el rechazo de modelos caros y locales.
 *
 * Reproducir (desde cualquier cwd):
 *   bun scripts/pi-discover.ts "$(git rev-parse --show-toplevel)"
 *
 * Package: `.pi/npm/node_modules/pi-subagents` (proyecto) primero; global
 * después. Override con PI_SUBAGENTS_DIR. No lee auth ni imprime credenciales.
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const PINNED_MODEL = "openrouter/deepseek/deepseek-v4.1-flash";
// Fallback automático del coder ante rate-limit del pin (non-free): en
// OpenRouter no hay una variante free del 1017.
const CODER_FALLBACK_MODEL = "openrouter/deepseek/deepseek-v4.1-flash-1017";
const PINNED_VERSION = "0.66.0";
const PINNED_PACKAGE = "pi-subagents";

const READONLY_TOOLS = ["read", "grep", "find", "ls"];
const EXPECTED_TOOLS: Record<string, string[]> = {
	coder: ["read", "write", "edit", "bash"],
	"deploy-agent": ["read", "write", "bash"],
	interrogator: READONLY_TOOLS,
	"perf-factory": READONLY_TOOLS,
	"reviewer-data": READONLY_TOOLS,
	"reviewer-frontend": READONLY_TOOLS,
	"reviewer-infra": READONLY_TOOLS,
	"reviewer-security": READONLY_TOOLS,
	"security-guardian": READONLY_TOOLS,
	sevbot: READONLY_TOOLS,
};
const EXTERNAL_CLI_AGENTS = [
	"claude-code",
	"claude-code-writer",
	"codex-exec",
	"codex-exec-writer",
	"cursor-agent",
	"cursor-agent-writer",
];

const projectRoot = process.argv[2];
if (!projectRoot) {
	console.error("usage: bun scripts/pi-discover.ts <projectRoot>");
	process.exit(2);
}

const failures: string[] = [];
const fail = (message: string): void => {
	failures.push(message);
	console.error(`FAIL ${message}`);
};

function resolvePackageDir(root: string): string | null {
	const candidates = [
		process.env.PI_SUBAGENTS_DIR,
		path.join(root, ".pi", "npm", "node_modules", PINNED_PACKAGE),
		path.join(os.homedir(), ".pi", "agent", "npm", "node_modules", PINNED_PACKAGE),
	];
	for (const candidate of candidates) {
		if (candidate && fs.existsSync(path.join(candidate, "package.json"))) return candidate;
	}
	return null;
}

const packageDir = resolvePackageDir(projectRoot);
if (!packageDir) {
	fail(`no encontré ${PINNED_PACKAGE} (proyecto .pi/npm ni global ~/.pi/agent/npm)`);
	process.exit(1);
}
const installedVersion = (JSON.parse(fs.readFileSync(path.join(packageDir, "package.json"), "utf-8")) as { version?: string }).version;
if (installedVersion !== PINNED_VERSION) {
	fail(`${PINNED_PACKAGE} versión '${installedVersion ?? "?"}' != '${PINNED_VERSION}'`);
}

const agentsModule = await import(`${packageDir}/src/agents/agents.ts`);
const scopeModule = await import(`${packageDir}/src/runs/shared/model-scope.ts`);
const discoverAgents = agentsModule.discoverAgents;
const discoverAgentSnapshot = agentsModule.discoverAgentSnapshot;
const { checkModelScope, resolveModelScopesForAgent } = scopeModule;

const effective = discoverAgents(projectRoot, "both", undefined);
const all = discoverAgentSnapshot(projectRoot, "both", undefined).all;

interface AgentSummary {
	name?: unknown;
	source?: unknown;
	model?: unknown;
	tools?: unknown;
	fallbackModels?: unknown;
	thinking?: unknown;
}
const summary = (agent: AgentSummary) => ({
	name: agent.name,
	source: agent.source,
	model: agent.model,
	thinking: agent.thinking,
	tools: agent.tools,
	fallbackModels: agent.fallbackModels,
});

console.log(
	JSON.stringify(
		{
			packageDir,
			packageVersion: installedVersion,
			cwd: effective.cwd,
			projectAgentsDir: effective.projectAgentsDir,
			modelScope: effective.modelScope,
			diagnostics: effective.agentDiagnostics,
			effectiveAgents: (effective.agents as AgentSummary[]).map(summary),
			projectAgents: (all.project as AgentSummary[]).map(summary),
		},
		null,
		2,
	),
);

// 1) Scope de modelo.
const scopeRule = effective.modelScope as { enforce?: unknown; strict?: unknown; allow?: unknown } | undefined;
if (scopeRule?.enforce !== true) fail("modelScope.enforce != true");
if (scopeRule?.strict !== true) fail("modelScope.strict != true");
const allow = Array.isArray(scopeRule?.allow) ? (scopeRule?.allow as unknown[]) : [];
const allowOk =
	allow.length === 2 && allow.includes(PINNED_MODEL) && allow.includes(CODER_FALLBACK_MODEL);
if (!allowOk) {
	fail(
		`modelScope.allow != [${PINNED_MODEL}, ${CODER_FALLBACK_MODEL}] (got ${JSON.stringify(allow)})`,
	);
}
if (allow.includes("inherit")) fail("modelScope.allow incluye 'inherit'");

// 2) Subagents ASF: presencia, tools y modelo.
const byName = new Map<string, AgentSummary>((effective.agents as AgentSummary[]).map((agent) => [String(agent.name), agent]));
for (const [name, expectedTools] of Object.entries(EXPECTED_TOOLS)) {
	const agent = byName.get(name);
	if (!agent) {
		fail(`falta subagent ASF '${name}'`);
		continue;
	}
	if (agent.model !== PINNED_MODEL) fail(`${name}: model '${String(agent.model)}' != '${PINNED_MODEL}'`);
	const expectedFallbacks = name === "coder" ? [CODER_FALLBACK_MODEL] : [];
	const fallbacks = Array.isArray(agent.fallbackModels)
		? (agent.fallbackModels as unknown[]).map(String)
		: [];
	if (JSON.stringify(fallbacks) !== JSON.stringify(expectedFallbacks)) {
		fail(`${name}: fallbackModels ${JSON.stringify(fallbacks)} != ${JSON.stringify(expectedFallbacks)}`);
	}
	const tools = Array.isArray(agent.tools) ? (agent.tools as unknown[]).map(String) : [];
	const sorted = [...tools].sort();
	const expected = [...expectedTools].sort();
	if (JSON.stringify(sorted) !== JSON.stringify(expected)) {
		fail(`${name}: tools ${JSON.stringify(tools)} != ${JSON.stringify(expectedTools)}`);
	}
}
const projectAgentNames = new Set((all.project as AgentSummary[]).map((agent) => String(agent.name)));
if (projectAgentNames.size !== Object.keys(EXPECTED_TOOLS).length) {
	fail(`project agents ${projectAgentNames.size} != ${Object.keys(EXPECTED_TOOLS).length}`);
}

// 3) Ningún agente efectivo puede quedar fuera del scope.
for (const agent of effective.agents as AgentSummary[]) {
	const model = typeof agent.model === "string" ? agent.model : undefined;
	if (!model) {
		fail(`agente '${String(agent.name)}' sin modelo resuelto`);
		continue;
	}
	const scopes = resolveModelScopesForAgent(scopeRule, String(agent.name), undefined);
	for (const scope of scopes) {
		const violation = checkModelScope(model, scope, "inherited");
		if (violation) fail(`agente '${String(agent.name)}' con modelo fuera de scope: ${model}`);
	}
}

// 4) Runners externos deshabilitados.
for (const name of EXTERNAL_CLI_AGENTS) {
	if (byName.has(name)) fail(`runner externo '${name}' sigue habilitado`);
}

// 5) Rechazo de escapes (caros y locales) y aceptación del pinneado.
const probes: Array<{ label: string; model: string; expected: "allowed" | "rejected" }> = [
	{ label: "pinneado", model: PINNED_MODEL, expected: "allowed" },
	{ label: "fallback-coder", model: CODER_FALLBACK_MODEL, expected: "allowed" },
	{ label: "caro", model: "openai-codex/gpt-5.6-sol", expected: "rejected" },
	{ label: "caro-global", model: "openrouter/z-ai/glm-5.3-flash", expected: "rejected" },
	{ label: "local", model: "ollama/qwen36-code", expected: "rejected" },
];
for (const probe of probes) {
	const scopes = resolveModelScopesForAgent(scopeRule, "coder", undefined);
	const rejected = scopes.some((scope: unknown) => checkModelScope(probe.model, scope, "inherited") !== undefined);
	const actual = rejected ? "rejected" : "allowed";
	console.log(`SCOPE ${probe.label} ${probe.model} => ${actual}`);
	if (actual !== probe.expected) fail(`probe ${probe.label} '${probe.model}' => ${actual}, esperado ${probe.expected}`);
}

if (failures.length > 0) {
	console.error(`\n${failures.length} fallo(s) de config Pi.`);
	process.exit(1);
}
console.log("\nOK: config pi.dev de ASF verificada.");
