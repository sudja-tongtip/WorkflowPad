# -*- coding: utf-8 -*-
"""WHAT-IF (no LLM, no new request): Gemma wrote the endpoints of its [ADD] tags inside the free-text `reason` instead of as `L<n>|word|role` lines, so the merge blocked every ADD ("0 endpoints").
This script extracts `L<n> (word)` mentions from those reasons and feeds them to the SAME merge, to show what the Python validation would have done with the content. It is a simulation of a fixed format, not a result of the prototype."""
import json, re, sys, importlib.util
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
import hybrid as H
spec = importlib.util.spec_from_file_location("songs", ROOT / "docs/analyze_song_audit/tools/songs.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
out = {}
for sid in ("S1", "S2", "S3"):
    rec = json.load(open(HERE / "raw" / f"{sid}.json", encoding="utf-8"))
    eng = H.python_engine(m.SONGS[sid]["text"])
    rv = H.parse_review(rec["raw"])
    fixed = 0
    for a in rv["add"]:
        if a["endpoints"]:
            continue
        seen, eps = set(), []
        for mm in re.finditer(r"L(\d{1,3})\s*\(([^)]+)\)", a["fields"].get("reason", "")):
            k = (int(mm.group(1)), mm.group(2).strip())
            if k not in seen:
                seen.add(k); eps.append({"line": k[0], "word": k[1], "role": "end"})
        if len(eps) >= 2:
            a["endpoints"] = eps[:2]; fixed += 1
    mg = H.merge(eng, rv, "ok")
    rows = [{"id": i["id"], "status": i["status"], "pair": f"L{i['a']['line']}:{i['a']['word']} <-> L{i['b']['line']}:{i['b']['word']}", "note": (i["notes"] or [""])[0][:110]} for i in mg["items"] if i["source"] == "llm"]
    out[sid] = {"adds_in_answer": len(rv["add"]), "adds_with_endpoints_extracted": fixed, "result": rows, "verified_new": sum(1 for r in rows if r["status"] == "verified"), "blocked": [b for b in mg["blocked"] if b["kind"] != "add_bad_endpoints"]}
    print("=====", sid, "ADDs", len(rv["add"]), "extracted", fixed, "-> verified new:", out[sid]["verified_new"])
    for r in rows:
        print("  ", r["id"], r["status"], r["pair"], "|", r["note"])
    for b in out[sid]["blocked"]:
        print("   BLOCKED", b["kind"], b.get("ref"), b.get("why"))
json.dump(out, open(HERE / "results" / "whatif_endpoints.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
