# Disk space: pack the title, compress the caches

How the runtime reads packed files, the limits and the memory guard: [large-games.md](large-games.md). A game folder without big files can use `scripts/nps5 data compress <folder> --apply` (README, "Save disk space: keep the game folder compressed").

A PS5 title is tens to hundreds of GB, and the caches this project builds for it are several GB more. Two tools shrink both
with APFS transparent compression. The game reads the same bytes; nothing about the dump or the caches changes except how
they sit on the disk. Numbers: SAROS (PPSA07631) packs 152.8 GB -> 78.9 GB; the ASTRO shader cache goes 5.9 GB -> 2.3 GB.

## Step 1 of every new title: pack it

Do this first, before translating or running anything, so every later step (translate, first run, cache prepare) works on the
small copy and you never need the full size on disk twice.

1. **Dry run (changes nothing).** Prints what each file would save and refuses nothing:
   `python3 scripts/gamepack.py pack --dry-run "$NPS5_DUMPS_DIR/<title>"`
   Check that the free space it asks for (estimated compressed size plus a 3 GiB margin) is there. Files whose sampled
   ratio is above 0.9 (video, already-compressed archives) are skipped on purpose.
2. **Make the dump writable.** `BUILDING.md` tells you to keep a dump read-only; packing replaces big files, so
   `chmod -R u+w "<dump>"` for the run and lock it again afterwards. Do not pack the only copy of a dump you cannot re-create
   unless you ran step 3 with `--keep`.
3. **Pack.** `python3 scripts/gamepack.py pack "<dump>"` (add `--keep` to leave every original in place until you have
   run the game once). Files over 1.9 GiB (UE `.ucas`, big paks) become `<file>.nps5seg/` folders of 1 GiB compressed
   segments plus a `manifest.json`; the runtime shows the game the original single file automatically (no setting;
   `NPS5_VFS_SEGFILES=0` turns it off). Smaller files are compressed in place. One file at a time, smallest first; each
   original is removed only after the packed copy has been re-read and compared byte for byte and by sha256.
4. **Verify.** `python3 scripts/gamepack.py verify "<dump>"` re-hashes every segment and file against the manifest. Run it
   again whenever a copy or backup tool touched the dump (plain `cp` or Time Machine restore expand the compression).
5. **Keep the originals until verify passes**, then `python3 scripts/gamepack.py finalize <file>` removes a kept original.
   `unpack` rebuilds the original bit for bit if you ever need it (for example for a tool that cannot read segments).

It refuses to run while a game runs or while a file is open. Back up with a tool that copies the segment folders as they
are; a backup of the unpacked view needs the full size again.

For a developer bringing up a title the same step comes first in the checklist ([../playbook.md](../playbook.md)); pack
before the first translate, then keep working on the packed dump.

## Compressed shader caches

`nps5 cache prepare <id>` builds the compiled caches (driver cache, program cache, KosmicKrisp archive, MSL texts and
libraries) and, when you set `NPS5_CACHE_COMPRESS=1`, compresses the finished cache folder with APFS LZFSE
(`scripts/nps5lib/cachezip.py`). It is off by default until the warm-start load time of a compressed cache has been
measured against a plain one on a quiet machine (the proxy measurement says the cost is small; see the cachezip note in
`docs/learnings.md` once the A/B is in). It is safe: it skips files that any process has open, files changed in the last two minutes (when run by hand), files the
game rewrites every run (`*.journal`, `*.known`, `*.last_scene`), files under 64 KiB, and anything already compressed; it
compares a sha256 of every file before and after and restores the file if they differ.

- Turn it on for a prepare: `NPS5_CACHE_COMPRESS=1 nps5 cache prepare <id>`; `=0` forces it off.
- Unset: on a terminal `nps5 cache prepare` asks "Compress the shader cache too? [y/N]" after the cache is built. The default
  of that question is yes when you already compressed the game files (`nps5 data compress --apply` finished, or
  `NPS5_GAME_COMPRESS=1`). Without a terminal (CI, scripts) there is no question and the built-in default applies (off).
  It never compresses while a game is running.
- Files over the APFS limit (2 GiB, or 512 MiB without afsctool) stay as they are; the summary line says how many and how
  many GB.
- By hand: `python3 scripts/nps5lib/cachezip.py --dry-run <cache dir>` (report only), then without `--dry-run`.
  Never on a cache a running game uses.
- Undo: `afsctool -d <file or dir>`.
- Needs macOS APFS. It uses `afsctool` (`brew install afsctool`) when installed, otherwise `ditto --hfsCompression`
  (which cannot compress files over 512 MiB, so the program and driver caches stay as they are without afsctool).
- A cache file the game rewrites later (the program cache when new pipelines appear) loses its compression when it is
  rewritten; that is harmless, and the next `cache prepare` compresses it again.
