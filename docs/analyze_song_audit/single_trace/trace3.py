# -*- coding: utf-8 -*-
"""Context integrity: does every specialist receive the original lyrics, character for character? Plus: every word the model quoted exists in its line. No LLM."""
import json, re, sys
sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
d = json.load(open("D:/AI/MyApps/WorkflowPad/docs/analyze_song_audit/raw_runs/local_S2_r1.json", encoding="utf-8"))
L = d["log"]
orig = d["lyrics"].split("\n")
out = {}
def numbered(user, pat):
    return {int(m.group(1)): m.group(2) for m in re.finditer(pat, user, re.M)}
for name, idx in (("Songwriter (first request)", 6), ("Producer (first request)", 12)):
    u = L[idx]["user"]
    blk = u[u.index("LYRICS (data):"):]
    got = numbered(blk, r"^(\d+)\| ?(.*)$")
    ok = all(got.get(i + 1, None) == orig[i] for i in range(len(orig)))
    out[name] = {"lines_sent": len(got), "lines_original": len(orig), "identical_to_original": ok}
rh = {}
for i in (0, 2, 3, 4, 5):
    for m in re.finditer(r"^L(\d+) \| (.*)$", L[i]["user"], re.M):
        rh[int(m.group(1))] = m.group(2)
sung = {n: t for n, t in rh.items()}
out["Rhyme Analyzer (all section requests)"] = {"sung_lines_sent": len(sung), "each_equals_original_line": all(orig[n - 1] == t for n, t in sung.items()), "lines_not_sent": [n for n in range(1, len(orig) + 1) if orig[n - 1].strip() and not orig[n - 1].startswith("[") and n not in sung]}
# words quoted by the model in the rhyme answers: do they occur in the line they point at?
bad = []
tot = 0
for i in (2, 3, 4, 5):
    for m in re.finditer(r"^L(\d+)\|([^\n(]+)", L[i]["raw"], re.M):
        n, w = int(m.group(1)), m.group(2).strip().split("|")[0].strip()
        tot += 1
        if w and w not in orig[n - 1]:
            bad.append((i, n, w))
out["Rhyme Analyzer raw: quoted words checked"] = {"total": tot, "not_in_their_line": bad}
json.dump(out, open("D:/AI/MyApps/WorkflowPad/docs/analyze_song_audit/single_trace/context_integrity.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
