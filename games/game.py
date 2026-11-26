#!/usr/bin/env python3
"""games/game.py: the one reader of game profiles (games/<title id>/). Python 3, standard library only.

  game.py list                                   profiles found (repo games/, then NPS5_GAMES_DIR), stage, gpu, package build
  game.py <id> <key>                             every row of one key (columns TAB separated)
  game.py <id> --env [--gpu G] [--scene S] [--build B] [--class C] [--hw H] [--explain]
                                                 resolved KEY=VALUE words, one per line (preset, hardware defaults,
                                                 derived switches, env rows). --hw auto (default: this Mac, or NPS5_HW),
                                                 none, or a spec such as m1-16g (scripts/nps5lib/hw.py)
  game.py <id> --scene <name>                    the resolved scene row (level:<x> fills the level:* template)
  game.py check [<id>]                           validate profile(s) against games/schema.tsv
Search path: games/ (next to this file), then each folder of NPS5_GAMES_DIR (colon separated); NPS5_GAMES_FIRST=1
reverses it. The first folder holding <id>/game.tsv wins; a profile outside games/ is "provisional".
Exit codes: 0 ok, 1 check failed / key absent, 2 profile or scene not found / bad argument, 78 forbidden env.
No default game: a tool without an id must stop and print `game.py list`."""
import ast, importlib.util, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MEM_LIMIT_MAX = 18944
STAGES = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
COND_NAMES = {"gpu": {"kyty", "anyps5"}, "build": None, "scene": None, "class": {"func", "perf"}}
# hardware conditions (docs/reference/hardware.md): hw=<machine class or chip> hw!=<...> gpufam=<g13..g17> gpufam!=<...>
# ram<=N ram>=N (GB)  cores<=N cores>=N (performance cores)  os<=N os>=N (macOS major version)
HW_COND = re.compile(r"(hw|gpufam)(=|!=)([a-z0-9-]+)|(ram|cores|os)(<=|>=)(\d+)")
HW_PLACEHOLDERS = ("{gpufam}", "{osbuild}", "{hw}")
HW_DEFAULTS = "hw-defaults.tsv"
ABS_TOKEN = re.compile(r"(^|[=:])(/[A-Za-z0-9_.~-]|~/|[A-Za-z]:[\\/])")


class Err(Exception):
    def __init__(self, msg, code=2):
        Exception.__init__(self, msg); self.code = code


# ---------------------------------------------------------------- files
def read_rows(path):
    """[(lineno, [columns])] ; comments (# at line start) and blank lines dropped; keeps empty trailing columns."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            rows.append((n, line.split("\t")))
    return rows


def load_schema(path=None):
    path = path or os.path.join(HERE, "schema.tsv")
    schema = {}
    for n, c in read_rows(path):
        if c[0] == "key":
            continue
        if len(c) < 8:
            raise Err("%s:%d: schema row needs 8 columns" % (path, n))
        schema[c[0]] = dict(type=c[1], required=c[2] == "yes", multi=c[3] == "many", allowed=c[4], default=c[5],
                            readers=c[6], meaning=c[7])
    return schema


# ---------------------------------------------------------------- search path
def search_path(environ=None):
    env = os.environ if environ is None else environ
    extra = [d for d in env.get("NPS5_GAMES_DIR", "").split(":") if d]
    paths = [os.path.join(REPO, "games")] + extra
    if env.get("NPS5_GAMES_FIRST") == "1":
        paths = extra + [os.path.join(REPO, "games")]
    return paths


def _is_repo(d):
    return os.path.realpath(d) == os.path.realpath(os.path.join(REPO, "games"))


def find_all(environ=None):
    """{id: [(dir, provisional)]} in search order (first = the winner)."""
    found = {}
    for d in search_path(environ):
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if os.path.isfile(os.path.join(d, name, "game.tsv")):
                found.setdefault(name, []).append((os.path.join(d, name), not _is_repo(d)))
    return found


def find_profile(tid, environ=None):
    hits = find_all(environ).get(tid)
    if not hits:
        raise Err("no profile %s in %s; known: `game.py list`" % (tid, ":".join(search_path(environ))))
    return hits[0]


class Profile:
    def __init__(self, directory, provisional=False):
        self.dir, self.provisional = directory, provisional
        self.id = os.path.basename(directory.rstrip("/"))
        self.rows = read_rows(os.path.join(directory, "game.tsv"))

    def get(self, key):
        return [c[1:] for _, c in self.rows if c[0] == key]

    def one(self, key, default=None):
        v = self.get(key)
        return "\t".join(v[0]) if v else default

    def builds(self):
        out = []
        for c in self.get("build"):
            eb = next((w[6:] for w in " ".join(c[1:]).split() if w.startswith("eboot=")), "")
            out.append((c[0].split()[0] if c and c[0].split() else "", eb))
        return out

    def optfile(self, name):
        p = os.path.join(self.dir, name)
        return read_rows(p) if os.path.isfile(p) else None

    def scenes(self):
        return [r for _, r in (self.optfile("scenes.tsv") or [])]


# ---------------------------------------------------------------- value validators (return error text or None)
def _cols(v):
    return [x for x in v]

def v_text(v, a): return None
def v_id(v, a): return None if len(v) == 1 and re.fullmatch(r"[A-Z]{4}\d{5}", v[0]) else "title id like PPSA12345 expected"
def v_int(v, a): return None if len(v) == 1 and re.fullmatch(r"\d+", v[0]) else "decimal integer expected"
def v_hex(v, a): return None if len(v) == 1 and re.fullmatch(r"0x[0-9a-fA-F_]+", v[0]) else "0x-prefixed hex expected"
def v_wxh(v, a): return None if len(v) == 1 and re.fullmatch(r"\d+x\d+", v[0]) else "<width>x<height> expected"
def v_wxh_opt(v, a): return None if not v or v == [""] or v_wxh(v, a) is None else "<width>x<height> or empty expected"
def v_enum(v, a):
    opts = a.split("|")
    return None if len(v) == 1 and v[0] in opts else "one of %s expected" % "|".join(opts)
def v_version(v, a): return None if len(v) == 1 and re.fullmatch(r"\d+(\.\d+)+", v[0]) else "version like 01.007 expected"
def v_words(v, a): return None if len(v) == 1 and v[0].split() else "one or more names (one column) expected"
def v_relpath(v, a):
    if len(v) != 1 or not v[0]: return "one path expected"
    p = v[0]
    if p.startswith(("/", "~")) or ".." in p.split("/") or re.match(r"[A-Za-z]:", p): return "path must be relative to the repository (no absolute path, no ..)"
def v_build(v, a):
    w = " ".join(v).split()
    if not w or not re.fullmatch(r"\d+(\.\d+)+", w[0]): return "first word must be the content version"
    e = [x for x in w[1:] if x.startswith("eboot=")]
    if len(e) != 1 or not re.fullmatch(r"eboot=[0-9a-f]{16}", e[0]): return "needs exactly one eboot=<16 lowercase hex>"
    for x in w[1:]:
        if x.startswith("size=") and not re.fullmatch(r"size=\d+", x): return "size= must be decimal"
        if x.startswith("sdk=") and not re.fullmatch(r"sdk=(0x)?[0-9a-fA-F]+", x): return "sdk= must be hex"
def v_lineage(v, a):
    w = " ".join(v).split()
    if not w or not re.fullmatch(r"[A-Za-z0-9_.+-]+", w[0]): return "lineage name expected"
    if not any(re.fullmatch(r"recomp=[0-9a-f]{6,40}", x) for x in w[1:]): return "needs recomp=<commit>"
    for x in w[1:]:
        if x.startswith("shift=") and not re.fullmatch(r"shift=0x[0-9a-fA-F]+", x): return "shift= must be 0x hex"
def v_kvwords(v, a):
    for x in " ".join(v).split():
        if "=" not in x: return "word %r is not key=value" % x
def parse_when(s):
    """-> (list of (name, value), error)"""
    if s in ("", "-"): return [], None
    out = []
    for c in s.split(","):
        m = HW_COND.fullmatch(c)
        if m:
            out.append((m.group(1) + m.group(2), m.group(3)) if m.group(1) else (m.group(4) + m.group(5), m.group(6)))
            continue
        n, eq, val = c.partition("=")
        if not eq or n not in COND_NAMES or not val:
            return None, "bad condition %r (gpu= build= scene= class= hw= hw!= gpufam= gpufam!= ram<= ram>= cores<= cores>= os<= os>=)" % c
        if COND_NAMES[n] is not None and val not in COND_NAMES[n]:
            return None, "%s= must be one of %s" % (n, "|".join(sorted(COND_NAMES[n])))
        out.append((n, val))
    return out, None
def v_envrow(v, a):
    if not v or not v[0]: return "KEY=VALUE expected"
    k, eq, val = v[0].partition("=")
    if not eq or not ENV_KEY.match(k): return "first column must be KEY=VALUE with a valid variable name"
    if len(v) > 1:
        _, e = parse_when(v[1])
        if e: return e
    if len(v) > 3: return "at most 3 columns (KEY=VALUE, when, why)"
    return mem_limit_error(k, val)
def v_forbid(v, a):
    if not v or not v[0]: return "KEY[=VALUE] expected"
    if not ENV_KEY.match(v[0].partition("=")[0]): return "bad variable name"
def v_marker(v, a):
    if len(v) != 2 or not re.fullmatch(r"[A-Za-z0-9_]+", v[0]): return "needs <name> <spec> in two columns"
    if not re.fullmatch(r"(log:.+|open:.+|present:\d+)", v[1]): return "spec must be log:<re> | open:<re> | present:<n>"
def v_runsecs(v, a):
    w = " ".join(v).split()
    if not w or not all(re.fullmatch(r"(default|max)=\d+", x) for x in w): return "default=<s> max=<s> expected"
def v_cache(v, a):
    w = " ".join(v).split(); kv = dict(x.partition("=")[::2] for x in w)
    if kv.get("kind") not in ("kyplst", "journal", "none"): return "kind=kyplst|journal|none required"
    for k, val in kv.items():
        if k == "limit_mb" and not val.isdigit(): return "limit_mb must be decimal"
        if k == "app_ver" and not re.fullmatch(r"\d+(\.\d+)+", val): return "app_ver must look like 01.007.000"
        if k == "dir" and val != "player": return "dir must be player"
        if k == "code" and val != "ue5": return "code must be ue5"
        if k not in ("kind", "limit_mb", "app_ver", "dir", "code"): return "unknown cache word %r" % k
    if kv.get("code") and (kv.get("kind") != "kyplst" or kv.get("dir") != "player"): return "code=ue5 needs kind=kyplst and dir=player"
def v_ue5(v, a):
    w = " ".join(v).split(); kv = dict(x.partition("=")[::2] for x in w)
    if len(kv) != len(w): return "duplicate word"
    for k in kv:
        if k not in ("decoder", "fp", "abi"): return "unknown ue5 word %r" % k
    if not re.fullmatch(r"0x[0-9a-fA-F]+", kv.get("decoder", "")): return "decoder=0x<guest vaddr> required"
    if not re.fullmatch(r"[0-9a-f]{16}", kv.get("fp", "")): return "fp=<16 lowercase hex> required"
    if kv.get("abi") != "oodlelz14": return "abi=oodlelz14 required"
def v_addrbuild(v, a):
    w = " ".join(v).split()
    if not w or not re.fullmatch(r"0x[0-9a-fA-F]+", w[0]): return "guest address (hex) first"
    for x in w[1:]:
        if not re.fullmatch(r"build=\S+", x): return "only build=<version> may follow the address"
def v_stage(v, a): return None if len(v) == 1 and v[0] in STAGES else "S0..S8 expected"

VALIDATORS = {"text": v_text, "id": v_id, "int": v_int, "hex": v_hex, "wxh": v_wxh, "wxh_opt": v_wxh_opt, "enum": v_enum,
              "version": v_version, "words": v_words, "relpath": v_relpath, "build": v_build, "lineage": v_lineage,
              "kvwords": v_kvwords, "envrow": v_envrow, "forbidrow": v_forbid, "marker": v_marker, "runsecs": v_runsecs,
              "cache": v_cache, "ue5row": v_ue5, "addrbuild": v_addrbuild, "stage": v_stage}


def mem_limit_error(k, val, limit=MEM_LIMIT_MAX):
    if k == "NPS5_SELF_MEM_LIMIT_MB":
        try:
            if int(val) > limit: return "NPS5_SELF_MEM_LIMIT_MB above %d is refused" % limit
        except ValueError:
            return "NPS5_SELF_MEM_LIMIT_MB must be a number"


# ---------------------------------------------------------------- scenes / bench / smoke
SCENE_HEAD = ["name", "args", "pad", "until", "secs", "save", "description"]
UNTIL_RE = re.compile(r"(first-present|pic|marker:[A-Za-z0-9_]+|scene:[^\s+,]+|log:.+?|open:.+?|time:\d+|frames:\d+)(\+\d+)?((?:,(?:black|stall):\d+)*)")


def scene_lookup(scenes, name):
    """scenes: list of columns. Returns the resolved row (list of 7) or None. `level:<x>` fills template `level:*`."""
    for r in scenes:
        if r[0] == name and "*" not in r[0]:
            return list(r)
    if ":" in name and not name.endswith("*"):
        pre, _, rest = name.partition(":")
        for r in scenes:
            if r[0] == pre + ":*":
                return [x.replace("{name}", rest) for x in r]
    return None


def scene_exists(scenes, name):
    return scene_lookup(scenes, name) is not None


def scene_dict(row):
    row = list(row) + [""] * (7 - len(row))
    return dict(zip(SCENE_HEAD, row))


# ---------------------------------------------------------------- check
def _abs_hit(cols):
    for c in cols:
        for tok in c.split():
            if tok.startswith("log:") or tok.startswith("open:"):
                tok = tok.split(":", 1)[1]
                if tok.startswith("/"): return tok
                continue
            if ABS_TOKEN.search(tok): return tok
    return None


def check_profile(p, schema):
    errs, warns = [], []
    def E(f, n, m): errs.append("%s/%s:%s: error: %s" % (p.id, f, n, m) if n else "%s/%s: error: %s" % (p.id, f, m))
    def W(f, n, m): warns.append("%s/%s:%s: warning: %s" % (p.id, f, n, m))
    seen = {}
    for n, c in p.rows:
        key, vals = c[0], c[1:]
        if key.startswith("fact."):
            if len(key) == 5: E("game.tsv", n, "empty fact key")
            h = _abs_hit(vals)
            if h: E("game.tsv", n, "absolute or personal path %r (profiles hold no machine paths)" % h)
            continue
        s = schema.get(key)
        if s is None:
            E("game.tsv", n, "unknown key %r (not in games/schema.tsv; use fact.<name> for free notes)" % key); continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1 and not s["multi"]:
            E("game.tsv", n, "key %r may appear once" % key)
        h = _abs_hit(vals)
        if h: E("game.tsv", n, "absolute or personal path %r (profiles hold no machine paths)" % h)
        e = VALIDATORS[s["type"]](vals, s["allowed"])
        if e: E("game.tsv", n, "%s: %s" % (key, e))
    for key, s in schema.items():
        if s["required"] and key not in seen:
            E("game.tsv", None, "missing required key %r" % key)
    cw = (p.one("cache", "") or "").split()
    if "code=ue5" in cw and not p.one("ue5"): E("game.tsv", None, "cache code=ue5 needs a `ue5 decoder=<vaddr> fp=<fnv> abi=oodlelz14` row")
    if p.one("ue5") and "code=ue5" not in cw: E("game.tsv", None, "a `ue5` row needs `cache ... code=ue5`")
    if len(cw) != len(set(cw)): E("game.tsv", None, "cache row repeats a word")
    if "code=ue5" in cw:
        av = next((w[8:] for w in cw if w.startswith("app_ver=")), "")
        bv = [b[0] for b in p.builds()]
        if not av: E("game.tsv", None, "cache code=ue5 needs app_ver=<version> (the list is tied to one build)")
        elif av not in bv: E("game.tsv", None, "cache app_ver %s is not a build row (%s): the ue5 decoder row belongs to one build" % (av, ", ".join(bv)))
        elif p.one("package") and av != p.one("package").split()[0]: E("game.tsv", None, "cache app_ver %s is not the package version %s" % (av, p.one("package")))
        # the render-fence wait must outlast the first start's compile (30 min), and that cvar only applies with the cvar machinery configured
        envs = [c[0] for c in p.get("env") if c]
        if not any(e.startswith("NPS5_UECVAR_SET=") and "g.TimeoutForBlockOnRenderFence=" in e for e in envs):
            E("game.tsv", None, "code=ue5 needs an `env NPS5_UECVAR_SET=...g.TimeoutForBlockOnRenderFence=<ms>` row")
        if not any(e.startswith("NPS5_UECVAR_SET_FOR=") for e in envs):
            E("game.tsv", None, "code=ue5 needs an `env NPS5_UECVAR_SET_FOR=<ms>` row (the cvars are re-applied only 120 s by default)")
        if not any(c and c[0] == "NPS5_UECVAR_LIST" for c in p.get("env_required")):
            E("game.tsv", None, "code=ue5 needs `env_required NPS5_UECVAR_LIST` (without the cvar list NPS5_UECVAR_SET sets nothing)")
    tid = p.one("title_id")
    if tid and tid != p.id: E("game.tsv", None, "title_id %s does not match folder name %s" % (tid, p.id))
    builds = p.builds()
    pk = p.one("package")
    if pk and builds and pk not in [b[0] for b in builds]:
        E("game.tsv", None, "package %s is not a build row (%s)" % (pk, ", ".join(b[0] for b in builds)))
    if len({b[0] for b in builds}) != len(builds): E("game.tsv", None, "duplicate build version")
    # forbidden env hit by the profile's own rows
    forb = [c[0] for c in p.get("env_forbidden") if c]
    for n, c in p.rows:
        if c[0] == "env" and len(c) > 1 and "=" in c[1]:
            for f in forb:
                if forbidden_match(c[1], f): E("game.tsv", n, "env %s is refused by env_forbidden %s" % (c[1], f))
    # preset file
    preset = p.one("preset")
    if preset and v_relpath([preset], "") is None and not os.path.isfile(os.path.join(REPO, preset)) and not p.provisional:
        W("game.tsv", None, "preset file %s not found in the repository" % preset)
    # patches.py BUILDS vs build rows (parsed, never executed)
    pp = os.path.join(p.dir, "patches.py")
    if os.path.isfile(pp):
        try:
            tree = ast.parse(open(pp, encoding="utf-8").read())
            pb = None
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "BUILDS" for t in node.targets):
                    pb = ast.literal_eval(node.value)
            if pb is None:
                W("patches.py", None, "no literal BUILDS = {...} found; builds are not cross-checked")
            else:
                bd = dict(builds)
                if not isinstance(pb, dict): raise ValueError("BUILDS is not a dict")
                for ver, eb in pb.items():
                    if not (isinstance(eb, str) and re.fullmatch(r"[0-9a-f]{16,64}", eb)):      # '' or a short prefix would match other builds
                        E("patches.py", None, "BUILDS %s=%r must be at least 16 lowercase hex digits" % (ver, eb)); continue
                    if ver not in bd: E("patches.py", None, "BUILDS version %s has no build row in game.tsv" % ver)
                    elif not (bd[ver].startswith(eb) or eb.startswith(bd[ver])): E("patches.py", None, "BUILDS %s=%s differs from game.tsv eboot=%s" % (ver, eb, bd[ver]))
                for ver in bd:
                    if ver not in pb: W("patches.py", None, "build %s has no BUILDS entry" % ver)
        except (SyntaxError, ValueError, TypeError, RecursionError) as ex:
            E("patches.py", None, "cannot parse: %s" % ex)
    # scenes
    markers = {c[0] for c in p.get("marker") if c}
    srows = p.optfile("scenes.tsv")
    scenes = []
    if srows is not None:
        names = set()
        for n, c in srows:
            if len(c) < 6: E("scenes.tsv", n, "needs 7 columns (name args pad until secs save description), has %d" % len(c)); continue
            c = c + [""] * (7 - len(c))
            scenes.append(c)
            name, args, pad, until, secs, save = c[:6]
            if name in names: E("scenes.tsv", n, "duplicate scene %r" % name)
            names.add(name)
            if not re.fullmatch(r"[A-Za-z0-9_]+|[A-Za-z0-9_]+:\*", name): E("scenes.tsv", n, "bad scene name %r" % name)
            tmpl = name.endswith(":*")
            if "{name}" in (args + until + pad) and not tmpl: E("scenes.tsv", n, "{name} only allowed in a level:* template row")
            h = _abs_hit([args, pad])
            if h: E("scenes.tsv", n, "absolute or personal path %r" % h)
            if pad not in ("", "-"):
                if re.match(r"f?\d+:", pad): pass
                else:
                    rel = pad if "/" in pad else "pad/" + pad
                    if v_relpath([rel], "") or not os.path.isfile(os.path.join(p.dir, rel)):
                        E("scenes.tsv", n, "pad file %s does not exist in the profile folder" % rel)
            m = UNTIL_RE.fullmatch(until)
            if not m: E("scenes.tsv", n, "bad until %r (first-present|pic|marker:<m>|scene:<s>|log:<re>|open:<re>, optional +N)" % until)
            elif until.startswith("marker:"):
                mk = re.split(r"[+,]", until[7:])[0]
                if mk not in markers: E("scenes.tsv", n, "marker %r is not defined by a marker row in game.tsv" % mk)
            if not re.fullmatch(r"\d+(/\d+)?", secs): E("scenes.tsv", n, "secs must be <s> or <s>/<max>")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", save): E("scenes.tsv", n, "save must be new, keep or a name")
    for fname, head, ncols in (("bench.tsv", ["name", "scene", "window", "class", "description"], 5),
                               ("smoke.tsv", ["name", "scene", "mode", "reach", "mem_max_mib", "stall_ms_max", "description"], 7)):
        rows = p.optfile(fname)
        if rows is None: continue
        for n, c in rows:
            if len(c) < ncols - 1 or len(c) > ncols:
                E(fname, n, "needs %d columns (%s), has %d" % (ncols, " ".join(head), len(c))); continue
            c = c + [""] * (ncols - len(c))
            if not scene_exists(scenes, c[1]):
                E(fname, n, "scene %r is not defined in scenes.tsv" % c[1])
            if fname == "bench.tsv":
                if c[3] not in ("func", "perf"): E(fname, n, "class must be func or perf")
                w = c[2]; m = re.fullmatch(r"(marker|present):([A-Za-z0-9_:]+)\+\d+|\d+s\.\.\d+s", w)
                if not m: E(fname, n, "bad window %r (marker:<m>+N | present:<scene>+N | <a>s..<b>s)" % w)
                elif w.startswith("marker:") and m.group(2) not in markers: E(fname, n, "marker %r is not defined" % m.group(2))
                elif w.startswith("present:") and not scene_exists(scenes, m.group(2)): E(fname, n, "scene %r in window is not defined" % m.group(2))
            else:
                if c[2] not in ("quick", "full", "quick,full"): E(fname, n, "mode must be quick, full or quick,full")
                if not re.fullmatch(r".+@\d+", c[3]): E(fname, n, "reach must be <until spec>@<s>")
                else:
                    reach = c[3].rsplit("@", 1)[0]
                    if not UNTIL_RE.fullmatch(reach): E(fname, n, "bad reach target %r (an until spec as in scenes.tsv)" % reach)
                    elif reach.startswith("marker:") and re.split(r"[+,]", reach[7:])[0] not in markers: E(fname, n, "marker %r is not defined" % re.split(r"[+,]", reach[7:])[0])
                    if int(c[3].rsplit("@", 1)[1]) < 1: E(fname, n, "reach seconds must be at least 1")
                if not c[4].isdigit() or not c[5].isdigit(): E(fname, n, "mem_max_mib and stall_ms_max must be decimal")
            h = _abs_hit(c)
            if h: E(fname, n, "absolute or personal path %r" % h)
    # validated.tsv: mode <TAB> words; a row a tool cannot read is not a validation
    for n, c in (p.optfile("validated.tsv") or []):
        kv = dict(w.partition("=")[::2] for w in (c[1].split() if len(c) > 1 else []))
        if len(c) != 2 or c[0] not in ("quick", "full"): E("validated.tsv", n, "needs <quick|full> <TAB> key=value words")
        elif not (("kyty" in kv) != ("anyps5" in kv)): E("validated.tsv", n, "needs exactly one of kyty=<commit> or anyps5=<commit> (the back end that ran)")
        elif not all(re.fullmatch(r"[0-9a-f]{7,40}(\+[0-9a-f]{8})?", kv.get(k, "")) for k in ("kyty" if "kyty" in kv else "anyps5", "kk", "ps5recomp")):
            E("validated.tsv", n, "kyty= / anyps5=, kk= and ps5recomp= must be commits (7 to 40 hex, optionally +<8 hex>)")
        elif not re.fullmatch(r"\d{4}-\d\d-\d\d", kv.get("date", "")): E("validated.tsv", n, "needs date=<YYYY-MM-DD>")
        h = _abs_hit(c)
        if h: E("validated.tsv", n, "absolute or personal path %r" % h)
    # stage evidence
    st = p.one("stage")
    if st in STAGES:
        for need in stage_gaps(p, st, scenes, markers):
            E("game.tsv", None, "stage %s claimed but %s" % (st, need))
    return errs, warns


def stage_gaps(p, st, scenes, markers):
    i = STAGES.index(st); gaps = []
    if i >= 0 and not p.builds(): gaps.append("no build row (S0)")
    if i >= 2 and not p.get("lineage"): gaps.append("no lineage row (S2)")
    if i >= 5 and not os.path.isdir(os.path.join(p.dir, "pad")) and not any(re.match(r"f?\d+:", r[2]) for r in scenes if len(r) > 2):
        gaps.append("no pad/ folder or inline pad script (S5)")
    if i >= 6 and not scenes: gaps.append("scenes.tsv missing or empty (S6)")
    if i >= 7:
        if p.optfile("bench.tsv") is None: gaps.append("bench.tsv missing (S7)")
        c = " ".join(" ".join(x) for x in p.get("cache"))
        if "kind=kyplst" in c and not os.path.isfile(os.path.join(p.dir, "pipelines.kyplst")): gaps.append("cache kind=kyplst but pipelines.kyplst missing (S7)")
    if i >= 8:
        if p.optfile("smoke.tsv") is None: gaps.append("smoke.tsv missing (S8)")
        v = p.optfile("validated.tsv")
        if not v or not any(r[0] == "full" for _, r in v): gaps.append("validated.tsv has no full row (S8)")
        if not os.path.isfile(os.path.join(p.dir, "README.md")): gaps.append("README.md missing (S8)")
    return gaps


# ---------------------------------------------------------------- env composition
def forbidden_match(word, rule):
    wk, _, wv = word.partition("="); rk, eq, rv = rule.partition("=")
    return wk == rk and (not eq or wv == rv)


def resolve_build(p, want):
    b = p.builds()
    if want is None:
        pk = p.one("package")
        for ver, eb in b:
            if ver == pk: return ver, eb
        return b[0] if b else ("", "")
    for ver, eb in b:
        if ver == want or ver.startswith(want) or (eb and eb.startswith(want)): return ver, eb
    raise Err("build %s is not a build row of %s (%s)" % (want, p.id, ", ".join(v for v, _ in b)))


def is_hw_cond(n):
    return n.rstrip("<>!=") in ("hw", "gpufam", "ram", "cores", "os")


def hw_cond_holds(n, val, hw):
    """One hardware condition against a hardware dict (scripts/nps5lib/hw.py); never holds when hw is None or the
    field it needs is unknown."""
    if not hw: return False
    base, op = n.rstrip("<>!="), n[len(n.rstrip("<>!=")):]
    if base in ("hw", "gpufam"):
        have = [hw.get("class"), hw.get("chip_key")] if base == "hw" else [hw.get("gpufam")]
        if not have[0] or have[0] == "unknown": return False
        return (val in have) == (op == "=")
    x = {"ram": hw.get("ram_gb"), "cores": hw.get("pcores"), "os": hw.get("os_major")}[base]
    if not x: return False
    return x <= int(val) if op == "<=" else x >= int(val)


def hw_fill(v, hw):
    """{gpufam} {osbuild} {hw} in an env value -> this machine's values ('unknown' without hardware)."""
    if "{" not in v: return v
    h = hw or {}
    for ph, key in (("{gpufam}", "gpufam"), ("{osbuild}", "osbuild"), ("{hw}", "class")):
        v = v.replace(ph, re.sub(r"[^A-Za-z0-9-]", "", str(h.get(key) or "unknown")))
    return v


_hwmod = None
def hwlib():
    """scripts/nps5lib/hw.py (hardware detection and derived limits), loaded once; None when the file is absent."""
    global _hwmod
    if _hwmod is None:
        path = os.path.join(REPO, "scripts", "nps5lib", "hw.py")
        if not os.path.isfile(path): _hwmod = False
        else:
            spec = importlib.util.spec_from_file_location("nps5_hw", path)
            _hwmod = importlib.util.module_from_spec(spec); spec.loader.exec_module(_hwmod)
    return _hwmod or None


def resolve_hw(arg, environ=None):
    """--hw value -> hardware dict or None. auto: NPS5_HW or this machine; none: no hardware; else a spec."""
    lib = hwlib()
    if arg == "none" or lib is None: return None
    try:
        if arg in (None, "auto"): return lib.detect(environ)
        return lib.parse_spec(arg)
    except ValueError as e:
        raise Err("--hw: %s" % e)


def mem_limit_for(hw):
    lib = hwlib()
    if hw and lib and hw.get("ram_mb"): return lib.defaults(hw)["NPS5_SELF_MEM_LIMIT_MB"]
    return MEM_LIMIT_MAX


def hw_default_rows():
    """Rows of games/hw-defaults.tsv: env rows for every title, each with at least one hardware condition."""
    path = os.path.join(HERE, HW_DEFAULTS)
    return read_rows(path) if os.path.isfile(path) else []


def check_hw_defaults():
    errs = []
    for n, c in hw_default_rows():
        where = "%s:%d: error: " % (HW_DEFAULTS, n)
        if c[0] != "env": errs.append(where + "only env rows are allowed"); continue
        e = v_envrow(c[1:], "")
        if e: errs.append(where + e); continue
        conds, _ = parse_when(c[2] if len(c) > 2 else "-")
        if not any(is_hw_cond(k) for k, _ in conds): errs.append(where + "needs a hardware condition (hw= gpufam= ram<= cores<= os>= ...)")
        if len(c) < 4 or not c[3].strip(): errs.append(where + "needs a reason (say `untested` when it was not measured)")
    return errs


def cond_holds(conds, ctx):
    for n, val in conds:
        if is_hw_cond(n):
            if not hw_cond_holds(n, val, ctx.get("hw")): return False
            continue
        if n == "gpu" and ctx["gpu"] != val: return False
        if n == "class" and ctx["class"] != val: return False
        if n == "build" and not (ctx["version"].startswith(val) or (ctx["eboot"] and ctx["eboot"].startswith(val))): return False
        if n == "scene" and val not in ctx["scene_names"]: return False
    return True


def read_preset(path):
    """Preset files hold KEY=VALUE words (several per line allowed), # comments, optional `export `."""
    import shlex
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            if line.startswith("export "): line = line[7:]
            for w in shlex.split(line, comments=True):
                k, eq, v = w.partition("=")
                if eq and ENV_KEY.match(k): out.append((k, v))
    return out


def compose_env(p, gpu=None, scene=None, build=None, cls=None, hw=None):
    """-> list of (KEY, VALUE, layer) in final order (one per key). Raises Err(code 78) on a forbidden word.
    hw: hardware dict (scripts/nps5lib/hw.py) or None; without it no hardware condition holds."""
    gpu = gpu or p.one("gpu")
    if gpu not in ("kyty", "anyps5"): raise Err("gpu must be kyty or anyps5")
    ver, eb = resolve_build(p, build)
    names = set()
    if scene:
        srows = p.scenes(); row = scene_lookup(srows, scene)
        if row is None: raise Err("scene %s is not defined in %s/scenes.tsv" % (scene, p.id))
        names = {scene, row[0], scene.split(":")[0] + ":*"} if ":" in scene else {scene}
        names.add(next((r[0] for r in srows if scene_lookup([r], scene)), scene))
    ctx = dict(gpu=gpu, version=ver, eboot=eb, scene_names=names, hw=hw, **{"class": cls})
    out = {}; order = []
    def put(k, v, layer):
        if k not in out: order.append(k)
        out[k] = (v, layer)
    put("NPS5_TITLE_ID", p.id, "profile")
    preset = p.one("preset")
    if preset and gpu == p.one("gpu"):
        pp = os.path.join(REPO, preset)
        if not os.path.isfile(pp): raise Err("preset %s not found" % preset)
        for k, v in read_preset(pp): put(k, v, "preset")
    for _, c in hw_default_rows():
        conds, e = parse_when(c[2] if len(c) > 2 else "-")
        if c[0] == "env" and not e and cond_holds(conds, ctx):
            k, _, v = c[1].partition("=")
            put(k, hw_fill(v, hw), "hardware [%s]" % c[2])
    d = p.one("direct_mem_mib")
    if d: put("PS4_DIRECT_MEM_SIZE", d + "m", "profile")
    h = p.one("host_import_mib")
    if h and gpu == "anyps5": put("APS5_HOST_IMPORT_MIB", h, "profile")
    r = p.one("title_resolution")
    if r: put("NPS5_TITLE_RESOLUTION", r, "profile")
    so = p.one("scene_open_key")
    if so: put("NPS5_SCENE_OPEN_KEY", so, "profile")
    # launch-time shader preparation (docs: games/README.md "Nothing compiles while you play")
    for key, word in (("shader_prepare", "NPS5_SHADER_PREPARE"), ("shader_prepare_always", "KYTY_SHADER_PREPARE_ALWAYS"),
                      ("scene_tag_skip", "NPS5_SCENE_TAG_SKIP")):
        v = p.one(key)
        if v: put(word, v, "profile")
    for c in p.get("framerate_block"):
        w = " ".join(c).split(); bc = [x[6:] for x in w[1:] if x.startswith("build=")]
        if not bc or cond_holds([("build", bc[0])], ctx):
            put("NPS5_FRAMERATE_BLOCK", w[0], "profile"); break
    for c in p.get("env"):
        kv = c[0]; when = c[1] if len(c) > 1 else "-"
        conds, e = parse_when(when)
        if e: raise Err("env row %s: %s" % (kv, e))
        if cond_holds(conds, ctx):
            k, _, v = kv.partition("=")
            put(k, hw_fill(v, hw), "env" + ("" if not conds else " [%s]" % when))
    forb = [c[0] for c in p.get("env_forbidden") if c]
    for k in order:
        v, layer = out[k]
        for f in forb:
            if forbidden_match("%s=%s" % (k, v), f):
                raise Err("%s=%s (%s) is refused by env_forbidden %s" % (k, v, layer, f), 78)
        e = mem_limit_error(k, v, mem_limit_for(hw))
        if e: raise Err(e, 78)
    return [(k, out[k][0], out[k][1]) for k in order]


# ---------------------------------------------------------------- CLI
def cmd_list(environ=None):
    allp = find_all(environ)
    if not allp:
        print("no profiles found in %s" % ":".join(search_path(environ))); return 0
    print("%-10s %-6s %-7s %-6s %-12s %s" % ("id", "stage", "gpu", "build", "source", "name"))
    for tid in sorted(allp):
        for i, (d, prov) in enumerate(allp[tid]):
            try: p = Profile(d, prov)
            except OSError as e: print("%-10s unreadable: %s" % (tid, e)); continue
            src = ("provisional" if prov else "repo") + ("" if i == 0 else " (shadowed)")
            print("%-10s %-6s %-7s %-6s %-12s %s" % (tid, p.one("stage", "-"), p.one("gpu", "-"), p.one("package", "-"), src, p.one("name", "")))
    return 0


def cmd_check(ids, environ=None):
    schema = load_schema(); allp = find_all(environ); bad = 0
    hwerrs = check_hw_defaults()
    for e in hwerrs: print(e)
    bad += bool(hwerrs)
    if not ids: ids = sorted(allp)
    for tid in ids:
        hits = allp.get(tid)
        if not hits: print("%s: error: no profile found" % tid); bad += 1; continue
        p = Profile(*hits[0]); errs, warns = check_profile(p, schema)
        for w in warns: print(w)
        for e in errs: print(e)
        print("%s: %s (%d errors, %d warnings)%s" % (tid, "FAIL" if errs else "ok", len(errs), len(warns), " [provisional]" if p.provisional else ""))
        bad += bool(errs)
    return 1 if bad else 0


def main(argv, environ=None):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0 if argv else 64
    try:
        if argv[0] == "list": return cmd_list(environ)
        if argv[0] == "check": return cmd_check(argv[1:], environ)
        tid, rest = argv[0], argv[1:]
        p = Profile(*find_profile(tid, environ))
        opts, pos, i = {}, [], 0
        while i < len(rest):
            a = rest[i]
            if a in ("--env", "--explain"): opts[a] = True
            elif a in ("--gpu", "--scene", "--build", "--class", "--hw"):
                if i + 1 >= len(rest): raise Err("%s needs a value" % a)
                opts[a] = rest[i + 1]; i += 1
            elif a.startswith("--"): raise Err("unknown option %s" % a)
            else: pos.append(a)
            i += 1
        if "--env" in opts:
            for k, v, layer in compose_env(p, opts.get("--gpu"), opts.get("--scene"), opts.get("--build"), opts.get("--class"),
                                           resolve_hw(opts.get("--hw"), environ)):
                print("%s=%s%s" % (k, v, "\t# " + layer if "--explain" in opts else ""))
            return 0
        if "--scene" in opts:
            row = scene_lookup(p.scenes(), opts["--scene"])
            if row is None: raise Err("scene %s is not defined in %s/scenes.tsv" % (opts["--scene"], p.id))
            for k, v in scene_dict(row).items(): print("%s=%s" % (k, v))
            return 0
        if len(pos) == 1:
            v = p.get(pos[0])
            for c in v: print("\t".join(c))
            return 0 if v else 1
        raise Err("usage: game.py <id> <key> | <id> --env [...] | <id> --scene <name> | list | check [id]", 64)
    except Err as e:
        print("game.py: %s" % e, file=sys.stderr); return e.code
    except OSError as e:
        print("game.py: %s" % e, file=sys.stderr); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
