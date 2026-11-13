# FINAL_REPARSE_RESULTS (Song Critic V3)

Final parser: `music_studio/spec_reports.py` sha256[:12] `f33663c76673` (with `_ASPECT_ALIAS`). The 10 raw candidate answers were parsed again by `final_reparse.py`.
`run.log` / `run_parser_v1.log` and the first version of QUALITY_COMPARISON.md used the FIRST parser (before the aliases): Gemma 6/5/6/6/7 aspects. **Those counts are superseded**; they stay in the package only as history.

| song | backend | aspects | all 8 in order | heading-only | quote_ok=false | invalid lines | actions | high concern/mixed | statuses | s base | s cand |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 01 | gemma | 8 | True | 0 | 2 | 0 | 3 | - | ope:stre the:stre sec:stre dev:no_i hoo:no_i ima:stre rep:stre end:stre | 19.8 | 33.3 |
| 01 | grok | 8 | True | 0 | 0 | 0 | 1 | development | ope:stre the:no_i sec:mixe dev:conc hoo:stre ima:stre rep:mixe end:no_i | 20.1 | 37.8 |
| 02 | gemma | 8 | True | 0 | 3 | 3 | 3 | - | ope:stre the:stre sec:stre dev:stre hoo:no_i ima:stre rep:stre end:stre | 13.7 | 22.9 |
| 02 | grok | 8 | True | 0 | 0 | 0 | 1 | - | ope:stre the:stre sec:stre dev:stre hoo:stre ima:stre rep:no_i end:stre | 20.3 | 27.8 |
| 03 | gemma | 8 | True | 0 | 2 | 2 | 3 | development | ope:stre the:stre sec:stre dev:conc hoo:no_i ima:stre rep:stre end:stre | 12.0 | 25.6 |
| 03 | grok | 8 | True | 0 | 0 | 0 | 1 | - | ope:stre the:no_i sec:no_i dev:mixe hoo:stre ima:stre rep:no_i end:stre | 27.1 | 35.7 |
| 04 | gemma | 8 | True | 0 | 3 | 0 | 3 | development | ope:stre the:stre sec:stre dev:conc hoo:no_i ima:stre rep:no_i end:stre | 13.6 | 27.1 |
| 04 | grok | 8 | True | 0 | 0 | 0 | 1 | development | ope:stre the:no_i sec:no_i dev:conc hoo:stre ima:stre rep:no_i end:stre | 16.5 | 27.0 |
| 05 | gemma | 8 | True | 0 | 2 | 0 | 3 | - | ope:stre the:stre sec:stre dev:stre hoo:stre ima:stre rep:stre end:stre | 12.2 | 24.3 |
| 05 | grok | 8 | True | 0 | 0 | 0 | 1 | - | ope:stre the:no_i sec:no_i dev:mixe hoo:stre ima:stre rep:no_i end:stre | 33.1 | 42.3 |

**Condition (a), format and coverage:** all eight aspects, in order, parsed in **10 of 10** cells; heading-only aspects: max 0 in any cell. -> **PASS**.
Condition (d) evidence: `quote_ok=false` in 12 Gemma/Grok aspects (Gemma 12, Grok 0); invalid line references 5. A flagged quote means the model's quote did not match the lines it cited (ill-formed citation, placeholder such as `(Full Song)`, or a paraphrase); it is NOT proof of invented lyrics.
Runtime (seconds, mean of 5): Gemma baseline 14.26 -> candidate 26.64 (+87 %); Grok baseline 23.42 -> candidate 34.12 (+46 %). Prompt size 5,847 -> 8,905 bytes (+52 %; bytes, not tokens).
