# Follow-up to the independent review — two handoff defects (short report)

Scope as asked: only the two defects, focused tests, no prompt or UI redesign, other specialists untouched. Production prompt unchanged.

## Fix A — old line numbers over changed lyrics (`spec_reports.handoff_text`)
- When the lyrics are not the same as at the analysis, the **"current lyrics around the finding" block is no longer built** (it used the report's OLD numbers on the CURRENT lyrics).
- The selected finding's evidence is shown from the lyrics **as they were at the analysis** and labelled "numbered as in the OLD lyrics of the analysis"; the full numbered CURRENT lyrics still travel when the budget allows. With unchanged lyrics nothing changes.
- Tests (`16A`): 10 lines inserted above (the old L24-L26 are now L34-L36: no around-block, old numbering labelled, current numbering present), 10 lines deleted, one line edited in place, no-change control (around-block present, L22-L28).

## Fix B — a hard token budget
- `handoff_text(..., budget_tokens)` now returns `None` + `{"refused": True, "tokens": <needed>, "budget": …}` when the smallest useful hand-off does not fit. The size is measured on the **final text including the CONTEXT NOTE**; blocks are degraded (shortened first, then cut, least important first, the selected finding last, in a very short form at worst) until it fits.
- The floor of 500 tokens is gone: `budget = min(3500, avail)`; `avail` (the engine's own limit minus system prompt, live conversation, message, margin) can be zero or negative.
- **Discuss is refused before anything happens** (`sr_discuss`): toast "no room, start a new session"; no chat message, no pending report, no model request, lyrics and session untouched. A second check in `ask_ai` (the real message size) refuses the same way with a chat note if the room disappeared in between.
- Tests (`16B`): the final text never exceeds 500 / 700 / 1500 / 3500 (current and outdated); the selected finding and its evidence survive wherever a hand-off fits; 300 → refused with the size it would need; a tight budget still names what was shortened or left out; end to end (`H4`): no room → no request, no chat message, no pending report.
- Measured (`HANDOFF_SAMPLES/`, real Gemma report of Test Song 04): 3 500 → 2 660 tokens; 1 500 → 1 347; 700 → 606; 300 → refused.

## Tests
`test_critic_actions` **59 / 59** (was 49; +10), `test_critic_refs` 62 / 62, `test_spec_reports` 108 / 108, `test_songwriter_tools`, `test_natural_chat`, `test_chat_selection_preview` pass. `git apply --check` of the task-only patch on a clean `HEAD` worktree (no `--recount`) passes, and the three suites pass there (62 / 59 / 108; `PATCH/clean_head_check.txt`; the clean tree also needs the untracked `comfy_apis/` workflows and `docs/realtime_internal_rhyme/screenshot_song.txt`, both of which tests read).

## Real Flet smoke: Discuss on an OUTDATED report
A real report of Test Song 04 whose lyrics differ by one line from the song: the page shows "ล้าสมัย" (outdated) with its notice; pressing "Discuss the whole report" sent the message and the note "about 2 767 tokens of a 3 500-token budget"; a real Gemma Songwriter request started (`SCREENSHOTS/360_outdated_report_discuss_whole_report_real_flet.jpg`). The trace of the request text (`specialist_trace.jsonl`) shows `Report status: OUTDATED — the lyrics changed after the report`, `Changes since the analysis … Line numbers in the report belong to the OLD lyrics` and **no** around-the-finding block. The Songwriter's answer was not read or graded.

## Still unverified (unchanged)
Flet 560 px; Grok chat in the real app; a model-written edit through Apply; the UX variant "open the composer with a draft and attach the report to the user's next message" (not done: the immediate-discussion behaviour is kept, as the review allowed).
