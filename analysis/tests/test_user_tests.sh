#!/usr/bin/env bash
# test_user_tests.sh DRIVER_DIR : USER_TESTS, the rule that tests use the product the way the human does (2026-10-08),
# end to end with the scripted single-agent stub (stubpi), no model, about a minute. Run on the VM.
# The project's settings file sets USER_TESTS=playwright. Sessions:
#   1 (001) adds tests/test_api.py, which calls the code               -> rejected, the reason names the file
#   2 (001) moves it into tests/test_e2e_api.py (drives a browser) and adds a timing test tests/test_perf_api.py
#                                                                       -> accepted (timing tests are exempt)
#   3 (002) adds a test to the old tests/test_old.py (no browser)      -> rejected
#   4 (002) only edits one old test and deletes another                -> accepted
# Then the same project without the settings file: nothing is checked (projects that do not set it are unchanged).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVER=${1:-$HERE/../../harness/driver}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!
run() { ( run_ "$@" ); }
run_() {  # run_ <name> <with settings: 1|0>
  local H=$T/$1/home P=$T/$1/ut
  export HOME=$H PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin"
  mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit/projects"
  for f in agent-loop task-audit codemap-gen decisions-archive user-tests; do cp "$DRIVER/$f" "$H/bin/"; done
  cp "$HERE/stubpi" "$H/.npm-global/bin/pi"; chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
  printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
  [ "$2" = 1 ] && printf "USER_TESTS=playwright\nUSER_TESTS_HOW='in a browser, with Playwright'\n" > "$H/.agent-kit/projects/ut.env"
  mkdir -p "$P"/{tasks,tests,.stub,.agent}; cd "$P"; git init -q
  printf '.agent/\n.stub/\n' > .gitignore; printf '# rules\n' > AGENTS.md; printf '# PROGRESS\n' > PROGRESS.md; printf '# DECISIONS\n' > DECISIONS.md
  printf 'Status: open\n# 001: Api\n\n## Acceptance criteria\n- [ ] api works\n' > tasks/001-api.md
  printf 'Status: open\n# 002: Old\n\n## Acceptance criteria\n- [ ] old works\n' > tasks/002-old.md
  printf 'from app import f\n\ndef test_one():\n    assert f() == 1\n\ndef test_two():\n    assert f() == 1\n' > tests/test_old.py
  git add -A; git commit -qm init
  cat > .stub/1.sh <<'S'
printf 'from app import api\n\ndef test_api():\n    assert api() == 2\n' > tests/test_api.py
sed -i 's/- \[ \]/- [x]/; 1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "001: api + test"
S
  cat > .stub/2.sh <<'S'
git rm -q tests/test_api.py
printf 'from playwright.sync_api import sync_playwright\n\ndef test_api_page():\n    pass\n' > tests/test_e2e_api.py
printf 'import time\n\ndef test_api_fast():\n    assert time.time()\n' > tests/test_perf_api.py
sed -i '1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "001: browser test instead"
S
  cat > .stub/3.sh <<'S'
printf '\ndef test_three():\n    assert f() == 1\n' >> tests/test_old.py
sed -i 's/- \[ \]/- [x]/; 1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "002: one more old test"
S
  cat > .stub/4.sh <<'S'
printf 'from app import f\n\ndef test_one():\n    assert f() >= 1\n' > tests/test_old.py
sed -i '1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "002: old test edited, one deleted"
S
  TEST_CMD=true LLM_URL=http://127.0.0.1:$MS timeout 300 agent-loop "$P" > /dev/null 2>&1
}
run on 1
P=$T/on/ut
L=$P/.agent/loop.log
rej=$(grep -c 'REJECTED' "$L")
[ "$rej" -eq 2 ] && ok "two done claims rejected" || bad "$rej rejections (want 2): $(grep -E 'REJECTED|verified' "$L" | tr '\n' '|')"
grep -q 'tests/test_api.py: a new test file' "$P"/DECISIONS*.md && grep -q 'in a browser, with Playwright' "$P"/DECISIONS*.md \
  && ok "the new non-browser test file is named, with the project's words" || bad "no reason for tests/test_api.py in DECISIONS.md"
grep -q 'tests/test_old.py: 1 test(s) added' "$P"/DECISIONS*.md && ok "a test added to an old non-browser file is named" || bad "no reason for tests/test_old.py"
grep -q 'test_perf_api.py' "$P"/DECISIONS*.md && bad "the timing test was flagged" || ok "the timing test is exempt"
nd=$(for f in "$P"/tasks/0*.md; do head -1 "$f"; done | grep -vc 'Status: done')
[ "$nd" -eq 0 ] && ok "both tasks done in the end (browser test, old test edited and cut)" || bad "$nd task(s) not done"
run off 0
grep -q 'Tests must use the product' "$T"/off/ut/DECISIONS*.md && bad "without USER_TESTS the rule still fired" || ok "without USER_TESTS nothing is checked"
kill $SRV 2>/dev/null
[ $fail -eq 0 ] && echo "ALL OK" || echo "SOME FAILED"
exit $fail
