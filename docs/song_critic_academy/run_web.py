# -*- coding: utf-8 -*-
"""Runs the REAL Music Studio app in Flet's web mode with a TEMPORARY profile (the real AppData is never touched) for the screenshots of the Songwriting Academy.
Three songs: Test Song 01 (no report: the user presses Analyze and a REAL request is made), Test Song 04 (a critique report from the FIXTURE docs/song_critic_academy/fixtures/critique_mixed_song.txt: a mock answer, not a model's), Test Song 05 (an older-format report from a real production answer). Usage: SIDE_W=360 python run_web.py   (PROFILE=<dir> reuses a profile: nothing is seeded, which shows the files and reports after a restart)"""
import importlib.util, json, os, sys, tempfile
from pathlib import Path

ROOT = Path("D:/AI/MyApps/WorkflowPad")
V3 = ROOT / "docs" / "song_critic_v3"
reuse = os.environ.get("PROFILE")
tmp = reuse or tempfile.mkdtemp(prefix="wp_ac_")
for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
    os.environ[k] = tmp
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "docs" / "specialist_quality_audit_01")); sys.path.insert(0, str(ROOT / "docs" / "song_critic_v2")); sys.path.insert(0, str(V3)); os.chdir(ROOT)
import flet as ft
if os.environ.get("SIDE_W"):
    d = Path(tmp) / "WorkflowPad"; d.mkdir(parents=True, exist_ok=True)
    (d / "music_studio.json").write_text(json.dumps({"side_w": float(os.environ["SIDE_W"])}), encoding="utf-8")
from music_studio import store as ST, sheets as SH, spec_reports as SR, specialist_engine as SE, lyric_analysis as LA
from songs import SONGS
from songs_s4 import S4
from songs_s5 import S5

if not reuse:
    from music_studio import specialists as SP
    FIX = ROOT / "docs" / "song_critic_academy" / "fixtures" / "critique_mixed_song.txt"
    system = SP.load("song_critic", user=Path(tempfile.mkdtemp())).text
    seeds = (("Test Song 01", SONGS["S1"], None), ("Test Song 04", S4, FIX), ("Test Song 05", S5, V3 / "RAW_RESPONSES" / "TestSong05_grok_baseline_response.txt"))
    for title, song_def, src in seeds:
        text = song_def["text"]
        song = ST.create_song(title, text)
        SH.ensure_book(song)
        if src is None:
            print("seeded", title, "(no report)", flush=True); continue
        raw = Path(src).read_text(encoding="utf-8")
        parsed = SR.parse_answer(raw, "song_critic", text, system)
        ev = SR.evidence_for("song_critic", text, "Thai", song_def["settings"], {})[1]
        rep = SR.make_report("song_critic", {"lyrics_hash": LA.text_hash(text), "backend": "local:gemma4_llm.json", "input_fp": ""}, parsed, ev, raw, "fixture")
        rep.data["lyrics"] = text
        so = SE.load_session(song.id, song.book.active_id, "song_critic"); SE.add_report(so, rep); SE.save_session(so)
        print("seeded", title, Path(src).name, "format", parsed["sr"].get("format", "legacy"), flush=True)
print("profile", tmp, flush=True)
spec = importlib.util.spec_from_file_location("ms_app", str(ROOT / "music_studio.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
ft.run(M._app_error.guard(ft, M.main, M.APP_NAME, M.APP_VERSION), view=ft.AppView.WEB_BROWSER, port=8571)
