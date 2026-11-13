# -*- coding: utf-8 -*-
"""Is the failure the prompt or the model? (1) Gemma asked the knowledge question directly, no prompt. (2) Grok with the PRODUCTION prompt on cases A and H2 (same system text, same user message)."""
import json, sys
from pathlib import Path
SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
sys.path.insert(0, str(SCR)); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
from tune_cases import CASES
from music_studio import llm_chat as LC, rhyme_steps as RS

LLM = Path("D:/AI/MyApps/WorkflowPad/comfy_apis/llm")
make = RS.conversation_factory("local:gemma4_llm.json", "127.0.0.1:8188", LLM, 0.5)
out = []
for q in ("คำไทยที่คนไทยพูดกันจริงๆ แทนคำว่า 'นัดบอด' (blind date) ที่แม่จัดให้ลูกไปเจอผู้ชายที่ยังไม่รู้จัก มีอะไรบ้าง ขอ 5 คำ/วลี",
          "ถ้าแม่จะพาลูกสาวไปเจอผู้ชายที่แม่เลือกให้ คนไทยจะพูดว่าอะไรบ้าง ขอ 5 วลีที่ใช้จริง"):
    conv = make(lambda: "")
    out.append(("GEMMA direct", q, conv.say("ตอบเป็นภาษาไทยสั้นๆ", q)))
sysmsg = Path(SCR / "tune_out" / "base_system.txt").read_text(encoding="utf-8")
g = LC.grok_creds()
for cid in ("A", "H2"):
    c = next(x for x in CASES if x["id"] == cid)
    user = LC.build_user_message("เพลงทดสอบ", {"genre": "pop", "mood": "playful", "language": "th", "bpm": 100}, c["lyrics"], "", c["turns"][0][0], [], "")
    out.append((f"GROK production prompt {cid}", c["turns"][0][0], LC.ask_grok(g["key"], g["model"], sysmsg, user, temperature=0.5)))
(SCR / "tune_out" / "capability.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
for who, q, a in out:
    print("===", who, "|", q[:60]); print(a.strip()[:1100]); print()
