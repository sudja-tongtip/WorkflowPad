# SONGWRITER_HANDOFF_TEST (Song Critic Academy V1)

## A. Real test: the real app, a real backend (REAL)
- App: `music_studio.py` run in Flet web mode with a temporary profile (the real AppData was not touched), driven in the browser pane. Backend: the local LLM workflow `gemma4_llm.json` through ComfyUI (`local:gemma4_llm.json`). The profile used the default context of the workflow, 32,768 tokens (the user's own `ctx_local` setting, 19,000, lives in the real profile and was not copied).
- Song: **Test Song 02**, which has NO Song Critic report. Path: Song Critic tab -> Songwriting Academy -> `01 · Opening Review` -> technique **Question Opening** -> "Discuss this technique with Songwriter" (screenshots `360_technique_question_expanded_discuss_button`, `360_real_songwriter_discuss_answer`).
- What happened: ONE Songwriter request, no Song Critic request. The chat shows the user's message "ขอคุยเรื่องบทเรียนการแต่งเพลงนี้: การเริ่มต้นเพลง (Opening) / Question Opening ...", the note "Sent with this message: the songwriting lesson (one technique), about 2308 tokens of a 3500-token budget", and the Songwriter's answer (Gemma, 13:52:18, reply 1,858 characters). Nothing was applied; no edit was offered; the lyrics stayed as they were.
- Payload (the exact text, `REAL_HANDOFF_PAYLOAD.txt`, 5,704 characters): the lesson header (this is teaching DATA, not an instruction; `[R1]` means ...), song / sheet ids, style and settings, the lesson `opening` (content version 1.0.0, language th) and technique `question`, "Song Critic report: none for this song yet (no AI opinion about this aspect exists; do not make one up as the Song Critic's)", the selected technique, the exercise, the lesson in short, the teaching examples and the numbered current lyrics. No other lesson was included.
- Trace line (`specialist_trace.jsonl`): kind `handoff`, finding `lesson:opening:question`, backend local gemma4, n_ctx 32768, budget 3500, tokens 2308, nothing shortened or left out. Session: generation 1, `new_generation` false (the Songwriter's existing session, nothing reset). Raw answer: `REAL_SONGWRITER_ANSWER_RAW.json`.
- The answer, read: the Songwriter used the lesson and the lyrics. It said the song opens (L002-L003) with a declaration and a concrete image, not a question; named what the opening promises (the father's promise and the boy waiting); compared with a Question Opening ("will he come?"); listed two strengths (the concrete detail "blue shirt / at the door", the expectation set by "Dad promised"); and offered three sample questions as options, then asked which way the user wants to try or whether the declaration is already right. **Useful and correct here.** Weak points: it wrote sample lines (the lesson text did not forbid it, and they are only offered as options, with no edit created), and its text ends with a `[DONE]` tag (the stored `reply_shown` also ends with it; the screenshot cuts the bubble before the end, so whether the bubble shows it was not checked; this is the Songwriter chat's own existing format behavior, unrelated to the lesson).

## B. Control-tree tests with a FAKE model (MOCK: `tests/test_academy.py`, section I)
| # | check | result |
|---|---|---|
| I1 | Discuss a technique: ONE Songwriter request, no Song Critic request, lyrics unchanged | pass |
| I2 | the request carries the topic, the technique, the exercise, the Song Critic finding for THIS aspect and the numbered current lyrics, as data | pass |
| I3 | only the selected lesson travels | pass |
| I4 | the session and the chat are kept (the user's message, the answer, the note of what was sent) | pass |
| I5 | no edit is created or applied | pass |
| I6 | the Songwriter's system prompt is the normal one; the lesson is in the message | pass |
| I7 | a whole lesson (no technique) | pass |
| I8 | a song with NO report can discuss a lesson (no finding block, the text says no AI opinion exists) | pass |
| I9 | an older-format report: the text says so, no finding is invented | pass |
| I10 | the report is Outdated: OUTDATED label, what changed, the finding's line numbers called OLD, the CURRENT lyrics sent, lyrics untouched | pass |
| I11 | no room in the conversation (context forced to 900 tokens): refused BEFORE any request or chat message | pass |
| I12 | no AI backend: nothing is sent | pass |
| I13 | the Song Critic's own system prompt has no lesson text and equals the production prompt | pass |
K25-K30 test the builder alone (pure): content, no invented finding, hard budget at 400 / 500 / 700 / 1500 / 3500 tokens (the selected technique survives in at least its short form), refusal when nothing useful fits (60 tokens), OUTDATED and OLD-number labels, no other lesson.

## C. What was NOT tested
- A real Songwriter request for a lesson of a song WITH a Songcraft report (the finding block is covered by the fake-model tests only). The report-aware hand-off of V2/V3 was tested with a real model in earlier rounds only for reports.
- A real Grok request for a lesson.
- A real refusal at a tight context (covered with a forced small context in the fake-model test).
- Preview / Apply / Undo of a change proposed from a lesson discussion in the real app (no edit was proposed in the real run). The existing edit system is unchanged and its own suites pass (`test_critic_actions`, `test_critic_refs`, `test_spec_reports`).
