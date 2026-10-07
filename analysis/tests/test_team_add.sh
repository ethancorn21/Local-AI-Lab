#!/usr/bin/env bash
# test_team_add.sh [AGENT_TEAM] : `agent-team add` puts a new agent into an existing team (a third GPU, 2026-10-06).
# The new checkout is a worktree of main on branch agent/<id> with its team.env, and its task registry is the union of
# the other agents' registries, a task any of them holds as the human's staying the human's. A fresh `task-audit init`
# there would have made every agent-created task the human's.
set -u
TEAMCMD=${1:-$HOME/bin/agent-team}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
export HOME=$T/home GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
P=$HOME/projects/p; mkdir -p "$P/tasks" "$P/.agent/team/agents" "$P/.agent/team/loops" "$HOME/.agent-kit/agents"
git -C "$P" init -q -b main
for t in 000-plan 101-x 102-y 103-z; do echo "- [ ] $t" > "$P/tasks/$t.md"; done
git -C "$P" add tasks && git -C "$P" commit -q -m tasks
touch "$P/.agent/team-main"
reg() { python3 -c 'import json,sys; json.dump({k: {"owner": v} for k, v in (a.split("=") for a in sys.argv[2:])}, open(sys.argv[1], "w"))' "$@"; }
for id in a b; do
  git -C "$P" worktree add -q -b "agent/$id" "$HOME/projects/p.$id" main; mkdir -p "$HOME/projects/p.$id/.agent"
  echo "$HOME/projects/p.$id" > "$P/.agent/team/agents/$id"
done
reg "$HOME/projects/p.a/.agent/tasks.json" 000=human 101=agent 102=agent
reg "$HOME/projects/p.b/.agent/tasks.json" 000=human 101=agent 102=human 103=agent
printf '# agent c: a comment that stays out of team.env\nLLM_URL=http://127.0.0.1:8081\nTEAM_SPEED=4\n' > "$HOME/.agent-kit/agents/c.env"

out=$(bash "$TEAMCMD" add p c 2>&1); rc=$?
C=$HOME/projects/p.c
[ $rc -eq 0 ] && ok "add finished (rc 0)" || bad "add rc $rc: $out"
[ "$(git -C "$C" branch --show-current 2>/dev/null)" = agent/c ] && [ "$(git -C "$C" rev-parse HEAD)" = "$(git -C "$P" rev-parse main)" ] \
  && ok "worktree on agent/c at main" || bad "worktree: $(git -C "$C" branch --show-current 2>&1)"
grep -qx "TEAM_DIR=$P" "$C/.agent/team.env" && grep -qx "AGENT_ID=c" "$C/.agent/team.env" \
  && grep -qx "LLM_URL=http://127.0.0.1:8081" "$C/.agent/team.env" && ! grep -q "stays out" "$C/.agent/team.env" \
  && ok "team.env: team dir, id, settings, no comments from c.env" || bad "team.env: $(cat "$C/.agent/team.env" 2>&1)"
owners=$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(" ".join(k + "=" + r[k]["owner"] for k in sorted(r)))' "$C/.agent/tasks.json" 2>&1)
[ "$owners" = "000=human 101=agent 102=human 103=agent" ] && ok "registry: union, human wins ($owners)" || bad "registry: $owners"
[ "$(cat "$P/.agent/team/agents/c" 2>/dev/null)" = "$C" ] && ok "registered as a team agent" || bad "agents/c missing"
echo "$out" | grep -q "4 tasks registered" && ok "reports the registered tasks" || bad "output: $out"

before=$(md5sum "$C/.agent/tasks.json")
out=$(bash "$TEAMCMD" add p c 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(md5sum "$C/.agent/tasks.json")" = "$before" ] && ok "adding c again: refused, nothing changed" || bad "re-add: rc $rc $out"
out=$(bash "$TEAMCMD" add p d 2>&1); rc=$?
[ $rc -ne 0 ] && [ ! -e "$HOME/projects/p.d" ] && echo "$out" | grep -q "missing .*d.env" \
  && ok "agent without an env file: refused, no worktree" || bad "no env: rc $rc $out"
mkdir -p "$HOME/projects/solo"
out=$(bash "$TEAMCMD" add solo c 2>&1); rc=$?
[ $rc -ne 0 ] && echo "$out" | grep -q "not a team project" && ok "not a team project: refused" || bad "solo: rc $rc $out"
exit $fail
