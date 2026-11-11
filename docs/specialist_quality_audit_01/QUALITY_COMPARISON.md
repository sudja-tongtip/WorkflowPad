# QUALITY_COMPARISON — Gemma local vs Grok (Song Critic, 3 songs × 1 run)

All quotes are from `RAW_RESPONSES/`. One run per cell: differences are observations, not measured rates.

## Numbers

| | Gemma (`gemma4_llm.json`) | Grok (`grok-4-latest`) |
|---|---|---|
| Seconds per report | 15.1 / 12.0 / 13.5 | 19.0 / 23.8 / 30.7 |
| Findings written (raw) | 4 / 4 / 4 (2 concerns + 2 strengths each) | 2 / 2 / 2 (1 concern + 1 strength each) |
| Findings that survived the parser | **0** / 4 / 4 | 1 / 2 / 2 |
| Verbatim quotes | 12 of 12 | 6 of 6 |
| Replacement wording in a recommendation | 5 of 6 concerns | 0 of 3 |
| Sections reviewed (all sections of the song) | 6 / 7 / 6 | 6 / 7 / 6 |

## Side by side

| Question | Gemma | Grok |
|---|---|---|
| Does it understand what the song is? | Yes on all three. S3: names the atmosphere intent in the NOTE and sees Verse 2's small changes as time passing ("…แม้จะนิ่ง แต่เวลาก็ยังเดินอยู่"). | Yes on all three, shorter. S3 headline: "ประสบความสำเร็จในฐานะเพลงบรรยากาศที่ไม่ต้องมีเรื่องราว". |
| S1 (pretty, weak): does it see it? | Half: finds the two repetitions with the three line ids, but the headline says it succeeds and the NOTE praises the "strong structure". | Closer: headline "…แต่เนื้อเพลงซ้ำโดยไม่มีการเคลื่อนไหว"; only one concern and an odd strength (the empty Bridge repetition). |
| S2 (plain, strong story): is plainness left alone? | Yes for the words; but asks the father to say more ("พ่อมาแล้ว ลูกเก่งมาก"), which weakens the understatement. | Yes; picks a good specific strength; misreads the Outro as stalling. |
| S3 (abstract): is the intent respected? | Understands it, then partly argues against it (passivity of `แค่`; add motion to the shadow). | Understands it, then flags the intended repetition as the top-priority concern. |
| Follows the prompt's rules (no replacement lyric, no word-choice judging, a direction not a rewrite) | Often not. | Yes. |
| Specificity of advice | Specific (and sometimes unnecessary) rewordings. | Generic: "ลองเพิ่มรายละเอียดเล็กน้อย…" in all three concerns. |
| Format | Comma-separated line lists in S1 (all four findings) → refused by the parser; ranges elsewhere. `[VERDICT]`…`[DONE]` complete. English tags inside Thai sentences. | `L5,L16` once; short ids `L5` (the parser accepts them); one empty NOTE bullet. |
| Natural Thai | Understandable but template-like ("ทำหน้าที่…", "(climax)", "(Concrete Imagery)"). | Plainer, more natural. |
| Honesty about limits | S1/S2/S3 NOTE do not say it cannot judge sound; no claims about melody either. | S1 and S3 NOTE: "ไม่สามารถตัดสินเสียงหรือทำนองจากเนื้อเพลงได้" (follows the prompt's limitation). |

## What follows from it (and what does not)

- Neither backend is clearly better "as a critic". Grok is the more rule-following and readable; Gemma reads deeper into the text and is the more prescriptive. Each one's main weakness is different (Grok: generic and occasionally wrong about what a line does; Gemma: ignores constraints and over-advises).
- The strongest single defect is not a model difference: the parser dropped Gemma's S1 findings (and one of Grok's).
- Nothing here shows the prompt to be the cause of any quality gap. The explicit rule violations are Gemma's, on a prompt Grok follows. The one place the contract itself plausibly contributes is the single-reference `lines:` field (KNOWN_ISSUES #1).
