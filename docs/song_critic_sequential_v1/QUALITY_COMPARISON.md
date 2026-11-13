# QUALITY_COMPARISON (Song Critic Sequential V1)

Facts come from the raw answers and `METRICS.md`; the quality judgments are one person's reading of 8 raw reports (4 one-shot, 4 sequential) and can be argued with. No LLM judge. Expected observations and the decision rule were fixed before the run (`EXPECTED_OBSERVATIONS.md`, `PRE_REGISTRATION.txt`).

## What each arm said (summary of the raw answers)
| cell | A one-shot (production) | B sequential (9 steps) |
|---|---|---|
| **S1 / Gemma** | Verdict: the second verse leaves the feeling "a little still". Findings: high concern on L014-L015 (new images but no emotional change), medium concern on the line that returns unchanged (L005/L016: "stuck"), strengths on the chorus and bridge. Sees the problem, softly. Writes sample wording in a recommendation ("ฉันเอื้อมมือไปสัมผัสเมฆขาว..."). | All eight steps `strength`. Final: "succeeds at a high level"; Step 4 is softened further by a [REVISION] (development "subtle expansion"). Calls the repeating chorus "reinforced by raising the feeling". Action 3 writes a replacement image ("ดวงดาวพร่างพรายดั่งหยาดน้ำตา"). **Does not see the development problem.** |
| **S1 / Grok** | Headline: no movement after the first chorus. High concern on the unchanged choruses (L20-L23, accurate), strength on Verse 1, section readings say Verse 2 adds nothing new. **Sees the problem clearly**, one action (add a small change before the last chorus). | 7 steps `strength`, Step 4 `mixed` ("stays still, develops only slightly"); Step 7 says the repetition adds value because of "new images in the verse and a contrast from the Bridge". Final: "high success on its own terms ... little development is a strength more than a problem"; one action: keep the structure. **Step 4's `mixed` is dropped without a revision; the problem disappears.** |
| **S3 / Gemma** | Strength on the opening; **high-priority concern on L014-L016** ("add an action or a decision to Verse 2": asks for plot in an atmosphere song, a false alarm against the aim); medium concern on the negative sentences at L026-L028 with sample wording. | All eight `strength`. Final: succeeds highly; Step 4 revised to "intensified stillness" with a reason from Verse 2's small changes (curtain, shadow); one action: let the sound "start to change" in Verse 2 (a trade-off stated). Restraint, understands the stillness; the action is mild and partly wording-level. |
| **S3 / Grok** | Strength on the images; **high-priority concern on the second chorus repeating word for word** (L020-L023) without seeing that the room returns with small changes: a false alarm against the aim. | 7 `strength`, Step 4 `mixed` (small additions: rain, radio, curtain, shadow, confirmation of absence); Step 7 says each chorus gains meaning from the verses; Step 8 "a deliberate open return". Final: succeeds on its own terms; action: keep. **Understands the device and says why; no false concern.** The Step 9 answer is cut off inside `[NOTE.1]` (no `[DONE]`). |

## The eight criteria
| criterion | result |
|---|---|
| 1 Accuracy (script, same check for both arms) | A: 1 of 12 evidence items unverified (Gemma wrote `L005/L016` as one reference). B: **0 of 46** (Gemma 0/23, Grok 0/23) after the checker was made to accept Gemma's trailing `[DONE]<end_turn>`. B is more accurate; the per-step quote block is easier for the models. |
| 2 Critical judgment | **S1: A sees the lack of development in both backends; B in neither** (Gemma softens it, Grok drops it). **S3: B avoids the false high-priority concern in both backends and explains the device in Grok; A raises one in both.** B said "strength" on 30 of the 32 aspect steps (Steps 1-8, four sequences) on S1 and S3 alike: it does not tell the two songs apart. |
| 3 Depth / relations between sections | B's steps refer to earlier ones (Step 8 looks back at the opening; Step 4 and 7 talk about Verse 2 and the Bridge). Some of it is accurate (S3), some is flattering and wrong (S1 Step 7: the Bridge is said to change a chorus that stays identical). Depth per aspect is higher than the one-shot's; the conclusions are not better. |
| 4 Consistency | Grok S1: Step 4 `mixed` vs the Final "development is a strength", no [REVISION]. Gemma S1: Step 4 `strength` while its own Step 9 `reading` admits "too still in places". Step 9 did write [REVISION]s (Gemma, both songs) but each moved toward a more favourable reading, not toward the evidence. |
| 5 Actionability | A: 1-2 concerns with a direction, but sample wording in 2 of 4. B: Grok "keep" x2 (valid for S3, a miss for S1); Gemma 3 actions on S1 (one with replacement wording, one asks to turn a line into a "declaration of resolution"), 1 on S3. The actions are generic or out of scope when they exist. |
| 6 Restraint | B is restrained on S3 (good) but also on S1 (bad). A over-warns on S3 (bad). |
| 7 Final synthesis | The Final Review of B reflects the steps' enthusiasm, not the S1 problem that A found and that B's own Step 4 half-saw. |
| 8 Efficiency | see `COST_LATENCY.md`: 9 requests per song; latency 3.2-8.0x; Grok input tokens about 20x (history replay). |

## Possible reason (interpretation, not proven)
Each step is answered with the earlier answers in view. Eight consecutive "strength" answers make the ninth answer agree with them (momentum), and each step's reading tools ("repetition can be a deliberate device", "stillness is not proof of failure") were applied as permissions. The one-shot sees the lines for repetition and development side by side in one pass and is not carried by earlier verdicts. This is a hypothesis to test, not a finding.

## Decision rule (pre-registered), applied
| rule | result |
|---|---|
| (J) B better in >= 2 cells and worse in none | B better in 2 (S3 x2), **worse in 2 (S1 x2)** -> **FAIL** |
| (A) unverified rate not higher | 0 % vs 8 % overall -> PASS |
| (C) no mattering contradiction | Grok S1 (Step 4 mixed vs Final) -> **FAIL** |
| (P) <= 3 actions, specific, no replacement lyrics | Gemma S1 has replacement wording -> **FAIL** |
| (E) latency <= 6x one-shot | Gemma 3.6x / 3.2x, Grok 4.1x / **8.0x** -> **FAIL** for S3/Grok |
**Decision: keep One-shot.** Sequential is not adopted, not wired into production. It is not better because it is longer: it is longer, more accurate in citation, and worse on the one song that has a real weakness.
