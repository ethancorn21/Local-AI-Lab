/**
 * thinking-budget: cap the model's thinking per response, as llama.cpp's --reasoning-budget does on the 5060 Ti.
 *
 * Why: on vLLM nothing bounds thinking except Pi's per-response maxTokens. At reasoning effort xhigh the model once
 * deliberated for 32,768 tokens in a single response and hit that cap without acting (hollowdeep iteration 65), which
 * ended the session with nothing done. vLLM's `thinking_token_budget` ends the thinking at the budget and lets the
 * model answer. Added to every request that has thinking enabled; a request that already sets it is left alone.
 * The cap was 16384 until 2026-10-05; raised to 32768 (and Pi's maxTokens to 49152, room for the answer) once the
 * servers ended a capped thinking block with a wrap-up sentence instead of a bare </think> (docs/model-choice.md);
 * back to 16384 (maxTokens 32768) on 2026-10-06: on the held-out set 32k passed the same 10 of 14 for 1.8x the tokens.
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
