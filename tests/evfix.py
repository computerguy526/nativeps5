"""Synthetic fixtures for the evidence-tool tests: frames logs, perf csvs, BMPs, run directories, commit logs.
Nothing here is, or is derived from, game data."""
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "scripts", "nps5lib")
sys.path.insert(0, LIB)
sys.dont_write_bytecode = True


def frames_log(segments, t0=1.0):
    """segments = [(count, frame_ms), ...] -> ("time_us,frame_us" text, end time of the last frame in s). Frame times are exact."""
    t, lines = t0, []
    for n, ms in segments:
        for _ in range(n):
            t += ms / 1000.0
            lines.append("%d,%d" % (round(t * 1e6), round(ms * 1000)))
    return "\n".join(lines) + "\n", t


def perf_csv(wall_ms):
    return "frame,wall_ms,flip_thread_cpu_ms,process_cpu_ms,hle_wall_ms\n" + "".join("%d,%.3f,1,2,3\n" % (i, w) for i, w in enumerate(wall_ms))


def jitter(n, base, seed, amp=0.3):
    r = random.Random(seed)
    return [base + r.uniform(-amp, amp) for _ in range(n)]


def make_run(root, title, tag, wall_ms, arm=None, env=None, total=None, comps=None, logs=None, bmps=None):
    """A run directory <root>/<title>/<tag> with run.json, perf_frames.csv, optional logs and frames/*.bmp."""
    d = os.path.join(root, title, tag)
    os.makedirs(os.path.join(d, "frames"), exist_ok=True)
    total = total if total is not None else sum(wall_ms) / 1000.0
    rj = dict(schema=1, title=title, tag=tag, arm=dict(label=arm, env=env or [], pieces={}) if arm else None, env=env or {}, components=comps or {},
              machine=dict(chip="Test chip", mem_gb=24, model="T1"), timings=dict(start=None, end=None, wall_s=total), rc=0, verdict="ok",
              build=dict(version="01.000"), logs={"out": os.path.join(d, "run.out"), "err": os.path.join(d, "run.err")})
    for k in ("class", "gpu", "scene"):
        rj[k] = {"class": "perf", "gpu": "kyty", "scene": None}[k]
    with open(os.path.join(d, "run.json"), "w") as f:
        json.dump(rj, f)
    with open(os.path.join(d, "frames", "perf_frames.csv"), "w") as f:
        f.write(perf_csv(wall_ms))
    for name, text in (logs or {}).items():
        with open(os.path.join(d, name), "w") as f:
            f.write(text)
    for name, data in (bmps or {}).items():
        with open(os.path.join(d, "frames", name), "wb") as f:
            f.write(data)
    return d


def commit_log(n_frames, t0=100.0, period=0.020, c1=0.005, overlap=0.003, c2=0.005):
    """Per frame: commit 1 [t, t+c1] with a 1920x1080 pass (3 pipeline binds) and a PRESENT commit starting `overlap`
    after t. GPU busy per frame = overlap + c2 ... union of the two intervals."""
    out = []
    for i in range(n_frames):
        t = t0 + i * period
        out.append("%.6f %.6f R1920x1080/c1*1/d2 Pfs1+vs1*3" % (t, t + c1))
        out.append("%.6f %.6f PRESENT" % (t + overlap, t + overlap + c2))
    return "\n".join(out) + "\n"


def write(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
