#!/bin/bash
# agxdis.sh <msl file | metallib | binary archive> [options]  -- see README.md / --help
D="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$D/build/getbin" ] || [ "$D/getbin.m" -nt "$D/build/getbin" ] || [ ! -x "$D/build/metal-archive-extractor" ] || [ ! -f "$D/ext/applegpu-m3/applegpu.py" ]; then
  "$D/build.sh" >&2 || { echo "agxdis: error: build failed (work/agxdis/build.sh)" >&2; exit 11; }
fi
exec python3 "$D/agxdis.py" "$@"
