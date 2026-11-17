#!/bin/bash
# Install the check as this clone's pre-commit hook. Run from any repository that should use it:
#   /path/to/nativeps5/scripts/install-hooks.sh [repo dir]
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${1:-.}"
HOOKS="$(git -C "$REPO" rev-parse --git-path hooks)"
case "$HOOKS" in /*) ;; *) HOOKS="$REPO/$HOOKS";; esac
mkdir -p "$HOOKS"
if [ -e "$HOOKS/pre-commit" ]; then echo "pre-commit hook already exists in $HOOKS: add this line yourself:"; echo "  exec \"$HERE/check.sh\""; exit 1; fi
printf '#!/bin/sh\nexec "%s/check.sh"\n' "$HERE" > "$HOOKS/pre-commit"
chmod +x "$HOOKS/pre-commit"
echo "installed $HOOKS/pre-commit"
