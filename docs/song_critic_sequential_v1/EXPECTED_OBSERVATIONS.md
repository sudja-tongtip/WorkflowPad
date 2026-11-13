# EXPECTED_OBSERVATIONS and decision rule (Song Critic Sequential V1), fixed BEFORE the 40 requests

Honest context: Test Song 01 (= S1) and Test Song 03 (= S3) are songs the auditor has read in earlier audits, with both backends' answers to them. The notes follow the song designs. They are what a careful reader would weigh, not an answer key.

## Test Song 01: pretty words, little development
Opening: moon, wind, stars (scene / atmospheric): lovely but interchangeable; it promises a mood, not a question. Central idea: a vague, lovely longing (the feeling is named, never given a cause). Sections: the Chorus names the feeling; Verse 2 and the Bridge restate it. **Development: none; the same line returns (L5 / L16), the chorus is identical three times (L8-L11, L20-L23, L32-L35), the Bridge restates** ("stagnant verses", the refrain wears thin). Hook: a soft, repeatable refrain; memorable but empty of consequence. Imagery: consistent but ornamental. Repetition: restating, not changing meaning. Ending: returns to the chorus unchanged.
**Judgment expected:** the review says the song does not move and names where; a concern (development and/or repetition), not "intended, therefore fine". Fair strengths: a consistent register, an easy refrain.

## Test Song 03: atmosphere, intended repetition
Opening: atmospheric/scene (rain on a tin roof, blue light, a radio). Theme: the hush of an empty house at night. Verse 2 shows time passing by small changes (curtain, kettle, shadow); the Bridge states the absence outright (L26-L27). Development: deliberately minimal. Repetition: the chorus repeats word for word; **it works as a device because the room returns with small changes**; a good review says why, not just "it is intended". Ending: returns to the opening (L31-L32).
**Judgment expected:** the song works as an atmosphere piece; no demand for a story or plot; at most one careful, conditional question with a trade-off. No high-priority concern on development or repetition.

## What the sequential arm should show, if it helps
- Step 4, 7 and 8 use the earlier steps (Step 7 refers to the development judgment; Step 8 looks back at Step 1).
- Step 9 recalls Steps 1-3 correctly (the `recall_` fields) and, where steps disagree, writes a [REVISION] with a reason instead of averaging.
- Each step answers only its aspect; the Action Plan has 0-3 items and follows the findings of the steps.

## Measures (computed by the script or read from the raw answers; no LLM judge)
1. **Accuracy (script):** for every evidence item (a `lines`+`quote` pair) in either arm: the line ids exist; the quote is found, whitespace-normalised, in the cited lines. A failure is "unverified". Rate = unverified / evidence items, per arm and backend. Nothing is corrected.
2. **Critical judgment (reading):** S1: does the arm see the lack of development / restating repetition? S3: does it avoid a high-priority concern on development / repetition and say why the repetition works or what is its only conditional risk?
3. **Depth, consistency, actionability, restraint, final synthesis (reading).** Contradiction = the final review or an action says the opposite of what the steps' evidence shows.
4. **Efficiency (script):** requests, latency, input/output tokens (estimated; Grok also as reported) per song.

## Decision rule (fixed in advance)
Choose Sequential only if ALL hold, over the 4 comparable (song x backend) cells:
- **(J) Judgment:** Sequential is better than One-shot in at least 2 cells and worse in none, counting S1 (sees the development problem) and S3 (no false high-priority concern, understands the repetition) as the two criteria per backend.
- **(A) Accuracy:** the unverified rate of Sequential is not higher than One-shot's (per backend, and overall).
- **(C) Consistency:** no Final Review that contradicts the evidence of its own steps in a way that matters.
- **(P) Actionability:** every Final Review has at most 3 actions, each saying what, where and why without replacement lyrics.
- **(E) Efficiency:** wall-clock latency per song of Sequential at most 6x One-shot's for the same backend and song, and nine requests per song accepted as the price. (The 6x bound is arbitrary and written down here so it is not chosen after the results.)
If better only because longer, or slower with no decision gain: keep One-shot. A cell cut by an error or the context guard counts as "worse" for Sequential. The prototype is never put into production by this test.
