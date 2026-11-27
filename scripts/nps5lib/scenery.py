#!/usr/bin/env python3
"""scenery.py: the player's `scenery` setting. Python 3, standard library only.

  this module is used by scripts/nps5lib/run.py (`nps5 run <id> --scenery full|reduced|minimal`).

What it does: for a level that has a profile row, it writes a THINNED COPY of the player's own level file into the player's
scratch folder (<scratch>/scenery/<title id>/<preset>/<same relative path as in the game folder>) and hands that folder to the
runtime as NPS5_OVERLAY_DIR, which mounts it read-only over /app0. The game folder is never written. `full` (the default) writes
and sets nothing.

Profile rows (games/<id>/game.tsv, schema type `scenery`; the profile carries no asset names):
  scenery <level> <preset> src=<sha256 of the level file this row was made for> class=<object class> drop=<len>:<hash>[,...]
  An object is a drop candidate when its class equals `class=` and its model name's first <len> characters hash (sha256, first 16
  hex digits) to <hash>. An optional last word `keep=<len>:<hash>[,...]` (same hashing) excludes models from that set: an object whose
  model name matches a keep key is never removed (used for models that carry collision shapes). Names are matched by hash in the same spirit as shader keys; the names themselves stay out of the repository.

Level attribute fixes (the same overlay, schema type `levelfix`; default ON for a title that has rows, NPS5_LEVELFIX=0 opts out):
  levelfix <level> src=<sha256> class=<object class> object=<len>:<hash> attr=<Attribute> old=<value> new=<value>
  The object is found by its <Name>: the LAST <len> characters hash (sha256, first 16 hex digits) to <hash>, so no game name is in the
  repository. Exactly one object of that class must match, it must carry exactly one <attr> whose text is `old`, and that text is
  replaced by `new` (same bytes everywhere else, CRLF kept). Any other outcome is a Skip: no overlay, one warning, game starts unchanged.
  Fixes and scenery thinning of one level go into ONE overlay file (fix first, then thinning; both on = both applied).

Category rows (perf mode, schema types `scenery` extended, `sceneryset`, `drawskip`):
  scenery <level> <cat> src=<sha256 of that file> class=bg drop=<len>:<hash>[,...] [file=<len>:<hash16>] [strip=suspend_group]
  cat = flowers | grass | moss | other. `file=` names a sub-level file of the level folder by the hash of its path below the level folder
  (lower case; the names are GUIDs and stay out of the repository); absent = level.lvx. `strip=suspend_group` lets an object that is
  referenced only by a streaming group's <Targets> list be removed (its id is taken out of that list). The legacy 5-word rows
  (cat column = reduced / minimal) keep parsing and give the same bytes as before.
  sceneryset <preset> <cat>[=<keep percent>][,<cat>...]    reduced | minimal | balanced | performance: which categories a preset removes;
  `=N` keeps N percent of that category's removable objects (the same ones every run: ranked by sha256 of the object id).
  drawskip <level> <cat>[+<cat>] vs=<hash16> ps=<hash16|->  (Kyty instant skip table, written to KYTY_SCENE_SKIP_FILE by run.py)
  Setting grammar: full | reduced | minimal | balanced | performance | custom:<cat>[+<cat>...].

Safety rules (every one is a test): whole <LevelObject> blocks are removed and every other byte is kept (CRLF, order, resources);
an object that anything else mentions by id (a link, any reference other than its children's Parent) is kept; an object with a
kept child is kept (no Parent dangles); objects without a model, or of another class, are kept. The source must hash to `src=`
(another game version is never edited blind: nothing is written and the reason is returned); the result is checked (object count,
well-formed XML, every Parent still resolves) before it is written, and written atomically."""
import collections, hashlib, json, os, re, sys, tempfile, time, xml.etree.ElementTree as ET

PRESETS = ("full", "reduced", "minimal", "balanced", "performance")
LEGACY_PRESETS = ("reduced", "minimal")                  # rows with the preset in the category column (v1.4.x form)
CATS = ("flowers", "grass", "moss", "other")
SG_CLASS = "suspend_group"
LEVELS_REL = "data/prein/levels/%s"
SUBLEVEL_DEPTH = 3                                       # path components of a sub-level file below the level folder
OBJ = re.compile(rb"[ \t]*<LevelObject>.*?</LevelObject>\r?\n", re.S)
UUID = re.compile(rb"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
LEVEL_REL = "data/prein/levels/%s/level.lvx"          # where a level's file lives in the game folder (and in the overlay)


class Skip(Exception):
    """The overlay is not made; the message says why (the launch goes on without it)."""


def name_hash(prefix):
    """The 16-hex-digit key of a model-name prefix, as profile rows carry it."""
    return hashlib.sha256(prefix.encode()).hexdigest()[:16]


def row_key(length, prefix):
    return "%d:%s" % (length, name_hash(prefix))


def parse_drop(word):
    """'drop=10:abcd...,9:ef01...' -> [(10, 'abcd...'), ...]; raises ValueError on a bad word."""
    if not word.startswith("drop="):
        raise ValueError("drop=<len>:<hash16>[,...] expected")
    out = []
    for item in word[5:].split(","):
        m = re.fullmatch(r"([1-9]\d?):([0-9a-f]{16})", item)
        if not m:
            raise ValueError("bad drop item %r (<len>:<16 hex digits>)" % item)
        out.append((int(m.group(1)), m.group(2)))
    return out


def _level_name(level):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", level):
        raise ValueError("level must be a plain folder name (no leading dot, no path)")


def parse_keep(word):
    """'keep=20:abcd...[,...]' -> [(20, 'abcd...'), ...]; raises ValueError on a bad word."""
    if not word.startswith("keep="):
        raise ValueError("keep=<len>:<hash16>[,...] expected")
    return parse_drop("drop=" + word[5:])


def parse_row(cols):
    """Columns after the key -> dict(level, preset, cat, src, cls, drops, file, strip, keep). Raises ValueError with the reason.
    Legacy form (column 2 = reduced | minimal, exactly 5 words): preset set, cat None. Category form (column 2 = a category): cat set,
    preset None, optional file=<len>:<hash16> (None = level.lvx) and strip=suspend_group."""
    w = " ".join(cols).split()
    if len(w) >= 2 and w[1] in CATS:
        return _parse_cat_row(w)
    if len(w) not in (5, 6):
        raise ValueError("needs <level> <preset|category> src=<sha256> class=<class> drop=<len>:<hash>[,...] [keep=<len>:<hash>[,...]] (legacy) "
                         "or <level> <category> src=<sha256> class=<class> drop=<len>:<hash>[,...] [file=<len>:<hash16>] [strip=suspend_group]")
    level, preset, src, cls, drop = w[:5]
    keeps = parse_keep(w[5]) if len(w) == 6 else []      # v1.4.4: models that are never removed (collision shapes); `keeps` = list, `keep` = a percent
    _level_name(level)
    if preset not in LEGACY_PRESETS:
        raise ValueError("column 2 must be reduced or minimal, or a category (%s); full is the absence of a row" % ", ".join(CATS))
    if not re.fullmatch(r"src=[0-9a-f]{64}", src):
        raise ValueError("src= must be a lower-case sha256")
    if not re.fullmatch(r"class=[A-Za-z0-9_]+", cls):
        raise ValueError("class=<word> expected")
    return dict(level=level, preset=preset, cat=None, src=src[4:], cls=cls[6:], drops=parse_drop(drop), file=None, strip=False, keep=0, keeps=keeps)


def _parse_cat_row(w):
    if len(w) < 5:
        raise ValueError("needs <level> <category> src=<sha256> class=<class> drop=<len>:<hash>[,...] [file=<len>:<hash16>] [strip=suspend_group]")
    level, cat, src, cls, drop = w[:5]
    _level_name(level)
    if not re.fullmatch(r"src=[0-9a-f]{64}", src):
        raise ValueError("src= must be a lower-case sha256")
    if not re.fullmatch(r"class=[A-Za-z0-9_]+", cls):
        raise ValueError("class=<word> expected")
    file = None; strip = False
    seen = set()
    for x in w[5:]:
        k = x.split("=", 1)[0]
        if k in seen:
            raise ValueError("%s= given twice" % k)
        seen.add(k)
        if k == "file":
            if not re.fullmatch(r"file=[1-9]\d{0,2}:[0-9a-f]{16}", x):
                raise ValueError("file=<len>:<16 hex digits> expected")
            file = x[5:]
        elif x == "strip=" + SG_CLASS:
            strip = True
        else:
            raise ValueError("unknown word %r (file=<len>:<hash16> or strip=%s)" % (x, SG_CLASS))
    return dict(level=level, preset=None, cat=cat, src=src[4:], cls=cls[6:], drops=parse_drop(drop), file=file, strip=strip, keep=0)


def parse_set(cols):
    """`sceneryset` columns -> dict(preset, cats={cat: keep percent}); plain cat = keep 0 (all removed). Raises ValueError."""
    w = " ".join(cols).split()
    if len(w) != 2:
        raise ValueError("needs <preset> <cat>[=<keep percent>][,<cat>...]")
    preset, spec = w
    if preset not in PRESETS or preset == "full":
        raise ValueError("preset must be reduced, minimal, balanced or performance")
    cats = collections.OrderedDict()
    for item in spec.split(","):
        m = re.fullmatch(r"([a-z]+)(?:=(\d{1,2}))?", item)
        if not m or m.group(1) not in CATS:
            raise ValueError("bad item %r (%s, optionally =<keep percent 0-99>)" % (item, "|".join(CATS)))
        if m.group(1) in cats:
            raise ValueError("%s given twice" % m.group(1))
        cats[m.group(1)] = int(m.group(2) or 0)
    return dict(preset=preset, cats=cats)


def parse_custom(setting):
    """'custom:flowers+grass' -> {cat: 0}. Raises ValueError."""
    if not setting.startswith("custom:"):
        raise ValueError("not a custom setting")
    out = collections.OrderedDict()
    for c in setting[7:].split("+"):
        if c not in CATS:
            raise ValueError("custom:<cat>[+<cat>...] with cat in %s" % ", ".join(CATS))
        out[c] = 0
    return out


def valid_setting(setting):
    if setting in PRESETS:
        return True
    try:
        parse_custom(setting); return True
    except ValueError:
        return False


def parse_skip(cols):
    """`drawskip` columns -> dict(level, cats (tuple), vs, ps (None for '-')). Raises ValueError."""
    w = " ".join(cols).split()
    if len(w) != 4:
        raise ValueError("needs <level> <cat>[+<cat>] vs=<hash16> ps=<hash16|->")
    level, cats, vs, ps = w
    _level_name(level)
    cl = cats.split("+")
    if not cl or any(c not in CATS for c in cl) or len(set(cl)) != len(cl):
        raise ValueError("categories: %s joined by +" % ", ".join(CATS))
    if not re.fullmatch(r"vs=[0-9a-f]{16}", vs):
        raise ValueError("vs=<16 hex digits> expected")
    if not re.fullmatch(r"ps=([0-9a-f]{16}|-)", ps):
        raise ValueError("ps=<16 hex digits> or ps=- expected")
    return dict(level=level, cats=tuple(sorted(cl)), vs=vs[3:], ps=None if ps == "ps=-" else ps[3:])


def mask_rows(rows, cats, skip_levels=()):
    """Category rows whose category is thinned by `cats` ({cat: keep percent}); each copy carries its keep. Pure."""
    return [dict(r, keep=cats[r["cat"]]) for r in rows if r.get("cat") in cats and cats[r["cat"]] < 100 and r["level"] not in skip_levels]


def effective_rows(rows, sets, setting):
    """The rows a setting uses: legacy rows of that preset, plus category rows of the preset's `sceneryset` (or of `custom:`) for the
    levels no legacy row covers. rows = all parsed `scenery` rows, sets = parsed `sceneryset` rows. `full` -> []. Pure."""
    if setting == "full":
        return []
    legacy = [r for r in rows if r["preset"] == setting]
    if setting.startswith("custom:"):
        cats = parse_custom(setting)
    else:
        spec = next((x for x in sets if x["preset"] == setting), None)
        cats = spec["cats"] if spec else {}
    return legacy + mask_rows(rows, cats, {r["level"] for r in legacy})


def preset_mask(sets, setting):
    """The category mask ({cat: keep percent}, absent = untouched) a setting stands for; used by the live keys."""
    if setting == "full":
        return {}
    if setting.startswith("custom:"):
        return dict(parse_custom(setting))
    spec = next((x for x in sets if x["preset"] == setting), None)
    return dict(spec["cats"]) if spec else {}


FIX_VALUE = r"[A-Za-z0-9_.+-]+"


def parse_fix(cols):
    """Columns after the key -> dict(level, src, cls, obj=(len, hash), attr, old, new). Raises ValueError with the reason."""
    w = " ".join(cols).split()
    if len(w) != 7:
        raise ValueError("needs <level> src=<sha256> class=<class> object=<len>:<hash16> attr=<Attribute> old=<value> new=<value>")
    level, src, cls, ob, attr, old, new = w
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", level):
        raise ValueError("level must be a plain folder name (no leading dot, no path)")
    if not re.fullmatch(r"src=[0-9a-f]{64}", src):
        raise ValueError("src= must be a lower-case sha256")
    if not re.fullmatch(r"class=[A-Za-z0-9_]+", cls):
        raise ValueError("class=<word> expected")
    m = re.fullmatch(r"object=([1-9]\d{0,2}):([0-9a-f]{16})", ob)
    if not m:
        raise ValueError("object=<len>:<16 hex digits> expected")
    if not re.fullmatch(r"attr=[A-Za-z][A-Za-z0-9_]*", attr):
        raise ValueError("attr=<Attribute> expected")
    for k, v in (("old", old), ("new", new)):
        if not re.fullmatch(k + "=" + FIX_VALUE, v):
            raise ValueError("%s=<value> expected (letters, digits and _ . + - only)" % k)
    if old[4:] == new[4:]:
        raise ValueError("old and new are the same")
    return dict(level=level, src=src[4:], cls=cls[6:], obj=(int(m.group(1)), m.group(2)), attr=attr[5:], old=old[4:], new=new[4:])


def fixes_for(profile_rows):
    """profile_rows: the `levelfix` rows' columns (Profile.get('levelfix')) -> parsed fixes."""
    return [parse_fix(c) for c in profile_rows]


def apply_fix(data, fx):
    """-> data with one attribute value replaced. Pure. Raises Skip unless exactly one object matches and carries the old value once."""
    n, h = fx["obj"]
    hits = []
    for m in OBJ.finditer(data):
        b = m.group(0)
        if _field(b, b"Lox") != fx["cls"]:
            continue
        name = _field(b, b"Name")
        if name and len(name) >= n and name_hash(name[-n:]) == h:
            hits.append(m)
    if len(hits) != 1:
        raise Skip("%s: level fix: %d objects match (exactly 1 needed)" % (fx["level"], len(hits)))
    blk = hits[0].group(0)
    tag = fx["attr"].encode()
    pat = re.compile(rb"(<%s type=\"\w+\">)([^<]*)(</%s>)" % (re.escape(tag), re.escape(tag)))
    found = list(pat.finditer(blk))
    if len(found) != 1:
        raise Skip("%s: level fix: the object has %d <%s> values (exactly 1 needed)" % (fx["level"], len(found), fx["attr"]))
    if found[0].group(2).decode() != fx["old"]:
        raise Skip("%s: level fix: %s is %r, expected %r (another game version?)" % (fx["level"], fx["attr"], found[0].group(2).decode(), fx["old"]))
    a, b2 = hits[0].span()
    ns = blk[:found[0].start(2)] + fx["new"].encode() + blk[found[0].end(2):]
    out = data[:a] + ns + data[b2:]
    if out.count(b"<LevelObject>") != data.count(b"<LevelObject>") or len(out) - len(data) != len(fx["new"]) - len(fx["old"]):
        raise Skip("%s: level fix: result check failed" % fx["level"])
    try:
        ET.fromstring(out)
    except ET.ParseError as e:
        raise Skip("%s: level fix: result is not well-formed XML (%s)" % (fx["level"], e))
    return out


def rows_for(profile_rows, preset):
    """profile_rows: the `scenery` rows' columns (Profile.get('scenery')). -> parsed rows of one preset."""
    return [r for r in (parse_row(c) for c in profile_rows) if r["preset"] == preset]


def _field(b, k):
    m = re.search(rb"<%s>(.*?)</%s>" % (k, k), b)
    return m.group(1).decode() if m else None


def _model(b):
    m = re.search(rb'<Odx type="\w+">(.*?)</Odx>', b)
    return m.group(1).decode() if m else None


def matches(model, drops, keeps=()):
    hit = lambda ks: any(len(model) >= n and name_hash(model[:n]) == h for n, h in ks)
    return hit(drops) and not hit(keeps)


TARGETS = re.compile(rb'(<Targets type="String">)(.*?)(</Targets>)', re.S)


def _keep_count(n, keep):
    """How many of n removable objects stay for a keep percentage (round half up)."""
    return min(n, (n * keep + 50) // 100)


def plan_rules(data, rules):
    """-> (objects, removable uuid set). rules: dicts with cls, drops and optionally strip (bool) and keep (percent of the removable
    objects of that rule that stay, default 0 = all removed; same ones every run: ranked by sha256 of the object id). An object belongs to the first rule that
    matches its class and model. Pure."""
    objs = []
    for m in OBJ.finditer(data):
        b = m.group(0)
        o = dict(span=m.span(), uuid=(_field(b, b"Uuid") or "").upper(), cls=_field(b, b"Lox"),
                 parent=(_field(b, b"Parent") or "").upper() or None, model=_model(b), targets=[])
        if o["cls"] == SG_CLASS:
            for t in TARGETS.finditer(b):
                o["targets"].append((m.start() + t.start(2), m.start() + t.end(2)))
        objs.append(o)
    kids = collections.defaultdict(list)
    for o in objs:
        if o["parent"]:
            kids[o["parent"]].append(o["uuid"])
    mentions = collections.Counter(x.decode().upper() for x in UUID.findall(data))
    in_targets = collections.Counter()          # mentions inside streaming groups' <Targets> text
    for o in objs:
        for a, b in o["targets"]:
            in_targets.update(x.decode().upper() for x in UUID.findall(data[a:b]))
    by_rule = collections.defaultdict(list)
    for o in objs:
        u = o["uuid"]
        if not u or not o["model"]:
            continue
        for i, r in enumerate(rules):
            if o["cls"] == r["cls"] and matches(o["model"], r["drops"], r.get("keeps", ())):
                break
        else:
            continue
        # mentions in its own block (<Uuid>, a prefab's own <RootUuid>) and one <Parent> per child are not references;
        # any other mention (a link, a prefab root, anything) means something depends on it: keep.
        # With strip, a mention inside a streaming group's <Targets> list is no reference (the id is taken out of the list).
        own = sum(1 for x in UUID.findall(data[o["span"][0]:o["span"][1]]) if x.decode().upper() == u)
        ext = mentions[u] - own - len(kids[u]) - (in_targets[u] if rules[i].get("strip") else 0)
        if ext != 0:
            continue
        by_rule[i].append(u)
    cand = set()
    for i, us in by_rule.items():
        keep = rules[i].get("keep", 0)
        if keep <= 0:
            cand.update(us)
        elif keep < 100:
            ranked = sorted(us, key=lambda u: hashlib.sha256(u.encode()).hexdigest())
            cand.update(ranked[_keep_count(len(us), keep):])      # the first `keep` percent in that order stay
    changed = True
    while changed:                         # an object with a kept child stays
        changed = False
        for u in list(cand):
            if any(k not in cand for k in kids[u]):
                cand.discard(u); changed = True
    return objs, cand


def plan(data, cls, drops, keeps=(), strip=False, keep=None):
    """-> (objects, removable uuid set) for one rule. `keeps` = models never removed (collision shapes). Pure."""
    return plan_rules(data, [dict(cls=cls, drops=drops, strip=strip, keep=keep or 0, keeps=keeps)])


def _targets_list(text):
    try:
        v = json.loads(text.decode())
    except ValueError:
        raise Skip("a streaming group's Targets is not a JSON list")
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise Skip("a streaming group's Targets is not a list of ids")
    return v


def apply(data, objs, cand):
    """Remove the objects of `cand` and take their ids out of every streaming group's <Targets> list (re-written with json.dumps; a list
    that loses nothing keeps its bytes). Every other byte is kept."""
    edits = []                                             # (start, end, replacement) in file order, disjoint
    for o in objs:
        if o["uuid"] in cand:
            edits.append((o["span"][0], o["span"][1], b""))
        else:
            for a, b in o["targets"]:
                lst = _targets_list(data[a:b])
                kept = [x for x in lst if x.upper() not in cand]
                if len(kept) != len(lst):
                    edits.append((a, b, json.dumps(kept).encode()))
    out, last = [], 0
    for a, b, r in edits:
        out.append(data[last:a]); out.append(r); last = b
    out.append(data[last:])
    return b"".join(out)


def check_output(src, out, n_src, n_removed, removed=None):
    """Raise Skip unless the result is a sane level: count, well-formed, every Parent that resolved still does, every Targets list is a JSON
    list, and (removed given) no removed id appears anywhere. -> number of streaming groups whose Targets list is now empty."""
    if out.count(b"<LevelObject>") != n_src - n_removed:
        raise Skip("object count mismatch after thinning")
    try:
        root = ET.fromstring(out)
    except ET.ParseError as e:
        raise Skip("thinned level is not well-formed XML (%s)" % e)
    ids = {(e.text or "").upper() for e in root.iter("Uuid")}
    src_ids = {x.decode().upper() for x in re.findall(rb"<Uuid>(.*?)</Uuid>", src)}
    for e in root.iter("Parent"):
        p = (e.text or "").upper()
        if p in src_ids and p not in ids:
            raise Skip("a Parent no longer resolves")
    empty = 0
    for m in TARGETS.finditer(out):
        if not _targets_list(m.group(2)):
            empty += 1
    if removed:
        left = {x.decode().upper() for x in UUID.findall(out)} & set(removed)
        if left:
            raise Skip("%d removed object ids are still mentioned in the result" % len(left))
    return empty


def thin_rows(data, rules, info=None):
    """-> (output bytes, objects, removed). rules as plan_rules. Raises Skip on a sanity failure. info (dict) gets `emptied`: streaming
    groups whose Targets list became empty (they are kept: their triggers stay valid)."""
    objs, cand = plan_rules(data, rules)
    out = apply(data, objs, cand)
    empty = check_output(data, out, len(objs), len(cand), cand)
    if info is not None:
        info["emptied"] = empty - sum(1 for t in TARGETS.finditer(data) if not _targets_list(t.group(2)))
    return out, len(objs), len(cand)


def thin(data, row):
    """-> (output bytes, objects, removed) for one row (legacy or category). Raises Skip on a sanity failure."""
    return thin_rows(data, [row])


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def overlay_dir(scratch, tid, preset, fixed=False):
    """Scenery only: <scratch>/scenery/<id>/<preset> (unchanged). With level fixes the folder is `fix` (preset full) or `<preset>+fix`."""
    name = preset if not fixed else ("fix" if preset == "full" else preset + "+fix")
    return os.path.join(scratch, "scenery", tid, name)


def rules_id(rows, fixes=()):
    return sha256_bytes(json.dumps([[(r["level"], r["src"], r["cls"], r["drops"]) + ((r.get("cat"), r.get("keep", 0), r.get("file"), r.get("strip"))
                                                                                    if r.get("cat") else ((r["keeps"],) if r.get("keeps") else ())) for r in rows],
                                    [(f["level"], f["src"], f["cls"], f["obj"], f["attr"], f["old"], f["new"]) for f in fixes]],
                                   sort_keys=True).encode())[:16]


def file_key(rel):
    """The `file=` key of a path below the level folder: <len>:<hash16> of the lower-case, '/'-separated path."""
    r = rel.replace(os.sep, "/").lower()
    return row_key(len(r), r)


def level_files(app0, level):
    """-> {file key: relative path} of every *.lvx below the level folder (depth <= SUBLEVEL_DEPTH, no dot files or folders)."""
    root = os.path.join(app0, LEVELS_REL % level)
    out = collections.defaultdict(list)
    for r, ds, fs in os.walk(root):
        ds[:] = sorted(d for d in ds if not d.startswith("."))
        for f in sorted(fs):
            if f.startswith(".") or not f.lower().endswith(".lvx"):
                continue
            rel = os.path.relpath(os.path.join(r, f), root).replace(os.sep, "/")
            if rel.count("/") + 1 <= SUBLEVEL_DEPTH:
                out[file_key(rel)].append(rel)
    return out


def resolve_file(app0, level, key):
    """-> the relative path (below the level folder) of the one file whose `file=` key is `key`; None = level.lvx. Raises Skip unless
    exactly one file matches."""
    if key is None:
        return "level.lvx"
    hits = level_files(app0, level).get(key, [])
    if len(hits) != 1:
        raise Skip("%s: file=%s: %d matching files (exactly 1 needed; another game version?)" % (level, key, len(hits)))
    return hits[0]


def _jobs(rows, fixes):
    """Group rows and fixes by (level, file key); a level fix always belongs to level.lvx. Insertion order kept."""
    jobs = collections.OrderedDict()
    for r in rows:
        jobs.setdefault((r["level"], r.get("file")), ([], []))[0].append(r)
    for f in fixes:
        jobs.setdefault((f["level"], None), ([], []))[1].append(f)
    return jobs


def generate(app0, scratch, tid, preset, rows, fixes=()):
    """Make (or reuse) the overlay folder of one preset (+ level fixes). -> (folder, [log lines]). Raises Skip with the reason when
    nothing can be made. A file that cannot be made (source is another game version, not readable, sanity check failed, file= matches
    no or two files) is left out alone: a stale file from an older run is removed so a wrong overlay can never be mounted, the others
    are still written and the reason is a `WARNING:` line. Only when every file failed is it a Skip. rows = scenery rows of this
    preset (may be empty; category rows carry `keep`), fixes = parsed levelfix rows. The name of the folder is `preset`."""
    base = overlay_dir(scratch, tid, preset, bool(fixes))
    meta_path = base + ".meta.json"        # beside the folder, never inside it: everything inside is mounted over the game folder
    try: meta = json.loads(_read(meta_path))
    except (OSError, ValueError): meta = {}
    lines, fresh, errors = [], {}, []
    for (lv, fkey), (rs, fs) in _jobs(rows, fixes).items():
        mkey = lv if fkey is None else "%s:%s" % (lv, fkey)
        label = lv if fkey is None else "%s file=%s" % (lv, fkey)
        try:
            rel_in = resolve_file(app0, lv, fkey)
        except Skip as e:
            errors.append(str(e)); lines.append("WARNING: %s" % e); continue
        rel = LEVELS_REL % lv + "/" + rel_in
        dst = os.path.join(base, rel)
        src_path = os.path.join(app0, rel)
        try:
            data = _read(src_path)
        except OSError as e:
            _drop(dst); msg = "%s: source not readable (%s)" % (label, e.strerror or e); errors.append(msg); lines.append("WARNING: " + msg); continue
        got = sha256_bytes(data)
        bad = next((x for x in rs + fs if got != x["src"]), None)
        if bad:
            _drop(dst)
            msg = "%s: source is not the file this setting was made for (sha256 %s..., expected %s...); no overlay" % (label, got[:12], bad["src"][:12])
            errors.append(msg); lines.append("WARNING: " + msg); continue
        key = rules_id(rs, fs)
        m = meta.get(mkey, {})
        if m.get("src") == got and m.get("rules") == key and os.path.isfile(dst) and m.get("out") == sha256_bytes(_read(dst)):
            fresh[mkey] = m; lines.append("%s: reused (%s)" % (label, m.get("what", "overlay"))); continue
        try:
            out, what = data, []
            for fx in fs:
                out = apply_fix(out, fx); what.append("fixed %s" % fx["attr"])
            if rs:
                info = {}
                out, n, k = thin_rows(out, rs, info); what.append("%d of %d objects removed" % (k, n))
                if info.get("emptied"):
                    what.append("%d streaming groups now empty (kept)" % info["emptied"])
        except Skip as e:
            _drop(dst); msg = "%s: %s" % (label, e); errors.append(msg); lines.append("WARNING: " + msg); continue
        _atomic_write(dst, out)
        what = ", ".join(what)
        fresh[mkey] = dict(src=got, rules=key, out=sha256_bytes(out), what=what)
        lines.append("%s: generated (%s, %d -> %d bytes)" % (label, what, len(data), len(out)))
    if errors and not fresh:
        raise Skip(errors[0] if len(errors) == 1 else "%s (+%d more)" % (errors[0], len(errors) - 1))
    _atomic_write(meta_path, json.dumps(fresh, sort_keys=True, indent=1).encode())
    return base, lines


MERGE_MAX = 20000


def merge_overlay(gen_dir, user_dir, out_dir):
    """The runtime mounts ONE overlay folder, so a player's own overlay and the generated one are combined into <out_dir> made of symlinks
    (the runtime follows them; nothing is copied). A file the player's overlay has at the same relative path wins (case-insensitive,
    as the console file system): the generated file is left out and named in the returned list. -> (out_dir, [rel paths not applied]).
    Raises Skip when either folder is unusable or too big."""
    import shutil
    def walk(root):
        out = {}
        for r, ds, fs in os.walk(root, followlinks=True):      # a symlinked sub-folder of the player's overlay is part of it (a loop ends at MERGE_MAX)
            ds[:] = [d for d in ds if not d.startswith(".")]
            for f in fs:
                if f.startswith("."): continue
                p = os.path.join(r, f); out[os.path.relpath(p, root)] = p
                if len(out) > MERGE_MAX: raise Skip("overlay %s has over %d files" % (root, MERGE_MAX))
        return out
    if not os.path.isdir(user_dir):
        raise Skip("NPS5_OVERLAY_DIR %s is not a folder" % user_dir)
    mine, theirs = walk(gen_dir), walk(user_dir)
    low = {k.lower() for k in theirs}
    skipped = sorted(k for k in mine if k.lower() in low)
    shutil.rmtree(out_dir, ignore_errors=True)
    for k, p in list(theirs.items()) + [(k, p) for k, p in mine.items() if k not in skipped]:
        d = os.path.join(out_dir, k); os.makedirs(os.path.dirname(d), exist_ok=True)
        os.symlink(os.path.abspath(p), d)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir, skipped


def _drop(path):
    try: os.unlink(path)
    except OSError: pass


# ---------------------------------------------------------------- live keys (`--scenery-keys on`)
def mask_text(mask):
    """{cat: keep percent} -> 'flowers=100,grass=0,...' (every category, 100 = untouched). The one spelling the skip table and the state
    file use."""
    return ",".join("%s=%d" % (c, mask.get(c, 100)) for c in CATS)


def parse_state(text):
    """The state file Kyty writes on a key press (atomically): lines `seq=<n>`, `preset=<name|custom>`, `<cat>=<keep percent 0-100>`.
    -> dict(seq, preset, mask {cat: keep} for every category named). Raises ValueError (a bad file is ignored by the watcher)."""
    kv = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        k, sep, v = ln.partition("=")
        if not sep:
            raise ValueError("line %r is not key=value" % ln[:40])
        kv[k.strip()] = v.strip()
    if not re.fullmatch(r"\d{1,9}", kv.get("seq", "")):
        raise ValueError("seq=<n> missing")
    mask = {}
    for c in CATS:
        if c in kv:
            if not re.fullmatch(r"(100|\d{1,2})", kv[c]):
                raise ValueError("%s=%r is not a percent 0-100" % (c, kv[c]))
            mask[c] = int(kv[c])
    return dict(seq=int(kv["seq"]), preset=kv.get("preset", "custom"), mask=mask)


def write_skip_table(path, presets, start, skips=()):
    """The table Kyty reads (KYTY_SCENE_SKIP_FILE; atomic). presets = [(name, {cat: keep})] in F3 cycle order (full is implicit first),
    start = the launch mask, skips = parsed `drawskip` rows. Lines (tab separated):
      preset <name> <cat=keep,...>      start <cat=keep,...>      skip <level> <cat+cat> <vs hash16> <ps hash16 or ->"""
    out = ["# nps5 scenery table: read by Kyty (KYTY_SCENE_SKIP_FILE)"]
    out += ["preset\t%s\t%s" % (n, mask_text(m)) for n, m in presets]
    out.append("start\t" + mask_text(start))
    out += ["skip\t%s\t%s\t%s\t%s" % (k["level"], "+".join(k["cats"]), k["vs"], k["ps"] or "-") for k in skips]
    _atomic_write(path, ("\n".join(out) + "\n").encode())


class Live:
    """The folder the runtime mounts when the keys are on: <scratch>/scenery/<id>/live, one SYMLINK per (level, file) that has a category
    row or a level fix, at the same relative path as in the game folder. Each link points at the variant of the current mask (a
    generated file), at the fix-only file, or at the player's own game file when nothing applies. The runtime mounted the paths at
    start and opens whatever a link points at on the next level load. A link is swapped atomically (new link + os.replace)."""

    def __init__(self, app0, scratch, tid, rows, fixes=()):
        self.app0, self.scratch, self.tid = app0, scratch, tid
        self.rows = [r for r in rows if r.get("cat")]
        self.fixes = list(fixes)
        self.dir = os.path.join(scratch, "scenery", tid, "live")
        self.warnings, self.items, self.mask = [], [], None
        for (lv, fkey) in _jobs(self.rows, self.fixes):
            try:
                self.items.append((lv, resolve_file(app0, lv, fkey)))
            except Skip as e:
                self.warnings.append(str(e))

    @staticmethod
    def variant(mask):
        parts = ["%s%s" % (c, "" if mask[c] == 0 else "~%d" % mask[c]) for c in CATS if c in mask and mask[c] < 100]
        return "cats-" + "+".join(parts) if parts else "full"

    def link_path(self, lv, rel):
        return os.path.join(self.dir, LEVELS_REL % lv, rel)

    def repoint(self, mask):
        """Generate what the mask needs (reused when current) and swap every link. -> (links changed, [log lines]). Never raises for a
        file that cannot be made: that link points at the player's own file."""
        mask = {c: v for c, v in mask.items() if c in CATS and v < 100}
        rows_m = mask_rows(self.rows, mask)
        base, lines = None, []
        if rows_m or self.fixes:
            try:
                base, lines = generate(self.app0, self.scratch, self.tid, self.variant(mask), rows_m, self.fixes)
            except Skip as e:
                lines = ["WARNING: %s" % e]
        changed = 0
        for lv, rel in self.items:
            dst = os.path.join(base, LEVELS_REL % lv, rel) if base else None
            target = dst if dst and os.path.isfile(dst) else os.path.join(self.app0, LEVELS_REL % lv, rel)
            changed += swap_link(self.link_path(lv, rel), os.path.abspath(target))
        self.mask = dict(mask)
        return changed, lines


def swap_link(link, target):
    """Make `link` a symlink to `target`, atomically (a reader opening `link` in a loop never sees it missing). -> 1 if it changed."""
    try:
        if os.readlink(link) == target:
            return 0
    except OSError:
        pass
    d = os.path.dirname(link)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".lnk-%d-%s" % (os.getpid(), os.path.basename(link)))
    _drop(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, link)
    return 1


class Watcher:
    """Polls the state file Kyty writes; on a new `seq` it repoints the live links. poll() does one check (tests call it); start() runs it
    on a daemon thread every `interval` seconds. A state file that does not parse is ignored with ONE warning per distinct content."""

    def __init__(self, live, state_path, log=None, interval=0.5):
        self.live, self.path, self.interval = live, state_path, interval
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.seq, self._last, self._stop = None, None, None

    def poll(self):
        try:
            with open(self.path, "rb") as f:
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return None
        if text == self._last:
            return None
        self._last = text
        try:
            st = parse_state(text)
        except ValueError as e:
            self.log("nps5 run: scenery: state file ignored (%s)" % e); return None
        if st["seq"] == self.seq:
            return None
        self.seq = st["seq"]
        t0 = time.time()
        try:
            changed, lines = self.live.repoint(st["mask"])
        except OSError as e:
            self.log("nps5 run: scenery: seq %d not staged (%s)" % (st["seq"], e.strerror or e)); return None
        for l in lines:
            if l.startswith("WARNING"): self.log("nps5 run: scenery: " + l)
        self.log("nps5 run: scenery: seq %d staged (%d files, %.1f s)" % (st["seq"], len(self.live.items), time.time() - t0))
        return st

    def start(self):
        import threading
        _drop(self.path)                      # a state file from an earlier run is not this run's
        self._stop = threading.Event()
        def loop():
            while not self._stop.wait(self.interval):
                try: self.poll()
                except Exception as e: self.log("nps5 run: scenery: watcher: %s" % e)
        threading.Thread(target=loop, daemon=True, name="scenery-watch").start()
        return self

    def stop(self):
        if self._stop: self._stop.set()
