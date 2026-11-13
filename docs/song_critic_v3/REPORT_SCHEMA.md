# REPORT_SCHEMA (Song Critic V3, format `songcraft-1`)

Parsed by `spec_reports.parse_answer(raw, "song_critic", lyrics, prompt)`; stored in the report as `data["sr"]` (the existing persistence; nothing new on disk).

## Tags (answer from the model)
`[VERDICT.1]` headline, reading · `[ASPECT.n]` x8 · `[ACTION.n]` 0-3 · `[SECTION.n]` name, lines, reading · `[NOTE.1]` · `[DONE]`.
Numbers `.n` are read as written; a missing `[DONE]`, a repeated number or an unknown tag is a diagnostic, not a crash.

## `sr` shape
```
sr = { "schema": ..., "format": "songcraft-1", "headline", "reading",
       "aspects":  [ {id, aspect, status, patterns[], priority, lines[(start,end)...], lines_invalid[...], quote, quote_ok,
                      observation, interpretation, recommendation, keep, kind} ... up to 8, ASPECTS order ],
       "actions":  [ {id, title, aspect, lines[], action, tradeoff} ... max 3 ],
       "sections": [ {name, lines, reading} ... ],   # Song Journey: the old Story Arc ribbon, reused
       "findings": [],  "notes": [..] }
```
A legacy report has no `format` and no `aspects`/`actions`; it renders with the old layout unchanged.

## Aspects (key -> title)
opening "Opening Review" · theme "Central Idea / Theme" · sections "Section Purpose / Song Journey" · development "Emotional / Narrative Development" · hook "Hook & Memorability" · imagery "Imagery & Metaphor" · repetition "Repetition & Contrast" · ending "Ending & Payoff".
Display names the model writes ("central idea", "section purpose", "song journey") are accepted through `_ASPECT_ALIAS`. Unknown aspect -> diagnostic, block dropped.

## Status
`strength` (Strength) · `concern` (Concern) · `mixed` (Mixed / Conditional) · `no_issue` (No Significant Issue) · `insufficient` (Not Applicable / Insufficient Evidence) · `unrated` (the aspect is missing or its status unreadable: "Not rated"; never forced into another status). Aliases in `_STATUS_ALIAS`. `priority` (high/medium/low) is kept only for concern/mixed.

## Opening patterns
scene, declaration, question, in_medias_res, dialogue, contradiction, hook_first, atmospheric; several allowed; unknown -> diagnostic, not used. UI note: patterns are tools for reading, not rules.

## Lines and evidence
`lines:` is a list of separate references (`L5, L16-L18`), never joined. Valid refs are checked against the lyrics; the quote is compared with the real lines (`quote_ok`). An invalid reference is **kept** and marked "Unverified / could not be read" in the UI (screenshot `560_song02_unreadable_lines_kept.jpg`); the program never replaces it with a guess.

## Limits
Aspects <= 8 (one per key; a repeat is a diagnostic) · actions <= 3 (extra ones dropped with a diagnostic) · `fields_of`: if a later repeated field is `-` the first real value is kept.

## Handoff (Discuss with Songwriter)
`handoff_text(report, finding_id, action_id)` builds a data block (report as stored, status NOW, what changed since): whole report, one aspect/finding, or one action. The Action Plan block is never cut; when the hard token budget cannot hold it the call is **refused before sending** (`None, {"refused": True, ...}`). Report text travels as data with the user's message; no Song Critic call and no lyric change.
