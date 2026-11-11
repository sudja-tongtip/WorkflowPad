# BASELINE vs CANDIDATE — Song Critic V2, Task B (16 runs)

Run files: `RAW_RESPONSES/TestSong0N_<gemma|grok>_<baseline|candidate>_response.txt`; parsed with the CURRENT parser (several line references supported) in `PARSED_REPORTS/`. **Fact** = countable or quotable from the raw files; **Reading** = my interpretation. Grades are my reading of the raw text (no LLM judge); the grid is at the end.

## 1. Facts

| Run | findings written / kept | kinds (c = concern, s = strength, k = keep) | priorities written | multi-reference findings | replacement wording in a recommendation | trade-off text in `keep` |
|---|---|---|---|---|---|---|
| 01 Gemma baseline | 4 / 4 | s s c c | high, high (strengths), medium, low | 1 | 2 of 2 concerns | 0 |
| 01 Gemma candidate | 4 / 4 | s s c c | – , – , medium, medium | 1 | 1 of 2 | 2 of 2 |
| 01 Grok baseline | 2 / 2 | c s | high, – | 2 | 0 of 1 | 0 |
| 01 Grok candidate | 2 / 2 | c s | high, – | 1 | 0 of 1 | 1 of 1 |
| 02 Gemma baseline | 4 / 4 | c s c s | high, high (s), medium, medium (s) | 0 | 2 of 2 | 0 |
| 02 Gemma candidate | 4 / 4 | s s c s | –, –, medium, – | 0 | 0 of 1 (examples of imagery, not lines) | 1 of 1 |
| 02 Grok baseline | 2 / 2 | s c | –, medium | 0 | 0 of 1 | 0 |
| 02 Grok candidate | 3 / 3 | s s k | all – | 1 | – | – |
| 03 Gemma baseline | 3 / 3 | c c k | high, medium, medium (k) | 0 | 1 of 2 | 0 |
| 03 Gemma candidate | 4 / 4 | s c s c | –, medium, –, low | 2 | 1 of 2 | 2 of 2 |
| 03 Grok baseline | 3 / 3 | c s c | high, –, medium | 0 | 0 of 2 | 0 |
| 03 Grok candidate | 2 / 2 | s c | –, high | 1 | 0 of 1 | 1 of 1 |
| 04 Gemma baseline | 4 / 4 | c c k k | high, medium, high (k), medium (k) | 0 | 2 of 2 | 0 |
| 04 Gemma candidate | 4 / 4 | s s c c | –, –, medium, medium | 2 | 1 of 2 | 2 of 2 |
| 04 Grok baseline | 2 / 2 | s c | –, high | 0 | 0 of 1 | 0 |
| 04 Grok candidate | 2 / 2 | s c | –, medium | 0 | 0 of 1 | 1 of 1 |

- **Parser:** 16 of 16 answers kept every finding the model wrote (0 refused). 9 findings used several separate references (`L5, L16`, `L12, L28`, …) and were all read correctly (Audit 01 lost 4 of 4 and 1 of 2 of these). Diagnostics: 7 on `02 Gemma candidate` only: its `[SECTION.n]` blocks each copied the format line "(one [SECTION.n] for each sung section, in order)" (a format slip the parser removed).
- **Evidence:** all quotes verbatim in the lyrics (`quote_ok` false in 0 of 16); every cited id exists.
- **Concerns written:** baseline 13 (Gemma 8, Grok 5), candidate 10 (Gemma 7, Grok 3). Strengths / keeps with a `priority` other than `-`: baseline Gemma 7, candidate 0 (the contract says `-`).
- **Replacement wording** (a new line or phrase offered in `recommendation`): baseline Gemma 7 of 8 concerns, baseline Grok 0 of 5; candidate Gemma 3 of 7 (01 "ความงามที่ฉันเคยพบ", 03 "ฝนจะหยุดไหม…", 04 "เสียงหัวเราะยายเงียบไปในห้อง"), candidate Grok 0 of 3.
- **Time:** Gemma 12.0–15.4 s; Grok 18.5–21.5 s (same for both prompts within noise).
- **Size:** baseline prompt 1 806 tokens, candidate 2 119 (+17 %).

## 2. The adoption rule of `EXPECTED_OBSERVATIONS.md` (fixed before the runs), applied

| Condition | Baseline | Candidate | Met? |
|---|---|---|---|
| (a) names the main real weakness in S1 and S4 (cells: 4) — at least 3 of 4 and not fewer than the baseline | S1 Gemma: **yes, weakly** (concern on the line that returns at L016, "no new change"; the same answer also calls the song a success and praises the repeated "ไร้ชื่อเรียกขาน" line); S1 Grok yes; S4 Gemma yes (Verse 2 states feelings); S4 Grok yes → **4 / 4** | S1 Gemma **no** (the returning line is a *strength*; headline "ทำได้ดีมากจนเกือบจะสมบูรณ์"); S1 Grok yes; S4 Gemma yes; S4 Grok yes → **3 / 4** | **No: 3 < 4.** If the baseline Gemma S1 answer is counted as a miss it is 3 = 3 and (a) would be met: the result is therefore close, and the rule says a close result keeps the production prompt. |
| (b) no more manufactured / against-the-aim concerns on S2 and S3 than the baseline (cells: 4) | 4 of 4 cells have one (Gemma 02 ×2, Grok 02 ×1 mild, Gemma 03 ×2, Grok 03 ×2) | 3 of 4 (Gemma 02 ×1, Gemma 03 ×2, Grok 03 ×1 high priority; Grok 02 none) | Yes |
| (c) no new regression in Evidence or Scope; replacement wording not more often | Gemma 7 of 8 | Gemma 3 of 7 | Yes (fewer); one format slip (02 Gemma candidate, 7 diagnostics) |
| (d) no `high` on a part the report calls deliberate | – | none says "deliberate" about a part with `high`; Grok 03 candidate's `high` concern asks a still song for development and, in its own `keep`, concedes "if stillness is the main purpose, keep it" | Technically yes; see Reading |

**Result: the production prompt is kept.** (a) is not met under the strict count and is a coin-flip under the lenient one; with a close result the pre-registered rule keeps the production prompt. The candidate is **not** in production.

## 3. Reading (interpretation)

- **Where the candidate is clearly better:** Grok. On Test Songs 01 and 04 its single concern is a model of what the brief asks for. 01: "ให้เวิร์ส 2 นำเสนอข้อเท็จจริงใหม่เกี่ยวกับสิ่งที่ถูกคิดถึงหนึ่งอย่าง เพื่อให้การกลับมาของคอรัสมีความหมายมากขึ้น" (what: one new fact; where: Verse 2; effect: the chorus means more) and `keep`: "การเปลี่ยนนี้อาจลดความสงบที่เป็นเอกลักษณ์ของเพลง หากความสงบคือจุดประสงค์หลักก็ควรคงบรรทัดเดิมไว้" (trade-off and when to keep the original). 04: "ให้เวอร์ส 2 นำเสนอข้อเท็จจริงใหม่เกี่ยวกับยายหรือบ้านเพียงข้อเดียว…" with the cost named. The baseline Grok's same slots read "ลองเพิ่มรายละเอียดเล็กน้อย…" with `keep: -`. On the strong song (02) the candidate Grok finds nothing to fix and says so (no Outro misreading, no invented concern).
- **Where it is mixed:** Gemma. It writes trade-offs in `keep` for every concern (2 of 2, 1 of 1, 2 of 2, 2 of 2) and stops giving a priority to strengths, and it writes fewer concerns that contradict its own reading (baseline: 02 `high` concern L020 whose interpretation is positive; 03 `high` concern whose interpretation says the details make the song more interesting). But it still offers new wording in 3 of 7 concerns, it over-praises (01: "ทำได้ดีมากจนเกือบจะสมบูรณ์"; 04: "ประสบความสำเร็จอย่างมาก"), and on 01 it turns the problem of the song into a strength.
- **Test Song 03 (atmosphere) is not solved by either prompt.** Baseline: both backends ask for development or will-driven action (Gemma `high`; Grok `high`). Candidate: Grok's one concern is `high` ("Verse 2 and the Bridge add images but do not change the meaning of the silence") with a trade-off admitting it may be wrong; Gemma's concerns ask for a change in "relationship or feeling" and offer a lyric for the Bridge (the one question the expectation file allowed: the Bridge states the absence outright). The "intent is not success" line pushed the candidate toward judging stillness by the yardstick of change.
- **Test Song 04: neither prompt, on either backend, found the unused Bridge letter** as a weakness (baseline Gemma and candidate Grok even praise it; baseline Grok notes in a NOTE only that "the song has no development after the Bridge"). Both found Verse 2's telling. Candidate Gemma added a manufactured concern on "บ้านขายไปแล้วก็ช่าง".
- **Keep Original / trade-offs:** all 25 candidate findings have a non-empty `keep` (Gemma 16 of 16, Grok 9 of 9); all 10 candidate concerns carry a trade-off or a condition for keeping the original (Gemma 7 of 7, Grok 3 of 3). Baseline: Gemma 0 of 15 findings, Grok 4 of 9 (strengths only); none of the 13 baseline concerns.

## 4. Criteria grid — Baseline → Candidate (Pass / Partial / Fail / n/a)

| Criterion | 01 Gemma | 01 Grok | 02 Gemma | 02 Grok | 03 Gemma | 03 Grok | 04 Gemma | 04 Grok |
|---|---|---|---|---|---|---|---|---|
| 1 Understanding | Partial → Partial | Pass → Pass | Pass → Pass | Pass → Pass | Pass → Partial | Partial → Partial | Pass → Pass | Pass → Pass |
| 2 Judgment | Partial → **Fail** | Pass → Pass | Fail → Partial | Partial → Pass | Fail → Partial | Fail → Partial | Partial → Partial | Pass → Partial |
| 3 Actionability | Partial → Fail | Partial → **Pass** | Fail → Partial | Fail → n/a | Fail → Fail | Fail → **Pass** | Fail → Partial | Partial → **Pass** |
| 4 Trade-offs | Fail → Pass | Fail → Pass | Fail → Pass | Fail → n/a | Fail → Pass | Fail → Pass | Fail → Pass | Fail → Pass |
| 5 Keep Original | Fail → Partial | Fail → Pass | Fail → Pass | Partial → Pass | Partial → Pass | Partial → Partial | Partial → Pass | Partial → Pass |
| 6 Evidence | Pass → Pass | Pass → Pass | Pass → Pass | Pass → Pass | Pass → Pass | Pass → Pass | Pass → Pass | Pass → Pass |
| 7 Consistency | Partial → Partial | Pass → Pass | Partial → Pass | Pass → Pass | Partial → Partial | Partial → Partial | Partial → Partial | Pass → Pass |
| 8 Scope | Fail → Partial | Pass → Pass | Fail → Partial | Pass → Pass | Fail → Partial | Pass → Pass | Fail → Partial | Pass → Pass |

Why the cells that move for the worse: **01 Gemma Judgment** (the returning line L005/L016 is filed as a strength, the headline says near-perfect); **04 Grok Judgment** (Pass → Partial: the Bridge letter is praised as making the returning chorus weigh more, which is not so, and the headline calls the song successful); **03 Grok/Gemma Understanding** (the stillness is asked to develop). Why the grid is "mine": every grade has its raw quote in § 3 or in the file named by the cell.
