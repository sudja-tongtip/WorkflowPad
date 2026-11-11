# KNOWN_ISSUES — Song Critic audit 01

Observed, not fixed (audit only). Category = the brief's four classes: 1 Model Judgment, 2 Prompt / Task Instructions, 3 Missing or Misleading Context, 4 Parser / Report Presentation.

| # | Issue | Class | Evidence | Open question |
|---|---|---|---|---|
| 1 | **A finding that cites separate lines is refused whole** (`lines: L005, L016`). S1 Gemma shows 0 findings of 4; S1 Grok loses 1 of 2. The page would show a verdict and sections but no findings. | 4 (+ possibly 2: the contract has one reference per finding, but a repetition naturally cites several places) | `PARSED_REPORTS/S1_gemma_parsed.json`, `S1_grok_parsed.json` | Change the contract (allow a short list), the parser (split a list into its parts), or the instruction? Not decided here. The strict rule was added after an earlier review against inventing a location from free text (`verse 2`). |
| 2 | **Replacement lyrics / word-level advice in spite of the rule** (Gemma, 5 of 6 concerns). | 1 | `S1_gemma` F1/F2, `S2_gemma` F3, `S3_gemma` F2/F3 | Is it instruction-following of this model, or would a different formulation help? Not shown. |
| 3 | **Uniform output size across different songs** (Gemma 2+2, Grok 1+1 always). | 1, possibly 2 (no explicit "zero concerns is fine") | all six | n = 3 per backend; needs more songs, incl. a clearly strong one, to see if the count ever changes. |
| 4 | **Generic advice** (Grok): "ลองเพิ่มรายละเอียดเล็กน้อย" in all three concerns. | 1 | `S1/S2/S3_grok` F-concern | — |
| 5 | **Misreading of what a section does** (Grok S2 Outro as stalled; Gemma S2 F1 `high` concern with a positive reading). | 1 | `S2_grok` F2 vs SECTION.7; `S2_gemma` F1 | — |
| 6 | **Intended device flagged as the top concern** (Grok S3 chorus repetition `priority: high`; Gemma S3 passivity of `แค่`). | 1 | `S3_grok` F2; `S3_gemma` F3 | Whether the prompt's "Repetition is not a flaw by itself" is read as weakly as it is written is untested. |
| 7 | **S1 is not called weak by either backend**; Gemma's headline and note praise it. | 1 | `S1_gemma` headline, NOTE.1 | — |
| 8 | Section readings are descriptive for every section on both backends. Fine for a map, but it makes "praise every section" hard to tell apart from description. | 1 / 4 | all | — |
| 9 | Gemma mixes English tags into Thai prose ("(climax)", "(Concrete Imagery)"). | 1 | `S1_gemma`, `S2_gemma` | — |
| 10 | Empty NOTE bullet (`- `) from Grok on S2. | 4 (minor; the page shows nothing for it) | `S2_grok_response1.txt` | — |
| 11 | The `priority` field is `-` for Grok strengths and "high" for a strength in Gemma; strengths carry a priority in some answers and not in others. | 1 / 2 | S1/S3 | — |

## Limits of this audit
- One run per song per backend; no repeat to measure noise (the brief caps it at 6 runs). A pattern in six answers is a hint.
- Three songs written by the auditor (an AI), each built to isolate one trait; real songs are messier.
- The criteria grades are the auditor's reading of the raw text; the brief says not to treat an LLM judge as proof, and none was used. `EXPECTED_OBSERVATIONS.md` was written after the answers were read.
- Only the Song Critic prompt as shipped (≈1 806 tokens) with its production request; no variant was tried.
- No stored report was opened in the app UI in this audit (parsed JSON only): how the user sees the #1 loss is inferred from the parser output.
- Grok received only the three synthetic songs.
