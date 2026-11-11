# WorkflowPad — Specialist Reports V1: Implementation Report

Status: **implemented, NOT committed** (as the brief says: no commit before the audit is reviewed).
Date: 2026-11-11. Base: `e2172a5` (v0.3.5 Beta) + the uncommitted work that was already in the tree.

## 1. What was built

Four read-only report specialists, each with its own prompt, its own evidence, its own page and its own saved result:

| Report | Specialist id | Strip icon (Flet) | Evidence given to the model | Evidence drawn by the program |
|---|---|---|---|---|
| Song Critic | `song_critic` | `ARTICLE` family (`r.icon` in `spec_reports.REPORTS`) | numbered lyrics, section map, style words | story-arc ribbon, line refs |
| Rhyme & Flow | `rhyme_flow` | wave icon | numbered lyrics + `python_rhyme_evidence` pairs (Exact/Near, positions, placement, confidence, forced words, repetition apart from rhyme) | lyrics with pair colours, per-pair EXACT/NEAR text |
| Vocal Flow | `vocal_flow` | mic icon | numbered lyrics + syllable counts, live/dead (`live_dead`), density per section | syllable chips, live/dead bar, density by section |
| Lyric Language | `lyric_language` | translate icon | numbered lyrics only | original vs alternative comparison (not applied) |

* Songwriter AI stays **Chat / Co-writing only**. Its old "report" button now explains that and makes no request. Music Producer AI is **unchanged** (Analyze Song now asks the Music Producer only).
* No Apply button exists on any report page (read-only / comparison only). Alternatives are shown as "ORIGINAL" vs "ALTERNATIVE · for you to consider … not applied to the lyrics".
* No LLM call happens when a tab is opened, when switching tabs, or while typing. The only request is the **Analyze / Analyze again** button of that one report.

## 2. Files

New: `music_studio/spec_reports.py` (UI-free: reports table, evidence builders, request builder, parser, state), `music_studio/prompts/specialists/{song_critic,rhyme_flow,vocal_flow,lyric_language}.md`, `tests/test_spec_reports.py`, `docs/specialist_reports/*`.
Changed production files: `music_studio.py` (report pages, tabs/icons, run/guards, Analyze Song → Producer only), `music_studio/specialists.py` (4 entries, `REPORT_IDS`, chat/analyze lists unchanged), `music_studio/locales/th.json` (91 new Thai strings).
`diff/music_studio_py_full_worktree.diff` also contains earlier, unrelated uncommitted work (Export Audio dialog, chat markdown code style); the Specialist Reports part is the block `sr_*` before `RP_TABS`, the `RP_TABS`/`RP_ICONS` additions, `rp_show_tab`, `rp_strip_refresh`, `start_report`, `run_report` (`specs = [only] if only else [SP.PRODUCER]`).

## 3. Prompts

Four separate `.md` files in the existing section format (Role, Primary Goal, Responsibilities, Analysis Method, Quality Standards, Rules, Limitations, Few-shot Examples, Output Requirements, Self-check). They follow the Custom Prompt / AppData rules through `ensure_user_copies / load / effective_system`. They reuse the Numbered Clear Tags and the report parser (`report_steps.split_answer`), output contract `[VERDICT.1] [FINDING.n] [SECTION.n] [NOTE.1] [DONE]`. None copies the long Songwriter REPORT prompt.

## 4. Prompt / token comparison (`estimate_tokens`)

| Prompt (system) | chars | tokens |
|---|---|---|
| Song Critic | 5 746 | 1 806 |
| Rhyme & Flow | 5 718 | 1 794 |
| Vocal Flow | 5 427 | 1 707 |
| Lyric Language | 6 293 | 1 982 |
| Songwriter AI (chat + report sections, for reference) | 23 808 | 7 629 |
| Music Producer AI (for reference) | 23 476 | 7 377 |

Each new report therefore costs about a quarter of the Songwriter prompt. User-message size on the smoke song: Song Critic 591, Rhyme & Flow 1 620, Vocal Flow 1 143, Lyric Language 965 tokens.

## 5. Real Gemma smoke (ONE short song, ONE request per report, no audio)

Song S1 (20 sung lines, Thai). Raw answers and parser results: `raw_llm/*.json`, `raw_llm/smoke.log`.

| Report | seconds | parsed | findings | diagnostics |
|---|---|---|---|---|
| Song Critic | 22.6 | ok | 4 | 0 |
| Rhyme & Flow | 18.4 | ok | 4 | 0 |
| Vocal Flow | 12.0 | ok | 3 | 2 (two multi-line quotes were not verbatim → the program shows the lyric lines themselves) |
| Lyric Language | 12.6 | ok | 4 | 0 after the fix below |

Finding from the smoke: Gemma sometimes closes a block itself (`[/VERDICT.1]`), which leaked into the visible text. The parser now removes closing tags (format only) and notes it in the diagnostics; covered by a test.

## 6. Real Flet UI test (web mode, real app, temp profile, real Gemma reports saved into it)

Screenshots in `screenshots/` (Song Critic 360 px + scrolled, Rhyme & Flow 360 px, Vocal Flow 360 px, Lyric Language 360 px, Song Critic 560 px). These are the real app (`music_studio.py` through `ft.run(... WEB_BROWSER)`), not the test harness.
The real UI found **two bugs the harness cannot see** (grey Flutter error box instead of the page); both fixed:
1. a container with a one-sided border (`ft.Border(left=…)`) **and** `border_radius` (Flutter refuses it);
2. a header `Row(wrap=True)` that held an expanded `Column` (Flutter's Wrap refuses an expanded child) → header is now two rows.

Not captured: a 320 px screenshot (the browser pane rendered the page at a broken scale in that run; 360 px and 560 px were captured). Rhyme & Flow / Vocal Flow / Lyric Language were captured at their top and first scroll only, not to the very bottom.

## 7. Regression

`tests/test_spec_reports.py`: **108 / 108 pass** (mock LLM; `regression_spec_reports_output.txt`). It covers the 12 items of the brief: icons open the right page; tab switching makes no LLM call; per song / per sheet storage; persistence (session files re-read); Outdated with old result kept; each specialist gets its own prompt and evidence; parser / Clear Tags / partial / refused parts; no Apply buttons; Producer and Songwriter chat lists unchanged and the Songwriter report refused without a request; layout (scrolling column, wrapping rows); one report's error does not remove the others or its own older report; the lyrics are never changed by a report. A harness is a control tree, not the running app, so item "UI" is covered by section 6, not by this suite.

Existing suites run (system Python): passing — test_ai_recommend_v2, test_analyze_song_fix, test_chat_selection_preview, test_clear_cues, test_clear_tags, test_conversation_fixes, test_cover_repaint, test_editor_menu, test_font_scale, test_internal_rhyme_syllables, test_live_dead, test_live_dead_ui, test_live_warning_ui, test_log_view, test_lyric_formatter, test_session_and_logs, test_songwriter_tools and more.
Failing suites — checked against a clean `HEAD` worktree: test_analyze_song_fix_ui (5 failures at HEAD, 3 now), test_basic_caption_builder, test_cue_catalog, test_caption_intelligence fail at HEAD too. **Not** compared against HEAD (they concern earlier uncommitted rhyme / prompt work, none touches the new files): test_prompt_modes, test_rhyme_flow, test_rhyme_map, test_rhyme_v6, test_songwriter_judgment, test_music_studio_quality (missing `lyric_rhyme.parse_window`, token-budget asserts). See Known Issues.

## 8. Known issues

* Existing failing suites listed above are not caused by this change as far as checked, but test_prompt_modes / test_rhyme_flow / test_rhyme_map / test_rhyme_v6 / test_songwriter_judgment / test_music_studio_quality were not verified against HEAD.
* `music_studio.py` diff in this audit mixes this task with other uncommitted work.
* The Songwriter's older saved report stays readable (its icon shows only for a song that has one); no new Songwriter report can be made.
* The story-arc cells of Song Critic are tall in a narrow (≈320–360 px) drawer; readable, but cramped.
* No "Analyze All" (as specified). One real Gemma run only; prompts were not tuned beyond the closing-tag fix.
* No commit, no version bump, no build.


## 9. Follow-up: the independent audit (P1-A … P1-E, Q1–Q3)

| Item | Status | What was done | Tests (`tests/test_spec_reports.py`, section 13) |
|---|---|---|---|
| P1-A freshness ignored settings | fixed | `SR.input_fp(spec, lyrics, language, settings, corrections, style)` — a cheap fingerprint of only what each report uses (Song Critic: style words; Rhyme & Flow: rhyme engine version; Vocal Flow: corrections + BPM + time signature + duration; Lyric Language: corrections; all: lyrics + language + schema). Stored with the report (`data.input_fp`), compared in `state_of / outdated_why` without rebuilding any evidence; the Outdated notice now says WHY (lyrics / settings / prompt). Language passed to the evidence is now the song's lyrics language (`song_lang()`, "" for Auto) in both the preview and Analyze. Old reports with no stored fingerprint are not marked outdated by it. | new BPM → only Vocal Flow Outdated; new duration → only Vocal Flow; new genre → only Song Critic; re-analyse → Current; reason = "inputs"; fingerprint sensitivity unit tests |
| P1-B silent truncation | fixed | 12 marked lines / 24 pairs shown first with "Showing 24 / N" + **Show all / Show fewer**; the sentence "Only the pairs above exist in the evidence" appears only when every stored pair is listed. | 40 pairs, 25 pairs, 10 pairs, 41 marked lines |
| P1-C colour semantics | fixed | Colour = rhyme GROUP (the pair's `group`, palette `LI.PALETTE`, assigned in order of first appearance; no new inference); Exact vs Near = style (bold underline vs italic wavy) and the EXACT / NEAR label. Legend explains both. | two unrelated exact groups get different colours; a near pair of the first group keeps its colour; exact/near differ by italic only |
| P1-D stale links | fixed | Outdated report: every line reference (quotes, sections, story arc, rhyme and vocal lines) is shown as the saved number but is **not clickable**, with a tooltip that explains why. `stale` is now passed to the sections tile and the story arc. | click action absent when stale / present when current; after inserting a line, no reference on the page is clickable |
| P1-E permissive line parser | fixed | A line field must be exactly `L12` or `L12-L13` (case-insensitive, leading zeros ok; "whole song" / empty / `-` = no line). Anything else (`verse 2`, `12`, `lines 12 and 13`, `L12 and L14`, a missing line) → finding left out with a diagnostic that says what a reference must be; a section with a bad field is kept **without** a link. | accept / reject tables, finding and section cases |
| Q3 unverified quotation | fixed | A finding whose quotation did not match the lyrics now shows a visible "Unverified" note under the program's own lines. | marked / not marked |
| Q1 Lyric Language contradiction (original "too formal" → a more formal alternative) | **known limitation** | A model-judgement failure; the parser cannot judge register. Not fixed, not tuned (no prompt-tuning round, as advised). | — |
| Q2 Rhyme & Flow no-op recommendation (same word as the example) | **known limitation** | Same: model judgement. The audit also asked for the actual request: `raw_llm/*.json` now contain `request_user_message` (rebuilt from the same inputs; its length equals the length recorded at the smoke) and re-parsed results. | — |

Acceptance 7 (Songwriter chat, Music Producer analysis, real-time Rhyme Map, read-only persistence unchanged): the Rhyme Map / Live Insights code was not touched; `test_songwriter_tools`, `test_ai_recommend_v2`, `test_clear_tags`, `test_live_dead_ui`, `test_analyze_song_fix`, `test_session_and_logs` still pass.

Packaging: `diff/music_studio_py_TASK_ONLY.diff` (8 hunks) is the Specialist Reports part only; `diff/music_studio_py_OTHER_WORK_not_part_of_this_task.diff` (Export Audio dialog, chat markdown) is kept apart. The complete production sources are in the repository, not in this package; `new_files/` holds the new modules. `tests/` is git-ignored — stage `tests/test_spec_reports.py` deliberately if the tests belong in the repo.
Still not verified: 320 px and desktop-width real-UI screenshots; the real UI after these follow-up fixes was only opened to check that the Song Critic page (vertical story arc) renders without the grey error box (`screenshots/song_critic_vertical_story_arc_zoomed_partial.jpg`, browser-pane scale glitch); the Rhyme & Flow colour page and the Show all button were verified by the control-tree tests only, not by a real-Flet screenshot.
The quality smoke the audit asks for (2–3 controlled scenarios) was NOT run; Q1/Q2 stay as known limitations. One real Gemma run (one song) is still the only quality evidence.


## 10. Follow-up 2: the second independent review

| Item | Status | What was done | Tests (section 14 of `tests/test_spec_reports.py`) |
|---|---|---|---|
| P1-A residual: fingerprint did not cover the whole request | fixed | `input_fp` now hashes everything `build_user` puts in the message for EVERY report: the title, the five STYLE words and the lyrics, plus the lyrics language and, per report, the evidence inputs (Rhyme & Flow: the rhyme engine version `rmap-2`; Vocal Flow: corrections, BPM, time signature, duration; Lyric Language: corrections). Consequence: a new title or mood now makes all four reports Outdated (they all send those fields). The test compares `build_user` and `input_fp` together for title, genre, mood, energy, vocal and language on all four reports: whenever the request text changes the fingerprint must change. The sound-reader implementation (PyThaiNLP) is NOT in the fingerprint: only `rmap-2` is (a PyThaiNLP upgrade does not change it) — known gap. | `14F1` |
| P1-D residual: links active while Analyze-again runs | fixed | Link safety is now computed from `SR.outdated_why(...)` (lyrics / inputs / prompt) and no longer from the status chip, so an old report keeps its references disabled while the state is `running`, and until a matching report replaces it. | `14F2` (old report, a line inserted above, Analyze pressed, request not yet finished: no reference can navigate; after the answer arrives the new references work) |
| P1-C semantic gap: sound key was not the Real-time group | fixed | `python_rhyme_evidence.build` now stores `comp` on each pair = the index of the connected component from `RM.groups(m.links)`, the same call and ordering `rhyme_color.colorize` uses; the page colours a pair with `LI.PALETTE[comp % len]`, the editor's palette. Reports saved before this change (no `comp`) fall back to one colour per sound key. Exact / Near stays a style. A real-Flet screenshot shows it: `screenshots/rhyme_flow_group_colours_real_flet_1100.jpg`. | `14F3` (on the screenshot song every pair's `comp` equals the `rhyme_color` group id of its words; same sound key + different components → different colours; exact + near of one component → one colour) |
| P1-B upstream cap | fixed | `python_rhyme_evidence` already cut each section to `RF.MAX_PAIRS` (40). It now also stores `pairs_total` (detected before the cap), the specialist evidence carries it, and the page says "N pairs were detected, but only the first 40 of a section are kept in the evidence, so this list is not complete" and never prints "Only the pairs above exist" for a capped list. Show all shows all STORED pairs. The cap itself is not removed. | `14F4` (a real 80-line song gives 40 kept of more than 40 detected) |
| P2 story arc 12 | fixed | "12 / N" with Show all / Show fewer. | `14F5` |
| P2 line reference contract | fixed | `L12-13` and a reversed `L13-L12` are now refused; only `L12` and `L12-L13`. | `14F6` |
| P2 task-only patch | fixed | `task_only.patch` (LF) = the Specialist Reports changes to tracked files only: `music_studio.py` (8 hunks, the `audio_export` import line removed), `specialists.py`, `python_rhyme_evidence.py`, and `th.json` limited to the 107 keys this task added. Checked: `git apply --check` on a clean `HEAD` (`e2172a5`) worktree succeeds; after applying it and adding the new files (`spec_reports.py`, 4 prompts, the test and its harness) **all 101 tests pass on that clean tree**. The unrelated hunks (Export Audio, chat markdown) are in `diff/music_studio_py_OTHER_WORK_not_part_of_this_task.diff`. The base for the check is the repository `HEAD`, not the earlier snapshot file `music_studio(20261111-141826).py`. |
| Tests directory ignored by git | open | `tests/` is git-ignored: stage `tests/test_spec_reports.py` (and `tests/_ui_harness.py` it needs) with `git add -f`. |

Other suites after these changes (system Python): test_analyze_song_fix 58/58, test_internal_rhyme_syllables 38/38, test_live_dead 65/65, test_rhyme_detail_ui 5/5, test_songwriter_tools, test_clear_tags, test_ai_recommend_v2 pass. test_rhyme_map (2), test_rhyme_v6 (3) and test_analyze_song_fix_ui (3) fail with the SAME counts as before the PRE change (they failed before this task's follow-ups).
Files outside the task are untouched: the Songwriter chat, the Music Producer analysis, the prompts, Clear Tags and Preview/Apply/Undo. `python_rhyme_evidence.py` got three additive keys (`comp`, `pairs_total`) and no logic change.

Still not done: a controlled 2–3 scenario Gemma quality smoke (Q1/Q2 are still known limitations); 320 px / desktop-width screenshots; PyThaiNLP version in the fingerprint.


## 11. Follow-up 3: the third review

| Item | Status | What was done |
|---|---|---|
| Malformed `task_only.patch` | fixed | The reviewer was right: after the `audio_export` import line was taken out of the first hunk, its header kept the old count, and my earlier check passed only because it used `git apply --recount`, which hides that error. The patch generator now recomputes every hunk header; the patch is verified **without** `--recount`: `git apply --numstat` passes, `git apply --check` on a clean `HEAD` (`e2172a5`) worktree passes, and after applying it plus the new files **106 / 106 tests pass on that clean tree**. |
| Reply language not in the fingerprint | fixed | `input_fp(..., reply_language)`; the window language is captured in the snapshot when Analyze starts, used to build the request, and part of the fingerprint (`sr_input_fp` reads the current window language). Switching Thai / English makes every report Outdated (readable, no LLM call); Analyze again refreshes only that report. The reply language is kept apart from the lyrics language. Tests: section 15. |
| Supporting files missing from the zip | fixed | `new_files/_ui_harness.py` and `new_files/fixtures/screenshot_song.txt` (the fixture the tests read from `docs/realtime_internal_rhyme/`) are now included. `tests/` is git-ignored: use `git add -f tests/test_spec_reports.py tests/_ui_harness.py`. |
| 320 px / desktop check of expanded pairs, long arcs, clicks | **not done** | The browser pane rendered 320 px at a broken scale in the earlier attempt; control-tree tests are not a substitute. Open limitation. |
| PyThaiNLP version in the fingerprint | open | Only `rmap-2` is in it; add a sound-reader discriminator before a release that upgrades PyThaiNLP. |
| Q1 / Q2 | open | Model-judgement limits; one real Gemma song only. |


## 12. Follow-up 4: the fourth review

Fixed: the visible status of an open report page was not redrawn when the window language changed (the state was Outdated, the chip still said Current until the tab was rebuilt). `set_ui_lang` now calls `sr_render()` after the locale refresh when a report tab is open (no request, no dirty mark; the request in flight keeps the language captured at Analyze). Regression (section 15): the REAL `set_ui_lang` is called with the report tab left open; the visible status goes Current -> Outdated with no tab change and no request, and Analyze again restores only that report. Patch regenerated; `git apply` on a clean `HEAD` and then 108 / 108 tests on that tree pass. Open limitations unchanged: PyThaiNLP version not in the fingerprint; Q1/Q2; no 320 px / desktop real-UI check.
