#!/usr/bin/env bash
# test_gpu_lease.sh DRIVER_DIR : one project per GPU (agent-loop take_gpu), real loops with stub pi, no model
# (about 1.5 minutes). GPUs are stood in for by two stub model servers (two LLM_URLs).
# 1 takeover: A works on GPU 1 (20 s sessions); B starts on GPU 1 -> A is paused, finishes its running session (its
#   work is committed, not killed), exits; B's first session starts only after that.
# 2 other GPU: C on GPU 2 runs the whole time, never paused, never mentioned.
# 3 stale lease: a lease left by a dead pid on GPU 2 does not hold C up and is removed.
# Agents of one team sharing a GPU are covered by test_team.sh (both stub agents use one model server).
set -u
D=$(cd "${1:-$HOME/bin}" && pwd); HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'kill $S1 $S2 2>/dev/null; pkill -f "agent-loop $T/" 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
srv() { local port; port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
  mkdir -p "$T/srv$1/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv$1/v1/models"
  python3 -m http.server --bind 127.0.0.1 "$port" --directory "$T/srv$1" > /dev/null 2>&1 & echo "$port $!"; }
read -r P1 S1 < <(srv 1); read -r P2 S2 < <(srv 2)
H=$T/home; mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit"
for f in agent-loop task-audit codemap-gen decisions-archive pitfalls-sync; do [ -f "$D/$f" ] && cp "$D/$f" "$H/bin/"; done
cp "$HERE/stubpi" "$H/.npm-global/bin/pi"; chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"

proj() {  # proj <name> <tasks> : every session sleeps 20 s, records its start, finishes its task
  local P=$T/$1 i; mkdir -p "$P"/{tasks,.stub,.agent}; cd "$P"; git init -q; git config user.email t@t; git config user.name t
  printf '.agent/\n.stub/\n' > .gitignore; printf '# rules\n' > AGENTS.md; printf '# P\n' > PROGRESS.md; printf '# D\n' > DECISIONS.md
  for i in $(seq 1 "$2"); do
    printf 'Status: open\n# 00%s: t\n\n## Acceptance criteria\n- [ ] done\n' "$i" > "tasks/00$i-t.md"
    printf 'date +%%s >> .stub/starts; sleep 20\nsed -i "1s/.*/Status: done/; s/^- \\[ \\]/- [x]/" "$TASK"; git add -A; git commit -qm "$TASK done"\n' > ".stub/$i.sh"
  done
  git add -A; git commit -qm init
}
run() {  # run <name> <port>
  ( export HOME=$H PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin" TEST_CMD=true LLM_URL=http://127.0.0.1:$2
    cd "$T/$1" && timeout 200 agent-loop "$T/$1" > "$T/$1.out" 2>&1 ) &
}
proj A 3; proj B 1; proj C 3
mkdir -p "$H/.agent-kit/gpu/127.0.0.1_$P2"; printf '/gone\n/gone\n1\n' > "$H/.agent-kit/gpu/127.0.0.1_$P2/999999"
run A "$P1"; run C "$P2"
for i in $(seq 1 30); do [ -s "$T/A/.stub/starts" ] && break; sleep 1; done
run B "$P1"
for i in $(seq 1 40); do grep -q 'queue empty - stopping' "$T/B/.agent/loop.log" 2>/dev/null && break; sleep 3; done

LA=$T/A/.agent/loop.log; LB=$T/B/.agent/loop.log; LC=$T/C/.agent/loop.log
grep -q "the loop of $T/A (pid [0-9]*) was using it - paused it" "$LB" && ok "takeover: B paused A" || bad "takeover: B did not pause A: $(grep GPU "$LB")"
grep -q 'PAUSE file found - stopping before iteration 2' "$LA" && ! pgrep -f "agent-loop $T/A" > /dev/null \
  && ok "takeover: A stopped after its running session" || bad "takeover: A $(tail -n 1 "$LA")"
[ "$(wc -l < "$T/A/.stub/starts")" = 1 ] && head -1 "$T/A/tasks/001-t.md" | grep -q 'Status: done' && grep -q '001-t.md done' <(git -C "$T/A" log --format=%s) \
  && ok "takeover: A's running session finished and committed (not killed)" || bad "takeover: A sessions $(wc -l < "$T/A/.stub/starts")"
astop=$(date -d "$(grep 'PAUSE file found - stopping' "$LA" | cut -c1-19)" +%s 2>/dev/null || echo 0); bstart=$(head -1 "$T/B/.stub/starts" 2>/dev/null || echo 0)
[ "$bstart" -ge "$astop" ] && [ "$astop" -gt 0 ] && ok "takeover: B's first session started after A exited (+$((bstart - astop)) s)" \
  || bad "takeover: B started $bstart, A stopped $astop"
grep -q 'GPU http://127.0.0.1:[0-9]*: free after' "$LB" && head -1 "$T/B/tasks/001-t.md" | grep -q 'Status: done' \
  && ok "takeover: B then did its task" || bad "takeover: B $(tail -n 2 "$LB")"
[ ! -f "$T/B/.agent/PAUSE" ] && ok "takeover: B itself not paused" || bad "takeover: B has a PAUSE file"
! grep -q 'PAUSE\|GPU ' "$LC" && pgrep -f "agent-loop $T/C" > /dev/null && [ "$(wc -l < "$T/C/.stub/starts")" -ge 2 ] \
  && ok "other GPU: C kept working, untouched" || bad "other GPU: C $(grep -c . "$T/C/.stub/starts") sessions, $(grep 'PAUSE\|GPU ' "$LC")"
[ ! -f "$H/.agent-kit/gpu/127.0.0.1_$P2/999999" ] && [ "$(head -1 "$T/C/.stub/starts")" -le "$(head -1 "$T/A/.stub/starts")" ] \
  && ok "stale lease: removed, C did not wait" || bad "stale lease: still there or C waited"
exit $fail
