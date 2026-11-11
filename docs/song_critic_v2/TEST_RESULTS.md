> **Superseded in two points by `FOLLOWUP_REPORT.md`:** the hand-off token budget is now a hard limit (no 500-token floor; the note is counted; a budget that cannot hold the smallest useful hand-off is refused before anything is sent), and for changed lyrics the 'lyrics around the finding' block is no longer built from old line numbers. `test_critic_actions` is now 59 / 59.

# TEST_RESULTS — what passed, by which kind of test

**Kinds:** *Unit* = pure functions, no UI. *Harness* = the real `main()` control tree on a fake page with MOCK model functions (a recorder, no model) — it is NOT the running app. *Real Flet* = the real app in Flet web mode in the browser pane with a temporary profile (the user's AppData untouched). *Real model* = an actual Gemma / Grok request. Mocks are not counted as real UI tests.

## Unit (no UI)
| Suite | Result | Covers |
|---|---|---|
| `tests/test_critic_refs.py` | **62 / 62** | `parse_refs` (valid forms, 13 invalid forms), findings with several references, quote checks, invalid lines kept, proposal rules, other specialists' rule, **all 18 raw answers of Audit 01 and Tune 02 re-parsed: every finding kept**, legacy finding shape |
| `handoff_text` cases in `tests/test_critic_actions.py` (H4, L2) | included below | budget order, selected finding survives every budget, "what was cut" named, legacy finding |
Output: `TEST_OUTPUT/test_critic_refs.txt`.

## Harness (mock model; control tree, not pixels)
| Suite | Result | Covers |
|---|---|---|
| `tests/test_critic_actions.py` | **49 / 49** | finding labels and line chips; conceptual finding has no Apply; ORIGINAL / PROPOSED with Preview + Apply; **Preview changes nothing; Apply changes exactly those lines; Undo restores; Redo re-applies; open again after Undo**; outdated report offers no Apply / Preview; Discuss (whole report and finding): one Songwriter request, **no Song Critic request**, framing / ids / status / verdict / findings / numbered lyrics in the request, earlier chat message and session kept, nothing applied; OUTDATED + what changed; budget on a 15 000-token context; **a full conversation sends nothing**; pending report not used in another song; no control wider than 280 px; wrapping button rows; legacy finding renders and can be handed off |
| `tests/test_spec_reports.py` | **108 / 108** | the V1 suite (navigation, persistence, Outdated, evidence, parsing, stale links, language switch) with the two V1 expectations updated for "finding kept" |
Output: `TEST_OUTPUT/`.

## Real Flet app (browser pane, temporary profile; real Gemma report data; Test Song 04)
Screenshots in `SCREENSHOTS/`. The data is a real Gemma answer parsed by the current parser, with two **demonstration edits that are not real model output**: one finding's `lines:` was replaced by `verse 2` (to show a kept finding with unreadable lines) and one single-reference finding got a synthetic `proposal` (the real prompt never asks for one).
| What | Result |
|---|---|
| Report page renders at 360 px (no grey error box): header, status, vertical story arc, findings | pass (11 screenshots) |
| Same page at 320 px (top of the page) | pass (`320_report_top.jpg`); **not scrolled** |
| 560 px | **not captured** (the browser pane's scale broke on that run) |
| Several references: two chips and two quote blocks with a gap mark | pass |
| Unreadable lines: kept, chip "อ่านเลขบรรทัดไม่ได้", unverified note | pass |
| Found / why it matters / suggested action / trade-off / Discuss button | pass |
| ORIGINAL vs PROPOSED, Preview and Apply | pass |
| **Preview** (the existing dialog) → **Close** → lyrics unchanged | pass |
| **Apply** → lines L2-L4 replaced, "applied" shown, toast | pass |
| **Undo** in the editor → lyrics restored, Preview / Apply open again | pass |
| Redo in the real app | **not tested** (harness only) |
| **Discuss with Songwriter** (finding): the user's message, the "Sent with this message … 2 730 tokens of a 3 500-token budget" note, then a **real Songwriter (Gemma) answer** that discusses the finding | pass; the quality of that answer was not graded |
| Discuss of the whole report; Discuss with Grok; Discuss on an Outdated report; Apply on an Outdated report | **not tested in real Flet** (harness only) |
| Two sheets / song switching in real Flet | **not tested in real Flet** (harness only) |

## Real model
16 report requests of Task B (QUALITY_AUDIT_REPORT.md) and 1 real Songwriter request (the hand-off above). Nothing else.

## Existing suites (system Python; `regression_all.log`, 62 files)
Pass as before: ai_recommend_v2, analyze_song_fix, chat_selection_preview, clear_tags, conversation_fixes, live_dead(_ui), live_warning_ui, natural_chat, section_apply, songwriter_tools, session_and_logs, generate_*, editor suites, font_scale and the rest of the list.
Fail with **exactly the same counts as before this work** (they failed on `HEAD` or are caused by earlier uncommitted rhyme / prompt work, none touches these files): test_basic_caption_builder (4), test_caption_intelligence (2), test_cue_catalog (5), test_music_studio_quality (1 failure, 3 errors), test_prompt_modes, test_rhyme_flow, test_rhyme_map (2), test_rhyme_v6 (3), test_songwriter_judgment, test_analyze_song_fix_ui (3 of 6).
Music Producer, Songwriter chat, Report persistence and the Lyrics Editor suites: pass.

## Clean-tree check of the patch
`PATCH/clean_head_check.txt`: on a clean `HEAD` worktree the task-only patch passes `git apply --check` (no `--recount`), applies, and with the new test files, the harness, the fixture and the (untracked) LLM workflow folder the three suites give **62 / 62, 49 / 49, 108 / 108**.
