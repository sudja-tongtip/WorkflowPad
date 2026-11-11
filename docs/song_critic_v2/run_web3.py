# -*- coding: utf-8 -*-
"""Runs the REAL Music Studio app in Flet's web mode with a TEMPORARY profile (the real AppData is never touched) for the screenshots of Song Critic V2.
One song (Test Song 04) with a Song Critic report made from a REAL Gemma answer of the Task B runs (parsed by the current parser). One finding of it gets a SYNTHETIC proposal wording, so that the
Original / Proposed / Preview / Apply card can be shown (no real model wrote a proposal: the prompt never asks for one). Usage: SIDE_W=360 python run_web3.py"""
import importlib.util, json, os, sys, tempfile
from pathlib import Path

ROOT = Path("D:/AI/MyApps/WorkflowPad")
HERE = ROOT / "docs" / "song_critic_v2"
tmp = tempfile.mkdtemp(prefix="wp_v2_")
for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
    os.environ[k] = tmp
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE)); os.chdir(ROOT)
import flet as ft
if os.environ.get("SIDE_W"):
    d = Path(tmp) / "WorkflowPad"; d.mkdir(parents=True, exist_ok=True)
    (d / "music_studio.json").write_text(json.dumps({"side_w": float(os.environ["SIDE_W"])}), encoding="utf-8")
from music_studio import store as ST, sheets as SH, spec_reports as SR, specialist_engine as SE, specialists as SP, lyric_analysis as LA
from songs_s4 import S4

text = S4["text"]
song = ST.create_song("Test Song 04", text)
SH.ensure_book(song)
tag = os.environ.get("RAW", "TestSong04_gemma_baseline")
raw = (HERE / "RAW_RESPONSES" / f"{tag}_response.txt").read_text(encoding="utf-8")
DEMO_INVALID = os.environ.get("DEMO_INVALID", "1") == "1"
if DEMO_INVALID:                                                          # DEMONSTRATION ONLY: one finding of the real answer gets unreadable lines, to show how such a finding is kept and marked
    raw = raw.replace("lines: L014, L031", "lines: verse 2", 1)
system = SP.effective_system("song_critic")[0]
parsed = SR.parse_answer(raw, "song_critic", text, system)
for f in parsed["sr"]["findings"]:                                       # a SYNTHETIC proposal on the first finding with exactly one reference (for the Preview / Apply card only)
    if len(f.get("refs") or []) == 1:
        a, b = f["refs"][0]
        f["proposal"] = chr(10).join(f"บรรทัดตัวอย่างที่ {i + 1} ของข้อเสนอ" for i in range(b - a + 1))
        break
ev = SR.evidence_for("song_critic", text, "Thai", S4["settings"], {})[1]
OLD = ("[Intro]" + chr(10) + text) if os.environ.get("OUTDATED") == "1" else text            # OUTDATED=1: the report was made from lyrics that have one more line at the start (the song has changed since)
snap = {"lyrics_hash": LA.text_hash(OLD), "backend": "local:gemma4_llm.json", "input_fp": ""}
rep = SR.make_report("song_critic", snap, parsed, ev, raw, "gemma4")
rep.data["lyrics"] = OLD
so = SE.load_session(song.id, song.book.active_id, "song_critic"); SE.add_report(so, rep); SE.save_session(so)
print("profile", tmp, "song", song.id, "findings", len(parsed["sr"]["findings"]), flush=True)
spec = importlib.util.spec_from_file_location("ms_app", str(ROOT / "music_studio.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
ft.run(M._app_error.guard(ft, M.main, M.APP_NAME, M.APP_VERSION), view=ft.AppView.WEB_BROWSER, port=8571)
