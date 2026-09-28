#!/usr/bin/env bash
# install-hooks.sh : install the pre-commit hook that blocks commits containing secrets (gitleaks).
# Run once after cloning. Requires gitleaks (brew install gitleaks / https://github.com/gitleaks/gitleaks).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
command -v gitleaks >/dev/null || { echo "gitleaks not found - install it first" >&2; exit 1; }
cat > .git/hooks/pre-commit <<'HOOK'
#!/usr/bin/env bash
# Blocks the commit if the staged changes contain anything that looks like a secret.
exec gitleaks git --pre-commit --staged --redact --no-banner
HOOK
chmod +x .git/hooks/pre-commit
echo "pre-commit secret scan installed"
