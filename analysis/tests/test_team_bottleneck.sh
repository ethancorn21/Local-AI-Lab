#!/usr/bin/env bash
# test_team_bottleneck.sh [DRIVER_DIR] : idle agents go for the work the team waits on. frontpage 2026-10-07: agent a's
# split session designed 236's parts 259-262, added them to 236's `Depends on:` line and ended without writing them; the
# driver published 236, which then waited for four tasks that did not exist. a held 236 (and 234, which it built for 90
# minutes); 237 and 238 waited behind 236; agents b and c, both slower than a, sat idle 90 and 30 minutes, because
# only a faster agent could ask for a claim. Checked here:
#   - parts named but never written: missing_deps; such a task is workable (own claim or unclaimed, also with other
#     unmet dependencies) and its prompt says to write the parts first; once they are in main it waits for them as usual
#   - the parts a session writes go to main (team_publish_split), and an idle agent can take one
#   - parked claims: the holder's session is on another task (loops/<id>.task) or a prep; any idle agent asks for it,
#     slower or not, whatever waits on it; not while the holder builds it or verifies it between sessions, nor with no
#     loops/<id>.task yet; the request says parked, the holder's give and the new owner's prompt say why
# Real git repo and worktrees; agents a, b, c are this shell's functions run with different AGENT_IDs. Run on the VM.
set -u
D=${1:-$(cd "$(dirname "$0")/../../harness/driver" && pwd)}
T=$(mktemp -d); trap 'kill $apid $bpid $cpid 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
export HOME=$T/home PATH="$D:$PATH"; mkdir -p "$HOME/.agent-kit/agents"
printf 'LLM_URL=x\nTEAM_SPEED=4\n' > "$HOME/.agent-kit/agents/a.env"
printf 'LLM_URL=y\nTEAM_SPEED=1\nTEAM_MAX_TOUCHES=8\n' > "$HOME/.agent-kit/agents/b.env"
printf 'LLM_URL=z\nTEAM_SPEED=3\n' > "$HOME/.agent-kit/agents/c.env"
P=$T/frontpage; mkdir -p "$P/tasks" "$P/src" "$P/e2e"; cd "$P"
git init -q -b main && git config user.email t@t && git config user.name t && echo ".agent/" > .gitignore
task() { printf 'Status: %s\n# %s\n\nDepends on: %s\nTouches: %s\n\n## Acceptance criteria\n- [ ] works\n' "$2" "$1" "$3" "$4" > "tasks/$1.md"; }
printf 'Status: done\n# 000\n' > tasks/000-plan.md
task 233-rows done none "src/rows.py"
task 234-strip in-progress 233 "src/strip.py"
task 236-actions open "233, 259, 260" "e2e/flow.mjs"                # 259 and 260: named by a split, never written
task 237-board open 236 "src/board.py"
task 238-e2e open "234, 237" "e2e/catchup.mjs"
task 290-unclaimed open "291" "src/x.py"                           # unclaimed, its part 291 never written
task 292-mixed open "234, 293" "src/y.py"                          # waits for 234 (being built) AND names unwritten 293
for f in rows strip board x y; do echo "$f" > "src/$f.py"; done; echo flow > e2e/flow.mjs
git add -A && git commit -q -m init
for x in a b c; do git worktree add -q "$P.$x" -b "agent/$x"; mkdir -p "$P.$x/.agent"; done
mkdir -p "$P/.agent/team/claims" "$P/.agent/team/loops"
sleep 600 & apid=$!; sleep 600 & bpid=$!; sleep 600 & cpid=$!
echo $apid > "$P/.agent/team/loops/a.pid"; echo $bpid > "$P/.agent/team/loops/b.pid"; echo $cpid > "$P/.agent/team/loops/c.pid"
claim() { local p; eval "p=\$${1}pid"; mkdir -p "$P/.agent/team/claims/$2"; echo "$1 $p $(date +%s) $P.$1/tasks/$3" > "$P/.agent/team/claims/$2/owner"; }
owner() { cut -d' ' -f1 "$P/.agent/team/claims/$1/owner" 2>/dev/null; }
building() { echo "$2" > "$P/.agent/team/loops/$1.task"; }   # building <agent> <what its current or last session is on>
fns() {  # the definition of one function of agent-loop (one line or a block)
  awk -v n="$1" '!p && $0 ~ "^"n"\\(\\) *\\{" { print; if ($0 ~ /; }[[:space:]]*(#.*)?$/) exit; p = 1; next } p { print } p && /^}/ { exit }' "$D/agent-loop"
}
as() {  # as <agent> <commands> : in that agent's checkout, with its identity and the driver's helpers
  ( cd "$P.$1" && export TEAM_DIR=$P AGENT_ID=$1 HOME; TEAM_SPEED=$(sed -n 's/^TEAM_SPEED=//p' "$HOME/.agent-kit/agents/$1.env")
    [ "$1" = b ] && export TEAM_MAX_TOUCHES=8
    eval "$(for f in status_of task_id handover_of pending_subtasks workable next_task commit_leftovers ask_field asks_with inbox_pending dead_status wait_for_human; do fns $f; done)"
    log() { echo "LOG $AGENT_ID $*" >> "$T/log"; }
    . "$D/agent-team-lib"; eval "$2" )
}
killtree() { local c; for c in $(pgrep -P "$1"); do killtree "$c"; done; kill "$1" 2>/dev/null; }
claim a 234 234-strip.md; claim a 236 236-actions.md

# --- parts named but never written ---
[ "$(as a 'missing_deps tasks/236-actions.md' | tr '\n' ' ')" = "259 260 " ] && ok "missing_deps: 259 and 260 have no task file" || bad "missing: $(as a 'missing_deps tasks/236-actions.md' | tr '\n' ' ')"
[ -z "$(as a 'missing_deps tasks/238-e2e.md')" ] && ok "238's dependencies all exist" || bad "238 missing: $(as a 'missing_deps tasks/238-e2e.md')"
r=$(as a 'team_why_not tasks/236-actions.md'); [ -z "$r" ] && ok "a's own 236 is workable: its parts are to be written, not waited for" || bad "236 for a: $r"
n=$(as a 'team_missing_note tasks/236-actions.md')
[[ $n == " CREATE THE PARTS FIRST: this task's \`Depends on:\` line names task(s) 259, 260, but no task file exists"* ]] && grep -q 'with exactly that number' <<<"$n" && grep -q 'Never both ways' <<<"$n" \
  && ok "the prompt says to write 259 and 260 first, with those numbers, no cycle" || bad "note: $(cut -c1-160 <<<"$n")"
[ -z "$(as a 'team_missing_note tasks/238-e2e.md')" ] && ok "no note for a task whose dependencies exist" || bad "note for 238"
r=$(as b 'team_why_not tasks/290-unclaimed.md'); [ -z "$r" ] && ok "an unclaimed task naming an unwritten part is free to take" || bad "290 for b: $r"
r=$(as b 'team_why_not tasks/292-mixed.md'); [ -z "$r" ] && ok "a task that also waits for real work still gets its parts written first" || bad "292 for b: $r"
r=$(as b 'team_why_not tasks/238-e2e.md'); [ "$r" = "waits for 234,237" ] && ok "238 still waits for 234 and 237" || bad "238 for b: $r"
[ "$(as b 'prep_layers tasks/237-board.md')" -lt 99 ] && ok "237 (behind 236) can be prepared" || bad "237 layers $(as b 'prep_layers tasks/237-board.md')"
grep -q 'split=$(team_missing_note "$task")' "$D/agent-loop" && grep -q 'tev parts_missing' "$D/agent-loop" \
  && ok "agent-loop puts the note in the prompt and publishes the session's task files like a split" || bad "agent-loop does not use team_missing_note"

# --- the parts a session writes reach main at once ---
h0=$(git -C "$P.a" rev-parse HEAD)
task_in() { (cd "$P.$1" && task "${@:2}"); }
task_in a 259-unmore open 233 "src/unmore.py"; task_in a 260-feedline open 233 "src/feedline.py"
sed -i 's/^- \[ \] works/- [ ] the e2e flow passes/' "$P.a/tasks/236-actions.md"
git -C "$P.a" add -A && git -C "$P.a" commit -q -m "236: write parts 259, 260"
as a 'team_publish_split tasks/236-actions.md '"$h0"
[ -f "$P/tasks/259-unmore.md" ] && [ -f "$P/tasks/260-feedline.md" ] && [ "$(git -C "$P" log -1 --format=%s)" = "[driver] 236 split by agent a: task files published to main (236 259 260)" ] \
  && ok "259, 260 and 236 published to main in one driver commit" || bad "publish: $(git -C "$P" log -1 --format=%s); $(ls "$P/tasks")"
git -C "$P.b" merge -q --no-edit main > /dev/null 2>&1; git -C "$P.c" merge -q --no-edit main > /dev/null 2>&1
[ -z "$(as a 'missing_deps tasks/236-actions.md')" ] && [ "$(as a 'team_why_not tasks/236-actions.md')" = "waits for 259,260" ] \
  && ok "with its parts in main, 236 waits for them like any dependency" || bad "236 after publish: $(as a 'team_why_not tasks/236-actions.md')"
[ -z "$(as c 'team_why_not tasks/259-unmore.md')" ] && [ -z "$(as b 'team_why_not tasks/260-feedline.md')" ] \
  && ok "b and c can each take a part" || bad "parts: c 259 '$(as c 'team_why_not tasks/259-unmore.md')', b 260 '$(as b 'team_why_not tasks/260-feedline.md')'"

# --- parked claims: any idle agent asks for them ---
git -C "$P" rm -q tasks/259-unmore.md tasks/260-feedline.md && git -C "$P" commit -q -m "back to the unwritten parts"
git -C "$P" checkout -q "$h0" -- tasks/236-actions.md && git -C "$P" commit -q -m "236 as published at 18:36"
for x in a b c; do git -C "$P.$x" reset -q --hard main; done
facts() { as "$1" team_takeover_facts | grep "^$2"$'\t'; }
[[ $(facts c 236) == $'236\ta\t4\t3\t'*$'\t0' ]] && [ -z "$(as c team_takeover_candidate)" ] && ok "no loops/a.task yet: not counted as parked, c (slower) asks for nothing" || bad "no task file: $(facts c 236) / $(as c team_takeover_candidate)"
building a tasks/236-actions.md
[[ $(facts c 236) == *$'\t0' ]] && [ "$(as c team_takeover_candidate)" = "234 parked" ] && ok "a builds 236 (or just did, and verifies it): 236 is not parked, its other claim 234 is" || bad "while a builds 236: $(facts c 236) / $(as c team_takeover_candidate)"
building a tasks/234-strip.md
[[ $(facts c 236) == $'236\ta\t4\t3\t3\t\t1' ]] && ok "facts: 236 parked (a's session is on 234), rank 3 (236 -> 237 -> 238), c could build it" || bad "facts: $(facts c 236)"
[ "$(as c team_takeover_candidate)" = "236 parked" ] && ok "c (slower than a) asks for parked 236" || bad "c's candidate: $(as c team_takeover_candidate)"
[ "$(as b team_takeover_candidate)" = "236 parked" ] && ok "b (a quarter of a's speed) would too" || bad "b's candidate: $(as b team_takeover_candidate)"
mkdir -p "$P/.agent/team/claims/000"; echo "a $apid $(date +%s) $P.a/tasks/000-plan.md" > "$P/.agent/team/claims/000/owner"
[ -z "$(as c team_takeover_candidate)" ] && ok "a re-plans (holds 000): nothing is parked meanwhile (the plan may rewrite 236)" || bad "parked during a re-plan: $(as c team_takeover_candidate)"
rm -rf "$P/.agent/team/claims/000"
[ -z "$(as c 'TEAM_CYCLES=$(printf "a\t236 237\t236\n"); team_takeover_candidate')" ] && [[ $(as c 'TEAM_CYCLES=$(printf "a\t236 237\t236\n"); team_takeover_facts' | grep $'^236\t') == *$'\tpart of a cycle\t1' ]] \
  && ok "a claim in a cycle is never asked for as parked (the cycle rule gathers it; otherwise two waiting agents pass it back and forth)" || bad "cycle: $(as c 'TEAM_CYCLES=$(printf "a\t236 237\t236\n"); team_takeover_facts')"
[[ $(facts c 234) == $'234\ta\t4\t3\t'*$'\t0' ]] && ok "234, which a is building, is not parked" || bad "234 facts: $(facts c 234)"
building a "prep tasks/237-board.md"
[ "$(as c team_takeover_candidate)" = "236 parked" ] && ok "a in a prep session: all its claims are parked" || bad "prep: $(as c team_takeover_candidate)"
claim a 290 290-unclaimed.md; building a tasks/234-strip.md
r=$(as c 'team_takeover_facts | team-takeover pick 2>&1 >/dev/null | grep "^290 "')
[ "$(as c team_takeover_candidate)" = "236 parked" ] && [[ $r == "290 (agent a, rank 1, parked): can be taken over" ]] && ok "a parked claim nothing waits on qualifies too; the higher rank (236) goes first" || bad "290: $r / $(as c team_takeover_candidate)"
rm -rf "$P/.agent/team/claims/290"
as c team_takeover_ask; d=$P/.agent/team/takeover/236
[ "$(cut -d' ' -f1 "$d/asker" 2>/dev/null)" = c ] && [ "$(cat "$d/why" 2>/dev/null)" = parked ] && grep -q 'LOG c team: nothing to build - asked agent a for 236 (it holds 236 but is building something else)' "$T/log" \
  && ok "c's request for 236: parked, logged" || bad "request: $(cat "$d/asker" "$d/why" 2>&1); $(tail -1 "$T/log")"
[ -z "$(as b team_takeover_candidate)" ] && ok "asked for already: b does not ask too" || bad "b asks too: $(as b team_takeover_candidate)"
# a's session on 234 watches for requests: a parked claim goes at once
( as a 'TEAM_TAKEOVER_POLL_S=1 team_session_watch tasks/234-strip.md' ) & w=$!
for _ in $(seq 20); do [ "$(owner 236)" = c ] && break; sleep 0.5; done; killtree $w; wait $w 2>/dev/null
[ "$(owner 236)" = c ] && [ ! -d "$d" ] && [ ! -f "$P.a/.agent/handover-now" ] && grep -q 'LOG a team: gave 236 to agent c (this agent was building something else, and agent c had nothing to build)' "$T/log" \
  && ok "a gives parked 236 to c during its session on 234, without cutting that session" || bad "give: owner $(owner 236); $(grep 'gave 236' "$T/log")"
[ "$(sed -n 3p "$P/.agent/team/carry/236" 2>/dev/null)" = parked ] && ok "the carry says why" || bad "carry: $(cat "$P/.agent/team/carry/236" 2>&1)"
as c team_takeover_receive; n=$(as c 'team_takeover_note tasks/236-actions.md')
[[ $n == " TAKEN OVER: agent a held this task but was building another one, and you had nothing to build"* ]] && ok "c's first prompt on 236 says why it has it" || bad "note: $(cut -c1-120 <<<"$n")"
r=$(as c 'team_why_not tasks/236-actions.md'); n=$(as c 'team_missing_note tasks/236-actions.md')
[ -z "$r" ] && [[ $n == *"259, 260"* ]] && ok "c can build 236 now, and is told to write 259 and 260 first" || bad "c on 236: '$r' / $(cut -c1-80 <<<"$n")"

# --- carve: an idle agent carves unbuilt work out of a task another agent builds ---
# b builds 400 (four boxes, one ticked; it has changed src/p1.py); 401 waits for 400
rm -rf "$P/.agent/team/claims/"* "$P/.agent/team/takeover"
(cd "$P" && printf 'Status: in-progress\n# 400: the strip\n\nDepends on: 233\nTouches: src/p1.py, src/p2.py, src/p3.py\n\n## Acceptance criteria\n- [x] the strip renders\n- [ ] the strip scrolls\n- [ ] the strip has a legend\n- [ ] tests/test_strip.py covers it\n\n## Hand-over\nNext: the strip scrolls (src/p2.py).\n- [ ] not a box: below the hand-over\n' > tasks/400-strip.md
  task 401-after open 400 "src/after.py"; git add -A && git commit -q -m "400, 401")
for x in a b c; do git -C "$P.$x" merge -q --no-edit main > /dev/null 2>&1; done
echo p1 > "$P.b/src/p1.py"; git -C "$P.b" add -A && git -C "$P.b" commit -q -m "400: p1"
claim b 400 400-strip.md; building b tasks/400-strip.md
tgt=$(as c team_carve_target)
[ "$tgt" = "400 b $P.b/tasks/400-strip.md" ] && ok "c's carve target: 400, which b builds" || bad "target: '$tgt'"
[ -z "$(TEAM_CARVE=0 as c team_carve_target)" ] && ok "TEAM_CARVE=0: off" || bad "TEAM_CARVE=0"
mkdir -p "$P/.agent/team/claims/000"; echo "a $apid $(date +%s) $P.a/tasks/000-plan.md" > "$P/.agent/team/claims/000/owner"
[ -z "$(as c team_carve_target)" ] && ok "a re-plans (holds 000): no carving meanwhile" || bad "carve during a re-plan"
rm -rf "$P/.agent/team/claims/000"
building b tasks/290-unclaimed.md; [ -z "$(as c team_carve_target)" ] && ok "b's session is on another task: 400 is parked (taken over), not carved" || bad "carve of a parked claim"
building b tasks/400-strip.md
sed -i 's/^Touches:.*/&\nSplit: no - one template/' "$P.b/tasks/400-strip.md"; [ -z "$(as c team_carve_target)" ] && ok "Split: no: not carved" || bad "carved despite Split: no"
sed -i '/^Split: no/d' "$P.b/tasks/400-strip.md"
mkdir -p "$P/.agent/team/splitting"; echo b > "$P/.agent/team/splitting/400"
[ -z "$(as c team_carve_target)" ] && ok "b's session is splitting 400: not carved meanwhile" || bad "carved during a split session"
echo zz > "$P/.agent/team/splitting/400"; [ -n "$(as c team_carve_target)" ] && ok "(a split marker of a dead loop does not count)" || bad "dead split marker blocks"
rm -rf "$P/.agent/team/splitting"
sed -i '1s/.*/Status: done/' "$P/tasks/000-plan.md"
n=$(as c 'team_carve_prompt 400 b '"$P.b/tasks/400-strip.md"' 77')
[[ $n == "You are iteration 77 of an autonomous coding loop, in a CARVE SESSION: you have nothing to build, while agent b builds task 400 and 1 more task(s) wait on it"* ]] \
  && grep -qF '  3. [ ] the strip has a legend' <<<"$n" && grep -qF '  1. [x] the strip renders' <<<"$n" && ! grep -q 'not a box' <<<"$n" \
  && grep -qF 'Files agent b has changed so far (they stay with it): src/p1.py.' <<<"$n" && grep -qF 'tasks/carve/400.md' <<<"$n" && grep -qF 'lowest free in 800-1099' <<<"$n" \
  && ok "the carve prompt: boxes numbered (hand-over aside), b's changed files, the carve list, c's numbers" || bad "prompt: $(cut -c1-200 <<<"$n")"
carve() {  # carve <list line> <part Depends> <part Touches> : c's carve session writes one part 800 and the carve list
  local h0; h0=$(git -C "$P.c" rev-parse HEAD)
  (cd "$P.c" && printf 'Status: open\n# 800: the legend\nCarved from: 400\n\n## Goal\nA legend.\n\nDepends on: %s\nTouches: %s\n\n## Acceptance criteria\n- [ ] the strip has a legend\n' "$2" "$3" > tasks/800-legend.md
    mkdir -p tasks/carve; printf '%s\n' "$1" > tasks/carve/400.md; echo stray > src/stray.py; git add -A && git commit -q -m "carve 400: 800")
  carve_lock_out=$(as c 'carve_lock 400 && echo locked')
  as c "team_carve_finish 400 b $P.b/tasks/400-strip.md $h0; echo \"RESULT \$CARVE_RESULT\"" | grep '^RESULT' | cut -d' ' -f2
}
ev() { grep -c "\"event\":\"$1\"" "$P/.agent/team/events.jsonl" 2>/dev/null || echo 0; }
[ "$(carve '3 -> 800' 233 src/p1.py)" = rejected ] && grep -q 'part 800 touches file(s) agent holding 400 changed: src/p1.py' "$T/log" && [ ! -f "$P/tasks/800-legend.md" ] \
  && ok "rejected: the part touches src/p1.py, which b changed" || bad "p1 carve: $(grep 'carve 400' "$T/log" | tail -1)"
[ -f "$P/.agent/team/carve-tries/400" ] && [ -z "$(as c team_carve_target)" ] && ok "not tried again on the same version of b's task file" || bad "retry right away"
echo "- notes" >> "$P.b/tasks/400-strip.md"; [ -n "$(as c team_carve_target)" ] && ok "b's task file changed: carving may be tried again" || bad "no retry after a change"
[ "$(carve '3 -> 800' "233, 400" src/p3.py)" = rejected ] && grep -q 'part 800 depends on 400' "$T/log" && ok "rejected: the part depends on 400" || bad "dep carve: $(tail -1 "$T/log")"
echo "- notes 2" >> "$P.b/tasks/400-strip.md"
[ "$(carve '1 -> 800' 233 src/p3.py)" = rejected ] && grep -q 'box 1 is ticked already' "$T/log" && ok "rejected: box 1 is done" || bad "ticked carve: $(tail -1 "$T/log")"
echo "- notes 3" >> "$P.b/tasks/400-strip.md"
[ "$(carve $'2 -> 800\n3 -> 800\n4 -> 800' 233 src/p3.py)" = rejected ] && grep -q 'would keep no unticked box' "$T/log" && ok "rejected: b would keep nothing" || bad "all carve: $(tail -1 "$T/log")"
echo "- notes 4" >> "$P.b/tasks/400-strip.md"
[ "$(carve '3 -> 800' 234 src/p3.py)" = rejected ] && grep -q 'no part can start now' "$T/log" && ok "rejected: the only part waits for unfinished 234" || bad "start carve: $(tail -1 "$T/log")"
echo "- notes 5" >> "$P.b/tasks/400-strip.md"
[ "$(carve 'none: every box needs the p2 scroller' 233 src/p3.py)" = none ] && grep -q 'carve 400: nothing to carve - every box needs the p2 scroller' "$T/log" && ok "none: logged, nothing published" || bad "none: $(tail -1 "$T/log")"
echo "- notes 6" >> "$P.b/tasks/400-strip.md"
r=$(carve '3 -> 800' 233 src/p3.py)
[ "$r" = published ] && [ -f "$P/tasks/800-legend.md" ] && [ "$(git -C "$P" log -1 --format=%s)" = "[driver] 400 carved by agent c: parts 800 (boxes 3) while agent b builds the rest" ] \
  && ok "published: 800 in main in one driver commit" || bad "publish: $r; $(git -C "$P" log -1 --format=%s)"
grep -q '^Depends on: 400, 800$' "$P/tasks/401-after.md" && ok "401, which waited for 400, now waits for 800 too" || bad "401: $(grep '^Depends' "$P/tasks/401-after.md")"
[ ! -f "$P.c/tasks/800-legend.md" ] && [ ! -f "$P.c/src/stray.py" ] && [ ! -d "$P/.agent/team/carve/400" ] && ok "c's branch keeps nothing of the session (the part reaches it through main); lock freed" || bad "c's branch: $(ls "$P.c/tasks" "$P.c/src")"
c=$P/.agent/team/carved/400
[ "$(sed -n 's/^boxes //p' "$c")" = "3:800" ] && [ "$(sed -n 's/^files //p' "$c")" = "src/p3.py" ] && ok "carved/400: box 3 -> 800, src/p3.py goes to the part" || bad "carved: $(cat "$c" 2>&1)"
[ "$(as a 'touches_of '"$P.b"'/tasks/400-strip.md' | tr '\n' ' ')" = "src/p1.py src/p2.py " ] && ok "400's Touches leave out src/p3.py at once" || bad "touches: $(as a 'touches_of '"$P.b"'/tasks/400-strip.md' | tr '\n' ' ')"
git -C "$P.a" merge -q --no-edit main > /dev/null 2>&1
[ -z "$(as a 'team_why_not tasks/800-legend.md')" ] && ok "a can build 800 now, while b still builds 400" || bad "800 for a: $(as a 'team_why_not tasks/800-legend.md')"
[ -z "$(as c team_carve_target)" ] && ok "no second carve of 400 before b applies the first" || bad "second carve"
m=$(grep -l '^\[driver\] carve 400$' "$P.b"/.agent/inbox/*.md 2>/dev/null | head -1)
[ -n "$m" ] && grep -q 'box(es) 3 of your Acceptance criteria. The file(s) src/p3.py are theirs now. Do not build those boxes or change those files' "$m" \
  && ok "b's inbox: a driver message saying what moved" || bad "inbox: $(cat "$P.b"/.agent/inbox/*.md 2>&1 | head -3)"
[ -z "$(as b 'team_split_note tasks/400-strip.md')" ] && ok "no idle split for b while its carve waits to be applied" || bad "split note: $(as b 'team_split_note tasks/400-strip.md' | cut -c1-80)"
grep -qx 'text 3 the strip has a legend' "$P/.agent/team/carved/400" && ok "carved/400 keeps box 3's text (found by it if b renumbers its boxes)" || bad "no text line: $(cat "$P/.agent/team/carved/400")"
# b's loop: right after its session, before verifying
as b team_carve_apply
grep -qx -- '- \[moved to 800\] the strip has a legend' "$P.b/tasks/400-strip.md" && grep -qx 'Touches: src/p1.py, src/p2.py' "$P.b/tasks/400-strip.md" \
  && grep -qx -- '- \[ \] not a box: below the hand-over' "$P.b/tasks/400-strip.md" && ok "b's 400: box 3 marked moved, src/p3.py off its Touches, the hand-over untouched" || bad "b's 400: $(sed -n '3,12p' "$P.b/tasks/400-strip.md")"
[ "$(git -C "$P.b" log -1 --format=%s)" = "[driver] 400 carved by agent c: box(es) 3 moved to 800, src/p3.py off its Touches" ] && [ ! -f "$c" ] && [ -z "$m" -o ! -f "$m" ] \
  && ok "one driver commit; carved/400 done; the undelivered message dropped (the task file says it)" || bad "apply: $(git -C "$P.b" log -1 --format=%s); $(ls "$P/.agent/team/carved")"
u=$(sed '/^## Hand-over/,$d' "$P.b/tasks/400-strip.md" | grep -cE '^[[:space:]]*- \[ \]'); [ "$u" = 2 ] && ok "two boxes left for b; the moved one no longer counts as unticked" || bad "unticked: $u"
n=$(as b 'team_carved_note tasks/400-strip.md'); n2=$(as b 'team_carved_note tasks/400-strip.md')
[[ $n == " CARVED: while you worked, agent c took box(es) 3 of this task as new task(s) 800"* ]] && [ -z "$n2" ] && ok "b's next prompt says so, once" || bad "note: '$n' / '$n2'"
grep -q 'vn=$(team_carved_note "$task")' "$D/agent-loop" && grep -q 'team_takeover_settle; team_carve_apply; }' "$D/agent-loop" && grep -q 'team_cycles_sync; team_carve_apply; }' "$D/agent-team-lib" \
  && ok "agent-loop applies carves after each session and before each pick, and puts the note in the prompt" || bad "agent-loop wiring"
# b ticks a moved box anyway (it built it before reading the message)
(cd "$P" && printf 'Status: open\n# 801: tests\nCarved from: 400\n\nDepends on: 233\nTouches: tests/test_strip.py\n\n## Acceptance criteria\n- [ ] tests\n' > tasks/801-tests.md && git add -A && git commit -q -m 801)
printf 'by c 1\nholder b\nboxes 4:801\nfiles \nparts 801\ntext 4 tests/test_strip.py covers it\n' > "$P/.agent/team/carved/400"; sed -i 's/^- \[ \] tests\/test_strip.py covers it/- [x] tests\/test_strip.py covers it/' "$P.b/tasks/400-strip.md"
as b team_carve_apply; [ "$(ev carve_conflict)" = 1 ] && grep -q 'ticked meanwhile or no longer has (ticked 4)' "$T/log" && ok "a moved box b ticked meanwhile (found by its text: box 3 now) stays ticked, and it is logged" || bad "conflict: $(tail -1 "$T/log")"
echo "- notes 7" >> "$P.b/tasks/400-strip.md"
# moot: b finished 400 before the carve came back
rm -rf "$P/.agent/team/claims/400"
[ "$(carve '2 -> 802' 233 src/p2.py)" = moot ] && [ ! -f "$P/tasks/802-legend.md" ] && ok "moot: b no longer holds 400 - nothing published" || bad "moot"

echo; [ "$fail" = 0 ] && echo "ALL OK" || echo "SOME FAILED"
exit $fail
