#!/bin/bash
# GPU-free test of patch 0042: every nir memory mode (every single bit and every combination of the bits that nir_to_msl.c
# can see in a barrier) maps to a valid MSL mem_flags name, never a bare integer.
#   run.sh <mesa source dir (has src/compiler/nir/nir_defines.h and src/kosmickrisp/compiler/kk_msl_memflags.h)>
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"; SRC="$1"; OUT="${TMPDIR:-/tmp}/kk-memflags-test"; mkdir -p "$OUT"
# the real nir_variable_mode values, read from nir_defines.h (no GPU headers needed)
{ echo "typedef unsigned nir_variable_mode;"; echo "enum {"
  awk '/^typedef enum/{e=0} /nir_variable_mode;/{e=0} /nir_var_[a-z_0-9]+ *= *\(1 *<< *[0-9]+\)/{print}' "$SRC/src/compiler/nir/nir_defines.h" | sed -E 's/^ *(nir_var_[a-z_0-9]+) *= *(\(1 *<< *[0-9]+\)).*/\1 = \2,/' | sort -u
  echo "};"; } > "$OUT/nir_modes.h"
grep -c nir_var_ "$OUT/nir_modes.h" | sed 's/^/nir_var modes read: /'
cc -O1 -Wall -Werror -I"$OUT" -include "$OUT/nir_modes.h" -I"$SRC/src/kosmickrisp/compiler" "$HERE/memflags_test.c" -o "$OUT/memflags_test"
"$OUT/memflags_test"
