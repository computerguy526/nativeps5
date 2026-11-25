#!/usr/bin/env python3
"""profdrift: the profile IS the test rig. Keeps validated settings (the promotion register) and the shipped profile in step.

Why: a perf/visual result only counts for players if the words that earned it are in what `nps5 run <title>` composes
(games/<id>/game.tsv + the preset + the deny list). Twice a rig (box scripts, k4run, BEST=1 ...) carried words or files the profile did
not, and the measured gain never reached players. This tool makes that visible and checkable. Standard library only.

The register is games/<id>/promotions.tsv (public: words, values, verdicts, evidence DOC NAMES and run tags; no game content, no paths):
    format<TAB>nps5-promotions<TAB>1
    title<TAB>word=value<TAB>verdict<TAB>evidence<TAB>decided<TAB>status<TAB>owner[<TAB>note]
    verdict  KEEP | NEUTRAL | LOSS
    status   pending | landed <commit> | rejected | optin (decided to stay opt-in: not a profile word)
    word     an environment word, or NAME[item]=value for one `item=value` element of a comma list word, e.g.
             NPS5_UECVAR_SET[r.Nanite.PrimShaderRasterization]=0 . A word starting with `?` is not resolved yet (owner fills it in).

Commands (python3 -I scripts/nps5lib/profdrift.py ...):
    check <title>|--all [--release] [--repo DIR]   every KEEP row (pending or landed) is in the composed profile with the same value, and
                                                  no LOSS row with status rejected is. landed + missing = FAIL (a regression). pending +
                                                  missing = WARN, FAIL under --release (a release must not carry open promotions).
    diff <title> <env.dump> [--ignore RE]...       word by word: what the test run's env.dump carried that players do not (RIG-ONLY), what
                                                  players get that the run lacked (PROFILE-ONLY), and VALUE differences. Run-only words
                                                  (window, tags, flight recorder, haptics, cache/dump dirs, paths) are ignored.
    list <title>|--all                             the register rows with their state against the composition
env.dump formats read: `KEY=VALUE` lines (rcgate, `ovguard.py envdump`), `[nps5-config] KEY=VALUE (layer)` and `  KEY=VALUE` lines of
`nps5 run --dry-run`, `# comments` ignored. The composition is games/game.py compose_env + games/<id>/fast-math-deny.txt (as `nps5 run`).
Exit: 0 ok, 1 FAIL, 2 usage."""
import importlib.util, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
REGISTER = "promotions.tsv"
HEAD = ("format", "nps5-promotions", "1")
VERDICTS = ("KEEP", "NEUTRAL", "LOSS")
STATUSES = ("pending", "landed", "rejected", "optin")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
WORD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[([^\[\]=,]+)\])?=(.*)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Words that belong to one run, not to the player's composition (regex on the NAME). The diff ignores them; extend with --ignore.
RUN_ONLY = [r"NPS5_WINDOW.*", r"NPS5_RUN_.*", r"NPS5_TITLE_RUN", r"NPS5_TITLE_PRIO", r".*_TAG", r".*FLIGHTREC.*", r".*HAPTIC.*", r".*RUMBLE.*",
            r".*_DIR", r".*_PATH", r".*_DIRS", r"KYTY_DUMP.*", r"APS5_DUMP.*", r"NPS5_NO_EARLY_STOP", r"NPS5_WATCH", r"NPS5_SCRATCH.*",
            r"VK_DRIVER_FILES", r"DYLD_.*", r"PATH", r"HOME", r"TMPDIR", r"USER", r"NPS5_USERDATA_DIR", r"KYTY_PROGRESS_TITLE_LOG",
            r"KYTY_PIPELINE_STATS", r"KYTY_AUDIO_MASTER_VOLUME", r"NPS5_AUDIO.*", r"NPS5_MUTE.*", r"NPS5_HAPTICS", r"NPS5_TITLE_ID",
            r"NPS5_TITLE_RESOLUTION", r"K4.*", r"RUN_.*", r"VPAD", r"NPS5_VPAD.*", r"ASTRO_ARGS"]


class Fail(Exception):
    pass


# ---------------------------------------------------------------- register
def parse_register(text):
    """-> (rows, problems). A row is a dict with the 7 columns plus note, word split into name/item/value, status split into status/commit."""
    rows, bad, head = [], [], False
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        c = line.rstrip("\n").split("\t")
        if not head:
            head = True
            if tuple(c[:3]) != HEAD:
                bad.append("line %d: first row must be `format<TAB>nps5-promotions<TAB>1`" % n)
            continue
        if c[0] == "title" and c[1:2] == ["word=value"]:
            continue                                  # column header row
        if len(c) < 7:
            bad.append("line %d: %d columns, 7 needed (title, word=value, verdict, evidence, decided, status, owner)" % (n, len(c))); continue
        title, word, verdict, evidence, decided, status, owner = [x.strip() for x in c[:7]]
        note = c[7].strip() if len(c) > 7 else ""
        r = dict(line=n, title=title, word=word, verdict=verdict, evidence=evidence, decided=decided, owner=owner, note=note, status=status.split()[0] if status else "", commit=" ".join(status.split()[1:]))
        m = WORD_RE.match(word)
        r["unresolved"] = word.startswith("?")
        if r["unresolved"]:
            r["name"], r["item"], r["value"] = word, None, ""
        elif not m:
            bad.append("line %d: word %r is not NAME=value or NAME[item]=value" % (n, word)); continue
        else:
            r["name"], r["item"], r["value"] = m.group(1), m.group(2), m.group(3)
        if not re.fullmatch(r"PPSA\d{5}", title): bad.append("line %d: title %r is not PPSAnnnnn" % (n, title))
        if verdict not in VERDICTS: bad.append("line %d: verdict %r is not KEEP/NEUTRAL/LOSS" % (n, verdict))
        if not evidence or evidence == "-": bad.append("line %d: evidence (doc name + run tags) is required" % n)
        if not DATE_RE.match(decided): bad.append("line %d: decided %r is not YYYY-MM-DD" % (n, decided))
        if r["status"] not in STATUSES: bad.append("line %d: status %r is not pending / landed <commit> / rejected / optin" % (n, status))
        if r["status"] == "landed" and not re.fullmatch(r"[0-9a-f]{7,40}", r["commit"]): bad.append("line %d: landed needs a commit hash (landed <commit>)" % n)
        if not owner: bad.append("line %d: owner is required" % n)
        if re.search(r"[/\\]Users[/\\]|~/", line): bad.append("line %d: a local path is in the register (names and tags only)" % n)
        rows.append(r)
    if not head:
        bad.append("empty register")
    return rows, bad


def read_register(repo, tid):
    p = os.path.join(repo, "games", tid, REGISTER)
    if not os.path.isfile(p):
        return None, []
    with open(p, encoding="utf-8") as f:
        return parse_register(f.read())


# ---------------------------------------------------------------- composition
def _game(repo):
    spec = importlib.util.spec_from_file_location("nps5_game_pd", os.path.join(repo, "games", "game.py"))
    g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
    return g


def compose(repo, tid, environ=None):
    """-> {KEY: value} the profile gives a player: game.py compose_env on the back end the profile names, plus the deny list file as
    `nps5 run` adds it. The same code path as `nps5 run --dry-run` up to the machine-local layers (titles/<id>.env, scene, cache)."""
    environ = dict(os.environ if environ is None else environ)
    g = _game(repo)
    try:
        p = g.Profile(*g.find_profile(tid, dict(environ, NPS5_PUBLIC_DIR=repo)))
        gpu = p.one("gpu")
        env = {k: v for k, v, _ in g.compose_env(p, gpu, None, None, "func", g.resolve_hw(None, environ))}
    except g.Err as e:
        raise Fail("compose %s: %s" % (tid, e))
    deny = os.path.join(p.dir, "fast-math-deny.txt")
    if gpu == "kyty" and os.path.isfile(deny) and "KYTY_FAST_MATH_DENY" not in env:
        env["KYTY_FAST_MATH_DENY"] = deny
    return env


def has_word(env, row):
    """True when the composition carries the row's word with its value (bracket form: that item of the comma list)."""
    if row["unresolved"]:
        return False
    if row["name"] not in env:
        return False
    if row["item"] is None:
        return env[row["name"]] == row["value"]
    for part in env[row["name"]].split(","):
        k, eq, v = part.partition("=")
        if eq and k.strip() == row["item"]:
            return v.strip() == row["value"]
    return False


# ---------------------------------------------------------------- check
def check_rows(rows, env, release=False):
    """-> (errors, warnings, infos). errors fail the check."""
    err, warn, info = [], [], []
    for r in rows:
        tag = "%s %s (line %d, %s)" % (r["title"], r["word"], r["line"], r["evidence"][:60])
        if r["verdict"] == "KEEP" and r["status"] in ("pending", "landed"):
            if r["unresolved"]:
                (err if release else warn).append("UNRESOLVED KEEP word: %s: owner %s must name the words" % (tag, r["owner"])); continue
            if not has_word(env, r):
                have = env.get(r["name"])
                why = "absent" if have is None else "composed value %r" % have[:80]
                (err if (r["status"] == "landed" or release) else warn).append("%s KEEP %s not in the composed profile (%s): %s" % (r["status"].upper(), r["word"], why, tag))
            else:
                info.append("ok: %s" % r["word"])
        elif r["verdict"] == "LOSS" and r["status"] == "rejected" and not r["unresolved"] and has_word(env, r):
            err.append("REJECTED word is in the composed profile: %s" % tag)
    return err, warn, info


def titles_with_register(repo):
    gd = os.path.join(repo, "games")
    return sorted(t for t in os.listdir(gd) if re.fullmatch(r"PPSA\d{5}", t) and os.path.isfile(os.path.join(gd, t, REGISTER)))


def cmd_check(args):
    repo, release, titles, i = REPO, False, [], 0
    while i < len(args):
        a = args[i]
        if a == "--release": release = True
        elif a == "--repo": i += 1; repo = args[i]
        elif a == "--all": titles = None
        elif a.startswith("--"): return usage()
        else: titles = (titles or []) + [a]
        i += 1
    if titles == []: return usage()
    if titles is None: titles = titles_with_register(repo)
    bad = 0
    for tid in titles:
        rows, problems = read_register(repo, tid)
        if rows is None:
            print("profdrift %s: no %s (nothing promoted yet)" % (tid, REGISTER))
            if release:
                print("profdrift %s: FAIL (--release needs a register)" % tid); bad += 1
            continue
        if problems:
            print("profdrift %s: FAIL register: " % tid + "; ".join(problems)); bad += 1; continue
        try:
            env = compose(repo, tid)
        except Fail as e:
            print("profdrift %s: FAIL %s" % (tid, e)); bad += 1; continue
        err, warn, info = check_rows(rows, env, release)
        keeps = sum(1 for r in rows if r["verdict"] == "KEEP" and r["status"] in ("pending", "landed"))
        for m in warn: print("profdrift %s: WARN %s" % (tid, m))
        for m in err: print("profdrift %s: FAIL %s" % (tid, m))
        print("profdrift %s: %s (%d KEEP rows, %d in the profile, %d missing/open)" % (tid, "FAIL" if err else "ok", keeps, len(info), len(err) + len(warn)))
        bad += 1 if err else 0
    return 1 if bad else 0


def cmd_list(args):
    repo = REPO; titles = []
    for a in args:
        if a == "--all": titles = titles_with_register(repo)
        elif not a.startswith("--"): titles.append(a)
    for tid in titles:
        rows, problems = read_register(repo, tid)
        if rows is None: print("%s: no register" % tid); continue
        env = compose(repo, tid)
        for r in rows:
            st = "IN-PROFILE" if has_word(env, r) else ("UNRESOLVED" if r["unresolved"] else "not in profile")
            print("%s\t%s\t%s\t%s %s\t%s\t%s" % (tid, r["word"], r["verdict"], r["status"], r["commit"], st, r["owner"]))
        for p in problems: print("%s: PROBLEM %s" % (tid, p))
    return 0


# ---------------------------------------------------------------- diff
def parse_dump(text):
    """-> {KEY: value} from env.dump formats (see the module doc). Later lines win, as in an env string."""
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^\[nps5-config\]\s+([A-Za-z_][A-Za-z0-9_]*)=(.*?)(?:\s+\([^()]*\))?$", s)
        if m:
            if m.group(1) not in ("title", "binary"):      # the two header lines of the config block are not words
                out[m.group(1)] = m.group(2)
            continue
        if s.startswith("[") or s.startswith("nps5 ") or s.startswith("command:"):
            continue
        m = NAME_VAL.match(s)                # one word per line; a value may hold spaces
        if m:
            out[m.group(1)] = m.group(2)
    return out


NAME_VAL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _ignored(name, pats):
    return any(re.fullmatch(p, name) for p in pats)


def _pathlike(v):
    return v.startswith("/") or v.startswith("~") or v.startswith("@/") or v.startswith("@~")


def diff_env(dump, comp, ignore=()):
    """-> dict(rig_only=[(k, v)], profile_only=[(k, v)], value=[(k, rig, profile)], ignored=[k]). Paths that differ only by location are ignored."""
    pats = RUN_ONLY + list(ignore)
    res = dict(rig_only=[], profile_only=[], value=[], ignored=[])
    for k in sorted(set(dump) | set(comp)):
        if _ignored(k, pats):
            res["ignored"].append(k); continue
        if k in dump and k not in comp: res["rig_only"].append((k, dump[k]))
        elif k in comp and k not in dump: res["profile_only"].append((k, comp[k]))
        elif dump[k] != comp[k]:
            if _pathlike(dump[k]) and _pathlike(comp[k]): res["ignored"].append(k)
            else: res["value"].append((k, dump[k], comp[k]))
    return res


def cmd_diff(args):
    repo, ign, pos, i = REPO, [], [], 0
    while i < len(args):
        a = args[i]
        if a == "--ignore": i += 1; ign.append(args[i])
        elif a == "--repo": i += 1; repo = args[i]
        elif a.startswith("--"): return usage()
        else: pos.append(a)
        i += 1
    if len(pos) != 2: return usage()
    tid, path = pos
    with open(path, encoding="utf-8", errors="replace") as f:
        dump = parse_dump(f.read())
    comp = compose(repo, tid)
    d = diff_env(dump, comp, ign)
    print("profdrift diff %s: %s (%d words) vs the profile composition (%d words); %d run-only/path words ignored" % (tid, path, len(dump), len(comp), len(d["ignored"])))
    print("RIG-ONLY (the test run carried these; players do not): %d" % len(d["rig_only"]))
    for k, v in d["rig_only"]: print("  + %s=%s" % (k, v[:120]))
    print("VALUE differs (rig / profile): %d" % len(d["value"]))
    for k, a, b in d["value"]: print("  ~ %s: %s / %s" % (k, a[:70], b[:70]))
    print("PROFILE-ONLY (players get these; the run lacked them): %d" % len(d["profile_only"]))
    for k, v in d["profile_only"]: print("  - %s=%s" % (k, v[:120]))
    rows, _ = read_register(repo, tid)
    if rows:
        names = {k for k, _ in d["rig_only"]} | {k for k, _, _ in d["value"]}
        hit = [r for r in rows if r["verdict"] == "KEEP" and r["name"] in names]
        for r in hit: print("  register: %s is a %s row (%s, %s): the rig has it, the profile does not" % (r["word"], r["verdict"], r["status"], r["evidence"][:50]))
    clean = not (d["rig_only"] or d["value"] or d["profile_only"])
    print("profdrift diff %s: %s" % (tid, "RIG == PROFILE" if clean else "RIG != PROFILE (say so in the report; the result is not a player result for these words)"))
    return 0 if clean else 1


def usage():
    print(__doc__); return 2


def main(argv):
    if not argv or argv[0] not in ("check", "diff", "list"):
        return usage()
    try:
        return {"check": cmd_check, "diff": cmd_diff, "list": cmd_list}[argv[0]](argv[1:])
    except Fail as e:
        print("profdrift: %s" % e); return 1
    except (OSError, IndexError) as e:
        print("profdrift: %s" % e); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
