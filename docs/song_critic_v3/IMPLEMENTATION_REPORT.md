# IMPLEMENTATION_REPORT: Song Critic V3, Complete Songcraft Review

Status: **not committed** (as instructed: review first). Branch master, HEAD b0a5c93 (V1 + V2 pushed). No version bump.

## One-paragraph outcome
The Song Critic report now has a second layout, the Complete Songcraft Review (Executive summary, eight aspects, Action Plan), together with its parser, page, Discuss-with-Songwriter handoff and Outdated handling. All of it works and is tested. **But the candidate prompt that produces this layout did not pass the quality rule I fixed before running it, so the production prompt is unchanged and the app still produces the previous report.** You can read the layout now (real candidate answers were saved as reports and shown in the real app); turning it on needs your decision (QUALITY_COMPARISON.md, "Decision").

## What was built (all inside the existing Song Critic; no new specialist or tab)
| Area | Change | Where |
|---|---|---|
| Schema + parser | format `songcraft-1`: `[VERDICT]`, 8 x `[ASPECT.n]`, 0-3 `[ACTION.n]`, `[SECTION.n]`, `[NOTE]`; five statuses + `unrated`; opening patterns; aliases for names the models really wrote; separate line references; invalid refs kept and marked; quotes checked against the lyrics | `music_studio/spec_reports.py` (+152 / -9) |
| Page | Executive summary; "Start here"; 8 numbered expand/collapse tiles with status chip and line chips; Action Plan cards with "View topic" and Discuss; opening patterns as tools; "not reviewed" for missing aspects; Song Journey reuses the old section ribbon; legacy reports render the old page | `music_studio.py` (`sr_songcraft`, `sr_aspect_tile`, `sr_action_card`, `sr_status_chip`, ...) |
| Handoff | `sr_discuss(finding_id, action_id)`: whole report, one aspect, or one action; the Action Plan is never cut; hard token budget with refusal before sending; session kept; Outdated labelled with old line numbers; no Song Critic call; no lyric change | `spec_reports.handoff_text`, `music_studio.py` |
| Panel | 320-560 px: nothing fixed above 280 px, rows wrap (checked in the harness and in real Flet at 320 / 360 / 560) | `music_studio.py` |
| Thai | +36 strings | `music_studio/locales/th.json` |
| Knowledge | Songcraft Reference (about 1.6 KB) inside the candidate prompt, sent with every request | `CANDIDATE_PROMPT.md` (not installed) |
| Tests | `tests/test_songcraft.py` (55 checks); existing suites unchanged | `tests/` |

Untouched: Music Producer, Rhyme & Flow, Vocal Flow, Lyric Language, Songwriter chat code paths (only the shared handoff hook that V2 already added), the Numbered Clear Tags contract, `SCHEMA`.

## Brief vs result
| Brief item | Result |
|---|---|
| Executive Summary, 8 aspects, Action Plan | built and shown in real Flet |
| Opening patterns | 8 patterns, several allowed, shown as "tools not rules" |
| Statuses never forced | five statuses + "Not rated" + "Not reviewed in this report" |
| Finding -> Evidence -> Impact -> Action -> Trade-off -> Keep original | fields and labels (found / why it matters / suggested action / trade-off or keep original) |
| Panel 320-560, summary and plan prominent, expand/collapse | done; one tile open (the first action's aspect) |
| Legacy reports still render | tested (A16, A22, B13) |
| Preview/Apply/Undo for real Original/Proposed | existing flow untouched; a Songcraft page has no Apply by design (conceptual suggestions go to the Songwriter) |
| Discuss with Songwriter (whole report / item / action), budget, Outdated, no lyric change, no Song Critic re-call | tested in the harness (C1-C5, D2, E1-E2); **not clicked against a real backend** |
| Knowledge loaded and sent, no bloat | yes, in the candidate prompt; +52 % size is itself one reason it failed (f) |
| Prompt version / Outdated | existing prompt-hash mechanism; B15 |
| Quality audit with Expected Observations first, two backends, five kinds, one candidate | done; candidate **fails** (b)(c)(d)(f), passes (a)(e) (a was first reported as a fail from stale first-parser counts; corrected, see FINAL_REPARSE_RESULTS.md) |
| If judgment gets worse keep production | done |
| Real Flet screenshots | 360, 560 (after restart), 320 (top only); see KNOWN_LIMITATIONS |

## Things found while verifying
- Parser first pass lost aspects when Gemma wrote display names -> aliases (numbers before the fix are in `run_parser_v1.log`).
- Gemma repeated `lines: -` at the end of a block and erased the real lines -> `fields_of` keeps the first real value.
- At tight token budgets the Action Plan was cut -> it is now never cut, and test E2 was updated.
- A second "Discuss with Songwriter" button appeared in expanded tiles (visible in the real app) -> removed; page re-capture not done (KNOWN_LIMITATIONS 6).

## Files
`CHANGED_FILES.md`, `PATCH/song_critic_v3_task_only.patch` (task hunks only; `git apply --check` without `--recount` and the four related test files pass on a clean HEAD worktree: `PATCH/clean_head_check.txt`), `PATCH/test_songcraft.py`, `PATCH/OTHER_WORK_music_studio_py_not_part_of_this_task.diff` (not part of this task).
