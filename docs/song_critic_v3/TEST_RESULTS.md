# TEST_RESULTS (Song Critic V3)

## Real vs harness (kept apart)
| Kind | What | Result |
|---|---|---|
| Unit / parser (real code, real candidate answers) | `tests/test_songcraft.py` A1-A22: statuses, aliases, patterns, limits, refs, legacy, 10 real candidate + 10 baseline answers | 55 / 55 pass (with B-F below) |
| Harness (control tree, NOT pixels) | B1-B15 page (B8 exact Discuss-button count with no duplicate per tile, B9 uses an answer that has opening patterns, B11 exactly three "not reviewed" tiles; tightened after review), expand/collapse, View topic, legacy page, restart persistence, Outdated; C1-C5 Discuss; D1-D2 Outdated discuss; E1-E2 token budget; F1-F2 widths | pass |
| Existing suites on the same code | test_critic_refs 62/62, test_critic_actions 59/59, test_spec_reports 108/108 | pass (outputs in TEST_OUTPUT/) |
| Clean HEAD worktree | task-only patch: `git apply --check` (no --recount) + the 4 suites above | pass (PATCH/clean_head_check.txt) |
| Real Flet app (web mode, temp profile, browser pane) | 360 px summary/tiles/expanded tile/Action Plan; 560 px after app restart (persistence) incl. unreadable-lines aspect; 320 px top | captured (SCREENSHOTS/) |
| Full regression, all 63 test files | regression_all.log | the failing set is identical to the V2-time run: basic_caption_builder (4), caption_intelligence (2), cue_catalog (5), music_studio_quality, prompt_modes, rhyme_flow, rhyme_map, rhyme_v6, songwriter_judgment; the others pass or print no verdict line as before. Only timing lines differ. Songwriter chat tests and Music Producer tests: same status as before. These failures pre-date this task; no old test was edited. |

## Not tested / not real
- Discuss with Songwriter clicked against a real backend; Preview/Apply/Undo on a Songcraft page (none by design; the old flow is covered by existing suites); lower part of the report at 320 px and a real "View topic" click in the browser; corrected page (after the duplicate-button fix) re-captured in the real app.
- The real-app reports were made from saved real candidate answers, not from a live production run (the production prompt is unchanged).
