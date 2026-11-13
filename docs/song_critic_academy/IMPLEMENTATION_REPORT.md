# IMPLEMENTATION_REPORT: Song Critic Academy V1

Status: implemented and tested; **not committed** (as instructed). Production prompt, parser, specialists, Gemma/Grok settings, sessions and the Music Producer are unchanged; no LLM request is made to show a lesson.

## Outcome
The Song Critic page now has a Songwriting Academy: eight editable Markdown lessons (Opening, Central Idea, Section Purpose, Development, Hook, Imagery, Repetition, Ending), in Thai and English, with sources kept apart from our synthesis and from teaching examples. A lesson opens inside each aspect tile of a Songcraft report (collapsed by default), and as a list of eight lessons when there is no report or an older-format report, so it can be read without pressing Analyze. "Discuss this lesson / technique with Songwriter" uses the existing hand-off (same session, hard token budget, Outdated labels, no automatic lyric change) and sends only the selected lesson. A real Songwriter request through the real app worked.

## Brief vs result
| brief | result |
|---|---|
| 8 lessons of quality, the 8 opening patterns with suits / expectation / strength and limit / what next | done (`SONGCRAFT_KNOWLEDGE/`); K4 checks the four lines per pattern |
| fixed lesson structure (What it is ... References), natural, no repeated generic text | seven fixed sections; every lesson has its own techniques and examples |
| Thai examples written for teaching, comparative | original Thai lines; compare-and-contrast sets (for example the same idea opened in eight ways) |
| research and verified sources, three kinds kept apart, no claim of academic theory for our own groupings | RESEARCH_REFERENCES.md; `[Rn]` tags only for what a checked page says; our groupings are `general` |
| editable UTF-8 Markdown, stable topic ids, no hard-coding, user edits kept, missing/invalid safe, version and sources, no song dependence | KNOWLEDGE_ARCHITECTURE.md; tests K15-K24; shown in the real app |
| UI inside the 8 aspect tiles, collapsed, readable without AI, no fake analysis, TH/EN, 320/360/560, scroll/expand state | UI_IMPLEMENTATION.md; U1-U14; real Flet screenshots |
| Discuss: topic id and title, technique, example/exercise, AI finding if any, lyrics, state; Songwriter session, budget guard, Outdated, no auto-edit | `ac_discuss` + `AKB.handoff_text`; K25-K30, I1-I13; one real request |
| LLM safety: no prompt change, no audit, no new requests to show lessons, other specialists untouched | K14, I13, U1; the diff touches none of them |
| tests: unit / UI / integration, real vs mock separated | TEST_RESULTS.md |

## One deviation to know about
The Academy list under older-format reports and with no report is an addition to the brief's mockup. It is needed because the production prompt (kept unchanged on purpose) still makes the older report, which has no aspect tiles. The in-tile lesson of the mockup is implemented for Songcraft reports.

## What to review
1. The lesson content (a human read of the Thai wording, the examples and the claims; KNOWN_LIMITATIONS 1-3).
2. The references: they were checked at page/listing level only (RESEARCH_REFERENCES.md).
3. The UI choices in UI_IMPLEMENTATION.md (the Academy list, button labels).

## Files
CHANGED_FILES.md · PATCH/ (task-only patch, new files, clean-base check) · SONGCRAFT_KNOWLEDGE/ (the 16 lessons + README) · SCREENSHOTS/ (real Flet) · TEST_OUTPUT/ · REAL_HANDOFF_PAYLOAD.txt, REAL_SONGWRITER_ANSWER_RAW.json
