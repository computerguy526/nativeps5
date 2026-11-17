# CLAUDE.md

The rules for working in this repository are in [AGENTS.md](AGENTS.md). Read it first; it applies to Claude Code
exactly as to every other agent and person. This file adds nothing to it and must not grow rules of its own.

In short, before anything else:

1. Never game data in a commit, an issue or a prompt; `scripts/check.sh` before every commit.
2. One GPU-using process at a time; title runs only through `scripts/run-title.sh`.
3. No GPU timestamp queries or Metal counter heaps; never raise the memory self-limit; no screenshots.
4. Search [docs/learnings.md](docs/learnings.md) before building an idea; write your result there when done.
5. Commit only when asked, with the identity git is configured with; never `--no-verify`.

Map of the documentation: [docs/README.md](docs/README.md).
