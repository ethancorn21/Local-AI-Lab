/**
 * ask-human: the agent's way to ask the human for something only a person can do: hardware, accounts or credentials,
 * anything outside this VM, a decision the acceptance criteria do not settle.
 *  - The request is written to .agent/asks/<nnn>.md in the project and never leaves the VM. Terminal control
 *    characters and bidi overrides are stripped: the human reads it in a terminal, where escape sequences could
 *    rewrite the screen or set the clipboard.
 *  - The human gets a content-free Telegram ping through `ring-doorbell` (the AI box sends a fixed text; this VM can
 *    ring the bell but not choose the words) and answers with `agent-talk` on this VM: a live conversation with a
 *    fresh agent in the project, or a short typed reply.
 *  - blocking (default): the driver starts no session until the request is answered, and points the first session
 *    after the answer at it (agent-loop, "Requests to the human").
 * Markers "[ask] ..." go to PI_LOOP_MARKERS (or stderr) for the loop ledger.
 * Env: RING_DOORBELL (default ~/bin/ring-doorbell); PI_LOOP_TASK and PI_LOOP_ITER come from the driver.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFile, execFileSync } from "node:child_process";
import { appendFileSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { Type } from "typebox";

const RING = process.env.RING_DOORBELL ?? join(homedir(), "bin", "ring-doorbell");
const MAX_CHARS = 8000;

const mark = (msg: string) => {
	const line = `[ask] ${msg}`;
	if (process.env.PI_LOOP_MARKERS) appendFileSync(process.env.PI_LOOP_MARKERS, line + "\n");
	else console.error(line);
};
const text = (t: string, details: any = {}) => ({ content: [{ type: "text" as const, text: t }], details });
const clean = (s: string) => s.replace(/[\u0000-\u0008\u000B-\u001F\u007F-\u009F‪-‮⁦-⁩]/g, "");

const projectRoot = (): string => {
	try {
		return execFileSync("git", ["rev-parse", "--show-toplevel"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
	} catch {
		return process.cwd();
	}
};

const ring = (): Promise<{ ok: boolean; out: string }> =>
	new Promise((resolve) => {
		execFile(RING, [], { timeout: 30000 }, (err, stdout, stderr) => {
			const out = String(stdout || stderr || (err ? err.message : "")).trim().split("\n").pop()?.slice(0, 200) ?? "";
			resolve({ ok: !err, out });
		});
	});

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "ask_human",
		label: "ask human",
		description: "Ask the human who runs this lab for something you cannot do yourself: a manual step (hardware, an " +
			"account, a credential or API key, anything outside this VM, a physical check) or a decision the task's " +
			"acceptance criteria do not settle. They get a ping on their phone and answer later, often hours later, " +
			"by talking with a fresh agent in this project. Not for anything you can find out or do yourself (the code, " +
			"installed docs, web_search, sudo apt-get), and not for approval of your own plan. Write the request so it " +
			"stands on its own: the human reads only it and the task file.",
		parameters: Type.Object({
			request: Type.String({
				description: "What you need the human to do or decide, why it blocks you, what you already tried, and how " +
					"you will know it is done. Include exact commands, paths, versions and error messages.",
			}),
			blocking: Type.Optional(Type.Boolean({
				description: "true (default): the loop waits for the answer before any new session starts. false: work " +
					"continues; the answer is passed to a later session.",
			})),
		}),
		async execute(_id, params: any) {
			const request = clean(String(params.request ?? "")).trim();
			if (request.length < 40) {
				throw new Error("request too short: say what you need, why it blocks you, what you tried, and how you will know it is done");
			}
			const blocking = params.blocking !== false;
			const dir = join(projectRoot(), ".agent", "asks");
			mkdirSync(dir, { recursive: true });
			const nums = readdirSync(dir).map((f) => /^(\d+)\.md$/.exec(f)).filter((m): m is RegExpExecArray => !!m).map((m) => Number(m[1]));
			const n = String((nums.length ? Math.max(...nums) : 0) + 1).padStart(3, "0");
			const rel = `.agent/asks/${n}.md`;
			const cut = request.length > MAX_CHARS;
			writeFileSync(join(dir, `${n}.md`),
				`# Request ${n}\nstatus: open\nblocking: ${blocking ? "yes" : "no"}\n` +
				`task: ${process.env.PI_LOOP_TASK || "none (not a loop session)"}\n` +
				`asked: ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC, iteration ${process.env.PI_LOOP_ITER ?? "-"}\n\n` +
				`## Request\n${request.slice(0, MAX_CHARS)}${cut ? "\n[cut at " + MAX_CHARS + " characters]" : ""}\n`,
				{ flag: "wx" });
			const r = await ring();
			mark(`filed n=${n} blocking=${blocking} chars=${request.length} ring=${r.ok ? "ok" : "failed"}`);
			const pinged = r.ok
				? "The human has been pinged."
				: `The ping did not go out (${r.out}); the request is filed, and the loop pings again while it waits.`;
			const note = cut ? ` Your request was cut at ${MAX_CHARS} characters.` : "";
			return text(blocking
				? `Filed as ${rel}. ${pinged}${note} No new session starts until the human answers, which can take hours, so ` +
					`do not wait or poll for it. Keep the task's status as it is. Write in your task's ## Hand-over that you ` +
					`are waiting on request ${n} and what to do with the answer, commit, and end the session.`
				: `Filed as ${rel}. ${pinged}${note} Carry on with work that does not depend on it; a later session is ` +
					`pointed at the answer.`,
				{ n, blocking, rang: r.ok });
		},
	});
}
