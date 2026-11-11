# KNOWN_LIMITATIONS — Song Critic Tune 02

## Method
- **n = 1 per cell** (12 runs). Run-to-run variation is real: the baseline prompt on the same songs gave a different `lines:` format and different findings in Audit 01 (Gemma 01 baseline lost all findings to the parser then, none now). A one-run difference between baseline and candidate may be noise; only patterns that repeat across songs/backends are noted as patterns.
- **Several edits at once.** The candidate changes the responsibilities, method, standards, one rule, the few-shot and the self-check together (PROMPT_DIFF). The data cannot say which edit caused which change. Per the brief there is no second candidate and no ablation; one is not proposed here.
- **Extreme songs.** The three songs are built to isolate one trait each (weak-pretty, strong-plain, abstract). Real songs are mixed; the S1 result in particular (candidate calls a weak song a success) may be less or more severe on a middle song.
- **Context hints.** The STYLE line (genre / mood / energy / vocal / language) was kept as in Audit 01 so both prompts get the same context. It can hint at a song's character (for example "Ambient Indie / Hushed" for 03, "Folk Pop / Tender" for 02); no title, description or design note was sent. One answer cites it (Gemma 01 candidate: "…เพลงแนว Dream Pop").
- **Auditor bias.** The auditor had read the Audit 01 answers on the same songs; `EXPECTED_OBSERVATIONS.md` says so and was fixed (hashed) before the 12 runs. Grades are the auditor's reading of the raw text; no LLM judge was used. The candidate was written by the same auditor who knew the Audit 01 problems (it answers them by design), so a gain on those exact problems is expected, not independent evidence.
- **Backend settings** as in production: Gemma local `gemma4_llm.json` with the user's `ctx_local` 19 000 (read, not changed), temperature 0.4; Grok `grok-4-latest`, temperature 0.4. Only the three synthetic songs were sent to Grok.
- **Reports were not opened in the app UI**; presentation effects (the 10–11-line card, the dropped findings) are inferred from the parser output.

## Not fixed (by instruction)
- Parser / contract: comma-list `lines:` still drops a finding whole (3 of 12 runs); `L005-L016` style ranges over separate places are accepted and show the lines between them. A strict multi-reference design is proposed in `AUDIT_REPORT.md` § 5 as work after this audit; no code was changed.
- Gemma's replacement-wording habit (baseline 7 of 7 concerns, candidate 2 of 2) is a model behaviour under both prompts; neither prompt changed it.
- Grok's generic recommendation ("ลองเพิ่มรายละเอียดเล็กน้อย…") is absent from the candidate only because the candidate wrote no concern.
- The candidate is **not** in production, not in AppData, not committed.
