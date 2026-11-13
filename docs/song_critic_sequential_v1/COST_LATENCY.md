# COST_LATENCY (Song Critic Sequential V1)

Source: `METRICS.md` / `METRICS.json` (script) and `SESSION_TRACE.md`. Latency is wall-clock per request as run (local Gemma on this machine, Grok API).

| cell | A requests | A s | B requests | B s (sum) | B / A latency | A in+out tok (est) | B out tok (est) | B input tokens |
|---|---|---|---|---|---|---|---|---|
| S1 Gemma | 1 | 15.0 | 9 | 54.2 | 3.6x | 2,516 + 1,624 | 5,064 | est 6,688 of new messages; the node re-reads the session history (est 11.7k tokens in context at Step 9) |
| S3 Gemma | 1 | 16.6 | 9 | 53.1 | 3.2x | 2,410 + 1,884 | 5,323 | est 6,583; context est 11.9k at Step 9 |
| S1 Grok | 1 | 11.8 | 9 | 47.8 | 4.1x | 2,332 + 700 (reported) | 3,064 (reported) | **47,651 reported** (history replayed) vs 2,332 |
| S3 Grok | 1 | 11.0 | 9 | 87.5 | 8.0x | 2,196 + 701 (reported) | 2,665 (reported) | **43,727 reported** vs 2,196 |

- **Requests:** 9x per song. **Grok input tokens: about 19-20x** the one-shot (the full history is sent again at every step); output tokens about 3.8-4.4x. Grok reasoning tokens were reported (e.g. 720 in Step 1 of S1) and are included in the reported completion numbers.
- **Gemma:** the node keeps the history server-side, so the new-message tokens are small, but the model still processes the growing context. Total output about 3x the one-shot's; the estimated session use reached 11.7-11.9k of the 12.35k allowed by the guard (about 62 % of ctx_local 19,000).
- **System prompt:** 309 estimated tokens (short) vs 1,806 (production), but each step message adds a format block (about 120-250 tokens) and Step 1 carries the song; B's total new input per song is 2.6x the one-shot's before counting history.
- **Cost of the quality?** None of this bought better judgment on the song with a real weakness (S1); see `QUALITY_COMPARISON.md`. The 6x latency bound of the pre-registered rule was exceeded once (S3 / Grok: 8.0x; one step took 15 s, Step 9 12.6 s).
