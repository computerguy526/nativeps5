#!/usr/bin/env python3 -I
"""metalexpected: the PRESENCE manifest of our custom Metal rows (USER 2026-11-27: "foliage rows silently stopped hitting" must fail).

games/<id>/metal-expected.tsv says, for every (row key, GPU class g16 / g17 / g18) that is or was ever a candidate, whether the list must
ship it (`ship`) or why it is intentionally off (`off:<reason>`: slower-g18, dropped-correctness:<commit>, mesh-pending, untested-g17, ...).
HASHES AND WORDS ONLY (no shader text, no game paths). Header:
  format  nps5-metal-expected  1
  title   <id> <version>
  prev    <tag> <sha256 of the prev=1 lines>      the previous release tag and a seal over the rows that shipped there
  columns kind  key  class  state  prev           kind compute|gfx, key = kk msl key | kk_vs/kk_fs, prev = 1 when that tag shipped it on that class
Lines are sorted (kind, key, class) and unique. A class shipping a key = a non-kk list row whose class is `*` or that generation (an @osNN pin counts).

check (tests/test_metal_expected.py, scripts/check.sh --tests) FAILS when
  (a) a `ship` row is not in the list for that class, or the list has no valid .verified record (sha256 of the list, reproduced N of N, per class counts) /
      the row's evidence says pendval;
  (b) a list row (class `*` expands to g16 g17 g18) is missing from the manifest, or the manifest says off for a row the list ships;
  (c) a row shipped at the previous tag is not `ship` and has no real `off:<reason>`, or a prev=1 line was removed (seal) / the tag has a row the manifest lacks;
  (d) the manifest is out of order, duplicated, or malformed.
A row that lands in the list after the manifest FAILS (b) until the manifest is updated:
  python3 scripts/nps5lib/metalexpected.py update <game id>            add new list rows as `ship`, flip gaps, keep every off:<reason>
  python3 scripts/nps5lib/metalexpected.py update <game id> --off KIND:KEY:CLASS=off:<reason> ...   (needed when a shipped row leaves a class)
  python3 scripts/nps5lib/metalexpected.py check <game id>
"""
import hashlib, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import metal  # noqa: E402

NAME = "metal-expected.tsv"
CLASSES = ("g16", "g17", "g18")
STATE_RE = re.compile(r"^(?:ship|off:[a-z0-9][a-z0-9._:-]*)$")
PLACEHOLDER_REASONS = ("off:unknown", "off:tbd", "off:todo", "off:?", "off:none", "off:")
KEY_RE = {"compute": re.compile(r"^[0-9a-f]{16}$"), "gfx": re.compile(r"^[0-9a-f]{16}/[0-9a-f]{16}$")}
PREV_TAG = "v1.4.3"


class ExpectedError(Exception):
    pass


# ---------------------------------------------------------------- list -> {(kind, key, class): 'ship' | 'kk'}
def list_states(comp, gfx, classes=CLASSES):
    """The state of every (kind, key, class) the list speaks about: 'ship' (a text row of `*` or that class), 'kk' (only a kk row)."""
    out = {}
    for kind, rows, kf in (("compute", comp, lambda r: r["kk"]), ("gfx", gfx, lambda r: "%s/%s" % (r["kk_vs"], r["kk_fs"]))):
        for r in rows:
            m = metal.CLASS_SPEC_RE.match(r["cls"]) if r["cls"] != "*" else None
            if r["cls"] != "*" and not m:
                raise ExpectedError("class %r of row %s is not a class spec" % (r["cls"], kf(r)))
            gen = m.group(1) if m else None
            for c in classes:
                if gen and gen != c:
                    continue
                k = (kind, kf(r), c)
                st = "kk" if r["mode"] == "kk" else "ship"
                if out.get(k) != "ship":
                    out[k] = st
    return out


def read_lists(game_dir):
    with open(os.path.join(game_dir, metal.LIST_NAME)) as f:
        comp = metal.parse_list(f.read())[1]
    with open(os.path.join(game_dir, metal.GFX_LIST_NAME)) as f:
        gfx = metal.parse_gfx_list(f.read())[1]
    return comp, gfx


def parse_lists_text(cs_text, gfx_text):
    return metal.parse_list(cs_text)[1], metal.parse_gfx_list(gfx_text)[1]


def tag_states(repo, tag, game):
    """-> list_states of the lists at git tag `tag` of repo (None when the tag or the files are not there)."""
    try:
        txt = [subprocess.run(["git", "-C", repo, "show", "%s:games/%s/%s" % (tag, game, n)], capture_output=True, text=True, check=True).stdout
               for n in (metal.LIST_NAME, metal.GFX_LIST_NAME)]
        return list_states(*parse_lists_text(*txt))
    except (subprocess.CalledProcessError, OSError, metal.MetalError):
        return None


# ---------------------------------------------------------------- the manifest file
def seal(prev_lines):
    return hashlib.sha256("\n".join(sorted(prev_lines)).encode()).hexdigest()


def parse_manifest(text):
    """-> (meta, rows) rows = [(kind, key, class, state, prev)] in file order. ExpectedError on a malformed line."""
    meta, rows = {}, []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        if f[0] in ("format", "title", "prev", "columns"):
            meta[f[0]] = tuple(f[1:])
            continue
        if len(f) != 5 or f[0] not in KEY_RE or not KEY_RE[f[0]].match(f[1]) or f[2] not in CLASSES or not STATE_RE.match(f[3]) or f[4] not in ("0", "1"):
            raise ExpectedError("%s line %d: not a manifest row (kind key class state prev): %r" % (NAME, n, line[:100]))
        rows.append((f[0], f[1], f[2], f[3], f[4]))
    if meta.get("format", ())[:2] != ("nps5-metal-expected", "1"):
        raise ExpectedError("%s: missing or unknown `format` line" % NAME)
    return meta, rows


def row_line(r):
    return "\t".join(r)


def render(meta_title, tag, rows, header=()):
    rows = sorted(set(rows), key=lambda r: (r[0], r[1], r[2]))
    prev = ["%s\t%s\t%s" % (r[0], r[1], r[2]) for r in rows if r[4] == "1"]
    out = ["# nativeps5 Metal presence manifest: for every (row key, GPU class) of our custom Metal rows, `ship` (the list must carry it) or `off:<reason>` (why not).",
           "# HASHES AND WORDS ONLY. tests/test_metal_expected.py fails when a ship row is missing from the list, a list row is missing here, or a row of the",
           "# previous release disappears without a reason. Update after a list change: python3 scripts/nps5lib/metalexpected.py update <game id> (see its --help text)."]
    out += list(header)
    out += ["format\tnps5-metal-expected\t1", "title\t" + meta_title, "prev\t%s\t%s" % (tag, seal(prev)), "columns\tkind\tkey\tclass\tstate\tprev"]
    out += [row_line(r) for r in rows]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- the check
def verified_problems(game_dir, comp, gfx):
    """The .verified records of both lists: sha256 of the list, reproduced N of N, per-class counts. -> reasons."""
    bad = []
    for name, rows, kind in ((metal.LIST_NAME, comp, "cs"), (metal.GFX_LIST_NAME, gfx, "gfx")):
        try:
            with open(os.path.join(game_dir, name), "rb") as f:
                data = f.read()
            with open(os.path.join(game_dir, name + ".verified")) as f:
                rec = f.read().splitlines()
        except OSError:
            bad.append("%s has no %s.verified record" % (name, name)); continue
        if "sha256=" + hashlib.sha256(data).hexdigest() not in rec:
            bad.append("%s.verified is for another version of the list (sha256 differs)" % name)
        m = next((re.match(r"^reproduced=(\d+) of (\d+)$", l) for l in rec if l.startswith("reproduced=")), None)
        nt = sum(1 for r in rows if r["mode"] != "kk")
        if not m or int(m.group(1)) != nt or int(m.group(2)) != nt:
            bad.append("%s.verified must say reproduced=%d of %d" % (name, nt, nt))
        for c in ("*",) + CLASSES:
            want = sum(1 for r in rows if r["cls"] == c and r["mode"] != "kk")
            have = next((int(x.group(1)) for l in rec for x in [re.match(r"^class %s %s: (\d+) of \1$" % (re.escape(kind), re.escape(c)), l)] if x), None)
            if want and have is not None and have != want:
                bad.append("%s.verified class %s count %s != %d rows" % (name, c, have, want))
    return bad


def check(manifest_text, comp, gfx, game_dir=None, prev_states=None, classes=CLASSES):
    """-> [reasons]; [] = pass. prev_states = list_states at the previous tag (None: only the seal protects prev rows)."""
    try:
        meta, rows = parse_manifest(manifest_text)
    except ExpectedError as e:
        return [str(e)]
    bad = []
    keys = [(r[0], r[1], r[2]) for r in rows]                                        # (d) order and duplicates
    for a, b in zip(keys, keys[1:]):
        if a == b:
            bad.append("duplicate manifest row %s %s %s" % a)
        elif a > b:
            bad.append("manifest out of order at %s %s %s (sorted by kind, key, class)" % b); break
    man = {(r[0], r[1], r[2]): r for r in rows}
    st = list_states(comp, gfx, classes)
    ship_lists = {k for k, v in st.items() if v == "ship"}
    for k in sorted(ship_lists):                                                      # (b) list row missing from the manifest / flagged off
        if k not in man:
            bad.append("list row %s %s %s is not in the manifest (python3 scripts/nps5lib/metalexpected.py update <id>)" % k)
        elif man[k][3] != "ship":
            bad.append("list ships %s %s %s but the manifest says %s" % (k + (man[k][3],)))
    for k, r in sorted(man.items()):
        if r[3] == "ship" and k not in ship_lists:                                    # (a) ship row absent from the list
            bad.append("manifest says ship for %s %s %s but the list has no row for that class" % k)
        if r[3] in PLACEHOLDER_REASONS:
            bad.append("manifest row %s %s %s: placeholder reason %s" % (k + (r[3],)))
    if game_dir:                                                                       # (a) evidence
        bad += verified_problems(game_dir, comp, gfx)
        for r in gfx:
            if "pendval" in (r.get("evidence") or ""):
                bad.append("gfx row %s/%s %s has evidence pendval (pending validation: must not ship)" % (r["kk_vs"], r["kk_fs"], r["cls"]))
    prev = [r for r in rows if r[4] == "1"]                                            # (c) previous release
    p = meta.get("prev", ())
    if len(p) != 2:
        bad.append("manifest has no `prev <tag> <seal>` line")
    elif p[1] != seal(["%s\t%s\t%s" % (r[0], r[1], r[2]) for r in prev]):
        bad.append("manifest prev=1 rows do not match the seal of %s: a prev row was removed or added by hand" % p[0])
    if prev_states is not None:
        for k, v in sorted(prev_states.items()):
            if v == "ship" and (k not in man or man[k][4] != "1"):
                bad.append("%s shipped %s %s %s but the manifest does not mark it prev=1" % ((p[0] if p else "the previous tag",) + k))
    for r in prev:
        if r[3] != "ship" and not r[3].startswith("off:"):
            bad.append("prev row %s %s %s disappeared without an off:<reason>" % r[:3])
    return bad


# ---------------------------------------------------------------- update (mechanical)
def update(manifest_text, comp, gfx, offs=None, prev_states=None, classes=CLASSES, title=None, tag=PREV_TAG):
    """New manifest text. List rows -> ship; kk-mode rows -> off:slower-<class>; keys the list lost -> need an explicit reason in `offs`
    ({(kind,key,class): 'off:<reason>'}) or ExpectedError; keys new to the manifest that the list does not ship -> off:untested-<class>."""
    offs = offs or {}
    meta, rows = parse_manifest(manifest_text) if manifest_text else ({"title": (title or "?",)}, [])
    man = {(r[0], r[1], r[2]): r for r in rows}
    st = list_states(comp, gfx, classes)
    ks = set(man) | set(st) | set(offs) | set(prev_states or {})
    ks |= {(k[0], k[1], c) for k in list(ks) for c in classes}                       # every key x every class
    new, need = [], []
    for k in sorted(ks):
        old = man.get(k)
        prev = "1" if (old and old[4] == "1") or (prev_states or {}).get(k) == "ship" else "0"
        s = st.get(k)
        if s == "ship":
            state = "ship"
        elif k in offs:
            state = offs[k]
        elif old and old[3].startswith("off:"):
            state = old[3]
        elif s == "kk":
            state = "off:slower-" + k[2]
        elif old and old[3] == "ship":
            need.append(k); continue
        else:
            state = "off:untested-" + k[2]
        new.append((k[0], k[1], k[2], state, prev))
    if need:
        raise ExpectedError("rows left the list without a reason (pass --off KIND:KEY:CLASS=off:<reason>): " + ", ".join("%s:%s:%s" % k for k in need))
    return render(" ".join(meta.get("title", (title or "?",))), meta.get("prev", (tag,))[0], new)


def main(argv):
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in ("check", "update") or len(argv) < 2:
        print(__doc__); return 2
    root = os.path.normpath(os.path.join(HERE, "..", ".."))
    gd = os.path.join(root, "games", argv[1])
    comp, gfx = read_lists(gd)
    path = os.path.join(gd, NAME)
    text = open(path).read() if os.path.exists(path) else ""
    ps = tag_states(root, PREV_TAG, argv[1])
    if argv[0] == "check":
        bad = check(text, comp, gfx, gd, ps)
        for b in bad:
            print("FAIL:", b)
        print("metalexpected: %s" % ("%d problem(s)" % len(bad) if bad else "ok"))
        return 1 if bad else 0
    offs = {}
    for a in argv[2:]:
        if a == "--off":
            continue
        k, _, v = a.partition("=")
        kind, key, cls = k.split(":")
        offs[(kind, key, cls)] = v
    try:
        out = update(text, comp, gfx, offs, ps, title=" ".join(metal.parse_list(open(os.path.join(gd, metal.LIST_NAME)).read())[0]["title"]))
    except ExpectedError as e:
        print("metalexpected:", e); return 1
    open(path, "w").write(out)
    print("metalexpected: wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
