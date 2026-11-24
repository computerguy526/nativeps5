# Large games: the file layer and the memory guard

A PS5 title is 35 to 160 GB and mostly one or two Unreal `.ucas` containers of up to 64 GB. Three pieces make that workable on a Mac
with 24 GB of RAM and a disk shared with everything else. Each is on by default only where it cannot hurt a title that does not
need it.

## 1. Packed files: `<file>.nps5seg/` (segment VFS)

**Why.** macOS transparent (APFS) compression cannot compress a single file over 2 GiB, and `ditto --hfsCompression` skips files over
about 512 MB. The big containers are what is large, so they stay as they are unless they are cut into pieces.

**What `scripts/gamepack.py` stores.** A file `<name>` of more than 1.9 GiB becomes a folder `<name>.nps5seg/` of 1 GiB segments
(`0000`, `0001`, ...), each APFS-compressed, and a `manifest.json` (format `nps5seg/1`: total size, segment size and count, a sha256
per segment and of the whole file, mode, mtime). Smaller files are compressed in place and keep a checksum in an extended attribute.
Commands, safety rules and numbers are in [disk-space.md](disk-space.md).

**What the runtime does.** The translated program's file layer (`pkg/runtime/kernel/vfs/nps5_segfile.c` in `nativeps5-recomp`) opens
a guest path whose host file is missing but whose `<name>.nps5seg/manifest.json` exists, checks the manifest against the segment
sizes (a bad manifest or a missing or wrong-sized segment is logged once as `[vfs] segfile <path>: ... not used`, remembered, and the file is treated as missing; a good one logs `[vfs] segfile <path>: N segments`), and serves the original
bytes: `open`, `stat`, `lstat`, `access`, `fstat`, `read`, `pread`, `lseek`, `mmap` copy-in, `readv`/`preadv`, the console's async
reads, the AMPR/Apr resolve and read worker (SAROS reads its containers that way, not through file descriptors), and directory
listings (`<name>.nps5seg` appears as the regular file `<name>`, hidden while `<name>` itself exists). Reads never hold the file's
lock during the disk read, so concurrent readers of different segments do not wait for each other. A plain file always wins: the
segment lookup runs only after the normal open or stat has failed.

| Knob | Default | Effect |
|---|---|---|
| (none) | automatic | a packed title just works; no setting is needed and no title that is not packed is touched |
| `NPS5_VFS_SEGFILES=0` | unset | turns the segment lookup off (a packed file then looks missing); `1` is the same as unset |
| `NPS5_VFS_SEGFILES_FDS=<n>` | 64 | segment file descriptors kept open; the least recently used closes above it |
| `NPS5_VFS_SEGFILES_TRACE=<n>` | 0 | logs the first n reads with a hash of the returned bytes; `gamepack.py checktrace <log>` re-reads those ranges from the segments and compares |

**Limits.** Only programs built from v1.3.0 or later read segments; an older translated program cannot (relink it, or `gamepack.py
unpack` the file). APFS only; exFAT and network disks are skipped. Segments of 1 GiB are fixed by the tool today. A backup tool that
expands the compression (plain `cp`, Time Machine restore) needs the full size again; run `gamepack.py verify` after any copy. The
dump is changed in place, so keep it writable while packing and the originals (`--keep`) until `verify` passes. Measured: SAROS
`pakchunk0` 2.95 GB -> 1.17 GB, the four large containers together 152.8 GB -> 78.9 GB on disk, gameplay frame rate and world load
unchanged, 12,951 of 12,951 traced reads equal to the segments.

## 2. Compressed caches and data

| What | Command | Result |
|---|---|---|
| a title's big files | `scripts/gamepack.py pack <dump>` | see above |
| any game folder, no segments | `scripts/nps5 data compress <folder> --apply` (dry run without `--apply`) | files up to 2 GiB, about 3x on textures and models |
| finished shader caches | `NPS5_CACHE_COMPRESS=1 nps5 cache prepare <id>`, or `python3 scripts/nps5lib/cachezip.py <cache dir>` | an ASTRO BOT cache 5.9 GB -> 2.3 GB; off by default until the warm-start A/B on a quiet machine |

All three are lossless, check a sha256 before and after, never touch a file a process has open, and can be undone
(`gamepack.py unpack`, `nps5 data decompress`, `afsctool -d`). Developers also compress old run output and captured data with the same
`nps5 data compress`; that data is not part of a release.

## 3. The memory guard and the trims

- The process ends itself with exit code 86 and a `[RED]` line when its footprint passes `NPS5_SELF_MEM_LIMIT_MB`: 37/48 of RAM by
  default (18944 MiB on a 24 GB Mac); `scripts/run-title.sh` refuses a larger value. Per-title rows lower it (SAROS 17408 in
  `games/PPSA07631/game.tsv`) or, once, raise it within the cap (Cronos 18432). **It is never raised to make a scene pass.** See
  [memory.md](memory.md).
- macOS counts compressed pages in the footprint, so a title whose guest memory compresses well still counts it in full.
- Trims, all opt-in unless a profile says otherwise: `KYTY_TEXTURE_RESIDENT_TRIM=1` (native images hold only resident mip levels; on in
  the ASTRO profile), `KYTY_TEX_MIP_SKIP=1|2|3` (large sampled textures start lower down; needs the resident trim), `KYTY_VRAM_BUDGET_MB`
  and `KYTY_VRAM_IDLE_FRAMES` (GPU memory planning; SAROS 8000 and 150), `KYTY_MEMTRIM_BIG_WIDTH=<px>` (own heap for large targets so
  retiring one returns memory), `NPS5_KYTY_ALIAS_TRIM_MS=<ms>` (alias views stop counting twice), and `KYTY_BDA_PAGETABLE_MB=64`
  (GPU address table 512 -> 64 MB, **only** for titles whose buffer addresses stay below 128 GiB: ASTRO BOT yes, SAROS and Cronos no).
- Window-server safety: a title that swaps takes the window server down with it; keep 8 GB free ([machine-safety.md](machine-safety.md)).

Verified on: ASTRO BOT (trims), SAROS (segment VFS on real runs). Cronos: no measurement of the trims yet.
