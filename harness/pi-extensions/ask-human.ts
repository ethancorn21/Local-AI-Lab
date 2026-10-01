/**
 * ask-human: the agent's way to ask the human for something only a person can do: hardware, accounts or credentials,
 * anything outside this VM, a decision the acceptance criteria do not settle.
 *  - The request is written to .agent/asks/<nnn>.md in the project. Terminal control characters and bidi overrides
 *    are stripped: the human reads it in a terminal, where escape sequences could rewrite the screen or set the
 *    clipboard.
 *  - Its text goes to the human through `ring-doorbell --ask <nnn>` (stdin): the AI box encrypts it (telecloak) and
 *    sends it over Telegram; the agent never handles keys or tokens. The human answers from the telecloak app, or with
 *    `agent-talk` on this VM (a live conversation with a fresh agent in the project, or a short typed reply).
 *  - blocking (default): this task waits for the answer; the loop works on other tasks meanwhile and comes back to this
 *    one first once the human has answered (agent-loop, "Requests to the human").
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

const ring = (n: string, request: string, cwd: string): Promise<{ ok: boolean; out: string }> =>
	new Promise((resolve) => {
		const child = execFile(RING, ["--ask", n], { timeout: 120000, cwd }, (err, stdout, stderr) => {
			const out = String(stdout || stderr || (err ? err.message : "")).trim().split("\n").pop()?.slice(0, 200) ?? "";
			resolve({ ok: !err, out });
		});
		child.stdin?.on("error", () => {});
		child.stdin?.end(request);
	});

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "ask_human",
		label: "ask human",
		description: "Ask the human who runs this lab for something you cannot do yourself: a manual step (hardware, an " +
			"account, a credential or API key, anything outside this VM, a physical check) or a decision the task's " +
			"acceptance criteria do not settle. Your request reaches them as a message on their phone; they answer " +
			"later, often hours later, with a written reply or by talking with a fresh agent in this project. Not for " +
			"anything you can find out or do yourself (the code, " +
			"installed docs, web_search, sudo apt-get), and not for approval of your own plan. Write the request so it " +
			"stands on its own: the human reads only it and the task file.",
		parameters: Type.Object({
			request: Type.String({
				description: "What you need the human to do or decide, why it blocks you, what you already tried, and how " +
					"you will know it is done. Include exact commands, paths, versions and error messages.",
			}),
			blocking: Type.Optional(Type.Boolean({
				description: "true (default): this task waits for the answer; the loop works on other tasks meanwhile and " +
					"comes back to this one when the human answers. false: this task can continue; the answer is passed on later.",
			})),
		}),
		async execute(_id, params: any) {
			const request = clean(String(params.request ?? "")).trim();
			if (request.length < 40) {
				throw new Error("request too short: say what you need, why it blocks you, what you tried, and how you will know it is done");
			}
			const blocking = params.blocking !== false;
			const root = projectRoot();
			const dir = join(root, ".agent", "asks");
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
			const r = await ring(n, request.slice(0, MAX_CHARS), root);
			mark(`filed n=${n} blocking=${blocking} chars=${request.length} ring=${r.ok ? "ok" : "failed"}`);
			const pinged = r.ok
				? "The human has been pinged."
				: `The ping did not go out (${r.out}); the request is filed, and the loop pings again while it waits.`;
			const note = cut ? ` Your request was cut at ${MAX_CHARS} characters.` : "";
			return text(blocking
				? `Filed as ${rel}. ${pinged}${note} This task now waits for the answer, which can take hours: do not wait or ` +
					`poll for it. The loop moves on to other tasks meanwhile and brings you back to this one once the human has ` +
					`answered. Keep this task's status as it is. Write in its ## Hand-over that it is waiting on request ${n} and ` +
					`what to do with the answer, commit, and end the session. If another task also cannot go on without this ` +
					`answer, set that task to Status: blocked; it resumes when the answer arrives.`
				: `Filed as ${rel}. ${pinged}${note} Carry on with work that does not depend on it; a later session is ` +
					`pointed at the answer.`,
				{ n, blocking, rang: r.ok });
		},
	});
}
