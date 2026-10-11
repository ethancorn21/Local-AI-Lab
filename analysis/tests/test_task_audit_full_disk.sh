#!/usr/bin/env bash
# test_task_audit_full_disk.sh [DRIVER_DIR] : a write that fails leaves task-audit's registry as it was. ancient-works
# 2026-10-11: the disk filled, and both agents' .agent/tasks.json became empty files (who owns which acceptance boxes,
# gone; every later call crashed). A full disk is a file-size limit here (ulimit -f 0: a write fails, as on ENOSPC).
set -u
D=${1:-$(cd "$(dirname "$0")/../../harness/driver" && pwd)}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
mkdir -p "$T/p/tasks" "$T/p/.agent"; cd "$T/p"
printf 'Status: open\n# 001: the human task\n\n## Acceptance criteria\n- [ ] it works\n' > tasks/001-human.md
python3 "$D/task-audit" init > /dev/null
before=$(cat .agent/tasks.json)
[ -n "$before" ] && grep -q '"human"' .agent/tasks.json && ok "init registered the human's task" || bad "init: '$before'"
printf 'Status: open\n# 002: a new task\n\n## Acceptance criteria\n- [ ] more\n' > tasks/002-new.md
( ulimit -f 0; python3 "$D/task-audit" scan > /dev/null 2>&1 )
[ "$(cat .agent/tasks.json)" = "$before" ] && ok "a scan that cannot write leaves the registry as it was" || bad "registry after the failed write: $(wc -c < .agent/tasks.json) bytes"
python3 "$D/task-audit" owner tasks/001-human.md 2>/dev/null | grep -qx human && ok "the human still owns 001" || bad "owner of 001: $(python3 "$D/task-audit" owner tasks/001-human.md 2>&1 | tail -1)"
python3 "$D/task-audit" scan | grep -q "NEW TASK by agent: tasks/002-new.md" && ok "the next scan with room records 002" || bad "002 not recorded"
exit $fail
