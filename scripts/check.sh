#!/bin/bash
# Pre-commit / pre-PR check (game data, secrets, personal paths, unit tests). See nps5_check.py --help.
exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nps5_check.py" "$@"
