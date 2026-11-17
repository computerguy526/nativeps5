#!/usr/bin/env python3
"""nps5 patch: apply a title's patches (games/<id>/patches.py) to a COPY of a translated binary. Python 3, stdlib only.

  patch.py <id> --list                              patches, purpose, binding now/goal, review, setting, checked
  patch.py <id> --check <native> [names]            resolve and assert every site, write nothing
  patch.py <id> <native> <name>[,<name>=ARG...] [-o COPY] [--lineage NAME] [--anchor symbol|lineage] [--force]
  patch.py --docs [--out FILE]                      regenerate docs/agents/game-patches.md from every BINDING

Sites are anchored by symbol: guest function _fn_0x<entry> + byte offset + asserted original word. The binary's
embedded build ID (nps5_input_sha256) must match BUILDS. Every word must be the original or already the new value;
anything else refuses the whole set and nothing is written (fail closed). The copy is re-signed ad hoc and a
<copy>.patches.json sidecar records what was applied. The source binary is only ever read.
`--lineage NAME` (symbol-less binaries only) falls back to the LINEAGES shift table of patches.py.
Exit codes: 0 ok, 1 refused / mismatch, 2 usage or file problem."""
import ast, fcntl, hashlib, json, mmap, os, shutil, struct, subprocess, sys

TOOL_VERSION = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
MH_MAGIC_64, LC_SEGMENT_64, LC_SYMTAB, LC_CODE_SIGNATURE = 0xfeedfacf, 0x19, 0x2, 0x1d
BUILD_SYMBOL = "_nps5_input_sha256"
FIELD_KINDS = ("f32hi", "u16")
SIGN = True  # tests on synthetic files switch this off


class PatchError(Exception):
    pass


# ---------------------------------------------------------------- Mach-O (thin arm64 64-bit, read only)
class MachO:
    def __init__(self, path):
        self.path = path
        try:
            self.f = open(path, "rb")
            self.m = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        except (OSError, ValueError) as e:
            raise PatchError("cannot read %s: %s" % (path, e))
        m = self.m
        if len(m) < 32 or struct.unpack_from("<I", m, 0)[0] != MH_MAGIC_64:
            raise PatchError("%s is not a thin 64-bit Mach-O file" % path)
        try:
            self._parse(m)
        except struct.error as e:
            self.close()
            raise PatchError("%s: corrupt Mach-O header (%s)" % (path, e))

    def _parse(self, m):
        ncmds = struct.unpack_from("<I", m, 16)[0]
        self.segs, self.symtab, self.sig = [], None, None
        off = 32
        for _ in range(ncmds):
            if off + 8 > len(m):
                raise PatchError("truncated load commands")
            cmd, size = struct.unpack_from("<II", m, off)
            if size < 8:
                raise PatchError("bad load command")
            if cmd == LC_SEGMENT_64:
                name = m[off + 8:off + 24].rstrip(b"\0").decode("ascii", "replace")
                vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", m, off + 24)
                self.segs.append((name, vmaddr, vmsize, fileoff, filesize))
            elif cmd == LC_SYMTAB:
                self.symtab = struct.unpack_from("<IIII", m, off + 8)  # symoff nsyms stroff strsize
            elif cmd == LC_CODE_SIGNATURE:
                self.sig = struct.unpack_from("<II", m, off + 8)       # dataoff datasize
            off += size

    def close(self):
        self.m.close(); self.f.close()

    def fileoff(self, vm, length=4):
        for _, va, vs, fo, fs in self.segs:
            if va <= vm and vm + length <= va + min(vs, fs) and fs:
                return fo + (vm - va)
        raise PatchError("address 0x%x is not backed by a segment of %s" % (vm, os.path.basename(self.path)))

    def word(self, vm):
        try:
            return struct.unpack_from("<I", self.m, self.fileoff(vm))[0]
        except struct.error:
            raise PatchError("address 0x%x is beyond the end of %s" % (vm, os.path.basename(self.path)))

    def symbols(self, names):
        """{name: address} of the wanted exact names (defined symbols only). Names seen twice with different
        addresses are ambiguous and raise."""
        if not self.symtab or not names:
            return {}
        symoff, nsyms, stroff, strsize = self.symtab
        m, want = self.m, {}
        if symoff + nsyms * 16 > len(m) or stroff + strsize > len(m):
            raise PatchError("%s: symbol table extends beyond the end of the file" % os.path.basename(self.path))
        for n in names:
            pat = b"\0" + n.encode() + b"\0"
            i = m.find(pat, stroff, stroff + strsize)
            while i >= 0:
                want[i + 1 - stroff] = n
                i = m.find(pat, i + 1, stroff + strsize)
        out = {}
        if not want:
            return out
        for strx, ntype, _sect, _desc, value in struct.iter_unpack("<IBBHQ", m[symoff:symoff + nsyms * 16]):
            n = want.get(strx)
            if n is not None and (ntype & 0x0e) == 0x0e:
                if n in out and out[n] != value:
                    raise PatchError("symbol %s is defined twice with different addresses" % n)
                out[n] = value
        return out

    def build_id(self):
        sym = self.symbols([BUILD_SYMBOL]).get(BUILD_SYMBOL)
        if sym is None:
            return None
        o = self.fileoff(sym, 64)
        s = bytes(self.m[o:o + 64]).decode("ascii", "replace")
        return s if len(s) == 64 and all(c in "0123456789abcdef" for c in s) else None


# ---------------------------------------------------------------- patches.py (data only)
def load_patches(path):
    """patches.py parsed with ast.literal_eval: names must be literals; any other statement is refused."""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
    except (OSError, SyntaxError) as e:
        raise PatchError("cannot read %s: %s" % (path, e))
    d = {}
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # docstring
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            raise PatchError("%s:%d: patches.py is data only (NAME = literal)" % (path, getattr(node, "lineno", 0)))
        try:
            d[node.targets[0].id] = ast.literal_eval(node.value)
        except ValueError:
            raise PatchError("%s:%d: %s is not a literal (no code, no arithmetic)" % (path, node.lineno, node.targets[0].id))
    for need in ("BUILDS", "CUTS", "BINDING"):
        if need not in d:
            raise PatchError("%s: %s missing" % (path, need))
    d.setdefault("PARAM_CUTS", {}); d.setdefault("REQUIRES", {}); d.setdefault("LINEAGES", {}); d.setdefault("FALLBACK_FUNCS", {})
    return d


def find_build(pd, sha):
    hits = [v for v, prefix in pd["BUILDS"].items() if sha.startswith(prefix)]
    if len(hits) != 1:
        raise PatchError("build %s... is not one of this profile's builds (%s)" % (
            sha[:16], ", ".join("%s=%s" % kv for kv in pd["BUILDS"].items())))
    return hits[0]


def parse_request(spec):
    """'dr,nt,lowres=1280x720' -> [('dr', None), ('nt', None), ('lowres', '1280x720')]"""
    out = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        n, _, a = item.partition("=")
        out.append((n, a or None))
    if not out:
        raise PatchError("no patch name given")
    if len({n for n, _ in out}) != len(out):
        raise PatchError("a patch is named twice")
    return out


def _params(pdef, arg, name):
    """parameter values of a param patch: 'WxH' -> {'W':..,'H':..}; None allowed (check only)."""
    if arg is None:
        return None
    names = pdef["param"].split("x")
    parts = arg.split("x")
    if len(parts) != len(names) or not all(p.isascii() and p.isdigit() and len(p) <= 6 for p in parts):
        raise PatchError("%s takes %s (got %r)" % (name, pdef["param"], arg))
    return dict(zip(names, (int(p) for p in parts)))


def _field(kind, v, shift, what):
    if kind == "u16":
        if not 0 <= v < 65536:
            raise PatchError("%s: %d does not fit 16 bits" % (what, v))
        return v << shift
    if kind == "f32hi":
        bits = struct.unpack("<I", struct.pack("<f", float(v)))[0]
        if bits & 0xffff:
            raise PatchError("%s: %d is not a float32 with zero low 16 bits (1280, 1440, 1600, 1920 are)" % (what, v))
        return (bits >> 16) << shift
    raise PatchError("unknown field kind %s" % kind)


def _decode(kind, cur, shift):
    v = (cur >> shift) & 0xffff
    return v if kind == "u16" else int(round(struct.unpack("<f", struct.pack("<I", v << 16))[0]))


class Site:
    def __init__(self, patch, fn, off, old, new, note, vm, anchor):
        self.patch, self.fn, self.off, self.old, self.new, self.note, self.vm, self.anchor = patch, fn, off, old, new, note, vm, anchor
        self.spec = None  # (base, fields) for a param site


def expand(pd, build, requests):
    """-> [(patch, params|None, [(fn, off, old, new_or_spec, note)])]; fails closed on unknown names / bad args."""
    cuts, pcuts = pd["CUTS"].get(build, {}), pd["PARAM_CUTS"].get(build, {})
    out = []
    for name, arg in requests:
        if name in cuts:
            if arg is not None:
                raise PatchError("patch %s takes no argument" % name)
            out.append((name, None, list(cuts[name])))
        elif name in pcuts:
            out.append((name, _params(pcuts[name], arg, name), list(pcuts[name]["sites"])))
        else:
            raise PatchError("no patch %r for build %s (known: %s)" % (name, build, ", ".join(sorted(list(cuts) + list(pcuts)))))
    return out


def resolve(pd, mo, build, requests, lineage=None, anchor=None):
    """All sites with host addresses. Symbol anchoring unless the binary has no symbols and `lineage` is given."""
    exp = expand(pd, build, requests)
    fns = sorted({s[0] for _, _, sites in exp for s in sites})
    syms = mo.symbols(["_fn_0x%x" % g for g in fns]) if anchor != "lineage" else {}
    use_syms = bool(syms) and anchor != "lineage"
    if use_syms:
        miss = [g for g in fns if "_fn_0x%x" % g not in syms]
        if miss:
            raise PatchError("guest function(s) %s have no symbol in %s; refusing (the binary is not the build this patch was made for)"
                             % (", ".join("0x%x" % g for g in miss), os.path.basename(mo.path)))
    elif lineage is None:
        raise PatchError("%s has no _fn_0x<entry> symbols (not linked with NPS5_KEEP_SYMS=1); name the link with --lineage (%s)"
                         % (os.path.basename(mo.path), ", ".join(sorted(pd["LINEAGES"])) or "none defined"))
    elif lineage not in pd["LINEAGES"]:
        raise PatchError("unknown lineage %r (known: %s)" % (lineage, ", ".join(sorted(pd["LINEAGES"]))))
    res = []
    for name, params, sites in exp:
        lst = []
        for fn, off, old, new, note in sites:
            if use_syms:
                vm, anc = syms["_fn_0x%x" % fn] + off, "symbol:_fn_0x%x" % fn
            else:
                base = pd["FALLBACK_FUNCS"].get(fn)
                if base is None:
                    raise PatchError("fn 0x%x has no FALLBACK_FUNCS entry for lineage fallback" % fn)
                vm, anc = base + pd["LINEAGES"][lineage] + off, "lineage:%s" % lineage
            s = Site(name, fn, off, old, None, note, vm, anc)
            if isinstance(new, tuple):
                s.spec = new
                if params is not None:
                    base_w, fields = new
                    w = base_w
                    for pn, kind, shift in fields:
                        w |= _field(kind, params[pn], shift, "%s" % name)
                    s.new = w
            else:
                s.new = new
            lst.append(s)
        res.append((name, params, lst))
    seen = {}
    for name, _, lst in res:
        for s in lst:
            if s.vm in seen and seen[s.vm] != name:
                raise PatchError("patches %s and %s both change the word at 0x%x" % (seen[s.vm], name, s.vm))
            seen[s.vm] = name
    return res


def site_state(mo, s):
    """('old'|'new'|'other', current word). A param site without parameters is 'new' if it decodes consistently."""
    cur = mo.word(s.vm)
    if cur == s.old:
        return "old", cur
    if s.new is not None:
        return ("new" if cur == s.new else "other"), cur
    if s.spec:
        base, fields = s.spec
        mask = 0
        for _, _, shift in fields:
            mask |= 0xffff << shift
        if cur & ~mask == base:
            return "new", cur
    return "other", cur


def inferred_params(mo, sites):
    """parameter values already in a patched binary (param patch checked without arguments), or None."""
    vals = {}
    for s in sites:
        if not s.spec or mo.word(s.vm) == s.old:
            continue
        cur = mo.word(s.vm)
        for pn, kind, shift in s.spec[1]:
            v = _decode(kind, cur, shift)
            if vals.setdefault(pn, v) != v:
                return None
    return vals or None


def judge(mo, res, pd):
    """per patch: dict(name, state, detail). state: applied | applicable | partial | MISMATCH."""
    rep = []
    for name, params, sites in res:
        st = [site_state(mo, s) for s in sites]
        kinds = [k for k, _ in st]
        if "other" in kinds:
            bad = [(s, c) for s, (k, c) in zip(sites, st) if k == "other"]
            d = "; ".join("fn 0x%x+0x%x at 0x%x holds %08x, expected %08x%s" % (
                s.fn, s.off, s.vm, c, s.old, "" if s.new is None else " or %08x" % s.new) for s, c in bad)
            rep.append(dict(name=name, state="MISMATCH", detail=d))
        elif all(k == "new" for k in kinds):
            det = ""
            if params is None:
                inf = inferred_params(mo, sites)
                if inf:
                    det = "values " + "x".join(str(v) for v in inf.values())
            rep.append(dict(name=name, state="applied", detail=det))
        elif all(k == "old" for k in kinds):
            rep.append(dict(name=name, state="applicable", detail=""))
        else:
            rep.append(dict(name=name, state="partial", detail="%d of %d sites already patched" % (kinds.count("new"), len(kinds))))
    # REQUIRES (a dependency counts as satisfied when requested together or already applied in the binary)
    return rep


# ---------------------------------------------------------------- profile access
def _game_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("nps5_game", os.path.join(REPO, "games", "game.py"))
    g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
    return g


def profile_patches(tid, environ=None):
    g = _game_module()
    try:
        d, _prov = g.find_profile(tid, environ)
    except g.Err as e:
        raise PatchError(str(e))
    p = os.path.join(d, "patches.py")
    if not os.path.isfile(p):
        raise PatchError("%s has no patches.py" % tid)
    return load_patches(p)


def open_binary(pd, path, build_override=None):
    mo = MachO(path)
    sha = mo.build_id()
    if sha is None:
        if build_override and build_override in pd["BUILDS"]:
            return mo, build_override, None
        mo.close()
        raise PatchError("%s carries no %s symbol: cannot identify the build (pass --build <version> with --lineage for a symbol-less binary)"
                         % (os.path.basename(path), BUILD_SYMBOL))
    try:
        return mo, find_build(pd, sha), sha
    except PatchError:
        mo.close(); raise


def dependency_ok(pd, mo, build, requests, lineage, anchor):
    """a REQUIRES dependency must be requested too or already applied in the binary."""
    names = {n for n, _ in requests}
    for n in sorted(names):
        for need in pd["REQUIRES"].get(n, []):
            if need in names:
                continue
            res = resolve(pd, mo, build, [(need, None)], lineage, anchor)
            st = judge(mo, res, pd)[0]["state"]
            if st != "applied":
                raise PatchError("%s requires %s, which is neither requested nor already applied (%s)" % (n, need, st))


# ---------------------------------------------------------------- commands
def cmd_check(pd, path, names, lineage=None, anchor=None, build=None, out=sys.stdout):
    mo, ver, sha = open_binary(pd, path, build)
    try:
        if names:
            reqs = parse_request(names)
        else:
            reqs = [(n, None) for n in list(pd["CUTS"].get(ver, {})) + list(pd["PARAM_CUTS"].get(ver, {}))]
        res = resolve(pd, mo, ver, reqs, lineage, anchor)
        rep = judge(mo, res, pd)
        print("%s: build %s (%s) anchor %s" % (os.path.basename(path), ver, (sha or "?")[:16],
              res[0][2][0].anchor if res and res[0][2] else "-"), file=out)
        bad = 0
        for r in rep:
            print("  %-8s %-10s %s" % (r["name"], r["state"], r["detail"]), file=out)
            bad += r["state"] == "MISMATCH"
        for n, _ in reqs:
            for need in pd["REQUIRES"].get(n, []):
                if need not in {x for x, _ in reqs}:
                    try:
                        dependency_ok(pd, mo, ver, [(n, None)], lineage, anchor)
                    except PatchError as e:
                        print("  %-8s NOTE       %s" % (n, e), file=out)
        return 1 if bad else 0
    finally:
        mo.close()


def default_out(path, reqs):
    tag = "+".join(n + (a.replace("=", "") if a else "") for n, a in reqs)
    return "%s+%s" % (path, tag)


def apply(pd, src, reqs, out_path=None, lineage=None, anchor=None, build=None, force=False, tid=None):
    """Patch a copy of `src`. Returns the sidecar dict. Nothing is written unless every site verifies."""
    mo, ver, sha = open_binary(pd, src, build)
    try:
        res = resolve(pd, mo, ver, reqs, lineage, anchor)
        for name, params, _ in res:
            if name in {n for n, c in pd["PARAM_CUTS"].get(ver, {}).items()} and params is None:
                raise PatchError("%s needs its argument (%s=%s)" % (name, name, pd["PARAM_CUTS"][ver][name]["param"]))
        rep = judge(mo, res, pd)
        bad = [r for r in rep if r["state"] == "MISMATCH"]
        if bad:
            raise PatchError("refusing, nothing patched: " + " | ".join("%s: %s" % (r["name"], r["detail"]) for r in bad))
        dependency_ok(pd, mo, ver, reqs, lineage, anchor)
        todo = [s for _, _, lst in res for s in lst]
        offs = [(mo.fileoff(s.vm), s) for s in todo]
        out_path = out_path or default_out(src, reqs)
        if os.path.realpath(out_path) == os.path.realpath(src):
            raise PatchError("the output must be a copy, not the source")
        if os.path.exists(out_path) and not force:
            raise PatchError("%s exists (use --force to replace it)" % out_path)
        before = {s.vm: mo.word(s.vm) for s in todo}
    finally:
        mo.close()
    side = out_path + ".patches.json"
    # Exclusive lock (flock: released by the kernel if the process dies), then everything is built beside the output
    # under temporary names and renamed into place last, so an interrupted run leaves no output at all.
    lock = open(out_path + ".lock", "a+")
    tmp = "%s.tmp%d" % (out_path, os.getpid()); tmp_side = tmp + ".json"; locked = False
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); locked = True
        except OSError:
            raise PatchError("another patch run is writing %s; refusing" % out_path)
        if os.path.exists(out_path) and not force:
            raise PatchError("%s exists (use --force to replace it)" % out_path)
        if subprocess.run(["cp", "-c", src, tmp], stderr=subprocess.DEVNULL).returncode != 0:
            shutil.copyfile(src, tmp)
        with open(tmp, "r+b") as f:
            for fo, s in offs:
                f.seek(fo); f.write(struct.pack("<I", s.new))
        mc = MachO(tmp)
        try:
            for _, s in offs:
                if mc.word(s.vm) != s.new:
                    raise PatchError("verify failed at 0x%x after writing" % s.vm)
        finally:
            mc.close()
        # codesign names the signature after the file minus its last extension (".tmp<pid>" is that extension here), so the
        # identifier is the same as when signing the final name
        r = subprocess.run(["codesign", "-f", "-s", "-", tmp], capture_output=True, text=True) if SIGN else None
        if r is not None and r.returncode != 0:
            raise PatchError("codesign failed: " + r.stderr.strip())
        info = dict(tool="nps5 patch", tool_version=TOOL_VERSION, title=tid, build=ver, build_id=sha,
                    source=os.path.basename(src), output=os.path.basename(out_path),
                    anchor_mode=res[0][2][0].anchor.split(":")[0] if res and res[0][2] else None,
                    patches=[dict(name=n, param=(None if p is None else "x".join(str(v) for v in p.values())),
                                  sites=[dict(function="0x%x" % s.fn, offset="0x%x" % s.off, anchor=s.anchor,
                                              address="0x%x" % s.vm, original="%08x" % s.old, new="%08x" % s.new,
                                              was="%08x" % before[s.vm], note=s.note) for s in l]) for n, p, l in res])
        with open(tmp_side, "w") as f:
            json.dump(info, f, indent=1); f.write("\n")
        os.replace(tmp_side, side)
        os.replace(tmp, out_path)
        return info
    finally:
        for p in (tmp, tmp_side):
            if os.path.exists(p):
                os.remove(p)
        if locked:  # only the holder removes the lock file
            try:
                os.remove(out_path + ".lock")
            except OSError:
                pass
        lock.close()


def cmd_list(pd, tid, out=sys.stdout):
    cuts = {n for b in pd["CUTS"].values() for n in b} | {n for b in pd["PARAM_CUTS"].values() for n in b}
    print("%s builds: %s" % (tid, ", ".join("%s=%s" % kv for kv in pd["BUILDS"].items())), file=out)
    for b in pd["BINDING"]:
        s = b.get("setting")
        print("%-8s %s\n         binding now=%s goal=%s | review: %s%s\n         guest: %s\n         why: %s\n         checked: %s%s" % (
            b["what"], b["purpose"], b["now"], b["goal"], b["review"],
            " | requires " + ",".join(pd["REQUIRES"][b["what"]]) if b["what"] in pd["REQUIRES"] else "",
            b.get("guest", "-"), b["why"], b["checked"],
            "\n         setting: %s (%s)" % (s["name"], s["state"]) if s else ""), file=out)
        cuts.discard(b["what"])
    if cuts:
        print("WARNING: patches without a BINDING row: " + ", ".join(sorted(cuts)), file=out)
    return 1 if cuts else 0


def docs_text(profiles):
    L = ["# Game patches", "",
         "Generated by `python3 scripts/nps5lib/patch.py --docs` from the `BINDING` rows of every `games/<title id>/patches.py`.",
         "Do not edit by hand. A patch changes OUR translated binary (never the dump); sites are anchored by guest function",
         "symbol + offset + asserted original word; the tool refuses on any mismatch. Binding levels, loosest first:",
         "`host` (PS5 / AGC semantics, no patch), `engine`, `game` (a guest function found by symbol), `build` (fixed offsets, redone per update).", ""]
    for tid, pd in profiles:
        L += ["## %s" % tid, "", "Builds: " + ", ".join("`%s` (eboot `%s`)" % kv for kv in pd["BUILDS"].items()), "",
              "| Patch | Purpose | Binding now / goal | Review | Setting | Checked |", "|---|---|---|---|---|---|"]
        for b in pd["BINDING"]:
            s = b.get("setting")
            L.append("| `%s` | %s | %s / %s | %s | %s | %s |" % (
                b["what"], b["purpose"], b["now"], b["goal"], b["review"],
                "`%s` (%s)" % (s["name"], s["state"]) if s else "-", b["checked"]))
        L.append("")
        for b in pd["BINDING"]:
            L += ["- `%s`: %s. Guest: %s.%s" % (b["what"], b["why"], b.get("guest", "-"),
                  " Requires `%s`." % "`, `".join(pd["REQUIRES"][b["what"]]) if b["what"] in pd["REQUIRES"] else "")]
        L.append("")
    return "\n".join(L)


def cmd_docs(out_file=None, environ=None):
    g = _game_module()
    profiles, claimed = [], {}
    for tid, hits in sorted(g.find_all(environ).items()):
        p = os.path.join(hits[0][0], "patches.py")
        if os.path.isfile(p):
            pd = load_patches(p)
            for ver, eb in pd["BUILDS"].items():
                for otid, oeb in claimed.items():
                    if eb[:16] == oeb[:16]:
                        raise PatchError("build %s is claimed by both %s and %s" % (eb, otid, tid))
                claimed[tid] = eb
            profiles.append((tid, pd))
    out_file = out_file or os.path.join(REPO, "docs", "agents", "game-patches.md")
    with open(out_file, "w") as f:
        f.write(docs_text(profiles))
    print("wrote %s" % out_file)
    return 0


def main(argv, environ=None):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0 if argv else 2
    opts, pos, i = {}, [], 0
    VAL = ("-o", "--lineage", "--anchor", "--build", "--out")
    while i < len(argv):
        a = argv[i]
        if a in VAL:
            if i + 1 >= len(argv):
                print("patch: %s needs a value" % a, file=sys.stderr); return 2
            opts[a] = argv[i + 1]; i += 1
        elif a in ("--list", "--check", "--docs", "--force"):
            opts[a] = True
        elif a.startswith("-") and a != "-":
            print("patch: unknown option %s" % a, file=sys.stderr); return 2
        else:
            pos.append(a)
        i += 1
    try:
        if "--docs" in opts:
            return cmd_docs(opts.get("--out"), environ)
        if not pos:
            print("patch: a title id is required (no default game); see --help", file=sys.stderr); return 2
        tid, rest = pos[0], pos[1:]
        pd = profile_patches(tid, environ)
        if "--list" in opts:
            return cmd_list(pd, tid)
        lin, anc, bld = opts.get("--lineage"), opts.get("--anchor"), opts.get("--build")
        if anc not in (None, "symbol", "lineage"):
            raise PatchError("--anchor is symbol or lineage")
        if "--check" in opts:
            if not rest:
                raise PatchError("usage: patch.py <id> --check <native> [names]")
            return cmd_check(pd, rest[0], rest[1] if len(rest) > 1 else None, lin, anc, bld)
        if len(rest) != 2:
            raise PatchError("usage: patch.py <id> <native> <name>[,<name>=ARG...] [-o COPY]")
        info = apply(pd, rest[0], parse_request(rest[1]), opts.get("-o"), lin, anc, bld, "--force" in opts, tid)
        for p in info["patches"]:
            print("%s%s: %d sites" % (p["name"], "=" + p["param"] if p["param"] else "", len(p["sites"])))
        print("wrote %s (re-signed) + %s.patches.json" % (info["output"], info["output"]))
        return 0
    except PatchError as e:
        print("patch: %s" % e, file=sys.stderr); return 1
    except OSError as e:
        print("patch: %s" % e, file=sys.stderr); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
