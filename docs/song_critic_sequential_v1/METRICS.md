# METRICS (script output; no quality judgment)

| cell | arm | requests | latency s | in tok (est) | out tok (est) | in/out tok (reported) | evidence items | unverified | rate |
|---|---|---|---|---|---|---|---|---|---|
| TestSong01_gemma | A one-shot | 1 | 15.0 | 2516 | 1624 | None/None | 4 | 1 | 25% |
| TestSong01_gemma | B sequential | 9 | 54.2 | 6688 | 5064 | None/None | 15 | 0 | 0% |
| TestSong01_grok | A one-shot | 1 | 11.8 | 2516 | 716 | 2332/700 | 2 | 0 | 0% |
| TestSong01_grok | B sequential | 9 | 47.8 | 6688 | 3102 | 47651/3064 | 15 | 0 | 0% |
| TestSong03_gemma | A one-shot | 1 | 16.6 | 2410 | 1884 | None/None | 4 | 0 | 0% |
| TestSong03_gemma | B sequential | 9 | 53.1 | 6583 | 5323 | None/None | 8 | 0 | 0% |
| TestSong03_grok | A one-shot | 1 | 11.0 | 2410 | 731 | 2196/701 | 2 | 0 | 0% |
| TestSong03_grok | B sequential | 9 | 87.5 | 6583 | 2875 | 43727/2665 | 8 | 0 | 0% |

- TestSong01_gemma A unverified: FINDING.2: unreadable line reference: ['L005/L016']
