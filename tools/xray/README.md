# xray: SHADER X-RAY (item 33). One shader, every layer, offline

Local-only data (game-derived dumps/outputs are NOT for git). No game run, no GPU counters: only pipeline creation on KosmicKrisp, `xcrun metal`, and (layer 6) agxdis' offline compile.

    work/xray/xray.sh <input> [--opt N] [--kk DIR] [--out DIR] [--no-air]
    work/xray/batch.sh <list-file> [same options]          # CSV on stdout + <out>/batch.csv; exit 1 if any row failed
    work/xray/tests/run_tests.sh [--quick]                   # 192 checks, ~35 s, exit 0 only if all pass

`<input>` is one of: a 16-hex MSL hash (work/mslover/dump_h|dump/<hash>.metal; `<hash>.v95` variants work), a `.spv` file, a `.metal` file, or a shaderlab id (`s6120.a0_0`).
Output: `out/<id>/` (id = MSL hash for MSL inputs; `<spv b3>_o<KK_MSL_OPT>` for SPIR-V inputs) with

| file | layer | content |
|---|---|---|
| 1_isa.txt | 1 PS5 ISA | Kyty RDNA2 disassembly if `work/xray/isa/*<id or msl hash or spv name>*.rdna2.txt` exists (env XRAY_ISA_DIR), else N/A + how to produce it (ASTROHYBRID_SHADER_DUMP=<guest hash>, KYTY_PROGRAM_CACHE=0). Nothing links a mslover MSL hash to a guest hash today, so this layer is manual. |
| 2_spirv.txt | 2 SPIR-V | spirv-dis. SPIR-V inputs always; MSL inputs only if `xray.sh --build-index` links the MSL to a shaderlab spv (byte-identical KK MSL; only 22 of 486 mslover dumps match: different KK build) |
| 3_nir.txt | 3 KK NIR | NIR at 0_preprocess_in, 1_opt_in, 2a_after_vec (KK_MSL_OPT bit 32768), 2_opt_out, 3_to_msl. Needs the KK_NIR_DUMP_DIR hook in work/s3/kk-xray (COPY of kk-vec, build `work/s3/kk-xray/kb.sh`, patch is in nir_to_msl.c `kk_xray_dump`; kk-opt2 untouched). SPIR-V inputs only. |
| 4_msl.metal, 4_msl_stats.txt | 4 MSL | KK's MSL; global loads by width (scalar vs vector), coherent, loops, atomics, tex, simd |
| 5_air.ll, 5_air_stats.txt | 5 AIR | `xcrun metal -S -emit-llvm -O1 ...` (shaderlab flags): device loads by width incl. the opaque `air.load.device_coherent.*` calls, loops, calls, atomics |
| 6_agx/ | 6 AGX | work/agxdis/agxdis.sh `<4_msl.metal> --json 6_agx/agx_stats.json` (stdout -> agx.txt). Absent hook -> README.txt (N/A). Env XRAY_AGXDIS overrides the path. Gives instr, gpr, spills, device-load widths for this GPU (M4 Pro = applegpu_g16s). Known: agxdis cannot do a fragment-only MSL (needs a vertex function) -> L6=FAIL(rc=5) for FS inputs. |
| 7_cost.txt | 7 cost | commit-ms (whole commit, not the shader alone) from work/mslover/msl_*.commits.log, ko_hashes.txt row, msl-override.md lines |
| summary.txt | | ONE line key=value (L1..L7 = ok/na/FAIL, msl_*, air_*, agx_*) |

SPIR-V -> MSL: runs `work/kscan/bin/kk_spv_gfx` with KK_MSL_COLLECT_DIR; KK writes internal init shaders + a trivial partner stage next to the real one, so the MSL files KK writes for base/{cs,vs,ps}.spv are subtracted and the largest remaining file is taken. KK = work/s3/kk-xray if installed, else kk-vec (XRAY_KK / --kk override; `--opt` sets KK_MSL_OPT; default is that KK's default). Shader cache is disabled for the run. Hash: MSL id = BLAKE3 first 8 bytes of the text = the name mslover/KK_MSL_DUMP_DIR uses (tested).

## Exit codes
0 ok; 1 xray ran but a layer FAILed (see summary L*=FAIL, e.g. xcrun metal rejects the MSL: error text is in 5_air.ll); 2 bad usage/input/missing tool (message `xray: ERROR: ...` on stderr). Never silent.

## Tests (tests/run_tests.sh, fixtures in tests/fixtures; 192 checks)
1 tools present; 2 hash function vs KK names on 6 dumps; 3 MSL fixtures with KNOWN answers at layers 4 and 5 (4 scalar, 1 vec4, mixed, coherent scalar, coherent vec4, guarded, 2 loops); 4 SPIR-V fixtures (glslang) at KK_MSL_OPT 1039/33807 through layers 2-5 incl. NIR sections; 5 hash linkage: xray's SPIR-V->MSL text == the file kk-mslover dumps under its own hash (KK_MSL_DUMP_DIR), L5 compiled from the same 4_msl.metal, L2 == spirv-dis(input); 6 the 3 validation shaders: every layer file non-empty, ids consistent; 7 idempotence (2 runs byte-identical, re-run leaves one folder); 8 errors (unknown hash, junk file, bad magic, truncated spv, missing KK, broken MSL, tools off PATH, missing list) all rc!=0 + message; 9 layer-6 hook with stub (ok and failing); 10 batch rows == single runs, bad row reported and the rest continue; 11 real agxdis on the fixtures (scalar4 -> 1 AGX load i32x4; coherent4 -> 4 loads i32x1). Layer 6 is off in sections 1-10 (XRAY_AGXDIS=none) so they are independent of agxdis.

## Findings (validation, see validation.csv; KK_MSL_OPT 33807 vs 1039)
- Does Apple merge scalar loads? Layer 5 (front-end -O1 IR): NO, 4 adjacent plain scalar loads stay 4. Layer 6 (AGX back end): YES for plain loads (fixture scalar4 -> ONE `device_load xyzw` i32x4). For `coherent` loads NO at either layer: each `*(coherent device float*)` is an opaque `air.load.device_coherent` call, and even a coherent packed_float4 is split into 4 scalar coherent calls at layer 5 (fixture coherent_vec4) and stays 4 scalar loads in AGX.
- KK emits ~all global loads as coherent (WA6): hoover megakernel c4f61f43: 569 MSL global loads, all coherent, 228 of them float4/uint4 -> AIR 789 scalar calls, 0 vector -> AGX 47257 instr, 199 GPRs, 0 spills, 886 device loads of which 130 vector. Hot world-map FS 36f5ae3d: 267 coherent scalar loads, 267 in AIR.
- kkvec bit 32768 on 12 changed shaders (s6346 ... s6174): MSL text changes, MSL vector loads stay 0, AGX instr +-1, device loads and GPRs identical. s6120: 146 instr both. So the vectoriser forms no vector loads in these and Apple's compiler had nothing to gain; the blocker for any load merging is `coherent`, not KK's scalar emission.
