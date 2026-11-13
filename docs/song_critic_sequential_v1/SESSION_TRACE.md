# SESSION_TRACE (every request, in order)

Source: `SESSION_TRACE.jsonl` written by `run_seq.py`; full requests/responses are in `RAW_REQUESTS/` and `RAW_RESPONSES/` (system prompt, user/task message, and for Grok the complete messages array that was replayed). Token numbers: `est` = the app's own `estimate_tokens` (a cautious estimate, 7-30 % above real); `rep` = reported by the Grok API. The Gemma node reports no usage.

**Session type.** Gemma: a REAL continuing session (one ComfyUI LLMSessionChatNode session id per song; the node keeps the history; `max_turns` 200, no summarizing; the same id on all nine steps; 'session 1 opened' and no second session). Grok: the API is stateless, so the client REPLAYS the full earlier messages with every step (nothing summarized). Both are the same conversation to the model; neither is an 'Explicit State Carry' summary.

| # | arm | song | backend | step | session id | history | est in (this msg) | est out | rep in/out | s | context est used (after) | error |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | A | 01 | gemma | - | none | none (a single request) | 2516 | 1624 | -/- | 15.0 | - | - |
| 2 | A | 03 | gemma | - | none | none (a single request) | 2410 | 1884 | -/- | 16.6 | - | - |
| 3 | A | 01 | grok | - | none | none (a single request) | 2516 | 716 | 2332/700 | 11.8 | - | - |
| 4 | A | 03 | grok | - | none | none (a single request) | 2410 | 731 | 2196/701 | 11.0 | - | - |
| 5 | B | 01 | gemma | 1 | music_1cec8d3b | server-side session (ComfyUI LLMSessionChatNode, | 1372 | 450 | -/- | 4.5 | 1822 | - |
| 6 | B | 01 | gemma | 2 | music_1cec8d3b | server-side session (ComfyUI LLMSessionChatNode, | 615 | 474 | -/- | 4.6 | 2911 | - |
| 7 | B | 01 | gemma | 3 | music_1cec8d3b | server-side session (ComfyUI LLMSessionChatNode, | 622 | 568 | -/- | 4.5 | 4101 | - |
| 8 | B | 01 | gemma | 4 | music_1cec8d3b | server-side session (ComfyUI LLMSessionChatNode, | 683 | 525 | -/- | 4.5 | 5309 | - |
| 9 | B | 01 | gemma | 5 | music_1cec8d3b | server-side session (ComfyUI LLMSessionChatNode, | 625 | 470 | -/- | 6.0 | 6404 | - |
| 10 | B | 01 | gemma | 6 | music_1cec8d3b | server-side session (ComfyUI LLMSessionChatNode, | 616 | 493 | -/- | 4.6 | 7513 | - |
| 11 | B | 01 | gemma | 7 | music_1cec8d3b | server-side session (ComfyUI LLMSessionChatNode, | 669 | 553 | -/- | 7.5 | 8735 | - |
| 12 | B | 01 | gemma | 8 | music_1cec8d3b | server-side session (ComfyUI LLMSessionChatNode, | 628 | 581 | -/- | 6.0 | 9944 | - |
| 13 | B | 01 | gemma | 9 | music_1cec8d3b | server-side session (ComfyUI LLMSessionChatNode, | 858 | 950 | -/- | 12.0 | 11752 | - |
| 14 | B | 03 | gemma | 1 | music_3f79f3ac | server-side session (ComfyUI LLMSessionChatNode, | 1267 | 518 | -/- | 4.5 | 1785 | - |
| 15 | B | 03 | gemma | 2 | music_3f79f3ac | server-side session (ComfyUI LLMSessionChatNode, | 615 | 547 | -/- | 4.7 | 2947 | - |
| 16 | B | 03 | gemma | 3 | music_3f79f3ac | server-side session (ComfyUI LLMSessionChatNode, | 622 | 545 | -/- | 6.0 | 4114 | - |
| 17 | B | 03 | gemma | 4 | music_3f79f3ac | server-side session (ComfyUI LLMSessionChatNode, | 683 | 587 | -/- | 4.9 | 5384 | - |
| 18 | B | 03 | gemma | 5 | music_3f79f3ac | server-side session (ComfyUI LLMSessionChatNode, | 625 | 536 | -/- | 6.0 | 6545 | - |
| 19 | B | 03 | gemma | 6 | music_3f79f3ac | server-side session (ComfyUI LLMSessionChatNode, | 616 | 567 | -/- | 6.0 | 7728 | - |
| 20 | B | 03 | gemma | 7 | music_3f79f3ac | server-side session (ComfyUI LLMSessionChatNode, | 669 | 559 | -/- | 6.0 | 8956 | - |
| 21 | B | 03 | gemma | 8 | music_3f79f3ac | server-side session (ComfyUI LLMSessionChatNode, | 628 | 609 | -/- | 7.5 | 10193 | - |
| 22 | B | 03 | gemma | 9 | music_3f79f3ac | server-side session (ComfyUI LLMSessionChatNode, | 858 | 855 | -/- | 7.5 | 11906 | - |
| 23 | B | 01 | grok | 1 | none (stateless API; history replayed by the client) | full replay of 0 earlier messages (0 steps), not | 1372 | 295 | 1497/295 | 9.2 | - | - |
| 24 | B | 01 | grok | 2 | none (stateless API; history replayed by the client) | full replay of 2 earlier messages (1 steps), not | 615 | 372 | 2767/374 | 6.5 | - | - |
| 25 | B | 01 | grok | 3 | none (stateless API; history replayed by the client) | full replay of 4 earlier messages (2 steps), not | 622 | 374 | 3656/382 | 5.6 | - | - |
| 26 | B | 01 | grok | 4 | none (stateless API; history replayed by the client) | full replay of 6 earlier messages (3 steps), not | 683 | 409 | 4585/396 | 4.8 | - | - |
| 27 | B | 01 | grok | 5 | none (stateless API; history replayed by the client) | full replay of 8 earlier messages (4 steps), not | 625 | 297 | 5456/285 | 4.1 | - | - |
| 28 | B | 01 | grok | 6 | none (stateless API; history replayed by the client) | full replay of 10 earlier messages (5 steps), no | 616 | 311 | 6207/318 | 3.8 | - | - |
| 29 | B | 01 | grok | 7 | none (stateless API; history replayed by the client) | full replay of 12 earlier messages (6 steps), no | 669 | 320 | 7002/312 | 4.2 | - | - |
| 30 | B | 01 | grok | 8 | none (stateless API; history replayed by the client) | full replay of 14 earlier messages (7 steps), no | 628 | 332 | 7762/335 | 4.3 | - | - |
| 31 | B | 01 | grok | 9 | none (stateless API; history replayed by the client) | full replay of 16 earlier messages (8 steps), no | 858 | 392 | 8719/367 | 5.3 | - | - |
| 32 | B | 03 | grok | 1 | none (stateless API; history replayed by the client) | full replay of 0 earlier messages (0 steps), not | 1267 | 248 | 1361/245 | 15.3 | - | - |
| 33 | B | 03 | grok | 2 | none (stateless API; history replayed by the client) | full replay of 2 earlier messages (1 steps), not | 615 | 301 | 2660/278 | 8.1 | - | - |
| 34 | B | 03 | grok | 3 | none (stateless API; history replayed by the client) | full replay of 4 earlier messages (2 steps), not | 622 | 361 | 3395/331 | 10.6 | - | - |
| 35 | B | 03 | grok | 4 | none (stateless API; history replayed by the client) | full replay of 6 earlier messages (3 steps), not | 683 | 381 | 4285/340 | 8.7 | - | - |
| 36 | B | 03 | grok | 5 | none (stateless API; history replayed by the client) | full replay of 8 earlier messages (4 steps), not | 625 | 282 | 5014/266 | 8.7 | - | - |
| 37 | B | 03 | grok | 6 | none (stateless API; history replayed by the client) | full replay of 10 earlier messages (5 steps), no | 616 | 311 | 5694/309 | 7.1 | - | - |
| 38 | B | 03 | grok | 7 | none (stateless API; history replayed by the client) | full replay of 12 earlier messages (6 steps), no | 669 | 293 | 6416/258 | 7.9 | - | - |
| 39 | B | 03 | grok | 8 | none (stateless API; history replayed by the client) | full replay of 14 earlier messages (7 steps), no | 628 | 307 | 7035/289 | 8.5 | - | - |
| 40 | B | 03 | grok | 9 | none (stateless API; history replayed by the client) | full replay of 16 earlier messages (8 steps), no | 858 | 391 | 7867/349 | 12.6 | - | - |

**Step 9 access to Steps 1-3 (checked):** every Step 9 answer wrote `recall_1..3`; all four sequences (2 songs x 2 backends) reproduce the conclusions of Steps 1-3 correctly in their own words (read by a person against `PARSED_STEPS/*_steps.json`), so the history was available in both backends.
**Context guard (Gemma):** the sessions reached an estimated 11,752 and 11,906 tokens of the 12,350 allowed by the 65 % guard (ctx_local 19,000). Nine steps with these songs fit, with about 4 % margin by the estimate; a longer song or longer answers would have stopped the run safely instead of opening a second session. No truncation, summarization or timeout happened in any request. No ComfyUI hang.
**Errors:** none from the backends. One format failure: `TestSong03_grok` Step 9 ended inside `[NOTE.1]` with no `[DONE]` (cause not recorded: the finish reason was not captured); it was not retried.
