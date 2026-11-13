# -*- coding: utf-8 -*-
"""Final scoring. Every input is a file in audit/ (metrics.json from score_audit.py, judge.json from judge_audit.py, runs in out/). The human reading verdicts are the HUMAN dict below (read by the auditor from the raw reports).
Formulas (0-5 each):
 Factual (30%)   = 5 x mean( line/evidence integrity , quote integrity , rhyme evidence score , section structure )
                   quote integrity = 1 - min(1, misquotes / 6) ; rhyme evidence = recall x (1 - false-claim rate) over the designed pairs (S7 bridge/fridge excluded: it is a real rhyme) ; structure = 1 when the report names the real sections
 Musical (25%)   = 0.5 x human reading + 0.25 x LLM judge + 0.25 x deterministic flags (5 x share of the song-specific flags that were met)
 Actionability(20%) = 0.5 x LLM judge + 0.5 x structural completeness (share of findings with real lines, a reason, valid evidence; minus findings on line 0 / empty lines)
 Faithfulness(15%) = 5 - 0.25 x misquotes (cap 2) - 1 x audio claims - 0.5 x findings that point at line 0 or an empty line - 0.5 x unsupported structure additions flagged by the judge/human (cap 1.5)
 Consistency (10%) = 5 x mean( flag agreement between the two rounds , overlap of the lines the findings point at , 1 - |judge difference| / 5 )"""
import json, re, statistics as st, sys
from pathlib import Path
AUD = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad\audit")
sys.stdout.reconfigure(encoding="utf-8")
M = json.loads((AUD / "metrics.json").read_text(encoding="utf-8"))
J = json.loads((AUD / "judge.json").read_text(encoding="utf-8"))

# human reading of the raw reports (0-5 = musical judgment of the pair of reports for that song), with the reason in the audit report
HUMAN = {
    ("grok", "S1"): 4, ("grok", "S2"): 4, ("grok", "S3"): 2, ("grok", "S4"): 4, ("grok", "S5"): 2, ("grok", "S6"): 4, ("grok", "S7"): 2, ("grok", "S8"): 4,
    ("local", "S1"): 1, ("local", "S2"): 2, ("local", "S3"): 1, ("local", "S4"): 1, ("local", "S5"): 1, ("local", "S6"): 3, ("local", "S7"): 1, ("local", "S8"): 2,
}
SONGS = [f"S{i}" for i in range(1, 9)]


def runs(kind, sid):
    return [(r, M[f"{kind}|{sid}|{r}"]) for r in ("1", "2") if f"{kind}|{sid}|{r}" in M]


def load_run(kind, sid, r):
    return json.loads((AUD / "out" / f"{kind}_{sid}_r{r}.json").read_text(encoding="utf-8"))


def factual(kind, sid):
    vals = []
    for r, x in runs(kind, sid):
        m = x["metrics"]
        reps = m["reports"].values()
        refs_ok = sum(d["refs_ok"] for d in reps)
        refs_bad = sum(d["refs_bad"] + d["finding_evidence_bad"] for d in reps)
        integ = refs_ok / max(1, refs_ok + refs_bad)
        misq = sum(d["quotes_bad"] for d in reps)
        qi = 1 - min(1, misq / 6)
        ev = m["rhyme_evidence"]
        true_total, false_total = ev["true_total"], ev["false_total"]
        false_claimed = ev["false_claimed"] if sid != "S7" else 0
        false_total = false_total if sid != "S7" else 0
        recall = ev["true_found"] / true_total if true_total else 1.0
        fr = false_claimed / false_total if false_total else 0.0
        rs = recall * (1 - fr)
        sec = 1.0 if x["flags"].get("sections_match_reference", {"ok": True})["ok"] else 0.0
        vals.append(5 * st.mean([integ, qi, rs, sec]))
    return st.mean(vals)


def musical(kind, sid):
    vals = []
    for r, x in runs(kind, sid):
        fl = x["flags"]
        flag_score = 5 * (sum(1 for v in fl.values() if v["ok"]) / max(1, len(fl)))
        # a flag that matched a keyword but whose context is wrong is corrected by the human reading below: S3/S5 on both backends were named only in passing
        j = J.get(f"{kind}|{sid}|{r}", {}).get("musical_judgment", 0)
        vals.append(0.5 * HUMAN[(kind, sid)] + 0.25 * j + 0.25 * flag_score)
    return st.mean(vals)


def action(kind, sid):
    vals = []
    for r, x in runs(kind, sid):
        rec = load_run(kind, sid, r)
        tot = ok = 0
        for rep in rec["reports"].values():
            for f in rep.get("findings") or []:
                tot += 1
                try:
                    a = int(f.get("first") or 0)
                except ValueError:
                    a = 0
                lines = rec["lyrics"].split("\n")
                blank = a < 1 or a > len(lines) or not lines[a - 1].strip()
                good = (not blank or f.get("kind") in ("cue", "arrangement", "structure")) and bool((f.get("reason") or "").strip())
                ok += good
        comp = ok / tot if tot else 0.6                      # no findings at all: an empty list is neutral (the song may be fine), judged by the LLM and the human reading instead
        j = J.get(f"{kind}|{sid}|{r}", {}).get("actionability", 0)
        vals.append(0.5 * j + 0.5 * 5 * comp)
    return st.mean(vals)


def faith(kind, sid):
    vals = []
    for r, x in runs(kind, sid):
        reps = x["metrics"]["reports"].values()
        misq = sum(d["quotes_bad"] for d in reps)
        audio = 0                                              # the single keyword hit (local S2 r1: 'ฟังแล้วรู้สึกว่า' = 'when read aloud it feels') was read by hand: an idiom, not a claim of having heard audio
        rec = load_run(kind, sid, r)
        lines = rec["lyrics"].split("\n")
        bad_lines = 0
        for rep in rec["reports"].values():
            for f in rep.get("findings") or []:
                try:
                    a = int(f.get("first") or 0)
                except ValueError:
                    a = 0
                if f.get("kind") not in ("cue", "arrangement") and (a < 1 or a > len(lines) or not lines[a - 1].strip()):
                    bad_lines += 1
        invented = len(J.get(f"{kind}|{sid}|{r}", {}).get("critical_errors") or [])
        vals.append(max(0, 5 - min(2, 0.25 * misq) - audio - 0.5 * bad_lines - min(1.5, 0.5 * invented)))
    return st.mean(vals)


def lineset(rec):
    s = set()
    for sp, rep in rec["reports"].items():
        for f in rep.get("findings") or []:
            if f.get("kind") in ("cue", "arrangement"):
                continue
            try:
                a, b = int(f.get("first") or 0), int(f.get("last") or 0)
            except ValueError:
                continue
            s |= {(sp, k) for k in range(a, b + 1)}
    return s


def consistency(kind, sid):
    if len(runs(kind, sid)) < 2:
        return None
    (r1, x1), (r2, x2) = runs(kind, sid)
    f1 = {k: v["ok"] for k, v in x1["flags"].items()}
    f2 = {k: v["ok"] for k, v in x2["flags"].items()}
    agree = sum(1 for k in f1 if f1[k] == f2.get(k)) / max(1, len(f1))
    a, b = lineset(load_run(kind, sid, r1)), lineset(load_run(kind, sid, r2))
    jac = (len(a & b) / len(a | b)) if (a | b) else 1.0
    j1, j2 = J.get(f"{kind}|{sid}|1", {}), J.get(f"{kind}|{sid}|2", {})
    jd = 1 - abs((j1.get("musical_judgment", 0) + j1.get("actionability", 0)) - (j2.get("musical_judgment", 0) + j2.get("actionability", 0))) / 10
    return 5 * st.mean([agree, jac, jd]), {"flag_agree": agree, "finding_line_overlap": jac, "judge_diff": 1 - jd}


W = {"Factual": 0.30, "Musical": 0.25, "Action": 0.20, "Faith": 0.15, "Consist": 0.10}
out = {}
for kind in ("grok", "local"):
    rows = {}
    for sid in SONGS:
        c = consistency(kind, sid)
        rows[sid] = {"Factual": factual(kind, sid), "Musical": musical(kind, sid), "Action": action(kind, sid), "Faith": faith(kind, sid), "Consist": c[0] if c else None, "consist_detail": c[1] if c else None}
        rows[sid]["Overall"] = sum(rows[sid][k] * w for k, w in W.items() if rows[sid][k] is not None) / sum(w for k, w in W.items() if rows[sid][k] is not None)
    out[kind] = rows
(AUD / "final_scores.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
for kind in out:
    print(f"== {kind}")
    print("song   Fact  Musi  Actn  Faith Cons  Overall")
    for sid, r in out[kind].items():
        print(f"{sid}   {r['Factual']:.1f}   {r['Musical']:.1f}   {r['Action']:.1f}   {r['Faith']:.1f}   {(r['Consist'] if r['Consist'] is not None else 0):.1f}   {r['Overall']:.2f}")
    avg = {k: st.mean(v[k] for v in out[kind].values() if v[k] is not None) for k in W}
    print("AVG ", "   ".join(f"{avg[k]:.1f}" for k in W), "  ", f"{sum(avg[k]*W[k] for k in W):.2f}")
