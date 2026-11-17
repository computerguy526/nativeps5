#!/bin/bash
# xray.sh <msl-hash | file.spv | file.metal | shaderlab-id> [--opt N] [--kk DIR] [--out DIR] [--no-air]
# One folder per shader: out/<id>/{1_isa,2_spirv,3_nir,4_msl,5_air,6_agx,7_cost,summary}. Offline only (no GPU run).
cd "$(dirname "$0")" || exit 2
exec nice python3 -I ./xray.py "$@"
