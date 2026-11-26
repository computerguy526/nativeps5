#!/bin/bash
# The programs a probe run of a UE5 title could not use, from its logs (run.out / run.err): one `0x<hash><TAB><the log line>` row each, sorted, unique.
#   scripts/ue5_probe_hashes.sh [--rt] <log>...  > hashes.txt        then   scripts/harvest_ue5.sh ... --drop-hashes hashes.txt
# Always: "SPIR-V validation failed: hash=0x..." (the recompiler refused the program: the launch-phase compile of the whole list ends with exit 65).
# --rt: also the ray-tracing kernels ("KYTY_RT_STUB|KYTY_RT_SOFTWARE: <stage> shader 0x... has N BVH intersection instruction(s)"), for a title whose
# profile turns ray tracing off (r.RayTracing.Enable=0): the game never runs them, so the list need not build them.
# Run the probe with the title's real settings (and KYTY_RT_STUB=1 with --rt, so the kernels build as stubs and are all logged). Prints nothing else.
set -euo pipefail
RT=0; [ "${1:-}" = --rt ] && { RT=1; shift; }
case "${1:-}" in -h|--help|"") sed -n '2,7p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; [ -n "${1:-}" ] && exit 0 || exit 2;; esac
for f in "$@"; do [ -f "$f" ] || { echo "ue5_probe_hashes: no such log $f" >&2; exit 2; }; done
{
  grep -a -h -o -E 'SPIR-V validation failed: hash=0x[0-9a-f]{16}.*' "$@" | sed -E 's/^(SPIR-V validation failed: hash=)(0x[0-9a-f]{16})(.*)$/\2\t\1\2\3/' || true
  if [ "$RT" = 1 ]; then
    grep -a -h -E '^KYTY_RT_(STUB|SOFTWARE): [A-Za-z]+ shader 0x[0-9a-f]{16} has [1-9][0-9]* BVH' "$@" | sed -E 's/^(KYTY_RT_[A-Z]+: [A-Za-z]+ shader )(0x[0-9a-f]{16})(.*)$/\2\t\1\2\3/' || true
  fi
} | LC_ALL=C sort -u -t "$(printf '\t')" -k1,1
