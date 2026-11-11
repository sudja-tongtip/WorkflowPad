# Song Critic AI — Quality Audit 01 (audit only)

Date: 2026-11-12 · Production prompt `music_studio/prompts/specialists/song_critic.md` (the repository file, unchanged) · production pipeline of one Song Critic report: `spec_reports.evidence_for` → `specialists.effective_system` → `spec_reports.build_user` → one request at temperature 0.4 → `spec_reports.parse_answer`.

## 0. What was run (and what was not)

| | |
|---|---|
| Runs | 6 report runs = 3 songs × (Gemma local `gemma4_llm.json`, Grok `grok-4-latest`). **6 requests in total, 0 retries** (the app retries only when an answer has no `[DONE]`; every answer had it). |
| Sessions | The Song Critic report is a stateless single request in the production pipeline: every run is a fresh request with no history (so "new session per song and backend" holds by construction). |
| Not done | No prompt, parser, contract, code, AppData, backend or model setting was changed (the local context size 19 000 is the user's own `ctx_local` from the real config, read, not modified). No audio, no Music Producer or other specialist, no retry for quality, no LLM judge. |
| Grok | Backend was available (key present, model `grok-4-latest`). Only the three synthetic test songs were sent. |
| Sample size | One run per song per backend. Differences between runs are **not** measured for noise; a pattern below is a pattern in six answers, not a statistic. |
| Evidence | `RAW_REQUESTS/` (system prompt, user message, tokens, seconds, model), `RAW_RESPONSES/` (raw answers), `PARSED_REPORTS/` (after the parser), `run.log`. System prompt ≈ 1 806 tokens, user message 614–716 tokens. Seconds: Gemma 12.0–15.1, Grok 19.0–30.7. |

The test songs and their line ids are in `TEST_SONGS.md`; what they were designed to test and what a careful reader would probably weigh is in `EXPECTED_OBSERVATIONS.md` (written after the runs: see the honesty note there).

## 1. Headline findings (read this first)

1. **Parser / presentation (category 4) — S1 Gemma shows an empty findings section although the model wrote four findings.** Every one of its four findings used a comma list (`lines: L005, L016`, `L011, L023, L035`, `L002, L003`, `L028, L029`). The production parser (strict since the Specialist Reports follow-up: exactly `L12` or `L12-L13`) refused all four: `PARSED_REPORTS/S1_gemma_parsed.json` → `findings: 0`, 4 diagnostics. Grok on S1 lost 1 of 2 findings the same way (`L5,L16`). A user would see a verdict and six section readings and no findings. This is the biggest concrete defect of the audit and it is **independent of quality of judgment**. Why it is not simply "the model's fault": the prompt says `L12 or L12-L13`, but a finding about a **repetition** (the same line at L005 and L016; a chorus line at L011/L023/L035) naturally cites two or three **separate** places and the contract has no way to say that. Whether the cause is the contract, the model's habit, or both is not decided by this data (the same Gemma used ranges correctly on S2 and S3, and also used a comma list for the adjacent `L002, L003` on S1).
2. **Model judgment (category 1) — Gemma ignores rules the prompt states explicitly.** The prompt says "A recommendation is a direction to try, never a replacement lyric" and "do not judge … word choice for its own sake". Gemma wrote replacement wording in 5 of its 6 concern findings (S1 `"ฉันสัมผัสความงามอันเงียบงัน"`, `"ความรู้สึกที่เหมือนฝัน"`; S2 `"พ่อมาแล้ว ลูกเก่งมาก"`; S3 `"เงาของเก้าอี้ที่เริ่มจะจาง"`, replace `แค่` with `มีเพียง`/`ก็แค่`). Grok wrote none. That points at instruction-following of the model (the rule is in the prompt, so this is not evidence of a missing rule); it cannot be said from six answers whether a differently worded rule would help.
3. **Both backends produce the same shape of report for three very different songs.** Gemma: exactly 2 concerns + 2 strengths in all three; Grok: exactly 1 concern + 1 strength in all three. The prompt allows "at most three concerns and at most two strengths". A uniform count on a weak song, a strong story and an intentional abstract piece is what quota-filling looks like; with n = 3 per backend it is a signal, not proof.
4. **Grok's recommendations are generic.** All three concerns end in the same move: S1 "ลองเพิ่มรายละเอียดเล็กน้อยที่เปลี่ยนแปลงสถานการณ์…", S2 "ลองเพิ่มรายละเอียดเล็กน้อยที่แสดงถึงความรู้สึก…", S3 "ลองเพิ่มรายละเอียดเล็กน้อยในคอรัสรอบที่สอง…" ("try adding a little detail"). That advice fits almost any song, and on S2/S3 it rests on a misreading (below).
5. **Quotes and line ids are accurate on both backends.** All 18 `quote:` blocks are verbatim in the lyrics (checked by script) and every cited line id points at the quoted text. No invented line. Evidence is the strongest criterion in this audit.
6. **Neither backend marked the abstract song down for having no story (S3), and neither marked the plain song down for plain words or sparse rhyme (S2).** The two traps of the brief were avoided. What they did instead is in §3.
7. **Neither backend states plainly that S1 is a weak song.** Gemma calls it a success at atmosphere and its note praises the structure; Grok's headline names the stasis ("เนื้อเพลงซ้ำโดยไม่มีการเคลื่อนไหว") and is the closer of the two.

## 2. Criteria matrix (Pass / Partial / Fail) — my reading of the raw text, not an LLM judge

| Criterion | S1 Gemma | S1 Grok | S2 Gemma | S2 Grok | S3 Gemma | S3 Grok |
|---|---|---|---|---|---|---|
| Song Identity | Partial | Pass | Pass | Pass | Pass | Pass |
| Artistic Judgment | Partial | Partial | Partial | Partial | Partial | Partial |
| Critical Independence | Partial | Partial | Partial | Partial | Partial | Partial |
| Evidence | Pass | Pass | Pass | Pass | Pass | Pass |
| Consistency | Partial | Pass | Partial | Partial | Partial | Partial |
| Recommendations | Fail | Partial | Fail | Partial | Fail | Partial |
| Creative Sensitivity | Pass | Pass | Pass | Pass | Partial | Partial |
| Natural Language | Partial | Pass | Partial | Pass | Partial | Pass |

Grades are mine and open to the reviewers' disagreement; the supporting text of each is below. "Evidence" grades the model's answer; S1 Gemma's parser loss is graded separately in §4.

## 3. Per-song reading

### S1 — Beautiful Words, Weak Song  (test: does it see that pretty ≠ good, and where the idea repeats?)

**Gemma** (`RAW_RESPONSES/S1_gemma_response1.txt`)
- Identity — **Partial**. Headline: "…แต่เนื้อหาโดยรวมประสบความสำเร็จในการสร้างบรรยากาศที่ชวนฝัน แม้จะขาดการพัฒนาเรื่องราวที่ชัดเจน" ("succeeds in creating a dreamy atmosphere, though it lacks clear story development"). It names the longing and the lack of development but frames the song as working; it never says that nothing deepens.
- Artistic Judgment — **Partial**. It does find the repetition (F1 "ฉันเดินไปในความงามอันเงียบงัน" at L005/L016: "…ความรู้สึกติดอยู่ในจุดเดิม"; F2 "ไร้ชื่อเรียกขาน" at L011/L023/L035: "…หยุดนิ่งอยู่กับการบอกว่า 'มันคือความรู้สึกที่ไม่มีชื่อ'"). But F3 praises "ลมพริ้วพรมกลีบฝัน" as "ใช้คำที่สร้างภาพได้ดีมาก" and NOTE.1 says "โครงสร้างที่แข็งแรงมากในการนำเสนอภาพ … และการวนซ้ำของอารมณ์" — it takes the ornate, repeated surface for strength.
- Consistency — **Partial**. F1/F2 say the song is stuck in place; the headline says it succeeded and the NOTE says the structure is very strong.
- Recommendations — **Fail**. Both concern recommendations are replacement wording ("ฉันสัมผัสความงามอันเงียบงัน", "ความรู้สึกที่เหมือนฝัน"), against the prompt.
- Presentation (category 4): all four findings were dropped by the parser (§1 item 1).

**Grok** (`RAW_RESPONSES/S1_grok_response1.txt`)
- Identity — **Pass**. "เพลงสื่อถึงความงามของความคิดถึงที่ไร้ชื่อผ่านภาพธรรมชาติ แต่เนื้อเพลงซ้ำโดยไม่มีการเคลื่อนไหว".
- Artistic Judgment — **Partial**. It sees stasis ("การซ้ำของประโยคเดิม… ไม่มีจุดเปลี่ยนหรือรายละเอียดใหม่"), but its only strength is the Bridge's "งดงามเหลือเกิน งดงามเหลือเกิน" as a "จุดพักทางอารมณ์" — that line is the most empty repetition in the song.
- Independence — **Partial**. One concern only. The chorus repeated verbatim three times is mentioned only in section readings ("คอรัสซ้ำเหมือนเดิม", "…โดยไม่มีการเปลี่ยนแปลง"), not raised as a finding.
- Consistency — **Pass**. Headline, concern and section readings all say the same thing.
- Recommendations — **Partial**. "ลองเพิ่มรายละเอียดเล็กน้อยที่เปลี่ยนแปลงสถานการณ์ก่อนคอรัสสุดท้าย": a direction (as the prompt asks) but generic.
- Presentation: finding 1 was dropped by the parser (`lines: L5,L16`).

### S2 — Strong Story, Simple Lyrics  (test: does it value the storytelling and not penalise plain words / sparse rhyme?)

**Gemma** (`S2_gemma_response1.txt`)
- Identity — **Pass**. Reads the movement correctly: expectation (Verse 1) → confrontation (Verse 2) → "เปลี่ยนมุมมองจากความโกรธเป็นความรักและความทรงจำ" (Outro). Section readings name the promise, the conflict, the medal as the decision.
- Artistic Judgment — **Partial**. Values the concrete image ("เสื้อเปียกเหงื่อทั้งตัว", the medal on the dashboard). But F3 treats the father's plain reply "พ่อขอโทษ พ่อมาแล้ว" as lacking emotional depth and proposes "พ่อมาแล้ว ลูกเก่งมาก": the understatement is the strength of that moment.
- Independence / Consistency — **Partial / Partial**. F1 is filed `concern`, `priority: high`, yet its interpretation is positive: "…ทำให้การกลับมาของพ่อมีความหมายมากขึ้น"; the recommendation asks to explain "why" the child is angry, which the song already does (L005 "ติดอยู่ตรงไหนไม่รู้", the whole Pre-Chorus). A high-priority concern that its own reading approves looks like a finding made to fill the slot.
- Creative Sensitivity — **Pass**: no criticism of plain words or the lack of rhyme.
- Natural Language — **Partial**: Thai sentences with embedded English tags ("(setting the scene)", "(building tension)", "(climax)", "(resolution of immediate conflict)") — the labels read like a template.

**Grok** (`S2_grok_response1.txt`)
- Identity — **Pass** ("ความเสียสละของพ่อและการให้อภัยของลูกชายผ่านเรื่องราววันกีฬาสี"); the reading narrates the events correctly.
- Artistic Judgment — **Partial**. The strength (father running two kilometres, one shoe off) is well chosen. The concern is a misreading: it says the Outro "กล่าวซ้ำสถานการณ์เดิมโดยไม่มีรายละเอียดใหม่" and the story "หยุดนิ่ง", but "เหรียญนั้นแขวนอยู่หน้ารถทุกวัน" is the new image and the payoff.
- Consistency — **Partial**. Its own Section 7 says the Outro "สรุปความทรงจำที่คงอยู่"; the finding says the ending stalls.
- Recommendations — **Partial**: "ลองเพิ่มรายละเอียดเล็กน้อย…" (generic, and based on the misreading).
- Natural Language — **Pass**; one `[NOTE.1]` is an empty bullet (`- `) — a small presentation blemish.

### S3 — Intentional Abstract / Atmospheric Song  (test: does it understand the intent and tell unity from aimless writing?)

**Gemma** (`S3_gemma_response1.txt`)
- Identity — **Pass**. The NOTE says it outright: "เพลงนี้เป็นเพลงแนวบรรยากาศ … ไม่ได้มีเรื่องราวที่ต้องคลี่คลาย … การวนซ้ำจึงเป็นกลไกสำคัญ … การกลับไปที่จุดเริ่มต้นใน Outro จึงเป็นการปิดวงจรทางอารมณ์". It also notices that Verse 2's small changes (curtain, kettle, shadow) show time passing.
- Creative Sensitivity — **Partial**: it never asks for a story, but two concerns pull against the intent: F3 calls the Bridge's "แค่ฝน แค่แสง…" "การยอมจำนนที่ค่อนข้างเฉื่อยชา" (passivity is the point of the song) and suggests changing `แค่` to `มีเพียง`/`ก็แค่` (nearly the same meaning: a change without a reason); F2 asks to add an action to the still image of the chair's shadow ("เงาของเก้าอี้ที่เริ่มจะจาง").
- Consistency — **Partial**: the NOTE endorses stillness and the loop; the concerns push away from it.
- Recommendations — **Fail**: replacement wording again.

**Grok** (`S3_grok_response1.txt`)
- Identity — **Pass**. Headline: "เนื้อเพลงประสบความสำเร็จในฐานะเพลงบรรยากาศที่ไม่ต้องมีเรื่องราว".
- Independence / Consistency — **Partial**. F2 is `concern`, `priority: high`: the chorus repeats verbatim, which "ทำให้เพลงรู้สึกนิ่งตามที่ตั้งใจ แต่ลดโอกาสในการพัฒนาความรู้สึกให้ลึกขึ้น" — it first recognises the repetition as the intended effect and then gives it the highest priority and asks for change.
- Creative Sensitivity — **Partial**: "ลองเพิ่มรายละเอียดเล็กน้อยในคอรัสรอบที่สอง" is a small-variation direction (mild), but it applies the development yardstick to a song designed to be still.
- It also did not raise the one thing a careful reader might: the Bridge states the absence outright ("ไม่มีใครเปิดประตู / ไม่มีใครปิดไฟ"), more explicitly than the rest.

## 4. The special checklist of the brief

| Behaviour | Found? | Evidence |
|---|---|---|
| Praises every section although the song has problems | Not as such. Section readings are descriptive for all songs; S1 Gemma's are all favourable ("Emotional Peak" for the Bridge). | `S1_gemma` SECTION.5 |
| "Good because it rhymes", ignoring meaning | No | — |
| Blames the abstract song for having no story | **No** (both backends) | `S3_*` |
| Recommendations only to reach a number | Possible: uniform 2+2 (Gemma) and 1+1 (Grok) on all three songs; weaker concerns exist in S2/S3 | §1 item 3; `S2_gemma` F1, F3; `S3_gemma` F2, F3 |
| Changes a good word with no reason | **Yes** (Gemma) | `S3_gemma` F3 (`แค่` → `มีเพียง`); `S2_gemma` F3 |
| Quotes lines that do not exist | **No** (18/18 verbatim) | script check, §1 item 5 |
| Summary says weak, detail says fine (or the reverse) | **Yes** in 4 of 6 | S1 Gemma (success vs stuck), S2 Gemma (F1 high concern, positive reading), S2 Grok (Outro stalls vs "สรุปความทรงจำที่คงอยู่"), S3 Grok (intended stillness vs high-priority concern) |
| Criticism that fits almost any song | **Yes** (Grok) | the same "เพิ่มรายละเอียดเล็กน้อย" in all three concerns |
| Replacement lyrics against the prompt rule | **Yes** (Gemma, 5 of 6 concerns) | §1 item 2 |
| Cites several lines in a form the parser refuses | **Yes** | §1 item 1 |

## 5. Classification of the errors (with the evidence for each, and its limit)

| # | Error | Category | Why this category | What the data cannot say |
|---|---|---|---|---|
| 1 | Findings dropped (`L005, L016`) | **4 Parser / Presentation**, with a possible **2 Task Instructions / contract** element | The parser did exactly what its contract says; the contract has no way to cite separate lines of a repetition, and a repetition is a central thing a critic reports. The model also deviated from the stated `L12 or L12-L13`. | Whether the model would follow the format if repetition were not the topic; one sample (the same Gemma used ranges on S2/S3). |
| 2 | Replacement lyrics / word-level advice | **1 Model Judgment** (instruction-following) | The prompt forbids it in two places ("never a replacement lyric", "not word choice for its own sake"); Grok obeyed on the same prompt. | Whether a rewording of the rule would change Gemma's behaviour. Not evidence of a prompt problem. |
| 3 | Misreading S2's Outro as stalled; S3's chorus given "high" priority although named intentional | **1 Model Judgment** | The context carried the whole lyrics and style; the facts it needed were present. | — |
| 4 | Uniform finding counts across songs; generic "add small detail" | **1 Model Judgment** (possibly **2 Task Instructions**: the prompt has a ceiling "at most three concerns" and Method step 5 "Choose at most three concerns" but no explicit "zero is fine") | The ceiling is stated; the instruction to choose none when nothing matters exists only implicitly ("a song that works is allowed to be reported as working"). | Whether the wording invites the quota behaviour: not tested and not claimed. |
| 5 | Softness on S1 (calls it a success) | **1 Model Judgment**; the Responsibilities say "Name … the weakness that costs the song the most" | Gemma named the stagnation in findings but not in the verdict. | — |
| 6 | Context | **3 Missing / Misleading Context**: none found. The request carried the style line, the numbered lyrics and the section map; every cited line id was right. | — | The style notes are minimal ("Dream Pop / Wistful…"); no evidence they mattered. |

## 6. Cross-backend summary (details in `QUALITY_COMPARISON.md`)

Grok follows the format and the rules more faithfully and writes in more natural Thai; it is also more generic and misreads one or two points per song. Gemma goes further into the lyrics (notices Verse 2's changes in S3, names the three recurring places of the "ไร้ชื่อเรียกขาน" line in S1) but ignores the "no replacement lyric" rule, mixes in English tags and, in S1, lost its whole findings section to the parser.

No prompt change is proposed here, as the brief asks.
