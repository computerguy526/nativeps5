# Promoting a validated setting into the profile

Rule: **the profile is the test rig.** What `nps5 run <title>` composes (games/<id>/game.tsv, the preset, the deny list)
is what players get, so every perf or visual A/B runs through it and changes only the tested words:

    nps5 run <title> --arm A:           # control = the profile composition
    nps5 run <title> --arm B:WORD=VALUE # the same, one word changed

A launcher that composes its own environment (a box script, a "best stack" file, a hand-copied override directory) measures
a rig. Its numbers are real for that rig and say nothing certain about players: a missing deny list made tuned shaders miss
for players while the rig hit them, and a win measured with words the profile did not carry never shipped. If a rig is
unavoidable, say so in the report, keep its `env.dump`, and diff it:

    python3 -I scripts/nps5lib/profdrift.py diff <title> <run>/env.dump

RIG-ONLY lists the words players do not get, PROFILE-ONLY the words the run lacked.

## The register

`games/<id>/promotions.tsv` lists every validated or rejected setting. Public: words, values, verdicts, evidence document
names and run tags, no paths and no game content.

| column | meaning |
|---|---|
| title | PPSAnnnnn |
| word=value | `NAME=value`, or `NAME[item]=value` for one `item=value` element of a comma-list word (`NPS5_UECVAR_SET[r.X]=0`); `?name` = words not named yet |
| verdict | KEEP, NEUTRAL, LOSS |
| evidence | document name and run tags (a research doc, a playbook row, a D number) |
| decided | YYYY-MM-DD |
| status | `pending`, `landed <commit>`, `rejected`, `optin` (decided to stay opt-in) |
| owner | who lands it |
| note | optional |

## Checklist after a KEEP verdict

1. Same day: add the register row (status `pending`, owner named).
2. Land the word in `games/<id>/game.tsv`: profile only, config-gated (`gpu=` / hardware condition so other titles and back ends are
   unaffected), with a focused unit test (synthetic data), and say why in the row's comment. Never an environment default in code.
3. Re-run ONCE through the player path (`nps5 run <title>`, no rig) and keep its env.dump; `profdrift diff` must show no rig-only words.
4. Set the row to `landed <commit>`.
5. A LOSS verdict gets a row too (`rejected`): `profdrift check` then fails if the word ever lands in the profile.

## Checks

    python3 -I scripts/nps5lib/profdrift.py check <title>|--all [--release]

Every KEEP row must be in the composed profile with the same value. A `landed` row that is missing fails; a `pending` row warns
(`scripts/check.sh --tests`) and fails under `--release`, so a release cannot ship with open promotions. Release checklist:
`check --release` green for every shipped title, and one player-path run per title with its env.dump attached.
