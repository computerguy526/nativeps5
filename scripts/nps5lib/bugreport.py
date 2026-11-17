#!/usr/bin/env python3
"""nps5 bug-report: collect what a bug report needs into ONE zip of text files, and never any game data.

  nps5 bug-report [<run>|--last] [--out FILE.zip] [--dry-run] [--runs-root DIR]

<run> is a run directory or a TAG under the runs root (default: the newest run, --last). The zip holds only the files named in
ALLOWED below: nothing else is ever read from the run directory, so a shader, dump, journal, cache or save cannot get in by a
new file turning up. Every text is redacted (home folder -> ~, user and host names, e-mail addresses, serial numbers and UUIDs,
key-looking lines) and cut to a size cap; the log is trimmed and de-duplicated (first and last lines, every WARN/ERROR/FATAL/
crash/assert line, one sample of each repeated line with its count). The file list is printed before the zip is written;
--dry-run stops there. Exit codes: 0 done, 1 nothing written (no run, a redaction leak found, write error), 2 usage.
Idea and layout after NXNative's scripts/bug_report.py (MIT licence); the code is written for this repository."""
from __future__ import annotations

import getpass
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from logsummary import IMPORTANT, LOG_HEAD, LOG_IMPORTANT_REPEAT, LOG_SAMPLES, LOG_TAIL, norm, summary, trim_log  # noqa: E402,F401

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
TOTAL_CAP = 8 * 1024 * 1024      # all text in the zip, before compression
FILE_CAP = 1024 * 1024           # one file: longer ones keep the first and last half

# The allow-list: the only things this tool reads. Anything else is not on it, so it cannot get in.
ALLOWED = (
    "run.json, summary.txt, crash.txt, manifest.json of the run directory (text, redacted, size-capped)",
    "run.err and run.out of the run directory (or <logs>/<tag>.err/.out next to a private run bundle), trimmed and de-duplicated",
    "`nps5 doctor` and the hardware report (machine class, macOS, chip, memory)",
    "components.lock, the commit of each component checkout and whether it has local changes (a count, no file names)",
    "the settings in effect: environment words of run.json / NPS5_*, KYTY_*, APS5_*, ANYPS5_*, KK_* of this shell, with secrets removed",
    "the profile's games/<id>/game.tsv (repository text: preset, settings, scenes)",
    "macOS and Xcode versions, and the names and USB ids of connected game controllers",
)
SECRET_NAME = re.compile(r"(?i)(key|secret|token|passw|credential|auth|cookie|session|cert|private)")
ENV_PREFIX = ("NPS5_", "KYTY_", "APS5_", "ANYPS5_", "KK_", "PS4_", "VK_", "MTL_")
ENV_PATH_ONLY = re.compile(r"(?i)(?:DIR|PATH|ROOT|HOME|FILE|DUMP|LIBS?|FOLDER)$")

HEXKEY = re.compile(r"(?i)(?:key|secret|token|passw\w*)[\w\s=:\"'\[\],]*[0-9a-f]{32,}")
EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,128}\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(r"(noreply|no-reply|@localhost|@example\.|@invalid|^git@)", re.I)
UUID = re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b")
SERIAL = re.compile(r"(?i)\b(serial(?:[ _-]?number)?|udid|hardware[ _-]?uuid|provisioning[ _-]?udid)\b(\W{0,6})[A-Za-z0-9-]{8,}")
USERPATH = re.compile(r"(?<![\w])(/Users|/home)/(?!Shared\b|Public\b|\.\.\.)[^/\s'\"`<>)]+")
WINPATH = re.compile(r"[A-Za-z]:\\Users\\(?!Public\b)[^\\\s'\"]+")
VOLUME = re.compile(r"/Volumes/(?!Macintosh HD\b)[^/\n'\"`]+")
VOLUME_SP = re.compile(r"/Volumes/(?!Macintosh HD\b)[^/\n'\"`]*")


SECRET_ASSIGN = re.compile(r"(?i)\b([A-Za-z0-9_.-]*(?:key|secret|token|passw|credential|cookie|private)[A-Za-z0-9_.-]*)(\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;\"']+)")


def mask_secrets(o):
    """A parsed JSON value with every value under a secret-looking key replaced (the names stay)."""
    if isinstance(o, dict):
        return {k: ("<removed>" if SECRET_NAME.search(str(k)) and not isinstance(v, (dict, list)) else mask_secrets(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [mask_secrets(v) for v in o]
    return o


class Redactor:
    def __init__(self, home=None, user=None, hosts=None):
        self.home = [h for h in {home, os.path.realpath(home) if home else None} if h and h != "/"]
        self.user = user if user and len(user) >= 3 else None
        self.hosts = [h for h in (hosts or []) if h and len(h) >= 3]
        self.dropped = 0

    def __call__(self, text: str) -> str:
        out = []
        for line in text.splitlines(keepends=True):
            if HEXKEY.search(line):
                self.dropped += 1
                continue
            for h in sorted(self.home, key=len, reverse=True):
                line = line.replace(h, "~")
            line = SECRET_ASSIGN.sub(lambda m: m.group(1) + m.group(2) + "<removed>", line)
            line = USERPATH.sub(lambda m: "~", line)
            line = WINPATH.sub("~", line)
            line = VOLUME_SP.sub("/Volumes/<volume>", line)
            for h in self.hosts:
                line = re.sub(r"(?i)(?<![A-Za-z0-9-])" + re.escape(h) + r"(?![A-Za-z0-9-])", "<host>", line)
            if self.user:
                line = re.sub(r"(?<![A-Za-z0-9])" + re.escape(self.user) + r"(?![A-Za-z0-9])", "<user>", line)
            line = EMAIL.sub(lambda m: m.group(0) if EMAIL_OK.search(m.group(0)) else "<email>", line)
            line = SERIAL.sub(lambda m: m.group(1) + m.group(2) + "<serial>", line)
            line = UUID.sub("<uuid>", line)
            out.append(line)
        if self.dropped:
            out.append("[bug-report: %d line(s) that look like key material were removed]\n" % self.dropped)
            self.dropped = 0
        return "".join(out)

    def leaks(self, text: str) -> list[str]:
        """What is still in an already redacted text that should not be (a self-check of the final zip content)."""
        bad = [m.group(0) for m in USERPATH.finditer(text)] + [m.group(0) for m in WINPATH.finditer(text)]
        bad += [m.group(0) for m in EMAIL.finditer(text) if not EMAIL_OK.search(m.group(0))]
        bad += [h for h in self.home if h in text]
        bad += [h for h in self.hosts if re.search(r"(?i)(?<![A-Za-z0-9-])" + re.escape(h) + r"(?![A-Za-z0-9-])", text)]
        return bad


def cut(text: str, cap: int = FILE_CAP) -> str:
    b = text.encode("utf-8", "replace")
    if len(b) <= cap:
        return text
    mid = "\n[bug-report: %d bytes cut here; the first and last parts are kept]\n" % (len(b) - cap)
    h = max(0, (cap - len(mid)) // 2)
    return b[:h].decode("utf-8", "ignore") + mid + b[-h:].decode("utf-8", "ignore")


def filter_env(env: dict) -> dict:
    """Names and values of the settings; a name that looks like a secret keeps its name but not its value."""
    out = {}
    for k in sorted(env):
        v = str(env[k])
        out[k] = "<removed>" if SECRET_NAME.search(k) else v
    return out


def env_text(env: dict, title: str) -> str:
    lines = ["# %s" % title] + ["%s=%s" % (k, v) for k, v in filter_env(env).items()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- collection

def sh(argv, timeout=30, run=subprocess.run):
    try:
        r = run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (r.stdout or "") + (("\n" + r.stderr) if r.returncode and r.stderr else "")


def read_file(path: str, cap: int = FILE_CAP):
    """A regular text file's content (symlinks, binary and non-regular files refused), or None."""
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        head = f.read(4096)
        if b"\0" in head:
            return None
        f.seek(0, 2); size = f.tell(); f.seek(0)
        if size <= 4 * cap:
            return f.read().decode("utf-8", "replace")
        a = f.read(2 * cap); f.seek(size - 2 * cap)           # a huge log: both ends only (the trim reads the rest of the lines it needs)
        return a.decode("utf-8", "replace") + "\n[bug-report: %d bytes cut here]\n" % (size - 4 * cap) + f.read().decode("utf-8", "replace")


def runs_roots(environ: dict) -> list[str]:
    root = environ.get("NPS5_RUNS_ROOT") or os.path.join(environ.get("NPS5_SCRATCH") or os.path.join(REPO, "scratch"), "runs")
    roots = [root]
    if environ.get("NPS5_RUNS_BY_TAG"):
        roots.append(environ["NPS5_RUNS_BY_TAG"])
    return roots


def is_run_dir(d: str) -> bool:
    return os.path.isdir(d) and any(os.path.isfile(os.path.join(d, n)) for n in ("run.json", "summary.txt", "crash.txt", "manifest.json"))


def find_runs(roots):
    """Every run directory (one that holds run.json, summary.txt, crash.txt or manifest.json) up to three levels below a root."""
    found = []
    for root in roots:
        for d, dirs, _ in os.walk(root):
            depth = d[len(root):].count(os.sep)
            if is_run_dir(d):
                found.append(d); dirs[:] = []
            elif depth >= 3:
                dirs[:] = []
    return found


def resolve(spec, environ):
    """A run directory from a path, a tag, <title>/<tag>, or None (newest run)."""
    if spec and is_run_dir(spec):
        return spec
    roots = runs_roots(environ)
    runs = find_runs(roots)
    if spec:
        t = spec.replace("\\", "/").rstrip("/")
        hit = [d for d in runs if d.replace("\\", "/").endswith("/" + t) or os.path.basename(d) == t]
        if not hit:       # a tag under a private runs_by_tag: <tag>/<ts>-eboot-run, newest wins
            hit = [d for d in runs if os.path.basename(os.path.dirname(d)) == t]
        runs = hit
    runs = [d for d in runs if not os.path.islink(d)]
    return max(runs, key=lambda d: os.path.getmtime(d)) if runs else None


def private_logs(run_dir: str, tag: str) -> list[str]:
    """A private run bundle <boot>/runs_by_tag/<tag>/<ts>-run keeps its text log at <boot>/logs/<tag>.err (and .out)."""
    up = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(run_dir))))
    return [os.path.join(up, "logs", tag + ext) for ext in (".err", ".out")]


def component_state(environ, runner=sh):
    """name -> commit and local-change count, for the checkouts that can be found."""
    names = dict(nativeps5=REPO, ps5recomp=environ.get("NPS5_RECOMP_DIR"), kyty=environ.get("NPS5_KYTY_DIR"), anyps5=environ.get("NPS5_ANYPS5_DIR"))
    base = os.path.dirname(REPO)
    for n in ("ps5recomp", "kyty", "anyps5"):
        for c in (os.path.join(base, n), os.path.join(REPO, n)):
            if not names.get(n) and os.path.isdir(os.path.join(c, ".git")) or (not names.get(n) and os.path.isfile(os.path.join(c, ".git"))):
                names[n] = c
    out = []
    for n, d in names.items():
        if not d or not os.path.isdir(d):
            out.append("%-10s not found" % n); continue
        head = runner(["git", "-C", d, "rev-parse", "HEAD"], 10).strip()[:40] or "unknown"
        st = [l for l in runner(["git", "-C", d, "status", "--porcelain", "--untracked-files=no"], 20).splitlines() if l.strip()]
        out.append("%-10s %s %s" % (n, head, "dirty (%d changed file(s))" % len(st) if st else "clean"))
    return "\n".join(out) + "\n"


CONTROLLER = re.compile(r"(?i)controller|gamepad|dualsense|dualshock|xbox|joy-?con|wireless controller|pro controller")


def controllers(runner=sh) -> str:
    """Names and vendor/product ids of connected game controllers (never serial numbers)."""
    text = runner(["ioreg", "-r", "-c", "IOHIDDevice", "-l", "-w0"], 30)
    seen, out = set(), []
    for blk in re.split(r"\n(?=\S*\+-o )", text):
        m = re.search(r'"Product" = "([^"]+)"', blk)
        if not m or not CONTROLLER.search(m.group(1)):
            continue
        v, p = re.search(r'"VendorID" = (\d+)', blk), re.search(r'"ProductID" = (\d+)', blk)
        row = "%s (vendor 0x%04x product 0x%04x)" % (m.group(1), int(v.group(1)) if v else 0, int(p.group(1)) if p else 0)
        if row not in seen:
            seen.add(row); out.append(row)
    return "\n".join(out) + "\n" if out else "none seen\n"


def load_hw():
    import importlib.util
    spec = importlib.util.spec_from_file_location("nps5_hw", os.path.join(HERE, "hw.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def hardware_text(environ, runner=sh) -> str:
    buf = io.StringIO()
    try:
        hw = load_hw()
        hw.doctor(hw.detect(environ, full=True), buf)
    except Exception as e:                       # a report must still be written on an odd machine
        buf.write("hardware report failed: %s\n" % type(e).__name__)
    buf.write("\nmacOS (sw_vers)\n" + runner(["sw_vers"], 10))
    buf.write("\nXcode\n" + (runner(["xcodebuild", "-version"], 20).strip() or "not installed or not selected") + "\n")
    return buf.getvalue()


def collect(run_dir, environ, runner=sh, doctor=None):
    """-> (list of (arcname, raw text), notes). Raw = not yet redacted."""
    files, notes = [], []
    tag = os.path.basename(os.path.normpath(run_dir)) if run_dir else "none"
    rj = {}
    if run_dir:
        for name in ("run.json", "summary.txt", "crash.txt", "manifest.json"):
            t = read_file(os.path.join(run_dir, name))
            if t is None:
                continue
            if name == "run.json":
                try:
                    rj = json.loads(t)
                    rj = mask_secrets(rj) if isinstance(rj, dict) else {}
                    t = json.dumps(rj, indent=1, sort_keys=True) + "\n"
                except ValueError:
                    rj, t = {}, "run.json is not valid JSON; left out\n"
            files.append((name, cut(t)))
        if rj.get("tag"):
            tag = str(rj["tag"])
        logs = [os.path.join(run_dir, n) for n in ("run.err", "run.out")]
        if not any(os.path.isfile(p) for p in logs):
            logs = private_logs(run_dir, os.path.basename(os.path.dirname(os.path.normpath(run_dir))))
        for p in logs:
            t = read_file(p)
            if t is not None:
                ext = os.path.splitext(p)[1]
                files.append(("log" + ext + ".txt", cut(trim_log(t, tail=LOG_TAIL if ext == ".err" else 40, samples=LOG_SAMPLES if ext == ".err" else 200))))
    else:
        notes.append("no run found: the report has the machine, components and settings only")
    if run_dir and rj:
        try:
            eb = read_file(os.path.join(run_dir, "run.err")) or ""
            files.append(("run-summary.txt", summary(eb, rj)))
        except Exception:
            pass
    d = doctor() if doctor else sh([sys.executable, os.path.join(HERE, "..", "nps5"), "doctor"], 90, subprocess.run)
    files.append(("doctor.txt", d.strip() + "\n" if d.strip() else "`nps5 doctor` printed nothing (Go nps5 not built?)\n"))
    files.append(("machine.txt", hardware_text(environ, runner)))
    lock = read_file(os.path.join(REPO, "components.lock"))
    files.append(("components.txt", "# commits of the checkouts found\n" + component_state(environ, runner) +
                  ("\n# run's recorded components (run.json)\n" + json.dumps(rj.get("components"), indent=1, sort_keys=True) + "\n" if rj.get("components") else "") +
                  ("\n# components.lock\n" + lock if lock else "")))
    env = rj.get("env") if isinstance(rj.get("env"), dict) else None
    sett = env_text(env, "environment words of the run (run.json env)") if env else ""
    shell = {k: v for k, v in environ.items() if k.startswith(ENV_PREFIX)}
    sett += env_text(shell, "NPS5_*/KYTY_*/APS5_*... set in the shell running this command") if shell else "# no NPS5_*/KYTY_*/... words set in this shell\n"
    arm = rj.get("arm")
    if arm:
        sett += "\n# arm\n" + json.dumps(arm, indent=1, sort_keys=True) + "\n"
    files.append(("settings.txt", sett))
    tid = str(rj.get("title") or "")
    if re.fullmatch(r"(?:PPSA|CUSA|PCAS|PLAS)\d{5}", tid):
        gt = read_file(os.path.join(REPO, "games", tid, "game.tsv"))
        if gt:
            files.append(("game.tsv", gt))
    files.append(("controllers.txt", controllers(runner)))
    return files, notes, tag


def build(files, redact: Redactor):
    """-> (list of (arcname, redacted text)), refusing on a leak left after redaction."""
    out, total = [], 0
    for name, text in files:
        r = redact(text)
        left = redact.leaks(r)
        if left:
            raise ValueError("%s still holds %d personal item(s) after redaction (e.g. %r): not written" % (name, len(left), left[0][:3] + "..."))
        total += len(r.encode("utf-8", "replace"))
        if total > TOTAL_CAP:
            r = "[bug-report: omitted, the report reached its %d MiB cap]\n" % (TOTAL_CAP >> 20)
        out.append((name, r))
    return out


def readme(tag, names, notes) -> str:
    return ("nps5 bug-report for run %s, made %s.\nText only. Redacted: home folder, user and host names, e-mail addresses, serials, UUIDs.\n"
            "Never included: game files, shaders, dumps, journals, caches, saves, pictures.\nFiles:\n%s\n%s"
            % (tag, time.strftime("%Y-%m-%d %H:%M:%S"), "\n".join("  " + n for n in names), "".join("Note: %s\n" % n for n in notes)))


def main(argv, environ=None, runner=sh, doctor=None, out=sys.stdout):
    environ = dict(os.environ if environ is None else environ)
    spec, outp, dry, i = None, None, False, 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(__doc__); return 0
        if a == "--dry-run": dry = True
        elif a == "--last": spec = None
        elif a == "--runs-root" and i + 1 < len(argv): environ["NPS5_RUNS_ROOT"] = argv[i + 1]; i += 1
        elif a == "--out" and i + 1 < len(argv): outp = argv[i + 1]; i += 1
        elif a.startswith("-"):
            print("bug-report: unknown option %s" % a, file=sys.stderr); return 2
        elif spec is None: spec = a
        else:
            print("bug-report: unexpected argument %s" % a, file=sys.stderr); return 2
        i += 1
    run_dir = resolve(spec, environ)
    if spec and not run_dir:
        print("bug-report: no such run: %s" % spec, file=sys.stderr); return 1
    files, notes, tag = collect(run_dir, environ, runner, doctor)
    home = environ.get("HOME") or os.path.expanduser("~")
    hosts = {socket.gethostname(), socket.gethostname().split(".")[0], runner(["scutil", "--get", "ComputerName"], 5).strip(), runner(["scutil", "--get", "LocalHostName"], 5).strip()}
    redact = Redactor(home, environ.get("USER") or getpass.getuser(), sorted(h for h in hosts if h))
    try:
        built = build(files, redact)
    except ValueError as e:
        print("bug-report: %s" % e, file=sys.stderr); return 1
    safe_tag = re.sub(r"[^A-Za-z0-9_.+-]", "_", tag)[:60]
    built.insert(0, ("README.txt", readme(tag, [n for n, _ in built], notes)))
    dest = outp or "nps5-bug-report-%s-%s.zip" % (safe_tag, time.strftime("%Y%m%d-%H%M%S"))
    out.write("run: %s\nthe zip %s holds (text only):\n" % (("<runs>/" + os.path.basename(run_dir)) if run_dir else "(none)", dest))
    for n, t in built:
        out.write("  %-16s %8d bytes\n" % (n, len(t.encode("utf-8", "replace"))))
    for n in notes:
        out.write("note: %s\n" % n)
    out.write("allow-list (nothing else is read):\n" + "".join("  - %s\n" % a for a in ALLOWED))
    if dry:
        out.write("dry run: nothing written\n"); return 0
    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for n, t in built:
                z.writestr(zipfile.ZipInfo(n, (2026, 1, 1, 0, 0, 0)), t, zipfile.ZIP_DEFLATED)
    except OSError as e:
        print("bug-report: cannot write %s: %s" % (dest, e.strerror), file=sys.stderr); return 1
    out.write("wrote %s\nCheck it, then attach it to the issue (docs: CONTRIBUTING.md, Reporting a problem).\n" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
