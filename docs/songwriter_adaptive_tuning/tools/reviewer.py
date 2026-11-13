# -*- coding: utf-8 -*-
"""usage: reviewer.py <candidate name> [include holdout diagnosis: no]  -> tune_out/<name>_review.txt
A separate Grok session (stateless, one request) reads: the goal, the system prompt that was really sent, the TUNE cases (question, context, expected, raw answer). Holdout answers are NOT sent (they must not drive the tuning)."""
import json, sys
from pathlib import Path

SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
sys.path.insert(0, str(SCR)); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
from tune_cases import CASES
from music_studio import llm_chat as LC

name = sys.argv[1]
OUT = SCR / "tune_out"
EXPECT = {
    "A": "Understands 'นัดบอด' = blind date set up by the mother. Offers 2-4 DISTINCT natural Thai alternatives people really say (e.g. จับคู่ดูตัว / พาไปดูตัว / ให้ไปเจอกันก่อน...), keeps meaning and narrator, checks the syllable fit/rhyme with the next line, says which it prefers and why. Does NOT invent stiff coinages (นัดคู่, นัดสุ่ม, นัดแบบไม่รู้จัก). May say the original is fine if it truly is, but the user dislikes the English-sounding 'บอด'.",
    "B": "Turn 1: an honest, specific view of the line (what works, what it risks), not generic praise. Turn 2: the user proposes a replacement that changes the narrator's stance (from pushing back to 'not disliking him') and the line to replace is the one discussed. A good songwriter weighs it honestly, names the cost (meaning shift, loses the playful pushback, syllables), does not just agree, and if proposing a tag, targets the discussed line.",
    "C": "Reads the actual last words of the lines (หนอ, นา, เอย, นะ). Separates words that look inserted just to rhyme/fill (หนอ/นา/เอย in contemporary speech-like lines) from words that do real work; says 'may be' when evidence is thin; no invented lines; ordinary language, no edit.",
    "D1": "Sees that the verses repeat one sadness in interchangeable images with no progression, despite good sound; says so candidly with quoted lines; does not praise the 'strong spine'.",
    "D2": "Treats the song as an intentional mood piece: judges whether the images build one experience, does NOT demand a plot or 'telling'; candid, specific.",
    "E": "Writes a natural spoken-Thai Verse 3 that moves the story (the daughter agrees to meet but wants to know him first), fresh images, some tasteful rhyme, singable lines, one insert tag in the right place with clean text; a short natural note, not a lecture.",
    "F": "Turn 1: one change_lyric tag for the right line with exact from, shorter but still funny. Turn 2: accepts the cancel in words, no tag, does not say anything was restored.",
}
GOAL = ("Build a better songwriter, not a better rule follower. The Songwriter AI (local Gemma) should be a creative, knowledgeable Thai songwriter with its own judgment: natural idiomatic Thai, "
        "understands words/phrases/context, real knowledge of rhyme (internal/end), syllables and singability, proposes words people really use (no coined awkward words), critiques independently "
        "(does not flatter or agree by default, says when the original is better), chats naturally like a co-writer, is free to be creative (no checklist), and still obeys the app's safety contract (action tags, read-only analysis).")

tune = [c for c in CASES if c["split"] == "tune"]
res = {}
for ln in (OUT / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
    r = json.loads(ln)
    res.setdefault((r["case"]), []).append(r)
system = (OUT / f"{name}_system.txt").read_text(encoding="utf-8")
parts = [f"GOAL\n{GOAL}\n", "You are an independent prompt reviewer. Below: the system prompt that was REALLY sent to the model (about %d tokens), then test cases with the raw answers. " % LC.estimate_tokens(system),
         "Diagnose WHY the model answered badly. Rules for you: (1) cite evidence: quote the exact prompt sentence AND the exact answer text for every cause; (2) label each point PROVEN (the prompt text plainly causes it) or CONJECTURE; "
         "(3) say whether it is a prompt problem (conflict, over-constraint, misleading few-shot, contract/tool wording, too many rules, duplicated instructions) or a Gemma capability limit or a parser/context-assembly problem; "
         "(4) do NOT propose rules about specific words from the test cases; propose general changes (what to delete, merge, rephrase, reorder, where to give freedom); (5) be brief.\n",
         "=== SYSTEM PROMPT THAT WAS SENT ===\n" + system + "\n=== END SYSTEM PROMPT ===\n"]
for c in tune:
    parts.append(f"\n--- CASE {c['id']}: {c['title']} ---\nEXPECTED: {EXPECT[c['id']]}\nLYRICS GIVEN TO THE MODEL (line ids L001..):\n" + "\n".join(f"L{i:03d}| {l}" for i, l in enumerate(c['lyrics'].split('\n'), 1)))
    for r in res.get(c["id"], []):
        parts.append(f"USER (turn {r['turn']}): {r['ask']}\nMODEL ANSWER (turn {r['turn']}):\n{r['raw'].strip()[:3000]}")
user = "\n".join(parts)
g = LC.grok_creds()
ans = LC.ask_grok(g["key"], g["model"], "You review prompts for LLM applications. Be evidence-based and concise.", user, temperature=0.2)
(OUT / f"{name}_review.txt").write_text(ans, encoding="utf-8")
print(ans)
