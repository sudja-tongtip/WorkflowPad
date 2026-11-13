# -*- coding: utf-8 -*-
"""Runs the REAL Music Studio app in Flet's web mode with a temporary profile (the real AppData is never touched) for the screenshots of Song Critic V3.
Two songs, each with a Song Critic report made from a REAL candidate answer of the V3 runs, parsed by the current parser (no edits to the answers):
  Test Song 04 <- TestSong04_grok_candidate ; Test Song 02 <- TestSong02_gemma_candidate (Gemma wrote `lines: L012-L015 (Chorus)`: unreadable lines, kept and marked).
Usage: SIDE_W=360 python run_web4.py   (PROFILE=<dir> reuses a profile: then nothing is seeded, which shows the reports after a restart)"""
import importlib.util, json, os, sys, tempfile
from pathlib import Path

ROOT = Path("D:/AI/MyApps/WorkflowPad")
HERE = ROOT / "docs" / "song_critic_v3"
reuse = os.environ.get("PROFILE")
tmp = reuse or tempfile.mkdtemp(prefix="wp_v3_")
for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
    os.environ[k] = tmp
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "docs" / "specialist_quality_audit_01")); sys.path.insert(0, str(ROOT / "docs" / "song_critic_v2")); sys.path.insert(0, str(HERE)); os.chdir(ROOT)
import flet as ft
if os.environ.get("SIDE_W"):
    d = Path(tmp) / "WorkflowPad"; d.mkdir(parents=True, exist_ok=True)
    (d / "music_studio.json").write_text(json.dumps({"side_w": float(os.environ["SIDE_W"])}), encoding="utf-8")
from music_studio import store as ST, sheets as SH, spec_reports as SR, specialist_engine as SE, specialists as SP, lyric_analysis as LA
from songs import SONGS
from songs_s4 import S4

if not reuse:
    CAND = (HERE / "CANDIDATE_PROMPT.md").read_text(encoding="utf-8").replace("\r\n", "\n").rstrip()
    for title, song_def, tag, settings in (("Test Song 04", S4, "TestSong04_grok_candidate", S4["settings"]), ("Test Song 02", SONGS["S2"], "TestSong02_gemma_candidate", SONGS["S2"]["settings"])):
        text = song_def["text"]
        song = ST.create_song(title, text)
        SH.ensure_book(song)
        raw = (HERE / "RAW_RESPONSES" / f"{tag}_response.txt").read_text(encoding="utf-8")
        parsed = SR.parse_answer(raw, "song_critic", text, CAND)
        ev = SR.evidence_for("song_critic", text, "Thai", settings, {})[1]
        rep = SR.make_report("song_critic", {"lyrics_hash": LA.text_hash(text), "backend": "local:gemma4_llm.json", "input_fp": ""}, parsed, ev, raw, "gemma4" if "gemma" in tag else "grok")
        rep.data["lyrics"] = text
        so = SE.load_session(song.id, song.book.active_id, "song_critic"); SE.add_report(so, rep); SE.save_session(so)
        print("seeded", title, tag, "aspects", len(parsed["sr"]["aspects"]), "actions", len(parsed["sr"]["actions"]), flush=True)
print("profile", tmp, flush=True)
spec = importlib.util.spec_from_file_location("ms_app", str(ROOT / "music_studio.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
ft.run(M._app_error.guard(ft, M.main, M.APP_NAME, M.APP_VERSION), view=ft.AppView.WEB_BROWSER, port=8571)
