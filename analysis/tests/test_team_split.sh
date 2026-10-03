#!/usr/bin/env bash
# test_team_split.sh [DRIVER_DIR] : a task bigger than another running agent's TEAM_MAX_TOUCHES is split by the agent
# that takes it, and the split's task files go into main at once (frontpage 2026-10-03: a 19-file task headed the
# sprint's chain; the 8-file agent sat idle, and a claim covers subtasks, so only new top-level tasks can help it).
# Real git repo and worktrees; agent a and agent b are this shell's functions run with different AGENT_IDs.
set -u
D=${1:-$HOME/bin}
T=$(mktemp -d); trap 'kill $bpid 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
export HOME=$T/home; mkdir -p "$HOME/.agent-kit/agents"
printf 'LLM_URL=x\n' > "$HOME/.agent-kit/agents/a.env"
printf 'LLM_URL=y\nTEAM_MAX_TOUCHES=8\n' > "$HOME/.agent-kit/agents/b.env"
P=$T/proj; mkdir -p "$P/tasks"; cd "$P"
git init -q -b main && git config user.email t@t && git config user.name t && echo ".agent/" > .gitignore
many=$(for i in $(seq 1 19); do printf 'src/m%02d.py, ' "$i"; done | sed 's/, $//')
task() { printf 'Status: %s\n# %s\n\nDepends on: %s\nTouches: %s\n\n## Acceptance criteria\n- [ ] works\n' "$2" "$1" "$3" "$4" > "tasks/$1.md"; }
task 216-done done none "src/a.py"; task 218-big open none "$many"; task 219-next open 218 "src/n.py"
task 220-other open none "src/o.py"; task 225-bs open none "src/b.py"
task 230-small open none "src/s1.py, src/s2.py, src/s3.py, src/s4.py, src/s5.py, src/s6.py, src/s7.py, src/s8.py, src/s9.py"
printf 'Status: open\n# 000\n\nTouches: %s\n' "$many" > tasks/000-plan.md
git add -A && git commit -q -m init
git worktree add -q "$P.a" -b agent/a && git worktree add -q "$P.b" -b agent/b
mkdir -p "$P/.agent/team/claims" "$P/.agent/team/loops"
sleep 300 & bpid=$!
echo $$ > "$P/.agent/team/loops/a.pid"; echo $bpid > "$P/.agent/team/loops/b.pid"
claim() { mkdir -p "$P/.agent/team/claims/$2"; echo "$1 $([ "$1" = a ] && echo $$ || echo $bpid) $(date +%s) $P.$1/tasks/$3" > "$P/.agent/team/claims/$2/owner"; }
claim a 218 218-big.md; claim b 225 225-bs.md
as() {  # as <agent> <dir> <commands> : run with that agent's identity and the driver's helpers
  ( cd "$2" && export TEAM_DIR=$P AGENT_ID=$1 HOME; [ "$1" = b ] && export TEAM_MAX_TOUCHES=8
    eval "$(sed -n '/^status_of()/p; /^task_id()/p' "$D/agent-loop")"; log() { echo "LOG $*" >> "$T/log"; }
    . "$D/agent-team-lib"; eval "$3" )
}
note=$(as a "$P.a" 'team_split_note tasks/218-big.md')
[[ $note == *"it changes 19 files"* && $note == *"at most 8 files"* && $note == *"200-499"* ]] \
  && ok "19-file task, b (8 files) running: a is told to split it, in its own number range" || bad "note: ${note:0:120}"
[ -z "$(as a "$P.a" 'team_split_note tasks/216-done.md')" ] && ok "small task: no split note" || bad "small task got a note"
[ -n "$(as a "$P.a" 'team_split_note tasks/230-small.md')" ] && ok "9 files > 8: note" || bad "9-file task got no note"
[ -z "$(as a "$P.a" 'team_split_note tasks/000-plan.md')" ] && ok "planning task never split" || bad "000 got a note"
[ -z "$(as b "$P.b" 'team_split_note tasks/218-big.md')" ] && ok "agent b: a has no limit, no note" || bad "b got a note"
cp tasks/218-big.md "$T/keep"; sed -i 's/^Touches:.*/&\nSplit: no - one schema change/' "$P.a/tasks/218-big.md"
[ -z "$(as a "$P.a" 'team_split_note tasks/218-big.md')" ] && ok "Split: no opts out" || bad "Split: no ignored"
cp "$T/keep" "$P.a/tasks/218-big.md"
kill $bpid; wait $bpid 2>/dev/null
[ -z "$(as a "$P.a" 'team_split_note tasks/218-big.md')" ] && ok "b not running: no note" || bad "note while b is stopped"
sleep 300 & bpid=$!; echo $bpid > "$P/.agent/team/loops/b.pid"; claim b 225 225-bs.md

# a's split session: parts 223 (free) and 224 (after 223); 218 waits for both and keeps 2 files; 219 gains 224;
# a also touches 220 (which main changed meanwhile) and b's claimed 225: both must stay as main/b have them
cd "$P.a"; printf "\n## Hand-over\nbox 1 built\n" >> tasks/218-big.md && git commit -qam "218: hand-over"   # main's 218 is now older
head0=$(git rev-parse HEAD)
task 223-part-one open none "src/m01.py, src/m02.py, src/m03.py, src/m04.py, src/m05.py, src/m06.py"
task 224-part-two open 223 "src/m07.py, src/m08.py, src/m09.py, src/m10.py, src/m11.py"
sed -i 's/^Depends on:.*/Depends on: 223, 224/; s/^Touches:.*/Touches: src\/m18.py, src\/m19.py/' tasks/218-big.md
sed -i 's/^Depends on:.*/Depends on: 218, 224/' tasks/219-next.md
echo "a's edit" >> tasks/220-other.md; echo "a's edit" >> tasks/225-bs.md
git add -A && git commit -q -m "218: split into 223, 224"
( cd "$P" && echo "main's edit" >> tasks/220-other.md && git commit -qam "main changed 220" )
as a "$P.a" "team_publish_split tasks/218-big.md $head0"
cd "$P"
[ -f tasks/223-part-one.md ] && [ -f tasks/224-part-two.md ] && ok "the parts are in main" || bad "parts not in main"
grep -q '^Depends on: 223, 224' tasks/218-big.md && grep -q '^Touches: src/m18.py, src/m19.py' tasks/218-big.md \
  && ok "the split task's new lines are in main (own task, though a had changed it before)" || bad "218 in main: $(grep -E '^(Dep|Tou)' tasks/218-big.md | tr '\n' ' ')"
grep -q '^Depends on: 218, 224' tasks/219-next.md && ok "the Depends on a dependent task gained is in main" || bad "219 not published"
grep -q "main's edit" tasks/220-other.md && ! grep -q "a's edit" tasks/220-other.md && ok "a task main changed meanwhile is left alone" || bad "220 overwritten"
! grep -q "a's edit" tasks/225-bs.md && ok "a task another agent holds is left alone" || bad "225 (b's) overwritten"
! git ls-files --error-unmatch src > /dev/null 2>&1 && ok "no code went into main" || bad "code in main"
[ -z "$(git status --porcelain)" ] && git log -1 --format=%s | grep -q '^\[driver\] 218 split by agent a' \
  && ok "main is clean, one driver commit" || bad "main: $(git status --porcelain | head -3) / $(git log -1 --format=%s)"
git -C "$P.b" merge -q --no-edit main > /dev/null 2>&1
[ -z "$(as b "$P.b" 'team_why_not tasks/223-part-one.md')" ] && ok "b can take part 223 now" || bad "223 for b: $(as b "$P.b" 'team_why_not tasks/223-part-one.md')"
[[ $(as b "$P.b" 'team_why_not tasks/224-part-two.md') == "waits for 223" ]] && ok "224 waits for 223" || bad "224 for b: $(as b "$P.b" 'team_why_not tasks/224-part-two.md')"
[[ $(as a "$P.a" 'team_why_not tasks/218-big.md') == "waits for 223,224" ]] && ok "a's 218 waits for its parts (claim kept)" || bad "218 for a: $(as a "$P.a" 'team_why_not tasks/218-big.md')"
[ -z "$(as a "$P.a" 'team_split_note tasks/218-big.md')" ] && ok "after the split: no more split note" || bad "note after split"
[ -d "$P/.agent/team/claims/218" ] && ok "a still holds 218" || bad "218 claim lost"
cd "$P.a"; head1=$(git rev-parse HEAD)
as a "$P.a" "team_publish_split tasks/218-big.md $head1"
grep -q 'no task file changed - nothing published' "$T/log" && ok "a session that changed no task file publishes nothing" || bad "empty split not logged"
exit $fail
