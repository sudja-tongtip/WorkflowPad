# Specialist report tags — contract (Song Critic V2 update)

Numbered Clear Tags, one `[TAG.n]` block per item, ending with `[DONE]`. Nothing before the first tag and nothing after `[DONE]` is used. Free text is written in the REPLY LANGUAGE; tag names, field names, `-` and the lyric quotes stay as they are. The parser is `spec_reports.parse_answer` (the same for the four report specialists; the rules below are the Song Critic's unless a specialist is named).

## Blocks
`[VERDICT.1]` `headline`, `reading` · `[FINDING.n]` (below) · `[SECTION.n]` `name`, `lines` (ONE range), `reading` · `[NOTE.1]` bullets · `[DONE]`.

## `lines:` of a finding (changed in V2)
A finding's `lines:` is a list of exact references separated by commas (or semicolons):

| written | read as |
|---|---|
| `L5` | line 5 |
| `L5-L8` | lines 5 to 8 |
| `L5, L16` | line 5 **and** line 16 (two places) — never `L5-L16` |
| `L5, L16-L18` | line 5 and lines 16 to 18 |
| `L005`, `l5`, `L5,L16` | the same (leading zeros, case, no space) |
| empty, `-`, `whole song`, `ทั้งเพลง` | no line |

Each reference must exist in the lyrics and be ascending (`L13-L12` is invalid); a repeated id is kept once; the order written is kept. **Nothing is guessed:** `verse 2`, a bare `12`, `L12-13`, `L12 and L14`, `lines 12 and 13`, `L5 L16` make the whole field invalid.

What the program does:
- **valid:** the finding keeps `refs: [[5,5],[16,18]]` (and `first/last` = the first reference, for readers of older reports). The quote shown is the program's own text of each place, one after the other with a gap mark (`…`), never the lines in between. The model's quotation is checked against the union of the places; a quotation that is not there is replaced by the program's lines and noted.
- **invalid (Song Critic):** the finding is **kept** with all its text, without a line link and marked "Lines not readable: <what was written>"; its quotation is shown as the AI's own words, unverified; a diagnostic names the field. (Before V2 the finding was dropped whole.)
- **invalid (Rhyme & Flow, Vocal Flow, Lyric Language):** unchanged: the finding is not used, with a diagnostic.
- A report saved before V2 has `first/last` and no `refs`: it is read as one reference.

## `proposal:` (new, optional, Song Critic only)
A finding may carry `proposal: <<< … >>>`: one wording that would replace **the lines of that finding**. It is used only when `lines:` is exactly ONE reference (separate places cannot be replaced by one text), has the same number of lines as the original, has no `[tag]` or line id, and differs from the original. Otherwise it is dropped with a diagnostic. The shipped Song Critic prompt does **not** ask for it (the Song Critic's recommendation is a direction, never a lyric), so a real model does not write one today; the field exists so that a proposal can be previewed and applied through the Songwriter chat's own edit system (Preview / Apply / Undo). Nothing is ever applied without the user's action.

## Findings
`kind: strength | concern | keep` (`change | keep` for Lyric Language); `priority: high | medium | low` for a concern, `-` otherwise; `quote`; `observation`; `interpretation`; `recommendation`; `keep`. Field meaning for the Song Critic page: observation = what was found, interpretation = why it matters, recommendation = the suggested action, keep = the trade-off of a concern or the reason to keep the original.
