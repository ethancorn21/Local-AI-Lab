// node --experimental-strip-types test_wrapup_signal.mjs [EXT_DIR] : the wrapup extension's driver signal, no model.
// The real wrapup.ts is loaded against a mock Pi API. Checked: in a prep session (WRAPUP_PREP_FILE) the driver's
// .agent/wrapup-now steers the running agent once, into its notes file (not the task's hand-over); afterwards only
// notes/task files and git are allowed; the session is ended DRIVER_TURNS turns later; without WRAPUP_PREP_FILE the
// signal is ignored. In a build session .agent/handover-now (a faster agent takes the task over) steers the agent into
// the task's hand-over the same way; a prep session ignores it. Each case runs in its own process (the extension reads
// its env at load time).
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ext = process.env.EXT ?? resolve(process.argv[2] ?? join(here, "../../harness/pi-extensions"), "wrapup.ts");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

if (process.env.CASE) {   // child: one case
	const dir = mkdtempSync(join(tmpdir(), "wrapup-")); mkdirSync(join(dir, ".agent")); process.chdir(dir);
	const on = {}, sent = [];
	const pi = { on: (e, f) => { on[e] = f; }, sendUserMessage: (m) => sent.push(m) };
	(await import(pathToFileURL(ext).href)).default(pi);
	let aborted = 0;
	if (process.env.CASE === "checkpoint") {   // context past the checkpoint, no notes file yet; then with one
		const ctxc = { getContextUsage: () => ({ tokens: 1000 }), abort: () => { aborted++; }, shutdown: () => {} };
		const turn = { message: { content: [{ type: "toolCall" }] } };
		const out = {};
		if (process.env.NOTES_EXIST) { mkdirSync("tasks/prep", { recursive: true }); writeFileSync("tasks/prep/231.md", "x"); }
		await on.turn_end(turn, ctxc); out.sent1 = [...sent];
		await on.turn_end(turn, ctxc); out.sent2 = [...sent];
		out.codeRead = await on.tool_call({ toolName: "read", input: { path: "src/app.py" } });
		console.log(JSON.stringify(out)); process.exit(0);
	}
	const ctx = { getContextUsage: () => ({ tokens: 1000 }), abort: () => { aborted++; }, shutdown: () => {} };
	const out = {};
	await on.agent_start?.({}, ctx);
	const sig = ".agent/" + (process.env.SIGNAL ?? "wrapup-now");
	writeFileSync(sig, process.env.SIGNAL === "handover-now" ? "agent a, on a faster card, has nothing to build and takes this task over\n"
		: "task 231 can be built now (its dependencies are in main)\n");
	await sleep(1600);
	out.sent = sent; out.signalLeft = existsSync(sig);
	out.notesEdit = await on.tool_call({ toolName: "edit", input: { path: "tasks/prep/231.md" } });
	out.codeRead = await on.tool_call({ toolName: "read", input: { path: "src/app.py" } });
	out.gitShow = await on.tool_call({ toolName: "bash", input: { command: "git show agent/a:src/app.py" } });
	const turn = { message: { content: [{ type: "toolCall" }] } };
	await on.turn_end(turn, ctx); out.abortAfter1 = aborted;
	await on.turn_end(turn, ctx); out.abortAfter2 = aborted;
	console.log(JSON.stringify(out));
	process.exit(0);
}

let fail = 0;
const check = (c, msg) => { console.log((c ? "ok   " : "FAIL ") + msg); if (!c) fail = 1; };
const run = (env) => JSON.parse(execFileSync(process.execPath, ["--experimental-strip-types", "--no-warnings", fileURLToPath(import.meta.url)],
	{ env: { ...process.env, CASE: "1", EXT: ext, ...env }, encoding: "utf8" }).trim().split("\n").pop());

const p = run({ WRAPUP_PREP_FILE: "tasks/prep/231.md", WRAPUP_DRIVER_TURNS: "2", WRAPUP_HANDOVER: "task" });
check(p.sent.length === 1 && p.sent[0].includes("PREP ENDS NOW: task 231 can be built now"), "prep: the driver signal steers the agent once");
check(p.sent[0]?.includes("tasks/prep/231.md") && !p.sent[0]?.includes("## Hand-over"), "prep: the steer points at the notes file, not the task's hand-over");
check(!p.signalLeft, "prep: the signal file is consumed");
check(p.notesEdit === undefined && p.codeRead?.block === true && p.gitShow === undefined, "after the steer: notes editable, other files blocked, git show allowed");
check(p.abortAfter1 === 0 && p.abortAfter2 === 1, "the session ends after WRAPUP_DRIVER_TURNS (2) turns");
const n = run({ WRAPUP_HANDOVER: "task" });
check(n.sent.length === 0 && n.signalLeft && n.codeRead === undefined && n.abortAfter2 === 0, "not a prep session: the signal is ignored");
const h = run({ SIGNAL: "handover-now", WRAPUP_DRIVER_TURNS: "2", WRAPUP_HANDOVER: "task" });
check(h.sent.length === 1 && h.sent[0].includes("HAND-OVER NOW: agent a, on a faster card") && h.sent[0].includes("## Hand-over") && h.sent[0].includes("after 2 more turns"), "build session: handover-now steers the agent once into the task's hand-over");
check(!h.signalLeft && h.codeRead?.block === true && h.abortAfter2 === 1, "build session: the signal is consumed, code reads are blocked, the session ends after 2 turns");
const hp = run({ SIGNAL: "handover-now", WRAPUP_PREP_FILE: "tasks/prep/231.md", WRAPUP_HANDOVER: "task" });
check(hp.sent.length === 0 && hp.signalLeft, "prep session: handover-now is ignored");
const c = run({ CASE: "checkpoint", WRAPUP_PREP_FILE: "tasks/prep/231.md", WRAPUP_PREP_CHECKPOINT_TOKENS: "500", WRAPUP_HANDOVER: "task" });
check(c.sent1.length === 1 && c.sent1[0].includes("PREP CHECKPOINT") && c.sent1[0].includes("tasks/prep/231.md"), "prep checkpoint: past it with no notes file, the agent is told to write what it has now");
check(c.sent2.length === 1 && c.codeRead === undefined, "the checkpoint is said once and limits no tools (the session goes on)");
const e = run({ CASE: "checkpoint", NOTES_EXIST: "1", WRAPUP_PREP_FILE: "tasks/prep/231.md", WRAPUP_PREP_CHECKPOINT_TOKENS: "500", WRAPUP_HANDOVER: "task" });
check(e.sent1.length === 0, "notes already written: no checkpoint");
const t = run({ CASE: "checkpoint", WRAPUP_PREP_CHECKPOINT_TOKENS: "500", WRAPUP_HANDOVER: "task" });
check(t.sent1.length === 0, "not a prep session: no checkpoint");
process.exit(fail);
