# UE5 warm shader list: adding a new title or version

For Unreal Engine 5 titles the list holds keys and hashes only. The shader code comes from the game's own shader library on the
player's Mac (the game's Oodle decoder runs on a host context), so one generic path serves every UE5 title. Everything that differs
per title or per UE version is a row in `games/<id>/game.tsv`; adding a title or a version needs no code change.

1. **Locate the decoder.** `python3 -I tools/ue5/ue5_locate_decoder.py <translated build dir>` prints `decoder=0x... fp=...` and the
   ready `ue5` row. The function that references both Oodle strings is the decoder; the vaddr comes from the image's region table
   (`NPS5IMG` header), so a non-zero shift is handled. Several candidates: give `--prologue <hex>`. Check that the printed vaddr is a
   function entry in the build's address map.
2. **Write the rows.** `cache kind=kyplst dir=player code=ue5 app_ver=<x.y.z> limit_mb=<n>`, then
   `ue5 decoder=0x<vaddr> fp=<16 hex> abi=oodlelz14`, and the cvar row
   `env NPS5_UECVAR_SET=g.TimeoutForBlockOnRenderFence=1800000`. `nps5 run` then adds the code store, decoder, fingerprint and abi
   words, drops `KYTY_PIPELINE_LIST_EXE` and sets `KYTY_LAUNCH_WAIT_BEFORE_ENTRY=1`. `scripts/check.sh` validates the rows.
3. **Harvest.** Play sessions fill `<id>[.session_<tag>].shaders.journal` and `.pipelines.journal` (static journals are skipped).
   `scripts/harvest_ue5.sh <id> --game <app0> [--code-store <file>] --require-runtime 100 <cache dir>...` exports every pair with
   `--runtime-code`, merges, checks and verifies; `--write` adopts `pipelines.kyplst` and `.verified` into `games/<id>/`.
4. **Gate.** The harvest stops when `check` exits 1 (`--require-runtime` not met) or 2 (title/version mismatch) or when `verify` finds
   code or an uncovered runtime shader. Nothing is adopted then.
5. **Fixtures.** Add the title's profile rows to the tests only as synthetic data (`tests/test_game.py`, `tests/test_run.py`,
   `tests/test_ue5tools.py`); no game bytes anywhere. A new UE version whose decoder moves changes only the `ue5` row; the fingerprint
   check in Kyty refuses a mismatching build and leaves the runtime shaders pending.

`KYTY_UE5_CODE=0` turns the extraction off. `nps5 cache prepare` is optional for these titles.
