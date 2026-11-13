# -*- coding: utf-8 -*-
"""Offline checks of the Python-rhyme-evidence integration on S1-S3. NO real LLM: LC.ask_local / LC.ask_grok are replaced by a recorder that answers "[DONE]". The UI harness (tests/_ui_harness) uses a temporary
profile, so the real AppData is not touched. Run: python docs/python_rhyme_evidence/test_integration.py [off]   ("off" = the previous behaviour, for the Producer comparison)"""
import asyncio, importlib.util, json, os, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(encoding="utf-8")
MODE_OFF = len(sys.argv) > 1 and sys.argv[1] == "off"
from _ui_harness import App
APP = App()
from music_studio import store as ST, sheets as SH, llm_chat as LC, feature_flags as FF, python_rhyme_evidence as PRE, rhyme_color as RC, rhyme_flow as RF, lyric_analysis as LA
APP.M.I18N.set_lang("th")
if MODE_OFF:
    FF.PYTHON_RHYME_PRIMARY = False
cfg = APP.get("cfg"); cfg.update(ctx_local=19000, host="127.0.0.1:8188", lang="th")
S = APP.get("S")
spec = importlib.util.spec_from_file_location("songs", ROOT / "docs/analyze_song_audit/tools/songs.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


import time as _t
LOG = []
_real = LC.ask_local
def rec(*a, **k):
    system = k.get("system") if "system" in k else (a[2] if len(a) > 2 else "")
    user = a[3] if len(a) > 3 else k.get("user", "")
    t0 = _t.perf_counter()
    try:
        out = _real(*a, **k); err = ""
    except Exception as ex:
        out = ""; err = repr(ex)
    LOG.append({"system": system or "", "user": user or "", "raw": out, "err": err, "sec": round(_t.perf_counter()-t0,1)})
    print("  req", len(LOG), "sec", LOG[-1]["sec"], "err", err or "-", flush=True)
    if err: raise RuntimeError(err)
    return out
LC.ask_local = rec; LC.ask_grok = rec
tasks = []
APP.page.run_task = lambda fn, *a, **k: tasks.append((fn, a))
def drain():
    while tasks:
        fn, a = tasks.pop(0); asyncio.run(fn(*a))
sid = sys.argv[1] if len(sys.argv) > 1 else "S1"
sg = m.SONGS[sid]
song = ST.create_song(sg["title"], sg["text"]); SH.ensure_book(song)
APP.found["open_song"](song.id); drain(); APP.refresh()
song = S.song; st = song.settings
st.genre, st.mood, st.bpm, st.timesignature, st.duration, st.vocal, st.energy = sg["genre"], sg["mood"], sg["bpm"], sg["timesig"], sg["duration"], sg["vocal"], sg["energy"]
st.language = sg["lang"]
APP.set_cell("lyr", lambda: song.lyrics); APP.refresh()
S.backend, S.host = "local:gemma4_llm.json", "127.0.0.1:8188"
sheet = song.book.active_id
snap = APP.found["spec_snapshot"]()
err = ""
try:
    asyncio.run(APP.found["run_report"](song.id, song.lyrics, sheet, snap, None))
except Exception as ex:
    err = repr(ex)
print("DONE", sid, "requests", len(LOG), "err", err or "-", flush=True)
json.dump(LOG, open(HERE / f"gemma_real_{sid}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
