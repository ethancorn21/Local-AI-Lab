#!/usr/bin/env bash
set -e
rm -rf "$1"; mkdir -p "$1"/{tasks,src,.stub,.agent}; cd "$1"; git init -q; git config user.email t@t; git config user.name t
printf '.agent/\n.stub/\n' > .gitignore; printf '# rules\n' > AGENTS.md; printf '# PROGRESS\nold snapshot\n' > PROGRESS.md; printf '# DECISIONS\n' > DECISIONS.md
printf 'Status: open\n# 001: Alpha\n\n## Acceptance criteria\n- [ ] alpha works\n' > tasks/001-alpha.md
printf '// a: does a.\nexport function a() {}\n' > src/a.js
printf 'echo "ok 1 - fine"; exit 0\n' > fake-test.sh
codemap-gen --init . || [ $? -eq 3 ]; git add -A; git commit -qm init
cat > .stub/1.sh <<'S'
printf '\n## Hand-over\n\nsession 1: next = write part 2\n' >> "$TASK"; git add -A; git commit -qm "001: s1"
S
cat > .stub/2.sh <<'S'
printf '# PROGRESS\nsession 2 wrote here by habit: next = part 3\n' > PROGRESS.md; git add -A; git commit -qm "001: s2"
S
cat > .stub/3.sh <<'S'
python3 - "$TASK" <<'P'
import re,sys; f=sys.argv[1]; t=open(f).read(); t=re.sub(r"(?ms)^## Hand-over\n.*\Z","",t).rstrip()+"\n\n## Hand-over\n\nsession 3: split into 001a\n- [ ] 001a does part 3\n"; open(f,"w").write(t)
P
sed -i '1s/.*/Status: split/' "$TASK"
printf 'Status: open\n# 001a: part three\n- [ ] part three\n' > tasks/001a-part3.md
printf '# PROGRESS\nsession 3 also wrote here\n' > PROGRESS.md; git add -A; git commit -qm "001: s3 split"
S
cat > .stub/4.sh <<'S'
grep -q 'subtask of tasks/001-alpha.md' .stub/prompt-4.txt && echo "PARENT POINTER OK" >&2
sed -i 's/- \[ \]/- [x]/; 1s/.*/Status: done/' "$TASK"; printf '\n## Hand-over\n\n- [ ] leftover idea (notes, not criteria)\n' >> "$TASK"; git add -A; git commit -qm "001a: done"
S
cat > .stub/5.sh <<'S'
sed -i '0,/- \[ \] alpha works/s//- [x] alpha works/; 1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "001: done"; touch .agent/PAUSE
S
