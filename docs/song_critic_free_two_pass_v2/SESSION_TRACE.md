# SESSION_TRACE (every request, in order)

Source: `SESSION_TRACE.jsonl` (written by `run_fr.py`); full requests/responses are in `RAW_REQUESTS/` and `RAW_RESPONSES/` (system prompt, user/task message, and for Grok the whole messages array that was sent).

**Session type.** Gemma: a REAL continuing session (one ComfyUI LLMSessionChatNode session id per song, the same id for Pass 1 and Pass 2, 'session 1 opened' only; `max_turns` 200, no summarizing). Grok: stateless API, so the client replays the Pass 1 user message and the Pass 1 answer with Pass 2 (the messages array of `TestSong01_grok_twopass_pass2_request.json` is: system, user (song + task 1), assistant (Pass 1 answer), user (task 2)). Neither backend used a summary.

| # | arm | pass | song | backend | session id | history | est in (this msg) | est out | reported in/out | s | ctx est used after | error |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | A | - | 01 | gemma | none | none (a single request) | 2516 | 1551 | -/- | 12.4 | - | - |
| 2 | A | - | 03 | gemma | none | none (a single request) | 2410 | 1706 | -/- | 14.0 | - | - |
| 3 | A | - | 01 | grok | none | none (a single request) | 2516 | 834 | 2332/814 | 31.9 | - | - |
| 4 | A | - | 03 | grok | none | none (a single request) | 2410 | 840 | 2196/777 | 38.9 | - | - |
| 5 | B | 1 | 01 | gemma | music_504019ca | server-side session (ComfyUI LLMSessionChatNode):  | 867 | 1556 | -/- | 10.9 | 2423 | - |
| 6 | B | 2 | 01 | gemma | music_504019ca | server-side session (ComfyUI LLMSessionChatNode):  | 233 | 1339 | -/- | 9.0 | 3995 | - |
| 7 | B | 1 | 03 | gemma | music_8e36be93 | server-side session (ComfyUI LLMSessionChatNode):  | 762 | 1598 | -/- | 12.0 | 2360 | - |
| 8 | B | 2 | 03 | gemma | music_8e36be93 | server-side session (ComfyUI LLMSessionChatNode):  | 233 | 1252 | -/- | 9.4 | 3845 | - |
| 9 | B | 1 | 01 | grok | none (stateless API; history replayed by the client) | none (first request of the session) | 867 | 584 | 1084/546 | 25.0 | - | - |
| 10 | B | 2 | 01 | grok | none (stateless API; history replayed by the client) | full replay of 2 earlier messages (the Pass 1 user | 233 | 521 | 2165/474 | 21.0 | - | - |
| 11 | B | 1 | 03 | grok | none (stateless API; history replayed by the client) | none (first request of the session) | 762 | 568 | 948/519 | 15.7 | - | - |
| 12 | B | 2 | 03 | grok | none (stateless API; history replayed by the client) | full replay of 2 earlier messages (the Pass 1 user | 233 | 510 | 1933/448 | 11.3 | - | - |

**Did Pass 2 use Pass 1? (read)** Yes in all four sequences: the Pass 2 answers refer back to Pass 1 (for example Gemma S1: it earlier worried about predictability and now finds it intended; Grok S1: it repeats the line it cited in Pass 1) and use lines of the lyrics that were sent only in Pass 1. Both the lyrics and the Pass 1 critique were available to Pass 2 in both backends.

**Context:** no truncation, summarization, second session, timeout or ComfyUI hang. `ai_calls.log` in AppData was NOT written (the helper's log function was switched off in the process).

**Errors:** none.
