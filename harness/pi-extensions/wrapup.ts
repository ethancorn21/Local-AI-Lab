/**
 * wrapup: make "flush memory, commit, stop" a rule the harness enforces when a loop session's context gets long.
 *
 * Why code and not AGENTS.md: agents told to stop after a compaction summary kept working instead (hollowdeep
 * iterations compacted 2-3 times, then hit the 45 min timeout with nothing committed). So:
 *  - soft limit: after a turn that ends in tool calls with context >= SOFT, steer the agent with a hand-over message
 *  - compaction is always cancelled: a summary lets the session run on with a blurred memory of its own work
 *  - hard stop: MAX_TURNS turns after the steer, or context >= HARD, abort and exit; the driver commits leftovers
 *  - driver signal: when the driver writes .agent/wrapup-now in a prep session (the task being prepared can be built
 *    now, or other work is free), or .agent/handover-now in a build session (a faster agent takes the task over, team
 *    mode, 2026-10-06), the same steer, tool limit and hard stop apply, with DRIVER_TURNS turns
 *  - a carve session (WRAPUP_CARVE=1, team mode, 2026-10-07: an idle agent writes new tasks carved out of a task another
 *    agent builds) is a prep session whose output is those task files and WRAPUP_PREP_FILE, the carve list: its own
 *    wording, no notes checkpoint
 *  - a prep session (WRAPUP_PREP_FILE set) hands over into its notes file, not the task's hand-over; and once its
 *    context reaches PREP_CHECKPOINT with no notes file yet, it is told to write what it has now and go on (agent b,
 *    2026-10-05: read ~90k tokens for 231, then tried to write all its notes in one last call at the window's edge;
 *    llama.cpp ignores the hand-over thinking cap, the call was cut off by the output limit, and 27 minutes left
 *    nothing - "write it early" in the prompt did not hold)
 * Markers go to stderr as "[wrapup] ..." so the driver can record them in the ledger.
 * Limits sized for the 150k vLLM window from measured hand-overs (iters 100-199: median 11.4k tokens from steer to
 * end, p90 20.4k, max 27.9k; the notes are committed in the first few of those): 120k soft / 142k hard. Hand-over
 * thinking is capped at HANDOVER_THINKING tokens per response.
 * Window edge: vLLM rejects a request whose prompt + max_tokens exceeds the window (Pi asks for 32,768), so past
 * CLAMP_FROM each request's prompt is counted exactly with the server's /tokenize and max_tokens (and the thinking
 * budget) shrunk to the room left; a prompt with no usable room left ends the session (the driver saves the work).
 * Env overrides are for testing only: WRAPUP_SOFT_TOKENS, WRAPUP_HARD_TOKENS, WRAPUP_MAX_TURNS, WRAPUP_WINDOW_TOKENS,
 * WRAPUP_CLAMP_FROM.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { appendFileSync, existsSync, readFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";

const SOFT = Number(process.env.WRAPUP_SOFT_TOKENS ?? 120000);
const HARD = Number(process.env.WRAPUP_HARD_TOKENS ?? 142000);
const WINDOW = Number(process.env.WRAPUP_WINDOW_TOKENS ?? 150000);
const CLAMP_FROM = Number(process.env.WRAPUP_CLAMP_FROM ?? 85000); // below this, prompt + 32k output always fits
const LLM = process.env.LLM_URL ?? "http://127.0.0.1:8080";
const MIN_ROOM = 1024;
// Where the hand-over notes go (the driver exports its HANDOVER_MODE): PROGRESS.md, or the task file's "## Hand-over".
const TASK_HANDOVER = process.env.WRAPUP_HANDOVER === "task";
const MAX_TURNS = Number(process.env.WRAPUP_MAX_TURNS ?? 10);
const HANDOVER_THINKING = Number(process.env.WRAPUP_HANDOVER_THINKING ?? 2048);
const PREP_FILE = process.env.WRAPUP_PREP_FILE ?? "";   // a prep session: these notes are its only output
const CARVE = process.env.WRAPUP_CARVE === "1";   // a carve session: its output is new task files and PREP_FILE, the carve list
const DRIVER_TURNS = Number(process.env.WRAPUP_DRIVER_TURNS ?? 4);
const PREP_CHECKPOINT = Number(process.env.WRAPUP_PREP_CHECKPOINT_TOKENS ?? Math.round(SOFT * 0.6));

const handoverMessage = (why: string, turns = MAX_TURNS) =>
	`[harness] ${why} ` +
	`Stop the current step now and hand over to a fresh agent. From now on only these tools work: reading or editing ` +
	`PROGRESS.md, DECISIONS.md and task files, and git add/commit/status/diff. Do not run tests or read other files.\n` +
	(TASK_HANDOVER
		? `1. FIRST, in one edit: REPLACE the \`## Hand-over\` section at the end of your task file (add it if it is missing; never a second one) with: `
		: `1. FIRST, in one edit: REPLACE the hand-over section at the top of PROGRESS.md (do not add a new one) with: `) +
	`where you are, state of the tests when you last ran them, current hypothesis, exact next step.\n` +
	`2. Commit it right away: git add ${TASK_HANDOVER ? "" : "PROGRESS.md "}DECISIONS.md tasks && git commit -m "<task id>: hand-over - <state>". ` +
	`Leave code changes uncommitted: the harness saves them for the next agent.\n` +
	`3. Only then, if turns remain: refine the notes (failed attempts go in DECISIONS.md) and commit again. If this step ` +
	`turned out too big for one session, split what remains of the task into subtask files now (AGENTS.md, Tasks).\n` +
	`4. End your turn with no further tool calls.\n` +
	`The harness ends this session after ${turns} more turns regardless: anything not committed by then is saved ` +
	`by the harness, but your notes should be committed by step 2.`;
const message = (tokens: number) =>
	handoverMessage(`CONTEXT LIMIT: this session's context is at ${tokens} tokens, past the ${SOFT}-token hand-over limit.`);

const carveMessage = (why: string) =>
	`[harness] ${why} Stop now. From now on only these tools work: reading or editing task files, and ` +
	`git add/commit/status/diff/log/show.\n` +
	`1. FIRST: finish the part task files you have begun and ${PREP_FILE} (one line per moved box: "<box number> -> ` +
	`<part number>"); with no part ready, write the single line "none: <reason>" into ${PREP_FILE}.\n` +
	`2. Commit right away: git add tasks && git commit -m "carve: parts".\n` +
	`3. End your turn with no further tool calls.\n` +
	`The harness ends this session after a few more turns regardless.`;
const prepMessage = (why: string) => CARVE ? carveMessage(why) :
	`[harness] ${why} Stop researching now. From now on only these tools work: reading or editing task files and ` +
	`tasks/prep/ notes, and git add/commit/status/diff/log/show.\n` +
	`1. FIRST, in one edit or write: put everything you have into ${PREP_FILE}: assumptions, plan, tests, open ` +
	`questions; mark what you did not get to check.\n` +
	`2. Commit it right away: git add ${PREP_FILE} && git commit -m "prep: notes".\n` +
	`3. End your turn with no further tool calls.\n` +
	`The harness ends this session after a few more turns regardless.`;

// After the hand-over message, tools are limited to the hand-over itself (enforced, not requested: iteration 66 kept
// debugging failing tests for all its remaining turns and was cut off without saving its notes).
const MEMORY_FILE = /(^|\/)(PROGRESS|DECISIONS|CODEMAP)\.md$|(^|\/)codemap\/.+\.md$|(^|\/)tasks\/((prep|carve)\/)?[^/]+\.md$/;
const READERS = /^(cat|head|tail|grep|wc|sed)\b(.*)$/;
// Split a shell command on unquoted ; && || | and newlines. Quoted text becomes the placeholder Q, so a ';' or '>'
// inside a commit message is not mistaken for a command separator or a redirect (iteration 81's commits were blocked
// that way).
const splitShell = (cmd: string): string[] => {
	const segs: string[] = []; let cur = ""; let q: string | null = null;
	for (let i = 0; i < cmd.length; i++) {
		const c = cmd[i];
		if (q) { if (c === "\\" && q === '"') i++; else if (c === q) q = null; continue; }
		if (c === "'" || c === '"') { q = c; cur += "Q"; continue; }
		if (c === ";" || c === "\n") { segs.push(cur); cur = ""; continue; }
		if ((c === "&" || c === "|") && cmd[i + 1] === c) { segs.push(cur); cur = ""; i++; continue; }
		if (c === "|") { segs.push(cur); cur = ""; continue; }
		cur += c;
	}
	segs.push(cur);
	return segs;
};
const segmentAllowed = (raw: string): boolean => {
	const seg = raw.replace(/\s*\d?>&\d\s*|\s*\d?>\s*\/dev\/null\s*/g, " ").trim();   // stderr/stdout plumbing is fine
	if (!seg) return true;
	if (seg.includes(">")) return false;                            // writes go through the edit/write tools only
	if (/^cd\s+\S+$/.test(seg) || /^(ls|pwd|echo)\b/.test(seg)) return true;
	if (/^git\s+(-C\s+\S+\s+)?(add|commit|status|diff|log|show|rev-parse)\b/.test(seg)) return true;
	const m = seg.match(READERS);                                     // readers only on memory files (or a pipe)
	if (!m) return false;
	return m[2].split(/\s+/).filter((a) => a && a !== "Q" && !a.startsWith("-") && !/^[0-9,p]+$/.test(a))
		.every((f) => MEMORY_FILE.test(f));
};
const handoverAllowed = (name: string, input: any): boolean => {
	if (name === "read" || name === "edit" || name === "write") return MEMORY_FILE.test(String(input?.path ?? ""));
	if (name !== "bash") return false;
	return splitShell(String(input?.command ?? "")).every(segmentAllowed);
};

export default function (pi: ExtensionAPI) {
	let steered = false;
	let turnsSinceSteer = 0;
	let stopping = false;
	let turnLimit = MAX_TURNS;
	let checkpointed = false;
	// In the TUI (loop viewer) stderr would draw over the screen: the driver passes a marker file instead.
	const mark = (msg: string) => {
		const line = `[wrapup] ${msg}`;
		if (process.env.PI_LOOP_MARKERS) appendFileSync(process.env.PI_LOOP_MARKERS, line + "\n");
		else console.error(line);
	};

	const steer = (tokens: number) => {
		steered = true;
		turnsSinceSteer = 0;
		mark(`steer tokens=${tokens}`);
		pi.sendUserMessage(PREP_FILE
			? prepMessage(`CONTEXT LIMIT: this session's context is at ${tokens} tokens, past the ${SOFT}-token limit.`)
			: message(tokens), { deliverAs: "steer" });
	};
	// The driver's signal: a steer from outside the session (a prep session whose task can be built now, or a build
	// session whose task a faster agent takes over). Each kind has its own file, so a prep signal left behind never ends
	// a build session. Delivered once a second at most, like the human's messages, and only while the agent runs (a
	// steer needs a running agent).
	const signal = join(process.cwd(), ".agent", PREP_FILE ? "wrapup-now" : "handover-now");
	let running = false;
	pi.on("agent_start", async () => { running = true; });
	pi.on("agent_end", async () => { running = false; });
	const iv = setInterval(() => {
		if (!running || steered || stopping || !existsSync(signal)) return;
		let why = "";
		try { why = readFileSync(signal, "utf8").trim(); unlinkSync(signal); } catch { return; }
		steered = true;
		turnsSinceSteer = 0;
		turnLimit = DRIVER_TURNS;
		mark(`driver-stop ${why.slice(0, 160)}`);
		pi.sendUserMessage(PREP_FILE ? prepMessage(`${CARVE ? "CARVE" : "PREP"} ENDS NOW: ${why}.`) : handoverMessage(`HAND-OVER NOW: ${why}.`, DRIVER_TURNS),
			{ deliverAs: "steer" });
	}, 1000);
	iv.unref?.();
	const stop = (ctx: any, why: string) => {
		if (stopping) return;
		stopping = true;
		mark(`abort ${why}`);
		ctx.abort();
		ctx.shutdown();
	};
	const tokensNow = (ctx: any, event?: any) =>
		ctx.getContextUsage()?.tokens ?? event?.message?.usage?.totalTokens ?? 0;

	// Exact prompt size from the model server (same chat template, tools and kwargs as the real request).
	const promptTokens = async (p: any): Promise<number | undefined> => {
		try {
			const r = await fetch(`${LLM}/tokenize`, {
				method: "POST", headers: { "Content-Type": "application/json" }, signal: AbortSignal.timeout(10000),
				body: JSON.stringify({ model: p.model, messages: p.messages, tools: p.tools, add_generation_prompt: true,
					chat_template_kwargs: p.chat_template_kwargs }),
			});
			return r.ok ? (await r.json()).count : undefined;
		} catch { return undefined; }
	};

	pi.on("before_provider_request", async (event: any, ctx: any) => {
		const p = event.payload;
		if (!p || typeof p !== "object" || !Array.isArray(p.messages)) return;
		// Hand-over turns need little deliberation: cap their thinking (overrides thinking-budget.ts, which loads first).
		let out = steered ? { ...p, thinking_token_budget: HANDOVER_THINKING } : p;
		if (tokensNow(ctx) >= CLAMP_FROM) {
			const key = "max_completion_tokens" in out ? "max_completion_tokens" : "max_tokens";
			const want = out[key] ?? 32768;
			const n = (await promptTokens(out)) ?? tokensNow(ctx) + 20000; // no count: assume a large tool result
			const room = WINDOW - n - 64;
			if (room < MIN_ROOM) { stop(ctx, `window full: prompt=${n}`); return; }
			if (room < want) {
				out = { ...out, [key]: room };
				if ((out.thinking_token_budget ?? 0) > room - 512) out.thinking_token_budget = Math.max(256, room - 512);
				mark(`clamp ${key}=${room} prompt=${n}`);
			}
		}
		return out === p ? undefined : out;
	});

	pi.on("tool_call", async (event: any) => {
		if (!steered || handoverAllowed(event.toolName, event.input)) return;
		mark(`blocked ${event.toolName} during hand-over`);
		return { block: true, reason: "[harness] Hand-over in progress: only reads/edits of PROGRESS.md, DECISIONS.md, " +
			"task files and git add/commit/status/diff are allowed now. Record where you are in PROGRESS.md, then commit the memory files." };
	});

	pi.on("turn_end", async (event: any, ctx: any) => {
		try {
			const tokens = tokensNow(ctx, event);
			if (steered) {
				turnsSinceSteer++;
				if (turnsSinceSteer >= turnLimit || tokens >= HARD) stop(ctx, `turns=${turnsSinceSteer} tokens=${tokens}`);
				return;
			}
			if (tokens >= HARD) return stop(ctx, `tokens=${tokens} before any hand-over`);
			const continuing = (event.message?.content ?? []).some((c: any) => c?.type === "toolCall");
			if (PREP_FILE && !CARVE && !checkpointed && continuing && tokens >= PREP_CHECKPOINT && tokens < SOFT &&
				!existsSync(join(process.cwd(), PREP_FILE))) {
				checkpointed = true;
				mark(`prep-checkpoint tokens=${tokens}`);
				pi.sendUserMessage(`[harness] PREP CHECKPOINT: this session's context is at ${tokens} tokens and ${PREP_FILE} ` +
					`does not exist yet. Write it now with what you have (assumptions, plan, tests, open questions; mark what ` +
					`you have not checked yet) and commit it: git add ${PREP_FILE} && git commit -m "prep: first notes". Then ` +
					`go on and improve it with edits. Keep it short enough to read in one go (about 15 KB at most): whoever ` +
					`builds the task reads all of it.`, { deliverAs: "steer" });
				return;
			}
			if (tokens >= SOFT && continuing) steer(tokens); // a turn without tool calls is the agent finishing anyway
		} catch (err) {
			mark(`error ${err}`);
		}
	});

	pi.on("session_before_compact", async (event: any, ctx: any) => {
		const tokens = event.preparation?.tokensBefore ?? tokensNow(ctx);
		mark(`compaction cancelled reason=${event.reason} tokens=${tokens}`);
		if (event.reason === "overflow") stop(ctx, "context overflow");
		else if (!steered) steer(tokens); // context jumped past Pi's threshold in one turn, before the soft check saw it
		return { cancel: true };
	});
}
