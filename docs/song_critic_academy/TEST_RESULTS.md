# TEST_RESULTS (Song Critic Academy V1)

## Separation: real vs control tree vs fake model
| kind | what | result |
|---|---|---|
| **Unit (pure)** | `tests/test_academy.py` section K (30 checks): the 16 lesson files, format, topic ids, sources, install / edit / upgrade / invalid / missing / delete in temporary folders, no LLM call, production prompt unchanged, the hand-off builder and its hard budget | pass |
| **Harness (control tree, MOCK; not pixels)** | section U (14 checks): the page with no report, with an older report, with a Songcraft report, five-aspect report, English UI, every lesson file missing, layout widths, buttons | pass |
| **Harness with a FAKE model** | section I (13 checks): Discuss a technique / lesson, no report, older report, Outdated, no-room refusal, no backend | pass |
| **Existing suites on the same code** | `test_songcraft` 55/55 (adapted), `test_critic_refs` 62/62, `test_critic_actions` 59/59, `test_spec_reports` 108/108 | pass |
| **Clean base** | the task-only patch on clean HEAD + the V3 task patch: `git apply --check` (no --recount) and the five suites above | pass (`PATCH/clean_base_check.txt`) |
| **Real Flet** (web mode, temporary profile, browser pane) | the Academy list with no report; a lesson and a technique opened; the lesson block inside a Songcraft report tile; an older-format report with the list; user-edited file and an upgraded file after a restart; 320 / 360 / 560 px; the English UI at 320 | captured (SCREENSHOTS/) |
| **Real backend** | one real Songwriter request through the app with local Gemma: Discuss "Question Opening" of the Opening lesson on a song with no report | worked; see SONGWRITER_HANDOFF_TEST.md |
| **Full regression** (64 test files) | `regression_all.log` | the failing set is identical to the run made before this task (Song Critic V3): `basic_caption_builder` (4), `caption_intelligence` (2), `cue_catalog` (5), `music_studio_quality`, `prompt_modes`, `rhyme_flow`, `rhyme_map`, `rhyme_v6`, `songwriter_judgment`; the files that print no verdict line print none as before; only timing lines differ; `test_academy` is the only new line. Songwriter chat suites (`test_natural_chat`, `test_chat_selection_preview`, `test_songwriter_tools`: ALL PASS) and Music Producer-related ones (`test_producer_settings`) have the same status as before. |

## What the Knowledge tests prove (K)
K1 ids = the eight aspect keys · K2 all 16 lessons valid · K3 seven sections in the standard · K4 Opening: the eight patterns, each with four lines (who it suits / expectation / strength and limit / what next) · K5 "a learning grouping, not an academic classification" · K6 technique counts · K7 references and `[Rn]` consistency · K8 sources vs synthesis vs examples separated · K9 Thai examples, no long copied text · K10 Hook says melody cannot be judged from lyrics · K11 Repetition separates intention from effect · K12 Imagery / Section Purpose / Ending cautions · K13 no LLM call · K14 production prompt = HEAD, no academy import in specialist modules · K15-K24 install, edit, upgrade, invalid, UTF-8, deleted, missing, English fallback, no song dependence · K25-K30 the hand-off.

## Not tested
See KNOWN_LIMITATIONS.md items 5-7: the frozen build, Flet desktop, a real lesson discussion of a song WITH a Songcraft report, an Outdated report, Grok, a real tight-context refusal, and Preview/Apply/Undo of a change coming from a lesson discussion in the real app. No LLM quality audit was run (the brief says none: no prompt changed).

Raw outputs: `TEST_OUTPUT/` (five suites), `regression_all.log`.
