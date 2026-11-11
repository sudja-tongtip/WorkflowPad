# -*- coding: utf-8 -*-
"""Song Critic quality tune 02: A/B of the production prompt (baseline) and ONE candidate prompt, 3 songs x 2 backends x 2 prompts = 12 report runs, one request each, no retry.
Everything else is the production pipeline of one Song Critic report (evidence_for -> build_user -> one request at temperature 0.4 -> parse_answer). Neutral titles. The production prompt,
parser, settings and AppData are not changed; the candidate is injected here as the system text only."""
import json, re, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "docs" / "specialist_quality_audit_01")); sys.stdout.reconfigure(encoding="utf-8")
from music_studio import llm_chat as LC, spec_reports as SR, specialists as SP
from songs import SONGS

LC.CTX_OVERRIDE["local"] = 19000                       # the user's own Settings value (ctx_local), read from the real config, not changed
WF = ROOT / "comfy_apis" / "llm" / "gemma4_llm.json"
HOST = "127.0.0.1:8188"
NEUTRAL = {"S1": "Test Song 01", "S2": "Test Song 02", "S3": "Test Song 03"}
BASELINE = SP.effective_system("song_critic")[0]
CANDIDATE = (HERE / "CANDIDATE_PROMPT.md").read_text(encoding="utf-8").replace("\r\n", "\n").rstrip()
PROMPTS = {"baseline": BASELINE, "candidate": CANDIDATE}
for d in ("RAW_REQUESTS", "RAW_RESPONSES", "PARSED_REPORTS"):
    (HERE / d).mkdir(exist_ok=True)


def ask(be, system, user):
    if be == "grok":
        g = LC.grok_creds()
        return LC.ask_grok(g["key"], g["model"], system, user, temperature=0.4), g["model"]
    return LC.ask_local(HOST, WF, system, user, temperature=0.4), "gemma4_llm.json"


for sid, song in SONGS.items():
    lyrics = song["text"]
    snap = {"title": NEUTRAL[sid], "lyrics": lyrics, "style": song["style"], "backend": "x", "lyrics_hash": "tune02"}
    ev_text, _ev = SR.evidence_for("song_critic", lyrics, "Thai", song["settings"], {})
    user = SR.build_user("song_critic", snap, ev_text, "Thai")             # identical for both prompts and both backends
    for be in ("gemma", "grok"):
        for pname, system in PROMPTS.items():
            tag = f"{NEUTRAL[sid].replace(' ', '')}_{be}_{pname}"
            rec = {"song": NEUTRAL[sid], "backend": be, "prompt": pname, "temperature": 0.4, "system_prompt": system, "user_message": user,
                   "system_tokens_est": LC.estimate_tokens(system), "user_tokens_est": LC.estimate_tokens(user)}
            t0 = time.perf_counter()
            raw = ""
            try:
                raw, model = ask(be, system, user)
                rec["model"] = model
            except Exception as ex:
                rec["error"] = f"{type(ex).__name__}: {ex}"
            rec["seconds"] = round(time.perf_counter() - t0, 1)
            (HERE / "RAW_REQUESTS" / f"{tag}_request.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            (HERE / "RAW_RESPONSES" / f"{tag}_response.txt").write_text(raw or f"[NO ANSWER] {rec.get('error', '')}", encoding="utf-8")
            parsed = SR.parse_answer(raw, "song_critic", lyrics, system) if raw else {"ok": False, "sr": {}, "diagnostics": ["no answer"], "incomplete": False}
            raw_findings = len(re.findall(r"^\[FINDING\.\d+\]", raw, re.M))
            out = {"tag": tag, "raw_findings": raw_findings, "parser_findings": len((parsed.get("sr") or {}).get("findings", [])), "ok": parsed.get("ok"), "diagnostics": parsed.get("diagnostics", []),
                   "seconds": rec["seconds"], "error": rec.get("error"), "parsed": parsed}
            (HERE / "PARSED_REPORTS" / f"{tag}_parsed.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            print(tag, "sec", rec["seconds"], "raw_findings", raw_findings, "parser_findings", out["parser_findings"], "ok", out["ok"], "diag", len(out["diagnostics"]), rec.get("error", ""), flush=True)
print("finished", flush=True)
