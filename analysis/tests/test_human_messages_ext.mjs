// node --experimental-strip-types test_human_messages_ext.mjs [EXT_DIR] : the human-messages extension, no model.
// The real human-messages.ts is loaded against a mock Pi API in a temp project. Checked: a message the human typed is
// delivered as the human's; one whose first line starts with "[driver]" (team mode: another agent carved part of this
// task, 2026-10-07) is delivered as the driver's, without that line; both are moved to .agent/inbox/delivered/.
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ext = resolve(process.argv[2] ?? join(here, "../../harness/pi-extensions"), "human-messages.ts");
const dir = mkdtempSync(join(tmpdir(), "hmsg-")); mkdirSync(join(dir, ".agent", "inbox"), { recursive: true }); process.chdir(dir);
const on = {}, sent = [];
const pi = { on: (e, f) => { on[e] = f; }, sendUserMessage: (m, o) => sent.push([m, o]) };
(await import(pathToFileURL(ext).href)).default(pi);
await on.agent_start?.({}, {});
writeFileSync(".agent/inbox/1000.md", "please also check the dark theme\n");
writeFileSync(".agent/inbox/1001.md", "[driver] carve 234\nAgent c has taken part of your task 234 as new task(s) 800.\n");
await new Promise((r) => setTimeout(r, 1600));
let fail = 0;
const check = (c, msg) => { console.log((c ? "ok   " : "FAIL ") + msg); if (!c) fail = 1; };
check(sent.length === 2, `two messages delivered (${sent.length})`);
check(sent[0]?.[0] === "[Message from the human, typed while you were working]\nplease also check the dark theme" && sent[0]?.[1]?.deliverAs === "steer",
	"the human's message: as the human's, as a steer");
check(sent[1]?.[0] === "[Message from the driver (the harness), sent while you were working]\nAgent c has taken part of your task 234 as new task(s) 800.",
	"a [driver] message: as the driver's, its first line dropped");
check(existsSync(".agent/inbox/delivered/1000.md") && existsSync(".agent/inbox/delivered/1001.md") && !existsSync(".agent/inbox/1001.md"), "both moved to delivered/");
process.exit(fail);
