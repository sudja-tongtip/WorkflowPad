> **Superseded in two points by `FOLLOWUP_REPORT.md`:** the hand-off token budget is now a hard limit (no 500-token floor; the note is counted; a budget that cannot hold the smallest useful hand-off is refused before anything is sent), and for changed lyrics the 'lyrics around the finding' block is no longer built from old line numbers. `test_critic_actions` is now 59 / 59.

# KNOWN_LIMITATIONS — Song Critic V2

## Quality (Task B)
- One run per cell (16): the same prompt gave different details in Audit 01, Tune 02 and now; a one-run difference may be noise. 16 requests cannot show a rate.
- Three of the four songs were already seen by the auditor; the candidate was written by the auditor knowing their problems. S4 is new. All songs are synthetic and built to isolate a trait; real songs are mixed.
- Several edits were applied together; which one caused the Gemma regression on Test Song 01 is not known. No second candidate was run (the brief forbids tuning after the results).
- The STYLE line (genre, mood…) is sent as before and can hint at a song's character ("Hushed", "Bittersweet").
- Grades are the auditor's reading of the raw text. `EXPECTED_OBSERVATIONS.md` was fixed before the runs but written by someone who knew earlier answers of S1–S3.
- The abstract song (Test Song 03) and the unused-thread weakness (Test Song 04's letter) are not handled by either prompt.
- Gemma offers new wording in `recommendation` under both prompts (candidate 3 of 7 concerns, baseline 7 of 8) although the prompts forbid it.

## Implementation
- **A real model never writes a `proposal`:** the shipped prompts do not ask for one (the Song Critic gives directions, never lyrics, and the brief says not to compose lyrics arbitrarily). The Original / Proposed / Preview / Apply path is tested with a synthetic proposal (harness and real Flet); no real model answer exercised it.
- **Outdated report + an existing chat edit:** the report page offers no Apply for an outdated report, but a proposal that was already turned into a chat edit stays in the Songwriter chat; the chat's own check (the original text must be found exactly, once) decides whether it can still be applied, as for any chat edit.
- **A hand-off stays in the live conversation:** each "Discuss" adds up to 3 500 tokens to the Songwriter's session, so later budgets shrink and a full conversation asks the user to start a new session (the engine's existing guard; nothing is sent). The note says what was cut. There is no way yet to drop a previous hand-off from the session.
- **The prompt hint:** the shipped Song Critic prompt still says `lines: L12 or L12-L13`; the parser accepts lists regardless. Changing the prompt text would mark every saved Song Critic report Outdated (prompt hash), so it was left.
- Multi-reference lines are supported by the parser for all four specialists, but a Lyric Language alternative and a Song Critic proposal need one contiguous reference; a `SECTION` keeps one range.
- The `Show all` / vertical-arc / legacy behaviours of V1 are unchanged.

## Tests
- **Real Flet:** 360 px complete for the new features; 320 px only the top of the page; **560 px not captured** (the browser pane's scale broke); Redo, whole-report Discuss, Grok, Outdated and two-sheet cases were tested in the harness only (mock model, control tree) and are marked as such. The screenshots show a real Gemma report with two **demonstration edits** (unreadable lines on one finding, a synthetic proposal on another), stated in TEST_RESULTS.md.
- The clean-tree check needs the untracked LLM workflow folder `comfy_apis/` (the context size comes from it); a clean checkout without it makes the Songwriter tests fail for that reason.
- Existing failing suites (basic_caption_builder, caption_intelligence, cue_catalog, music_studio_quality, prompt_modes, rhyme_flow, rhyme_map, rhyme_v6, songwriter_judgment, analyze_song_fix_ui) fail with the same counts as before this work; none touches these files.
- `tests/` is git-ignored: `git add -f tests/test_critic_refs.py tests/test_critic_actions.py tests/test_spec_reports.py`.

## Not committed, not pushed, production prompt unchanged.
