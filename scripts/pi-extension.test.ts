/**
 * Tests de la extensión guard `.pi/extensions/asf-subagents.ts`.
 *
 * Correr: `bun test scripts/pi-extension.test.ts`
 *
 * Usa un mock de `ExtensionAPI` (import type-only del namespace de Pi) y la
 * validación REAL `validateWorkflowScript` del pi-subagents instalado. No
 * inicia llamadas de modelo ni lee `auth.json`; si falta el package o el
 * binario `pi`, los casos se saltan.
 */

import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import asfSubagents, {
	PINNED_MODEL,
	PINNED_VERSION,
	evaluateSubagentCall,
	parseAsfWorkflowScript,
	resolveAsfRoot,
	resolvePackageDir,
} from "../.pi/extensions/asf-subagents.ts";

const REPO_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const packageDir = resolvePackageDir(REPO_ROOT);
if (packageDir) process.env.ASF_SUBAGENTS_PACKAGE_DIR = packageDir;

type Handler = (event: any, ctx: any) => unknown | Promise<unknown>;

function mockPi(): { pi: ExtensionAPI; handlers: Record<string, Handler[]> } {
	const handlers: Record<string, Handler[]> = {};
	const pi = {
		on: (name: string, handler: Handler) => {
			(handlers[name] ??= []).push(handler);
		},
	} as unknown as ExtensionAPI;
	return { pi, handlers };
}

const ctx = { cwd: REPO_ROOT };
const skipIfNoPackage = test.skipIf(!packageDir);

const coderScript = (cwd: string): string =>
	`return runs.run("asf", { "agent": "coder", "task": "Implement X", "cwd": ${JSON.stringify(cwd)}, "agentScope": "both", "context": "fresh", "model": ${JSON.stringify(PINNED_MODEL)} })`;

describe("asf-subagents guard", () => {
	skipIfNoPackage("the coder template is valid for the REAL validateWorkflowScript", async () => {
		const module = (await import(`${packageDir}/src/workflows/scripted-workflow.ts`)) as {
			validateWorkflowScript: (script: string) => { ok: boolean; errors: unknown[] };
		};
		const result = module.validateWorkflowScript(coderScript(REPO_ROOT));
		expect(result.ok).toBe(true);
		expect(result.errors).toEqual([]);
	});

	skipIfNoPackage("allows the coder template with an in-harness cwd", async () => {
		const { pi, handlers } = mockPi();
		asfSubagents(pi);
		const decision = await handlers.tool_call[0]!(
			{ toolName: "subagent", input: { workflowScript: coderScript(REPO_ROOT), cwd: REPO_ROOT, agentScope: "both" } },
			ctx,
		);
		expect(decision).toBeUndefined();
	});

	skipIfNoPackage("blocks agentScope:'user' (outer and child)", async () => {
		const outer = await evaluateSubagentCall(
			REPO_ROOT,
			{ workflowScript: coderScript(REPO_ROOT), agentScope: "user" },
			REPO_ROOT,
		);
		const child = await evaluateSubagentCall(
			REPO_ROOT,
			{
				workflowScript: `return runs.run("asf", { "agent": "coder", "task": "x", "agentScope": "user" })`,
			},
			REPO_ROOT,
		);
		expect(outer).toMatchObject({ block: true });
		expect(child).toMatchObject({ block: true });
	});

	skipIfNoPackage("blocks cwd outside the harness (outer and child)", async () => {
		const outer = await evaluateSubagentCall(
			REPO_ROOT,
			{ workflowScript: coderScript("/tmp"), cwd: "/tmp" },
			REPO_ROOT,
		);
		const child = await evaluateSubagentCall(
			REPO_ROOT,
			{ workflowScript: coderScript("/tmp") },
			REPO_ROOT,
		);
		expect(outer).toMatchObject({ block: true });
		expect(child).toMatchObject({ block: true });
	});

	test("rejects non-literal scripts (no eval of arbitrary JS)", () => {
		for (const script of [
			`const x = 1; return runs.run("asf", { "agent": "coder", "task": "x" })`,
			`return runs.run("asf", { agent: "coder", task: "x" })`,
			`return runs.run("asf", { "agent": "coder", "task": "x" }); return 1;`,
			`return runs.all([{ "key": "a", "agent": "coder", "task": "x" }])`,
		]) {
			expect("error" in parseAsfWorkflowScript(script)).toBe(true);
		}
	});

	test("rejects child fields outside the allowlist", async () => {
		const cases: Array<Record<string, unknown>> = [
			{ agent: "coder", task: "x", resume: "run-1" },
			{ agent: "coder", task: "x", tools: ["bash"] },
			{ agent: "coder", task: "x", model: "openai-codex/gpt-5.6-sol" },
			{ agent: "unknown-role", task: "x" },
			{ agent: "coder", task: "  " },
			{ agent: "coder", task: "x", context: "fork" },
		];
		for (const child of cases) {
			const decision = await evaluateSubagentCall(
				REPO_ROOT,
				{ workflowScript: `return runs.run("asf", ${JSON.stringify(child)})` },
				REPO_ROOT,
			);
			expect(decision).toMatchObject({ block: true });
		}
	});

	test("rejects top-level execution fields and legacy workflow inputs", async () => {
		for (const input of [
			{ agent: "coder", task: "x" },
			{ workflowScriptPath: "w.js" },
			{ workflow: "review" },
			{ workflowScript: coderScript(REPO_ROOT), model: "openai-codex/gpt-5.6-sol" },
			{ workflowScript: coderScript(REPO_ROOT), resume: "run-1" },
		]) {
			expect(await evaluateSubagentCall(REPO_ROOT, input as Record<string, unknown>, REPO_ROOT)).toMatchObject({
				block: true,
			});
		}
	});

	test("management allowlist blocks initiators and allows read-only metadata", async () => {
		for (const action of ["resume", "update", "create", "eject", "enable", "schedule.create", "project.open"]) {
			expect(await evaluateSubagentCall(REPO_ROOT, { action }, REPO_ROOT)).toMatchObject({ block: true });
		}
		for (const action of ["status", "list", "get", "models", "guide", "doctor", "children.list", "stop", "interrupt", "steer"]) {
			expect(await evaluateSubagentCall(REPO_ROOT, { action }, REPO_ROOT)).toBeNull();
		}
	});

	skipIfNoPackage("allows offline action:'validate' for the template", async () => {
		const decision = await evaluateSubagentCall(
			REPO_ROOT,
			{ action: "validate", workflowScript: coderScript(REPO_ROOT) },
			REPO_ROOT,
		);
		expect(decision).toBeNull();
	});

	test("before_agent_start injects the delegation contract", () => {
		const { pi, handlers } = mockPi();
		asfSubagents(pi);
		const result = handlers.before_agent_start[0]!({ systemPrompt: "BASE" }, ctx) as { systemPrompt: string };
		expect(result.systemPrompt.startsWith("BASE")).toBe(true);
		expect(result.systemPrompt).toContain("ASF en pi.dev");
		expect(result.systemPrompt).toContain('runs.run("asf"');
	});

	// CLI versions the guard suite was verified against. Se acepta un set
	// explícito (no un rango abierto): la suite afirma sobre superficies
	// sensibles a la versión del CLI (envelope RPC, flags), así que un CLI
	// no verificado debe frenar la línea, no pasar en silencio. Sumar una
	// versión es una línea, después de correr la suite contra ese CLI.
	// pi-subagents@0.66.0 declara peer pi-ai>=0.80.0: 1.1.0 y 1.2.0 verificadas.
	const VERIFIED_PI_VERSIONS = ["1.1.0", "1.2.0"];

	test("the installed pi binary matches the verified guard suite version", () => {
		const version = spawnSync("pi", ["--version"], { encoding: "utf-8" });
		if (version.error || version.status !== 0) return;
		expect(VERIFIED_PI_VERSIONS).toContain(version.stdout.trim());
	});

	test("pinned constants are the ASF ones", () => {
		expect(PINNED_MODEL).toBe("openrouter/deepseek/deepseek-v4.1-flash");
		expect(PINNED_VERSION).toBe("0.66.0");
		expect(resolveAsfRoot("file:///repo/.pi/extensions/asf-subagents.ts")).toBe("/repo");
	});
});
