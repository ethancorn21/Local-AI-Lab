#!/usr/bin/env bash
# test_doorbell_off.sh [TOOLS_DIR] : with ~/.agent-kit/doorbell-off, ring-doorbell sends nothing (harness/tools).
# The owner turned Telegram off on 2026-10-11 ("i dont check telegram"): ancient-works request 001 went out at 22:37
# and sat 2.5 h unseen. Checked: an ask, a ping and an --info notice all exit 0 without opening the relay, and say
# where the request is; without the file the relay is still used.
set -u
D=${1:-$(cd "$(dirname "$0")/../../harness/tools" && pwd)}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
mkdir -p "$T/bin" "$T/home/.agent-kit" "$T/p/.agent/asks"
printf '#!/bin/sh\necho "ssh $*" >> "%s/ssh.log"; cat > /dev/null; echo "{\\"ok\\": true, \\"line\\": \\"sent\\"}"\n' "$T" > "$T/bin/ssh"; chmod +x "$T/bin/ssh"
cd "$T/p" && git init -q
run() { HOME=$T/home PATH="$T/bin:$PATH" python3 "$D/ring-doorbell" "$@"; }

touch "$T/home/.agent-kit/doorbell-off"
out=$(echo "may I?" | run --ask 001); rc=$?
[ $rc = 0 ] && [ "$out" = "Telegram off: the request is in the human's inbox" ] && ok "an ask: nothing sent, rc 0" || bad "an ask: rc=$rc out='$out'"
out=$(run); rc=$?
[ $rc = 0 ] && ok "a ping: rc 0" || bad "a ping: rc=$rc out='$out'"
out=$(echo "decided 001" | run --info); rc=$?
[ $rc = 0 ] && ok "an --info notice: rc 0" || bad "an --info notice: rc=$rc out='$out'"
[ ! -s "$T/ssh.log" ] && ok "the relay was never opened" || bad "the relay was opened: $(cat "$T/ssh.log")"

rm "$T/home/.agent-kit/doorbell-off"
echo "may I?" | run --ask 002 > /dev/null
[ -s "$T/ssh.log" ] && ok "without the file the relay is used" || bad "without the file nothing was sent"
exit $fail
