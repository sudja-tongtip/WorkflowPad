# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
AUD = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad\audit")
sys.stdout.reconfigure(encoding="utf-8")
key = sys.argv[1]
d = json.loads((AUD / "out" / f"{key}.json").read_text(encoding="utf-8"))
J = json.loads((AUD / "judge.json").read_text(encoding="utf-8")).get(key.replace("_r", "|").replace("_", "|"), {})
print(f"##### {key} {d['seconds']}s requests={d['requests']}")
for sp, r in d["reports"].items():
    print(f"--- {sp} [{r['status']}] diagnostics={r['diagnostics'][:2]}")
    print("SUMMARY:", r["summary"])
    for s in r["strengths"]:
        print("  + ", s)
    for f in r["findings"]:
        print(f"  F[{f.get('kind')}/{f.get('priority')}] {f.get('first')}-{f.get('last')}: {f.get('finding','')[:140]} | why: {f.get('reason','')[:140]} | sug: {(f.get('suggestion') or '')[:80]}")
    for s in (r.get("data") or {}).get("rhyme_flow") or []:
        print(f"  SEC {s['name']} {s['first']}-{s['last']} pattern={s.get('pattern')} | Q: {s.get('quality','')[:200]} | issue: {s.get('issue','')[:120]} | consider: {(s.get('consideration') or s.get('consider') or '')[:100]} | keep: {s.get('keep_reason','')[:100]} | rec: {s.get('recommendation','')[:140]} ({s.get('priority')})")
    for u in r["uncertain"]:
        print("  ? ", u[:200])
print("JUDGE:", J.get("musical_judgment"), J.get("actionability"), J.get("critical_errors"), J.get("notes"))
