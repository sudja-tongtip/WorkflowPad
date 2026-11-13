# EXPECTED_OBSERVATIONS (Song Critic V3, Complete Songcraft Review) — fixed BEFORE the 20 runs

Honest context: Test Songs 01–04 are S1–S4 of the earlier audits (the auditor has read both backends' answers on them); Test Song 05 is new and unseen. These notes follow the song designs and the brief; they are hashed and timed in `PRE_REGISTRATION.txt` before the first request. They are what a careful reader would weigh, not an answer key; an aspect may legitimately be `no_issue` or `insufficient`.

Mapping to the brief's five song kinds: **01** = an opening that is pretty but a song that does not develop (S1) · **02** = a well-told story in plain words (S2) · **03** = an atmosphere song whose repetition is intended (S3) · **04** = mixed strengths and weaknesses (S4) · **05** = an interesting opening whose ending does not answer it (S5, new).

Line ids are the file line numbers sent to the model (tag lines count; blank lines are counted but show no id).

## Test Song 01 (pretty, no development)
- Opening: `scene`/`atmospheric` (moon, wind, stars): specific enough as images but interchangeable; it promises a mood, not a question. Central idea: a vague, lovely longing; named, never given a cause. Sections: the Chorus names the feeling, Verse 2 and the Bridge restate it. Development: **none: the same line returns (L005 / L016), the chorus is identical three times (L008-L011, L020-L023, L032-L035), the Bridge restates** (Pattison's "stagnant verses": the refrain wears thin). Hook: a soft, repeatable refrain; memorable but empty of consequence. Imagery: consistent but ornamental. Repetition: restating, not changing meaning. Ending: returns to the chorus unchanged.
- **Judgment expected: the review says the song does not move and names where; a `concern` (high or medium) on development and/or repetition, not "intended, therefore fine".** Fair strengths: a consistent register, an easy refrain.

## Test Song 02 (plain story)
- Opening: `scene`/`in_medias_res` (waiting at the school gate, L002-L005). Theme: a child and a late father; a gesture. Sections: each does a job (promise, race, anger, medal, payoff). Development: **strong** (anger L020 → medal L025 → "forgot I was angry" L031). Hook: the repeated "ผมวิ่งไปทั้งที่ไม่มีใครเชียร์" returns changed (L012, L028-L031). Imagery: specific (shoe, sweat, medal). Repetition: works (the second chorus adds the father's reaction). Ending: the medal on the dashboard (L035) pays off the gesture.
- **Judgment expected: mostly `strength` / `no_issue`; no manufactured concern; plain language and sparse rhyme are not weaknesses; the Action Plan may be "keep" or one optional, non-urgent experiment.** A careful reader may mention that the Pre-Chorus is short; nothing more is required.

## Test Song 03 (atmosphere, intended repetition)
- Opening: `atmospheric`/`scene` (rain on a tin roof, blue light, a radio). Theme: the hush of an empty house at night. Sections: Verse 2 shows time passing through small changes (curtain, kettle, shadow); the Bridge states the absence outright (L026-L027, "ไม่มีใครเปิดประตู / ไม่มีใครปิดไฟ"). Development: deliberately minimal. Repetition: the chorus repeats word for word; **it works as a device because the room returns with small changes**, which the review should say as a reason, not merely "it is intended". Ending: returns to the opening (L031-L032).
- **Judgment expected: the song works as an atmosphere piece; no request for a story, plot or will-driven action; a single careful question at most (the Bridge explains where the rest shows), with a trade-off.** Aspects like Development may be `no_issue` or `insufficient`/"not the aim".

## Test Song 04 (mixed)
- **Strong:** specific opening (L002-L005: truck, grandmother with the blue cup chipped at the rim, the door she used to close each night, the keys not yet handed over); the hook (L012-L015) is short and memorable; the Outro (L035-L036) pays off the chipped cup.
- **Weak:** Verse 2 (L018-L021) names the feelings the opening showed (no new fact); the Bridge (L024-L026) opens a thread (a letter for the youngest grandchild) the song never uses again, and the last chorus (L029-L032) is the first unchanged; the Pre-Chorus (L008-L009) is stock philosophy.
- **Judgment expected: a mixed review: strengths for opening, hook, imagery, ending; `concern` or `mixed` for development (Verse 2 telling) and for the unused letter / unchanged last chorus (repetition or ending-payoff aspect).** Keep Original for opening, hook and Outro.

## Test Song 05 (strong opening, ending that does not answer it) — new
- **Opening (L002-L005): `question` + `scene`/`contradiction`:** a chair that moves one step away each morning (L003), a yellow raincoat on it (L004), a wet backrest with no rain for three days (L005). Strong: specific, odd, it promises an answer.
- **The song never answers or returns to it.** Chorus: the narrator "waits" and "asks" (L008-L010); Verse 2 asks neighbours, tries the chair (L013-L016) and the moment is dropped; the Bridge is a general moral (L024-L025: "learn to live with what we do not know"); the Outro is a cheerful general ending (L028-L029) that does not mention the chair, the raincoat or the question.
- **Judgment expected: the review praises the opening and says the Ending & Payoff do not connect back (what the opening set up is neither resolved nor deliberately left open with its meaning changed); `concern` for ending (and likely development / hook: the title idea of the chair is not the hook; the chorus is generic).** An action such as "let the ending return to the chair or the raincoat, or let the Bridge make the unanswered question the point" with the trade-off (an answer may cost the mystery) is the model of a good recommendation.
- A report that calls the ending "resolved" because it is cheerful misreads it; one that demands an explanation of the mystery misreads the aim (leaving it open can be a choice if the song makes the open question matter).

## For every run
1. **Complete:** all eight aspects present, in order, each with only what it deserves (`no_issue` and `insufficient` are good answers); not eight filled-in headings. 2. **Relations:** it sees how a section serves the next one (Section Purpose / Song Journey). 3. **Intent vs result:** says why a device works or not, not just that it is intended. 4. **No false concerns.** 5. **Actions:** at most three, specific (what / where / for what effect), with a trade-off where one exists; "keep" is a valid plan. 6. **Scope:** no rhyme, word-choice or syllable judgments; no new lyric lines. 7. **Evidence:** quotes verbatim, ids exist, separate places cited as separate references. 8. **Handoff-ready:** an aspect and an action can be discussed with the Songwriter without more explanation.

## Decision rule, fixed in advance
Judged from the raw answers per song and backend (10 cells for the candidate; the 10 baseline cells use the production format and are compared on the same judgments; no LLM judge). Longer or more findings is **not** better.
**The candidate goes into the production prompt only if ALL hold:**
(a) **Format and coverage:** in at least 8 of 10 cells it gives all eight aspects, parsed without loss (an aspect refused by the parser counts as a loss), and in no cell more than two aspects are mere headings (no line, no reason).
(b) **Judgment on the weak cases:** on Test Songs 01, 04 and 05 (6 cells) it names the main weakness in at least 5 cells (01: the song does not develop / the returns only restate; 04: Verse 2 tells, or the unused letter; 05: the ending does not answer the opening) **and not fewer cells than the baseline**.
(c) **No false concerns on the sound songs:** on Test Songs 02 and 03 (4 cells) it writes no `concern` or `mixed` with priority `high`, and no more manufactured / against-the-aim concerns than the baseline.
(d) **Evidence and scope:** 0 quotations that are not verbatim; no more replacement lyrics than the baseline; no rhyme / syllable / word-choice judgments.
(e) **Actions:** the Action Plan has at most three items in every cell and is not one action per aspect.
(f) **Size:** the system prompt grows only by what the eight aspects and the reference need (the candidate is about +50 % of the production prompt; a larger one fails).
If any condition fails or is close, **the production prompt stays**; the new report format, parser and page stay in the code (they read a report of the new format when one is saved, and the old reports read as before), and the limitation is reported. One candidate only; no re-tuning after the results.
