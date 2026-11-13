# -*- coding: utf-8 -*-
"""Two REAL requests with the new production Song Critic prompt (one Gemma, one Grok) on Test Song 01 (a song that is pretty and does not develop), through the same pipeline as the app
(evidence_for -> build_user -> one request at temperature 0.4 -> parse_answer). Raw request/response and the parsed result are saved. No retry. Production files are not changed."""
import json, sys, time, urllib.request
from pathlib import Path
HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "docs" / "specialist_quality_audit_01"))
sys.stdout.reconfigure(encoding="utf-8")
from music_studio import llm_chat as LC, spec_reports as SR, specialists as SP
from songs import SONGS
LC._log_call = lambda *a, **k: None
LC.CTX_OVERRIDE["local"] = 19000
song = SONGS["S1"]; name = "Test Song 01"; lyrics = song["text"]
import tempfile
system = SP.load("song_critic", user=Path(tempfile.mkdtemp())).text          # the bundled (new) prompt: the user copy in AppData is upgraded by the app at its next start
snap = {"title": name, "lyrics": lyrics, "style": song["style"], "backend": "x", "lyrics_hash": "real"}
ev_text, _ev = SR.evidence_for("song_critic", lyrics, "Thai", song["settings"], {})
user = SR.build_user("song_critic", snap, ev_text, "Thai")
out = HERE / "REAL_RUNS"
for be in ("gemma", "grok"):
    t0 = time.perf_counter(); err = ""; raw = ""; usage = {}
    try:
        if be == "gemma":
            raw = LC.ask_local("127.0.0.1:8188", ROOT / "comfy_apis" / "llm" / "gemma4_llm.json", system, user, temperature=0.4)
        else:
            g = LC.grok_creds()
            body = {"model": g["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": 4096, "temperature": 0.4, "reasoning_effort": "low"}
            req = urllib.request.Request("https://api.x.ai/v1/chat/completions", data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json", "Authorization": f"Bearer {g['key']}"})
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read())
            raw = (resp["choices"][0]["message"].get("content") or "").strip(); usage = resp.get("usage") or {}
    except Exception as ex:
        err = f"{type(ex).__name__}: {ex}"
    sec = round(time.perf_counter() - t0, 1)
    parsed = SR.parse_answer(raw, "song_critic", lyrics, system) if raw else {"ok": False, "sr": {}, "diagnostics": ["no answer"]}
    (out / f"S1_{be}_request.json").write_text(json.dumps({"system_prompt": system, "user_message": user, "seconds": sec, "usage": usage, "error": err}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"S1_{be}_response.txt").write_text(raw or f"[NO ANSWER] {err}", encoding="utf-8")
    sr = parsed.get("sr") or {}
    (out / f"S1_{be}_parsed.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8")
    print(be, sec, "s", "ok", parsed.get("ok"), "format", sr.get("format"), "aspects", len(sr.get("aspects") or []), "proposals", sum(1 for a in sr.get("aspects") or [] if a.get("proposal")),
          "quote_bad", sum(1 for a in sr.get("aspects") or [] if a.get("quote_ok") is False), "diag", len(parsed.get("diagnostics") or []), err, flush=True)
    for a in sr.get("aspects") or []:
        print("  ", a["aspect"], a["status"], a["priority"], SR.status_group(a["status"], a["priority"]), "|", (a.get("summary") or "")[:90])
