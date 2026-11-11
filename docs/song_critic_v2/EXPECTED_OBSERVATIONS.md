# EXPECTED_OBSERVATIONS (Song Critic V2, Task B) — fixed BEFORE the 16 runs

Honest context: S1–S3 were read in Audit 01 and Tune 02, so the auditor knows how both backends answered them; S4 is new and unseen. These notes follow the song designs and the brief; they are hashed and timed in `PRE_REGISTRATION.txt` before the first request. They are what a careful reader would weigh, not an answer key.

Line ids are the file line numbers sent to the model (tag lines count; blank lines are counted but show no id).

## Test Song 01 (beautiful words, little movement)
- The mood is lovely and constant but never deepens: one line returns verbatim (L005, L016); the chorus is identical three times (L008-L011, L020-L023, L032-L035); the Bridge restates the same words (L026-L029). A feeling is named ("ความคิดถึง", "ไร้ชื่อเรียกขาน") but has no cause, person or event.
- **Judgment expected: the report says the repetition does not move the song forward and names where (the returning line, the repeated chorus, the Bridge).** Calling the repetition "deliberate" is not a defence: a repetition counts as working only when each return adds or changes something; here it only restates. A fair strength: the consistent soft register and the opening images.
- Good action: say what kind of material is missing and where (a fact, person or turn in Verse 2 or the Bridge that changes what the chorus means) without writing a line.

## Test Song 02 (plain words, strong story)
- Promise (L002), obstacle (L005), conflict (L020), small gesture (L025, the medal), payoff (L035, the medal on the dashboard). Plain wording and little rhyme are the child narrator's voice. The father's short reply (L021) is understatement.
- **Judgment expected: it works; a clean "keep" for most of it.** At most one minor point. No manufactured concern; the Outro is the payoff, not a stall; no request for a more eloquent father or for ornament.

## Test Song 03 (atmosphere, no story, on purpose)
- Motifs (rain on the tin roof, blue light, the whispering radio, the glass of water) in a closed room; stillness is the aim; Verse 2 shows time passing through small changes (curtain, kettle, shadow); the Outro returns to the opening.
- **Judgment expected: the song succeeds as an atmosphere piece because the repetition returns the same room with small changes (not only because the writer "meant" it); keep the chorus and the structure.** A careful reader may raise at most one question: the Bridge says the absence outright (L026-L027) where the rest shows it. No request for a story arc, for will-driven action, or for word changes.

## Test Song 04 (mixed — new)
- **Strong:** the opening is specific (L002-L005: the truck, the grandmother holding the blue cup chipped at the rim, the workers carrying the cabinet through the door she used to close each night, the keys not yet handed over); the hook (L012-L015) is short and memorable and states the object-as-memory idea; the Outro (L035-L036) pays off the cup with a physical image (the chip where the grandmother held it).
- **Weak, in order of cost to the listener:**
  1. **Verse 2 (L018-L021) states the feelings in general words** ("ฉันเสียใจมาก ฉันคิดถึงยายเหลือเกิน…") after Verse 1 showed them; it adds no new fact or image, so the middle of the song stalls.
  2. **The Bridge (L024-L026) opens a thread (a letter for the youngest grandchild) and the song never uses it again;** the final chorus is the first chorus unchanged, so the letter's weight is lost.
  3. **The Pre-Chorus (L008-L009) is stock philosophy** ("ชีวิตก็เป็นแบบนี้…", "เวลาไม่เคยรอใคร") next to the specific opening.
- **Judgment expected: a mixed verdict that names both the strong opening/hook/ending and the weak Verse 2 and the unused letter.** A report that praises everything, or that condemns the whole song, fails. Keep original is right for the opening, the hook and the Outro.
- Good actions: let Verse 2 carry one new concrete fact instead of naming feelings (where: L018-L021; for what: the middle moves); make the letter matter (use it in the last chorus or the Outro, or drop it: a trade-off: the unopened letter is also a quiet tension worth keeping); the trade-off of changing the last chorus (the hook loses its familiarity). No rewritten line.

## For every run (the eight criteria of the brief)
1. Understanding: the song's aim in the writer's terms, correctly. 2. Judgment: real strengths vs real problems; "nothing important to fix" is allowed only where true (S2, S3). 3. Actionability: what to try, where (section or ids), for what effect, without writing the lyric. 4. Trade-offs: only when a real one exists. 5. Keep Original: said where it is right. 6. Evidence: quotes and ids exist and match; several places cited as separate references. 7. Consistency: headline, findings, priorities and section readings agree. 8. Scope: no rhyme, syllable or word-choice judgments.

## Decision rule, fixed in advance
- Judged from the raw answers per song and backend; no LLM judge; the raw findings are read even when the parser would drop one.
- **The candidate goes into the production prompt only if ALL hold:** (a) on S1 and S4 it names the main real weakness (S1: repetition that only restates; S4: Verse 2 telling, or the unused Bridge letter) in at least 3 of the 4 cells, and not fewer than the baseline; (b) on S2 and S3 it does not manufacture a concern (a concern the report itself contradicts, or against the song's evident aim) in more cells than the baseline; (c) no new regression in Evidence or Scope (a quote not in the lyrics, replacement wording more often than the baseline); (d) no priority `high` on a part the report calls deliberate.
- If the result is close or the candidate is worse in any of (a)-(d), the production prompt stays as it is. One candidate only; no re-tuning after the results.
