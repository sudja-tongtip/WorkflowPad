# AUDIT_REPORT: Song Critic Free-Thinking Two-Pass Prototype V2

Status: prototype + A/B + audit done in one pass. **Production untouched, nothing committed, not adopted.**

## Outcome
A minimal system prompt (about 142 est. tokens) with a free critique (Pass 1) and one reconsideration (Pass 2) in the same session cost little (2 requests, 1.5-1.6x latency on Gemma, input tokens 1.3-1.4x on Grok), and it removed the one false alarm the one-shot made on the atmosphere song (S3, Gemma). But it **lost the S1 judgment**: Gemma's Pass 2 withdrew its doubts and called the song a high success "in the context of Dream Pop"; Grok saw the problem in Pass 1 and softened it in Pass 2. By the pre-registered condition ("must not lose the ability to criticise the weak song"): **keep production one-shot.**

## What was done
- `prompts_fr.py`: the minimal system prompt and the two task prompts exactly as in the brief; no checklist, format or examples. Pass 1 carries the song once (title `Test Song 01`/`03`, style, numbered lyrics, program-counted structure, returning lines, reply language: the same data production sends); Pass 2 is the brief's short text only.
- `run_fr.py`: 4 one-shot requests (production prompt, same pipeline and user message) + 8 pass requests, temperature 0.4, no retry, new session per song. Raw output in a temporary directory during the run, copied here afterwards.
- `evaluate_fr.py`: line ids, quoted Thai fragments, cost. No quality judgment by script, and no eight-tile mapping (see KNOWN_LIMITATIONS 4).
- Pre-registration with hashes and time (13:02:56) before the first request.

## Real examples (full texts in `FINAL_CRITIQUES/`)
**Useful**
- *Grok S1, Pass 1:* "Verse 2 uses imagery too close to Verse 1, so it feels like circling instead of widening the song's world; three identical choruses ... may make a low-energy song too still; the bridge goes back to the same image without added tension or release." Specific, true of this song, and it points at the right place. (The production one-shot said the same more sharply, with a high concern.)
- *Grok S3, Pass 2:* the repetition of rain on the roof and the blue light "helps the sense of stopped time"; the bridge ("no one opens the door / no one turns off the light") is "the clearest and weightiest" statement of emptiness; a conditional doubt ("some listeners may feel the feeling does not move, even if that is intended") and a small, lyric-free suggestion. It judges stillness by effect, as the brief asks.
- *Gemma S3, both passes:* the observation that the song moves from still images (rain, light) to moving ones (curtain, drops) to a sense of time (the shadow gets longer, L016) is accurate and is exactly why the repeated chorus is not a flaw here. A better reason than the one-shot's "Verse 2 needs an action".

**Wrong or misleading**
- *Gemma S1, Pass 2:* "I earlier worried the song was predictable; now I see the predictability is the intention ... a high success in the context of Dream Pop; do not try to break this cycle." The withdrawal rests on intention and genre, with no lyric evidence; the lyrics still repeat three identical choruses and a line unchanged. This is the failure the brief warns about, and the strongest result of this test.
- *Gemma S1, Pass 2, guidance:* "raise the instrumentation density and push the tempo in Chorus 3": a claim about sound and an arrangement tip, outside a lyric critic's job (the model has not heard the song).
- *Gemma S3, Pass 2:* rewritten lines for the Bridge ("ประตูยังคงรอใครสักคนเปิด / ไฟยังคงรอใครสักคนดับ"): replacement lyrics. The production prompt forbids this; the minimal prompt does not, and Gemma did it (the one-shot Gemma also wrote sample wording in both songs).
- *Gemma S3, production one-shot:* a **high-priority concern** that Verse 2 needs "a decision or an action of the character". A misreading of an atmosphere song: the false alarm this prototype avoided.
- *Grok B* wrote **no line ids**, only quoted phrases, so a reader cannot jump to the lines (the production report always does).

## Session honesty
Gemma: a real continuing session (one ComfyUI session id per song, same id in both passes). Grok: stateless API with the Pass 1 messages replayed in Pass 2. Pass 2 used both the lyrics and the Pass 1 critique in all four sequences (read). Gemma used about 21 % of the context. No truncation or error.

## Files
`QUALITY_COMPARISON.md` (the judgments) · `COST_LATENCY.md` · `SESSION_TRACE.md` (+ `.jsonl`) · `SYSTEM_PROMPT_MINIMAL.md` · `PASS1_PROMPT.md` · `PASS2_PROMPT.md` · `KNOWN_LIMITATIONS.md` · `METRICS.md/.json` · `RAW_REQUESTS/` (12) · `RAW_RESPONSES/` (12) · `FINAL_CRITIQUES/` (the one-shot report, the Pass 1 and the final text of each cell, unedited) · `PRE_REGISTRATION.txt`, `EXPECTED_OBSERVATIONS.md`, scripts, `run.log`.

## Deviations and cautions
1. The evaluation script was written after the run and changed once (range ends), disclosed in KNOWN_LIMITATIONS 3.
2. The script's quote counts overstate inaccuracy; see QUALITY_COMPARISON.
3. No eight-tile mapping was built (brief: Python must not judge meaning); the free critique is stored whole.
4. Two songs, one run each, one reader: indicative only.
