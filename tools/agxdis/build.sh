#!/bin/bash
# Builds work/agxdis/build/{getbin,metal-archive-extractor}; clones the external decoders if missing.
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$D/build" "$D/ext"
clone() { # dir url [branch] [sparse paths...]
  local dir="$D/ext/$1" url="$2" br="${3:-}"; shift 3 || true
  [ -d "$dir/.git" ] && return 0
  if [ $# -gt 0 ]; then
    git clone -q --depth 1 --filter=blob:none --sparse "$url" "$dir" && git -C "$dir" sparse-checkout set "$@"
  else
    git clone -q ${br:+-b "$br"} "$url" "$dir"
  fi
}
clone applegpu    https://github.com/dougallj/applegpu.git ""
clone applegpu-m3 https://github.com/TellowKrinkle/applegpu.git M3
[ "${AGXDIS_NO_AGXRE:-1}" = 1 ] || clone agx-re https://github.com/ADevWithAnIdea/agx-re.git "" tools docs || echo "build.sh: agx-re clone failed (optional)" >&2
X="$D/ext/applegpu/compiler_explorer_tools/metal-archive-extractor.cpp"
[ "$D/build/metal-archive-extractor" -nt "$X" ] || clang++ -std=c++11 -O2 "$X" -o "$D/build/metal-archive-extractor"
[ "$D/build/getbin" -nt "$D/getbin.m" ] || clang -O2 -fobjc-arc "$D/getbin.m" -framework Metal -framework Foundation -o "$D/build/getbin"
