# -*- coding: utf-8 -*-
"""Song Critic quality audit 01: the PRODUCTION pipeline of one Song Critic report (spec_reports.evidence_for -> specialists.effective_system -> spec_reports.build_user -> one request at
temperature 0.4 -> spec_reports.parse_answer), run once per song and backend. The same retry the app makes (only when the answer has no [DONE]) is recorded as a request of its own.
No prompt, parser, setting or AppData is changed. Usage: run_audit.py gemma|grok|all"""
import json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE)); sys.stdout.reconfigure(encoding="utf-8")
from music_studio import llm_chat as LC, report_steps as RPT, spec_reports as SR, specialists as SP
from songs import SONGS

LC.CTX_OVERRIDE["local"] = 19000                       # the user's own Settings value (ctx_local) in the real config
WF = ROOT / "comfy_apis" / "llm" / "gemma4_llm.json"
HOST = "127.0.0.1:8188"
which = sys.argv[1] if len(sys.argv) > 1 else "all"
backends = ["gemma", "grok"] if which == "all" else [which]
for d in ("RAW_REQUESTS", "RAW_RESPONSES", "PARSED_REPORTS"):
    (HERE / d).mkdir(exist_ok=True)


def ask(backend, system, user):
    if backend == "grok":
        g = LC.grok_creds()
        return LC.ask_grok(g["key"], g["model"], system, user, temperature=0.4), g["model"]
    return LC.ask_local(HOST, WF, system, user, temperature=0.4), "gemma4_llm.json"


for sid, song in SONGS.items():
    lyrics = song["text"]
    snap = {"title": song["title"], "lyrics": lyrics, "style": song["style"], "backend": backends[0], "lyrics_hash": "audit"}
    ev_text, ev_data = SR.evidence_for("song_critic", lyrics, "Thai", song["settings"], {})
    system, _p = SP.effective_system("song_critic")
    user = SR.build_user("song_critic", snap, ev_text, "Thai")
    for be in backends:
        raw, parsed, attempts = "", None, []
        for attempt in (1, 2):
            u = (RPT.RETRY_INCOMPLETE if attempt == 2 else "") + user
            t0 = time.perf_counter()
            rec = {"song": sid, "backend": be, "attempt": attempt, "temperature": 0.4, "system_prompt": system, "user_message": u, "system_tokens_est": LC.estimate_tokens(system), "user_tokens_est": LC.estimate_tokens(u)}
            try:
                raw, model = ask(be, system, u)
                rec["model"] = model
            except Exception as ex:
                rec["error"] = f"{type(ex).__name__}: {ex}"
                raw = ""
            rec["seconds"] = round(time.perf_counter() - t0, 1)
            (HERE / "RAW_REQUESTS" / f"{sid}_{be}_request{attempt}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            (HERE / "RAW_RESPONSES" / f"{sid}_{be}_response{attempt}.txt").write_text(raw or f"[NO ANSWER] {rec.get('error', '')}", encoding="utf-8")
            attempts.append(rec)
            if not raw:
                break
            parsed = SR.parse_answer(raw, "song_critic", lyrics, system)
            if parsed["ok"] or not parsed.get("incomplete") or attempt == 2:
                break
        out = {"song": sid, "backend": be, "requests": len(attempts), "seconds": [a["seconds"] for a in attempts], "errors": [a.get("error") for a in attempts if a.get("error")], "parsed": parsed}
        (HERE / "PARSED_REPORTS" / f"{sid}_{be}_parsed.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        p = parsed or {}
        print(sid, be, "requests", len(attempts), "sec", out["seconds"], "ok", p.get("ok"), "findings", len((p.get("sr") or {}).get("findings", [])), "diag", len(p.get("diagnostics", [])), out["errors"], flush=True)
print("finished", flush=True)
