# -*- coding: utf-8 -*-
"""Phase 1 real-model run of the Arrangement V3.2 mode: ONE request per backend, no retry. Reads the existing song from the real store (read-only) and writes only into docs/music_director_activation/live/.
Run: python docs\\music_director_activation\\live_phase1.py grok|gemma"""
import json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
from music_studio import ai_music_director as AD, arrangement_plan as AP, llm_chat as LC, sheets as SH, store as ST

which = sys.argv[1]
SONG_ID = "song_351733cf3298"
song = next(s["song"] for s in ST.list_songs() if s["song"].id == SONG_ID)
SH.ensure_book(song)
st = SH.settings_to_dict(song.settings)
st["duration"] = 180
snap = AD.make_snapshot(song.id, song.book.active_id, "A", song.title, song.lyrics, st, instructions="Electronic ดุดัน สนุก ๆ ดนตรีเหมือนดูหนัง Action", reference=False, singer="male")
calls = []


def ask(system, user):
    calls.append(time.time())
    if which == "grok":
        g = LC.grok_creds()
        return LC.ask_grok(g["key"], g["model"], system, user)
    return LC.ask_local("127.0.0.1:8188", ROOT / "comfy_apis/llm/gemma4_llm.json", system, user)


out = ROOT / "docs/music_director_activation/live"
out.mkdir(parents=True, exist_ok=True)
t0 = time.time()
rep = {"backend": which, "song": song.title, "prompt_hash": AD.candidate_system()[1]}
try:
    prop = AD.analyze(ask, snap, "Thai", variant="candidate")
    rep["seconds"] = round(time.time() - t0, 1)
    raw = prop.raw
    (out / f"{which}_raw.txt").write_text(raw, encoding="utf-8")
    rep.update(chars=len(raw), done="[DONE]" in raw, requests=len(calls), directions=[])
    for d in prop.directions:
        p = d.plan
        rep["directions"].append({"name": d.name, "genre": (AD.suggested(prop, d) or {}).get("genre"), "plan": None if p is None else {
            "complete": p.complete, "duration_ok": p.duration_ok, "bars": p.clock.bars if p.clock else None, "clock": p.clock.clock if p.clock else None,
            "rows": [(r.name, r.bars, r.role, r.energy, r.proposed) for r in p.rows], "proposed": p.proposed, "missing": p.missing, "rejected": list(p.rejected), "cues": [(c.section, c.action, c.phrase, c.status) for c in p.cues]},
            "reasoning": d.duration_reasoning[:300]})
except AD.DirectorError as ex:
    rep.update(seconds=round(time.time() - t0, 1), error=str(ex), problems=ex.problems, requests=len(calls))
    (out / f"{which}_raw.txt").write_text(ex.raw or "", encoding="utf-8")
(out / f"{which}_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(rep, ensure_ascii=False, indent=1)[:6000])
