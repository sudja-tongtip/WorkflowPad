# -*- coding: utf-8 -*-
"""Builds RHYME_HYBRID_V2_REPORT.md: the hand-written body (report_body.md) + appendices generated from the recorded files (prompt, raw responses, merge results, tests)."""
import json
from pathlib import Path
HERE = Path(__file__).resolve().parent
import sys; sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import hybrid as H
body = (HERE / "report_body.md").read_text(encoding="utf-8")
out = [body, "\n---\n\n## ภาคผนวก A — Prompt ของ Reviewer (ตัวจริงที่ส่งให้ Gemma, system)\n\n```text\n" + H.REVIEW_SYSTEM + "\n```\n"]
for sid in ("S1", "S2", "S3"):
    rec = json.load(open(HERE / "raw" / f"{sid}.json", encoding="utf-8"))
    out.append(f"\n## ภาคผนวก B-{sid} — Request (user) และ Raw Response ของ Gemma\n\nระบุ: system {rec['system_tokens']} tokens, user {rec['user_tokens']} tokens, LLM {rec['llm_seconds']} วินาที, error: {rec['error'] or '-'}\n\n"
               f"### Request (user)\n\n```text\n{rec['user']}\n```\n\n### Raw response\n\n```text\n{rec['raw']}\n```\n")
sc = json.load(open(HERE / "results" / "score.json", encoding="utf-8"))
for sid in ("S1", "S2", "S3"):
    h = sc[sid]["hybrid"]
    out.append(f"\n## ภาคผนวก C-{sid} — ผล Merge\n\nสถานะ: {h['counts']} | coverage: {h['coverage']} | parse issues: {h['parse_issues']}\n\n### Evidence ที่ Songwriter จะได้ (prototype `evidence_text_v2`)\n\n```text\n{h['evidence_text']}\n```\n\n"
               f"### Disagreement / Blocked\n\n```json\n{json.dumps({'disagreements': h['disagreements'], 'blocked': h['blocked']}, ensure_ascii=False, indent=1)}\n```\n")
tests = json.load(open(HERE / "test_offline_result.json", encoding="utf-8"))
out.append("\n## ภาคผนวก D — Offline regression (`test_offline.py`)\n\n| Case | ผล | รายละเอียด |\n|---|---|---|\n" + "\n".join(f"| {t['case']} | {'PASS' if t['pass'] else 'FAIL'} | {str(t['detail'])[:90].replace('|', '/')} |" for t in tests) + "\n")
(HERE / "RHYME_HYBRID_V2_REPORT.md").write_text("\n".join(out), encoding="utf-8")
print("written", (HERE / "RHYME_HYBRID_V2_REPORT.md").stat().st_size)
