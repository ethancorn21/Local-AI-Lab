#!/usr/bin/env bash
# Two simulated agents, two branches, one merge - once per design. Prints conflicts and whether every note survived.
set -u
design=$1; d=$(mktemp -d); cd "$d"; git init -q -b main; git config user.email t@t; git config user.name t
printf '# Progress\nnext: start\n' > PROGRESS.md; printf '# Decisions\n' > DECISIONS.md
mkdir tasks; printf 'Status: open\n# 101: alpha\n' > tasks/101-alpha.md; printf 'Status: open\n# 102: beta\n' > tasks/102-beta.md
[ "$design" = new ] && echo 'DECISIONS.md merge=union' > .gitattributes
git add -A; git commit -qm base
agent() {  # agent <branch> <task file> <label> <sessions>
  git checkout -q -b "$1" main
  for s in $(seq 1 "$4"); do
    printf '\n## 2026-09-28 %s DECISION: %s choice %s\nwhy %s.\n' "$3" "$3" "$s" "$s" >> DECISIONS.md
    if [ "$design" = old ]; then printf '# Progress\nagent %s session %s: next = step %s of %s\n' "$3" "$s" "$((s + 1))" "$3" > PROGRESS.md
    else python3 - "$2" "$3" "$s" <<'PY'
import sys, re
f, label, s = sys.argv[1:]; t = open(f).read()
note = f"## Hand-over\nagent {label} session {s}: next = step {int(s)+1} of {label}\n"
t = re.sub(r"(?ms)^## Hand-over\n.*\Z", "", t).rstrip("\n") + "\n\n" + note
open(f, "w").write(t)
PY
    fi
    git add -A; git commit -qm "$3 session $s"
  done
}
agent agent-a tasks/101-alpha.md 101 3; agent agent-b tasks/102-beta.md 102 3
git checkout -q main; git merge -q --no-edit agent-a >/dev/null 2>&1
if git merge -q --no-edit agent-b >/dev/null 2>&1; then echo "$design design: second merge CLEAN"; else echo "$design design: second merge CONFLICT in: $(git diff --name-only --diff-filter=U | tr '\n' ' ')"; git merge --abort; fi
# which notes survive on main after the merges (conflicts resolved by nobody = merge aborted)
echo "  decisions entries on main: $(grep -c '^## ' DECISIONS.md) of 6"
if [ "$design" = old ]; then echo "  hand-over notes on main: $(grep -c 'agent 10[12] session 3' PROGRESS.md) of 2 (PROGRESS.md holds one snapshot)"
else echo "  hand-over notes on main: $(cat tasks/*.md | grep -c 'session 3: next') of 2 (one per task file)"; fi
rm -rf "$d"
