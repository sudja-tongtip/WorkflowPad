"""Opens the REAL music_studio.py in Flet web mode on a throw-away profile with a canned (fake) LLM answer, to check the Director Mode UI in a browser. Nothing of the user's is touched."""
import os, sys, tempfile
PROFILE = tempfile.mkdtemp(prefix="ms_web_dir_")
for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
    os.environ[k] = PROFILE
ROOT = r"D:\AI\MyApps\WorkflowPad"
sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + r"\tests")
import importlib.util
import flet as ft
import test_music_director_v3_2 as T
from music_studio import store as ST, llm_chat as LC, ai_music_director as AD
BARS = "Intro|4\nVerse 1|16\nChorus|16\nVerse 2|16\nChorus|16\nOutro|4"
def fake(*a, **k):
    import time; time.sleep(1.5)
    system = k.get("system") if "system" in k else (a[2] if len(a) > 2 else "")
    if system == AD.system_prompt()[0]:
        import test_ai_recommend_v2 as R
        return R.GOOD
    return T.answer(T.block(1, T.CAP1, BARS), T.block(2, T.CAP2, BARS), T.block(3, T.CAP3, BARS))
LC.ask_local = fake; LC.ask_grok = fake
s = ST.create_song("Chase", T.LYRIC)
s.settings.bpm, s.settings.duration = 120, 150
ST.save_song(s)
spec = importlib.util.spec_from_file_location("ms_app", ROOT + r"\music_studio.py")
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
ft.run(M.main, view=ft.AppView.WEB_BROWSER, port=8573)
