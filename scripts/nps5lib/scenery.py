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
  hex digits) to <hash>. Names are matched by hash in the same spirit as shader keys; the names themselves stay out of the repository.

Level attribute fixes (the same overlay, schema type `levelfix`; default ON for a title that has rows, NPS5_LEVELFIX=0 opts out):
  levelfix <level> src=<sha256> class=<object class> object=<len>:<hash> attr=<Attribute> old=<value> new=<value>
  The object is found by its <Name>: the LAST <len> characters hash (sha256, first 16 hex digits) to <hash>, so no game name is in the
  repository. Exactly one object of that class must match, it must carry exactly one <attr> whose text is `old`, and that text is
  replaced by `new` (same bytes everywhere else, CRLF kept). Any other outcome is a Skip: no overlay, one warning, game starts unchanged.
  Fixes and scenery thinning of one level go into ONE overlay file (fix first, then thinning; both on = both applied).

Safety rules (every one is a test): whole <LevelObject> blocks are removed and every other byte is kept (CRLF, order, resources);
an object that anything else mentions by id (a link, any reference other than its children's Parent) is kept; an object with a
kept child is kept (no Parent dangles); objects without a model, or of another class, are kept. The source must hash to `src=`
(another game version is never edited blind: nothing is written and the reason is returned); the result is checked (object count,
well-formed XML, every Parent still resolves) before it is written, and written atomically."""
import collections, hashlib, json, os, re, tempfile, xml.etree.ElementTree as ET

PRESETS = ("full", "reduced", "minimal")
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


def parse_row(cols):
    """Columns after the key -> dict(level, preset, src, cls, drops). Raises ValueError with the reason."""
    w = " ".join(cols).split()
    if len(w) != 5:
        raise ValueError("needs <level> <preset> src=<sha256> class=<class> drop=<len>:<hash>[,...]")
    level, preset, src, cls, drop = w
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", level):
        raise ValueError("level must be a plain folder name (no leading dot, no path)")
    if preset not in PRESETS or preset == "full":
        raise ValueError("preset must be reduced or minimal (full is the absence of a row)")
    if not re.fullmatch(r"src=[0-9a-f]{64}", src):
        raise ValueError("src= must be a lower-case sha256")
    if not re.fullmatch(r"class=[A-Za-z0-9_]+", cls):
        raise ValueError("class=<word> expected")
    return dict(level=level, preset=preset, src=src[4:], cls=cls[6:], drops=parse_drop(drop))


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


def matches(model, drops):
    return any(len(model) >= n and name_hash(model[:n]) == h for n, h in drops)


def plan(data, cls, drops):
    """-> (objects, removable uuid set). objects: dicts with span/uuid/parent. Pure."""
    objs = []
    for m in OBJ.finditer(data):
        b = m.group(0)
        objs.append(dict(span=m.span(), uuid=(_field(b, b"Uuid") or "").upper(), cls=_field(b, b"Lox"),
                         parent=(_field(b, b"Parent") or "").upper() or None, model=_model(b)))
    kids = collections.defaultdict(list)
    for o in objs:
        if o["parent"]:
            kids[o["parent"]].append(o["uuid"])
    mentions = collections.Counter(x.decode().upper() for x in UUID.findall(data))
    cand = set()
    for o in objs:
        u = o["uuid"]
        if not u or o["cls"] != cls or not o["model"] or not matches(o["model"], drops):
            continue
        # mentions in its own block (<Uuid>, a prefab's own <RootUuid>) and one <Parent> per child are not references;
        # any other mention (a link, a prefab root, anything) means something depends on it: keep
        own = sum(1 for x in UUID.findall(data[o["span"][0]:o["span"][1]]) if x.decode().upper() == u)
        if mentions[u] - own - len(kids[u]) != 0:
            continue
        cand.add(u)
    changed = True
    while changed:                         # an object with a kept child stays
        changed = False
        for u in list(cand):
            if any(k not in cand for k in kids[u]):
                cand.discard(u); changed = True
    return objs, cand


def apply(data, objs, cand):
    out, last = [], 0
    for o in objs:
        if o["uuid"] in cand:
            a, b = o["span"]; out.append(data[last:a]); last = b
    out.append(data[last:])
    return b"".join(out)


def check_output(src, out, n_src, n_removed):
    """Raise Skip unless the result is a sane level: count, well-formed, every Parent that resolved still does."""
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


def thin(data, row):
    """-> (output bytes, objects, removed). Raises Skip on a sanity failure."""
    objs, cand = plan(data, row["cls"], row["drops"])
    out = apply(data, objs, cand)
    check_output(data, out, len(objs), len(cand))
    return out, len(objs), len(cand)


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
    return sha256_bytes(json.dumps([[(r["level"], r["src"], r["cls"], r["drops"]) for r in rows],
                                    [(f["level"], f["src"], f["cls"], f["obj"], f["attr"], f["old"], f["new"]) for f in fixes]],
                                   sort_keys=True).encode())[:16]


def generate(app0, scratch, tid, preset, rows, fixes=()):
    """Make (or reuse) the overlay folder of one preset (+ level fixes). -> (folder, [log lines]). Raises Skip with the reason when
    nothing can be made; in that case nothing under the folder is changed for the failing level (a stale file from an older run is
    removed so a wrong overlay can never be mounted). rows = scenery rows of this preset (may be empty), fixes = parsed levelfix rows."""
    base = overlay_dir(scratch, tid, preset, bool(fixes))
    meta_path = base + ".meta.json"        # beside the folder, never inside it: everything inside is mounted over the game folder
    try: meta = json.loads(_read(meta_path))
    except (OSError, ValueError): meta = {}
    lines, fresh = [], {}
    levels = []
    for r in list(rows) + list(fixes):
        if r["level"] not in levels: levels.append(r["level"])
    for lv in levels:
        rs = [r for r in rows if r["level"] == lv]; fs = [f for f in fixes if f["level"] == lv]
        rel = LEVEL_REL % lv
        dst = os.path.join(base, rel)
        src_path = os.path.join(app0, rel)
        try:
            data = _read(src_path)
        except OSError as e:
            _drop(dst); raise Skip("%s: source not readable (%s)" % (lv, e.strerror or e))
        got = sha256_bytes(data)
        for x in rs + fs:
            if got != x["src"]:
                _drop(dst); raise Skip("%s: source is not the file this setting was made for (sha256 %s..., expected %s...); no overlay" % (lv, got[:12], x["src"][:12]))
        key = rules_id(rs, fs)
        m = meta.get(lv, {})
        if m.get("src") == got and m.get("rules") == key and os.path.isfile(dst) and m.get("out") == sha256_bytes(_read(dst)):
            fresh[lv] = m; lines.append("%s: reused (%s)" % (lv, m.get("what", "overlay"))); continue
        try:
            out, what = data, []
            for fx in fs:
                out = apply_fix(out, fx); what.append("fixed %s" % fx["attr"])
            n = k = 0
            if rs:
                out, n, k = thin(out, rs[0]); what.append("%d of %d objects removed" % (k, n))
        except Skip as e:
            _drop(dst); raise Skip("%s: %s" % (lv, e))
        _atomic_write(dst, out)
        what = ", ".join(what)
        fresh[lv] = dict(src=got, rules=key, out=sha256_bytes(out), what=what)
        lines.append("%s: generated (%s, %d -> %d bytes)" % (lv, what, len(data), len(out)))
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
