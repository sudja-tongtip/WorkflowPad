# ONE_SHOT_VS_SEQUENTIAL (Song Critic Sequential V1)

**Answer: Sequential, as prototyped here, is NOT better than One-shot. Keep One-shot. Nothing was put into production.**

| | A One-shot (production) | B Sequential (prototype) |
|---|---|---|
| System prompt | production, about 1,806 est. tokens | short, about 309 est. tokens |
| Knowledge / questions | all in the system prompt | in the task message of each step (about 60-120 words of reading tools per step) |
| Requests per song | 1 | 9 (one session) |
| Session | none | Gemma: real server-side session; Grok: full history replayed (both continuing, no summaries) |
| Output | production Song Critic report (verdict, findings, sections) | numbered tags per step (conclusion, status, reasoning, uncertainty, action, keep, evidence) + Step 9 final with recalls, revisions, <= 3 actions |
| Citations unverified (script) | 1 of 12 items (8 %) | 0 of 46 (0 %) |
| S1 (pretty, does not develop): sees the problem? | Gemma yes (softly), Grok yes (clearly) | **Gemma no, Grok no** (all steps strength; Final "high success") |
| S3 (atmosphere, intended repetition): avoids a false high concern? | **No in both** (high-priority concerns on development / repeated chorus) | **Yes in both**; Grok explains why the device works |
| Distinguishes the two songs | yes | **no**: 30 of 32 aspect steps are `strength`; both songs end "succeeds highly" |
| Replacement lyrics in actions | 2 of 4 reports contain sample wording | 1 of 4 (Gemma S1) |
| Latency per song | 11-17 s | 47-88 s (3.2-8.0x) |
| Grok input tokens per song | about 2.2-2.3k | about 44-48k (about 20x) |

Why it is not adopted (the pre-registered rule): judgment was worse on S1 in both backends (the one song with a real weakness), better on S3 in both, so not "better in at least two cells and worse in none"; a Final Review dropped its own Step 4 `mixed` without a revision; one latency above 6x; replacement wording in one action. Better citation accuracy and the clean per-step evidence format are real and worth keeping in mind.

What the test does NOT show: that a different sequential design cannot work. Candidates for a later, separately pre-registered trial (not done here): ask each step for the strongest counter-argument to its own `strength`; give the final step the raw lyrics' repeated-line list again; score S1/S3 pairs as a pair so that a prompt that praises everything cannot win; add a cheap one-shot "global diagnosis" first and use the steps only to examine the aspects it flags.
