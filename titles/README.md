# titles/: this machine's paths

`games/<id>/` says what a title needs (the same on every Mac). `titles/<id>.env` says where things are on THIS Mac.
Copy `example.env` to `titles/<TITLE ID>.env`. Git ignores every file here except `example.env`, `README.md` and
`kyty-preset.env`; never commit a real path, never put game data in this folder.

| Key | Meaning |
|---|---|
| `BIN` | one translated binary; wins over `BIN_DIR` |
| `BIN_DIR` | folder holding `native_<lineage>`; `nps5 run` makes `<scratch>/native-cache/<id>/<lineage>+<patches>` from the profile's `canonical_lineage` and `patches_default` with `nps5 patch` on first use |
| `APP0` | your dump (read only) |
| `GPU` | `kyty` or `anyps5`; the profile's `gpu` when absent |
| `ENV` | extra environment words (above the profile, below the scene, `--arm` and `--env`) |
| `KK`, `LIBS`, `KYTY_LIB` | component builds to use (KosmicKrisp checkout, AnyPS5 build folder, Kyty library or its folder) |
| `WORKDIR`, `USERDATA` | work folder (HOME of the run) and save folder |
| `DUMP_VERIFY` | a command run after the title; non-zero is exit 99 "RUN MODIFIED THE DUMP" |

Order of everything: safety base < profile (`game.py --env`) < `ENV` here < scene pad < `--arm` < `--env`.
`nps5 run <id> --scene worldmap --dry-run` prints the final environment, the binary and the command.
