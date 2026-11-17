#!/usr/bin/env python3
"""scripts/nps5lib/hw.py: what this Mac is, and the limits derived from it. Python 3, standard library only.

  hw.py doctor            the hardware report of `nps5 doctor`: chip, GPU family, cores, RAM, macOS, power, fan,
                          machine class, derived limits, and a support verdict (docs/reference/hardware.md)
  hw.py class             the machine class string, e.g. m4pro-24g
  hw.py cache-key         <gpu family>-<OS build>, e.g. g16-25G83: part of every compiled-cache identity
  hw.py env               `export NAME=VALUE` lines for scripts/nps5-env.sh (only names not already set)
  hw.py json              every detected field and derived default as JSON
  hw.py --hw "<spec>" ... any command for another machine (what-if), e.g. --hw "m1-16g" or
                          --hw "chip=m2 ram=16 pcores=4 ecores=4 os=26.1 osbuild=25B78 model=Mac14,2 fan=no power=battery"
The environment variable NPS5_HW holds the same spec (NPS5_HW=none: no hardware known). Detection reads sysctl
and pmset only; the doctor report also asks system_profiler for the model name (the fan hint).
Exit codes: 0 ok (doctor: supported or experimental), 1 doctor found an unsupported machine, 2 bad argument.

Everything was tuned on ONE machine: Apple M4 Pro (10 performance + 4 efficiency cores), 24 GB, macOS 26.6.
The formulas below reproduce that machine's hand-tuned values exactly (tests/test_hw.py holds them to it) and
scale them by RAM and core count for the others. Values for other machines are derived, not measured."""
import json, os, re, subprocess, sys

REFERENCE_CLASS = "m4pro-24g"
MIN_MACOS = 26                      # KosmicKrisp needs Metal 4 (MTLGPUFamilyMetal4, MTL4Compiler, MTL4Archive)
MIN_RAM_GB, EXPERIMENTAL_RAM_GB = 16, 16
# AGX generation by M-series generation. `gpufam` is this name: it is what compiled GPU code depends on (the
# Metal binary-archive architecture applegpu_g13..g17) and what tools/agxdis decodes.
GPU_BY_GEN = {1: "g13", 2: "g14", 3: "g15", 4: "g16", 5: "g17"}
AGXDIS_GPUS = ("g13", "g14", "g15", "g16")
# performance/efficiency cores of the common configuration, used only for a what-if spec that names no cores
CORES_BY_CHIP = {"m1": (4, 4), "m1pro": (8, 2), "m1max": (8, 2), "m1ultra": (16, 4),
                 "m2": (4, 4), "m2pro": (8, 4), "m2max": (8, 4), "m2ultra": (16, 8),
                 "m3": (4, 4), "m3pro": (6, 6), "m3max": (12, 4), "m3ultra": (24, 8),
                 "m4": (4, 6), "m4pro": (10, 4), "m4max": (12, 4), "m5": (4, 6)}
FIELDS = ("chip", "chip_key", "gen", "gpufam", "pcores", "ecores", "ram_mb", "ram_gb", "os", "os_major", "osbuild",
          "model", "machine_name", "power", "fan", "class")


def _run(argv, timeout=10):
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout)
        return r.stdout.decode(errors="replace") if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _sysctl(name, run=_run):
    return run(["/usr/sbin/sysctl", "-n", name]).strip()


def chip_key(brand):
    """'Apple M4 Pro' -> ('m4pro', 4); anything else -> ('unknown', 0)."""
    m = re.search(r"\bM(\d+)(?:\s+(Pro|Max|Ultra))?\b", brand or "")
    if not m or "Apple" not in brand:
        return "unknown", 0
    return "m%s%s" % (m.group(1), (m.group(2) or "").lower()), int(m.group(1))


def _finish(h):
    """Fill the fields that follow from others; unknown numbers stay 0, unknown text stays 'unknown'."""
    if not h.get("chip_key") or h["chip_key"] == "unknown":
        h["chip_key"], h["gen"] = chip_key(h.get("chip", ""))
    elif not h.get("gen"):
        m = re.match(r"m(\d+)", h["chip_key"]); h["gen"] = int(m.group(1)) if m else 0
    h.setdefault("chip", "unknown")
    if not h.get("gpufam"):
        h["gpufam"] = GPU_BY_GEN.get(h["gen"], "unknown")
    for k in ("pcores", "ecores", "ram_mb", "os_major"):
        h[k] = int(h.get(k) or 0)
    h["ram_gb"] = (h["ram_mb"] + 512) // 1024
    if not h.get("os_major") and h.get("os"):
        m = re.match(r"(\d+)", h["os"]); h["os_major"] = int(m.group(1)) if m else 0
    for k in ("os", "osbuild", "model", "machine_name", "power", "fan"):
        h[k] = h.get(k) or "unknown"
    if h["fan"] == "unknown" and h["machine_name"] != "unknown":
        h["fan"] = "no" if "MacBook Air" in h["machine_name"] else "yes"   # every Apple silicon Air is fanless
    h["class"] = "%s-%dg" % (h["chip_key"], h["ram_gb"]) if h["chip_key"] != "unknown" and h["ram_gb"] else "unknown"
    return h


def parse_spec(spec):
    """'m1-16g' or 'chip=m1 ram=16 pcores=4 ...' -> hw dict (missing cores come from CORES_BY_CHIP). Raises ValueError."""
    h = {}
    for w in spec.split():
        m = re.fullmatch(r"(m\d+(?:pro|max|ultra)?)-(\d+)g", w)
        if m:
            h["chip_key"], h["ram_mb"] = m.group(1), int(m.group(2)) * 1024; continue
        k, eq, v = w.partition("=")
        if not eq or not v:
            raise ValueError("bad hardware word %r (class like m1-16g, or chip= ram= pcores= ecores= gpufam= os= osbuild= model= fan= power=)" % w)
        if k == "chip": h["chip_key"] = v.lower().replace(" ", "")
        elif k == "ram": h["ram_mb"] = int(v) * 1024
        elif k in ("pcores", "ecores"): h[k] = int(v)
        elif k in ("gpufam", "os", "osbuild", "model", "power", "fan"): h[k] = v
        elif k == "name": h["machine_name"] = v.replace("_", " ")
        else: raise ValueError("unknown hardware word %r" % k)
    if not re.fullmatch(r"m\d+(pro|max|ultra)?", h.get("chip_key", "")):
        raise ValueError("hardware spec needs a chip (m1, m2pro, m4max, ...) and ram")
    if not h.get("ram_mb"):
        raise ValueError("hardware spec needs ram=<GB>")
    p, e = CORES_BY_CHIP.get(h["chip_key"], (0, 0))
    h.setdefault("pcores", p); h.setdefault("ecores", e)
    h["chip"] = "Apple " + re.sub(r"m(\d+)(\w*)", lambda m: "M%s%s" % (m.group(1), (" " + m.group(2).capitalize()) if m.group(2) else ""), h["chip_key"])
    return _finish(h)


def detect(environ=None, run=_run, full=False):
    """The hardware dict of this machine, or None when it cannot be told (not macOS on Apple silicon, NPS5_HW=none).
    NPS5_HW=<spec> replaces detection. full=True also asks system_profiler for the model name (slower)."""
    env = os.environ if environ is None else environ
    spec = env.get("NPS5_HW", "")
    if spec == "none":
        return None
    if spec:
        return parse_spec(spec)
    brand = _sysctl("machdep.cpu.brand_string", run)
    key, gen = chip_key(brand)
    mem = _sysctl("hw.memsize", run)
    if key == "unknown" or not mem.isdigit():
        return None
    h = dict(chip=brand, chip_key=key, gen=gen, ram_mb=int(mem) // 1048576)
    p, e = _sysctl("hw.perflevel0.physicalcpu", run), _sysctl("hw.perflevel1.physicalcpu", run)
    h["pcores"] = int(p) if p.isdigit() else 0
    h["ecores"] = int(e) if e.isdigit() else 0
    if not h["pcores"]:                                   # no perf levels reported: count every core as fast
        n = _sysctl("hw.physicalcpu", run); h["pcores"] = int(n) if n.isdigit() else 0
    h["os"] = _sysctl("kern.osproductversion", run); h["osbuild"] = _sysctl("kern.osversion", run)
    h["model"] = _sysctl("hw.model", run)
    batt = run(["/usr/bin/pmset", "-g", "batt"])
    if "AC Power" in batt: h["power"] = "ac"
    elif "Battery Power" in batt: h["power"] = "battery"
    if batt and "InternalBattery" not in batt: h["power"] = "ac"; h["fan"] = "yes"      # desktop
    if full:
        try:
            sp = json.loads(run(["/usr/sbin/system_profiler", "-json", "SPHardwareDataType"], 20) or "{}")
            h["machine_name"] = sp["SPHardwareDataType"][0].get("machine_name", "")
        except (ValueError, KeyError, IndexError, TypeError):
            pass
    return _finish(h)


# ---------------------------------------------------------------- derived defaults
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def defaults(h):
    """Limits and worker counts for a machine. On the reference machine (24576 MiB, 10 P + 4 E) every value is
    the hand-tuned one: 18944 / 6144 / 20480 / 18432 / 12288 / 12 / 24 / 4 / 4 / 3."""
    ram, p, e = h["ram_mb"], h["pcores"], h["ecores"]
    load = max(4, p + e // 2)                 # an efficiency core is worth about half a performance core here
    return {
        # the title exits cleanly (code 86) at this footprint, before the guard's kill
        "NPS5_SELF_MEM_LIMIT_MB": ram * 37 // 48,
        # GPU budget the back end plans with: a quarter of RAM, never above the measured 6144 (7168 hit the limit on 24 GB)
        "vram_budget_mb": _clamp(ram // 4 // 512 * 512, 2048, 6144),
        # machine guard (host/machine_guard.sh)
        "NPS5_GUARD_RUN_CAP_MB": ram * 5 // 6,          # SIGKILL above this footprint
        "NPS5_GUARD_FAST_MB": ram * 3 // 4,             # 0.5 s polling above this footprint
        "NPS5_GUARD_SWAP_MB": ram // 2,                 # swap above this: the largest run is killed
        "NPS5_GUARD_HOLD_SWAP_MB": ram // 2,            # ... and no new title run starts
        "NPS5_GUARD_HOLD_LOAD": load,                   # perf-class runs wait above this 1-minute load average
        "NPS5_FUNC_HOLD_LOAD": 2 * load,                # func-class runs wait above this
        "NPS5_BUILD_JOBS": _clamp(p * 2 // 5, 2, 4),    # builds run beside title runs
        # back-end compile workers while a game runs (the code defaults fit 10 performance cores)
        "journal_threads": _clamp(p * 2 // 5, 2, 4),
        "skip_threads": _clamp(p * 3 // 10, 1, 3),
    }


EXPORTED = ("NPS5_SELF_MEM_LIMIT_MB", "NPS5_GUARD_RUN_CAP_MB", "NPS5_GUARD_FAST_MB", "NPS5_GUARD_SWAP_MB",
            "NPS5_GUARD_HOLD_SWAP_MB", "NPS5_GUARD_HOLD_LOAD", "NPS5_FUNC_HOLD_LOAD", "NPS5_BUILD_JOBS")


def cache_key(h):
    """<gpu family>-<OS build>: compiled GPU code is valid for one GPU generation and one OS build."""
    return "%s-%s" % (h["gpufam"], re.sub(r"[^A-Za-z0-9]", "", h["osbuild"]) or "unknown")


def env_lines(h, environ=None):
    env = os.environ if environ is None else environ
    out = [("NPS5_HW_CLASS", h["class"]), ("NPS5_HW_CACHE_KEY", cache_key(h))]
    d = defaults(h)
    out += [(k, str(d[k])) for k in EXPORTED]
    return ["export %s=%s" % (k, v) for k, v in out if not env.get(k)]


# ---------------------------------------------------------------- support verdict
def verdict(h):
    """-> (status, [notes]); status: tested | experimental | untested | unsupported."""
    notes, status = [], "untested"
    if h["class"] == REFERENCE_CLASS and h["os_major"] == MIN_MACOS:
        status = "tested"
    if h["gen"] in (1, 2):
        notes.append("M1/M2 (%s): no title has run on this GPU generation. Known risks: compute kernels above 384 threads "
                     "(KosmicKrisp sets the thread limit on every compute pipeline, unverified on this GPU); "
                     "depth bounds and other newest-family features are emulated" % h["gpufam"])
    if h["gpufam"] == "g17" or h["gen"] >= 5:
        notes.append("M5 (g17): untested; tools/agxdis refuses this GPU generation (decoders cover g13-g16)")
    if h["gpufam"] == "unknown":
        notes.append("unknown GPU generation: conditional rows on gpufam= never hold; tools/agxdis will refuse")
    if 0 < h["pcores"] <= 6:
        notes.append("%d performance cores: compile workers are reduced (games/hw-defaults.tsv, untested); the back end's "
                     "spin-waiting draw-prep workers were tuned on 10" % h["pcores"])
    if h["fan"] == "no":
        notes.append("no fan (%s): sustained runs throttle; frame times from this machine are not comparable, and "
                     "perf-class benchmarks need a cold start" % h["machine_name"])
    if h["power"] == "battery":
        notes.append("on battery: perf-class runs need AC power")
    if h["ram_gb"] and h["ram_gb"] <= EXPERIMENTAL_RAM_GB and status != "unsupported":
        status = "experimental"
        notes.append("%d GB: experimental and untested. The memory limit is %d MiB; ASTRO BOT levels peak at 16.3-17.8 GiB "
                     "on 24 GB, so profiles carry smaller budgets and mip skip for ram<=16 (untested); expect exit code 86 "
                     "(memory limit) in levels" % (h["ram_gb"], defaults(h)["NPS5_SELF_MEM_LIMIT_MB"]))
    if h["ram_gb"] and h["ram_gb"] < MIN_RAM_GB:
        status = "unsupported"; notes.append("less than %d GB of RAM: not supported" % MIN_RAM_GB)
    if h["os_major"] and h["os_major"] < MIN_MACOS:
        status = "unsupported"
        notes.append("macOS %s: macOS %d or later is required (Metal 4)" % (h["os"], MIN_MACOS))
    if h["os_major"] > MIN_MACOS:
        notes.append("macOS %s: newer than the tested macOS %d; compiled caches are per OS build and rebuild once" % (h["os"], MIN_MACOS))
    return status, notes


def doctor(h, out=None):
    out = out or sys.stdout
    w = lambda k, v: out.write("  %-22s %s\n" % (k, v))
    if h is None:
        out.write("hardware: not an Apple silicon Mac (or NPS5_HW=none): unsupported\n"); return 1
    d = defaults(h); status, notes = verdict(h)
    out.write("hardware\n")
    w("machine class", h["class"])
    w("chip", h["chip"])
    w("GPU family", "%s (AGX generation; tools/agxdis: %s)" % (h["gpufam"], "supported" if h["gpufam"] in AGXDIS_GPUS else "refused"))
    w("cores", "%d performance + %d efficiency" % (h["pcores"], h["ecores"]))
    w("memory", "%d GB (%d MiB)" % (h["ram_gb"], h["ram_mb"]))
    w("macOS", "%s (build %s; minimum %d)" % (h["os"], h["osbuild"], MIN_MACOS))
    w("model", "%s%s" % (h["model"], "" if h["machine_name"] == "unknown" else " (%s)" % h["machine_name"]))
    w("power", {"ac": "AC power", "battery": "battery"}.get(h["power"], "unknown"))
    w("fan", {"yes": "yes", "no": "none (fanless)"}.get(h["fan"], "unknown"))
    w("cache key", cache_key(h))
    out.write("derived limits (docs/reference/hardware.md)\n")
    w("memory self-limit", "%d MiB (37/48 of RAM)" % d["NPS5_SELF_MEM_LIMIT_MB"])
    w("GPU budget default", "%d MiB (profiles may set their own)" % d["vram_budget_mb"])
    w("guard", "kill > %d MiB, fast poll > %d MiB, swap > %d MiB" % (d["NPS5_GUARD_RUN_CAP_MB"], d["NPS5_GUARD_FAST_MB"], d["NPS5_GUARD_SWAP_MB"]))
    w("start hold", "load > %d (perf) / %d (func), swap > %d MiB, memory pressure warn" % (d["NPS5_GUARD_HOLD_LOAD"], d["NPS5_FUNC_HOLD_LOAD"], d["NPS5_GUARD_HOLD_SWAP_MB"]))
    w("workers", "build jobs %d, pipeline journal %d, pipeline skip %d" % (d["NPS5_BUILD_JOBS"], d["journal_threads"], d["skip_threads"]))
    out.write("support: %s\n" % status.upper())
    for n in notes:
        out.write("  - %s\n" % n)
    return 1 if status == "unsupported" else 0


def main(argv, environ=None):
    env = dict(os.environ if environ is None else environ)
    if argv[:1] == ["--hw"]:
        if len(argv) < 2: print("hw.py: --hw needs a spec", file=sys.stderr); return 2
        env["NPS5_HW"] = argv[1]; argv = argv[2:]
    cmd = argv[0] if argv else "doctor"
    if cmd in ("-h", "--help"):
        print(__doc__); return 0
    try:
        h = detect(env, full=(cmd in ("doctor", "json")))
    except ValueError as e:
        print("hw.py: %s" % e, file=sys.stderr); return 2
    if cmd == "doctor":
        return doctor(h)
    if h is None:
        if cmd == "env": return 0            # nothing known: nps5-env.sh keeps its own fallbacks
        print("hw.py: hardware unknown", file=sys.stderr); return 1
    if cmd == "class": print(h["class"])
    elif cmd == "cache-key": print(cache_key(h))
    elif cmd == "env": print("\n".join(env_lines(h, env)))
    elif cmd == "json": print(json.dumps(dict(h, defaults=defaults(h), cache_key=cache_key(h), support=verdict(h)[0]), indent=1, sort_keys=True))
    else:
        print("hw.py: unknown command %s (doctor, class, cache-key, env, json)" % cmd, file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
