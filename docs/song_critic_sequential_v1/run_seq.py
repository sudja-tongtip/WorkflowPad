# -*- coding: utf-8 -*-
"""Song Critic Sequential Prototype V1: A (one-shot, production prompt and pipeline) vs B (short system prompt, nine steps in one session), 2 songs x 2 backends.
4 one-shot requests + 36 step requests, temperature 0.4, NO retry, no tuning. Production code, prompts, parser, settings and AppData are not changed; this script only calls the LLM helpers.
Gemma: a real continuing session (the ComfyUI LLMSessionChatNode keeps the history server-side by session id; LLMSession of llm_chat is used as production does).
Grok: the API has no session; the FULL earlier messages are sent again with every step ("history replay"), which is the same conversation the model sees. Nothing is summarized.
A safe stop: if the next Gemma step would pass the session's context guard (65 % of ctx_local, production's own ratio), the sequence stops and says so; it never opens a second session silently.
usage: python run_seq.py [--dry] [--only gemma|grok] [--oneshot-only]"""
import json, re, sys, time, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "docs" / "specialist_quality_audit_01")); sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from music_studio import llm_chat as LC, spec_reports as SR, specialists as SP
from songs import SONGS
import steps as ST

DRY = "--dry" in sys.argv
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else ""
ONESHOT_ONLY = "--oneshot-only" in sys.argv
LC.CTX_OVERRIDE["local"] = 19000                       # the user's own Settings value (ctx_local), read earlier from the real config, not changed
N_CTX = 19000
WF = ROOT / "comfy_apis" / "llm" / "gemma4_llm.json"
HOST = "127.0.0.1:8188"
TEMP = 0.4
SONG_SET = {"Test Song 01": SONGS["S1"], "Test Song 03": SONGS["S3"]}
PROD_SYSTEM = SP.effective_system("song_critic")[0]
for d in ("RAW_REQUESTS", "RAW_RESPONSES", "PARSED_STEPS", "FINAL_REPORTS"):
    (HERE / d).mkdir(exist_ok=True)
TRACE = HERE / "SESSION_TRACE.jsonl"


def grok_call(system, messages_after_system):
    """The same request as llm_chat.ask_grok (same URL, body, temperature, reasoning_effort, max_tokens) but it also returns the usage the API reports."""
    g = LC.grok_creds()
    body = {"model": g["model"], "messages": [{"role": "system", "content": system}, *messages_after_system], "max_tokens": 4096, "temperature": TEMP, "reasoning_effort": "low"}
    req = urllib.request.Request("https://api.x.ai/v1/chat/completions", data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json", "Authorization": f"Bearer {g['key']}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    content = (resp["choices"][0]["message"].get("content") or "").strip()
    if not content:
        raise RuntimeError("Grok returned an empty answer")
    return content, resp.get("usage") or {}, g["model"], body


def trace(rec):
    with open(TRACE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def song_inputs(name, song):
    lyrics = song["text"]
    snap = {"title": name, "lyrics": lyrics, "style": song["style"], "backend": "x", "lyrics_hash": "seq"}
    ev_text, _ = SR.evidence_for("song_critic", lyrics, "Thai", song["settings"], {})
    user = SR.build_user("song_critic", snap, ev_text, "Thai")      # the production user message (also what arm A sends)
    block = user.split("\nREQUEST:")[0].rstrip() + "\nREPLY LANGUAGE: Thai"      # the same song data for arm B: title, style, numbered lyrics, structure, returning lines (no report request)
    return lyrics, user, block


def run_oneshot(be, name, song, req_no):
    lyrics, user, _ = song_inputs(name, song)
    tag = f"{name.replace(' ', '')}_{be}_oneshot"
    rec = {"request_no": req_no, "arm": "A one-shot", "song": name, "backend": be, "temperature": TEMP, "system_prompt": PROD_SYSTEM, "user_message": user,
           "history": "none (a single request)", "system_tokens_est": LC.estimate_tokens(PROD_SYSTEM), "user_tokens_est": LC.estimate_tokens(user)}
    t0 = time.perf_counter(); raw = ""; usage = {}
    if not DRY:
        try:
            if be == "grok":
                raw, usage, rec["model"], _b = grok_call(PROD_SYSTEM, [{"role": "user", "content": user}])
            else:
                raw = LC.ask_local(HOST, WF, PROD_SYSTEM, user, temperature=TEMP); rec["model"] = "gemma4_llm.json"
        except Exception as ex:
            rec["error"] = f"{type(ex).__name__}: {ex}"
    rec["seconds"] = round(time.perf_counter() - t0, 1); rec["usage_reported"] = usage
    rec["output_tokens_est"] = LC.estimate_tokens(raw)
    (HERE / "RAW_REQUESTS" / f"{tag}_request.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "RAW_RESPONSES" / f"{tag}_response.txt").write_text(raw or f"[NO ANSWER] {rec.get('error', '')}", encoding="utf-8")
    trace({k: rec[k] for k in ("request_no", "arm", "song", "backend", "seconds", "usage_reported", "system_tokens_est", "user_tokens_est", "output_tokens_est", "history") if k in rec} | {"session_id": "none", "error": rec.get("error")})
    print(tag, rec["seconds"], "s", rec.get("error", ""), flush=True)
    return raw, rec


def run_sequence(be, name, song, req_no0):
    """Nine steps in one session. Returns the list of step records; stops safely on an error or when the context guard would be passed."""
    lyrics, _user, block = song_inputs(name, song)
    base = f"{name.replace(' ', '')}_{be}_seq"
    out = []
    sess = LC.LLMSession(N_CTX, goal="") if be == "gemma" else None      # goal "": nothing but the short system prompt is sent
    history = []                                                         # Grok: the earlier messages, replayed in full
    for n in range(1, 10):
        user = ST.step_user(n, block)
        rec = {"request_no": req_no0 + n - 1, "arm": "B sequential", "step": n, "step_name": ST.STEPS[n - 1][0], "song": name, "backend": be, "temperature": TEMP, "system_prompt": ST.SYSTEM_SHORT, "user_message": user,
               "system_tokens_est": LC.estimate_tokens(ST.SYSTEM_SHORT), "user_tokens_est": LC.estimate_tokens(user)}
        if be == "gemma":
            cost = LC.estimate_tokens(ST.SYSTEM_SHORT if n == 1 else "") + rec["user_tokens_est"] + LC.SESSION_RESERVE
            rec["history"] = "server-side session (ComfyUI LLMSessionChatNode, session id below): every earlier turn is kept by the node; max_turns 200, no summarizing"
            rec["session_used_est_before"] = sess.used
            if sess.id and sess.used + cost > sess.ratio * sess.n_ctx:
                rec["error"] = "STOPPED by the context guard: the next step would pass 65 % of ctx_local; production would open a NEW session here, the prototype does not (it would not be a continuing session)"
                rec["seconds"] = 0
                out.append(rec); trace({k: rec[k] for k in ("request_no", "arm", "step", "song", "backend", "session_used_est_before", "error")}); print(base, n, rec["error"], flush=True)
                break
        else:
            rec["history"] = f"full replay of {len(history)} earlier messages ({len(history) // 2} steps), nothing summarized"
        t0 = time.perf_counter(); raw = ""; usage = {}
        if not DRY:
            try:
                if be == "grok":
                    raw, usage, rec["model"], b = grok_call(ST.SYSTEM_SHORT, [*history, {"role": "user", "content": user}])
                    rec["messages_sent"] = b["messages"]; rec["session_id"] = "none (stateless API; history replayed by the client)"
                else:
                    raw = LC.ask_local(HOST, WF, ST.SYSTEM_SHORT, user, temperature=TEMP, session=sess); rec["model"] = "gemma4_llm.json"
                    rec["session_id"] = sess.id; rec["session_log"] = list(sess.log); rec["session_used_est_after"] = sess.used
            except Exception as ex:
                rec["error"] = f"{type(ex).__name__}: {ex}"
        rec["seconds"] = round(time.perf_counter() - t0, 1); rec["usage_reported"] = usage; rec["output_tokens_est"] = LC.estimate_tokens(raw)
        tag = f"{base}_step{n}"
        (HERE / "RAW_REQUESTS" / f"{tag}_request.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        (HERE / "RAW_RESPONSES" / f"{tag}_response.txt").write_text(raw or f"[NO ANSWER] {rec.get('error', '')}", encoding="utf-8")
        trace({k: rec[k] for k in ("request_no", "arm", "step", "song", "backend", "seconds", "usage_reported", "system_tokens_est", "user_tokens_est", "output_tokens_est", "history", "session_id", "session_used_est_before", "session_used_est_after", "session_log", "error") if k in rec})
        print(tag, rec["seconds"], "s", usage.get("total_tokens", ""), rec.get("error", ""), flush=True)
        out.append(rec)
        if rec.get("error") or not raw:
            break
        history += [{"role": "user", "content": user}, {"role": "assistant", "content": raw}]
    return out


if __name__ == "__main__":
    req = 1
    for be in ("gemma", "grok"):
        if ONLY and be != ONLY:
            continue
        for name, song in SONG_SET.items():
            run_oneshot(be, name, song, req); req += 1
    if not ONESHOT_ONLY:
        for be in ("gemma", "grok"):
            if ONLY and be != ONLY:
                continue
            for name, song in SONG_SET.items():
                run_sequence(be, name, song, req); req += 9
    print("finished", flush=True)
