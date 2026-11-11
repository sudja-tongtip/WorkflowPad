# Song Critic — Quality Tune 02: baseline vs one candidate

2026-11-12 · 12 report runs (3 songs × Gemma local / Grok × baseline / candidate), 12 requests, **0 retries**, new request per run (the report is stateless), no audio, no other specialist. Production prompt, parser, settings and AppData **unchanged**; the candidate was injected only as the system text by `run_ab.py`. Neutral titles (`Test Song 01/02/03`); user message and context identical for both prompts and both backends. Both backends were available. Pre-registration (hashes and time before the first request): `PRE_REGISTRATION.txt`.

**How to read this report:** § 1 = facts from the raw files (countable, checkable). § 2 = my reading of the raw text (interpretation, marked as such). § 4 = what the data cannot show.

## 1. Facts (from the raw files)

### 1.1 Findings: model sent vs parser kept
| Run | Kinds written (raw) | Priority written | Raw / kept by parser | Invalid `lines:` |
|---|---|---|---|---|
| 01 Gemma baseline | 3 concern, 1 strength | high, high, medium, **high (a strength)** | 4 / 4 | – |
| 01 Gemma candidate | 2 keep, 1 strength, 1 concern | -, -, -, medium | 4 / 3 | `L005, L016` |
| 01 Grok baseline | 1 concern, 1 strength | high, - | 2 / 1 | `L5,L16` |
| 01 Grok candidate | 2 keep | -, - | 2 / 2 | – |
| 02 Gemma baseline | 2 concern, 2 strength | high, medium, medium, low (**strengths carry high / medium**) | 4 / 4 | – |
| 02 Gemma candidate | 2 strength, 1 keep | all - | 3 / 3 | – |
| 02 Grok baseline | 1 concern, 1 strength | -, medium | 2 / 2 | – |
| 02 Grok candidate | 1 strength, 1 keep | -, - | 2 / 1 | `L12, L28` |
| 03 Gemma baseline | 2 concern, 2 strength | high, high (strengths), medium, low | 4 / 4 | – |
| 03 Gemma candidate | 2 keep, 1 concern | -, medium, - | 3 / 3 | – |
| 03 Grok baseline | 1 concern, 1 strength | high, - | 2 / 2 | – |
| 03 Grok candidate | 1 keep, 1 strength | -, - | 2 / 2 | – |

- Totals: **concerns** baseline 10 (Gemma 7, Grok 3), candidate 2 (Gemma 2, Grok 0). **Findings** baseline 18 written / 17 kept by the parser, candidate 16 written / 14 kept. Parser losses: 3 of 12 runs (1 baseline, 2 candidate), all for comma lists.
- All 12 answers are complete (`[DONE]`), all quotes verbatim (checked by script: 0 not found in the lyrics), all cited line numbers exist. No invented line.
- Priority contract: baseline Gemma gave a priority to all 5 strengths (contract: `-` for non-concerns); candidate Gemma gave none. Grok never did.
- Replacement wording in a recommendation (the prompt forbids it in both versions): baseline Gemma **7 of 7** concerns; candidate Gemma **2 of 2** concerns (`"พัดพาความคิดถึงมา"` / `"ลมที่ร่ำร้องถึงใครบางคน"`; `"ฝนยังเคาะ แต่หนักกว่าเมื่อวาน"`); Grok baseline and candidate **0**.
- A recommendation with no stated what / where / why ("ลองเพิ่มรายละเอียดเล็กน้อย…"): Grok baseline in all 3 concerns; Grok candidate has no concern at all.
- Trade-off written in `keep` for a **real cost**: baseline 0 of 10 concerns; candidate 2 of 2 (Gemma 01, Gemma 03). A non-empty `keep` field: baseline 3 of 18 findings (all Grok strengths), candidate 16 of 16 (2 of them are trade-offs, the rest say why a good part stays).
- Parser display fact: baseline Gemma 01 cited `lines: L005-L016` and `L011-L023` for single repeated lines; the parser accepted the ranges and the page would show **10 and 11 lines** as the finding's text for a one-line point (`PARSED_REPORTS/TestSong01_gemma_baseline_parsed.json`).
- Time: Gemma 12.1–13.5 s both prompts; Grok 13.4–17.7 s baseline, 14.3–18.0 s candidate.
- Run-to-run variation exists: the same baseline prompt on the same songs differs from Audit 01 (Gemma 01 baseline used ranges and lost nothing now, against comma lists and 4 lost findings then; Grok 01 baseline repeated `L5,L16`; Grok 02 baseline repeated the Outro "stalls" reading).

### 1.2 What the S1 answers say (the clearest change)
- Gemma 01 baseline `reading`: "…การเดินทางทางอารมณ์ค่อนข้างวนเวียนอยู่กับความงามเดิมๆ โดยไม่มีการพัฒนา…" — names the stasis; two `high` concerns on the repeated lines.
- Gemma 01 candidate `headline`: "…ประสบความสำเร็จในการสร้างบรรยากาศที่ชวนฝันได้อย่างดีเยี่ยม"; the repeated line L005/L016 is filed as a **strength** ("ช่วยสร้างความรู้สึกของการวนเวียน… ได้อย่างมีประสิทธิภาพ"); the Bridge repetition is a `keep`.
- Grok 01 baseline `headline`: "…แต่ยังคงเป็นบรรยากาศเดียวตลอดทั้งเพลง"; `high` concern on the repeated line.
- Grok 01 candidate `headline`: "…ทำได้ดีในฐานะชิ้นบรรยากาศที่ไม่ต้องการการพัฒนาเพิ่ม"; finding 2: "การซ้ำเป็นอุปกรณ์ที่จงใจใช้เพื่อรักษาบรรยากาศ จึงควรคงไว้" — two `keep`s, no concern.

## 2. Interpretation (my reading of the raw text)

Grades are mine, from the raw answer, not from an LLM; they are shown in § 3. Where the answer had nothing to act on, Actionability and Trade-offs are `n/a`.

- **The candidate improves restraint, consistency and scope on Test Songs 02 and 03 (Grok) and on the format of Gemma's `priority`.** On 02 neither backend invents a concern any more; Gemma's baseline asks to change the lines "ผมโกรธที่พ่อมาช้า…" and "ผมไม่ได้พูดอะไรสักคำ", which are the restrained moments of the song, and the candidate leaves them alone. Grok's baseline reading of the Outro as stalling is gone. On 03 Grok's baseline `high` concern against the song's own chorus is gone and the verdict states the stillness as the character of the song.
- **The candidate loses the main problem of Test Song 01 on both backends.** The one song that has a weakness the reader should name (the mood never deepens; the same words return) is now judged as working, and both backends call the repetition effective or deliberate. In Grok 01 the model asserts "จงใจ" (deliberate) without anything in the lyrics that shows it: the new line "a device the lyrics *show* to be deliberate" gives no test for that, so it is a plausible gap in the candidate wording (not proven). The removal of "name the weakness that costs the song the most" together with the "keep is a complete result" example is the most likely cause of the shift in direction; the data cannot separate these two edits.
- **Actionability is not shown improved.** The candidate produced 2 concerns in 6 runs (both Gemma) and both recommendations contain replacement wording; Grok produced none, so there is no recommendation to grade. The brief's example format (what / where / why / trade-off) appears only in Gemma 01 candidate F4 and Gemma 03 candidate F2 (the trade-off is stated in `keep`, the "where" is the line, the "what" is "link the wind sound to the longing"), but both then give a lyric. The baseline's best recommendation is Grok 01's "เพิ่มรายละเอียดเล็กน้อยที่เปลี่ยนไปในเวิร์สที่สอง" (what: a small change; where: Verse 2; why: the picture develops), which is generic.
- **Trade-offs: improved in form, thin in sample** (2 real trade-offs in 2 concerns, against 0 in baseline).
- **Gemma 03 candidate** (mixed): it keeps `แค่` (the Audit 01 rewording suggestion is gone), but its `reading` says the song "ขาดการ 'ขยับ' ทางอารมณ์ที่ชัดเจนในท่อนกลาง … ดูเหมือนการ 'หยุด' มากกว่าการ 'ไหลไป'", treats the recurring motif "ฝนยังเคาะอยู่ที่เดิม" as returning "เร็วเกินไป", and its NOTE reads a waiting for someone into a song with no characters. It understands less of the abstract intent than Grok candidate and more than Gemma baseline in one respect (no change to `แค่`).
- **Scope:** baseline Gemma judges "telling vs showing" and writes replacement wording on every concern (Lyric Language's job); the candidate reduces this because it writes fewer concerns, not because the two it writes obey the rule.

## 3. Criteria (Pass / Partial / Fail / n/a) — Baseline → Candidate

| Criterion | 01 Gemma | 01 Grok | 02 Gemma | 02 Grok | 03 Gemma | 03 Grok |
|---|---|---|---|---|---|---|
| Understanding | Partial → Partial | Pass → Partial | Pass → Pass | Pass → Pass | Pass → Partial | Pass → Pass |
| Judgment | Partial → Partial | Partial → **Fail** | Partial → Pass | Partial → Pass | Partial → Partial | Partial → Pass |
| Actionability | Partial → Partial | Partial → n/a (nothing flagged; a weakness existed) | Partial → n/a | Fail → n/a | Partial → Partial | Fail → n/a |
| Trade-offs | Fail → Pass | Fail → n/a | Fail → n/a | Fail → n/a | Fail → Pass | Fail → n/a |
| Restraint | Fail → Partial | Partial → Partial (over-keeps) | Fail → Pass | Partial → Pass | Fail → Partial | Fail → Pass |
| Evidence | Partial → Partial | Partial → Pass | Pass → Pass | Pass → Partial | Pass → Pass | Pass → Pass |
| Consistency | Partial → Pass | Pass → Pass | Partial → Pass | Partial → Pass | Partial → Partial | Partial → Pass |
| Specialist Scope | Fail → Partial | Pass → Pass | Fail → Pass | Pass → Pass | Fail → Partial | Pass → Pass |

Reasons for the cells that changed direction are in `QUALITY_COMPARISON.md`. "Evidence: Partial" in the baseline Gemma 01 is the 10–11-line display of one-line findings; in the other Partial cells it is the parser dropping a finding that was correct in the raw answer (graded from the raw answer for every other criterion, as the brief asks).

## 4. What the data cannot show
- One run per cell. The same prompt differs between Audit 01 and this run in several details, so a one-run difference is within the noise seen here. 12 runs do not allow a rate.
- Which of the edits (removing "name the weakness", the `keep` few-shot, the "deliberate device" line, "zero concerns is valid", the second example) causes the S1 shift: all were applied together.
- Whether the candidate's "one `lines:` reference; name the other place in the observation" helps: comma lists still appeared in 2 of 6 candidate runs against 1 of 6 baseline runs; the numbers are too small to say.
- Whether a real, partly weak song (neither a clear failure nor a clear success) would be handled better: the three songs are extremes by design.

## 5. Parser / contract: multi-line references (a proposal for AFTER the audit; nothing was changed)

The comma list is what models write for "the same line at L5 and L16"; the loss is the parser's refusal of the whole finding. A follow-up that **keeps strict validation** and does not guess numbers from free text:
1. Accept a `lines:` value that is a list of references **only** in the exact shape `L<n>`, `L<n>-L<m>` separated by commas (or ` and `), nothing else in the field. Each part must exist and ranges must be ascending; any other text (`verse 2`, a bare number, `L12-13`) stays refused with its diagnostic.
2. Store it as a list of ranges (`refs: [[5,5],[16,16]]`); the finding card shows one clickable chip per range and shows the quote the model gave (verbatim-checked against **each** range, not against the span between them), so a repeated line never pulls in the lines between.
3. Keep the existing `first/last` for the first range so older consumers still read the report; the stale-link rule applies to every chip.
4. The prompt keeps saying `L12 or L12-L13`; the list is tolerated, not taught.
5. Test: `L005, L016` accepted as two ranges; `L005, L99` refused; `L5 L16` refused; the 10–11-line display of Gemma 01 baseline can no longer happen.

## 6. Recommendation (the decision is yours)
Do **not** replace the production prompt with this candidate as it stands: it trades a real gain (no manufactured concerns on the strong and the abstract song, honest `priority` field, real trade-offs) for a real loss (the weak song is judged a success on both backends). As the brief says, this is a trade-off, not a win. Which edits to keep, split or reverse is a decision for the next round; no second candidate was run and none is proposed here.
