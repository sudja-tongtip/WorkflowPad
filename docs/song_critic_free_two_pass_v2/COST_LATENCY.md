# COST_LATENCY (Free-Thinking Two-Pass V2)

Source: `METRICS.md`, `SESSION_TRACE.md`. Latency is wall-clock per request as run (local Gemma; Grok API, whose speed varied: the same one-shot took 11-12 s in the earlier test today and 32-39 s here).

| cell | A req / s | B req / s (Pass 1 + Pass 2) | B / A latency | A tokens | B tokens |
|---|---|---|---|---|---|
| S1 Gemma | 1 / 12.4 | 2 / 19.9 (10.9 + 9.0) | 1.6x | est in 2,516, out 1,551 | est new input 1,100 (Pass 1 867, Pass 2 233), out 2,895 |
| S3 Gemma | 1 / 14.0 | 2 / 21.4 (12.0 + 9.4) | 1.5x | est in 2,410, out 1,706 | est new input 995, out 2,850 |
| S1 Grok | 1 / 31.9 | 2 / 46.0 (25.0 + 21.0) | 1.4x | reported in 2,332, out 814 | reported in 3,249 (1,084 + 2,165), out 1,020 |
| S3 Grok | 1 / 38.9 | 2 / 27.0 (15.7 + 11.3) | 0.7x | reported in 2,196, out 777 | reported in 2,881 (948 + 1,933), out 967 |

- Grok input tokens: 1.3-1.4x the one-shot (the Pass 1 messages are replayed in Pass 2; the production system prompt is about 1,806 estimated tokens, the minimal one about 142). Output 1.2-1.3x.
- Gemma output tokens about 1.7-1.9x (longer prose); the node keeps the session, so the new input is small. Context use after Pass 2: about 4.0k (S1) and 3.8k (S3) estimated tokens of 19,000 (about 21 %), far below the 65 % guard.
- Requests: 2 per song instead of 1. No truncation, summarization, timeout or error; every Grok answer ended with finish_reason `stop`.
- The lyrics are sent once per song (Pass 1). The system prompt goes with each request, as the API and the node need it.
- Compared with the 9-step sequential test (latency 3.2-8.0x, Grok input about 20x) this design costs little. Part of the latency difference between cells is API noise.
