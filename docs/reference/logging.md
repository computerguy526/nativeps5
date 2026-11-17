# Logging and bug reports

How a run's text log is laid out, how to read it, and how to hand a failed run to someone else in one file. The bug-report
tool follows the idea of NXNative's `scripts/bug_report.py` (MIT licence): one command, an allow-list of text files, redacted,
nothing from the game; the code here is written for this repository.

## Line convention

```
[subsystem] LEVEL message        LEVEL: E error, W warning, I info (default), D debug
```

An `I` line has no level token (`[kyty-seam] watch interval: ...`), so every tool that greps a log keeps working; `E`, `W` and
`D` lines carry it (`[kyty-seam] W slow page`). Older lines are read by their words: `ERROR`, `FATAL`, `assert`, `crash`,
`[RED]` count as E, `WARN` and `failed` as W. Do not rename a tag that a script parses; add a level token instead.

`NPS5_LOG=subsystem:level,...` sets the lowest level a subsystem prints (`NPS5_LOG=kyty-seam:W,kk-archive:D`; `=` works as well
as `:`, and words work as well as letters: `hle=debug`). A bare level (`NPS5_LOG=W`) or `*:W` is the default for every subsystem.
`NPS5_LOG_RATE=FIRST,EVERY` thins the periodic lines of the runtime (the first FIRST, then every EVERYth, with a
`(xK repeated; N not shown)` note). It is off by default because analysis scripts add up every interval line.
The header is `ps5recomp` `pkg/runtime/modules/libSceAnyPS5/nps5_log.h`; the runtime core has its own structured log
(`log.jsonl` in the run bundle) behind the same `NPS5_LOG`.

## Reading a log: `nps5 log`

```
nps5 log <run|tag|file.err>              condensed log + SUMMARY block
nps5 log <run> --summary                 only the block
nps5 log <run> --rate 3,50               the whole log in order, first 3 of each kind then every 50th, "(xK repeated)" lines
nps5 log <run> --level W --grep RE       hide lines below a level / keep lines matching RE
```

The condensed log keeps the first and last lines, every warning, error, crash and assert line, and one sample of each repeated
line with its count (numbers and hashes count as the same line). The log file itself is never changed.

`nps5 run` prints the **SUMMARY** block when the run ends and writes it to `run.summary.txt` next to `run.json`: verdict and
wall time, fps p50 and frame-time p95 (from `fps: N` and `[framerate] ... measured N fps` lines), live shader compiles,
stalls, guard lines, crashes and asserts, warning and error counts, and the settings of the run.

## Reporting a failed run: `nps5 bug-report`

```
nps5 bug-report [<run>|--last] [--out FILE.zip] [--dry-run]
```

One zip of text, to attach to a bug report. `--dry-run` prints the file list and writes nothing; the list is printed before
every write. The zip holds only what the tool's allow-list names, nothing else is read from the run folder:

| In the zip | From |
|---|---|
| `run.json`, `summary.txt`, `crash.txt`, `manifest.json` | the run folder (secret-looking values removed, size-capped) |
| `log.err.txt`, `log.out.txt` | the run's logs, trimmed and de-duplicated as above |
| `run-summary.txt` | the SUMMARY block |
| `doctor.txt`, `machine.txt` | `nps5 doctor`, machine class, macOS and Xcode versions |
| `components.txt` | `components.lock`, the commit of each component checkout and whether it has local changes |
| `settings.txt` | the environment words of the run and `NPS5_*`, `KYTY_*`, `APS5_*`, `ANYPS5_*`, `KK_*` of your shell; a name that looks like a secret keeps its name, not its value |
| `game.tsv` | the title's profile from `games/` |
| `controllers.txt` | names and USB ids of connected game controllers (no serial numbers) |

Redacted in every file: home folder (`~`), user name, computer and host names, other users' home paths, `/Volumes/<name>`,
e-mail addresses, serial numbers, UUIDs, lines that look like key material. After redaction the tool checks its own output
and refuses to write when a personal item is left. Never in the zip: game files, shaders, dumps, journals, caches, saves,
pictures. Read the zip before you attach it.
