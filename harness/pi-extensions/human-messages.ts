/**
 * human-messages: what the human types in agent-watch reaches the running agent.
 *  - agent-watch writes each message to .agent/inbox/<ms>.md. Once a second this extension delivers new ones as
 *    steering messages: Pi hands them to the model after its current turn (the response being generated and its tool
 *    calls finish first, which can take minutes at the 16k thinking cap), then moves them to .agent/inbox/delivered/.
 *  - <ms>.now.md asks to stop now. A headless Pi cannot take a message after an abort (measured: sendUserMessage
 *    throws once the run is aborted), so the session is aborted and every waiting message is left in the inbox for the
 *    driver, which opens the next session's prompt with them.
 *  - A stop request only stops the session it was sent to: one older than this session (left by an older driver that
 *    does not put messages into the prompt) is delivered as a normal message, or every new session would abort.
 *  - Messages are only delivered while a run is active; anything that arrives between runs waits for the driver.
 *  - A message whose first line starts with "[driver]" comes from the loop's driver, not the human (team mode: another
 *    agent carved part of this task, 2026-10-07): that line is dropped and it is delivered as the driver's.
 * Markers "[human] ..." go to PI_LOOP_MARKERS (or stderr) for the loop ledger.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, statSync } from "node:fs";
import { join } from "node:path";

const mark = (msg: string) => {
	const line = `[human] ${msg}`;
	if (process.env.PI_LOOP_MARKERS) appendFileSync(process.env.PI_LOOP_MARKERS, line + "\n");
	else console.error(line);
};

const projectRoot = (): string => {
	try {
		return execFileSync("git", ["rev-parse", "--show-toplevel"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
	} catch {
		return process.cwd();
	}
};

export default function (pi: ExtensionAPI) {
	const inbox = join(projectRoot(), ".agent", "inbox");
	let ctxRef: any = null;
	let running = false;
	let stopping = false;
	const started = Date.now();
	pi.on("session_start", async (_e: any, ctx: any) => { ctxRef = ctx ?? ctxRef; });
	pi.on("agent_start", async (_e: any, ctx: any) => { ctxRef = ctx ?? ctxRef; running = true; });
	pi.on("turn_start", async (_e: any, ctx: any) => { ctxRef = ctx ?? ctxRef; running = true; });
	pi.on("agent_end", async () => { running = false; });

	const tick = () => {
		if (!running || stopping || !existsSync(inbox)) return;
		let files: string[];
		try {
			files = readdirSync(inbox).filter((f) => /^\d+(\.now)?\.md$/.test(f)).sort();
		} catch {
			return;
		}
		if (!files.length) return;
		const fresh = (f: string) => { try { return statSync(join(inbox, f)).mtimeMs >= started; } catch { return false; } };
		if (files.some((f) => f.endsWith(".now.md") && fresh(f))) {
			stopping = true;
			mark(`interrupt waiting=${files.length}`);
			ctxRef?.abort();
			return;
		}
		mkdirSync(join(inbox, "delivered"), { recursive: true });
		for (const f of files) {
			const text = readFileSync(join(inbox, f), "utf8").trim();
			renameSync(join(inbox, f), join(inbox, "delivered", f));
			if (!text) continue;
			const driver = text.startsWith("[driver]");
			const body = driver ? text.split("\n").slice(1).join("\n").trim() : text;
			pi.sendUserMessage(driver ? `[Message from the driver (the harness), sent while you were working]\n${body}`
				: `[Message from the human, typed while you were working]\n${text}`, { deliverAs: "steer" });
			mark(`delivered ${f} chars=${text.length}`);
		}
	};
	const iv = setInterval(tick, 1000);
	iv.unref();
}
