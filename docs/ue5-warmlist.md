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
   `env NPS5_UECVAR_SET=g.TimeoutForBlockOnRenderFence=1800000`, `env NPS5_UECVAR_SET_FOR=100000` (the cvars are otherwise applied only
   for 120 s) and `env_required NPS5_UECVAR_LIST` (or `env_advised NPS5_UECVAR_LIST` for a title that must keep running without a list, e.g. Cronos: a warning, the fence cvar is inert; the console-variable list made from the player's own eboot: `uecvar xref -o x.json
   <eboot.bin>` then `uecvar dumplist x.json > <title>.list`; without it `NPS5_UECVAR_SET` sets nothing). `scripts/check.sh` refuses a
   `code=ue5` profile without those three rows, without `app_ver`, or with an `app_ver` that is not a build row / the package version.
   `nps5 run` then adds the code store, decoder, fingerprint and abi
   words, keeps `KYTY_PIPELINE_LIST_EXE` (a list may hold a few executable shaders; without the eboot Kyty ignores the whole list) and sets `KYTY_LAUNCH_WAIT_BEFORE_ENTRY=1`. `scripts/check.sh` validates the rows.
3. **Harvest.** Play sessions fill `<id>[.session_<tag>].shaders.journal` and `.pipelines.journal` (static journals are skipped).
   `scripts/harvest_ue5.sh <id> --game <app0> [--code-store <file>] --require-runtime 100 <cache dir>...` exports every pair with
   `--runtime-code`, merges, checks and verifies; `--write` adopts `pipelines.kyplst` and `.verified` into `games/<id>/`.
4. **Gate.** The harvest stops when `check` exits 1 (`--require-runtime` not met) or 2 (title/version mismatch) or when `verify` finds
   code or an uncovered runtime shader. Nothing is adopted then.
5. **Fixtures.** Add the title's profile rows to the tests only as synthetic data (`tests/test_game.py`, `tests/test_run.py`,
   `tests/test_ue5tools.py`); no game bytes anywhere. A new UE version whose decoder moves changes only the `ue5` row; the fingerprint
   check in Kyty refuses a mismatching build and leaves the runtime shaders pending.

`KYTY_UE5_CODE=0` turns the extraction off. `nps5 cache prepare` is optional for these titles: it passes the same UE5 words (code store,
decoder, fingerprint, abi), extracts the code at guest_ready, compiles and exits; it never deletes the player's journals or code store
(`dir=player`). `nps5 cache share` exports with `--runtime-code` and the player's code store (the `*.codestore` itself is never written to a contribution or a bug report).

A prepare of a `code=ue5` title runs in the player's own cache dir, so it replays the player's journals (`KYTY_PIPELINE_JOURNAL_PATH`) as well
as the list: that is intended (prewarm everything the player has seen), at the cost of a longer prepare. The player's journals and code
store are moved to `<dir>.playerkeep` (with a marker file) while the dir is wiped and moved back after; an interrupted prepare leaves
them there and the next prepare restores them first. Nothing deletes that keep dir while it holds a file.

## Notes from the SAROS dry run

- The `env NPS5_UECVAR_SET=g.TimeoutForBlockOnRenderFence=...` row is inert on SAROS: its console-variable list (made from the eboot) has no
  entry for that name (the string is in the eboot as UTF-16 but has no registration xref). It is not needed there: extraction and the listed
  compiles run before the game enters. It is needed on Cronos.
- `tools/ue5/ue5_locate_decoder.py` wants both Oodle strings in the decoder; SAROS's decoder references only one. Use `--all` and check the
  fingerprint and the address-map entry (`F 0x... ` in the build's addrmap.txt) until the locator accepts a single-string decoder.
- `nps5 run` of a non-play class is capped at 180 s: a cold first start is made by `nps5 cache prepare` (up to 1800 s), then a normal run.

## Open checks for WP8

- Cronos (PPSA24096) now uses the journal cache while it has no list (`cache kind=kyplst dir=player code=ue5`; owner decision: needed to
  harvest Cronos sessions and for the second-session benefit). Check the Cronos memory footprint and cache disk growth against its
  18432 MiB guard in WP8 (17770 MiB was reached at about 8.5 min of a cold-cache session); the guard is never raised for this.
- Cronos `NPS5_UECVAR_LIST`: made from the player's own eboot with the recipe above (the same cvar table as SAROS to within a few names);
  check that `g.TimeoutForBlockOnRenderFence` is in each title's list.

