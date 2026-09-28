#!/usr/bin/env bash
# builds the test project in $1 and the scenario
set -e
rm -rf "$1"; mkdir -p "$1"/{tasks,src,.stub,.agent}; cd "$1"; git init -q; git config user.email t@t; git config user.name t
printf '.agent/\n.stub/\n' > .gitignore
printf '# rules\n' > AGENTS.md; printf '# PROGRESS\n' > PROGRESS.md; printf '# DECISIONS\n' > DECISIONS.md
printf 'Status: open\n# 001: Alpha\n\n## Acceptance criteria\n- [ ] alpha part one works\n- [ ] alpha part two works\n' > tasks/001-alpha.md
printf 'Status: open\n# 002: Beta\n\n## Acceptance criteria\n- [ ] beta works\n' > tasks/002-beta.md
printf '// Module a: does the a things. More detail here.\nexport function doA() {}\nexport const K = 1;\n' > src/a.js
cat > fake-test.sh <<'T'
i=0; fail=0; while IFS= read -r t; do [ -z "$t" ] && continue; i=$((i+1)); echo "not ok $i - $t"; fail=1; done < failing.txt
echo "ok 99 - always fine"; exit $fail
T
echo "old red" > failing.txt
codemap-gen --init . || [ $? -eq 3 ]; git add -A; git commit -qm init
# --- scenario: one file per agent session ---
cat > .stub/1.sh <<'S'
# splits 001 into 001a/001b but FORGETS to set the parent to split (leaves it in-progress)
sed -i '1s/.*/Status: in-progress/' tasks/001-alpha.md
printf 'Status: open\n# 001a: alpha part one\n- [ ] part one done\n' > tasks/001a-part-one.md
printf 'Status: open\n# 001b: alpha part two\n- [ ] part two done\n' > tasks/001b-part-two.md
printf '\n## 2026-09-27 001 DECISION: split alpha in two\nwhy.\n' >> DECISIONS.md
git add -A; git commit -qm "001: split"
S
cat > .stub/2.sh <<'S'
[ "$TASK" = tasks/001a-part-one.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
sed -i 's/- \[ \]/- [x]/; 1s/.*/Status: done/' "$TASK"
printf '\n## 2026-09-27 001a DECISION: did part one\nhow.\n' >> DECISIONS.md
git add -A; git commit -qm "001a: done"
S
cat > .stub/3.sh <<'S'
[ "$TASK" = tasks/001b-part-two.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
echo "new red" >> failing.txt
sed -i 's/- \[ \]/- [x]/; 1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "001b: done (breaks a test)"
S
cat > .stub/4.sh <<'S'
[ "$TASK" = tasks/001b-part-two.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
sed -i '/new red/d' failing.txt; sed -i '1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "001b: fixed"
S
cat > .stub/5.sh <<'S'
[ "$TASK" = tasks/001-alpha.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
sed -i '/alpha part two/d; s/- \[ \]/- [x]/; 1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "001: done (drops a criterion)"
S
cat > .stub/6.sh <<'S'
[ "$TASK" = tasks/001-alpha.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
printf -- '- [x] alpha part two works\n' >> "$TASK"; sed -i '1s/.*/Status: done/' "$TASK"
: > failing.txt
printf '\n## Proposed changes\nBeta should also cover gamma, because X.\n' >> tasks/002-beta.md
printf 'Status: open\n# 003: A feature the agent wants\n- [ ] feature works\n' > tasks/003-feature.md
printf '// Module b: brand new.\nexport function doB() {}\n' > src/b.js
git add -A; git commit -qm "001: restored criterion; proposal for 002; new task 003; src/b.js"
S
cat > .stub/7.sh <<'S'
[ "$TASK" = tasks/002-beta.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
S
# ledger history: 5 earlier sessions on 002, to trigger the split nudge
for k in 1 2 3 4 5; do echo '{"task":"tasks/002-beta.md","iter":0}' >> .agent/iterations.jsonl; done
# --- sibling-reds case: 002 split into 002a (agent), whose cutover breaks two tests and splits the fixes
cat > .stub/8.sh <<'S'
printf 'Status: open\n# 002a: cutover\n- [ ] cutover done\n' > tasks/002a-cutover.md
printf 'Status: split\n# 002: Beta\n\n## Acceptance criteria\n- [ ] beta works\n' > tasks/002-beta.md
git add -A; git commit -qm "002: split"
S
cat > .stub/9.sh <<'S'
[ "$TASK" = tasks/002a-cutover.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
printf 'red one\nred two\n' > failing.txt
printf 'Status: split\n# 002a: cutover\n- [x] cutover done\n' > tasks/002a-cutover.md
printf 'Status: open\n# 002aa: fix red one\n- [ ] red one fixed\n' > tasks/002aa-fix-one.md
printf 'Status: open\n# 002ab: fix red two\n- [ ] red two fixed\n' > tasks/002ab-fix-two.md
git add -A; git commit -qm "002a: cutover breaks two tests; split the fixes"
S
cat > .stub/10.sh <<'S'
[ "$TASK" = tasks/002aa-fix-one.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
sed -i '/red one/d' failing.txt; sed -i 's/- \[ \]/- [x]/; 1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "002aa: done"
S
cat > .stub/11.sh <<'S'
[ "$TASK" = tasks/002ab-fix-two.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
: > failing.txt; sed -i 's/- \[ \]/- [x]/; 1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "002ab: done"
S
cat > .stub/12.sh <<'S'
[ "$TASK" = tasks/002a-cutover.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
sed -i '1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "002a: done"; touch .agent/PAUSE
S
