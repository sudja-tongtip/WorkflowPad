# -*- coding: utf-8 -*-
"""Song Critic Free-Thinking Two-Pass Prototype V2: A (one-shot, production prompt and pipeline) vs B (short system prompt, Pass 1 free critique then Pass 2 independent reconsideration in the SAME session), 2 songs x 2 backends.
4 one-shot + 8 pass requests = 12, temperature 0.4, NO retry, no tuning. Production code/prompts/parser/UI/settings/AppData are not changed. The raw output goes to a temporary directory (OUT) while the run
is going; the finished files are copied into the deliverable folder afterwards. The helper's per-call line in AppData `ai_calls.log` is switched off in this process only (a no-op in memory, no file changed).
Gemma: a real continuing session (ComfyUI LLMSessionChatNode, one session id per song). Grok: stateless API, the full earlier messages are replayed (Pass 1 user message + Pass 1 answer) with Pass 2.
The song is sent ONCE (Pass 1); Pass 2 is a short message that refers to what is already in the conversation (lyrics + the Pass 1 answer). The system prompt is sent once per request as the API needs, and the node of Gemma keeps the session.
usage: OUT=<dir> python run_fr.py"""
import json, os, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = Path(os.environ["OUT"])
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "docs" / "specialist_quality_audit_01")); sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from music_studio import llm_chat as LC, spec_reports as SR, specialists as SP
from songs import SONGS
import prompts_fr as P

LC._log_call = lambda *a, **k: None                      # no line in AppData ai_calls.log from this process
LC.CTX_OVERRIDE["local"] = 19000                         # the user's own Settings value (ctx_local), read earlier from the real config, not changed
N_CTX = 19000
WF = ROOT / "comfy_apis" / "llm" / "gemma4_llm.json"
HOST = "127.0.0.1:8188"
TEMP = 0.4
SONG_SET = {"Test Song 01": SONGS["S1"], "Test Song 03": SONGS["S3"]}
PROD_SYSTEM = SP.effective_system("song_critic")[0]
for d in ("RAW_REQUESTS", "RAW_RESPONSES"):
    (OUT / d).mkdir(parents=True, exist_ok=True)
TRACE = OUT / "SESSION_TRACE.jsonl"


def grok_call(system, messages_after_system):
    """The same request as llm_chat.ask_grok (same URL, body, temperature, reasoning_effort, max_tokens) but it also returns the usage and the finish reason."""
    g = LC.grok_creds()
    body = {"model": g["model"], "messages": [{"role": "system", "content": system}, *messages_after_system], "max_tokens": 4096, "temperature": TEMP, "reasoning_effort": "low"}
    req = urllib.request.Request("https://api.x.ai/v1/chat/completions", data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json", "Authorization": f"Bearer {g['key']}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    ch = resp["choices"][0]
    content = (ch["message"].get("content") or "").strip()
    if not content:
        raise RuntimeError("Grok returned an empty answer")
    return content, resp.get("usage") or {}, g["model"], body, ch.get("finish_reason")


def trace(rec):
    with open(TRACE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def song_inputs(name, song):
    lyrics = song["text"]
    snap = {"title": name, "lyrics": lyrics, "style": song["style"], "backend": "x", "lyrics_hash": "tp"}
    ev_text, _ = SR.evidence_for("song_critic", lyrics, "Thai", song["settings"], {})
    user = SR.build_user("song_critic", snap, ev_text, "Thai")          # the production user message (arm A)
    block = user.split("\nREQUEST:")[0].rstrip() + "\nREPLY LANGUAGE: Thai"      # the same song data for arm B (title, style, numbered lyrics, structure, returning lines)
    return user, block


def save(tag, rec, raw):
    (OUT / "RAW_REQUESTS" / f"{tag}_request.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "RAW_RESPONSES" / f"{tag}_response.txt").write_text(raw or f"[NO ANSWER] {rec.get('error', '')}", encoding="utf-8")
    trace({k: rec[k] for k in ("request_no", "arm", "pass", "song", "backend", "seconds", "usage_reported", "finish_reason", "system_tokens_est", "user_tokens_est", "output_tokens_est", "history", "session_id", "session_used_est_after", "session_log", "error") if k in rec})
    print(tag, rec.get("seconds"), "s", (rec.get("usage_reported") or {}).get("total_tokens", ""), rec.get("finish_reason", ""), rec.get("error", ""), flush=True)


def run_oneshot(be, name, song, no):
    user, _ = song_inputs(name, song)
    tag = f"{name.replace(' ', '')}_{be}_oneshot"
    rec = {"request_no": no, "arm": "A one-shot", "pass": "-", "song": name, "backend": be, "temperature": TEMP, "system_prompt": PROD_SYSTEM, "user_message": user, "history": "none (a single request)",
           "system_tokens_est": LC.estimate_tokens(PROD_SYSTEM), "user_tokens_est": LC.estimate_tokens(user), "session_id": "none"}
    t0 = time.perf_counter(); raw = ""
    try:
        if be == "grok":
            raw, rec["usage_reported"], rec["model"], _b, rec["finish_reason"] = grok_call(PROD_SYSTEM, [{"role": "user", "content": user}])
        else:
            raw = LC.ask_local(HOST, WF, PROD_SYSTEM, user, temperature=TEMP); rec["model"] = "gemma4_llm.json"
    except Exception as ex:
        rec["error"] = f"{type(ex).__name__}: {ex}"
    rec["seconds"] = round(time.perf_counter() - t0, 1); rec["output_tokens_est"] = LC.estimate_tokens(raw)
    save(tag, rec, raw)


def run_twopass(be, name, song, no0):
    _, block = song_inputs(name, song)
    base = f"{name.replace(' ', '')}_{be}_twopass"
    sess = LC.LLMSession(N_CTX, goal="") if be == "gemma" else None
    history = []
    for p in (1, 2):
        user = P.pass1_user(block) if p == 1 else P.pass2_user()
        rec = {"request_no": no0 + p - 1, "arm": "B two-pass", "pass": p, "song": name, "backend": be, "temperature": TEMP, "system_prompt": P.SYSTEM_SHORT, "user_message": user,
               "system_tokens_est": LC.estimate_tokens(P.SYSTEM_SHORT), "user_tokens_est": LC.estimate_tokens(user)}
        if be == "gemma":
            rec["history"] = "server-side session (ComfyUI LLMSessionChatNode): the node keeps the lyrics (Pass 1) and the Pass 1 answer; max_turns 200, no summarizing"
        else:
            rec["history"] = f"full replay of {len(history)} earlier messages (the Pass 1 user message and the Pass 1 answer)" if history else "none (first request of the session)"
        t0 = time.perf_counter(); raw = ""
        try:
            if be == "grok":
                raw, rec["usage_reported"], rec["model"], b, rec["finish_reason"] = grok_call(P.SYSTEM_SHORT, [*history, {"role": "user", "content": user}])
                rec["messages_sent"] = b["messages"]; rec["session_id"] = "none (stateless API; history replayed by the client)"
            else:
                raw = LC.ask_local(HOST, WF, P.SYSTEM_SHORT, user, temperature=TEMP, session=sess); rec["model"] = "gemma4_llm.json"
                rec["session_id"] = sess.id; rec["session_log"] = list(sess.log); rec["session_used_est_after"] = sess.used
        except Exception as ex:
            rec["error"] = f"{type(ex).__name__}: {ex}"
        rec["seconds"] = round(time.perf_counter() - t0, 1); rec["output_tokens_est"] = LC.estimate_tokens(raw)
        save(f"{base}_pass{p}", rec, raw)
        if rec.get("error") or not raw:
            break
        history += [{"role": "user", "content": user}, {"role": "assistant", "content": raw}]


if __name__ == "__main__":
    no = 1
    for be in ("gemma", "grok"):
        for name, song in SONG_SET.items():
            run_oneshot(be, name, song, no); no += 1
    for be in ("gemma", "grok"):
        for name, song in SONG_SET.items():
            run_twopass(be, name, song, no); no += 2
    print("finished", flush=True)
