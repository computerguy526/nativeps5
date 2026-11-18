<!-- One change per PR. CONTRIBUTING.md and AGENTS.md say what is expected; link to them instead of repeating them. -->

## What and why

<!-- The symptom or the need, the cause, the change. Which component repositories it touches (link the sibling PRs). -->

## Evidence

<!-- Paste `nps5 report <runs> --out DIR` (docs/agents/evidence.md) and edit; it covers the lines about the title, before / after, picture, and machine below. -->
<!-- Delete what does not apply and say what was NOT checked. Numbers, hashes and log lines only: no game files. -->
- `scripts/check.sh --range <base>..HEAD --tests`: <!-- paste the last line -->
- Unit tests: <!-- e.g. go test ./pkg/... ; ctest --test-dir build-arm64 -R <pattern> ; kosmickrisp/tests/<name>/run.sh <icd> -->
- Regression gates (`tools/regress.sh` in ps5recomp: calc3d, syobon, greenhollow): <!-- result lines, or why not needed -->
- Shader gate (`tools/shaderlab/gate.sh <KosmicKrisp install>`), for a change to KosmicKrisp or the shader recompiler: <!-- summary line -->
- Title, scene and build: <!-- title ID and version, scene (menu / cutscene / level), run length, runs per side -->
- Before / after: <!-- frame time p50 / p95 and fps, paired or interleaved runs (identical runs differ by up to 3 fps); memory peak -->
- Picture: <!-- frame-dump comparison: which frames, the difference metric, and what the eye sees. Attach pictures to the PR if you want; never commit them -->
- Machine and OS: <!-- e.g. M4 Pro 24 GB, macOS 26.1 -->

## Safety

- [ ] CI is green (`scripts/check.sh --tree --tests` and the other checks in CONTRIBUTING.md, "CI")
- [ ] `scripts/ci/run-local.sh` passes (run before pushing)
- [ ] No game files, shaders, journals, frame dumps, caches or anything extracted from a game in the diff (a profile's `pipelines.kyplst` only with its `.verified` record from `scripts/verify-pipeline-list.sh`)
- [ ] No secrets, personal paths or machine names (`scripts/check.sh` is clean)
- [ ] New behaviour is off by default behind an environment switch, or the evidence above shows it is neutral or better on the title and the gates
- [ ] The run rules in AGENTS.md were kept (one GPU process, memory self-limit untouched, no counter-heap profiling)

## Shader list

<!-- Only for a PR that adds games/<title id>/contrib/<date>-<hash>.kyplst (from `nps5 cache share`): title, app version, new counts (the NEW line), nps5 commit, machine class. Delete otherwise. -->

## AI assistance

<!-- REQUIRED, a PR without this line is not reviewed. yes / no; if yes, which agent, and confirm a person read the diff and the evidence -->
