// node --experimental-strip-types test_ask_human_ext.mjs EXT_DIR TYPEBOX_DIR : the ask_human tool's recommend and
// human_only rules, no model. The real ask-human.ts is copied next to a node_modules/typebox link (Pi's own copy) and
// loaded against a mock Pi API, with a stub doorbell. Checked: a decision without a recommendation is refused; an
// unknown human_only reason is refused; a recommendation lands in the request file and in the doorbell text; a
// human_only request needs no recommendation and is marked `human-only: <reason>` (the loop never auto-decides it).
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [extDir, typebox] = process.argv.slice(2).map((p) => resolve(p));
const dir = mkdtempSync(join(tmpdir(), "askext-"));
mkdirSync(join(dir, "node_modules")); symlinkSync(typebox, join(dir, "node_modules", "typebox"));
copyFileSync(join(extDir, "ask-human.ts"), join(dir, "ask-human.ts"));
const proj = join(dir, "proj"); mkdirSync(proj); process.chdir(proj);
const bell = join(dir, "bell"); writeFileSync(bell, `#!/bin/sh\ncat >> ${dir}/bell.log; echo sent\n`); chmodSync(bell, 0o755);
process.env.RING_DOORBELL = bell; process.env.PI_LOOP_TASK = "tasks/001-x.md"; delete process.env.PI_LOOP_PREP;

let tool;
(await import(pathToFileURL(join(dir, "ask-human.ts")).href)).default({ registerTool: (t) => { tool = t; } });
let fail = 0;
const ok = (c, m) => { console.log(`${c ? "ok  " : "FAIL"} ${m}`); if (!c) fail = 1; };
const call = async (p) => { try { return { r: await tool.execute("id", p) }; } catch (e) { return { err: String(e.message) }; } };
const req = "Pick a or b for the corpus: a keeps the assertions, b drops the test. Tried both locally.";
const askFile = (n) => readFileSync(join(proj, ".agent", "asks", `${n}.md`), "utf8");

let x = await call({ request: req });
ok(/recommend missing/.test(x.err ?? ""), "no recommendation: refused");
x = await call({ request: req, recommend: "a" });
ok(/recommend missing/.test(x.err ?? ""), "a one-letter recommendation: refused");
x = await call({ request: req, human_only: "preference" });
ok(/human_only must be one of/.test(x.err ?? ""), "human_only 'preference': refused");
x = await call({ request: req, recommend: "(a): keeps every assertion, test data only." });
const f1 = x.err ? "" : askFile("001");
ok(!x.err && /## Recommendation\n\(a\): keeps every assertion/.test(f1) && !/human-only/.test(f1), "recommendation filed in 001");
ok(/answers for them: go ahead with your recommendation/.test(x.r?.content?.[0]?.text ?? ""), "tool result names the deadline");
ok(/Recommended: \(a\): keeps every assertion/.test(readFileSync(join(dir, "bell.log"), "utf8")), "doorbell text carries it");
x = await call({ request: "Plug the second 3090 into slot 2 and power the box back on, then tell me.", human_only: "hardware" });
const f2 = x.err ? "" : askFile("002");
ok(!x.err && /^human-only: hardware$/m.test(f2) && !/## Recommendation/.test(f2), "human_only hardware: filed without a recommendation");
process.exit(fail);
