# Navigation and data flow — Specialist Reports V1

## Navigation
* `RP_TABS` gained `sr_critic`, `sr_rhyme`, `sr_vocal`, `sr_lang` (after `tools`); `RP_ICONS` gained the four Flet icons, so they sit in the far-right vertical strip (`side_strip`) in the order: Settings, Lyrics Tools, Song Critic, Rhyme & Flow, Vocal Flow, Lyric Language, then the older tabs. No horizontal tabs, no landing page.
* `rp_show_tab(k)` shows `sr_page` for these keys and calls `sr_render()`, which only reads the saved report. Collapse/expand, drawer, tooltip, selected state and the 320–560 px drawer width are the existing mechanisms (`rp_strip_open`, `rp_strip_refresh`).
* Old routes: `rp_show_tab("report")` and the `rep_lyrics` / `rep_vocal` tabs still work. The `rep_lyrics` strip icon is shown only for a song that already has a saved Songwriter report.

## Data flow of one report (Analyze / Analyze again)
1. `sr_start(spec)`: guards (busy, no backend, no sung lines, prompt invalid); captures song id, sheet id, lyrics, lyrics hash. Nothing is sent when a tab is merely opened.
2. `sr_run` (async): evidence is built in an executor — Rhyme & Flow from `python_rhyme_evidence`, Vocal Flow from `live_insights` / `live_dead`, others from the lyrics — then `SP.effective_system(spec)` (the specialist's own prompt, Custom Prompt / AppData rules) + `SR.build_user(spec, snapshot, evidence)` (numbered lyrics, `L012| …`, and only that specialist's evidence).
3. One `spec_llm` request (temperature 0.4). One retry only if the answer has no `[DONE]`.
4. `SR.parse_answer`: Clear Tags → verdict / findings / sections / notes. Refuses and reports in `diagnostics`: text outside the tags, copied format lines, closing tags (taken off), unknown tags, bad kind / priority, lines that do not exist, quotes not in the lyrics (replaced by the lyric lines), alternatives that are not a wording or equal the original, more findings than the limit.
5. `SR.make_report` → `SpecReport(data={"sr","evidence","evidence_fp","schema","partial","lyrics"})`, stored with `SE.add_report/save_session` in the specialist's session of **this song id + sheet id**.
6. Stale guard: if the song, sheet or lyrics changed while the request ran, the older answer never replaces the newer state; an error is kept per report key and never deletes an older good report.

## States (`SR.state_of`)
`not_analyzed` · `running` · `current` · `outdated` (lyrics hash differs; the old report stays readable with a notice) · `partial` (something of the answer was refused) · `error`. `dr_hooks` (called from `mark_dirty`) re-syncs the state when lyrics change; typing never calls an LLM.

## Suggestions / Apply
All four reports are read-only. Lyric Language alternatives are comparison-only; there is no Apply button. Edit proposals remain the Songwriter chat's job through the existing Preview / Apply / Undo.
