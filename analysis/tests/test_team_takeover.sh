#!/usr/bin/env bash
# test_team_takeover.sh [DRIVER_DIR] : a faster agent takes over a slower agent's task that others wait on.
# frontpage 2026-10-06: agent b (a quarter of a's speed) took 253, which five tasks waited on, 14 s after a merged 252;
# b's next session went to 243b (its lowest-rank-first order, applied to its own claims too), 253 lay parked, and a
# waited 47 minutes; then handing 243 over reset b's whole branch, and b's work on 253 was lost and done again. Checked:
#   - own claims first, the highest rank first, slow agent or not (b: 253 before 243b)
#   - the ask: a faster agent with nothing to build asks for the slower agent's claim others wait on, the highest rank;
#     not a claim nothing waits on, one waiting for the human, one it could not build, nor a faster agent's
#   - the answer between sessions: the claim goes to the asker with only that task's work (by commit subject and the
#     ledger); the holder's branch keeps its other work; the asker applies the work and its prompt says so, once
#   - during a session on that task: the session is told to hand over (.agent/handover-now); on another task (parked):
#     the claim goes at once and its files leave the branch right after the session
#   - a hand-over keeps the other claims' work (the whole branch is reset only when no other claim is held)
#   - an asker that is gone is dropped; an asker's prep session is cut when a takeover is possible; TEAM_TAKEOVER=0
# Real git repo and worktrees; agents a and b are this shell's functions run with different AGENT_IDs. Run on the VM.
set -u
D=${1:-$(cd "$(dirname "$0")/../../harness/driver" && pwd)}
T=$(mktemp -d); trap 'kill $apid $bpid 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
export HOME=$T/home PATH="$D:$PATH"; mkdir -p "$HOME/.agent-kit/agents"
printf 'LLM_URL=x\nTEAM_SPEED=4\n' > "$HOME/.agent-kit/agents/a.env"
printf 'LLM_URL=y\nTEAM_SPEED=1\nTEAM_MAX_TOUCHES=2\n' > "$HOME/.agent-kit/agents/b.env"
P=$T/proj; mkdir -p "$P/tasks" "$P/src"; cd "$P"
git init -q -b main && git config user.email t@t && git config user.name t && echo ".agent/" > .gitignore
task() { printf 'Status: %s\n# %s\n\nDepends on: %s\nTouches: %s\n\n## Acceptance criteria\n- [ ] works\n' "$2" "$1" "$3" "$4" > "tasks/$1.md"; }
printf 'Status: done\n# 000\n' > tasks/000-plan.md
task 253-flow open none "src/flow.py, src/flow_helper.py"   # rank 3: 253 -> 232 -> 234
task 232-panel open 253 "src/panel.py"
task 234-after open 232 "src/after.py"
task 243-perf split none "src/feed.py"                         # nothing waits on 243
task 243b-fix open none "src/feed.py"
task 270-asked open none "src/asked.py"                        # rank 2, but waits for the human's answer
task 271-after-asked open 270 "src/a271.py"
for f in flow feed shared panel asked; do echo "$f base" > "src/$f.py"; done; echo base > notes.txt
git add -A && git commit -q -m init
git worktree add -q "$P.a" -b agent/a && git worktree add -q "$P.b" -b agent/b
mkdir -p "$P/.agent/team/claims" "$P/.agent/team/loops" "$P.a/.agent" "$P.b/.agent"
sleep 600 & apid=$!; sleep 600 & bpid=$!
echo $apid > "$P/.agent/team/loops/a.pid"; echo $bpid > "$P/.agent/team/loops/b.pid"
claim() { mkdir -p "$P/.agent/team/claims/$2"; echo "$1 $([ "$1" = a ] && echo $apid || echo $bpid) $(date +%s) $P.$1/tasks/$3" > "$P/.agent/team/claims/$2/owner"; }
unclaim() { rm -rf "$P/.agent/team/claims/$1"; }
owner() { cut -d' ' -f1 "$P/.agent/team/claims/$1/owner" 2>/dev/null; }
fns() {  # the definition of one function of agent-loop (one line or a block)
  awk -v n="$1" '!p && $0 ~ "^"n"\\(\\) *\\{" { print; if ($0 ~ /; }[[:space:]]*(#.*)?$/) exit; p = 1; next } p { print } p && /^}/ { exit }' "$D/agent-loop"
}
as() {  # as <agent> <commands> : in that agent's checkout, with its identity and the driver's helpers
  ( cd "$P.$1" && export TEAM_DIR=$P AGENT_ID=$1 HOME TEAM_SPEED; TEAM_SPEED=$([ "$1" = a ] && echo 4 || echo 1)
    [ "$1" = b ] && export TEAM_MAX_TOUCHES=2
    eval "$(for f in status_of task_id handover_of pending_subtasks workable next_task commit_leftovers ask_field asks_with inbox_pending wait_for_human; do fns $f; done)"
    log() { echo "LOG $AGENT_ID $*" >> "$T/log"; }
    . "$D/agent-team-lib"; eval "$2" )
}
killtree() { local c; for c in $(pgrep -P "$1"); do killtree "$c"; done; kill "$1" 2>/dev/null; }   # a watcher runs in nested subshells
ev() { grep -c "\"event\":\"$1\"" "$P/.agent/team/events.jsonl" 2>/dev/null || echo 0; }
bcommit() { git -C "$P.b" add -A && git -C "$P.b" commit -q -m "$1"; }
at_main() { [ "$(git -C "$P.$1" show "HEAD:$2" 2>/dev/null)" = "$(git -C "$P" show "main:$2" 2>/dev/null)" ]; }   # at_main <agent> <path>

# --- own claims first, the highest rank first ---
claim b 253 253-flow.md; claim b 243 243b-fix.md; claim b 270 270-asked.md
[ "$(as b next_task)" = tasks/253-flow.md ] && ok "b holds 253 (rank 3) and 243 (rank 1): it builds 253 first" || bad "b took $(as b next_task) (253 parked behind its lowest-rank-first order)"

# --- the ask ---
mkdir -p "$P.b/.agent/asks"; printf 'status: open\nblocking: yes\ntask: tasks/270-asked.md\n' > "$P.b/.agent/asks/001.md"
facts=$(as a team_takeover_facts)
grep -qP '^253\tb\t1\t4\t3\t\t0$' <<<"$facts" && ok "facts: 253 held by b (speed 1 < 4), rank 3, a could build it" || bad "facts 253: $(grep '^253' <<<"$facts")"
grep -qP '^270\tb\t1\t4\t2\twaits for the human\t0$' <<<"$facts" && ok "facts: 270 waits for the human's answer in b's checkout" || bad "facts 270: $(grep '^270' <<<"$facts")"
grep -qP '^243\tb\t1\t4\t1\t' <<<"$facts" && ok "facts: 243 has rank 1 (nothing waits on it)" || bad "facts 243: $(grep '^243' <<<"$facts")"
[ "$(as a team_takeover_candidate)" = 253 ] && ok "a's candidate: 253" || bad "candidate: $(as a team_takeover_candidate)"
[ -z "$(as b team_takeover_candidate)" ] && ok "b asks a faster agent for nothing" || bad "b's candidate: $(as b team_takeover_candidate)"
[ -z "$(as a 'TEAM_TAKEOVER=0 team_takeover_candidate')" ] && ! as a 'TEAM_TAKEOVER=0 team_takeover_ask' && ok "TEAM_TAKEOVER=0: no takeover" || bad "TEAM_TAKEOVER=0 still asks"
as a team_takeover_ask && [ "$(cut -d' ' -f1 "$P/.agent/team/takeover/253/asker" 2>/dev/null)" = a ] && [ "$(ev takeover_asked)" = 1 ] \
  && ok "a asks for 253 (takeover/253/asker, takeover_asked logged)" || bad "no request: $(ls "$P/.agent/team/takeover" 2>/dev/null)"
as a team_takeover_ask && [ "$(ls "$P/.agent/team/takeover" | wc -l)" = 1 ] && [ "$(ev takeover_asked)" = 1 ] && ok "asked again: the open request stands, no second one" || bad "second request"
[ -z "$(as a team_takeover_candidate)" ] && ok "a claim asked for is no candidate again" || bad "253 asked twice"

# --- b's branch: work on 253 and on 243, some of it shared, some unattributable ---
cd "$P.b"
echo "flow b1" > src/flow.py; sed -i '1s/.*/Status: in-progress/' tasks/253-flow.md; printf '\n## Hand-over\nflow half built\n' >> tasks/253-flow.md
echo "progress" > PROGRESS.md; bcommit "253: step 1 - flow half built"
echo "helper" > src/flow_helper.py; bcommit "[driver] uncommitted work after iteration 7"
echo '{"iter":7,"task":"tasks/253-flow.md"}' >> .agent/iterations.jsonl
echo "feed b" > src/feed.py; sed -i '1s/.*/Status: in-progress/' tasks/243b-fix.md; bcommit "243b: step 1 - feed"
echo "shared 253" > src/shared.py; bcommit "253: shared"; echo "shared 243" >> src/shared.py; bcommit "243b: shared"
echo "unknown" > notes.txt; bcommit "[driver] uncommitted work before syncing with main"
paths=$(cd "$P.b" && team-takeover paths 253 main HEAD .agent/iterations.jsonl 2> "$T/paths.err" | tr '\n' ' ')
[ "$paths" = "src/flow.py src/flow_helper.py tasks/253-flow.md " ] && ok "253's paths: its commits' files (one by the ledger), not PROGRESS.md, shared or unknown ones" || bad "paths: $paths"
grep -q '^shared src/shared.py' "$T/paths.err" && grep -q '^unknown .* uncommitted work before syncing' "$T/paths.err" && ok "shared and unknown reported" || bad "stderr: $(cat "$T/paths.err")"
cd "$P"

# --- the answer between sessions ---
sha=$(git -C "$P.b" rev-parse HEAD)
as b team_takeover_answer
[ "$(owner 253)" = a ] && [ ! -d "$P/.agent/team/takeover/253" ] && [ "$(ev takeover)" = 1 ] && ok "b gives 253 to a (claim owner a, request closed, takeover logged)" || bad "claim: $(cat "$P/.agent/team/claims/253/owner")"
grep -q "^a [0-9]* [0-9]* $P.a/tasks/253-flow.md$" "$P/.agent/team/claims/253/owner" && ok "the claim names a's loop and a's checkout" || bad "owner line: $(cat "$P/.agent/team/claims/253/owner")"
[ "$(head -1 "$P/.agent/team/carry/253")" = "b $sha" ] && ok "carry/253: from b at its branch head" || bad "carry: $(head -1 "$P/.agent/team/carry/253")"
at_main b src/flow.py && at_main b tasks/253-flow.md && ! git -C "$P.b" cat-file -e HEAD:src/flow_helper.py 2>/dev/null \
  && ok "b's branch: 253's files are main's again (src/flow_helper.py gone)" || bad "253 still on b: $(git -C "$P.b" diff --name-only main HEAD | tr '\n' ' ')"
[ "$(git -C "$P.b" show HEAD:src/feed.py)" = "feed b" ] && grep -q 'shared 243' "$P.b/src/shared.py" && [ "$(cat "$P.b/notes.txt")" = unknown ] \
  && ok "b's branch keeps 243's work, the shared file and the unattributed change" || bad "b lost other work"
[ -z "$(git -C "$P.b" status --porcelain)" ] && git -C "$P.b" log -1 --format=%s | grep -q '^\[driver\] 253 taken over by agent a' && ok "b's put-back is one clean driver commit" || bad "b: $(git -C "$P.b" status --porcelain | tr '\n' ' ')"
[ "$(as b 'WAITING=tasks/270-asked.md; next_task')" = tasks/243b-fix.md ] && ok "b goes on with 243b (270 waits for the human)" || bad "b next: $(as b 'WAITING=tasks/270-asked.md; next_task')"

# --- a receives the work ---
as a team_takeover_receive
[ "$(cat "$P.a/src/flow.py")" = "flow b1" ] && [ "$(cat "$P.a/src/flow_helper.py")" = helper ] && grep -q 'flow half built' "$P.a/tasks/253-flow.md" \
  && ok "a's branch has b's work on 253 (code, the helper, the task's hand-over)" || bad "a: flow=$(cat "$P.a/src/flow.py")"
[ "$(cat "$P.a/src/feed.py")" = "feed base" ] && [ "$(cat "$P.a/notes.txt")" = base ] && ok "a got nothing of 243's work" || bad "243 work leaked to a"
git -C "$P.a" log -1 --format=%s | grep -q '^\[driver\] 253 taken over from agent b: its work carried' && [ ! -f "$P/.agent/team/carry/253" ] && [ "$(ev takeover_carried)" = 1 ] \
  && ok "carried in one driver commit; carry/253 removed" || bad "a's commit: $(git -C "$P.a" log -1 --format=%s)"
[ "$(as a next_task)" = tasks/253-flow.md ] && ok "a's next task: 253" || bad "a next: $(as a next_task)"
n=$(as a 'team_takeover_note tasks/253-flow.md'); n2=$(as a 'team_takeover_note tasks/253-flow.md')
[[ $n == " TAKEN OVER: agent b, on a slower card, was building this task;"* ]] && [ -z "$n2" ] && ok "a's first prompt says it was taken over, once" || bad "note: '$n' / '$n2'"
as a team_takeover_ask; [ ! -d "$P/.agent/team/takeover/253" ] && ok "a's request for 253 is done once it holds 253" || bad "stale request"
# the human answers b's request about 253 after (or just before) the takeover: the answer goes to a (frontpage 18:41)
printf '# Request 005\nstatus: answered\nblocking: yes\ntask: tasks/253-flow.md\n\n## Request\nA or B?\n\n## Answer (now, telegram)\nB, as recommended\n' > "$P.b/.agent/asks/005.md"
as b team_takeover_sync
f=$(ls "$P.a/.agent/inbox/"*.md 2>/dev/null | head -1)
[ -n "$f" ] && grep -q '^B, as recommended$' "$f" && grep -q 'agent b filed about task 253' "$f" && grep -qx 'status: closed' "$P.b/.agent/asks/005.md" \
  && grep -qx 'status: open' "$P.b/.agent/asks/001.md" && [ "$(ev takeover_answer_forwarded)" = 1 ] \
  && ok "b's answered request about 253 goes to a's inbox and is closed in b's checkout; b's own open one stays" || bad "answer not forwarded: $(ls "$P.a/.agent/inbox" 2>&1)"
as b team_takeover_sync; [ "$(ls "$P.a/.agent/inbox/"*.md | wc -l)" = 1 ] && ok "forwarded once" || bad "forwarded twice"

# --- during a session on that task: hand over ---
cd "$P"; task 280-crit open none "src/c280.py"; task 281-after open 280 "src/c281.py"; git add -A && git commit -q -m "280, 281"
git -C "$P.a" merge -q --no-edit main > /dev/null; git -C "$P.b" merge -q --no-edit main > /dev/null
claim b 280 280-crit.md
[ "$(as a team_takeover_candidate)" = 280 ] && as a team_takeover_ask && ok "a asks for 280 (rank 2)" || bad "280 candidate: $(as a team_takeover_candidate)"
rm -f "$P.b/.agent/handover-now"
( as b 'TEAM_TAKEOVER_POLL_S=1 team_session_watch tasks/280-crit.md' ) & w=$!
for _ in $(seq 1 8); do [ -f "$P.b/.agent/handover-now" ] && break; sleep 1; done
grep -q 'agent a, on a faster card, has nothing to build and takes this task over' "$P.b/.agent/handover-now" 2>/dev/null && [ "$(owner 280)" = b ] \
  && ok "b's session on 280 is told to hand over (handover-now); the claim stays until it ends" || bad "no cut: $(cat "$P.b/.agent/handover-now" 2>/dev/null)"
rm -f "$P.b/.agent/handover-now"; sleep 2
[ ! -f "$P.b/.agent/handover-now" ] && [ "$(ev takeover_cut)" = 1 ] && ok "told once" || bad "told again"
killtree $w; wait $w 2>/dev/null
echo "c280 b" > "$P.b/src/c280.py"; bcommit "280: hand-over - started"
as b team_takeover_answer; as a team_takeover_receive
[ "$(owner 280)" = a ] && [ "$(cat "$P.a/src/c280.py")" = "c280 b" ] && ! git -C "$P.b" cat-file -e HEAD:src/c280.py 2>/dev/null && ok "after the session: 280 and its work go to a" || bad "280: owner $(owner 280)"

# --- during a session on another task: a parked claim goes at once ---
cd "$P"; task 290-crit open none "src/c290.py"; task 291-after open 290 "src/c291.py"; git add -A && git commit -q -m "290, 291"
git -C "$P.a" merge -q --no-edit main > /dev/null; git -C "$P.b" merge -q --no-edit main > /dev/null
claim b 290 290-crit.md; echo "c290 b" > "$P.b/src/c290.py"; bcommit "290: step 1"
as a team_takeover_ask
( as b 'TEAM_TAKEOVER_POLL_S=1 team_session_watch tasks/243b-fix.md' ) & w=$!
for _ in $(seq 1 8); do [ "$(owner 290)" = a ] && break; sleep 1; done
killtree $w; wait $w 2>/dev/null
[ "$(owner 290)" = a ] && [ ! -f "$P.b/.agent/handover-now" ] && ok "290 parked while b builds 243b: given at once, b's session not cut" || bad "290 owner $(owner 290)"
[ -f "$P.b/.agent/put-back-290" ] && [ "$(cat "$P.b/src/c290.py")" = "c290 b" ] && ok "b's checkout is left alone during the session (put-back-290 pending)" || bad "touched during the session"
as b team_takeover_settle
! git -C "$P.b" cat-file -e HEAD:src/c290.py 2>/dev/null && [ ! -f "$P.b/.agent/put-back-290" ] && [ "$(git -C "$P.b" show HEAD:src/feed.py)" = "feed b" ] \
  && ok "after the session: 290's files leave b's branch, 243's work stays" || bad "settle: $(git -C "$P.b" diff --name-only main HEAD | tr '\n' ' ')"
as a team_takeover_receive; [ "$(cat "$P.a/src/c290.py")" = "c290 b" ] && ok "a gets 290's work" || bad "a's c290: $(cat "$P.a/src/c290.py" 2>/dev/null)"

# --- an asker that is gone ---
cd "$P"; task 300-crit open none "src/c300.py"; task 301-after open 300 "src/c301.py"; git add -A && git commit -q -m "300, 301"
git -C "$P.a" merge -q --no-edit main > /dev/null; git -C "$P.b" merge -q --no-edit main > /dev/null; claim b 300 300-crit.md
mkdir -p "$P/.agent/team/takeover/300"; echo "a 999999 $(date +%s) $P.a" > "$P/.agent/team/takeover/300/asker"
as b team_takeover_answer
[ "$(owner 300)" = b ] && [ ! -d "$P/.agent/team/takeover/300" ] && [ "$(ev takeover_dropped)" = 1 ] && ok "an asker whose loop is gone: request dropped, claim kept" || bad "dead asker: owner $(owner 300)"

# --- the asker's prep is cut when a takeover becomes possible ---
mkdir -p "$P/.agent/team/prep/301"; rm -f "$P.a/.agent/wrapup-now"
( as a 'TEAM_PREP_POLL_S=1 team_prep_watch tasks/301-after.md' ) & w=$!
for _ in $(seq 1 10); do [ -f "$P.a/.agent/wrapup-now" ] && break; sleep 1; done
killtree $w; wait $w 2>/dev/null
grep -q 'task 300, held by slower agent b, can be taken over by you' "$P.a/.agent/wrapup-now" 2>/dev/null && ok "a's prep session is cut: 300 can be taken over (real work beats prep)" || bad "prep not cut: $(cat "$P.a/.agent/wrapup-now" 2>/dev/null)"
rm -rf "$P/.agent/team/prep/301" "$P.a/.agent/wrapup-now"

# --- a hand-over keeps the other claims' work ---
echo "c300 b" > "$P.b/src/c300.py"; bcommit "300: step 1"
mkdir -p "$P/.agent/team/handed"; echo "b $(date +%s) pending" > "$P/.agent/team/handed/243"; unclaim 243
as b team_handover_finish
b243=$(git -C "$P.b" branch --list 'agent/b-handover-243-*' | tr -d ' *')
[ -n "$b243" ] && [ "$(git -C "$P.b" show "$b243:src/feed.py")" = "feed b" ] && ok "243's work is kept on $b243" || bad "no backup branch"
at_main b src/feed.py && at_main b tasks/243b-fix.md && [ "$(git -C "$P.b" show HEAD:src/c300.py)" = "c300 b" ] \
  && ok "hand-over of 243 while b holds 300: 243's files back to main's, 300's work stays" || bad "after hand-over: feed=$(git -C "$P.b" show HEAD:src/feed.py) c300=$(git -C "$P.b" show HEAD:src/c300.py 2>&1)"
grep -q '^b .* kept agent/b-handover-243-' "$P/.agent/team/handed/243" && ok "handed/243 records the backup" || bad "handed: $(cat "$P/.agent/team/handed/243")"
echo "b $(date +%s) pending" > "$P/.agent/team/handed/300"; unclaim 300; unclaim 270
as b team_handover_finish
[ -z "$(git -C "$P.b" diff --name-only main HEAD)" ] && ok "hand-over with no other claim held: the branch is reset to main, as before" || bad "not reset: $(git -C "$P.b" diff --name-only main HEAD | tr '\n' ' ')"

exit $fail
