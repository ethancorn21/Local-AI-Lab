/**
 * image-budget: keep only the newest images in each model request.
 *
 * The model server accepts at most 8 images per prompt (vLLM --limit-mm-per-prompt), and every earlier turn is resent,
 * so a session that looks at many screenshots eventually has every request rejected with HTTP 400 and dies without a
 * hand-over (hand-over A/B, arm A iterations 7-9: 9-11 screenshots, "At most 8 image(s) may be provided in one
 * prompt"). Older images are replaced with a short note; the files are still on disk, so the agent can read one again.
 * Images are token-heavy, so this also saves context. Runs before the other before_provider_request handlers
 * (extensions load in name order), so wrapup.ts counts the trimmed prompt.
 * Env: IMAGE_KEEP (default 6: under the server's 8, with room for images added in the current turn).
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { appendFileSync } from "node:fs";

const KEEP = Number(process.env.IMAGE_KEEP ?? 6);
const NOTE = "[older image removed from the context to stay within the model's image limit; read the file again if you need it]";
const IMAGE_PART = new Set(["image_url", "input_image", "image"]);

export default function (pi: ExtensionAPI) {
	let reported = 0;
	pi.on("before_provider_request", (event: any) => {
		const p = event.payload;
		if (!p || typeof p !== "object" || !Array.isArray(p.messages)) return;
		let seen = 0;
		let dropped = 0;
		// newest first: walk messages and their parts backwards, keep the first KEEP images met
		const messages = [...p.messages].reverse().map((m: any) => {
			if (!Array.isArray(m?.content)) return m;
			let changed = false;
			const content = [...m.content].reverse().map((c: any) => {
				if (!IMAGE_PART.has(c?.type)) return c;
				seen++;
				if (seen <= KEEP) return c;
				dropped++;
				changed = true;
				return { type: "text", text: NOTE };
			}).reverse();
			return changed ? { ...m, content } : m;
		}).reverse();
		if (!dropped) return;
		if (dropped > reported) {
			reported = dropped;
			const line = `[images] kept the ${KEEP} newest, replaced ${dropped} older image(s) with a note`;
			if (process.env.PI_LOOP_MARKERS) appendFileSync(process.env.PI_LOOP_MARKERS, line + "\n");
			else console.error(line);
		}
		return { ...p, messages };
	});
}
