# -*- coding: utf-8 -*-
"""Before/after measurement of the Python rhyme map (no LLM): links per song and time. usage: measure.py <tag>"""
import importlib.util, json, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.stdout.reconfigure(encoding="utf-8")
from music_studio import live_insights as LI, rhyme_map as RM
sp = importlib.util.spec_from_file_location("songs", ROOT / "docs/analyze_song_audit/tools/songs.py"); m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
SCREEN = (HERE / "screenshot_song.txt").read_text(encoding="utf-8")
songs = {"SHOT": SCREEN, "S1": m.SONGS["S1"]["text"], "S2": m.SONGS["S2"]["text"], "S3": m.SONGS["S3"]["text"], "LONG": "\n\n".join([SCREEN] * 6)}
out = {"version": RM.ANALYZER_VERSION, "songs": {}}
for sid, text in songs.items():
    LI._map_cache.clear(); LI._core_cache.clear()
    t0 = time.perf_counter(); mp = LI.rhyme_map(text, None, "th"); cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(5):
        LI._map_cache.clear(); LI.rhyme_map(text, None, "th")
    warm = (time.perf_counter() - t0) / 5            # tokens cached, links rebuilt: what a keystroke costs
    rows = [(k.placement, k.sound, k.a.text, k.a.file_line, k.a.start, k.a.end, k.b.text, k.b.file_line, k.b.start, k.b.end, k.group) for k in mp.links]
    cnt = {}
    for r in rows: cnt[(r[0], r[1])] = cnt.get((r[0], r[1]), 0) + 1
    out["songs"][sid] = {"lines": text.count("\n") + 1, "links": len(rows), "by_kind": {f"{a}/{b}": n for (a, b), n in sorted(cnt.items())}, "cold_s": round(cold, 3), "warm_s": round(warm, 4), "rows": rows}
    print(sid, "lines", out["songs"][sid]["lines"], "links", len(rows), out["songs"][sid]["by_kind"], "cold", round(cold, 3), "warm", round(warm, 4))
(HERE / f"measure_{sys.argv[1]}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
