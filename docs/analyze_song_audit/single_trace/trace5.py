import json
d = json.load(open("docs/analyze_song_audit/raw_runs/local_S2_r1.json", encoding="utf-8"))
L = d["log"]
stage = {0: "Rhyme Analyzer", 1: "Rhyme Analyzer", 2: "Rhyme Analyzer", 3: "Rhyme Analyzer", 4: "Rhyme Analyzer", 5: "Rhyme Analyzer"}
for i in range(6, 12): stage[i] = "Songwriter"
for i in range(12, 19): stage[i] = "Music Producer"
out = ["# Raw trace: song S2 (local Gemma, round 1) - every request exactly as sent and answered", "",
       "Source: docs/analyze_song_audit/raw_runs/local_S2_r1.json. System prompts are not stored per request there; they are rebuilt from the production files in system_prompt_*_reconstructed.txt (token estimate identical to the recorded request: 4413 / 6417 / 7056).", ""]
for i, r in enumerate(L):
    out += [f"## Request {i} - {stage[i]} - system ~{r['system_tokens']} tok, user ~{r['user_tokens']} tok, {r['sec']} s", "", "### Input (user message)", "```text", r["user"].rstrip(), "```", "", "### Raw answer", "```text", r["raw"].rstrip(), "```", ""]
open("docs/analyze_song_audit/single_trace/raw_trace.md", "w", encoding="utf-8").write("\n".join(out))
print("written", len(out))
