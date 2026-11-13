# -*- coding: utf-8 -*-
"""Offline checks of the Python-rhyme-evidence integration on S1-S3. NO real LLM: LC.ask_local / LC.ask_grok are replaced by a recorder that answers "[DONE]". The UI harness (tests/_ui_harness) uses a temporary
profile, so the real AppData is not touched. Run: python docs/analyze_song_fix/test_integration.py [off]   ("off" = the previous behaviour, for the Producer comparison)"""
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



RAWLOG = json.load(open(HERE / "raw_log_S1.json", encoding="utf-8"))
from music_studio import report_steps as RPT, specialist_engine as SE
LOG = []; STEPPERS = []; IDX = [0]
def replay(*a, **k):
    system = k.get("system") if "system" in k else (a[2] if len(a) > 2 else "")
    user = a[3] if len(a) > 3 else k.get("user", "")
    i = IDX[0]; IDX[0] += 1
    raw = RAWLOG[i]["raw"] if i < len(RAWLOG) else "[DONE]"
    LOG.append({"i": i + 1, "system": system or "", "user": user or "", "raw": raw})
    return raw
LC.ask_local = replay; LC.ask_grok = replay
_run = RPT.ReportStepper.run
def _wrapped(self):
    STEPPERS.append(self); return _run(self)
RPT.ReportStepper.run = _wrapped
tasks = []
APP.page.run_task = lambda fn, *a, **k: tasks.append((fn, a))
def drain():
    while tasks:
        fn, a = tasks.pop(0); asyncio.run(fn(*a))
sid = "S1"; sg = m.SONGS[sid]
song = ST.create_song(sg["title"], sg["text"]); SH.ensure_book(song)
APP.found["open_song"](song.id); drain(); APP.refresh()
song = S.song; st = song.settings
st.genre, st.mood, st.bpm, st.timesignature, st.duration, st.vocal, st.energy = sg["genre"], sg["mood"], sg["bpm"], sg["timesig"], sg["duration"], sg["vocal"], sg["energy"]
st.language = sg["lang"]
APP.set_cell("lyr", lambda: song.lyrics); APP.refresh()
S.backend, S.host = "local:gemma4_llm.json", "127.0.0.1:8188"
sheet = song.book.active_id
snap = APP.found["spec_snapshot"]()
asyncio.run(APP.found["run_report"](song.id, song.lyrics, sheet, snap, None))
sess = APP.get("sessions")[song.id]
out = {"requests_replayed": IDX[0], "reports": {}}
for spec, r in sess.specreports.items():
    d = {"status": r.status, "error": r.error, "summary": r.summary, "strengths": r.strengths, "uncertain": r.uncertain, "diagnostics": list(r.diagnostics),
         "findings": [{"id": f.id, "kind": f.kind, "priority": f.priority, "first": f.first, "last": f.last, "finding": f.finding[:80], "tradeoff": f.tradeoff, "suggestion": f.suggestion[:60],
                       "op": getattr(f, "op", None)} for f in r.findings],
         "rhyme_flow": [{k: c.get(k) for k in ("name", "first", "last", "keep", "recommendation", "priority", "raw_priority", "rhyme", "limits")} for c in (r.data or {}).get("rhyme_flow") or []]}
    out["reports"][spec] = d
out["steppers"] = [{"spec": s.spec, "requests": s.requests, "stray": s.stray, "problems": s.problems, "incomplete": len(s.incomplete), "diagnostics": list(getattr(s, "diagnostics", []))} for s in STEPPERS]
tag = sys.argv[1] if len(sys.argv) > 1 else "before"
json.dump(out, open(HERE / f"replay_{tag}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("replayed", IDX[0], "of", len(RAWLOG), [ (s, len(d["findings"]), len(d["diagnostics"])) for s, d in out["reports"].items()])
