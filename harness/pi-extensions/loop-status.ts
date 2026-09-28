/**
 * loop-status: makes Pi's own TUI the agent-loop viewer. Active only when the driver sets PI_LOOP=1.
 *  - footer status next to Pi's context usage: iteration, task, live generation speed, elapsed time
 *    (speed = growth of vLLM's generation-token counter, read from the model server's /metrics every 2 s)
 *  - when the agent has finished and Pi would wait for input, exit, so the driver can start the next iteration
 *  - a turn that ends with tool-call markup as plain text (a malformed call: nothing ran) gets a follow-up asking the
 *    agent to re-issue it, at most MAX_RETRIES times per session (iterations 100 and 144 ended on such a message)
 * Env from the driver: PI_LOOP_ITER, PI_LOOP_TASK. LLM_URL overrides the metrics host (default 127.0.0.1:8080).
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { appendFileSync } from "node:fs";

const LOOP = process.env.PI_LOOP === "1";
const ITER = process.env.PI_LOOP_ITER ?? "?";
const TASK = (process.env.PI_LOOP_TASK ?? "").replace(/^tasks\//, "").replace(/\.md$/, "");
const METRICS = (process.env.LLM_URL ?? "http://127.0.0.1:8080") + "/metrics";
const MAX_RETRIES = 2;
export const MALFORMED = /<\/?(tool_call|function[=>]|parameter[=>])/;

export default function (pi: ExtensionAPI) {
	if (!LOOP) return;
	const started = Date.now();
	let timer: ReturnType<typeof setInterval> | undefined;
	let last: { t: number; gen: number } | undefined;
	let rate = 0;

	const poll = async (ctx: any) => {
		try {
			const text = await (await fetch(METRICS, { signal: AbortSignal.timeout(1500) })).text();
			let gen = 0, running = 0;
			for (const line of text.split("\n")) {
				if (line.startsWith("vllm:generation_tokens_total")) gen += Number(line.split(" ").pop());
				if (line.startsWith("vllm:num_requests_running")) running += Number(line.split(" ").pop());
			}
			const now = Date.now();
			if (last && now > last.t) rate = running > 0 ? ((gen - last.gen) * 1000) / (now - last.t) : 0;
			last = { t: now, gen };
		} catch {
			rate = -1;
		}
		const mins = Math.floor((Date.now() - started) / 60000);
		const secs = Math.floor(((Date.now() - started) % 60000) / 1000);
		const speed = rate < 0 ? "server ?" : rate > 0 ? `${rate.toFixed(0)} tok/s` : "idle";
		ctx.ui.setStatus("loop", `iter ${ITER} | ${TASK} | ${speed} | ${mins}m${String(secs).padStart(2, "0")}s`);
	};

	pi.on("session_start", async (_event: any, ctx: any) => {
		timer = setInterval(() => void poll(ctx), 2000);
		void poll(ctx);
	});
	pi.on("session_shutdown", async () => {
		if (timer) clearInterval(timer);
	});
	let retries = 0;
	pi.on("turn_end", async (event: any) => {
		const m = event.message;
		if (!m || m.role !== "assistant" || retries >= MAX_RETRIES) return;
		const content = m.content ?? [];
		if (content.some((c: any) => c?.type === "toolCall")) return;
		const text = content.filter((c: any) => c?.type === "text").map((c: any) => c.text ?? "").join("");
		if (!MALFORMED.test(text)) return;
		retries++;
		if (process.env.PI_LOOP_MARKERS) appendFileSync(process.env.PI_LOOP_MARKERS, `[loop] malformed tool call, re-issue requested (${retries})\n`);
		pi.sendUserMessage("[harness] Your last message contained tool-call markup as plain text, so no tool ran. " +
			"Re-issue it as a proper tool call. Do not draft long file contents in your thinking: put them directly in the tool call.",
			{ deliverAs: "followUp" });
	});

	// The loop has no human to answer: once the agent is done, leave, so the driver moves on.
	pi.on("agent_settled", async (_event: any, ctx: any) => {
		if (timer) clearInterval(timer);
		ctx.shutdown();
	});
}
