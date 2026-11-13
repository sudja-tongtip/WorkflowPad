# -*- coding: utf-8 -*-
"""Real-Gemma smoke of the four specialist reports on ONE short song (S1): one request per report, the raw answer and the parser result are saved. No audio. Usage: run_smoke.py [spec,spec,...]"""
import importlib.util, json, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.stdout.reconfigure(encoding="utf-8")
from music_studio import llm_chat as LC, spec_reports as SR, specialists as SP

sp = importlib.util.spec_from_file_location("songs", ROOT / "docs/analyze_song_audit/tools/songs.py")
m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
song = m.SONGS["S1"]
LYR = song["text"]
WF = ROOT / "comfy_apis" / "llm" / "gemma4_llm.json"
LC.CTX_OVERRIDE["local"] = 19000
OUT = HERE / "raw"; OUT.mkdir(exist_ok=True)
want = sys.argv[1].split(",") if len(sys.argv) > 1 else list(SR.REPORTS)
snap = {"title": song["title"], "lyrics": LYR, "backend": "local:gemma4_llm.json", "lyrics_hash": "smoke",
        "style": {"genre": song["genre"], "mood": song["mood"], "energy": song["energy"], "vocal": song["vocal"], "language": song["lang"]}}
settings = {"bpm": song["bpm"], "timesignature": "4", "duration": song["duration"]}
for spec in want:
    t0 = time.perf_counter()
    ev_text, ev_data = SR.evidence_for(spec, LYR, "th", settings, {})
    system, _ = SP.effective_system(spec)
    user = SR.build_user(spec, snap, ev_text, "Thai")
    rec = {"spec": spec, "system_chars": len(system), "user_chars": len(user), "system_tokens": LC.estimate_tokens(system), "user_tokens": LC.estimate_tokens(user), "evidence_chars": len(ev_text)}
    try:
        raw = LC.ask_local("127.0.0.1:8188", WF, system, user, timeout_s=420)
        rec["raw"] = raw
        parsed = SR.parse_answer(raw, spec, LYR, system)
        rec["parsed"] = parsed
    except Exception as ex:
        rec["error"] = f"{type(ex).__name__}: {ex}"
    rec["seconds"] = round(time.perf_counter() - t0, 1)
    (OUT / f"{spec}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    p = rec.get("parsed") or {}
    print(spec, "tokens", rec["system_tokens"], rec["user_tokens"], "sec", rec["seconds"], "ok", p.get("ok"), "findings", len((p.get("sr") or {}).get("findings", [])), "diag", len(p.get("diagnostics", [])), rec.get("error", ""), flush=True)
print("finished", flush=True)
