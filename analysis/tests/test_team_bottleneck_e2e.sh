#!/usr/bin/env bash
# test_team_bottleneck_e2e.sh [DRIVER_DIR] : idle agents attack the bottleneck, end to end: two stub agents, real loops
# (about 3-6 minutes). frontpage 2026-10-07: agent a built 234 for 90 minutes while c and b had nothing to build, and
# 236 waited for parts (259-262) its split had named but never written. Run on the VM.
#   001  one agent builds it for ~90 s (Stub-sleep); its second box can go to another agent (Stub-carve): the idle agent
#        carves it out as a part while the holder works, builds the part, and the holder leaves that box and file alone
#        (the driver's message in its inbox); after its session the box shows as moved; 002, which waited for 001, waits
#        for the part too
#   003  names a part, 009, that has no task file: the agent that takes 003 writes 009 first; both get built
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVER=${1:-$HERE/../../harness/driver}
T=$(mktemp -d); trap 'kill $SRV 2>/dev/null; rm -rf "$T"' EXIT
H=$T/home; PR=$H/projects; mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit/agents" "$PR"
for f in agent-loop agent-team-lib agent-team task-audit codemap-gen decisions-archive plan-schedule pitfalls-sync team-takeover team-carve; do [ -f "$DRIVER/$f" ] && cp "$DRIVER/$f" "$H/bin/"; done
for f in agent-start agent-stop; do sudo cat "/home/agent/bin/$f" > "$H/bin/$f"; done
cp "$HERE/stubpi-team" "$H/.npm-global/bin/pi"
printf 'cat >> %s/doorbell.txt; echo --- >> %s/doorbell.txt; echo sent\n' "$T" "$T" > "$H/bin/ring-doorbell"
chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
sudo cp -r /home/agent/.agent-kit/template "$H/.agent-kit/" && sudo chown -R "$(id -u)" "$H/.agent-kit/template"
printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
: > "$H/.agent-kit/agents/a.env"; : > "$H/.agent-kit/agents/b.env"
FX=$T/fixtures; mkdir -p "$FX"
printf 'Status: open\n# 001-big\n\nDepends on: none\nTouches: src/big/, src/legend/\nStub-sleep: 90\nStub-carve: 2 src/legend/\n\n## Acceptance criteria\n- [ ] the big part\n- [ ] the legend\n' > "$FX/001-big.md"
printf 'Status: open\n# 002-after\n\nDepends on: 001\nTouches: src/after/\n\n## Acceptance criteria\n- [ ] done\n' > "$FX/002-after.md"
printf 'Status: open\n# 003-parent\n\nDepends on: 009\nTouches: src/parent/\n\n## Acceptance criteria\n- [ ] done\n' > "$FX/003-parent.md"
export HOME=$H STUB_FIXTURES=$FX STUB_SLEEP=3 TEAM_WAIT_S=3 TEAM_PREP_POLL_S=3 TEAM_TAKEOVER_POLL_S=3 TEST_CMD=true NO_PROGRESS_LIMIT=20
export PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin"
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!
export LLM_URL=http://127.0.0.1:$MS
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
EV=$PR/bn/.agent/team/events.jsonl
evj() { grep "\"event\":\"$1\"" "$EV" 2>/dev/null; }
running() { pgrep -u "$(id -u)" -f "agent-loop $PR/$1" > /dev/null; }
mkdir -p "$PR/bn"; echo "# Test goal" > "$PR/bn/GOAL.md"; agent-team init bn a b > /dev/null && agent-team start bn > /dev/null
for _ in $(seq 1 100); do sleep 5; [ -f "$PR/bn/.agent/team/STOP" ] && ! running bn && break; done
agent-team status bn | sed 's/^/     /' | head -14
[ -f "$PR/bn/.agent/team/STOP" ] && ! running bn && ok "finished (goal check ran, both loops stopped)" || { bad "did not finish"; agent-team stop bn --now > /dev/null; }
nd=$(for f in "$PR/bn"/tasks/[0-9]*.md; do head -1 "$f"; done | grep -vc 'Status: done'); [ "$nd" -eq 0 ] && ok "every task done in main" || bad "$nd task(s) not done in main"

# --- the carve ---
holder=$(evj claim | grep '/001-big' | head -1 | jq -r .agent)
pub=$(evj carve_published | head -1)
carver=$(jq -r .agent <<<"$pub" 2>/dev/null); part=$(jq -r .detail <<<"$pub" 2>/dev/null | sed -n 's/^parts \([0-9]*\);.*/\1/p')
[ -n "$part" ] && [ -n "$holder" ] && [ "$carver" != "$holder" ] && ok "agent $carver carved part $part out of 001 while agent $holder built it" || bad "carve: holder '$holder', published '$pub'"
pt=$(jq -r .time <<<"$pub" 2>/dev/null); mt=$(evj merge | grep '/001-big' | head -1 | jq -r .time)
[ -n "$pt" ] && [ -n "$mt" ] && [[ $pt < $mt ]] && ok "the part was published ($pt) before 001 merged ($mt)" || bad "times: published '$pt', 001 merged '$mt'"
pf=$(compgen -G "$PR/bn/tasks/$part-*.md" | head -1)
[ -n "$pf" ] && [ "$(head -1 "$pf")" = "Status: done" ] && grep -qx 'Carved from: 001' "$pf" && ok "part $part is done in main (Carved from: 001)" || bad "part file: ${pf:-none}"
pm=$(evj merge | grep "/$part-" | head -1 | jq -r .agent); [ "$pm" = "$carver" ] && ok "the carver built the part" || bad "part merged by '$pm'"
lg=$(cat "$PR/bn/src/legend/"* 2>/dev/null)
[ -n "$lg" ] && ! grep -q '^001-big' <<<"$lg" && grep -q "^$part-carved by agent $carver" <<<"$lg" && ok "src/legend/ was written by the part only (the holder left it alone)" || bad "src/legend: $lg"
grep -qx -- "- \[moved to $part\] the legend" "$PR/bn/tasks/001-big.md" && grep -qx 'Touches: src/big/' "$PR/bn/tasks/001-big.md" \
  && ok "001 in main: box 2 moved to $part, src/legend/ off its Touches" || bad "001: $(grep -E '^- |^Touches' "$PR/bn/tasks/001-big.md")"
grep -q "^Depends on: 001, $part$" "$PR/bn/tasks/002-after.md" && ok "002 waited for the part too" || bad "002: $(grep '^Depends' "$PR/bn/tasks/002-after.md")"
s2=$(evj claim | grep '/002-after' | head -1 | jq -r .time); pm2=$(evj merge | grep "/$part-" | head -1 | jq -r .time)
[ -n "$s2" ] && ! [[ $s2 < $pm2 ]] && ! [[ $s2 < $mt ]] && ok "002 started after both 001 and the part were in main" || bad "002 at '$s2' (001 '$mt', part '$pm2')"
[ "$(evj carve_conflict | grep -c .)" = 0 ] && ok "no conflict (the holder did not tick the moved box)" || bad "conflict: $(evj carve_conflict)"
grep -q 'CARVED: while you worked' "$PR/bn.$holder/.agent/stub-prompts.txt" 2>/dev/null || [ "$(evj claim | grep -c '/001-big')" = 1 ] \
  && ok "(001 took one session: no CARVED note needed, or the next prompt had it)" || bad "a later 001 session got no CARVED note"

# --- the parts never written ---
pmiss=$(evj parts_missing | head -1)
[ "$(jq -r .detail <<<"$pmiss")" = 009 ] && ok "003's session was told to write 009 first ($(jq -r .agent <<<"$pmiss"))" || bad "parts_missing: '$pmiss'"
[ "$(head -1 "$(compgen -G "$PR/bn/tasks/009-*.md" | head -1)" 2>/dev/null)" = "Status: done" ] && [ -e "$PR/bn/src/part009" ] && [ -e "$PR/bn/src/parent" ] \
  && ok "009 was written, built and merged; then 003" || bad "009/003: $(ls "$PR/bn/tasks" "$PR/bn/src" | tr '\n' ' ')"

echo; [ "$fail" = 0 ] && echo "ALL OK" || echo "SOME CHECKS FAILED"
exit "$fail"
