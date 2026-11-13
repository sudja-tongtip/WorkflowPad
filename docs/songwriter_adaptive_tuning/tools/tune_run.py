# -*- coding: utf-8 -*-
"""usage: tune_run.py <candidate name> <md path | prod> <transform .py | prod> [case ids comma separated | all | tune | holdout]
Runs the quality cases on the local Gemma with the Songwriter chat system assembled the way the app does it (optionally with a candidate songwriter.md and a candidate transform of spec_extra).
Writes tune_out/<name>.jsonl (raw prompts' size, user messages, raw responses) incrementally. Nothing in the repo or the real AppData is touched."""
import json, os, re, sys, time, importlib.util
from pathlib import Path

SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
sys.path.insert(0, str(SCR))
import assemble as AS
from tune_cases import CASES

name, md, tf = sys.argv[1], sys.argv[2], sys.argv[3]
which = sys.argv[4] if len(sys.argv) > 4 else "all"
OUT = SCR / "tune_out"
OUT.mkdir(exist_ok=True)

AS.app()
if md != "prod":
    AS.install_candidate(md)
from music_studio import llm_chat as LC, rhyme_steps as RS, specialists as SP

full, extra = AS.chat_system()
if tf != "prod":
    spec = importlib.util.spec_from_file_location("cand_tf", tf)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    extra2 = mod.transform(extra)
    full = SP.effective_system("songwriter", extra2, mode="chat")[0]
est = LC.estimate_tokens
(OUT / f"{name}_system.txt").write_text(full, encoding="utf-8")
print(f"[{name}] system tokens ~{est(full)}", flush=True)

LLM = Path("D:/AI/MyApps/WorkflowPad/comfy_apis/llm")
make = RS.conversation_factory("local:gemma4_llm.json", "127.0.0.1:8188", LLM, 0.5)
ids = None if which == "all" else (set(c["id"] for c in CASES if c["split"] == which) if which in ("tune", "holdout") else set(which.split(",")))
path = OUT / f"{name}.jsonl"
done = set()
if path.exists():
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln); done.add((r["case"], r["turn"]))
        except Exception:
            pass
for case in CASES:
    if ids is not None and case["id"] not in ids:
        continue
    conv = make(lambda: "")
    for t, (ask, sel) in enumerate(case["turns"], 1):
        if (case["id"], t) in done:
            continue
        user = LC.build_user_message("เพลงทดสอบ", {"genre": "pop", "mood": "playful", "language": "th", "bpm": 100}, case["lyrics"], sel, ask, [], "")
        t0 = time.perf_counter()
        try:
            raw = conv.say(full, user)
            err = ""
        except Exception as ex:
            raw, err = "", f"{type(ex).__name__}: {ex}"
        dt = time.perf_counter() - t0
        rec = {"cand": name, "case": case["id"], "split": case["split"], "turn": t, "ask": ask, "system_tokens": est(full), "response_tokens": est(raw), "sec": round(dt, 1), "raw": raw, "error": err}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{name}] {case['id']} t{t} {dt:.0f}s {len(raw)} chars {err}", flush=True)
print(f"[{name}] finished", flush=True)
