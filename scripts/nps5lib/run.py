#!/usr/bin/env python3
"""nps5 run: one title run under the machine-safety rules. Python 3, standard library only.

  nps5 run <id> [--scene NAME] [--until SPEC[+N]] [--secs N] [--class func|perf] [--tag T]
               [--gpu kyty|anyps5] [--bin PATH] [--patch NAMES] [--arm LABEL:KEY=VAL,...]
               [--env KEY=VAL]... [--workdir NAME] [--save NAME] [--play] [--dry-run]

Environment, lowest to highest: safety base < profile (games/<id>/game.tsv: preset, rows, schema keys, scene rows)
< titles/<id>.env ENV < scene pad < --arm < --env. The final set is then checked (env_forbidden, GPU profiling
ban, memory limit never above the machine's, run length cap, one title at a time, no second GPU process).
--play  a person playing at the keyboard, not a test: sound on, no --until target and no early stop by the watcher, up
to 7200 s (the default) without NPS5_RUN_REASON. Every other safety rule stays. Not for scripts or agents: the 180 s cap
is what bounds an unattended run.
--arm LABEL:K=V,...  K=V sets an environment word; the reserved keys bin= patch= gpu= libs= (or lib=) kk= kyty=
workdir= cache= save= select pieces instead. --dry-run prints the final environment and the command and stops.
Every run writes <runs>/<id>/<tag>/run.json (see RUN_JSON_KEYS). Exit codes: 0 ok, 2 usage, 75 busy (lock or GPU
process), 78 refused by a safety rule, 99 the run modified the dump, 130 interrupted; otherwise the title's own status.
Verdicts in run.json: DONE_UNTIL (the --until target was reached), DONE_FIXED (no target: ran its seconds), TIMEOUT (target
not reached in time), ENDED_EARLY (the title exited by itself before the target), EARLY_BLACK, EARLY_STALL, CRASH,
FAIL_RC<n>, DUMP_MODIFIED, INTERRUPTED / ERROR (the runner was stopped or failed; the title is stopped with it).
A tag names one run: a run folder that already holds a run.json is set aside as <tag>.prev-<time>, never mixed."""
import ctypes, hashlib, importlib.util, json, os, re, shlex, shutil, signal, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCHEMA = 1
RESERVED = ("bin", "patch", "gpu", "libs", "lib", "kk", "kyty", "workdir", "cache", "save")
CLASSES = ("func", "perf")
RUN_CAP_S, RUN_NO_REASON_S, GPU_PROFILE_MAX_S = 180, 90, 40
CACHE_RUN_CAP_S = 1800                            # `nps5 cache prepare` passes (is_prepare_run): the list ends them, secs is only their ceiling
PLAY_CAP_S = 7200                                 # --play: an interactive session
NAME_RE = r"[A-Za-z0-9_][A-Za-z0-9_.+:-]*"       # a tag, a save name, a relative work dir: one path component, never .. or a path
RUN_JSON_KEYS = ("schema", "title", "tag", "class", "gpu", "scene", "until", "secs", "arm", "build", "lineage", "patches",
                 "binary", "components", "env", "machine", "command", "timings", "rc", "verdict", "logs", "warnings")
TITLES_KEYS = ("BIN", "BIN_DIR", "APP0", "GPU", "ENV", "KK", "LIBS", "KYTY_LIB", "WORKDIR", "USERDATA", "DUMP_VERIFY")
BASE_WORDS = ("NPS5_AUDIO_NULL=1", "PS4_RECOMP_HEADLESS=1", "PS4_NO_DIALOGS=1", "PS4_LANG=en")
PROFILE_BAN = ("APS5_PROFILE_PASSES", "APS5_PROFILE_GPU")      # plus KYTY_EOP_TIMESTAMPS=gpu


class Refuse(Exception):
    def __init__(self, msg, code=78):
        super().__init__(msg); self.code = code


def _game():
    spec = importlib.util.spec_from_file_location("nps5_game", os.path.join(REPO, "games", "game.py"))
    g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
    return g


def _watch():
    spec = importlib.util.spec_from_file_location("nps5watch", os.path.join(HERE, "watch.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _logsummary():
    spec = importlib.util.spec_from_file_location("nps5_logsummary", os.path.join(HERE, "logsummary.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _patch():
    spec = importlib.util.spec_from_file_location("nps5_patch", os.path.join(HERE, "patch.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


ANYPS5_MISSING = "<AnyPS5 host libraries missing: set NPS5_ANYPS5_BUILD or LIBS (BUILDING.md, step 5)>"


# ---------------------------------------------------------------- machine layout (scripts/nps5-env.sh)
def machine_env(environ=None):
    """The NPS5_* layout and limits. Resolved by sourcing scripts/nps5-env.sh unless the caller already did."""
    environ = dict(os.environ if environ is None else environ)
    need = ("NPS5_SCRATCH", "NPS5_SELF_MEM_LIMIT_MB", "NPS5_PUBLIC_DIR", "NPS5_KK_ICD")   # NPS5_ANYPS5_BUILD is set by nps5-env.sh only when a build exists
    if all(environ.get(k) for k in need):
        return environ
    sh = os.path.join(REPO, "scripts", "nps5-env.sh")
    out = subprocess.run(["bash", "-c", '. "$1" && env -0', "_", sh], capture_output=True, env=environ).stdout.decode(errors="replace")
    for kv in out.split("\0"):
        k, eq, v = kv.partition("=")
        if eq and k.startswith("NPS5_") and not environ.get(k):
            environ[k] = v
    miss = [k for k in need if not environ.get(k)]
    if miss:
        raise Refuse("cannot resolve the machine layout: %s gave no %s" % (sh, ", ".join(miss)), 2)
    return environ


def parse_titles_env(path, environ):
    """titles/<id>.env without executing it: KEY=VALUE lines (quotes allowed, $VAR / ${VAR} expanded from environ)."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    def expand(s):
        return re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: environ.get(m.group(1) or m.group(2), ""), s)
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                toks = shlex.split(line, comments=True)
            except ValueError as e:
                raise Refuse("%s:%d: %s" % (path, n, e), 2)
            for t in toks:
                k, eq, v = t.partition("=")
                if not eq or k not in TITLES_KEYS:
                    raise Refuse("%s:%d: only %s are read here (got %r)" % (path, n, " ".join(TITLES_KEYS), k), 2)
                out[k] = expand(v)
    return out


# ---------------------------------------------------------------- arm syntax
def parse_arm(spec):
    """'label:K=V,K=V' -> dict(label, env=[(K,V)], pieces={bin:..}). Reserved keys select pieces, the rest is env."""
    if spec is None:
        return dict(label=None, env=[], pieces={})
    m = re.match(r"^([A-Za-z0-9_.+-]+):(.*)$", spec)
    label, body = (m.group(1), m.group(2)) if m else (None, spec)
    arm = dict(label=label, env=[], pieces={})
    if label is None and "=" not in body:
        raise Refuse("--arm %r: expected LABEL:KEY=VAL,KEY=VAL" % spec, 2)
    for item in [x for x in body.split(",") if x != ""]:
        k, eq, v = item.partition("=")
        if not eq or not k:
            raise Refuse("--arm %r: %r is not KEY=VALUE" % (spec, item), 2)
        if k in RESERVED:
            k = "libs" if k == "lib" else k
            if k in arm["pieces"]:
                raise Refuse("--arm %r: %s given twice" % (spec, k), 2)
            arm["pieces"][k] = v
        elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            arm["env"].append((k, v))
        else:
            raise Refuse("--arm %r: %r is not an environment name" % (spec, k), 2)
    return arm


# ---------------------------------------------------------------- composition
class Plan:
    """Everything decided before a run. Plain data so tests can inspect it."""
    def __init__(self):
        self.env = {}; self.layers = {}; self.order = []; self.warnings = []
    def put(self, k, v, layer):
        if k not in self.env:
            self.order.append(k)
        self.env[k] = v; self.layers[k] = layer
    def words(self):
        return ["%s=%s" % (k, self.env[k]) for k in self.order]


def _int(s, what):
    try:
        return int(s)
    except (TypeError, ValueError):
        raise Refuse("%s must be a number, got %r" % (what, s), 2)


def is_prepare_run(plan):
    """A `nps5 cache prepare` pass: class func, tag cache-prepare and KYTY_PIPELINE_LIST_PREPARE=1, which builds the list and
    exits before the game runs. Only such a run may ask for the CACHE_RUN_CAP_S ceiling (v1.1 let any func run tagged cache-*,
    e.g. `nps5 cache record --secs 1800` or a hand-made --tag cache-x, play for 30 minutes)."""
    return (plan.env.get("NPS5_RUN_CLASS") == "func" and plan.env.get("NPS5_RUN_TAG") == "cache-prepare"
            and plan.env.get("KYTY_PIPELINE_LIST_PREPARE") == "1")


def check_final(g, p, plan, secs, environ, gpu):
    """The safety rules on the FINAL environment, whatever layer a word came from. Raises Refuse(78)."""
    forb = [c[0] for c in p.get("env_forbidden") if c]
    for k in plan.order:
        v = plan.env[k]
        for f in forb:
            if g.forbidden_match("%s=%s" % (k, v), f):
                raise Refuse("%s=%s (%s) is refused by env_forbidden %s of %s" % (k, v, plan.layers[k], f, p.id))
    ban_on = (p.one("gpu_profile_ban", "on") or "on") != "off"
    prof = [k for k in PROFILE_BAN if plan.env.get(k, "0") not in ("", "0")]
    if plan.env.get("KYTY_EOP_TIMESTAMPS") == "gpu":
        prof.append("KYTY_EOP_TIMESTAMPS=gpu")
    if prof:
        if ban_on:
            raise Refuse("GPU timestamp / counter-heap profiling (%s) is refused by gpu_profile_ban (kernel panic risk); "
                         "use a Metal System Trace" % ", ".join(prof))
        if environ.get("NPS5_ALLOW_GPU_PROFILE") != "1" or secs > GPU_PROFILE_MAX_S:
            raise Refuse("GPU profiling (%s) needs NPS5_ALLOW_GPU_PROFILE=1 and a run of at most %d s" % (", ".join(prof), GPU_PROFILE_MAX_S))
    machine_max = min(_int(environ.get("NPS5_SELF_MEM_LIMIT_MB", g.MEM_LIMIT_MAX), "NPS5_SELF_MEM_LIMIT_MB"), g.MEM_LIMIT_MAX)
    lim = plan.env.get("NPS5_SELF_MEM_LIMIT_MB")
    if lim is not None:
        e = g.mem_limit_error("NPS5_SELF_MEM_LIMIT_MB", lim)
        if e:
            raise Refuse("%s (%s)" % (e, plan.layers["NPS5_SELF_MEM_LIMIT_MB"]))
        if int(lim) > machine_max:
            raise Refuse("NPS5_SELF_MEM_LIMIT_MB=%s is above %d, the most this Mac allows (%s)" % (lim, machine_max, plan.layers["NPS5_SELF_MEM_LIMIT_MB"]))
    if "NPS5_TITLE_RUN" in plan.env and plan.env["NPS5_TITLE_RUN"] != "1":
        raise Refuse("NPS5_TITLE_RUN is set by the runner")
    if getattr(plan, "play", False):
        if secs > PLAY_CAP_S:
            raise Refuse("%d s is over the %d s cap of --play" % (secs, PLAY_CAP_S))
    elif secs > RUN_CAP_S and not (secs <= CACHE_RUN_CAP_S and is_prepare_run(plan)):
        raise Refuse("%d s is over the %d s cap (a person playing: --play)" % (secs, RUN_CAP_S))
    elif secs > RUN_NO_REASON_S and not environ.get("NPS5_RUN_REASON"):
        raise Refuse("runs over %d s need NPS5_RUN_REASON=<why>" % RUN_NO_REASON_S)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def sha16(path):
    return sha256_file(path)[:16]


def select_binary(g, p, titles, arm, bin_opt, patch_opt, environ, build_dry=True, patchmod=None):
    """-> dict(path, lineage, patches, source, state, notes). state: ready | build | missing.
    Order: --bin > arm bin= > titles BIN > cache copy of <canonical_lineage>+<patches_default> made by `nps5 patch`."""
    patches = (patch_opt or arm["pieces"].get("patch") or (p.one("patches_default") or "")).replace(" ", ",")
    patches = ",".join(x for x in patches.split(",") if x)
    explicit = bin_opt or arm["pieces"].get("bin") or titles.get("BIN")
    notes = []
    if explicit:
        explicit = os.path.expanduser(explicit)
        return dict(path=explicit, lineage=None, patches=patches, source="explicit", state="ready" if os.path.isfile(explicit) else "missing", notes=notes)
    lin = (p.one("canonical_lineage") or "").strip()
    if not lin:
        raise Refuse("%s has no canonical_lineage and no BIN in titles/%s.env, nothing to run" % (p.id, p.id), 2)
    root = titles.get("BIN_DIR") or os.path.join(environ["NPS5_SCRATCH"], "build", p.id)
    src = next((c for c in (os.path.join(root, "native_" + lin), os.path.join(root, "native", lin), os.path.join(root, lin)) if os.path.isfile(c)),
               os.path.join(root, "native_" + lin))
    cache = os.path.join(environ["NPS5_SCRATCH"], "native-cache", p.id)
    out = os.path.join(cache, "%s+%s" % (lin, patches.replace(",", "+"))) if patches else src
    if not patches:
        return dict(path=src, lineage=lin, patches="", source="lineage", state="ready" if os.path.isfile(src) else "missing", notes=notes, src=src)
    return dict(path=out, lineage=lin, patches=patches, source="cache", src=src, notes=notes,
                state="ready" if os.path.isfile(out) else ("build" if os.path.isfile(src) else "missing"))


def verify_binary(g, p, sel, environ, patchmod=None):
    """Checks the binary against the profile: embedded build ID has a build row, requested patches are applied
    (sidecar first, else by reading the sites). -> dict(build_id, build, sidecar, notes). Raises Refuse(78)."""
    path = sel["path"]; notes = []; info = dict(build_id=None, build=None, sidecar=None, patches_applied=None, notes=notes)
    pm = patchmod or _patch()
    try:
        mo = pm.MachO(path)
    except Exception as e:
        raise Refuse("%s is not a readable Mach-O binary: %s" % (path, e), 2)
    try:
        sha = mo.build_id()
    finally:
        mo.close()
    rows = p.builds()
    if sha:
        info["build_id"] = sha[:16]
        hit = [v for v, eb in rows if eb and sha.startswith(eb)]
        if not hit:
            raise Refuse("binary build ID %s has no build row in %s/game.tsv (%s)" % (sha[:16], p.id, ", ".join(eb for _, eb in rows)))
        info["build"] = hit[0]
    else:
        notes.append("binary carries no embedded build ID: cannot identify the build")
    side = path + ".patches.json"
    if os.path.isfile(side):
        try:
            with open(side) as f:
                sc = json.load(f)
            info["sidecar"] = dict(path=side, sha16=sha16(side), patches=[x["name"] for x in sc.get("patches", [])], source_build_id=(sc.get("build_id") or "")[:16])
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise Refuse("sidecar %s is not a readable patch record (%s); delete the copy and its sidecar to rebuild them" % (side, e or type(e).__name__))
        # the record must be about THESE bytes: a relinked or replaced binary beside an old sidecar would otherwise be
        # reported as patched (nps5 patch writes output_sha256; older sidecars lack it and are checked by build ID only)
        if sc.get("output_sha256") and sc["output_sha256"] != sha256_file(path):
            raise Refuse("sidecar %s describes other bytes than %s (the binary changed after it was patched); delete both to rebuild" % (side, path))
        want = [x.split("=")[0] for x in sel["patches"].split(",") if x]
        have = info["sidecar"]["patches"]
        if sha and sc.get("build_id") and not sc["build_id"].startswith(sha[:16]):
            raise Refuse("sidecar %s records build ID %s, the binary says %s" % (side, sc["build_id"][:16], sha[:16]))
        miss = [w for w in want if w not in have]
        if miss and sel["source"] == "cache":
            raise Refuse("%s: sidecar lacks patches %s (has %s); delete the copy to rebuild it" % (path, ",".join(miss), ",".join(have)))
        if miss:
            notes.append("patches %s requested by the profile are not in the sidecar" % ",".join(miss))
        info["patches_applied"] = have
    elif sel["patches"]:
        if sel["source"] == "cache":
            raise Refuse("%s has no .patches.json sidecar: a cached copy is only trusted with its record; delete it to rebuild" % path)
        notes.append("no sidecar next to the binary; patches checked by reading the sites")
        try:
            import io
            pd = pm.profile_patches(p.id)
            want = pm.parse_request(sel["patches"])
            mo2, ver, _ = pm.open_binary(pd, path)
            try:
                res = pm.resolve(pd, mo2, ver, want)
                rep = pm.judge(mo2, res, pd, ver)
            finally:
                mo2.close()
            bad = [r for r in rep if r["state"] == "MISMATCH"]
            if bad:
                raise Refuse("%s: %s" % (path, "; ".join("%s: %s" % (r["name"], r["detail"]) for r in bad)))
            info["patches_applied"] = [r["name"] for r in rep if r["state"] in ("applied", "setting")]
            for r in rep:
                if r["state"] == "setting":
                    notes.append("patch %s: %s" % (r["name"], r["detail"]))
            notapplied = [r["name"] for r in rep if r["state"] not in ("applied", "setting")]
            if notapplied:
                notes.append("patches not applied in this binary: %s" % ",".join(notapplied))
        except pm.PatchError as e:
            notes.append("patch check skipped: %s" % e)
    return info


def build_patched(sel, p, environ, runner=None):
    """Make the cached patched copy with `nps5 patch` (scripts/nps5lib/patch.py main). Source is only read."""
    os.makedirs(os.path.dirname(sel["path"]), exist_ok=True)
    pm = _patch()
    rc = pm.main([p.id, sel["src"], sel["patches"], "-o", sel["path"]], environ)
    if rc != 0:
        raise Refuse("nps5 patch failed (rc %d); nothing was run" % rc, 78)


def compose(tid, opts, environ=None, g=None):
    """Resolve a request into a Plan. No side effects beyond reading files."""
    environ = machine_env(environ)
    g = g or _game()
    try:
        p = g.Profile(*g.find_profile(tid, environ))
    except g.Err as e:
        raise Refuse(str(e), 2)
    titles_path = os.path.join(environ.get("NPS5_PUBLIC_DIR", REPO), "titles", tid + ".env")
    titles = parse_titles_env(titles_path, environ)
    arm = parse_arm(opts.get("arm"))
    plan = Plan(); plan.p = p; plan.titles = titles; plan.arm = arm; plan.titles_path = titles_path
    cls = opts.get("class") or "func"
    if cls not in CLASSES:
        raise Refuse("--class is func or perf", 2)
    gpu = opts.get("gpu") or arm["pieces"].get("gpu") or titles.get("GPU") or p.one("gpu")
    if gpu not in ("kyty", "anyps5"):
        raise Refuse("GPU must be kyty or anyps5", 2)
    scene = opts.get("scene")
    row = None
    if scene:
        row = g.scene_lookup(p.scenes(), scene)
        if row is None:
            raise Refuse("scene %s is not defined in %s/scenes.tsv" % (scene, p.id), 2)
        row = g.scene_dict(row)
    tag = opts.get("tag") or environ.get("NPS5_RUN_TAG") or time.strftime("%Y%m%d-%H%M%S")
    for what, v in (("--tag", tag), ("--save", opts.get("save") or arm["pieces"].get("save")), ("scene save", (row or {}).get("save") or None)):
        if v is not None and v != "-" and not re.fullmatch(NAME_RE, v):
            raise Refuse("%s %r must be a plain name (letters, digits, _ . + : -), not a path" % (what, v), 2)
    play = bool(opts.get("play"))
    plan.play = play
    if play and opts.get("until"):
        raise Refuse("--play has no --until target (it is a session, not a test)", 2)
    if play and cls != "func":
        raise Refuse("--play is not a measurement: --class perf is refused", 2)
    secs = opts.get("secs")
    if secs is None and play:
        secs = str(PLAY_CAP_S)
    if secs is None:
        m = re.match(r"\d+", (row or {}).get("secs", "") or "")          # scenes.tsv: <s> or <s>/<max>
        if not m:
            m = re.search(r"default=(\d+)", p.one("run_secs", "") or "")
        secs = m.group(m.lastindex or 0) if m else "90"
    secs = _int(secs, "--secs")
    if secs < 1:
        raise Refuse("--secs must be at least 1 (0 would start the title without a time limit)", 2)
    until = opts.get("until") or ((row or {}).get("until") if (row or {}).get("until", "-") != "-" else None)
    if play:
        until = None                                 # a scene's own target does not end a session

    if until:
        w = _watch()
        try: w.parse_until(until, w.profile_markers(p))
        except w.UntilError as e: raise Refuse("--until: %s" % e, 2)

    # 1. safety base
    scratch = environ["NPS5_SCRATCH"]
    wd = opts.get("workdir") or arm["pieces"].get("workdir")
    if wd and not os.path.isabs(wd):
        if not re.fullmatch(NAME_RE, wd):
            raise Refuse("--workdir %r must be a plain name or an absolute path" % wd, 2)
        wd = os.path.join(scratch, "work", tid, wd)
    wd = wd or titles.get("WORKDIR") or os.path.join(scratch, "work", tid)
    run_dir = os.path.join(scratch, "runs", tid, tag)
    for w in BASE_WORDS:
        k, _, v = w.partition("=")
        if play and k == "NPS5_AUDIO_NULL":
            continue                                 # a session has sound
        plan.put(k, v, "base")
    plan.put("HOME", os.path.join(wd, "home"), "base"); plan.put("TMPDIR", os.path.join(wd, "home"), "base")
    plan.put("PS4RECOMP_DATA_DIR", os.path.join(wd, "home", "data"), "base")
    plan.put("PS4RECOMP_CACHE_DIR", os.path.join(wd, "home", "cache"), "base")
    plan.put("NPS5_RUNS_DIR", run_dir, "base")
    plan.put("NPS5_APP0_DIR", titles.get("APP0") or "<APP0 missing: set it in titles/%s.env>" % tid, "titles")
    plan.put("NPS5_TITLE_ID", tid, "base"); plan.put("NPS5_RUN_TAG", tag, "base")
    plan.put("NPS5_SELF_MEM_LIMIT_MB", environ["NPS5_SELF_MEM_LIMIT_MB"], "machine")
    plan.put("NPS5_TITLE_RUN", "1", "runner"); plan.put("NPS5_RUN_CLASS", cls, "runner")
    kk = arm["pieces"].get("kk") or titles.get("KK")
    icd = os.path.join(os.path.expanduser(kk), "install-arm64/share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json") if kk else environ["NPS5_KK_ICD"]
    plan.put("VK_DRIVER_FILES", icd, "base")
    plan.put("SDL_VULKAN_LIBRARY", environ.get("NPS5_VULKAN_LIB") or os.path.join(environ.get("NPS5_BREW_PREFIX", "/opt/homebrew"), "lib/libvulkan.1.dylib"), "base")
    libs = arm["pieces"].get("libs") or titles.get("LIBS")
    # The PS5 system libraries come from AnyPS5's arm64 host libraries, also with GPU=kyty (Kyty replaces the graphics
    # exports only). Without a build (scripts/build.sh anyps5) the plan carries a placeholder and the run stops below.
    libs = os.path.expanduser(libs) if libs else (environ.get("NPS5_ANYPS5_BUILD") or ANYPS5_MISSING)
    plan.put("DYLD_LIBRARY_PATH", os.path.join(libs, "core/libs/libs/unpatched"), "back end")
    if gpu == "kyty":
        kl = arm["pieces"].get("kyty") or titles.get("KYTY_LIB")
        if kl:
            kl = os.path.expanduser(kl)
            if not kl.endswith(".dylib"):
                kl = os.path.join(kl, "kyty_gpu.dylib") if os.path.isfile(os.path.join(kl, "kyty_gpu.dylib")) else os.path.join(kl, "build-arm64", "kyty_gpu.dylib")
        else:
            kl = environ.get("NPS5_KYTY_GPU_LIB") or os.path.join(environ["NPS5_KYTY_DIR"], "build-arm64", "kyty_gpu.dylib")
        plan.put("NPS5_GPU", "kyty", "back end"); plan.put("NPS5_KYTY_GPU_LIB", kl, "back end")
        plan.put("KYTY_FLIGHTREC_DIR", os.path.join(run_dir, "flightrec"), "base")   # always-on flight recorder: dumps beside the run log, not in the working directory
        if until:
            # The Kyty back end prints no [pstat] line: the watcher sees its presents and pictures through frame dumps in
            # <run>/frames (one small BMP every 15th present, at most 150). Any later layer may override these words.
            plan.put("KYTY_DUMP_FRAMES", "150", "watch"); plan.put("KYTY_DUMP_FRAMES_EVERY", "15", "watch")
            plan.put("KYTY_DUMP_DIR", os.path.join(run_dir, "frames"), "watch")
    # 2. profile (preset, rows, schema keys, forbidden words)
    try:
        for k, v, layer in g.compose_env(p, gpu, scene, None, cls, g.resolve_hw(None, environ)):   # hardware rows: this Mac or NPS5_HW
            plan.put(k, v, layer)
    except g.Err as e:
        raise Refuse(str(e), getattr(e, "code", 78))
    # 3. titles/<id>.env ENV
    for w in shlex.split(titles.get("ENV", "")):
        k, eq, v = w.partition("=")
        if not eq:
            raise Refuse("%s: ENV word %r is not KEY=VALUE" % (titles_path, w), 2)
        plan.put(k, v, "titles")
    # 4. scene pad
    pad = (row or {}).get("pad", "-")
    if row and pad not in ("-", ""):
        padv = ("@" + os.path.join(p.dir, pad)) if re.fullmatch(r"[\w./-]+\.vpad", pad) else pad
        plan.put("NPS5_VPAD_SCRIPT", padv, "scene"); plan.put("NPS5_VPAD_TYPE", "ps5", "scene")
    save = opts.get("save") or arm["pieces"].get("save") or ((row or {}).get("save") if row and (row or {}).get("save", "-") not in ("-", "") else None)
    ud = titles.get("USERDATA") if not (opts.get("save") or arm["pieces"].get("save")) else None
    if ud or save:
        plan.put("NPS5_USERDATA_DIR", ud or os.path.join(scratch, "userdata", tid, save), "scene")
    cache = arm["pieces"].get("cache")
    if cache:
        plan.put("NPS5_CACHE_DIR", cache if os.path.isabs(cache) else os.path.join(scratch, "cache", tid, cache), "arm")
    # 4b. the shader cache of this stack (nps5 cache): a normal run on the Kyty back end uses the cache `nps5 cache prepare` fills and
    #     starts with the launch phase behind the loading card. Not prepared yet = the same phase compiles the list on this start
    #     (minutes, once; the card counts), so a first launch never sits on a blank window. An explicit cache arm (cache=, or --env
    #     KYTY_PIPELINE_JOURNAL_PATH=..., as `nps5 cache record|prepare|check` pass) or NPS5_RUN_SHADER_CACHE=0 turns this off.
    explicit = cache or any(kv.startswith("KYTY_PIPELINE_JOURNAL_PATH=") for kv in opts.get("env", [])) or any(k == "KYTY_PIPELINE_JOURNAL_PATH" for k, _ in arm["env"])
    if not explicit and environ.get("NPS5_RUN_SHADER_CACHE", "1") != "0" and "kind=kyplst" in (p.one("cache", "") or "") \
            and (titles.get("GPU") or p.one("gpu", "kyty")) == "kyty" and titles.get("APP0"):
        for k, v, note in shader_cache_words(tid, environ, titles["APP0"]):
            if k:
                plan.put(k, v, "cache")
            elif note:
                plan.warnings.append(note)
    # 5. arm, then --env
    for k, v in arm["env"]:
        plan.put(k, v, "arm" + (":" + arm["label"] if arm["label"] else ""))
    for kv in opts.get("env", []):
        k, eq, v = kv.partition("=")
        if not eq or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            raise Refuse("--env %r: KEY=VALUE expected" % kv, 2)
        plan.put(k, v, "--env")
    if "NPS5_TITLE_RUN" in plan.env:
        plan.env["NPS5_TITLE_RUN"] = "1"
    # checks and binary
    check_final(g, p, plan, secs, environ, gpu)
    plan.tid, plan.tag, plan.cls, plan.gpu, plan.scene, plan.secs, plan.until = tid, tag, cls, gpu, scene, secs, until
    plan.workdir, plan.run_dir = wd, run_dir
    plan.args = shlex.split((row or {}).get("args", "") if (row or {}).get("args", "-") != "-" else "")
    plan.sel = select_binary(g, p, titles, arm, opts.get("bin"), opts.get("patch"), environ)
    plan.environ = environ
    return plan


def shader_cache_words(tid, environ, app0):
    """-> [(key, value, None)] + [(None, None, warning)]: the environment that points a normal run at this stack's shader cache
    (nps5lib/cache.py: cache dir, Metal cache id, the pipeline list, the launch phase). Empty when the title has no list."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import cache as cachemod
        cx = cachemod.context(tid, environ)
        if not os.path.isfile(cx.list_path):
            return []
        words = cachemod.cache_env(tid, cx.dir, cx.metal, with_libcache=os.path.isdir(os.path.join(cx.dir, "kk-libcache", "lib")))
        out = [(k, v, None) for k, v in words + cachemod.launch_words(cx, app0)]
        mwarn = cachemod.metalmod.launch_words(cx.dir, os.path.join(cx.p.dir, cachemod.metalmod.LIST_NAME), environ)[1]
        if mwarn:
            out.append((None, None, mwarn))
        if cachemod.status(cx)[0] != "prepared":
            out.append((None, None, "the shader cache of this stack is not prepared: this start builds the whole list behind the loading card "
                                    "(\"PREPARING SHADERS X / T\", several minutes, once). `nps5 cache prepare %s` does it ahead, with prebuilt Metal libraries" % tid))
        return out
    except Exception as e:                           # the cache is an optimisation: a run never fails on it
        return [(None, None, "shader cache not used: %s" % (str(e) or type(e).__name__))]


def command_line(plan, timeout_bin="gtimeout"):
    """The exact argv; the environment is passed as a dict (env -i style), shown here as `env -i` words for --dry-run."""
    return [timeout_bin, str(plan.secs), plan.sel["path"]] + plan.args


# ---------------------------------------------------------------- preflight
def power_source():
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    return "AC" if "AC Power" in out else ("battery" if "Battery Power" in out else None)


def gpu_others(sample_s=1.0):
    """ms of GPU time per second used by processes that are not a title run (the gputop.py reading); None if unreadable."""
    def snap():
        out = subprocess.run(["ioreg", "-r", "-c", "AGXDeviceUserClient", "-w", "0"], capture_output=True, text=True, errors="replace", timeout=10).stdout
        res = {}
        for blk in re.split(r"\n(?=[ |]*\+-o )", out):
            if "accumulatedGPUTime" not in blk:
                continue
            m = re.search(r'"IOUserClientCreator" = "pid (\d+), ([^"]*)"', blk)
            if m:
                res[(int(m.group(1)), m.group(2))] = res.get((int(m.group(1)), m.group(2)), 0) + sum(int(x) for x in re.findall(r'"accumulatedGPUTime"=(\d+)', blk))
        return res
    try:
        a = snap(); t0 = time.time(); time.sleep(sample_s); b = snap(); dt = time.time() - t0
    except Exception:
        return None
    return sum((b[k] - a.get(k, 0)) / 1e6 / dt for k in b if not k[1].startswith("native_"))


GPU_EXE = re.compile(r"^(native(_.*)?|agc_frame_replay.*|kk_spv.*|spvpipe.*|.*harness.*)$")      # executables that use the GPU
GPU_SCRIPT = re.compile(r"(harness|spvpipe|kk_spv)[^/]*\.(py|sh)$")                                 # the same, started through an interpreter
INTERPRETERS = re.compile(r"^(python[\d.]*|Python|bash|sh|zsh)$")


def _ancestors(pid=None, limit=12):
    """This process and its parents."""
    out, pid = set(), pid or os.getpid()
    while pid > 1 and len(out) < limit:
        out.add(pid)
        try: pid = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip() or 0)
        except (OSError, ValueError): break
    return out


def gpu_users(comm_lines, args_lines, mine=()):
    """-> ["<pid> <name>"] of the processes that use the GPU now, from `ps -axo pid=,comm=` and `ps -axo pid=,args=` lines.
    Decided by the EXECUTABLE: a title binary (native, native_<lineage>) or a known GPU tool; for an interpreter, by the
    script it runs. A command line that merely names a title binary is not a GPU user: a runner or wrapper script waiting
    in the title queue (run_sandboxed.sh ... /path/native_x), a gtimeout in front of a title, an editor, a grep."""
    args = {}
    for l in args_lines:
        pid, _, rest = l.strip().partition(" ")
        if pid.isdigit(): args[int(pid)] = rest.strip()
    res = []
    for l in comm_lines:
        pid, _, comm = l.strip().partition(" ")
        if not pid.isdigit() or int(pid) in mine:
            continue
        name = os.path.basename(comm.strip())
        if GPU_EXE.match(name):
            res.append("%s %s" % (pid, name))
        elif INTERPRETERS.match(name):
            script = next((t for t in args.get(int(pid), "").split()[1:] if not t.startswith("-")), "")
            if GPU_SCRIPT.search(script):
                res.append("%s %s" % (pid, os.path.basename(script)))
    return res


def gpu_processes():
    def ps(col):
        return subprocess.run(["ps", "-axo", "pid=,%s=" % col], capture_output=True, text=True, errors="replace").stdout.splitlines()
    try:
        return gpu_users(ps("comm"), ps("args"), _ancestors())
    except Exception:
        return []


def preflight(plan, power=power_source, others=gpu_others, procs=gpu_processes):
    """-> (warnings, busy_reasons). Warnings never stop a run; a busy reason (a second GPU process) does."""
    warn, busy = [], []
    ps = power()
    if ps == "battery":
        warn.append("on battery power: frame times and the start hold are not representative (AC power needed for perf runs)")
    elif ps is None:
        warn.append("power source unknown")
    o = others()
    if o is not None and o > 30:
        warn.append("other processes use the GPU: %.0f ms/s (WindowServer and other apps); perf numbers will be noisy" % o)
    pr = procs()
    if pr:
        busy.append("another GPU-using process is running (pid name): " + ", ".join(x.strip()[:60] for x in pr[:8]) + (" and %d more" % (len(pr) - 8) if len(pr) > 8 else ""))
    if plan.cls == "perf" and (ps != "AC" or (o or 0) > 30):
        warn.append("this run is class perf: results should be marked NOISY")
    return warn, busy


# ---------------------------------------------------------------- queue (port of run_sandboxed.sh nps5_title_lock)
class Queue:
    """Title ticket queue and lock under <guard>: title.queue/<epoch12>-<prio>-<pid> (content = class), title.lock/pid.
    FIFO; a func run skips perf tickets ahead of it while the start hold is fresh; the start hold applies to perf
    always and to func only under heavy load. A dead pid's ticket or lock is removed."""
    def __init__(self, guard, cls, prio="nps5", pid=None, sleep=time.sleep, now=time.time, load=None, pressure=None, swap_mb=None, polls=1440):
        self.g, self.cls, self.prio, self.pid = guard, cls, prio, pid or os.getpid()
        self.sleep, self.now, self.polls = sleep, now, polls
        self.load, self.pressure, self.swap_mb = load or self._load, pressure or self._pressure, swap_mb or self._swap
        self.q, self.lock = os.path.join(guard, "title.queue"), os.path.join(guard, "title.lock")
        self.ticket = None

    @staticmethod
    def _sysctl(name):
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True).stdout.strip()
    def _load(self):
        try: return int(float(self._sysctl("vm.loadavg").strip("{} ").split()[0]))
        except Exception: return 0
    def _pressure(self):
        try: return int(self._sysctl("kern.memorystatus_vm_pressure_level"))
        except Exception: return 1
    def _swap(self):
        m = re.search(r"used = ([\d.]+)M", self._sysctl("vm.swapusage"))
        return int(float(m.group(1))) if m else 0

    @staticmethod
    def _alive(pid):
        try:
            os.kill(int(pid), 0); return True
        except (OSError, ValueError):
            return False

    def take_ticket(self):
        os.makedirs(self.q, exist_ok=True)
        self.ticket = os.path.join(self.q, "%012d-%s-%d" % (int(self.now()), self.prio, self.pid))
        with open(self.ticket, "w") as f:
            f.write(self.cls + "\n")
        return self.ticket

    def _hold_fresh(self):
        h = os.path.join(self.g, "start.hold")
        return os.path.isfile(h) and self.now() - os.stat(h).st_mtime < 30

    def _first(self):
        for name in sorted(os.listdir(self.q)):
            pid = name.rsplit("-", 1)[-1]
            if not self._alive(pid):
                try: os.remove(os.path.join(self.q, name))
                except OSError: pass
                continue
            if self.cls != "perf":
                try: c = open(os.path.join(self.q, name)).read().strip()
                except OSError: continue
                if c == "perf" and self._hold_fresh():
                    continue
            return name
        return None

    def _held(self):
        if not self._hold_fresh() or os.environ.get("NPS5_IGNORE_START_HOLD") == "1":
            return False
        return self.cls == "perf" or self.load() > int(os.environ.get("NPS5_FUNC_HOLD_LOAD", 24)) or self.pressure() >= 2 or self.swap_mb() > int(os.environ.get("NPS5_GUARD_HOLD_SWAP_MB", 12288))

    def acquire(self, log=print):
        """Blocks until this run may start; True when the lock is held (pid file written). False after the wait."""
        self.take_ticket()
        for i in range(1, self.polls + 1):
            first = self._first()
            if first != os.path.basename(self.ticket):
                if i == 1: log("[nps5 run] title queue: %d waiting; queued" % len(os.listdir(self.q)))
                self.sleep(5); continue
            if self._held():
                if i == 1 or i % 12 == 0: log("[nps5 run] start hold: machine too loaded for a title run; waiting")
                self.sleep(5); continue
            try:
                os.mkdir(self.lock)
            except FileExistsError:
                try: pid = open(os.path.join(self.lock, "pid")).read().strip()
                except OSError: pid = ""
                if (pid and not self._alive(pid)) or (not pid and self.now() - os.stat(self.lock).st_mtime > 60):
                    shutil.rmtree(self.lock, ignore_errors=True); continue
                self.sleep(5); continue
            with open(os.path.join(self.lock, "pid"), "w") as f:
                f.write("%d\n" % self.pid)
            try: os.remove(self.ticket)
            except OSError: pass
            if self.cls == "perf":
                for n in ("perf.quiet", "title.active"):
                    open(os.path.join(self.g, n), "w").write("%d\n" % self.pid)
            else:
                open(os.path.join(self.g, "title.active"), "w").write("%d\n" % self.pid)
            return True
        try: os.remove(self.ticket)
        except OSError: pass
        return False

    def release(self):
        shutil.rmtree(self.lock, ignore_errors=True)
        for n in ("perf.quiet", "title.active"):
            try: os.remove(os.path.join(self.g, n))
            except OSError: pass
        if self.ticket:
            try: os.remove(self.ticket)
            except OSError: pass


# ---------------------------------------------------------------- run.json
def _git_out(d, *a, binary=False):
    try:
        r = subprocess.run(("git", "-C", d) + a, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout if binary else r.stdout.decode(errors="replace").strip()


def commit_of(d):
    """Identity of the source in checkout `d` (its top folder, not a folder inside some other repository): the commit, 10 hex,
    when the tracked files are as committed; <commit>+<8 hex of the difference> when they are not, so a build from changed
    sources is never reported under, or cached under, the commit it only started from. None when `d` is no checkout."""
    if not d or not os.path.isdir(d):
        return None
    top = _git_out(d, "rev-parse", "--show-toplevel")
    if not top or os.path.realpath(top) != os.path.realpath(d):
        return None
    head = _git_out(d, "rev-parse", "--short=10", "HEAD")
    if not head:
        return None
    diff = _git_out(d, "diff", "HEAD", "--no-ext-diff", binary=True)
    if diff is None:
        return head + "+unknown"
    return head + ("+" + hashlib.sha256(diff).hexdigest()[:8] if diff else "")


def kk_commit(*dirs):
    """KosmicKrisp's identity from the first folder that is a checkout: the folder itself (a Mesa branch with the changes
    committed) or its mesa/ (the public layout: kosmickrisp/build-kosmickrisp.sh applies the patch series on the pinned
    commit without committing, so the identity reads <pin>+<hash of the applied changes>)."""
    for d in dirs:
        for c in (d, os.path.join(d, "mesa") if d else None):
            v = commit_of(c)
            if v:
                return v
    return None


def machine_class():
    def sc(n):
        return subprocess.run(["sysctl", "-n", n], capture_output=True, text=True).stdout.strip()
    try: mem = int(sc("hw.memsize")) // (1 << 30)
    except ValueError: mem = None
    d = dict(model=sc("hw.model") or None, chip=sc("machdep.cpu.brand_string") or None, mem_gb=mem)
    d["class"], d["cache_key"] = os.environ.get("NPS5_HW_CLASS") or None, os.environ.get("NPS5_HW_CACHE_KEY") or None
    return d


def keep_cache_per_hardware(cache_dir, key, log=None):
    """Compiled caches are valid for one GPU family and OS build (NPS5_HW_CACHE_KEY, docs/reference/hardware.md).
    <cache_dir>/.hwkey names the key the caches were built for; with another key they are set aside as
    <cache_dir>.<old key> (never deleted) and the ones of this key, if any, come back. Returns the old key or None."""
    if not key or not re.fullmatch(r"[A-Za-z0-9-]+", key):
        return None
    stamp, old = os.path.join(cache_dir, ".hwkey"), None
    try:
        with open(stamp) as f: old = f.read().strip() or None
    except OSError:
        pass
    moved = None
    if old and old != key and re.fullmatch(r"[A-Za-z0-9-]+", old):
        aside = "%s.%s" % (cache_dir, old)
        if os.path.exists(aside): os.rename(aside, "%s.%d" % (aside, int(time.time())))
        os.rename(cache_dir, aside)
        if os.path.isdir("%s.%s" % (cache_dir, key)): os.rename("%s.%s" % (cache_dir, key), cache_dir)
        moved = old
        (log or (lambda m: print(m, file=sys.stderr)))("nps5 run: the compiled caches in %s were built for %s; this Mac is %s. Set aside as %s; first visits compile again"
                                                         % (cache_dir, old, key, os.path.basename(aside)))
    os.makedirs(cache_dir, exist_ok=True)
    with open(stamp, "w") as f: f.write(key + "\n")
    return moved


def components(plan):
    e = plan.environ
    libs = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(plan.env["DYLD_LIBRARY_PATH"])))))  # <anyps5 checkout>/build-arm64 -> checkout
    kk = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(plan.env["VK_DRIVER_FILES"])))) if "install-arm64" in plan.env["VK_DRIVER_FILES"] else None
    out = dict(public=commit_of(e.get("NPS5_PUBLIC_DIR", REPO)), ps5recomp=commit_of(e.get("NPS5_RECOMP_DIR")),
               anyps5=commit_of(libs if "build" in os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(plan.env["DYLD_LIBRARY_PATH"])))) else e.get("NPS5_ANYPS5_DIR")),
               kk=kk_commit(os.path.dirname(kk) if kk and os.path.basename(kk) == "install-arm64" else kk))
    kl = plan.env.get("NPS5_KYTY_GPU_LIB")
    out["kyty"] = commit_of(os.path.dirname(os.path.dirname(kl))) if kl else None
    return out


def new_run_json(plan, bininfo, hash16, commits):
    return dict(schema=SCHEMA, title=plan.tid, tag=plan.tag, **{"class": plan.cls}, gpu=plan.gpu, scene=plan.scene, until=plan.until,
                secs=plan.secs, arm=dict(label=plan.arm["label"], env=plan.arm["env"], pieces=plan.arm["pieces"]) if plan.arm["label"] or plan.arm["env"] or plan.arm["pieces"] else None,
                build=dict(version=bininfo.get("build"), build_id=bininfo.get("build_id")), lineage=plan.sel.get("lineage"),
                patches=plan.sel["patches"].split(",") if plan.sel["patches"] else [],
                binary=dict(path=plan.sel["path"], sha256_16=hash16, source=plan.sel["source"], sidecar=bininfo.get("sidecar"), patches_applied=bininfo.get("patches_applied")),
                components=commits, env={k: plan.env[k] for k in plan.order}, machine=machine_class(),
                command=command_line(plan) , timings=dict(start=None, end=None, wall_s=None), rc=None, verdict=None,
                logs=dict(out=os.path.join(plan.run_dir, "run.out"), err=os.path.join(plan.run_dir, "run.err")), warnings=list(plan.warnings))


def validate_run_json(d):
    """-> list of problems (empty = schema ok)."""
    bad = ["missing key %s" % k for k in RUN_JSON_KEYS if k not in d]
    if d.get("schema") != SCHEMA: bad.append("schema must be %d" % SCHEMA)
    for k, t in (("env", dict), ("command", list), ("timings", dict), ("components", dict), ("binary", dict), ("logs", dict), ("machine", dict), ("build", dict)):
        if k in d and not isinstance(d[k], t): bad.append("%s must be a %s" % (k, t.__name__))
    if d.get("class") not in CLASSES: bad.append("class must be func or perf")
    for k in ("path", "sha256_16"):
        if isinstance(d.get("binary"), dict) and k not in d["binary"]: bad.append("binary.%s missing" % k)
    if isinstance(d.get("timings"), dict):
        for k in ("start", "end", "wall_s"):
            if k not in d["timings"]: bad.append("timings.%s missing" % k)
    return bad


def config_block(plan):
    lines = ["[nps5-config] title=%s tag=%s class=%s gpu=%s scene=%s until=%s secs=%s" % (plan.tid, plan.tag, plan.cls, plan.gpu, plan.scene or "-", plan.until or "-", plan.secs),
             "[nps5-config] binary=%s patches=%s" % (plan.sel["path"], plan.sel["patches"] or "-")]
    lines += ["[nps5-config] %s=%s (%s)" % (k, plan.env[k], plan.layers[k]) for k in plan.order]
    return "\n".join(lines) + "\n"


def dry_run_text(plan, bininfo=None):
    o = ["nps5 run %s: dry run (nothing is started)" % plan.tid,
         "title: %s  tag: %s  class: %s  gpu: %s  scene: %s  until: %s  secs: %s" % (plan.tid, plan.tag, plan.cls, plan.gpu, plan.scene or "-", plan.until or "-", plan.secs),
         "titles file: %s%s" % (plan.titles_path, "" if os.path.isfile(plan.titles_path) else "  (missing: copy titles/example.env; paths below are placeholders)"),
         "binary: %s" % plan.sel["path"],
         "  from: %s%s, patches: %s, state: %s" % (plan.sel["source"], " (lineage %s, source %s)" % (plan.sel["lineage"], plan.sel.get("src")) if plan.sel.get("lineage") else "", plan.sel["patches"] or "-", {"ready": "ready", "build": "will be built by nps5 patch on the first run", "missing": "MISSING"}[plan.sel["state"]])]
    if bininfo:
        o.append("  build %s id %s, patches applied: %s, sidecar: %s" % (bininfo.get("build"), bininfo.get("build_id"), ",".join(bininfo.get("patches_applied") or []) or "-", "yes" if bininfo.get("sidecar") else "no"))
        o += ["  note: " + n for n in bininfo.get("notes", [])]
    o.append("workdir: %s" % plan.workdir)
    o.append("env:")
    o += ["  %s=%s" % (k, plan.env[k]) for k in plan.order]
    o.append("command: cd %s && env -i PATH=/usr/bin:/bin <env above> %s" % (os.path.join(plan.run_dir, "frames"), " ".join(shlex.quote(a) for a in command_line(plan))))
    o += ["warning: " + w for w in plan.warnings]
    return "\n".join(o) + "\n"


# ---------------------------------------------------------------- CLI
def parse_args(argv):
    opts = dict(env=[]); pos = []; i = 0
    VAL = {"--scene": "scene", "--until": "until", "--secs": "secs", "--class": "class", "--tag": "tag", "--gpu": "gpu", "--bin": "bin",
           "--patch": "patch", "--arm": "arm", "--workdir": "workdir", "--save": "save"}
    while i < len(argv):
        a = argv[i]
        if a in VAL or a == "--env":
            if i + 1 >= len(argv):
                raise Refuse("%s needs a value" % a, 2)
            if a == "--env": opts["env"].append(argv[i + 1])
            else: opts[VAL[a]] = argv[i + 1]
            i += 1
        elif a == "--dry-run":
            opts["dry"] = True
        elif a == "--play":
            opts["play"] = True
        elif a.startswith("--"):
            raise Refuse("unknown option %s" % a, 2)
        else:
            pos.append(a)
        i += 1
    if len(pos) != 1:
        raise Refuse("a title id is required (no default game): nps5 run <id> [options]; see --help", 2)
    return pos[0], opts


def watch_run(plan, pr, err_path, environ):
    """Follow the child with the watcher (nps5lib/watch.py): stops it cleanly on the --until target (DONE_UNTIL), a black screen
    (EARLY_BLACK) or a stall (EARLY_STALL). None when disabled (NPS5_WATCH=0) or the child has no pid to poll."""
    if environ.get("NPS5_WATCH", "1") == "0" or not hasattr(pr, "poll") or getattr(plan, "play", False):
        return None                                  # --play: a paused game or a dark scene is not a failed test
    w = _watch()
    src = w.Sources(err_path, plan.run_dir)
    def cpu():
        r = subprocess.run(["ps", "-o", "cputime=", "-p", str(pr.pid)], capture_output=True, text=True).stdout.strip()
        try: return sum(float(x) * 60 ** i for i, x in enumerate(reversed(r.split(":"))))
        except ValueError: return None
    pr.cpu_seconds = cpu
    def lsof():
        kids = subprocess.run(["pgrep", "-P", str(pr.pid)], capture_output=True, text=True).stdout.split() or [str(pr.pid)]
        return subprocess.run(["lsof", "-n", "-p", ",".join(kids)], capture_output=True, text=True).stdout
    v = w.follow(pr, w.judge_for(plan), src, lsof=lsof, log=lambda m: print("nps5 run: " + m, file=sys.stderr))
    return None if v[0] is None else v


# ---------------------------------------------------------------- peak memory of the title
class Footprint:
    """Peak phys_footprint of the started command and its children, in MiB: what the memory limit and the machine guard
    count (RSS hides compressed and GPU memory). Sampled once a second from proc_pid_rusage's lifetime maximum, so the
    last sample before the exit holds the peak up to that moment. peak() is None when nothing could be read."""
    RUSAGE_INFO_V4, WORDS, LIFETIME_MAX = 4, 35, 28          # struct rusage_info_v4: 16 uuid bytes, then 35 uint64

    def __init__(self, pid, interval=1.0, children=None, reader=None):
        self.pid, self.interval, self.max = pid, interval, None
        self.children = children or self._children
        self.reader = reader or self._reader()
        self._stop = threading.Event(); self._t = None

    @staticmethod
    def _children(pid):
        try:
            return [int(x) for x in subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()]
        except (OSError, ValueError):
            return []

    @classmethod
    def _reader(cls):
        try:
            lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            buf = (ctypes.c_uint64 * (2 + cls.WORDS))()
        except (OSError, AttributeError):
            return lambda pid: None
        def read(pid):
            try:
                if lib.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(cls.RUSAGE_INFO_V4), ctypes.byref(buf)) != 0:
                    return None
            except (OSError, AttributeError, ValueError):
                return None
            return int(buf[2 + cls.LIFETIME_MAX])
        return read

    def sample(self):
        for pid in [self.pid] + self.children(self.pid):
            v = self.reader(pid)
            if v:
                self.max = max(self.max or 0, v)

    def start(self):
        def loop():
            while not self._stop.is_set():
                self.sample(); self._stop.wait(self.interval)
        self._t = threading.Thread(target=loop, daemon=True); self._t.start()
        return self

    def peak(self):
        """Stops the sampling; MiB or None."""
        self._stop.set()
        if self._t: self._t.join(5)
        return None if not self.max else int(round(self.max / 1048576.0))


def end_child(pr, grace=10):
    """The started command must not outlive the runner (the title lock is released when the runner ends): SIGTERM, which
    gtimeout forwards to the title, then SIGKILL of the command and its children."""
    if not hasattr(pr, "poll") or pr.poll() is not None:
        return
    kids = Footprint._children(pr.pid) if isinstance(getattr(pr, "pid", None), int) else []
    try: pr.terminate()
    except OSError: pass
    try:
        pr.wait(grace)
    except Exception:
        for k in kids:
            try: os.kill(k, signal.SIGKILL)
            except OSError: pass
        try: pr.kill()
        except OSError: pass
        try: pr.wait(5)
        except Exception: pass


def set_aside(run_dir):
    """A run folder that already holds a run.json belongs to an earlier run of this tag: rename it <tag>.prev-<time> so its
    frames, logs and run.json are neither lost nor read as this run's. Returns the new name or None."""
    old = os.path.join(run_dir, "run.json")
    if not os.path.isfile(old):
        return None
    base = "%s.prev-%s" % (run_dir, time.strftime("%Y%m%d-%H%M%S", time.localtime(os.stat(old).st_mtime)))
    aside, n = base, 1
    while os.path.exists(aside):
        n += 1; aside = "%s-%d" % (base, n)
    os.rename(run_dir, aside)
    return aside


def run_interruptible(fn):
    """Runs fn with SIGTERM raised as KeyboardInterrupt, so a killed runner still stops its title, finishes run.json and
    releases the lock (the `finally` blocks of execute). Exit 130 when interrupted."""
    def on_term(signum, frame):
        raise KeyboardInterrupt()
    old = None
    try:
        old = signal.signal(signal.SIGTERM, on_term)
    except ValueError:
        pass                    # not the main thread (a test, an embedding tool): the caller owns signals
    try:
        return fn()
    except KeyboardInterrupt:
        print("nps5 run: interrupted; the title was stopped", file=sys.stderr); return 130
    finally:
        if old is not None:
            signal.signal(signal.SIGTERM, old)


def execute(plan, opts, environ, queue_factory=Queue, popen=subprocess.Popen, hasher=sha16, footprint=Footprint):
    """The real run. Returns the exit code. Ticket -> lock -> binary -> run -> manifest verify -> run.json."""
    warn, busy = preflight(plan)
    plan.warnings += warn
    for w in warn: print("nps5 run: warning: " + w, file=sys.stderr)
    if busy:
        print("nps5 run: " + busy[0], file=sys.stderr); return 75
    if plan.env.get("DYLD_LIBRARY_PATH", "").startswith(ANYPS5_MISSING):
        print("nps5 run: the AnyPS5 arm64 host libraries are missing: set NPS5_ANYPS5_BUILD or LIBS in %s. They provide the PS5 "
              "system libraries for every title run (also with GPU=kyty): scripts/fetch-components.sh anyps5, then "
              "scripts/build.sh anyps5 (BUILDING.md, step 5)" % plan.titles_path, file=sys.stderr); return 2
    if not os.path.isdir(plan.titles.get("APP0", "")):
        print("nps5 run: APP0 %r is not a folder (set it in %s)" % (plan.titles.get("APP0"), plan.titles_path), file=sys.stderr); return 2
    tmo = shutil.which("gtimeout") or shutil.which("timeout")
    if not tmo:
        print("nps5 run: needs gtimeout (brew install coreutils)", file=sys.stderr); return 2
    guard = environ.get("NPS5_GUARD_DIR") or os.path.join(environ["NPS5_SCRATCH"], "guard")
    os.makedirs(guard, exist_ok=True)
    q = queue_factory(guard, plan.cls, environ.get("NPS5_TITLE_PRIO", "nps5"))
    if not q.acquire():
        print("nps5 run: title lock not acquired in the wait time; exit 75", file=sys.stderr); return 75
    rj = save = None
    try:
        if plan.sel["state"] == "missing":
            print("nps5 run: binary %s is missing (BUILDING.md, Translate a title)" % plan.sel["path"], file=sys.stderr); return 2
        if plan.sel["state"] == "build":
            build_patched(plan.sel, plan.p, environ); plan.sel["state"] = "ready"
        info = verify_binary(None, plan.p, plan.sel, environ)
        for n in info["notes"]: print("nps5 run: note: " + n, file=sys.stderr)
        aside = set_aside(plan.run_dir)
        if aside:
            print("nps5 run: note: tag %s was used before; the earlier run is now %s" % (plan.tag, os.path.basename(aside)), file=sys.stderr)
        os.makedirs(os.path.join(plan.run_dir, "frames"), exist_ok=True); os.makedirs(os.path.join(plan.workdir, "home"), exist_ok=True)
        keep_cache_per_hardware(plan.env.get("PS4RECOMP_CACHE_DIR") or os.path.join(plan.workdir, "home", "cache"), environ.get("NPS5_HW_CACHE_KEY"))
        rj = new_run_json(plan, info, hasher(plan.sel["path"]), components(plan))
        rjp = os.path.join(plan.run_dir, "run.json")
        def save():
            with open(rjp + ".tmp", "w") as f: json.dump(rj, f, indent=1); f.write("\n")
            os.replace(rjp + ".tmp", rjp)
        t0 = time.time(); rj["timings"]["start"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0)); rj["verdict"] = "RUNNING"; save()
        env = {"PATH": "/usr/bin:/bin"}; env.update(plan.env)
        with open(rj["logs"]["err"], "w") as ef:
            ef.write(config_block(plan)); ef.flush()
            with open(rj["logs"]["out"], "w") as of:
                pr = popen(command_line(plan, tmo), env=env, cwd=os.path.join(plan.run_dir, "frames"), stdout=of, stderr=ef, stdin=subprocess.DEVNULL)
                fp = footprint(pr.pid).start() if isinstance(getattr(pr, "pid", None), int) else None
                try:
                    wv = watch_run(plan, pr, rj["logs"]["err"], environ)
                    rc = pr.wait() if wv is None else wv[2]
                finally:
                    rj["timings"]["peak_footprint_mib"] = fp.peak() if fp else None
                    end_child(pr)
        t1 = time.time()
        rj["rc"] = rc; rj["timings"].update(end=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t1)), wall_s=round(t1 - t0, 1))
        rj["verdict"] = "DONE_FIXED" if rc in (0, 124) else ("CRASH" if rc < 0 or rc > 128 else "FAIL_RC%d" % rc)
        if wv and wv[0]:
            rj["verdict"] = wv[0]; rj["timings"]["watch"] = wv[1]
            if wv[0] in ("DONE_UNTIL", "DONE_FIXED"): rc = 0
        elif plan.until and rj["verdict"] == "DONE_FIXED":
            # no watcher verdict: the time ran out (124) or the title exited by itself (0) before the target was seen
            rj["verdict"] = "TIMEOUT" if rc == 124 else "ENDED_EARLY"
        dv = plan.titles.get("DUMP_VERIFY")
        if dv and subprocess.run(shlex.split(dv)).returncode != 0:
            print("!!!!!!!! RUN MODIFIED THE DUMP !!!!!!!!", file=sys.stderr); rj["verdict"] = "DUMP_MODIFIED"; save(); return 99
        save()
        try: print(_logsummary().write_run_summary(plan.run_dir, rj), end="")     # the run-end SUMMARY block, also run.summary.txt
        except Exception: pass
        print("nps5 run: %s rc=%s verdict=%s, output in %s" % (plan.tid, rc, rj["verdict"], plan.run_dir))
        if rj["verdict"] in ("EARLY_BLACK", "EARLY_STALL", "ENDED_EARLY"): return 1
        return 0 if rc in (0, 124) and rj["verdict"] in ("DONE_FIXED", "DONE_UNTIL", "TIMEOUT") else (rc if rc > 0 else 1)
    except BaseException as e:
        if rj is not None and rj.get("verdict") == "RUNNING":       # never leave a run.json that says a dead run is going
            rj["verdict"] = "INTERRUPTED" if isinstance(e, (KeyboardInterrupt, SystemExit)) else "ERROR"
            rj["warnings"].append("runner stopped: %s" % (str(e) or type(e).__name__))
            rj["timings"]["end"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            try: save()
            except OSError: pass
        raise
    finally:
        q.release()


def main(argv, environ=None):
    if not argv or "-h" in argv or "--help" in argv:
        print(__doc__); return 0 if argv else 2
    try:
        tid, opts = parse_args(argv)
        environ = machine_env(environ)
        plan = compose(tid, opts, environ)
        if opts.get("dry"):
            info = None
            if plan.sel["state"] == "ready":
                try: info = verify_binary(None, plan.p, plan.sel, environ)
                except Refuse as e: plan.warnings.append("binary: %s" % e)
            w, busy = preflight(plan); plan.warnings += w + busy
            sys.stdout.write(dry_run_text(plan, info)); return 0
        return run_interruptible(lambda: execute(plan, opts, environ))
    except Refuse as e:
        print("nps5 run: %s" % e, file=sys.stderr); return e.code
    except OSError as e:
        print("nps5 run: %s" % e, file=sys.stderr); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
