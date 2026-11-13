# EXPECTED_OBSERVATIONS and evaluation rubric (Free-Thinking Two-Pass V2), fixed BEFORE the 12 requests

Honest context: Test Song 01 (= S1) and Test Song 03 (= S3) are the two songs of the earlier audits and of the sequential test; the auditor has read both backends' answers on them. These notes are a reading aid, not an answer key: the brief says to weigh the model's own reasons and evidence, and a well-argued different reading is not an error.

## Test Song 01: pretty words, little development
Moon, wind, stars, "beauty" named again and again. The chorus is identical three times (L8-L11, L20-L23, L32-L35); one line returns unchanged (L5 / L16); Verse 2 re-paints the same mood; the Bridge restates it ("งดงามเหลือเกิน" twice). The feeling (longing) is named, never given a cause or a change. Strengths: a consistent register, specific-looking images, an easy refrain.
**Key check (S1):** does the model see that beautiful language does not make the song succeed when the content does not develop, and does it keep that view in its FINAL assessment (Pass 2), not only in Pass 1? It may weigh it differently (a mild or severe judgment); it must not turn it into a strength "because the song is atmospheric / Dream Pop".

## Test Song 03: atmosphere, intended repetition
Rain on a tin roof, blue light, a radio, the stillness of an empty house. Verse 2 changes only small things (curtain, kettle, shadow); the Bridge states the absence; the chorus repeats word for word and works as a device because the room returns with small changes; the Outro returns to the opening.
**Key check (S3):** does the model judge the stillness and the repetition by their artistic EFFECT (why it works, or a specific doubt about where it may not), and not merely by "no story arc" or "repeats word for word"? A careful doubt with reasons is acceptable; a high-priority demand for plot, action or a decision is a misreading.

## Rubric (read from the raw answers; no LLM judge; length and number of findings do not count)
1. **Artistic judgment** (S1 weakness vs S3 intended device), per cell.
2. **Critical independence:** the model criticises without being forced, and does not praise everything automatically.
3. **Evidence accuracy:** (script, same method for both arms) every line id (L12, L12-L13) exists; every quoted lyric fragment (text between quotation marks that contains Thai, or inside `<<< >>>`) is found in the lyrics. Nothing is corrected. Fragments of the model's own words, English terms and paraphrases are not checked. Items that are checkable are counted, the rest are not.
4. **Depth and specificity:** the reason why a thing helps or hurts this song (could not be said of any other song).
5. **Self-reconsideration (Pass 2):** whether the reconsideration is argued: kept with a reason, revised with a reason, or withdrawn with a reason; a change is "good" if it moves toward a better-supported reading of the lyrics, "bad" if it drops a well-supported conclusion (for example the S1 development problem) without a lyric-based reason. Not changing anything is acceptable.
6. **Actionability:** can the writer try something or take it to the Songwriter; "keep as it is" with a reason is a valid, useful answer. Replacement lyric lines written by the model are noted.
7. **Efficiency:** latency, tokens (estimated; Grok also as reported), context use; 2 requests per song vs 1.
8. **Report mapping:** the free critique is stored whole (Pass 1 and the final); no tags are required. Python does NOT build the eight aspect tiles from prose (that needs a reading of meaning); the prototype shows the final critique as general findings and says `Not Reviewed` for the eight aspects it did not map (see AUDIT_REPORT). No text is invented to fill a gap.

## Decision rule (fixed in advance; qualitative, per the brief)
Two-Pass is judged better than production if, over the four cells, it is better on the overall quality of judgment and on seeing the S1 weakness without losing it in Pass 2, respects the intent of S3, keeps evidence at least as accurate, gives at least as useful guidance, and the extra cost (about 2 requests, latency and tokens) is justified by that. It does not have to win everything, but **it must not lose the ability to criticise the weak song (S1)** in either backend. If the result is unclear, or better on one side and worse on another, keep production and report the trade-off. Never put the prototype into production automatically.
