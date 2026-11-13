# -*- coding: utf-8 -*-
"""Offline replay of the recorded Rhyme Analyzer answers (local S2 r1) through the production parser/validator. NO LLM call."""
import json, sys
from pathlib import Path
sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
from music_studio import lyric_rhyme as LR, lyric_analysis as LA, rhyme_steps as RS, rhyme_v6 as R6, rhyme_flow as RF

d = json.load(open("D:/AI/MyApps/WorkflowPad/docs/analyze_song_audit/raw_runs/local_S2_r1.json", encoding="utf-8"))
L = d["log"]
lyrics = d["lyrics"]
lines = LA.sung_lines(lyrics)
secs = RS.parse_request(L[0]["user"].split("Reply with the single word READY.\n\n", 1)[1])
bodies = [(f"s{i+1}", L[2 + i]["raw"]) for i in range(4)]
doc = RS.assemble(bodies)
Path(sys.argv[1] + "/assembled_rhyme_document.txt").write_text(doc, encoding="utf-8")
calls = []


def replay(system, user):
    calls.append((len(system), len(user)))
    return doc


res = LR.analyze_rhymes(lines, replay, language="th", system=LR.step_system_prompt())
out = {"windows_asked": len(calls), "pairs": [], "rejected": [], "ignored": [], "notes": [], "sections": []}
for p in res.pairs:
    out["pairs"].append({"a": [p.a_line, p.a_word], "b": [p.b_line, p.b_word], "type": p.type, "verify": p.verify, "confidence": p.confidence, "kind": getattr(p, "kind", None), "relation": getattr(p, "relation", None)})
for p in res.rejected:
    out["rejected"].append({"a": [p.a_line, p.a_word], "b": [p.b_line, p.b_word], "verify": p.verify})
out["ignored"] = [str(x)[:200] for x in res.ignored]
llm = getattr(res, "llm", {}) or {}
out["llm_keys"] = list(llm.keys())
out["llm_pairs_n"] = len(llm.get("pairs") or [])
out["llm_notes"] = llm.get("notes")
out["llm_dropped_pairs"] = llm.get("dropped_pairs")
out["llm_endings"] = {k: v for k, v in list((llm.get("endings") or {}).items())[:30]}
out["llm_sounds"] = llm.get("sounds")
json.dump(out, open(sys.argv[1] + "/replay_parsed.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("windows asked", len(calls))
print("PAIRS kept (after Python parse/validation):", len(res.pairs), "| rejected:", len(res.rejected), "| ignored:", len(res.ignored))
for p in out["pairs"]:
    print(" ", p["a"], "<->", p["b"], p["type"], p["verify"], p["confidence"])
print("REJECTED:", out["rejected"])
print("IGNORED:", out["ignored"][:10])
print("llm notes:", out["llm_notes"], "dropped:", out["llm_dropped_pairs"])
