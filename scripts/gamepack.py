#!/usr/bin/env python3
"""gamepack - shrink game files on disk with APFS transparent compression (stdlib only).

APFS (decmpfs) cannot compress a file over 2 GiB: afsctool refuses, ditto --hfsCompression silently skips anything over
~512 MB. The big game containers (UE IoStore .ucas, 2-64 GB) are read by the game as single files, so extracting assets
would not help. gamepack therefore stores such a file <f> as

    <f>.nps5seg/0000, 0001, ...   segments of --seg bytes (the last one shorter), each APFS-compressed
    <f>.nps5seg/manifest.json     format nps5seg/1: total_size, seg_size, seg_count, sha256 of every segment and of the
                                  whole original, its mode and mtime

and the runtime's file layer presents the directory to the game as the original single file automatically: no setting
is needed (NPS5_VFS_SEGFILES=0 turns it off). A plain file next to a segment directory always wins.
Files below --whole-max (default 1.9 GiB) are compressed IN PLACE instead (no runtime change);
their sha256 is kept in the xattr com.nps5.gamepack.sha256 so `verify` works for them too.
Safe by default: pack verifies (byte-compare plus sha256) before it touches the original, removes the original only
after that passes, and refuses to run while a game is running or the file is open. --keep leaves the original in place
(the runtime then keeps using it; `finalize` removes it later). --dry-run shows what would happen and changes nothing.
Example:  python3 scripts/gamepack.py pack --dry-run ~/Games/MyTitle     then without --dry-run

Commands
  pack <file|dir>... [--seg 1GiB] [--min-size 512MiB] [--ratio-skip 0.9] [--whole-max 1.9GiB] [--margin 3GiB] [--dry-run] [--keep]
      One file at a time, smallest first. A file whose sampled ratio (64 KiB zlib blocks, as APFS compresses) is above
      --ratio-skip is skipped. Before each file: free space >= estimated compressed size + margin, or stop. The
      original is removed only after the reassembled stream byte-compares equal to it and the sha256s match.
  unpack <file|dir>... [--keep-segments]   rebuild <f> bit-exact from <f>.nps5seg (checked against the manifest
      sha256), restore mode/mtime, then remove the segments. In-place compressed files: --decompress (afsctool -d).
  verify <file|dir>...   re-hash segments + whole stream against the manifest; in-place files against their xattr.
  stat <file|dir>...     logical vs on-disk size (and what a pack would save, sampled).
  finalize <file>... [--log F --reason R]   delete an original kept next to its segments (unpack --keep-segments)
      once segments and original both hash to the manifest sha256.
  checktrace <log>...    check "[vfs] segread <segdir> off= len= fnv=" lines (NPS5_VFS_SEGFILES_TRACE=<n>) against
      the bytes in the segments: proves the VFS handed the game the right bytes at the right offsets.

Refuses to change files while a game process (boot/native_*, ue2boot/native_out*) runs, or while the file is open
(lsof); --no-game-check skips both (unit tests only).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat as st_mod
import subprocess
import sys
import time
import zlib

FORMAT = "nps5seg/1"
SUFFIX = ".nps5seg"
XATTR = "com.nps5.gamepack.sha256"
CHUNK = 8 << 20
GIB = 1 << 30
MIB = 1 << 20
DITTO_MAX = 448 * MIB  # ditto --hfsCompression silently skips files over ~512 MB
APFS_MAX = 2 * GIB - 64 * MIB
GAME_RE = re.compile(r"(^|/)(ue2)?boot/native_|/ue2boot/native_out\d+|(^|/)native(_[A-Za-z0-9_.-]+)?$")
UF_COMPRESSED = getattr(st_mod, "UF_COMPRESSED", 0x20)


def size_arg(s):
    m = re.fullmatch(r"\s*([0-9.]+)\s*([KMGT]?)(i?B)?\s*", s, re.I)
    if not m:
        raise argparse.ArgumentTypeError("bad size %r" % s)
    mul = {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}[m.group(2).upper()]
    return int(float(m.group(1)) * mul)


def human(n):
    for u, d in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if abs(n) >= d:
            return "%.2f %s" % (n / d, u)
    return "%d B" % n


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def ondisk(path):
    s = os.lstat(path)
    return s.st_blocks * 512


def is_compressed(path):
    return bool(os.lstat(path).st_flags & UF_COMPRESSED)


def free_bytes(path):
    return shutil.disk_usage(path).free


def afsctool():
    for p in (shutil.which("afsctool"), "/opt/homebrew/bin/afsctool", "/usr/local/bin/afsctool"):
        if p and os.access(p, os.X_OK):
            return p
    return None


def game_running():
    try:
        out = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, check=True).stdout
    except Exception as e:  # cannot tell: refuse
        return ["ps failed: %s" % e]
    me = os.getpid()
    hits = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and int(parts[0]) != me and GAME_RE.search(parts[1]):
            hits.append(line.strip())
    return hits


def file_open_by_others(path):
    try:
        r = subprocess.run(["lsof", "-t", "--", path], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return bool(r.stdout.strip())


def check_safe(args, path=None):
    if args.no_game_check:
        return
    g = game_running()
    if g:
        sys.exit("gamepack: refusing: a game process is running:\n  " + "\n  ".join(g))
    if path and os.path.exists(path) and file_open_by_others(path):
        sys.exit("gamepack: refusing: %s is open by another process (lsof)" % path)


def sample_ratio(path, size, samples=24, span=4 * MIB):
    """Estimated APFS ratio: zlib level 6 on independent 64 KiB blocks of evenly spread samples."""
    if size == 0:
        return 1.0
    raw = comp = 0
    with open(path, "rb") as f:
        n = max(1, min(samples, size // span or 1))
        for i in range(n):
            off = (size - span) * i // max(1, n - 1) if size > span else 0
            f.seek(max(0, off))
            data = f.read(span)
            for j in range(0, len(data), 65536):
                blk = data[j:j + 65536]
                raw += len(blk)
                comp += min(len(blk), len(zlib.compress(blk, 6)))
    return comp / raw if raw else 1.0


def compress_in_place(path, size):
    """APFS-compress one file (< 2 GiB) in place. Returns True when the file ends up compressed."""
    a = afsctool()
    if a:
        r = subprocess.run([a, "-c", "-T", "LZFSE", path], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("afsctool failed on %s: %s" % (path, (r.stderr or r.stdout).strip()))
    elif size <= DITTO_MAX:
        tmp = path + ".gpditto"
        if os.path.exists(tmp):
            os.unlink(tmp)
        subprocess.run(["ditto", "--hfsCompression", path, tmp], check=True)
        if is_compressed(tmp):
            if not same_bytes(path, tmp):
                os.unlink(tmp)
                raise RuntimeError("ditto copy of %s differs" % path)
            os.replace(tmp, path)
        else:
            os.unlink(tmp)
    else:
        raise RuntimeError("no afsctool (brew install afsctool) and %s is over %s (ditto limit)" % (path, human(DITTO_MAX)))
    return is_compressed(path)


def same_bytes(a, b):
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(CHUNK), fb.read(CHUNK)
            if x != y:
                return False
            if not x:
                return True


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                return h.hexdigest()
            h.update(b)


def segdir_of(path):
    return path + SUFFIX


def load_manifest(sd):
    with open(os.path.join(sd, "manifest.json")) as f:
        m = json.load(f)
    if m.get("format") != FORMAT:
        raise RuntimeError("%s: not a %s manifest" % (sd, FORMAT))
    return m


def seg_names(m):
    return [s["name"] for s in m["segments"]]


def iter_targets(paths, min_size, for_pack):
    """Files to act on, smallest first. For pack: regular files >= min_size, not already compressed/segmented."""
    out = []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p) and not p.endswith(SUFFIX):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.endswith(SUFFIX) and not d.endswith(SUFFIX + ".tmp")]
                for d in os.listdir(root):
                    if d.endswith(SUFFIX) and os.path.isdir(os.path.join(root, d)):
                        out.append(os.path.join(root, d[: -len(SUFFIX)]))
                for fn in files:
                    out.append(os.path.join(root, fn))
        elif p.endswith(SUFFIX):
            out.append(p[: -len(SUFFIX)])
        else:
            out.append(p)
    seen, res = set(), []
    for p in out:
        if p in seen:
            continue
        seen.add(p)
        if for_pack:
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            if p.endswith(".gpditto") or p.endswith(".gpunpack.tmp"):
                continue
            if os.path.getsize(p) < min_size:
                continue
        res.append(p)
    res.sort(key=lambda q: os.path.getsize(q) if os.path.isfile(q) else 0)
    return res


_FS = {}


def fs_type(path):
    """File system type of the volume holding path ('apfs', 'exfat', ...), from mount(8)."""
    dev = os.stat(path).st_dev
    if dev not in _FS:
        mp = os.path.realpath(path)
        while not os.path.ismount(mp):
            mp = os.path.dirname(mp)
        out = subprocess.run(["mount"], capture_output=True, text=True).stdout
        t = "?"
        for line in out.splitlines():
            m = re.match(r".+ on (.+) \(([^,)]+)", line)
            if m and m.group(1) == mp:
                t = m.group(2)
        _FS[dev] = t
    return _FS[dev]


def plan_file(p, args):
    size = os.path.getsize(p)
    if fs_type(p) != "apfs":
        return dict(path=p, size=size, mode="skip", why="on %s, not APFS: no transparent compression" % fs_type(p), ratio=None, est=0)
    if is_compressed(p):
        return dict(path=p, size=size, mode="skip", why="already APFS-compressed", ratio=None, est=0)
    if os.path.isdir(segdir_of(p)):
        return dict(path=p, size=size, mode="skip", why="segment dir already exists", ratio=None, est=0)
    ratio = sample_ratio(p, size)
    if ratio > args.ratio_skip:
        return dict(path=p, size=size, mode="skip", why="sampled ratio %.2f > %.2f" % (ratio, args.ratio_skip), ratio=ratio, est=0)
    mode = "inplace" if size <= args.whole_max else "segment"
    return dict(path=p, size=size, mode=mode, why="", ratio=ratio, est=int(size * (1 - ratio)))


def effective_seg(args):
    seg = args.seg
    if seg > APFS_MAX:
        sys.exit("gamepack: --seg must be under 2 GiB (APFS cannot compress larger files)")
    if not afsctool() and seg > DITTO_MAX:
        log("no afsctool: segments capped at %s so ditto can compress them" % human(DITTO_MAX))
        seg = DITTO_MAX
    return seg


def pack_segmented(p, size, args, seg):
    sd = segdir_of(p)
    tmp = sd + ".tmp"
    if os.path.exists(tmp):
        shutil.rmtree(tmp)  # an interrupted earlier pack of this file
    os.mkdir(tmp)
    st = os.stat(p)
    count = (size + seg - 1) // seg
    whole = hashlib.sha256()
    segs = []
    est_ratio = args._ratio
    try:
        with open(p, "rb") as f:
            for i in range(count):
                n = seg if i + 1 < count else size - seg * (count - 1)
                need = int(n * est_ratio) + args.margin
                if free_bytes(tmp) < need:
                    raise RuntimeError("free %s < %s needed for segment %d: stopped" % (human(free_bytes(tmp)), human(need), i))
                name = "%04d" % i
                sp = os.path.join(tmp, name)
                h = hashlib.sha256()
                with open(sp, "wb") as o:
                    left = n
                    while left:
                        b = f.read(min(CHUNK, left))
                        if not b:
                            raise RuntimeError("%s ended early" % p)
                        h.update(b)
                        whole.update(b)
                        o.write(b)
                        left -= len(b)
                comp = compress_in_place(sp, n)
                segs.append(dict(name=name, size=n, sha256=h.hexdigest(), apfs_compressed=comp))
                log("  %s/%s %s -> %s on disk%s" % (os.path.basename(sd), name, human(n), human(ondisk(sp)),
                                                    "" if comp else " (not compressed)"))
        m = dict(format=FORMAT, name=os.path.basename(p), total_size=size, seg_size=seg, seg_count=count,
                 sha256=whole.hexdigest(), mode=st_mod.S_IMODE(st.st_mode), mtime_ns=st.st_mtime_ns,
                 created=time.strftime("%Y-%m-%dT%H:%M:%S"), tool="gamepack", segments=segs)
        with open(os.path.join(tmp, "manifest.json"), "w") as o:
            json.dump(m, o, indent=1)
            o.flush()
            os.fsync(o.fileno())
        # byte-compare the reassembled stream (read back through APFS decompression) with the original
        log("  verify: byte-compare of the reassembled stream against the original")
        rh = hashlib.sha256()
        with open(p, "rb") as f:
            for s in segs:
                sh = hashlib.sha256()
                with open(os.path.join(tmp, s["name"]), "rb") as g:
                    while True:
                        b = g.read(CHUNK)
                        if not b:
                            break
                        if f.read(len(b)) != b:
                            raise RuntimeError("MISMATCH in segment %s of %s: original kept" % (s["name"], p))
                        sh.update(b)
                        rh.update(b)
                if sh.hexdigest() != s["sha256"]:
                    raise RuntimeError("segment %s sha256 differs after compression: original kept" % s["name"])
            if f.read(1):
                raise RuntimeError("segments shorter than %s: original kept" % p)
        if rh.hexdigest() != m["sha256"]:
            raise RuntimeError("whole-file sha256 differs: original kept")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    os.rename(tmp, sd)
    if not args.keep:
        os.unlink(p)
    return m


def pack_inplace(p, size, args):
    h = sha256_file(p)
    if not afsctool() and size > DITTO_MAX:
        raise RuntimeError("needs afsctool for %s (%s)" % (p, human(size)))
    tmp = p + ".gpclone"
    if os.path.exists(tmp):
        os.unlink(tmp)
    subprocess.run(["cp", "-c", p, tmp], check=True)  # APFS clone: no space until compression rewrites it
    try:
        if not compress_in_place(tmp, size):
            os.unlink(tmp)
            return None
        if not same_bytes(p, tmp) or sha256_file(tmp) != h:
            raise RuntimeError("compressed copy of %s differs: original kept" % p)
        st = os.stat(p)
        os.chmod(tmp, st_mod.S_IMODE(st.st_mode))
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        try:
            os.setxattr(tmp, XATTR, h.encode())
        except (AttributeError, OSError):
            subprocess.run(["xattr", "-w", XATTR, h, tmp], check=True)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return h


def cmd_pack(args):
    targets = iter_targets(args.paths, args.min_size, True)
    seg = effective_seg(args)
    plans = [plan_file(p, args) for p in targets]
    tot_size = sum(x["size"] for x in plans if x["mode"] != "skip")
    tot_est = sum(x["est"] for x in plans)
    print("%-9s %12s %6s %12s  %s" % ("mode", "size", "ratio", "est. saved", "file"))
    for x in plans:
        print("%-9s %12s %6s %12s  %s%s" % (x["mode"], human(x["size"]), "%.2f" % x["ratio"] if x["ratio"] is not None else "-",
                                            human(x["est"]), x["path"], ("  (" + x["why"] + ")") if x["why"] else ""))
    print("total: %d file(s) to pack, %s -> est. %s saved (segment size %s, free now %s)" % (
        sum(1 for x in plans if x["mode"] != "skip"), human(tot_size), human(tot_est), human(seg),
        human(free_bytes(targets[0])) if targets else "-"))
    if args.dry_run or not plans:
        return 0
    check_safe(args)
    saved = 0
    for x in plans:
        if x["mode"] == "skip":
            continue
        p, size = x["path"], x["size"]
        check_safe(args, p)
        need = int(size * x["ratio"]) + args.margin
        if free_bytes(p) < need:
            log("STOP: free %s < %s needed for %s" % (human(free_bytes(p)), human(need), p))
            return 4
        before = ondisk(p)
        log("pack %s (%s, %s, sampled ratio %.2f)" % (p, human(size), x["mode"], x["ratio"]))
        args._ratio = x["ratio"]
        if x["mode"] == "segment":
            m = pack_segmented(p, size, args, seg)
            after = sum(ondisk(os.path.join(segdir_of(p), s["name"])) for s in m["segments"])
            log("done %s: %d segments, %s -> %s on disk, sha256 %s" % (p, m["seg_count"], human(before), human(after), m["sha256"][:16]))
            if args.log and not args.keep:  # the original was removed: one deleted.log line
                with open(args.log, "a") as f:
                    f.write("%s\t%s\t%d\t%s (replaced by %s, %d segments, sha256 %s)\n" % (
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), p, size, args.reason, os.path.basename(segdir_of(p)),
                        m["seg_count"], m["sha256"]))
        else:
            h = pack_inplace(p, size, args)
            if h is None:
                log("skip %s: APFS did not compress it" % p)
                continue
            after = ondisk(p)
            log("done %s: in place, %s -> %s on disk" % (p, human(before), human(after)))
        saved += before - after
    log("pack: saved %s, free now %s" % (human(saved), human(free_bytes(plans[0]["path"] if os.path.exists(plans[0]["path"]) else os.path.dirname(plans[0]["path"])))))
    return 0


def stream_segments(sd, m, sink):
    whole = hashlib.sha256()
    for s in m["segments"]:
        sh = hashlib.sha256()
        n = 0
        with open(os.path.join(sd, s["name"]), "rb") as g:
            while True:
                b = g.read(CHUNK)
                if not b:
                    break
                sh.update(b)
                whole.update(b)
                n += len(b)
                if sink:
                    sink(b)
        if n != s["size"] or sh.hexdigest() != s["sha256"]:
            raise RuntimeError("%s/%s: size/sha256 differs from the manifest" % (sd, s["name"]))
    return whole.hexdigest()


def cmd_verify(args):
    bad = 0
    for p in iter_targets(args.paths, 0, False):
        sd = segdir_of(p)
        if os.path.isdir(sd):
            m = load_manifest(sd)
            try:
                h = stream_segments(sd, m, None)
                ok = h == m["sha256"] and sum(s["size"] for s in m["segments"]) == m["total_size"]
            except RuntimeError as e:
                print("BAD", e)
                ok = False
            print("%s %s (%d segments, %s)" % ("OK " if ok else "BAD", p, m["seg_count"], human(m["total_size"])))
            bad += not ok
        elif os.path.isfile(p):
            try:
                want = os.getxattr(p, XATTR).decode()
            except (AttributeError, OSError):
                r = subprocess.run(["xattr", "-p", XATTR, p], capture_output=True, text=True)
                want = r.stdout.strip() if r.returncode == 0 else None
            if not want:
                continue
            ok = sha256_file(p) == want
            print("%s %s (in place, %s)" % ("OK " if ok else "BAD", p, "compressed" if is_compressed(p) else "NOT compressed"))
            bad += not ok
    return 1 if bad else 0


def cmd_unpack(args):
    rc = 0
    for p in iter_targets(args.paths, 0, False):
        sd = segdir_of(p)
        if os.path.isdir(sd):
            check_safe(args)
            if os.path.exists(p):
                print("skip %s: the original exists next to its segments" % p)
                continue
            m = load_manifest(sd)
            need = m["total_size"] + args.margin
            if free_bytes(sd) < need:
                log("STOP: free %s < %s needed to unpack %s" % (human(free_bytes(sd)), human(need), p))
                return 4
            tmp = p + ".gpunpack.tmp"
            log("unpack %s (%s from %d segments)" % (p, human(m["total_size"]), m["seg_count"]))
            try:
                with open(tmp, "wb") as o:
                    h = stream_segments(sd, m, o.write)
                    o.flush()
                    os.fsync(o.fileno())
                if h != m["sha256"] or os.path.getsize(tmp) != m["total_size"]:
                    raise RuntimeError("rebuilt %s does not match the manifest sha256" % p)
                os.chmod(tmp, m.get("mode", 0o644))
                if "mtime_ns" in m:
                    os.utime(tmp, ns=(m["mtime_ns"], m["mtime_ns"]))
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            os.rename(tmp, p)
            if not args.keep_segments:
                shutil.rmtree(sd)
            log("done %s: sha256 %s matches the manifest" % (p, h[:16]))
        elif os.path.isfile(p) and args.decompress and is_compressed(p):
            check_safe(args, p)
            a = afsctool()
            if not a:
                print("no afsctool for %s" % p)
                rc = 1
                continue
            subprocess.run([a, "-d", p], check=True)
            log("decompressed %s" % p)
    return rc


def cmd_finalize(args):
    """Delete an original that still sits next to its segments (after unpack --keep-segments), once both the segments
    and the original hash to the manifest sha256. Logs to --log (deleted.log line: time, path, bytes, reason)."""
    rc = 0
    for p in iter_targets(args.paths, 0, False):
        sd = segdir_of(p)
        if not (os.path.isdir(sd) and os.path.isfile(p)):
            print("skip %s: needs both the original and %s" % (p, os.path.basename(sd)))
            continue
        check_safe(args, p)
        m = load_manifest(sd)
        hs = stream_segments(sd, m, None)
        ho = sha256_file(p)
        if not (hs == m["sha256"] == ho and os.path.getsize(p) == m["total_size"]):
            print("KEEP %s: sha256 mismatch (segments %s, original %s, manifest %s)" % (p, hs[:16], ho[:16], m["sha256"][:16]))
            rc = 1
            continue
        size = os.path.getsize(p)
        os.unlink(p)
        log("removed %s (%s; segments and original sha256 %s)" % (p, human(size), ho[:16]))
        if args.log:
            with open(args.log, "a") as f:
                f.write("%s\t%s\t%d\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), p, size, args.reason))
    return rc


def cmd_stat(args):
    tl = td = te = 0
    rows = []
    for p in args.paths:
        p = os.path.abspath(p)
        for root, dirs, files in os.walk(p) if os.path.isdir(p) else [(os.path.dirname(p), [], [os.path.basename(p)])]:
            for d in list(dirs):
                if d.endswith(SUFFIX):
                    sd = os.path.join(root, d)
                    try:
                        m = load_manifest(sd)
                    except Exception:
                        continue
                    disk = sum(ondisk(os.path.join(sd, n)) for n in os.listdir(sd))
                    rows.append((sd[: -len(SUFFIX)], m["total_size"], disk, "segmented (%d)" % m["seg_count"], 0))
                    dirs.remove(d)
            for fn in files:
                fp = os.path.join(root, fn)
                if os.path.islink(fp) or not os.path.isfile(fp):
                    continue
                size, disk = os.path.getsize(fp), ondisk(fp)
                est = 0
                if args.estimate and size >= args.min_size and not is_compressed(fp):
                    est = int(size * (1 - sample_ratio(fp, size)))
                rows.append((fp, size, disk, "compressed" if is_compressed(fp) else "", est))
    for r in sorted(rows, key=lambda r: -r[1]):
        tl += r[1]
        td += r[2]
        te += r[4]
        if r[1] >= args.min_size or r[3]:
            print("%12s %12s %-15s %s%s" % (human(r[1]), human(r[2]), r[3], r[0], ("  est. pack saves " + human(r[4])) if r[4] else ""))
    print("total: logical %s, on disk %s (%s saved)%s" % (human(tl), human(td), human(tl - td),
                                                           (", est. more from pack " + human(te)) if args.estimate else ""))
    return 0


def fnv64(b):
    h = 1469598103934665603
    for x in b:
        h = ((h ^ x) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def cmd_checktrace(args):
    pat = re.compile(r"\[vfs\] segread (.+?) off=(\d+) len=(\d+) fnv=([0-9a-f]{16})")
    cache = {}
    ok = bad = 0
    for lp in args.logs:
        with open(lp, errors="replace") as f:
            for line in f:
                mt = pat.search(line)
                if not mt:
                    continue
                sd, off, n, fnv = mt.group(1), int(mt.group(2)), int(mt.group(3)), int(mt.group(4), 16)
                if sd not in cache:
                    cache[sd] = load_manifest(sd)
                m = cache[sd]
                seg = m["seg_size"]
                data = bytearray()
                pos, left = off, n
                while left > 0 and pos < m["total_size"]:
                    i = pos // seg
                    with open(os.path.join(sd, m["segments"][i]["name"]), "rb") as g:
                        g.seek(pos - i * seg)
                        b = g.read(min(left, seg - (pos - i * seg)))
                    if not b:
                        break
                    data += b
                    pos += len(b)
                    left -= len(b)
                if len(data) == n and fnv64(data) == fnv:
                    ok += 1
                else:
                    bad += 1
                    if bad <= 10:
                        print("BAD read %s off=%d len=%d" % (sd, off, n))
    print("checktrace: %d reads match the segments, %d do not" % (ok, bad))
    return 1 if bad or not ok else 0


def main():
    ap = argparse.ArgumentParser(prog="gamepack", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, sizes=True):
        p.add_argument("paths", nargs="+")
        p.add_argument("--no-game-check", action="store_true", help="skip the running-game / open-file refusal (tests)")
        p.add_argument("--margin", type=size_arg, default=3 * GIB, help="free space kept beyond the estimate (default 3GiB)")
        if sizes:
            p.add_argument("--min-size", type=size_arg, default=512 * MIB)

    p = sub.add_parser("pack")
    common(p)
    p.add_argument("--seg", type=size_arg, default=GIB)
    p.add_argument("--ratio-skip", type=float, default=0.9)
    p.add_argument("--whole-max", type=size_arg, default=int(1.9 * GIB), help="files up to this size are compressed in place")
    p.add_argument("--dry-run", action="store_true", help="show the plan and change nothing")
    p.add_argument("--keep", action="store_true", help="keep the original next to its segments (the game keeps using the original; `finalize` deletes it)")
    p.add_argument("--log", help="append a deleted.log line for every original removed")
    p.add_argument("--reason", default="gamepack pack")
    p = sub.add_parser("unpack")
    common(p, False)
    p.add_argument("--keep-segments", action="store_true")
    p.add_argument("--decompress", action="store_true", help="also decompress in-place compressed files (afsctool -d)")
    p = sub.add_parser("verify")
    common(p, False)
    p = sub.add_parser("finalize")
    common(p, False)
    p.add_argument("--log", help="append a deleted.log line here")
    p.add_argument("--reason", default="gamepack finalize")
    p = sub.add_parser("stat")
    common(p)
    p.add_argument("--estimate", action="store_true", help="sample what a pack would save")
    p = sub.add_parser("checktrace")
    p.add_argument("logs", nargs="+")
    args = ap.parse_args()
    if args.cmd == "pack" and args.whole_max >= APFS_MAX:
        args.whole_max = APFS_MAX - 1
    try:
        return {"pack": cmd_pack, "unpack": cmd_unpack, "verify": cmd_verify, "stat": cmd_stat,
                "checktrace": cmd_checktrace, "finalize": cmd_finalize}[args.cmd](args)
    except RuntimeError as e:
        log("ERROR: %s" % e)
        return 3


if __name__ == "__main__":
    sys.exit(main())
