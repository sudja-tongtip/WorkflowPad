# UI_IMPLEMENTATION (Song Critic Academy V1)

Everything is inside the existing Song Critic page of the right panel: no new specialist, no new icon, no new tab. The code is in `music_studio.py` (functions `ac_lang`, `ac_lines`, `ac_section`, `ac_technique_tile`, `ac_content`, `ac_block`, `ac_list`, `ac_discuss`; the module is imported as `AKB` because `AC` is already `lyric_actions` in that file).

## Where a lesson appears
| situation | what the page shows |
|---|---|
| **A Songcraft report exists** (the 8-aspect page) | each of the eight aspect tiles ends with ONE lesson block "Learn songcraft principles", collapsed. It sits below the AI finding (and its own Discuss button), tinted, with a legend. The aspect the Song Critic did not review still has its lesson and says "not reviewed in this report"; no analysis text is invented. |
| **No report yet** (opening the tab asks nothing) | the Song Critic header and the question, then **Songwriting Academy · 8 lessons**: eight collapsed tiles `01 · Opening Review` ... `08 · Ending & Payoff`, each with a "Lesson" chip and the lesson title. A note says these are reference knowledge, not an analysis of the lyrics, and that no AI was asked. No song open: the same list under "Open or create a song first". |
| **An older-format report** (what the unchanged production prompt makes today) | the report as before (findings, story arc), then the same Academy list under it, before the technical diagnostics. |
| **An unreadable answer** | the message as before, then the Academy list. |

Why the list exists: the production prompt is unchanged (brief section 8) so a normal Analyze still produces the older report, which has no aspect tiles. Without the list the lessons would show only for a report no one can produce today. The brief's own mockup (lesson inside the aspect tile) is implemented for Songcraft reports.

## Inside a lesson
Labels follow the UI language (`th.json`); the text comes from the Markdown file: legend (reference knowledge, not AI opinion; `[R1]` meaning; untagged = our synthesis; examples written for teaching) -> What It Is -> Why It Works -> How to Use (introduction) -> **Techniques** (one collapsed tile per technique; opening shows its eight patterns by name; expanding shows who it suits / the expectation / strength and limit / what to do after; each has "Discuss this technique with Songwriter") -> Examples (green label) -> Common Pitfalls (amber label) -> Try It Yourself -> References (each with "Source checked" or "General knowledge, not a citation", what it supports, and the URL) -> **Discuss this lesson with Songwriter** -> `Lesson version 1.0.0 · bundled/user`.

## Requirements
| requirement | how |
|---|---|
| Academy opens/closes separately in each aspect | its own `ExpansionTile` per aspect (`rep_open["ac:<topic>"]`), technique tiles `ac:<topic>:<technique>` |
| default collapsed | lesson blocks, list tiles and technique tiles are all collapsed until opened; remembered after a redraw (U3, U4) |
| readable before Analyze / with no report | `ac_list()` (U1); lessons make no request |
| no fake analysis for an aspect with no AI analysis | the lesson never prints an AI status; "not reviewed" comes from the report code as before (U2, U14) |
| UI language Thai / English, lyrics and examples stay Thai | headings translated by `th.json` (26 keys added), the lesson text in the language of the UI (English lesson files exist); Thai lyric examples are inside the English lessons too (U8; real app: `320_english_ui_lesson`) |
| 320 / 360 / 560 px | no fixed width above 280 px, rows wrap; real Flet at 320, 360 and 560 (SCREENSHOTS) |
| keep scroll, expand/collapse, selected state | the same `rep_open` dict and `sr_render` as the report page; the report redraws keep every opened lesson |
| no duplicate buttons | AI-finding Discuss buttons are unchanged (V3 test B8, exact count); lesson buttons are one per lesson (8) and one per technique (47), counted by U6/U13 |
| text does not overflow or overlap | checked in real Flet at 320 / 360 / 560 (screenshots); the earlier Flutter pitfalls were kept in mind (no wrapping Row with an expanded child) |

## Differences from the mockup in the brief
- The mockup puts "AI วิเคราะห์เพลงนี้" first and the lesson second inside the tile: that is the order used. The mockup lists three techniques and "see all 8"; we list all techniques as collapsed tiles (the title row of each is one line), which keeps the lesson short until opened.
- The mockup's "Discuss this with Songwriter" is a button at the end of the lesson plus one per technique.
- A lesson with no AI finding in a Songcraft report: the tile says what it did before ("not reviewed ..."), and the lesson block is still there.
- Added beyond the mockup: the Academy list for pages with no Songcraft report (see above).

## Found while testing in the real app
- A stale browser pane drew the page at a smaller size and sent clicks to the lyrics editor; reloading and resetting the viewport fixed it (a pane issue, not an app issue).
- In an earlier V3 screenshot a duplicate Discuss button appeared in expanded aspect tiles; that fix is in the tree and the corrected page was re-captured here (single AI Discuss button in `320_songcraft_tile_with_collapsed_lesson` and `360_songcraft_report_aspect_with_lesson_block`).
