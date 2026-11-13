# -*- coding: utf-8 -*-
"""Python-only rhyme run on S1, S2, S3. NO LLM, NO API, no AppData. Reads songs from docs/analyze_song_audit/tools/songs.py.
Output: docs/python_rhyme_audit/out_<song>.json and a console summary."""
import json, sys, importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
from music_studio import live_insights as LI, thai_rhyme as TR, rhyme_color as RC

spec = importlib.util.spec_from_file_location("songs", ROOT / "docs/analyze_song_audit/tools/songs.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
OUT = Path(__file__).resolve().parent

for sid in ("S1", "S2", "S3"):
    s = m.SONGS[sid]
    text = s["text"]
    lines = text.split("\n")
    rm = LI.rhyme_map(text, None, "th")
    ins = LI.analyze(text, None, 0, "th")
    rec = {"song": sid, "title": s["title"], "links": [], "rejected": len(rm.rejected), "skipped": rm.skipped, "line_ends": {}, "line_end_rime": {}}
    for k in rm.links:
        rec["links"].append({"a": [k.a.file_line, k.a.text, k.a.start], "b": [k.b.file_line, k.b.text, k.b.start], "sound": k.sound, "placement": k.placement, "verification": k.verification, "group": k.group, "kind": k.kind, "colourable": k.colourable, "reason": k.reason})
    for n, raw in enumerate(lines, 1):
        if raw.strip() and not raw.startswith("["):
            last = raw.strip().split()[-1]
            rec["line_end_rime"][n] = {"text": raw, "last_token": last, "last_syllable": TR.final_syllable(last), "rime": TR.describe_rime(last)}
    # syllable counts Python gives for anchors
    anc = {}
    for line_obj in getattr(ins, "lines", []) or []:
        pass
    rc = RC.colorize(text, None, "th")
    rec["groups"] = [[(sp.file_line, sp.text, sp.sound, sp.placement) for sp in g] for g in rc.groups]
    json.dump(rec, open(OUT / f"out_{sid}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("=" * 20, sid, s["title"], "links:", len(rec["links"]), "colour groups:", len(rec["groups"]))
    for l in rec["links"]:
        print(f"  L{l['a'][0]}:{l['a'][1]} <-> L{l['b'][0]}:{l['b'][1]} | {l['sound']} {l['placement']} {l['verification']} grp={l['group']} col={l['colourable']}")
    print("  line ends:")
    for n, d in rec["line_end_rime"].items():
        print(f"    L{n} {d['last_token']} -> {d['last_syllable']} {d['rime']}")
