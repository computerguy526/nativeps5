#!/usr/bin/env python3 -I
"""keyguard: release gate "every shipped Metal row is still REACHABLE by the key the candidate stack produces".

Why (2026-11-27): a row of metal-shaders.tsv / metal-gfx-pairs.tsv is found at run time by KosmicKrisp's key of the text it writes for the
SPIR-V Kyty emits. A Kyty code-generation change (the v1.4 base) moved that key for 107 graphics rows and every mesh row; nothing failed, the
rows simply never matched again (0 foliage hits, 0 mesh hits) for three releases. keyguard makes that a FAIL before a release.

Evidence = keys files: for every list row, the key KosmicKrisp gives the row's own pipeline (shipped pipeline list + the SPIR-V the candidate
Kyty recorded in a programs.bin), produced offline by the rehearsal's keyer (no title run, no Metal compile). One JSON object per line:
  {"list": "gfx"|"cs", "row": <1-based line of the row in its list>, "old": [row keys], "key": [live keys]}   a pipeline of the row
  {"list": ..., "row": ..., "old": [...], "err": "not in programs.bin ..." | "no list pipeline ..." | "no keys ..."}
and next to it <keys>.meta.json = {"programs_fingerprint", "gfx_sha256", "cs_sha256", "kyplst_sha256"}: which Kyty code generation recorded
the SPIR-V and which lists the rows were read from. Stale evidence is refused (another code generation, another list).

Row status (per GPU class the row serves):
  LIVE     the row's own key is what its pipeline gets today
  COVERED  the key moved, and another row of the list carries today's key with the same texts for that class (an alias row)
  MOVED    the key moved and no row carries today's key: the row can never match          -> FAIL unless accounted `moved`
  NOPIPE   no pipeline of the shipped list is the row's                                    -> FAIL unless accounted `nopipe`
  NOPROG   the row's program is not in the programs.bin (not checkable)                    -> FAIL unless accounted `noprog`
  NOKEY    the keyer could not translate the pipeline                                      -> FAIL (never accountable: fix the tool)
  NOEVID   the keys files say nothing about the row                                        -> FAIL (never accountable)
Accounting = games/<id>/keyguard-account.tsv (hashes and words): `<gfx|cs> <key or vs/fs> <class> <moved|nopipe|noprog> <reason>`.
The reason is mandatory and specific; an entry whose status is not the row's status FAILS (a `noprog` row that turns out MOVED), an entry
for a row that is LIVE again FAILS for moved / nopipe (stale accounting), an entry that names no row FAILS.

Record = games/<id>/keyguard.record: the PASS, bound to the Kyty commit of components.lock, the Kyty code-generation hash, the KosmicKrisp
tree (kosmickrisp/EXPECTED_TREE), and the sha256 of both lists, the pipeline list and the accounting. scripts/check.sh refuses a tree whose
record is for another Kyty commit, KosmicKrisp tree or list (record_problems). Hashes and counts only, no shader code, no paths.

Precision (2026-11-27, the mesh regression): a key that moves tells nothing about WHY. The v1.4 Kyty base stopped emitting NoContraction
in mesh programs (2,364 decorations -> 0); the rows were re-keyed by alias and only a hand-made SPIR-V diff showed that the validated text
now stood against other float semantics. So a keys line may carry "prec": [NoContraction decorations, float arithmetic ops] of the pipeline's
FIRST stage (vertex / mesh: the stage that computes positions), counted by the keyer from the recorded SPIR-V, and
games/<id>/keyguard-precision.tsv records that census per first-stage key at the time the row was validated (`precision-record`):
  - a row served by its own key whose census differs from the recorded one                      -> FAIL (never accountable)
  - a row reached through an alias (key moved) whose census today differs from the census recorded for its old key, or has none recorded
    -> FAIL "PRECISION": the alias carries a text validated against other arithmetic. Re-validate on the GPU, then accept today's key with
       its census and a reason (`precision-record --accept <key> --reason ...`).
  - a mesh row whose program has float arithmetic and no NoContraction at all                    -> FAIL (positions are not exact)
Without the precision file nothing is compared (one INFO line); the mesh rule always applies when the evidence has "prec".

usage:
  keyguard.py precision-record --game games/<id> --keys K.jsonl [...] [--accept KEY16 --reason TEXT]   record the census of rows served by their own key
  keyguard.py check --game games/<id> --keys K.jsonl [--keys K2.jsonl ...] [--account FILE] [--kyty-lib kyty_gpu.dylib] [--kk-lib libvulkan_kosmickrisp.dylib]
                    [--report OUT.tsv] [--record --repo REPO]        exit 0 PASS, 1 FAIL
  keyguard.py record-check --repo REPO [--game <id>] [--kyty-lib LIB]   is the committed record the tree's (and this lib's)? exit 0 / 1
  keyguard.py carry --repo REPO --game <id> --kyty-lib LIB              a Kyty pin move with the SAME code generation: re-stamp the commit (no keyer run)
  keyguard.py propose --game games/<id> --keys K.jsonl ...              account lines for every unaccounted row, reason left to write
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import sys

ACCOUNT_NAME, RECORD_NAME = "keyguard-account.tsv", "keyguard.record"
PRECISION_NAME, PRECISION_FORMAT = "keyguard-precision.tsv", "format\tnps5-keyguard-precision\t1"
ACCOUNT_FORMAT = "format\tnps5-keyguard-account\t1"
STATUSES = ("moved", "nopipe", "noprog")
OK_STATES, NEVER = ("LIVE", "COVERED"), ("NOKEY", "NOEVID")
STATE_OF = {"moved": "MOVED", "nopipe": "NOPIPE", "noprog": "NOPROG"}
CS_ROW = re.compile(r"^[0-9a-f]{32}\t")
KEY_RE = re.compile(r"^[0-9a-f]{16}(/[0-9a-f]{16})?$")
VAGUE = re.compile(r"^\W*(todo|tbd|unknown|n/?a|none|reason\??|fix ?me|later|pending|see above|\?+)\W*$", re.I)
REASON_MIN = 16
FP_MARK = b"KytyCodegenFingerprint/1"


def _read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def _write(path, text):
    with open(path, "w") as f:
        f.write(text)


def base_class(cls):
    return cls.split("@")[0]


def load_rows(game_dir, metal=None):
    """-> [dict(list, line, key (tuple), cls, texts (tuple), pending)] for every text row of both lists (rows KosmicKrisp's own text serves are not rows)."""
    if metal is None:
        from nps5lib import metal
    out = []
    p = os.path.join(game_dir, "metal-shaders.tsv")
    if os.path.isfile(p):
        text = _read(p)
        _m, rows = metal.parse_list(text)
        lines = [i + 1 for i, l in enumerate(text.split("\n")) if CS_ROW.match(l)]
        if len(lines) != len(rows):
            raise ValueError("%s: %d row lines but %d parsed rows" % (p, len(lines), len(rows)))
        for n, r in zip(lines, rows):
            out.append(dict(list="cs", line=n, key=(r["kk"],), cls=r["cls"], texts=(r["b"],), pending="pendval" in r["evidence"]))
    p = os.path.join(game_dir, "metal-gfx-pairs.tsv")
    if os.path.isfile(p):
        text = _read(p)
        _m, rows = metal.parse_gfx_list(text)
        lines = [i + 1 for i, l in enumerate(text.split("\n")) if l.startswith("gfx\t")]
        if len(lines) != len(rows):
            raise ValueError("%s: %d row lines but %d parsed rows" % (p, len(lines), len(rows)))
        for n, r in zip(lines, rows):
            if r["mode"] == "kk" or r["b_vs"] == "-":
                continue
            out.append(dict(list="gfx", line=n, key=(r["kk_vs"], r["kk_fs"]), cls=r["cls"], texts=(r["b_vs"], r["b_fs"]), pending="pendval" in r["evidence"]))
    return out


def read_keys(paths):
    """-> ({(list, line): [entry]}, [meta dict per file], [problems])."""
    by, metas, bad = collections.defaultdict(list), [], []
    for p in paths:
        try:
            meta = json.loads(_read(p + ".meta.json"))
        except (OSError, ValueError):
            bad.append("%s: no readable .meta.json next to it (which code generation and lists is this evidence for?)" % os.path.basename(p))
            meta = {}
        metas.append(meta)
        try:
            for l in _read(p).split("\n"):
                if l.strip():
                    j = json.loads(l)
                    by[(j.get("list", "gfx"), j["row"])].append(j)
        except (OSError, ValueError, KeyError) as e:
            bad.append("%s: unreadable keys file (%s)" % (os.path.basename(p), e))
    return by, metas, bad


def sha_file(path):
    try:
        return hashlib.sha256(_read(path, "rb")).hexdigest()
    except OSError:
        return ""


def list_shas(game_dir):
    return dict(gfx_sha256=sha_file(os.path.join(game_dir, "metal-gfx-pairs.tsv")), cs_sha256=sha_file(os.path.join(game_dir, "metal-shaders.tsv")),
                kyplst_sha256=sha_file(os.path.join(game_dir, "pipelines.kyplst")))


def evidence_problems(metas, shas, lib_bytes=None):
    """Why the keys files are not evidence for THIS list and THIS Kyty build. lib_bytes = the candidate kyty_gpu.dylib (None = not checked here)."""
    bad = []
    fps = {m.get("programs_fingerprint", "") for m in metas}
    for i, m in enumerate(metas):
        if not m:
            continue
        for k in ("gfx_sha256", "cs_sha256", "kyplst_sha256"):
            if m.get(k, "") != shas[k]:
                bad.append("keys file %d is for another %s (sha256 differs): rerun the keyer on this tree" % (i + 1, k[:-7].replace("gfx", "metal-gfx-pairs.tsv").replace("cs", "metal-shaders.tsv").replace("kyplst", "pipelines.kyplst")))
        if not re.fullmatch(r"[0-9a-f]{64}", m.get("programs_fingerprint", "")):
            bad.append("keys file %d: its programs.bin names no Kyty code generation (old cache format): not evidence" % (i + 1))
    if len(fps) > 1:
        bad.append("the keys files are from programs.bin of different Kyty code generations (%s)" % ", ".join(sorted(f[:8] or "none" for f in fps)))
    if lib_bytes is not None:
        if FP_MARK not in lib_bytes:
            bad.append("the Kyty library carries no code-generation fingerprint")
        for f in sorted(fps):
            if f and f.encode() not in lib_bytes:
                bad.append("STALE SPIR-V: the programs.bin was recorded by code generation %s, which is not the candidate Kyty library's" % f[:8])
    return bad


def classes_of(rows):
    return sorted({base_class(r["cls"]) for r in rows if r["cls"] != "*"})


def row_states(rows, keys):
    """-> [(row, {class: state}, detail)] : the state of every row for every GPU class it serves."""
    concrete = classes_of(rows) or ["*"]
    carriers = collections.defaultdict(list)                   # (list, key) -> rows listing that key
    for r in rows:
        carriers[(r["list"], r["key"])].append(r)
    out = []
    for r in rows:
        serves = concrete if r["cls"] == "*" else [base_class(r["cls"])]
        ents = keys.get((r["list"], r["line"]), [])
        wrong = [e for e in ents if tuple(e.get("old", ())) != r["key"]]
        if wrong:
            out.append((r, {c: "NOEVID" for c in serves}, "the keys file names another row on this line (keys of another list version)"))
            continue
        if not ents:
            out.append((r, {c: "NOEVID" for c in serves}, "no entry in the keys files"))
            continue
        live = {tuple(e["key"]) for e in ents if "key" in e}
        if not live:
            errs = sorted({e.get("err", "no keys") for e in ents})
            st = "NOPROG" if all(x.startswith("not in programs.bin") for x in errs) else "NOPIPE" if all(x.startswith(("no list pipeline", "not in programs.bin")) for x in errs) else "NOKEY"
            if st == "NOPIPE" and any(x.startswith("not in programs.bin") for x in errs):
                st = "NOPROG"                                  # several caches: missing in one, no pipeline in another = still not checkable
            out.append((r, {c: st for c in serves}, "; ".join(errs)[:160]))
            continue
        if r["key"] in live:
            extra = sorted("/".join(k) for k in live if k != r["key"])
            out.append((r, {c: "LIVE" for c in serves}, ("also gets %s on another pipeline" % ", ".join(extra)) if extra else ""))
            continue
        st = {}
        for c in serves:
            ok = any(o is not r and o["texts"] == r["texts"] and (o["cls"] == "*" or base_class(o["cls"]) == c)
                     for k in live for o in carriers.get((r["list"], k), ()))
            st[c] = "COVERED" if ok else "MOVED"
        out.append((r, st, "today's key %s" % ", ".join(sorted("/".join(k) for k in live))))
    return out


# ---------------------------------------------------------------- precision
def read_precision(path):
    """-> ({first-stage key: (noc, arith, note)} or None when there is no file, [problems])."""
    try:
        lines = _read(path).split("\n")
    except OSError:
        return None, []
    out, bad = {}, []
    for n, l in enumerate(lines, 1):
        if not l or l.startswith("#") or l == PRECISION_FORMAT:
            continue
        c = l.split("\t")
        if len(c) not in (3, 4) or not re.fullmatch(r"[0-9a-f]{16}", c[0]) or not c[1].isdigit() or not c[2].isdigit():
            bad.append("precision line %d is not `<first-stage key> <NoContraction> <float ops> [reason]`" % n)
            continue
        if c[0] in out:
            bad.append("precision line %d: key %s is listed twice" % (n, c[0]))
        out[c[0]] = (int(c[1]), int(c[2]), c[3] if len(c) == 4 else "")
    return out, bad


def _prec(e):
    p = e.get("prec")
    return (int(p[0]), int(p[1])) if isinstance(p, (list, tuple)) and len(p) == 2 else None


def precision_judge(rows, keys, recorded, is_mesh=lambda r: False):
    """-> (results [(ok, name, detail)], learn {first-stage key: (noc, arith)} of the rows served by their own key).
    recorded = read_precision's dict, or None (no file: only the mesh rule). Graphics rows only (compute has no position)."""
    res, learn, n_checked, n_noprec = [], {}, 0, 0
    for r in rows:
        if r["list"] != "gfx":
            continue
        kname = "gfx %s (%s, line %d)" % ("/".join(r["key"]), r["cls"], r["line"])
        ents = [e for e in keys.get((r["list"], r["line"]), []) if "key" in e and tuple(e.get("old", ())) == r["key"]]
        if not ents:
            continue
        cens = {_prec(e) for e in ents}
        if None in cens:
            n_noprec += 1
            cens.discard(None)
        if not cens:
            continue
        n_checked += 1
        own = any(tuple(e["key"]) == r["key"] for e in ents)
        today = sorted(cens)
        say = lambda c: "%d NoContraction of %d float ops" % c
        if is_mesh(r) and any(c[1] > 0 and c[0] == 0 for c in today):
            res.append((False, "PRECISION: %s" % kname, "mesh program with float arithmetic and no NoContraction at all: its positions are not exact (Kyty MadMode::Position contract)"))
            continue
        if recorded is None:
            if own and len(today) == 1:
                learn[r["key"][0]] = today[0]
            continue
        want = recorded.get(r["key"][0])
        if own:
            if len(today) == 1:
                learn[r["key"][0]] = today[0]
            if want is not None and any(c != want[:2] for c in today):
                res.append((False, "PRECISION: %s" % kname, "same key, other census: recorded %s, today %s (evidence of another program?)" % (say(want[:2]), "; ".join(map(say, today)))))
            continue
        # the key moved: the row is reached through today's key(s)
        for e in ents:
            c, k2 = _prec(e), e["key"][0]
            if c is None or tuple(e["key"]) == r["key"]:
                continue
            acc = recorded.get(k2)
            if acc is not None and acc[:2] == c and (k2 == r["key"][0] or not reason_problem(acc[2])):
                continue                                  # today's key was accepted with this census after re-validation
            if want is None:
                res.append((False, "PRECISION: %s" % kname, "the key moved (today %s, %s) and no census was recorded for the row's own key: the alias cannot be shown to keep the float semantics the text was validated against" % (k2, say(c))))
            elif want[:2] != c:
                res.append((False, "PRECISION: %s" % kname, "the key moved to %s AND Kyty's precision for the program changed: validated at %s, today %s. Re-validate the text, then `precision-record --accept %s --reason ...`" % (k2, say(want[:2]), say(c), k2)))
            break
    if recorded is None:
        res.append((None, "precision", "no %s: census not compared (%d rows carry one; `precision-record` writes it)" % (PRECISION_NAME, n_checked)))
    elif n_noprec:
        res.append((None, "precision", "%d rows have evidence without a census (keyer older than the precision check)" % n_noprec))
    return res, learn


def precision_text(entries):
    lines = ["# keyguard precision census (scripts/nps5lib/keyguard.py): per first-stage key, NoContraction decorations and float arithmetic ops of",
             "# the SPIR-V Kyty emitted when the row was validated. Counts only. A 4th column = why a re-keyed row was accepted with another census.",
             PRECISION_FORMAT]
    lines += ["\t".join([k, str(v[0]), str(v[1])] + ([v[2]] if len(v) > 2 and v[2] else [])) for k, v in sorted(entries.items())]
    return "\n".join(lines) + "\n"


def read_account(path):
    """-> ([(list, key tuple, cls, status, reason, line no)], [problems])."""
    ents, bad = [], []
    try:
        lines = _read(path).split("\n")
    except OSError:
        return ents, bad
    for n, l in enumerate(lines, 1):
        if not l or l.startswith("#") or l == ACCOUNT_FORMAT:
            continue
        c = l.split("\t")
        if len(c) != 5 or c[0] not in ("gfx", "cs") or not KEY_RE.match(c[1]) or c[3] not in STATUSES or (c[0] == "gfx") != ("/" in c[1]):
            bad.append("accounting line %d is not `<gfx|cs> <key> <class> <%s> <reason>`" % (n, "|".join(STATUSES)))
            continue
        why = reason_problem(c[4])
        if why:
            bad.append("accounting line %d (%s %s): %s" % (n, c[0], c[1][:17], why))
            continue
        ents.append((c[0], tuple(c[1].split("/")), c[2], c[3], c[4], n))
    return ents, bad


def reason_problem(reason):
    r = reason.strip()
    if len(r) < REASON_MIN or VAGUE.match(r) or r.lower() in STATUSES or "REASON?" in r:
        return "the reason must say WHY the row is unreachable and what happens next (at least %d characters, not a placeholder)" % REASON_MIN
    return None


def judge(rows, keys, account):
    """-> (results [(ok, name, detail)], per-row table [(row, {class: state}, detail, accounted reason or '')]).  ok None = information."""
    states = row_states(rows, keys)
    acct = collections.defaultdict(list)
    for e in account:
        acct[(e[0], e[1])].append(e)
    used, res, table = set(), [], []
    count = collections.Counter()
    for r, st, detail in states:
        kname = "%s %s (%s, line %d)" % (r["list"], "/".join(r["key"]), r["cls"], r["line"])
        worst = sorted(set(st.values()) - set(OK_STATES))
        for s in set(st.values()):
            count[(r["list"], s)] += sum(1 for v in st.values() if v == s) / float(len(st))
        ents = [e for e in acct.get((r["list"], r["key"]), ()) if e[2] in ("*", r["cls"]) or base_class(e[2]) == base_class(r["cls"])]
        if not worst:
            for e in ents:
                used.add(e[5])
                if e[3] != "noprog":
                    res.append((False, "stale accounting: %s" % kname, "accounted `%s` (line %d) but the row is %s again: remove the entry" % (e[3], e[5], "/".join(sorted(set(st.values()))))))
                else:
                    res.append((None, "accounting no longer needed: %s" % kname, "accounted `noprog` (line %d), the row is checkable now and reachable" % e[5]))
            table.append((r, st, detail, ""))
            continue
        bad_cls = sorted(c for c, v in st.items() if v not in OK_STATES)
        if any(w in NEVER for w in worst):
            res.append((False, "%s: %s" % ("/".join(worst), kname), detail + " (not accountable: fix the evidence)"))
            table.append((r, st, detail, ""))
            continue
        want = worst[0]
        match = [e for e in ents if STATE_OF[e[3]] == want]
        for e in ents:
            used.add(e[5])
        if match:
            res.append((None, "accounted %s: %s" % (want, kname), "classes %s: %s" % (",".join(bad_cls), match[0][4])))
            table.append((r, st, detail, match[0][4]))
        elif ents:
            res.append((False, "%s but accounted `%s`: %s" % (want, ents[0][3], kname), "%s; the accounting (line %d) describes another state" % (detail, ents[0][5])))
            table.append((r, st, detail, ""))
        else:
            what = {"MOVED": "UNREACHABLE: key moved, no row carries today's key", "NOPIPE": "no pipeline of the shipped list is this row's",
                    "NOPROG": "not checkable: program not in the programs.bin"}[want]
            res.append((False, "%s: %s" % (want, kname), "%s; classes %s; %s" % (what, ",".join(bad_cls), detail)))
            table.append((r, st, detail, ""))
    for e in account:
        if e[5] not in used:
            res.append((False, "accounting names no row: %s %s (%s)" % (e[0], "/".join(e[1]), e[2]), "line %d: remove it or fix the key / class" % e[5]))
    summ = collections.defaultdict(dict)
    for (lst, s), n in count.items():
        summ[lst][s] = round(n, 2)
    res.insert(0, (None, "rows", "; ".join("%s: %s" % (l, ", ".join("%s %g" % (s, summ[l][s]) for s in sorted(summ[l]))) for l in sorted(summ))))
    return res, table


def counts(table):
    c = collections.Counter()
    for r, st, _d, acc in table:
        worst = sorted(set(st.values()) - set(OK_STATES))
        c[(worst[0] if worst else ("COVERED" if "COVERED" in st.values() else "LIVE")) + ("+accounted" if acc else "")] += 1
    return dict(c)


# ---------------------------------------------------------------- record
def lock_kyty(lock_text):
    for l in lock_text.split("\n"):
        c = [x.strip() for x in l.split("|")]
        if len(c) >= 7 and c[0] == "kyty":
            return c[6]
    return ""


def record_text(kyty_commit, codegen, kk_tree, shas, account_sha, cnt, kyty_lib_sha="", kk_lib_sha="", date=None, note=""):
    lines = ["# keyguard record (scripts/nps5lib/keyguard.py): every shipped Metal row is reachable by the key this stack produces, or accounted.",
             "# Hashes and counts only. scripts/check.sh refuses a tree whose Kyty commit, KosmicKrisp tree or lists are not the ones below.",
             "result=PASS", "kyty=%s" % kyty_commit, "kyty_codegen=%s" % codegen, "kk_tree=%s" % kk_tree]
    lines += ["%s=%s" % (k, shas[k]) for k in ("gfx_sha256", "cs_sha256", "kyplst_sha256")] + ["account_sha256=%s" % account_sha]
    if kyty_lib_sha:
        lines.append("kyty_lib_sha256=%s" % kyty_lib_sha)
    if kk_lib_sha:
        lines.append("kk_lib_sha256=%s" % kk_lib_sha)
    lines.append("rows=%s" % " ".join("%s:%d" % (k, cnt[k]) for k in sorted(cnt)))
    if note:
        lines.append("note=%s" % note)
    lines.append("date=%s" % (date or datetime.date.today().isoformat()))
    return "\n".join(lines) + "\n"


def parse_record(text):
    return dict(l.split("=", 1) for l in text.split("\n") if "=" in l and not l.startswith("#"))


def record_problems(record, lock_text, kk_tree, shas, account_sha, lib_bytes=None):
    """Why a committed record is not THIS tree's (pure: the checker feeds blobs). shas = list_shas of the tree."""
    if not record.strip():
        return ["no keyguard record"]
    rec, bad = parse_record(record), []
    if rec.get("result") != "PASS":
        bad.append("the record is not a PASS")
    if rec.get("kyty", "") != lock_kyty(lock_text) or not rec.get("kyty"):
        bad.append("STALE: recorded for Kyty %s, components.lock pins %s (keyguard must run on the new Kyty; `keyguard.py carry` when its code generation is unchanged)"
                   % (rec.get("kyty", "?")[:10], lock_kyty(lock_text)[:10] or "?"))
    if rec.get("kk_tree", "") != kk_tree.strip() or not rec.get("kk_tree"):
        bad.append("STALE: recorded for KosmicKrisp tree %s, kosmickrisp/EXPECTED_TREE is %s (keyguard must run on the new KosmicKrisp)" % (rec.get("kk_tree", "?")[:10], kk_tree.strip()[:10]))
    for k, name in (("gfx_sha256", "metal-gfx-pairs.tsv"), ("cs_sha256", "metal-shaders.tsv"), ("kyplst_sha256", "pipelines.kyplst")):
        if rec.get(k, "") != shas.get(k, ""):
            bad.append("STALE: recorded for another %s (sha256 differs)" % name)
    if rec.get("account_sha256", "") != account_sha:
        bad.append("STALE: recorded for another %s (sha256 differs)" % ACCOUNT_NAME)
    if lib_bytes is not None and rec.get("kyty_codegen", "?").encode() not in lib_bytes:
        bad.append("STALE: recorded for Kyty code generation %s, which is not this library's" % rec.get("kyty_codegen", "?")[:8])
    return bad


def repo_inputs(repo, game):
    g = os.path.join(repo, "games", game)
    rd = lambda p: _read(p) if os.path.isfile(p) else ""
    return dict(game_dir=g, lock=rd(os.path.join(repo, "components.lock")), kk_tree=rd(os.path.join(repo, "kosmickrisp", "EXPECTED_TREE")).strip(),
                shas=list_shas(g), account_sha=sha_file(os.path.join(g, ACCOUNT_NAME)), record=rd(os.path.join(g, RECORD_NAME)))


def games_with_lists(repo):
    d = os.path.join(repo, "games")
    return sorted(x for x in (os.listdir(d) if os.path.isdir(d) else []) if os.path.isfile(os.path.join(d, x, "metal-gfx-pairs.tsv")) or os.path.isfile(os.path.join(d, x, "metal-shaders.tsv")))


# ---------------------------------------------------------------- CLI
def _print(res):
    nf = 0
    for ok, name, detail in res:
        nf += ok is False
        print("%-4s %s%s" % ("PASS" if ok else "INFO" if ok is None else "FAIL", name, (": " + detail) if detail else ""))
    return nf


def cmd_check(o):
    lib = _read(o.kyty_lib, "rb") if o.kyty_lib else None
    rows = load_rows(o.game)
    keys, metas, bad = read_keys(o.keys)
    shas = list_shas(o.game)
    apath = o.account or os.path.join(o.game, ACCOUNT_NAME)
    account, abad = read_account(apath)
    res = [(False, "evidence", b) for b in bad + evidence_problems(metas, shas, lib)] + [(False, "accounting file", b) for b in abad]
    jr, table = judge(rows, keys, account)
    res += jr
    recorded, pbad = read_precision(os.path.join(o.game, PRECISION_NAME))
    res += [(False, "precision file", b) for b in pbad]
    res += precision_judge(rows, keys, recorded, _mesh_test(o.game))[0]
    if o.kyty_lib is None:
        res.append((None, "candidate Kyty library not given", "the SPIR-V's code generation was not compared with a library (--kyty-lib): not recordable"))
    nf = _print(res)
    cnt = counts(table)
    if o.report:
        with open(o.report, "w") as f:
            f.write("list\tline\tclass\tkey\tstate\taccounted\tdetail\n")
            for r, st, d, acc in table:
                f.write("\t".join([r["list"], str(r["line"]), r["cls"], "/".join(r["key"]), ",".join("%s=%s" % kv for kv in sorted(st.items())), acc, d]) + "\n")
    print("keyguard: %s (%d FAIL; rows %s)" % ("FAIL" if nf else "PASS", nf, " ".join("%s:%d" % kv for kv in sorted(cnt.items()))))
    if o.record:
        if nf or lib is None or not o.repo:
            print("keyguard: NOT recorded (needs a PASS, --kyty-lib and --repo)")
            return 1
        game = os.path.basename(os.path.normpath(o.game))
        inp = repo_inputs(o.repo, game)
        if os.path.realpath(inp["game_dir"]) != os.path.realpath(o.game):
            print("keyguard: NOT recorded (--game is not games/%s of --repo)" % game)
            return 1
        fp = sorted({m.get("programs_fingerprint", "") for m in metas})[0]
        text = record_text(lock_kyty(inp["lock"]), fp, inp["kk_tree"], shas, sha_file(apath), cnt, hashlib.sha256(lib).hexdigest(),
                           sha_file(o.kk_lib) if o.kk_lib else "", note=o.note or "")
        _write(os.path.join(o.game, RECORD_NAME), text)
        print("keyguard: recorded %s (Kyty %s, code generation %s, KosmicKrisp tree %s)" % (os.path.join("games", game, RECORD_NAME), lock_kyty(inp["lock"])[:10], fp[:8], inp["kk_tree"][:10]))
    return 1 if nf else 0


def _mesh_test(game_dir):
    """is_mesh(row) for the rows of this game's graphics list (variant column, as nps5lib.metal.is_mesh)."""
    from nps5lib import metal
    p = os.path.join(game_dir, "metal-gfx-pairs.tsv")
    if not os.path.isfile(p):
        return lambda r: False
    text = _read(p)
    _m, rows = metal.parse_gfx_list(text)
    lines = [i + 1 for i, l in enumerate(text.split("\n")) if l.startswith("gfx\t")]
    mesh = {n for n, r in zip(lines, rows) if metal.is_mesh(r)}
    return lambda r: r["list"] == "gfx" and r["line"] in mesh


def cmd_precision_record(o):
    rows = load_rows(o.game)
    keys, _metas, bad = read_keys(o.keys)
    path = os.path.join(o.game, PRECISION_NAME)
    recorded, pbad = read_precision(path)
    if bad or pbad:
        print("keyguard precision-record: refused:\n  " + "\n  ".join(bad + pbad))
        return 1
    entries = dict(recorded or {})
    _res, learn = precision_judge(rows, keys, recorded, _mesh_test(o.game))
    added = 0
    for k, c in learn.items():
        if k not in entries:
            entries[k] = (c[0], c[1], "")
            added += 1
        elif entries[k][:2] != c:
            print("keyguard precision-record: refused: key %s is recorded with another census (%d/%d, today %d/%d)" % ((k,) + entries[k][:2] + c))
            return 1
    if o.accept:
        why = reason_problem(o.reason or "")
        cens = {_prec(e) for es in keys.values() for e in es if e.get("key") and e["key"][0] == o.accept} - {None}
        if why or len(cens) != 1:
            print("keyguard precision-record: --accept refused: %s" % (why or "the keys files give %d censuses for %s (need exactly one)" % (len(cens), o.accept)))
            return 1
        c = cens.pop()
        entries[o.accept] = (c[0], c[1], o.reason.strip())
        added += 1
    _write(path, precision_text(entries))
    print("keyguard precision-record: %s now has %d keys (%d new)" % (os.path.join(os.path.basename(os.path.normpath(o.game)), PRECISION_NAME), len(entries), added))
    return 0


def cmd_record_check(o):
    lib = _read(o.kyty_lib, "rb") if o.kyty_lib else None
    nf = 0
    for game in ([o.game] if o.game else games_with_lists(o.repo)):
        inp = repo_inputs(o.repo, game)
        bad = record_problems(inp["record"], inp["lock"], inp["kk_tree"], inp["shas"], inp["account_sha"], lib)
        for b in bad:
            print("FAIL %s: %s" % (game, b))
        if not bad:
            print("PASS %s: keyguard record is this tree's%s" % (game, " and this library's" if lib is not None else ""))
        nf += len(bad)
    return 1 if nf else 0


def cmd_carry(o):
    inp = repo_inputs(o.repo, o.game)
    rec = parse_record(inp["record"])
    lib = _read(o.kyty_lib, "rb")
    other = [b for b in record_problems(inp["record"], "kyty | | | | | | " + rec.get("kyty", ""), inp["kk_tree"], inp["shas"], inp["account_sha"], lib)]
    if other:
        print("keyguard carry: refused, the record is stale beyond the Kyty commit:\n  " + "\n  ".join(other))
        return 1
    new = lock_kyty(inp["lock"])
    text = "\n".join(("kyty=%s" % new) if l.startswith("kyty=") else l for l in inp["record"].rstrip("\n").split("\n"))
    text += "\ncarried=%s from Kyty %s (same code generation %s, library sha256 %s)\n" % (datetime.date.today().isoformat(), rec.get("kyty", "?")[:10], rec.get("kyty_codegen", "?")[:8], hashlib.sha256(lib).hexdigest()[:16])
    _write(os.path.join(inp["game_dir"], RECORD_NAME), text)
    print("keyguard carry: record now names Kyty %s" % new[:10])
    return 0


def cmd_propose(o):
    rows = load_rows(o.game)
    keys, _metas, _bad = read_keys(o.keys)
    account, _ = read_account(o.account or os.path.join(o.game, ACCOUNT_NAME))
    _res, table = judge(rows, keys, account)
    seen = set()
    for r, st, d, acc in table:
        worst = sorted(set(st.values()) - set(OK_STATES))
        if not worst or acc or worst[0] in NEVER:
            continue
        line = (r["list"], "/".join(r["key"]), r["cls"], {v: k for k, v in STATE_OF.items()}[worst[0]])
        if line not in seen:
            seen.add(line)
            print("\t".join(line + ("REASON? (%s)" % d[:80],)))
    return 0


def main(argv):
    ap = argparse.ArgumentParser(prog="keyguard.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--game", required=True); c.add_argument("--keys", action="append", required=True); c.add_argument("--account")
    c.add_argument("--kyty-lib"); c.add_argument("--kk-lib"); c.add_argument("--report"); c.add_argument("--record", action="store_true")
    c.add_argument("--repo"); c.add_argument("--note")
    c = sub.add_parser("record-check"); c.add_argument("--repo", required=True); c.add_argument("--game"); c.add_argument("--kyty-lib")
    c = sub.add_parser("carry"); c.add_argument("--repo", required=True); c.add_argument("--game", required=True); c.add_argument("--kyty-lib", required=True)
    c = sub.add_parser("propose"); c.add_argument("--game", required=True); c.add_argument("--keys", action="append", required=True); c.add_argument("--account")
    c = sub.add_parser("precision-record"); c.add_argument("--game", required=True); c.add_argument("--keys", action="append", required=True)
    c.add_argument("--accept"); c.add_argument("--reason")
    o = ap.parse_args(argv)
    return {"precision-record": cmd_precision_record, "check": cmd_check, "record-check": cmd_record_check, "carry": cmd_carry, "propose": cmd_propose}[o.cmd](o)


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main(sys.argv[1:]))
