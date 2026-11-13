import hashlib, re, sys
from pathlib import Path
sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
from music_studio import specialists as SP, specialist_engine as SE, llm_chat as LC, lyric_thesaurus as TH, chat_tags as CT

SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
est = LC.estimate_tokens
real = Path(r"C:\Users\sudja\AppData\Roaming\WorkflowPad\specialists\prompts\songwriter.md")
bund = Path("D:/AI/MyApps/WorkflowPad/music_studio/prompts/specialists/songwriter.md")
print("AppData copy == bundled:", hashlib.md5(real.read_bytes()).hexdigest() == hashlib.md5(bund.read_bytes()).hexdigest())

extra = SE.spec_extra("songwriter") if hasattr(SE, "spec_extra") else ""
chat, p = SP.effective_system("songwriter", extra, mode="chat")
(SCR / "audit_chat_system.txt").write_text(chat, encoding="utf-8")
print("chat system tokens:", est(chat), "chars:", len(chat))
print("chat contract (SONGWRITER_CHAT) tokens:", est(CT.SONGWRITER_CHAT) if hasattr(CT, "SONGWRITER_CHAT") else "?")
parts = re.split(r"(?m)^(## .+)$", chat)
rows = []
head = parts[0]
rows.append(("(top)", est(head)))
for i in range(1, len(parts), 2):
    rows.append((parts[i], est(parts[i + 1])))
for name, t in sorted(rows, key=lambda r: -r[1])[:40]:
    print(f"  {t:5d}  {name[:70]}")
neg = len(re.findall(r"(?i)\b(never|do not|don't|must not|no )\b", chat))
must = len(re.findall(r"(?i)\b(must|always|only|exactly)\b", chat))
print("prohibitions (never/do not/no):", neg, "| absolutes (must/always/only/exactly):", must)
print("few-shot headings:", re.findall(r"(?m)^###? .*(?:Example|[A-D]\. ).*$", chat)[:12])
rep, _ = SP.effective_system("songwriter", "", mode="report")
print("report system tokens:", est(rep), "| analyze task tokens:", est(SE.analyze_task("songwriter")))
print("thesaurus system tokens:", est(TH.SYSTEM_PROMPT))
(SCR / "audit_thes_system.txt").write_text(TH.SYSTEM_PROMPT, encoding="utf-8")
