/**
 * thinking-budget: cap the model's thinking per response, the way llama.cpp's --reasoning-budget 16384 did.
 *
 * Why: on vLLM nothing bounds thinking except Pi's per-response maxTokens. At reasoning effort xhigh the model once
 * deliberated for 32,768 tokens in a single response and hit that cap without acting (hollowdeep iteration 65), which
 * ended the session with nothing done. vLLM's `thinking_token_budget` ends the thinking at the budget and lets the
 * model answer. Added to every request that has thinking enabled; a request that already sets it is left alone.
 * Env THINKING_BUDGET overrides the default (testing).
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const BUDGET = Number(process.env.THINKING_BUDGET ?? 16384);

export default function (pi: ExtensionAPI) {
	pi.on("before_provider_request", (event: any) => {
		const p = event.payload;
		if (!p || typeof p !== "object" || !Array.isArray(p.messages)) return;
		if (!p.chat_template_kwargs?.enable_thinking || p.thinking_token_budget !== undefined) return;
		return { ...p, thinking_token_budget: BUDGET };
	});
}
