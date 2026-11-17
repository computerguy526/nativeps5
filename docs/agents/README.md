# Working here as (or with) a coding agent

[AGENTS.md](../../AGENTS.md) has the rules. This page has the working loop that the rules assume: how a piece of
work is scoped, run, measured, handed off and reported. It was distilled from several dozen agent sessions on one
Mac; the incidents behind each habit are in [learnings.md](../learnings.md), section 4.

## 1. Before writing code

1. Search [learnings.md](../learnings.md) and [playbook.md](../playbook.md) for the idea. If it is listed as tried
   and failed, you need a new angle, and you say what it is.
2. Find what bounds the frame in the scene you care about ([reference/architecture.md](../reference/architecture.md),
   section 5). Do not optimise the GPU for a CPU-bound level.
3. Estimate the ceiling: (share of the work you remove) x (its share of the critical path). Under a few percent,
   stop and write that down instead.
4. Prefer the offline route. A shader question goes through `tools/xray` and `tools/agxdis` before any title run;
   a picture bug that can be simulated is simulated first.
5. Write a plan of a page or less: the hypothesis, the switch you will add (off by default), the test, the scenes,
   what would make you stop.

## 2. Your own space

- Your own branch and worktree per component; your own copy of the work directory (`cp -c -R`), your own cache
  identifier, your own output folder and run-tag prefix. Two agents in one output folder have deleted each other's
  frames.
- Your own copy of KosmicKrisp per experiment: a rebuilt driver changes the pipeline cache key for everyone who
  shares it.
- Never edit, rebuild or delete what a canonical run uses. Announce what you own in your first status line.
- Helper scripts get names that are yours (`<agent>_chain.sh`), so nobody kills them by mistake and you kill only
  them, by PID.

## 3. The canonical runner

One runner defines "the current configuration": `scripts/run-title.sh` with `titles/<title id>.env` and, for the
Kyty back end, `titles/kyty-preset.env`. Rules:

- A variant is the canonical runner plus overrides passed to it (extra environment words; the last one wins), never
  a private copy with drifting defaults.
- A comparison names the canonical commit of every component and the overrides of each arm.
- When the canonical configuration changes, the announcement says how to select the previous one, so an old
  baseline can be reproduced.
- After any source change to the driver or a back end: a warm-up run and the offline prewarm on your own work
  directory before measuring.

## 4. A/B rules

- Interleave: A B B A or A B A B as whole runs, or phases inside one run with a live switch on a static scene.
- Same binary, same work directory state, same cache temperature, same scene, same machine state for both arms.
- Performance class only on a quiet machine, on AC power, with no other GPU client
  ([reference/profiling.md](../reference/profiling.md), the checklist). Everything else is functional class, and
  its frame times are not quoted.
- Look at the picture of both arms (frame dumps at the same present counts). Any difference you did not intend
  stops the comparison.
- A gate that waits for a go-ahead matches an anchored, attributed pattern or a flag file, never free text that
  someone might quote.

## 5. Evidence

A claim needs the thing that would let someone else check it:

| Claim | Evidence |
|---|---|
| faster | p50, p95 and fps per arm, n, order of runs, scene, run length, commits, switches, power source |
| less memory | peak footprint per arm (footprint, not RSS) and what the memory was |
| picture fixed | which frames, the difference metric, what the eye sees, at least five runs if the bug was intermittent |
| exact (a codegen change) | the GPU exactness test's check count and mismatches, a negative control, the shaderlab gate verdict |
| root cause | the measurement that separates it from the next most likely cause; what was ruled out |
| not worth doing | the ceiling arithmetic or the neutral numbers |

Pictures are frame dumps written by the program under `NPS5_SCRATCH`. Never screenshots. Never committed.

## 6. Pacing yourself

- Start title runs in the background and wait for them; a tool timeout kills a foreground run mid-frame.
- Do not sit in a wait loop on a job that takes minutes. Arm the chain (a script that runs the arms in order and
  appends one progress line per run), write the handoff, stop. A fresh session with the handoff costs less than an
  old one that waited.
- One GPU-using process at a time on the whole machine, yours included. Check before you start.
- Builds at 4 jobs, nice 5; none during a run whose frame times you measure.
- If the machine is unhealthy (battery, another GPU client, memory pressure), do offline work and say so. Numbers
  from an unhealthy machine are worse than no numbers.

## 7. Status lines and hand-offs

**Status line** (one line, appended to the shared notes of the work): date and time, your name, state (started,
finding, done, stopped), what you own, the commit, the switches and their defaults, the numbers with scene and n,
what is queued and how to stop it (a PID file, not a name).

**Handoff** (one page, when you stop for any reason):

1. State in one paragraph: what is done, what is built but not measured, what is not started.
2. Branches and commits, pushed or local; where the built artefacts are and how to rebuild them.
3. What was measured: a table with tags, arms, numbers. Which runs are invalid and why.
4. The exact commands for what is left, in order, one run per line.
5. What to watch for: the failure signs, the things that looked like the cause and were not.
6. What you own that someone must clean up.

## 8. The report

One report per piece of work, not one per run. In this order:

1. Verdict in a sentence: win (default-on or opt-in), neutral, loss, broke something, or unconfirmed.
2. The numbers behind it (section 5).
3. What changed: commits, switches, defaults.
4. What you did not check.
5. Where you wrote it down: the row in [learnings.md](../learnings.md), the entry in the reference page or the game
   note.

"Unconfirmed" is an acceptable verdict. A win claimed from one run is not.

## 9. Finishing

- Add your row to [learnings.md](../learnings.md), negative results included.
- Put the mechanism where it belongs ([docs/README.md](../README.md), "Where a new finding goes").
- Delete your clones, build folders and scratch output. Leave what a handoff names.
- `scripts/check.sh` before every commit; `scripts/check.sh --range <base>..HEAD --tests` before a pull request.

Evidence tooling (`nps5 ab`, `pics`, `report`, `analyze`): [evidence.md](evidence.md).
