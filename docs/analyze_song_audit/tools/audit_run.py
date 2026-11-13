# -*- coding: utf-8 -*-
"""usage: audit_run.py <local|grok> <round> [song ids comma separated]
Runs the REAL Analyze Song of the app (run_report: Rhyme Analyzer -> Songwriter report -> Music Producer report) inside the app's own test harness, with the production prompts (bundled copies are byte-identical
to the AppData ones apart from CRLF), the user's real LLM settings (copied read-only into a temporary profile) and Thai as the reply language. Every request/response to the model is recorded.
Nothing is written to the real AppData or the repo."""
import asyncio, json, os, shutil, sys, time
from pathlib import Path

SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
AUD = SCR / "audit"
OUT = AUD / "out"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(SCR)); sys.path.insert(0, str(AUD)); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad"); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad/tests")
sys.stdout.reconfigure(encoding="utf-8")
REAL = Path(os.environ["APPDATA"])                      # the real profile: read only (copied from)
kind, rnd = sys.argv[1], sys.argv[2]
from songs import SONGS
want = sys.argv[3].split(",") if len(sys.argv) > 3 else list(SONGS)

from _ui_harness import App
APP = App()
TMP = Path(os.environ["APPDATA"])
for src, dst in ((REAL / "WorkflowPad" / "llm_chat_config.json", TMP / "WorkflowPad" / "llm_chat_config.json"), (REAL / "StoryGenerator" / "credentials.json", TMP / "StoryGenerator" / "credentials.json")):
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
from music_studio import store as ST, sheets as SH, llm_chat as LC, specialists as SP, rhyme_steps as RS
APP.M.I18N.set_lang("th")
cfg = APP.get("cfg")
cfg.update(ctx_local=19000, host="127.0.0.1:8188", lang="th")
S = APP.get("S")
backend = "grok" if kind == "grok" else "local:gemma4_llm.json"

# ---- record every request to the model ----
LOG = []
_orig_local, _orig_grok = LC.ask_local, LC.ask_grok


def _wrap(name, fn):
    def w(*a, **k):
        t0 = time.perf_counter()
        system = k.get("system") if "system" in k else (a[2] if name == "local" and len(a) > 2 else (a[2] if len(a) > 2 else ""))
        user = a[3] if len(a) > 3 else k.get("user", "")
        err = ""
        try:
            raw = fn(*a, **k)
        except Exception as ex:
            raw, err = "", f"{type(ex).__name__}: {ex}"
            LOG.append({"fn": name, "system_tokens": LC.estimate_tokens(system or ""), "user_tokens": LC.estimate_tokens(user or ""), "user": user, "raw": raw, "error": err, "sec": round(time.perf_counter() - t0, 1)})
            raise
        LOG.append({"fn": name, "system_tokens": LC.estimate_tokens(system or ""), "user_tokens": LC.estimate_tokens(user or ""), "system_head": (system or "")[:200], "user": user, "raw": raw, "error": err, "sec": round(time.perf_counter() - t0, 1)})
        return raw
    return w


LC.ask_local, LC.ask_grok = _wrap("local", _orig_local), _wrap("grok", _orig_grok)

tasks = []
APP.page.run_task = lambda fn, *a, **k: tasks.append((fn, a))


def drain():
    while tasks:
        fn, a = tasks.pop(0)
        asyncio.run(fn(*a))


def run_song(sid):
    sg = SONGS[sid]
    LOG.clear()
    song = ST.create_song(sg["title"], sg["text"])
    SH.ensure_book(song)
    APP.found["open_song"](song.id)
    drain()
    APP.refresh()
    song = S.song
    st = song.settings
    st.genre, st.mood, st.bpm, st.timesignature, st.duration, st.vocal, st.energy = sg["genre"], sg["mood"], sg["bpm"], sg["timesig"], sg["duration"], sg["vocal"], sg["energy"]
    st.language = sg["lang"]
    APP.set_cell("lyr", lambda: song.lyrics)
    APP.refresh()
    S.backend, S.host = backend, "127.0.0.1:8188"
    sheet = song.book.active_id
    snap = APP.found["spec_snapshot"]()
    t0 = time.perf_counter()
    err = ""
    try:
        asyncio.run(APP.found["run_report"](song.id, song.lyrics, sheet, snap, None))
    except Exception as ex:
        err = f"{type(ex).__name__}: {ex}"
    dt = time.perf_counter() - t0
    sess = APP.get("sessions")[song.id]
    reports = {}
    for spec, r in (sess.specreports or {}).items():
        d = r.to_dict()
        reports[spec] = d
    rh = sess.rhyme or {}
    rhr = rh.get("result")
    rhyme = {"state": ("ok" if rhr is not None else "none"), "pairs": len(getattr(rhr, "pairs", []) or []), "has_llm": bool(getattr(rhr, "llm", None)), "last_error": rh.get("last_error")}
    rec = {"song": sid, "backend": backend, "round": rnd, "seconds": round(dt, 1), "error": err, "requests": len(LOG), "reports": reports, "rhyme": rhyme, "log": list(LOG), "snapshot_keys": sorted(snap.keys()),
           "lyrics": song.lyrics, "settings": {"genre": sg["genre"], "mood": sg["mood"], "bpm": sg["bpm"], "timesig": sg["timesig"], "duration": sg["duration"], "vocal": sg["vocal"], "energy": sg["energy"], "language": sg["lang"]}}
    (OUT / f"{kind}_{sid}_r{rnd}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"[{kind} r{rnd}] {sid} {dt:.0f}s requests={len(LOG)} err={err or '-'} reports={ {k: (v.get('status'), len(v.get('findings') or [])) for k, v in reports.items()} } rhyme={rhyme['state']}", flush=True)


for sid in want:
    done = OUT / f"{kind}_{sid}_r{rnd}.json"
    if done.exists():
        print("skip", sid, flush=True)
        continue
    run_song(sid)
print("finished", flush=True)
