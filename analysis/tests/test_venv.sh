#!/usr/bin/env bash
# test_venv.sh DRIVER : the driver's virtualenv handling (ensure_venv, pick_test_cmd), against real PyPI (~30 s).
# 1 a requirements.txt with a pinned version and its real hash builds .venv and the tests run with .venv/bin/python;
# 2 an unchanged requirements.txt does not rebuild it; 3 a wrong hash fails the install, leaves no .venv (tests fall
# back to the system python) and writes exactly one note into DECISIONS.md, however often it is retried.
# The package must not be installed system-wide: .venv sees the system packages, and pip takes an installed one as
# satisfied without downloading it (so without a hash check; apt packages are signed by apt instead).
set -u
DRIVER=${1:-$HOME/bin/agent-loop}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
sed -n '/^USER_TEST_CMD=/,/^pick_test_cmd$/p' "$DRIVER" | sed '$d' > "$T/fns.sh"
mkdir -p "$T/p/.agent"; cd "$T/p" || exit 1; : > DECISIONS.md
log() { echo "     log: $*"; }
. "$T/fns.sh"
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
python3 -m venv "$T/dl" && "$T/dl/bin/pip" download -q --no-deps -d "$T/w" tomli-w==1.0.0 || { echo "cannot reach PyPI"; exit 1; }
h=$("$T/dl/bin/pip" hash "$T"/w/tomli_w-1.0.0-*.whl | sed -n 's/^--hash=//p')
echo "tomli-w==1.0.0 --hash=$h" > requirements.txt
ensure_venv; pick_test_cmd
if [ -x .venv/bin/python ] && .venv/bin/python -c "import tomli_w" && [ "$TEST_CMD" = ".venv/bin/python -m pytest -q" ]; then
  ok "pinned + hashed requirement: .venv built, tests run with .venv/bin/python"
else bad "venv not built or wrong test command ($TEST_CMD)"; fi
.venv/bin/python -c "import json" && ok ".venv sees the standard library" || bad "stdlib import"
m0=$(stat -c %Y .venv/bin/python); sleep 2; ensure_venv
[ "$(stat -c %Y .venv/bin/python)" = "$m0" ] && ok "unchanged requirements.txt: no rebuild" || bad "rebuilt although nothing changed"
echo "tomli-w==1.0.0 --hash=sha256:$(printf '0%.0s' $(seq 1 64))" > requirements.txt
ensure_venv; ensure_venv; pick_test_cmd
[ ! -x .venv/bin/python ] && [ "$TEST_CMD" = "python3 -m pytest -q" ] && ok "wrong hash: install refused, no .venv, system python for tests" || bad "wrong hash was accepted ($TEST_CMD)"
n=$(grep -c "could not be built" DECISIONS.md)
[ "$n" -eq 1 ] && ok "one DECISIONS.md note for the agents" || bad "$n DECISIONS.md notes"
exit $fail
