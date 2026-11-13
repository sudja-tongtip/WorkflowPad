# AUDIT_REPORT: Song Critic Sequential Prototype V1

Status: prototype + A/B + audit done in one pass. **Production untouched, nothing committed, not adopted.**

## Outcome
Asking the Song Critic one aspect at a time in a continuing session (Gemma: real session; Grok: history replayed) did **not** give better criticism than the current one-shot. It cited lines and quotes more accurately (0 of 46 unverified vs 1 of 12) and avoided the false high-priority concerns the one-shot raised on the atmosphere song (S3), but it stopped seeing the lack of development in the weak song (S1) on both backends, rated 30 of 32 aspect steps a strength on both songs, and cost 3.2-8.0x the latency (Grok input tokens about 20x). By the rule fixed before the run: **keep One-shot.**

## What was done
- `steps.py`: one short system prompt (about 309 est. tokens) and nine step prompts (reading tools + question + compact tag format); Step 9 may write [REVISION], must write recall_1..3 (checks that Steps 1-3 are still available) and at most 3 actions.
- `run_seq.py`: 4 one-shot requests (production prompt and the same user message production builds) and 36 step requests, temperature 0.4, no retries; every request, response and trace recorded.
- `evaluate.py`: the same mechanical check of every `lines` + `quote` for both arms; tokens, latency; no quality judgment by script.
- Pre-registration (sha256 + time 12:34:15) of the expected observations, the measures and the decision rule before the first request.

## Session honesty
Gemma: a real continuing session (one ComfyUI session id per song, same id on all nine steps, 'session 1 opened' only). Grok: stateless API, full message history replayed at each step. Neither is a summary-based carry. The Gemma sessions used about 62 % of the 19,000-token context by the app's estimate (guard 65 %), so a longer song would have stopped the run safely. No truncation, summarization, timeout or ComfyUI hang. Step 9 recalled Steps 1-3 correctly in all four sequences.

## Files
`ONE_SHOT_VS_SEQUENTIAL.md` (the answer) · `QUALITY_COMPARISON.md` · `COST_LATENCY.md` · `SESSION_TRACE.md` (+ `.jsonl`) · `SYSTEM_PROMPT_SHORT.md` · `STEP_PROMPTS.md` · `KNOWN_LIMITATIONS.md` · `METRICS.md/.json` · `RAW_REQUESTS/` (40) · `RAW_RESPONSES/` (40) · `PARSED_STEPS/` (per-step conclusions/status/evidence checks) · `FINAL_REPORTS/` (Step 9 and one-shot reports as answered, unedited) · `run.log` · scripts and `PRE_REGISTRATION.txt`, `EXPECTED_OBSERVATIONS.md`.

## Deviations to know about
1. The checker was fixed once after the first look at the numbers (Gemma's `[DONE]<end_turn>`); disclosed in KNOWN_LIMITATIONS 7. Prompts, models and raw answers were not touched.
2. The Gemma helper appended 20 lines to the app's `ai_calls.log` in AppData (KNOWN_LIMITATIONS 10).
3. Grok S3 Step 9 ended without `[DONE]` (not retried).
