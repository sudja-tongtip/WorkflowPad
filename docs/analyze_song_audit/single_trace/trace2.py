# -*- coding: utf-8 -*-
"""Report Builder stage: render the SAVED reports of the local S2 r1 run with the app's own UI code (harness, no LLM) and list every piece of text the user sees. English UI so hard-coded strings are visible."""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, "D:/AI/MyApps/WorkflowPad"); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad/tests")
sys.stdout.reconfigure(encoding="utf-8")
from _ui_harness import App
APP = App()
ft = APP.ft
from music_studio import store as ST, sheets as SH, specialist_engine as SE, lyric_analysis as LA

OUT = Path(sys.argv[1])
d = json.load(open("D:/AI/MyApps/WorkflowPad/docs/analyze_song_audit/raw_runs/local_S2_r1.json", encoding="utf-8"))
tasks = []
APP.page.run_task = lambda fn, *a, **k: tasks.append((fn, a))
song = ST.create_song(d["song"], d["lyrics"])
SH.ensure_book(song)
APP.found["open_song"](song.id)
while tasks:
    fn, a = tasks.pop(0)
    asyncio.run(fn(*a))
APP.refresh()
S = APP.get("S")
sess = APP.get("sessions")[song.id]
song = S.song
APP.set_cell("lyr", lambda: song.lyrics)
APP.refresh()
for spec, r in d["reports"].items():
    rep = SE.SpecReport.from_dict(r)
    rep.lyrics_hash = LA.text_hash(song.lyrics)
    sess.specreports[spec] = rep
lines = []
for spec in ("songwriter", "music_producer"):
    body = APP.found["_report_body"](sess, spec)
    lines.append(f"######## UI of the {spec} report (English UI; every Text control, closed tiles included) ########")
    for ctl in body:
        for t in APP.texts(ctl, skip_tiles=False):
            if t.strip():
                lines.append(t.strip())
OUT.joinpath("ui_text_songwriter_producer.txt").write_text("\n".join(lines), encoding="utf-8")
print(len(lines), "text lines written")
