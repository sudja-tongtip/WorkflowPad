# -*- coding: utf-8 -*-
"""Scores Python-only, Hybrid V2 and the OLD Rhyme Analyzer (recorded Gemma answers of the audit, replayed through the production parser, no LLM) against the existing references.
Unit = a pair of lines. 'Not Evaluated' = a claim the reference says nothing about (never counted as right or wrong)."""
import json, sys, importlib.util
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
import hybrid as H
from music_studio import lyric_rhyme as LR, lyric_analysis as LA, rhyme_steps as RS

spec = importlib.util.spec_from_file_location("songs", ROOT / "docs/analyze_song_audit/tools/songs.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
RAWDIR = ROOT / "docs/analyze_song_audit/raw_runs"


def lines_of(text_lines, t):
    return [i + 1 for i, l in enumerate(text_lines) if l.strip() == t.strip() or t.strip() in l]


def ref_sets(sid):
    s = m.SONGS[sid]
    tl = s["text"].split("\n")
    def occ(pairs):
        out = set()
        for a, b in pairs:
            la, lb = lines_of(tl, a), lines_of(tl, b)
            if len(la) == len(lb):
                out |= {frozenset((x, y)) for x, y in zip(la, lb)}      # a repeated chorus: the 1st occurrence of a pairs with the 1st of b, the 2nd with the 2nd
            else:
                out |= {frozenset((x, y)) for x in la for y in lb}
        return out
    return occ(s["rhyme_true"]), occ(s["rhyme_false"])


def score(claims, T, F):
    """claims: set of frozenset line pairs the system AFFIRMS as rhymes (different lines)."""
    tp = sum(1 for t in T if t in claims)
    fn = len(T) - tp
    fp = sum(1 for f in F if f in claims)
    other = sorted(tuple(sorted(c)) for c in claims if c not in T and c not in F)
    return {"TP": tp, "FN": fn, "FP_vs_reference": fp, "reference_true": len(T), "reference_false": len(F), "not_evaluated_claims": other}


def old_analyzer(sid, rnd):
    f = RAWDIR / f"local_{sid}_r{rnd}.json"
    d = json.load(open(f, encoding="utf-8"))
    L = d["log"]
    lyrics = d["lyrics"]
    lines = LA.sung_lines(lyrics)
    bodies = [(f"s{i+1}", e["raw"]) for i, e in enumerate(x for x in L if x["system_tokens"] == 4413 and x["user"].startswith("SECTION"))]
    doc = RS.assemble(bodies)
    res = LR.analyze_rhymes(lines, lambda system, user: doc, language="th", system=LR.step_system_prompt())
    llm_pairs = (res.llm or {}).get("pairs") or []
    given = set()          # what the Songwriter was given: the LLM's own pairs (res.llm), unfiltered
    for p in llm_pairs:
        a, b = p["a"]["file_line"], p["b"]["file_line"]
        if a != b:
            given.add(frozenset((a, b)))
    kept = {frozenset((p.a_line, p.b_line)) for p in res.pairs if p.a_line != p.b_line and p.verify in ("verified", "llm_exact", "llm_near")}
    secs = sum(1 for e in L if e["system_tokens"] == 4413 and e["user"].startswith("SECTION"))
    return {"given_to_songwriter": given, "kept_after_python": kept, "requests": sum(1 for e in L if e["system_tokens"] == 4413), "seconds": round(sum(e["sec"] for e in L if e["system_tokens"] == 4413), 1)}


def affirmed(merged, statuses=("verified",), exclude_distant=False):
    out = set()
    for i in merged["items"]:
        if i["status"] in statuses and i["a"]["line"] != i["b"]["line"] and not (exclude_distant and i.get("scope") == "distant"):
            out.add(frozenset((i["a"]["line"], i["b"]["line"])))
    return out


report = {}
for sid in ("S1", "S2", "S3"):
    T, F = ref_sets(sid)
    s = m.SONGS[sid]
    eng = H.python_engine(s["text"])
    po = H.python_only(eng)
    r = {"python_only": score(affirmed(po), T, F), "python_only_without_distant": score(affirmed(po, exclude_distant=True), T, F)}
    raw = HERE / "raw" / f"{sid}.json"
    if raw.exists():
        rec = json.load(open(raw, encoding="utf-8"))
        if rec["error"] or not rec["raw"].strip():
            mg = H.merge(eng, None, "python_only_after_reviewer_failure")
            parsed = None
        else:
            parsed = H.parse_review(rec["raw"])
            mg = H.merge(eng, parsed, "ok")
        r["hybrid_verified"] = score(affirmed(mg), T, F)
        r["hybrid_verified_plus_disputed"] = score(affirmed(mg, ("verified", "disputed")), T, F)
        r["hybrid_verified_without_distant"] = score(affirmed(mg, exclude_distant=True), T, F)
        r["hybrid_candidates_only"] = score(affirmed(mg, ("candidate",)), T, F)
        r["hybrid"] = {"requests": 1, "llm_seconds": rec["llm_seconds"], "python_seconds": eng["seconds"], "merge_seconds": mg["seconds"], "reviewer_error": rec["error"], "parse_issues": (parsed or {}).get("issues"),
                       "counts": {k: sum(1 for i in mg["items"] if i["status"] == k) for k in ("verified", "candidate", "disputed", "rejected")},
                       "coverage": mg["coverage"], "blocked": mg["blocked"], "disagreements": mg["disagreements"],
                       "llm_added": [{"id": i["id"], "status": i["status"], "a": f"L{i['a']['line']}:{i['a']['word']}", "b": f"L{i['b']['line']}:{i['b']['word']}", "sound": i["sound"], "scope": i.get("scope"), "notes": i["notes"]} for i in mg["items"] if i["source"] == "llm"],
                       "hallucination_blocked": sum(1 for b in mg["blocked"] if b["kind"] == "hallucination_or_bad_position"),
                       "rejected_in_verified": 0}
        ev = H.to_evidence(eng, mg)
        r["hybrid"]["evidence_text"] = H.evidence_text_v2(ev)
        r["hybrid"]["rejected_listed_as_pair"] = sum(1 for sec in ev["sections"] for p in sec["pairs"] if p["status"] == "rejected")
        (HERE / "results").mkdir(exist_ok=True)
        json.dump({"merged": mg, "evidence": ev}, open(HERE / "results" / f"merged_{sid}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    for rnd in (1, 2):
        try:
            o = old_analyzer(sid, rnd)
            r[f"old_analyzer_r{rnd}_given_to_songwriter"] = {**score(o["given_to_songwriter"], T, F), "requests": o["requests"], "llm_seconds": o["seconds"]}
            r[f"old_analyzer_r{rnd}_after_python_check"] = score(o["kept_after_python"], T, F)
        except Exception as ex:
            r[f"old_analyzer_r{rnd}"] = f"{type(ex).__name__}: {ex}"
    r["python_seconds"] = eng["seconds"]
    report[sid] = r
    print("=" * 10, sid)
    for k, v in r.items():
        if isinstance(v, dict) and "TP" in v:
            print(f"  {k:46s} TP={v['TP']}/{v['reference_true']} FN={v['FN']} FP={v['FP_vs_reference']}/{v['reference_false']} not-evaluated={len(v['not_evaluated_claims'])}")
json.dump(report, open(HERE / "results" / "score.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
