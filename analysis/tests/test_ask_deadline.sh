#!/usr/bin/env bash
# test_ask_deadline.sh DRIVER_DIR : the request deadline (agent-loop ask_deadline), real loop with stub pi, no model
# (about 3 minutes, ASK_AUTO_MIN=1).
# 1 decide: the only task files a blocking request with a recommendation, nothing else to build -> after the deadline
#   the loop answers it itself (auto answer, status answered then closed), tells the human (ring-doorbell --info),
#   the task goes next with the answer in its prompt, finishes, and the clock file is gone.
# 2 human-only: the same with `human-only: credential` -> still open and waiting well past the deadline.
# 3 off: ASK_AUTO_MIN=0 -> still open and waiting.
set -u
D=$(cd "${1:-$HOME/bin}" && pwd); HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'kill $SRV 2>/dev/null; pkill -f "agent-loop $T/" 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!

setup() {  # setup <case> <human-only value or empty> : home + project; prints nothing
  local H=$T/$1/home P=$T/$1/proj
  mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit" "$P"/{tasks,.stub,.agent}
  for f in agent-loop task-audit codemap-gen decisions-archive pitfalls-sync agent-team-lib; do
    [ -f "$D/$f" ] && cp "$D/$f" "$H/bin/"; done
  cp "$HERE/stubpi" "$H/.npm-global/bin/pi"
  printf '#!/bin/sh\necho "ARGS:$*" >> "%s/doorbell.log"; [ "$1" = --info ] && cat >> "%s/doorbell.log"; echo rang\n' "$T/$1" "$T/$1" > "$H/bin/ring-doorbell"
  chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
  printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
  cd "$P"; git init -q; git config user.email t@t; git config user.name t
  printf '.agent/\n.stub/\n' > .gitignore; printf '# rules\n' > AGENTS.md; printf '# PROGRESS\n' > PROGRESS.md
  printf '# DECISIONS\n' > DECISIONS.md; printf 'Status: open\n# 001: Wait\n\n## Acceptance criteria\n- [ ] decided\n' > tasks/001-wait.md
  git add -A; git commit -qm init
  # session 1: files a blocking request (as ask_human writes it) and hands over; session 2: finishes the task
  cat > .stub/1.sh <<S
mkdir -p .agent/asks
printf '# Request 001\nstatus: open\nblocking: yes\n${2:+human-only: $2\n}task: tasks/001-wait.md\nasked: x\n\n## Request\nPick a or b.\n\n## Recommendation\n(a), because it is simpler.\n' > .agent/asks/001.md
sed -i '1s/.*/Status: in-progress/' tasks/001-wait.md; printf '\n## Hand-over\nwaiting on request 001\n' >> tasks/001-wait.md
git add -A; git commit -qm "001: asked"
S
  cat > .stub/2.sh <<'S'
sed -i '1s/.*/Status: done/; s/^- \[ \] decided/- [x] decided/' tasks/001-wait.md; git add -A; git commit -qm "001: did (a)"
S
}
run() {  # run <case> <ASK_AUTO_MIN> : loop in the background
  ( export HOME=$T/$1/home PATH="$T/$1/home/.npm-global/bin:$T/$1/home/bin:/usr/local/bin:/usr/bin:/bin" \
      TEST_CMD=true LLM_URL=http://127.0.0.1:$MS ASK_AUTO_MIN=$2
    cd "$T/$1/proj" && timeout 400 agent-loop "$T/$1/proj" > "$T/$1/out" 2>&1 ) &
}
setup decide ""; setup human credential; setup off ""
run decide 1; run human 1; run off 0
for i in $(seq 1 40); do grep -q 'queue empty - stopping' "$T/decide/proj/.agent/loop.log" 2>/dev/null && break; sleep 5; done
sleep 10   # human/off: past the deadline by now (60 s + one 30 s wait step)

A=$T/decide/proj/.agent/asks/001.md; L=$T/decide/proj/.agent/loop.log
grep -q '^## Answer (.*auto: no answer after 1 min' "$A" && grep -q 'AUTO-DECIDED request 001' "$A" \
  && ok "decide: the loop answered the request itself" || bad "decide: no auto answer in the request file"
grep -q '^status: closed' "$A" && ok "decide: request closed once a session was pointed at it" || bad "decide: status $(grep ^status "$A")"
grep -q 'decided .agent/asks/001.md on the agent' "$L" && ok "decide: loop log names it" || bad "decide: not in loop log"
grep -q 'ARGS:--info' "$T/decide/doorbell.log" && grep -q 'Decided request(s) 001 myself' "$T/decide/doorbell.log" \
  && ok "decide: human told (ring-doorbell --info)" || bad "decide: no --info notice: $(cat "$T/decide/doorbell.log" 2>/dev/null)"
grep -q 'asks/001.md' "$T/decide/proj/.stub/prompt-2.txt" 2>/dev/null && ok "decide: next session's prompt points at the answer" \
  || bad "decide: session 2 not pointed at the request"
head -1 "$T/decide/proj/tasks/001-wait.md" | grep -q 'Status: done' && grep -q 'queue empty - stopping' "$L" \
  && ok "decide: the task finished and the loop ended" || bad "decide: task $(head -1 "$T/decide/proj/tasks/001-wait.md")"
[ ! -f "$T/decide/proj/.agent/asks-idle-since" ] && ok "decide: clock cleared after real work" || bad "decide: clock file left"

for c in human off; do
  A=$T/$c/proj/.agent/asks/001.md
  if grep -q '^status: open' "$A" && ! grep -q '^## Answer' "$A" && pgrep -f "agent-loop $T/$c/proj" > /dev/null \
     && [ "$(cat "$T/$c/proj/.stub/count")" = 1 ]; then ok "$c: still open and waiting past the deadline"
  else bad "$c: $(grep ^status "$A"), sessions $(cat "$T/$c/proj/.stub/count")"; fi
done
grep -q 'Decided' "$T/human/doorbell.log" && bad "human: an --info notice went out" || ok "human: no auto-decide notice"
exit $fail
