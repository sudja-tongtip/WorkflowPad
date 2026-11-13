# -*- coding: utf-8 -*-
"""Python-only checks (no LLM): (1) is an old report marked outdated after the lyrics change, and is a suggestion still safe to apply; (2) does the report path ever write lyrics (static check of the code range).
Uses a recorded real report from the audit runs."""
import asyncio, json, re, sys
from pathlib import Path
SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
AUD = SCR / "audit"
sys.path.insert(0, str(AUD)); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad"); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad/tests")
sys.stdout.reconfigure(encoding="utf-8")
from songs import SONGS
from _ui_harness import App
APP = App()
from music_studio import store as ST, sheets as SH, specialist_engine as SE, lyric_analysis as LA

res = {}
tasks = []
APP.page.run_task = lambda fn, *a, **k: tasks.append((fn, a))
src_run = json.loads((AUD / "out" / "local_S3_r1.json").read_text(encoding="utf-8"))
song = ST.create_song("stale", SONGS["S3"]["text"])
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
rep = SE.SpecReport.from_dict(src_run["reports"]["songwriter"])
rep.lyrics_hash = LA.text_hash(song.lyrics)
rep.backend = "local:gemma4_llm.json"
sess.specreports["songwriter"] = rep
rep_state = APP.found["rep_state"]
res["same_lyrics"] = rep_state(sess, "songwriter")[0]
orig = song.lyrics
song.lyrics = orig.replace("ลูกกุญแจดอกเล็ก", "ลูกกุญแจดอกใหญ่")           # an edit in one line
res["after_edit_of_one_line"] = rep_state(sess, "songwriter")[0]
body = APP.found["_report_body"](sess, "songwriter")
texts = " | ".join(" ".join(APP.texts(ft_col)) for ft_col in body) if body else ""
res["warning_shown"] = "The lyrics changed after this report" in texts or "เนื้อเพลงเปลี่ยน" in texts
song.lyrics = orig
res["after_undo"] = rep_state(sess, "songwriter")[0]
# suggestions: can each still be located / applied after (a) an unrelated edit, (b) an edit of its own line
sug = [f for f in rep.findings if (f.suggestion or "").strip()]
res["suggestions"] = len(sug)
if sug:
    f = sug[0]
    item = APP.found["_fd_item"](f) if "_fd_item" in APP.found else None
    out = []
    for label, new_lyr in (("unchanged", orig), ("another line edited", orig.replace("ลูกกุญแจดอกเล็ก", "ลูกกุญแจดอกใหญ่")), ("a line was inserted above", "[Intro]\nเสียงเปียโน\n\n" + orig), ("its own line edited", orig.replace(f.evidence.strip()[:6], "XXXXXX") if f.evidence else orig)):
        loc = SE.locate_text(item, new_lyr) if item is not None else "n/a"
        out.append((label, "found" if isinstance(loc, tuple) else str(loc)[:90]))
    res["suggestion_locate"] = out
# static check: the report path never writes lyrics
code = Path("D:/AI/MyApps/WorkflowPad/music_studio.py").read_text(encoding="utf-8").split("\n")
a = next(i for i, l in enumerate(code) if l.strip().startswith("async def run_spec_report"))
b = next(i for i, l in enumerate(code) if l.strip().startswith("def open_alternatives"))
seg = "\n".join(code[a:b])
res["report_path_calls_that_write_lyrics"] = re.findall(r"set_lyrics|apply_edit|apply_change|push_undo|song\.lyrics\s*=(?!=)", seg)
print(json.dumps(res, ensure_ascii=False, indent=1))
(AUD / "stale_test.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
