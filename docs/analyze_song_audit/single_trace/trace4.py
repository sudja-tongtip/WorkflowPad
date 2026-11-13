# -*- coding: utf-8 -*-
"""Rebuild the system prompts of the three stages from the production files and compare their token estimate with the recorded request (6417 / 7056 / 4413). No LLM."""
import sys
from pathlib import Path
sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
from music_studio import specialists as SP, specialist_engine as SE, report_steps as RP, llm_chat as LC, lyric_rhyme as LR
OUT = Path(sys.argv[1])
est = LC.estimate_tokens
rows = []
for spec, recorded in (("songwriter", 6417), ("music_producer", 7056)):
    base = SP.effective_system(spec, "", mode="report")[0]
    task = RP.STEP_NOTE + "\n\n" + SE.analyze_task(spec)
    for label, text in (("md(report) + STEP_NOTE + analyze_task", base + "\n\n" + task), ("task only", task)):
        rows.append((spec, label, est(text), recorded))
    (OUT / f"system_prompt_{spec}_reconstructed.txt").write_text(base + "\n\n" + task, encoding="utf-8")
rs = LR.step_system_prompt()
rows.append(("rhyme_analyzer", "step_system_prompt()", est(rs), 4413))
(OUT / "system_prompt_rhyme_analyzer_reconstructed.txt").write_text(rs, encoding="utf-8")
for r in rows:
    print(r)
