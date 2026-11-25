#!/usr/bin/env python3
"""scripts/nps5lib/data.py: store a game folder with APFS transparent compression. Python 3, standard library only
(plus two libraries every macOS has: libSystem for the file system calls, libcompression for LZFSE).

  nps5 data status <folder>                    how much of the folder is compressed and what that saves
  nps5 data compress <folder> [--apply]        DRY RUN by default: counts, a size estimate from a sample, free space.
                                               --apply compresses file by file, in place.
  nps5 data decompress <folder> [--apply]      undo: plain files again (dry run by default)
  options: --jobs N (default: half the cores, at most 4)   --min-size BYTES (default 4096: smaller files are skipped)
           --pause-while PATH (wait between files while PATH exists, e.g. a lock a running game holds)   --quiet

What it does. macOS can keep a file compressed on an APFS volume and hand every program the original bytes: the
kernel decompresses on read (64 KiB chunks, random access, mmap works). Nothing that reads the folder changes, the
game and the tools see identical files. Game data (textures, models) typically shrinks 3x. Reads cost about one
CPU-second per GB on an Apple M-series core.

Safety. One file at a time, never in place:
  1. the file is read and hashed (SHA-256), and compressed in memory (LZFSE);
  2. the compressed form is written to a NEW temporary file in the same directory and flushed to the disk
     (F_FULLFSYNC);
  3. the temporary file is read back through the kernel and must hash to the same SHA-256, with the same length;
  4. the original must be unchanged since step 1 (same inode, size, modification and change time);
  5. only then is the temporary file renamed over the original (atomic: after a crash or a power loss the name
     holds either the complete old file or the complete new one).
A file that fails any step keeps its original, untouched. A leftover temporary file (a run that was killed) is
removed by the next run (unless the process that made it still runs and touched it within a day); they are named
".nps5z-<pid>-<n>.tmp". The run can be stopped at any time and started again: finished files are recognised by their
compressed flag, files that did not shrink are remembered in a state file outside the folder ($NPS5_DATA_STATE_DIR,
default ~/Library/Application Support/nativeps5/data).

What it leaves alone: symbolic links, files with more than one hard link, files that are already compressed, files
under --min-size, files with a (non-blank) resource fork, an ACL or an immutable/append flag, cloud files whose data
is not on the disk, files changed in the last minute (their change time: right after a copy or a decompress, run it
again a minute later), files of 2 GiB or more, anything that is not a regular file, other volumes mounted inside the
folder. It refuses a folder that is not on APFS (exFAT, HFS+, network shares have no transparent compression): copy
the game to an APFS disk first. It also refuses the top of a volume, your home folder (or a folder containing it),
~/Library, system folders and read-only volumes: it is meant for a game folder, whose files are only read.

Things to know: a program that WRITES to a compressed file makes macOS store it plain again (correct, only the saving
is lost). A program that holds a file open FOR WRITING while it is replaced would write into the replaced copy: do
not run it on a folder another program writes to (a game only reads its data; a running game is fine). A file that
shares its blocks with a copy (an APFS clone, `cp -c`) stops sharing when it is compressed. Time Machine and `cp`
keep working; a copy to a non-APFS disk arrives at full size.

Exit codes: 0 ok, 1 some files failed (they are unchanged), 2 bad argument or the folder cannot be used,
3 `compress --apply` left files that changed in the last minute (unchanged; run the same command again in a minute).
"""
import ctypes, errno, fcntl, hashlib, itertools, json, mmap, os, stat, struct, sys, threading, time

CHUNK = 65536                       # decmpfs chunk size
UF_COMPRESSED = 0x00000020
KEEP_OUT_FLAGS = 0x00000002 | 0x00000004 | 0x00020000 | 0x00040000   # UF_IMMUTABLE, UF_APPEND, SF_IMMUTABLE, SF_APPEND
SF_DATALESS = 0x40000000            # a cloud file (iCloud Drive, File Provider) whose data is not on this disk
MNT_RDONLY = 0x00000001
INFLIGHT_BYTES = 1 << 30            # files in flight across all workers (memory: a worker holds one compressed file)
ATTR_CMN_CRTIME = 0x00000200
START_MARGIN, RUN_MARGIN, DECOMPRESS_MARGIN = 256 << 20, 64 << 20, 1 << 30   # free space kept beyond the files in flight
COMPRESSION_LZFSE = 0x801
TYPE_LZFSE_XATTR, TYPE_LZFSE_RSRC = 11, 12
INLINE_MAX = 3802 - 16              # largest compressed payload that fits the decmpfs xattr beside its 16-byte header
MAX_SIZE = 2 << 30                  # resource-fork offsets are 32 bit; stay far below
XA_DECMPFS, XA_RSRC = b"com.apple.decmpfs", b"com.apple.ResourceFork"
TMP_PREFIX, TMP_SUFFIX = ".nps5z-", ".tmp"
RECENT_S = 60
RECENT = "changed in the last minute"
STALE_TMP_S = 24 * 3600             # a temporary file unchanged this long is a leftover even if its pid runs again
F_FULLFSYNC = 51
F_GETPATH = 50
ACL_TYPE_EXTENDED = 0x00000100
SAMPLE_BYTES, SAMPLE_FILES = 128 << 20, 400

_libc = _lzfse = None
_load_lock = threading.Lock()


class DataError(Exception):
    def __init__(self, msg, code=2):
        Exception.__init__(self, msg); self.code = code


def _load():
    """The two system libraries. Raises DataError off macOS."""
    global _libc, _lzfse
    if _libc is not None:
        return
    with _load_lock:
        _load_locked()


def _load_locked():
    global _libc, _lzfse
    if _libc is not None:
        return
    if sys.platform != "darwin":
        raise DataError("nps5 data needs macOS (APFS transparent compression)")
    c = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    z = ctypes.CDLL("/usr/lib/libcompression.dylib")
    z.compression_encode_buffer.restype = ctypes.c_size_t
    z.compression_encode_buffer.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    c.fsetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    c.flistxattr.restype = ctypes.c_ssize_t
    c.flistxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
    c.fgetxattr.restype = ctypes.c_ssize_t
    c.fgetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    c.statfs.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    c.fsetattrlist.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
    c.acl_get_fd_np.restype = ctypes.c_void_p
    c.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    c.acl_free.argtypes = [ctypes.c_void_p]
    _lzfse = z
    _libc = c                                    # last: _load() tests this one


def _statfs(path):
    _load()
    buf = ctypes.create_string_buffer(4096)          # struct statfs (arm64) is 2168 bytes
    if _libc.statfs(os.fsencode(path), buf) != 0:
        raise DataError("cannot read the file system of %s: %s" % (path, os.strerror(ctypes.get_errno())))
    return buf.raw


def fs_type(path):
    """File system type name of the volume holding path ('apfs', 'exfat', 'hfs', 'smbfs', ...)."""
    return _statfs(path)[72:88].split(b"\0")[0].decode("ascii", "replace")     # f_fstypename[16] at offset 72


def fs_readonly(path):
    return bool(struct.unpack_from("<I", _statfs(path), 64)[0] & MNT_RDONLY)       # f_flags at offset 64


def fs_mount_point(path):
    return os.fsdecode(_statfs(path)[88:88 + 1024].split(b"\0")[0])                 # f_mntonname[1024] at offset 88


def _xattr_names(fd):
    n = _libc.flistxattr(fd, None, 0, 0)
    if n <= 0:
        return []
    buf = ctypes.create_string_buffer(n)
    n = _libc.flistxattr(fd, buf, n, 0)
    return [x for x in buf.raw[:max(n, 0)].split(b"\0") if x]


def _xattr_get(fd, name):
    n = _libc.fgetxattr(fd, name, None, 0, 0, 0)
    if n < 0:
        raise OSError(ctypes.get_errno(), "fgetxattr")
    buf = ctypes.create_string_buffer(max(n, 1))
    n = _libc.fgetxattr(fd, name, buf, n, 0, 0)
    if n < 0:
        raise OSError(ctypes.get_errno(), "fgetxattr")
    return buf.raw[:n]


def _xattr_set(fd, name, value, size=None):
    """value: bytes, or the address of `size` bytes."""
    if _libc.fsetxattr(fd, name, value, len(value) if size is None else size, 0, 0) != 0:
        e = ctypes.get_errno(); raise OSError(e, "fsetxattr %s: %s" % (name.decode(), os.strerror(e)))


def _has_acl(fd):
    a = _libc.acl_get_fd_np(fd, ACL_TYPE_EXTENDED)
    if a:
        _libc.acl_free(a); return True
    return False


def _full_sync(fd):
    try:
        fcntl.fcntl(fd, F_FULLFSYNC)
    except OSError:
        os.fsync(fd)


def _sync_dir(d):
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _encode_n(b, out):
    """LZFSE-compress b into the buffer out; the compressed length, 0 when it does not fit."""
    return _lzfse.compression_encode_buffer(out, len(out), b, len(b), None, COMPRESSION_LZFSE)


def _encode(b, out):
    n = _encode_n(b, out)
    return ctypes.string_at(out, n) if n else None


def lzfse_chunks(data):
    """Compress data as independent 64 KiB LZFSE streams (the decmpfs layout). None when the encoder refuses."""
    out = ctypes.create_string_buffer(CHUNK + 4096)
    chunks = []
    for o in range(0, len(data), CHUNK):
        c = _encode(data[o:o + CHUNK], out)
        if c is None:
            return None
        chunks.append(c)
    return chunks


BLANK_FORK_NOTE = b"This resource fork intentionally left blank"


def blank_fork(value):
    """True for a resource fork without resources: zero length, or the 286-byte placeholder macOS writes into the
    AppleDouble ("._name") companion of a file on a FAT/exFAT disk: header, the note above, an empty resource map."""
    if len(value) == 0:
        return True
    if len(value) != 286 or value[16:16 + len(BLANK_FORK_NOTE)] != BLANK_FORK_NOTE:
        return False
    data_off, map_off, data_len, map_len = struct.unpack(">4I", value[:16])
    return (data_off, map_off, data_len, map_len) == (256, 256, 0, 30) and value[256 + 28:256 + 30] == b"\xff\xff"   # type count -1 = no resources


def decmpfs_payload(size, chunks, consume=False):
    """(decmpfs xattr value, resource fork value or None) for a file of `size` bytes made of `chunks`. consume=True
    empties `chunks` while the fork is built, so the peak memory is one copy of the compressed file, not two."""
    total = sum(len(c) for c in chunks)
    if len(chunks) == 1 and total <= INLINE_MAX:
        return struct.pack("<4sIQ", b"fpmc", TYPE_LZFSE_XATTR, size) + chunks[0], None
    offs = [4 * (len(chunks) + 1)]
    for c in chunks:
        offs.append(offs[-1] + len(c))
    # one buffer, filled chunk by chunk and emptying `chunks` as it goes: the peak is one copy of the compressed file
    fork = ctypes.create_string_buffer(offs[-1])
    table = struct.pack("<%dI" % len(offs), *offs)
    ctypes.memmove(fork, table, len(table))
    for i in range(len(chunks) - 1, -1, -1):
        c = chunks.pop() if consume else chunks[i]
        ctypes.memmove(ctypes.addressof(fork) + offs[i], c, len(c))
    return struct.pack("<4sIQ", b"fpmc", TYPE_LZFSE_RSRC, size), fork


def stored_bytes(size, chunks, block=4096):
    """Disk bytes the compressed form will occupy: nothing when it fits the xattr, else the fork rounded to blocks."""
    total = sum(len(c) for c in chunks)
    if len(chunks) == 1 and total <= INLINE_MAX:
        return 0
    n = total + 4 * (len(chunks) + 1)
    return (n + block - 1) // block * block


def allocated(st):
    return st.st_blocks * 512


def skip_reason(st, min_size, now=None):
    """Why a file is left alone, from its lstat alone ('' = a candidate). Cheap: no open, no read."""
    if stat.S_ISLNK(st.st_mode): return "symlink"
    if not stat.S_ISREG(st.st_mode): return "not a regular file"
    if getattr(st, "st_flags", 0) & UF_COMPRESSED: return "already compressed"
    if st.st_nlink > 1: return "hard link"
    if st.st_size < min_size or st.st_size == 0: return "tiny"
    if st.st_size >= MAX_SIZE: return "2 GiB or larger"
    if getattr(st, "st_flags", 0) & KEEP_OUT_FLAGS: return "immutable or append-only"
    if getattr(st, "st_flags", 0) & SF_DATALESS: return "cloud file not on this disk"
    # the change time: every write moves it, nobody can set it (a copy tool sets an old modification time, a file from
    # another machine can carry one in the future). A change time far in the future means the clock was set back since
    # the last write: a file being written now would have a change time of now, so it does not count as recent.
    age = (time.time() if now is None else now) - getattr(st, "st_ctime", st.st_mtime)
    if -RECENT_S < age < RECENT_S: return RECENT
    return ""


def is_tmp(name):
    return name.startswith(TMP_PREFIX) and name.endswith(TMP_SUFFIX)


def walk(folder):
    """Yield (path, lstat) of every entry under folder that is not a directory, staying on the folder's volume and
    never following a link. Our own temporary files are yielded too (callers filter with is_tmp)."""
    dev = os.lstat(folder).st_dev
    stack = [folder]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                ents = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in ents:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                if st.st_dev == dev:
                    stack.append(e.path)
            else:
                yield e.path, st


def _same(a, b):
    return (a.st_ino, a.st_dev, a.st_size, a.st_mtime_ns, a.st_ctime_ns) == (b.st_ino, b.st_dev, b.st_size, b.st_mtime_ns, b.st_ctime_ns)


def _hash_file(path):
    h = hashlib.sha256(); n = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b: break
            h.update(b); n += len(b)
    return h.hexdigest(), n


_tmp_seq = itertools.count(1)


def _tmp_name(path):
    return os.path.join(os.path.dirname(path), "%s%d-%d%s" % (TMP_PREFIX, os.getpid(), next(_tmp_seq), TMP_SUFFIX))


def _carry_metadata(src_fd, dst_fd, st, flags):
    for name in _xattr_names(src_fd):
        if name not in (XA_DECMPFS, XA_RSRC):
            _xattr_set(dst_fd, name, _xattr_get(src_fd, name))
    os.fchmod(dst_fd, stat.S_IMODE(st.st_mode))
    try:
        os.fchown(dst_fd, st.st_uid, st.st_gid)
    except OSError:
        if (os.fstat(dst_fd).st_uid, os.fstat(dst_fd).st_gid) != (st.st_uid, st.st_gid):
            raise
    if flags:
        os.fchflags(dst_fd, flags) if hasattr(os, "fchflags") else os.chflags("/dev/fd/%d" % dst_fd, flags)


class _AttrList(ctypes.Structure):
    _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16), ("commonattr", ctypes.c_uint32),
                ("volattr", ctypes.c_uint32), ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32), ("forkattr", ctypes.c_uint32)]


def _set_crtime(fd, st):
    """The creation date (Finder's "Created") of the original on the new file."""
    if getattr(st, "st_birthtime_ns", None) is not None:          # Python 3.12+: exact
        sec, nsec = divmod(st.st_birthtime_ns, 1000000000)
    elif getattr(st, "st_birthtime", None) is not None:
        bt = st.st_birthtime; sec = int(bt // 1); nsec = min(999999999, int(round((bt - sec) * 1e9)))
    else:
        return
    al = _AttrList(5, 0, ATTR_CMN_CRTIME, 0, 0, 0, 0)
    ts = struct.pack("<qq", sec, nsec)
    if _libc.fsetattrlist(fd, ctypes.byref(al), ts, len(ts), 0) != 0:
        e = ctypes.get_errno(); raise OSError(e, "fsetattrlist (creation date): %s" % os.strerror(e))


def _replace(path, st, tmp, digest, hook):
    """Steps 3-5 of the module text: read tmp back, compare, check the original is unchanged, rename."""
    back, n = _hash_file(tmp)
    if n != st.st_size or back != digest:
        raise OSError(errno.EIO, "read-back differs from the original")
    hook("verified", path, tmp)
    if not _same(os.lstat(path), st):
        raise OSError(errno.EBUSY, "the file changed while it was being processed")
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    fd = os.open(tmp, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        _set_crtime(fd, st)                      # after utime: a modification time older than it would pull it back
    finally:
        os.close(fd)
    os.rename(tmp, path)
    _sync_dir(os.path.dirname(path))


def _nohook(stage, path, tmp):
    pass


def _compress_into(fd, st, base, room, table, limit):
    """Read the file 64 KiB at a time, hash it, and compress each chunk into the resource-fork layout at address
    `base` (`room` bytes): the offset table (4 bytes per chunk + 1) first, then the chunks. Returns (why it is
    left alone or '', SHA-256 of the bytes read, end of the fork)."""
    h = hashlib.sha256(); n = 0; offs = [table]
    out = ctypes.create_string_buffer(CHUNK + 4096)
    with os.fdopen(os.dup(fd), "rb", buffering=1 << 20) as f:
        while True:
            b = f.read(CHUNK)
            if not b: break
            h.update(b); n += len(b)
            if n > st.st_size or len(offs) * 4 >= table:
                return "changed", None, 0                          # it grew since the lstat
            c = _encode_n(b, out)
            if not c or (offs[-1] + c - table > limit and n < st.st_size) or offs[-1] + c > room:
                return "no gain", None, 0
            ctypes.memmove(base + offs[-1], out, c); offs.append(offs[-1] + c)
    if n != st.st_size:
        return "changed", None, 0
    ctypes.memmove(base, struct.pack("<%dI" % len(offs), *offs), table)
    return "", h.hexdigest(), offs[-1]


def compress_file(path, min_size=4096, hook=_nohook):
    """Compress one file in place. Returns (status, bytes before on disk, bytes after on disk, detail):
    status 'done' | 'kept' (left as it was, detail says why) | 'error' (left as it was). Never raises for a file problem."""
    _load()
    tmp = None
    try:
        st = os.lstat(path)
        why = skip_reason(st, min_size)
        if why:
            return "kept", allocated(st), allocated(st), why
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if not _same(os.fstat(fd), st):
                return "kept", allocated(st), allocated(st), "changed"
            names = _xattr_names(fd)
            # A BLANK resource fork (files copied from exFAT/FAT carry one, made from their "._" companion) holds
            # nothing and is dropped; a real one cannot coexist with the compressed form, so that file is left alone.
            if XA_DECMPFS in names or (XA_RSRC in names and not blank_fork(_xattr_get(fd, XA_RSRC))):
                return "kept", allocated(st), allocated(st), "has a resource fork"
            if _has_acl(fd):
                return "kept", allocated(st), allocated(st), "has an ACL"
            # streamed 64 KiB at a time, each chunk compressed straight into its place in the resource fork: the
            # compressed form is the only copy held in memory, in anonymous memory that takes pages only as they are
            # written. A file that cannot shrink is given up as soon as its compressed size passes the bound.
            nchunks = -(-st.st_size // CHUNK); table = 4 * (nchunks + 1)
            limit = allocated(st) - 4096 - table      # compressed bytes past this cannot free a block
            room = table + max(limit, 0) + CHUNK + 4096   # what the chunks can fill before the bound stops them, + one
            with mmap.mmap(-1, room) as buf:
                base = ctypes.addressof(ctypes.c_char.from_buffer(buf))   # (the export ends here; buf stays open)
                why, digest, end = _compress_into(fd, st, base, room, table, limit)
                if why:
                    return "kept", allocated(st), allocated(st), why
                inline = nchunks == 1 and end - table <= INLINE_MAX           # as decmpfs_payload() / stored_bytes()
                if (0 if inline else (end + 4095) // 4096 * 4096) + 4096 > allocated(st):   # must free a block
                    return "kept", allocated(st), allocated(st), "no gain"
                if inline:
                    head = struct.pack("<4sIQ", b"fpmc", TYPE_LZFSE_XATTR, st.st_size) + buf[table:end]; fork = 0
                else:
                    head = struct.pack("<4sIQ", b"fpmc", TYPE_LZFSE_RSRC, st.st_size); fork = end
                tmp = _tmp_name(path)
                tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    if fork:
                        _xattr_set(tfd, XA_RSRC, base, fork)
                    _xattr_set(tfd, XA_DECMPFS, head)
                    _carry_metadata(fd, tfd, st, (st.st_flags & ~UF_COMPRESSED) | UF_COMPRESSED)
                    _full_sync(tfd)
                finally:
                    os.close(tfd)
        finally:
            os.close(fd)
        hook("written", path, tmp)
        if not os.lstat(tmp).st_flags & UF_COMPRESSED:
            raise OSError(errno.ENOTSUP, "the volume did not accept the compressed flag")
        _replace(path, st, tmp, digest, hook)
        tmp = None
        return "done", allocated(st), allocated(os.lstat(path)), ""
    except OSError as e:
        return "error", 0, 0, e.strerror or str(e)
    finally:
        if tmp is not None:
            try: os.unlink(tmp)
            except OSError: pass


def decompress_file(path, hook=_nohook):
    """Store one compressed file plain again, in place, with the same checks. Same return shape as compress_file."""
    _load()
    tmp = None
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or not st.st_flags & UF_COMPRESSED:
            return "kept", allocated(st), allocated(st), "not compressed"
        if st.st_nlink > 1:
            return "kept", allocated(st), allocated(st), "hard link"
        if st.st_flags & KEEP_OUT_FLAGS:
            return "kept", allocated(st), allocated(st), "immutable or append-only"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if _has_acl(fd):
                return "kept", allocated(st), allocated(st), "has an ACL"
            tmp = _tmp_name(path)
            tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                h = hashlib.sha256()
                with os.fdopen(os.dup(fd), "rb") as f:
                    while True:
                        b = f.read(1 << 20)
                        if not b: break
                        h.update(b)
                        while b:
                            b = b[os.write(tfd, b):]
                _carry_metadata(fd, tfd, st, st.st_flags & ~UF_COMPRESSED)
                _full_sync(tfd)
            finally:
                os.close(tfd)
        finally:
            os.close(fd)
        hook("written", path, tmp)
        _replace(path, st, tmp, h.hexdigest(), hook)
        tmp = None
        return "done", allocated(st), allocated(os.lstat(path)), ""
    except OSError as e:
        return "error", 0, 0, e.strerror or str(e)
    finally:
        if tmp is not None:
            try: os.unlink(tmp)
            except OSError: pass


# ---- state (outside the folder: the game must not see our files) ----

def state_dir(environ):
    d = environ.get("NPS5_DATA_STATE_DIR")
    if not d:
        d = os.path.join(environ.get("HOME") or os.path.expanduser("~"), "Library", "Application Support", "nativeps5", "data")
    return d


def state_path(folder, environ):
    # canonical(): a home folder spelled in another letter case, or through the Data volume firmlink, is the same folder, so the same lock
    return os.path.join(state_dir(environ), hashlib.sha256(os.fsencode(canonical(folder))).hexdigest()[:24])


def load_state(path):
    try:
        with open(path + ".json") as f:
            s = json.load(f)
        if isinstance(s, dict) and isinstance(s.get("nogain"), dict):
            return s
    except (OSError, ValueError):
        pass
    return {"nogain": {}}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.json.%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(state, f)
        f.flush(); os.fsync(f.fileno())
    os.rename(tmp, path + ".json")


def take_lock(path):
    """One run per folder. Returns the open lock file, or raises DataError when another run holds it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path + ".lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise DataError("another `nps5 data` run is working on this folder")
    return f


def _nogain_key(st):
    return "%d:%d" % (st.st_size, st.st_mtime_ns)


# ---- survey ----

def survey(folder, min_size, state=None, now=None):
    """One pass over the folder. Returns a dict: candidates [(path, st)], compressed [(path, st)], counts by skip
    reason, stale temporary files, and byte totals."""
    nogain = (state or {}).get("nogain", {})
    r = {"candidates": [], "compressed": [], "skipped": {}, "stale": [], "files": 0, "logical": 0, "disk": 0,
         "compressed_logical": 0, "compressed_disk": 0}
    for path, st in walk(folder):
        if is_tmp(os.path.basename(path)) and stat.S_ISREG(st.st_mode):
            r["stale"].append(path); continue
        if not stat.S_ISREG(st.st_mode):
            why = skip_reason(st, min_size, now)
            r["skipped"][why] = r["skipped"].get(why, 0) + 1
            continue
        r["files"] += 1; r["logical"] += st.st_size; r["disk"] += allocated(st)
        why = skip_reason(st, min_size, now)
        if not why and nogain.get(os.path.relpath(path, folder)) == _nogain_key(st):
            why = "no gain"
        if why == "already compressed":
            r["compressed"].append((path, st)); r["compressed_logical"] += st.st_size; r["compressed_disk"] += allocated(st)
        if why:
            r["skipped"][why] = r["skipped"].get(why, 0) + 1
        else:
            r["candidates"].append((path, st))
    return r


def estimate(cands):
    """Predicted disk bytes after compressing the candidates, from a spread sample of whole files (at most
    SAMPLE_FILES files / SAMPLE_BYTES). Returns (bytes now, predicted bytes, sampled bytes)."""
    now = sum(allocated(st) for _, st in cands)
    if not cands:
        return 0, 0, 0
    by_size = sorted(cands, key=lambda c: c[1].st_size)
    step = max(1, len(by_size) // SAMPLE_FILES)
    raw = comp = 0
    for path, st in by_size[step // 2::step]:
        if raw >= SAMPLE_BYTES:
            break
        try:
            with open(path, "rb") as f:
                data = f.read(SAMPLE_BYTES - raw)
        except OSError:
            continue
        chunks = lzfse_chunks(data)
        if chunks is None:
            continue
        a = (len(data) + 4095) // 4096 * 4096
        raw += a; comp += min(a, stored_bytes(len(data), chunks))
    if raw == 0:
        return now, now, 0
    return now, int(now * comp / raw), raw


def gb(n):
    return "%.2f GB" % (n / 1e9)


def free_bytes(folder):
    v = os.statvfs(folder)
    return v.f_bavail * v.f_frsize


SYSTEM_DIRS = ("/System", "/Library", "/Applications", "/usr", "/bin", "/sbin", "/opt", "/cores", "/private/etc",
               "/private/var/db", "/private/var/root", "/private/var/log")


def _within(a, b):
    a, b = a.lower(), b.lower()                  # the Mac's disk ignores letter case: /users/x/library is ~/Library
    return a == b or a.startswith(b.rstrip("/") + "/")


def canonical(path):
    """The path as the kernel names the folder: links resolved, the real letter case, and firmlinks folded
    (the Data volume path of a home folder is the home folder). realpath() alone does neither of the last two."""
    r = os.path.realpath(path)
    try:
        fd = os.open(r, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return r
    try:
        return os.fsdecode(fcntl.fcntl(fd, F_GETPATH, b"\0" * 1024).split(b"\0")[0]) or r
    except OSError:
        return r
    finally:
        os.close(fd)


def refused_place(folder, environ):
    """Why `folder` must not be changed ('' = it may be). This tool is for a game folder: its files are only read.
    A whole volume, the home folder or a system folder holds files other programs keep open for writing (databases,
    logs), and a write through a descriptor opened before the rename would land in the replaced file."""
    r = canonical(folder)
    home = canonical(environ.get("HOME") or os.path.expanduser("~"))
    try:
        top = fs_mount_point(r)
    except DataError:
        top = None
    if r == "/" or os.path.ismount(r) or r == top:
        return "it is the top of a volume; name the game folder on it"
    for d in SYSTEM_DIRS:
        if _within(r, d) or _within(d, r):
            return "it is a system folder (%s)" % d
    if _within(home, r):
        return "it is your home folder or contains it; name the game folder"
    if _within(r, os.path.join(home, "Library")):
        return "it is inside ~/Library, where applications keep their files"
    return ""


def check_folder(folder, environ=None, writing=False):
    if not os.path.isdir(folder):
        raise DataError("not a folder: %s" % folder)
    if os.path.islink(folder.rstrip("/")):
        raise DataError("the folder is a symbolic link; name the real folder: %s" % os.path.realpath(folder))
    t = fs_type(folder)
    if t != "apfs":
        raise DataError("this folder is on a %s volume. Transparent compression exists only on APFS (the format of a Mac's "
                        "internal disk): copy the game folder to an APFS disk and run this there." % (t or "non-APFS"))
    if not writing:
        return
    why = refused_place(folder, environ if environ is not None else os.environ)
    if why:
        raise DataError("refusing %s: %s" % (folder, why))
    if fs_readonly(folder):
        raise DataError("the volume holding %s is read-only" % folder)


# ---- commands ----

def _work(job):
    kind, path, min_size = job
    try:
        return (path,) + (compress_file(path, min_size) if kind == "compress" else decompress_file(path))
    except Exception as e:                       # a worker must never die with a file half done
        return path, "error", 0, 0, "%s: %s" % (type(e).__name__, e)


def _wait_pause(pause, out, quiet):
    said = False
    while pause and os.path.exists(pause):
        if not said and not quiet:
            print("paused: %s exists" % pause, file=out, flush=True); said = True
        time.sleep(2)


def run_jobs(kind, folder, cands, o, environ, state, spath, out):
    """Process the candidates with o['jobs'] workers. Returns (done, kept, errors [(path, detail)], before, after)."""
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    total = len(cands); total_bytes = sum(st.st_size for _, st in cands)
    done = kept = 0; errors = []; before = after = seen_bytes = 0
    t0 = last = time.time(); saved_at = [0.0]
    need = max((st.st_size for _, st in cands), default=0)
    jobs = max(1, min(o["jobs"], total or 1))
    sizes = {p: st for p, st in cands}

    def feed():
        for path, st in cands:
            _wait_pause(o["pause"], out, o["quiet"])
            if free_bytes(folder) < st.st_size + need * (jobs - 1) + RUN_MARGIN:
                raise DataError("stopped: the disk is nearly full (the files done so far are complete; run again after freeing space)", 1)
            yield kind, path, o["min_size"]

    pool = ThreadPoolExecutor(jobs)              # threads: the hashing, the LZFSE encoder and the file calls release the GIL
    stop = threading.Event()

    def results():
        # at most `jobs` files in flight, so a pause or a stop takes effect between files
        # and at most INFLIGHT_BYTES of files in flight (each worker holds its file's compressed form in memory)
        pending = {}; it = feed(); nxt = None
        try:
            while True:
                while len(pending) < jobs and not stop.is_set():
                    nxt = nxt or next(it, None)
                    if nxt is None: break
                    size = sizes[nxt[1]].st_size
                    if pending and sum(pending.values()) + size > INFLIGHT_BYTES: break
                    pending[pool.submit(_work, nxt)] = size; nxt = None
                if not pending: return
                finished, _ = wait(list(pending), return_when=FIRST_COMPLETED)
                for f in finished:
                    del pending[f]; yield f.result()
        finally:
            stop.set()
            wait(list(pending))                  # never leave a file half done behind us

    try:
        for path, status, b, a, detail in results():
            st = sizes[path]; seen_bytes += st.st_size
            if status == "done":
                done += 1; before += b; after += a
            elif status == "kept":
                kept += 1
                if detail == "no gain":
                    state["nogain"][os.path.relpath(path, folder)] = _nogain_key(st)
            else:
                errors.append((path, detail))
            now = time.time()
            if now - last >= 5 or done + kept + len(errors) == total:
                last = now
                if saved_at[0] + 30 < now:
                    save_state(spath, state); saved_at[0] = now
                if not o["quiet"]:
                    rate = seen_bytes / max(now - t0, 1e-3)
                    eta = (total_bytes - seen_bytes) / rate if rate > 0 else 0
                    print("%6d / %d files  %s of %s  %s on disk now %s  %.0f MB/s  about %d min left" % (
                        done + kept + len(errors), total, gb(seen_bytes), gb(total_bytes),
                        "freed" if kind == "compress" else "added", gb(abs(before - after)), rate / 1e6, eta / 60 + 0.5), file=out, flush=True)
    except KeyboardInterrupt:
        stop.set()
        raise DataError("interrupted: every file is either done or untouched; run the same command again to continue", 1)
    finally:
        pool.shutdown(wait=True)
        save_state(spath, state)
    return done, kept, errors, before, after


def _tmp_alive(path):
    """True when the temporary file belongs to a process that is still running (another `nps5 data` run on an
    overlapping folder, e.g. its parent): that run still needs it, and it removes it itself."""
    try:
        pid = int(os.path.basename(path)[len(TMP_PREFIX):].split("-")[0])
    except ValueError:
        return False
    if pid == os.getpid() or pid <= 0:            # cleaning runs before this process makes any: ours are stale
        return False
    try:                                         # a pid is reused: a file no live run touched for a day is a leftover
        if time.time() - os.lstat(path).st_ctime > STALE_TMP_S:
            return False
    except OSError:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _clean_stale(folder, paths=None):
    n = 0
    for p in (paths if paths is not None else [p for p, st in walk(folder) if is_tmp(os.path.basename(p)) and stat.S_ISREG(st.st_mode)]):
        if _tmp_alive(p):
            continue
        try:
            os.unlink(p); n += 1
        except OSError:
            pass
    return n


def _reasons(sk):
    return ", ".join("%d %s" % (n, why) for why, n in sorted(sk.items(), key=lambda kv: -kv[1])) or "none"


def cmd_status(folder, o, environ, out):
    check_folder(folder)
    r = survey(folder, o["min_size"], load_state(state_path(folder, environ)))
    print("%s: %d files, %s of data, %s on disk" % (folder, r["files"], gb(r["logical"]), gb(r["disk"])), file=out)
    print("compressed: %d files, %s of data in %s on disk (saves %s)" % (
        len(r["compressed"]), gb(r["compressed_logical"]), gb(r["compressed_disk"]), gb(max(0, r["compressed_logical"] - r["compressed_disk"]))), file=out)
    print("not compressed yet: %d files, %s on disk" % (len(r["candidates"]), gb(sum(allocated(st) for _, st in r["candidates"]))), file=out)
    print("left alone: %s" % _reasons({k: v for k, v in r["skipped"].items() if k != "already compressed"}), file=out)
    if r["stale"]:
        print("%d leftover temporary file(s) of an interrupted run (the next `compress --apply` removes them)" % len(r["stale"]), file=out)
    return 0


def cmd_compress(folder, o, environ, out):
    check_folder(folder, environ, writing=True)
    spath = state_path(folder, environ)
    if not o["apply"]:
        r = survey(folder, o["min_size"], load_state(spath))
        now, pred, sampled = estimate(r["candidates"])
        print("DRY RUN (nothing is changed; add --apply to compress)", file=out)
        print("%s: %d files, %s on disk; %d already compressed" % (folder, r["files"], gb(r["disk"]), len(r["compressed"])), file=out)
        print("to compress: %d files, %s on disk -> about %s (estimate from a %s sample; saves about %s)" % (
            len(r["candidates"]), gb(now), gb(pred), gb(sampled), gb(max(0, now - pred))), file=out)
        print("left alone: %s" % _reasons({k: v for k, v in r["skipped"].items() if k != "already compressed"}), file=out)
        if r["skipped"].get(RECENT):
            print("note: %d file(s) changed in the last minute are not counted; wait a minute before --apply" % r["skipped"][RECENT], file=out)
        big = max((st.st_size for _, st in r["candidates"]), default=0)
        print("free space: %s (needs %s while it works: room for the largest file per worker)" % (gb(free_bytes(folder)), gb(big * o["jobs"] + START_MARGIN)), file=out)
        return 0
    lock = take_lock(spath)
    try:
        state = load_state(spath)
        r = survey(folder, o["min_size"], state)
        if r["stale"]:
            n = _clean_stale(folder, r["stale"])
            if not o["quiet"]: print("removed %d leftover temporary file(s) of an interrupted run" % n, file=out)
        big = max((st.st_size for _, st in r["candidates"]), default=0)
        jobs = max(1, min(o["jobs"], len(r["candidates"]) or 1))
        if r["candidates"] and free_bytes(folder) < big * jobs + START_MARGIN:
            raise DataError("not enough free space to work safely: %s free, %s needed (room for the largest file per worker). "
                            "Free some space or use --jobs 1." % (gb(free_bytes(folder)), gb(big * jobs + START_MARGIN)))
        if not o["quiet"]:
            print("compressing %d files (%s) with %d worker(s); safe to stop with Ctrl-C and run again" % (
                len(r["candidates"]), gb(sum(st.st_size for _, st in r["candidates"])), jobs), file=out, flush=True)
        done, kept, errors, before, after = run_jobs("compress", folder, r["candidates"], o, environ, state, spath, out)
    finally:
        lock.close()
    print("done: %d files compressed, %s -> %s on disk (freed %s); %d left as they were; %d failed" % (
        done, gb(before), gb(after), gb(max(0, before - after)), kept, len(errors)), file=out)
    recent = r["skipped"].get(RECENT, 0)
    if recent:
        print("NOT DONE YET: %d file(s) changed in the last minute were left alone (a copy or a decompress just wrote "
              "them; a file being written must not be replaced). Run the same command again in a minute." % recent, file=out)
    for p, d in errors[:20]:
        print("  failed (file unchanged): %s: %s" % (os.path.relpath(p, folder), d), file=out)
    if not errors:
        try:     # remembered so `nps5 cache prepare` defaults its "compress the shader cache too?" question to yes
            import importlib.util
            sp = importlib.util.spec_from_file_location("nps5_cachezip", os.path.join(os.path.dirname(os.path.abspath(__file__)), "cachezip.py"))
            cz = importlib.util.module_from_spec(sp); sp.loader.exec_module(cz)
            cz.record_game_answer(environ, True)
        except Exception:
            pass
    return 1 if errors else (3 if recent else 0)


def cmd_decompress(folder, o, environ, out):
    check_folder(folder, environ, writing=True)
    spath = state_path(folder, environ)
    r = survey(folder, o["min_size"])
    grow = r["compressed_logical"] - r["compressed_disk"]
    if not o["apply"]:
        print("DRY RUN (nothing is changed; add --apply to decompress)", file=out)
        print("%s: %d compressed files, %s on disk -> %s plain (needs %s more; %s free)" % (
            folder, len(r["compressed"]), gb(r["compressed_disk"]), gb(r["compressed_logical"]), gb(max(0, grow)), gb(free_bytes(folder))), file=out)
        return 0
    if free_bytes(folder) < grow + DECOMPRESS_MARGIN:
        raise DataError("not enough free space to decompress: %s more is needed, %s free" % (gb(grow + DECOMPRESS_MARGIN), gb(free_bytes(folder))))
    lock = take_lock(spath)
    try:
        if r["stale"]: _clean_stale(folder, r["stale"])
        state = load_state(spath)
        done, kept, errors, before, after = run_jobs("decompress", folder, r["compressed"], o, environ, state, spath, out)
    finally:
        lock.close()
    print("done: %d files stored plain again, %s -> %s on disk; %d left as they were; %d failed" % (done, gb(before), gb(after), kept, len(errors)), file=out)
    for p, d in errors[:20]:
        print("  failed (file unchanged): %s: %s" % (os.path.relpath(p, folder), d), file=out)
    return 1 if errors else 0


def main(argv, environ=None, out=None):
    environ = dict(os.environ if environ is None else environ)
    out = out or sys.stdout
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__, file=out); return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd not in ("status", "compress", "decompress"):
        print("nps5 data: unknown command %s (status, compress, decompress)" % cmd, file=sys.stderr); return 2
    o = {"apply": False, "jobs": max(1, min(4, (os.cpu_count() or 2) // 2)), "min_size": 4096, "pause": None, "quiet": False}
    folder, i = None, 0
    try:
        while i < len(rest):
            a = rest[i]
            if a == "--apply": o["apply"] = True
            elif a == "--quiet": o["quiet"] = True
            elif a in ("--jobs", "--min-size", "--pause-while"):
                if i + 1 >= len(rest): raise DataError("%s needs a value" % a)
                v = rest[i + 1]; i += 1
                if a == "--pause-while": o["pause"] = v
                else:
                    if not v.isdigit() or (a == "--jobs" and not 1 <= int(v) <= 64): raise DataError("%s: a number is needed" % a)
                    o["jobs" if a == "--jobs" else "min_size"] = int(v)
            elif a.startswith("-"): raise DataError("unknown option %s" % a)
            elif folder is None: folder = a
            else: raise DataError("unexpected argument %s" % a)
            i += 1
        if folder is None: raise DataError("which folder? nps5 data %s <folder>" % cmd)
        folder = os.path.abspath(folder)
        _load()
        return {"status": cmd_status, "compress": cmd_compress, "decompress": cmd_decompress}[cmd](folder, o, environ, out)
    except DataError as e:
        print("nps5 data: %s" % e, file=sys.stderr); return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
