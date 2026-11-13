# -*- coding: utf-8 -*-
"""Runs ONE whole-song Gemma review request per song (S1, S2, S3 = 3 requests in total, no retry) and stores the raw answer.
usage: run_reviewer.py [S1,S2,S3]
Reads the user's LLM settings only through llm_chat (read-only). Writes only to docs/rhyme_hybrid_v2/raw/."""
import json, sys, time, importlib.util
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
import hybrid as H
from music_studio import llm_chat as LC

spec = importlib.util.spec_from_file_location("songs", ROOT / "docs/analyze_song_audit/tools/songs.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
RAW = HERE / "raw"; RAW.mkdir(exist_ok=True)
WF = ROOT / "comfy_apis" / "llm" / "gemma4_llm.json"
LC.CTX_OVERRIDE["local"] = 19000            # the same context size the audit used
want = sys.argv[1].split(",") if len(sys.argv) > 1 else ["S1", "S2", "S3"]
for sid in want:
    out = RAW / f"{sid}.json"
    if out.exists():
        print("skip", sid, flush=True); continue
    lyrics = m.SONGS[sid]["text"]
    eng = H.python_engine(lyrics)
    user = H.build_review_request(eng)
    rec = {"song": sid, "system": H.REVIEW_SYSTEM, "user": user, "system_tokens": LC.estimate_tokens(H.REVIEW_SYSTEM), "user_tokens": LC.estimate_tokens(user), "python_seconds": eng["seconds"], "raw": "", "error": "", "llm_seconds": None}
    t0 = time.perf_counter()
    try:
        rec["raw"] = LC.ask_local("127.0.0.1:8188", WF, H.REVIEW_SYSTEM, user, timeout_s=420)
    except Exception as ex:
        rec["error"] = f"{type(ex).__name__}: {ex}"
    rec["llm_seconds"] = round(time.perf_counter() - t0, 1)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(sid, "tokens", rec["system_tokens"], rec["user_tokens"], "sec", rec["llm_seconds"], "err", rec["error"] or "-", "raw_chars", len(rec["raw"]), flush=True)
print("finished", flush=True)
