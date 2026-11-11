# PARSER_REGRESSION — Task A

Suite: `tests/test_critic_refs.py` — **62 / 62** (pure; `TEST_OUTPUT/test_critic_refs.txt`). Contract: `docs/specialist_reports/REPORT_TAG_CONTRACT.md`.

## Reading a `lines:` field
| Input | Result | Input | Result |
|---|---|---|---|
| `L005` | one reference (5) | `verse 2` | invalid |
| `L005-L008` | one reference (5-8) | `12` (bare number) | invalid |
| `L005, L016` | two references, **not** 5-16 | `L12-13` | invalid |
| `L005, L016-L018` | two references (5; 16-18) | `L13-L12` (descending) | invalid |
| `L5,L16`, `L5; L16`, `l5, l16-l18` | the same, case / spacing tolerated | `L12 and L14`, `lines 12 and 13`, `L5 L16`, `section 3, L5` | invalid |
| `L011, L023, L035` | three references | `L99` / `L5, L99` (a line that does not exist) | invalid (the whole field) |
| `L5, L5` | one (kept once) | empty, `-`, `whole song` | no line (not an error) |
Each part must be exactly a reference; one bad part makes the whole field invalid (no part is salvaged: nothing is guessed). `parse_lines` (used by the one-range `SECTION` field) is unchanged: `L5, L16` is NOT a section reference.

## What the parser does with a finding
| Case | Result |
|---|---|
| valid references | `refs` kept as written; `first/last` = the first reference; shown quote = each place's own lines with a gap mark; the model's quotation checked against the union (a line that is only BETWEEN two places does not verify it) |
| invalid (Song Critic) | **finding kept**, text intact; `refs = []`, `lines_invalid = <what was written>`, quotation = the AI's own words, `quote_ok = False`; diagnostic "…not a readable reference… kept WITHOUT a line link and its quotation is not verified" (`verse 2`, `L5-L99`, `L005, L999`, `12`, `L005 and L016` all tested) |
| invalid (Rhyme & Flow, Vocal Flow, Lyric Language) | not used, with a diagnostic (unchanged) |
| no line (Song Critic) | kept as before |
| `proposal:` | only for ONE reference, same number of lines, no tag / line id, different from the original; otherwise dropped with a diagnostic |

## Audit 01 / Tune 02 raw answers re-parsed with the new parser (all 18 answers; test lines `raw answer …`)
| Answer | Findings written | Kept before | Kept now |
|---|---|---|---|
| Audit 01 S1 Gemma (`L005, L016`, `L011, L023, L035`, `L002, L003`, `L028, L029`) | 4 | 0 | **4** (refs `[[5,5],[16,16]]`, `[[11,11],[23,23],[35,35]]`, …; all quotes verbatim) |
| Audit 01 S1 Grok (`L5,L16`) | 2 | 1 | **2** |
| Tune 02 Test Song 01 Gemma candidate (`L005, L016`) | 4 | 3 | **4** |
| Tune 02 Test Song 01 Grok baseline (`L5,L16`) | 2 | 1 | **2** |
| Tune 02 Test Song 02 Grok candidate (`L12, L28`) | 2 | 1 | **2** |
| the other 13 answers | – | all | all |
V2 Task B answers (16): 16 of 16 kept, 9 multi-reference findings.

## Legacy reports and presentation
A report saved before this change has `first/last` and no `refs`: it renders with one chip (`sr_refs_of`), can be handed off to the Songwriter, and its state / persistence are unchanged (`SCHEMA` not bumped; `test_critic_actions` L2). The Audit-01 display defect (a one-line finding shown as 10-11 lines because the parser accepted `L005-L016`) cannot recur for lists: separate places are shown separately; a range written by the model is still shown as a range.

## Not covered
Multi-reference for Rhyme & Flow / Vocal Flow findings is parsed (same function) but their pages were not given new tests beyond the V1 suite; a `lines:` field mixing a range and a list in the SECTION block is not supported (sections keep one range).
