/**
 * bash-timeout: give every bash command a default timeout, so a hung command cannot eat a whole session.
 *
 * Why: Pi's bash tool has no default timeout and the model never passes one. In the thinking-effort A/B (2026-09-29,
 * arm X2) the agent wrote a windower whose test looped forever; `pytest` never returned, and three sessions in a row
 * sat idle until the driver's 45-minute limit. hollowdeep had 11 of 216 sessions end on that limit. With a default,
 * Pi kills the command's process tree and returns the output so far plus "Command timed out after N seconds", so the
 * agent sees where it hung and fixes it in the same session. A call that sets its own timeout is left alone.
 * Env BASH_TIMEOUT overrides the default (seconds).
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const DEFAULT_S = Number(process.env.BASH_TIMEOUT ?? 600);

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event: any) => {
		if (event.toolName !== "bash" || !event.input || event.input.timeout !== undefined) return;
		event.input.timeout = DEFAULT_S;
	});
}
