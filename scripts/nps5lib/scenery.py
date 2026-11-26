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


def overlay_dir(scratch, tid, preset):
    return os.path.join(scratch, "scenery", tid, preset)


def rules_id(rows):
    return sha256_bytes(json.dumps([(r["level"], r["src"], r["cls"], r["drops"]) for r in rows], sort_keys=True).encode())[:16]


def generate(app0, scratch, tid, preset, rows):
    """Make (or reuse) the overlay folder of one preset. -> (folder, [log lines]). Raises Skip with the reason when nothing can be
    made; in that case nothing under the folder is changed for the failing level (a stale file from an older run is removed so a
    wrong overlay can never be mounted)."""
    base = overlay_dir(scratch, tid, preset)
    meta_path = base + ".meta.json"        # beside the folder, never inside it: everything inside is mounted over the game folder
    try: meta = json.loads(_read(meta_path))
    except (OSError, ValueError): meta = {}
    lines, fresh = [], {}
    for r in rows:
        rel = LEVEL_REL % r["level"]
        dst = os.path.join(base, rel)
        src_path = os.path.join(app0, rel)
        try:
            data = _read(src_path)
        except OSError as e:
            _drop(dst); raise Skip("%s: source not readable (%s)" % (r["level"], e.strerror or e))
        got = sha256_bytes(data)
        if got != r["src"]:
            _drop(dst); raise Skip("%s: source is not the file this setting was made for (sha256 %s..., expected %s...); no overlay" % (r["level"], got[:12], r["src"][:12]))
        key = rules_id([r])
        m = meta.get(r["level"], {})
        if m.get("src") == got and m.get("rules") == key and os.path.isfile(dst) and m.get("out") == sha256_bytes(_read(dst)):
            fresh[r["level"]] = m; lines.append("%s: reused (%d of %d objects removed)" % (r["level"], m["removed"], m["objects"])); continue
        try:
            out, n, k = thin(data, r)
        except Skip as e:
            _drop(dst); raise Skip("%s: %s" % (r["level"], e))
        _atomic_write(dst, out)
        fresh[r["level"]] = dict(src=got, rules=key, out=sha256_bytes(out), objects=n, removed=k)
        lines.append("%s: generated (%d of %d objects removed, %d -> %d bytes)" % (r["level"], k, n, len(data), len(out)))
    _atomic_write(meta_path, json.dumps(fresh, sort_keys=True, indent=1).encode())
    return base, lines


def _drop(path):
    try: os.unlink(path)
    except OSError: pass
