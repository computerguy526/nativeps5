#!/bin/bash
# usage: gate.sh <kk_install_dir> [--baseline] [-j N]     (< 15 min; CPU: KK pipeline create + xcrun metal; no dispatch)
cd "$(dirname "$0")" || exit 1
mkdir -p tmp
if pgrep -fl "agc_frame_replay|kk_spv|spvpipe|prof.py|harness" | grep -v gate >/dev/null; then echo "other GPU-using process running; the gate only creates pipelines but check the rule"; fi
exec nice python3 -I gate.py run "$@"
