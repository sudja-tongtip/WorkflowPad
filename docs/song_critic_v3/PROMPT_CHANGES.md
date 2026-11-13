# PROMPT_CHANGES (Song Critic V3)

**Production prompt: unchanged.** `music_studio/prompts/specialists/song_critic.md` (5,847 bytes) is byte-identical to HEAD. Its hash is the baseline of the 20 runs (`PRE_REGISTRATION.txt`: sha256 0761dada...).
There is exactly ONE candidate: `CANDIDATE_PROMPT.md` (8,905 bytes, +52 %; sha256 df048bf5... fixed before the runs). The full line diff is `PROMPT_DIFF.patch`.

## What the candidate changes, and why
| Change | Why |
|---|---|
| Primary goal reworded: "what is this song trying to communicate, and does the text succeed on its own terms?" then **review eight aspects** | the brief asks for a complete review with eight fixed aspects |
| "Responsibilities": separate intention / what the words do / what to keep / what might improve; "intent is not result" | avoid both "it is deliberate, so fine" and "it repeats, so a flaw" |
| `no_issue` / `insufficient` stated as normal answers; "no song has to have a story arc, hook, bridge..." | statuses are never forced; atmosphere songs must not be pushed toward plot |
| **New `# Songcraft Reference`** (7 bullets, ~1.6 KB) | the knowledge must be inside the request (see SONGCRAFT_KNOWLEDGE.md); kept short instead of a long system prompt |
| Output format: `[VERDICT.1]`, 8x `[ASPECT.n]` (aspect, status, pattern, priority, lines, quote, observation, interpretation, recommendation, keep), 0-3 `[ACTION.n]`, `[SECTION.n]`, `[NOTE.1]`, `[DONE]` | Finding -> Evidence -> Impact -> Suggested action -> Trade-off -> Keep original |
| Few-shot: 2 aspects + 1 action, Thai, marked "format and tone only; never quote" | shows the shape; this is the only example in the prompt |
| Rules: no rhyme/syllable/word-choice judgments, no audio claims, never write replacement lyrics | scope stays with the other specialists |

Unchanged: Numbered Clear Tags (`[TAG.n]` ... `[DONE]`), `lines:` as separate references (never joined), verbatim `quote` blocks `<<< >>>`, "answer only in tags".

## Result
The candidate did **not** pass the pre-registered adoption rule (QUALITY_COMPARISON.md), so **it is not installed**. The production prompt keeps producing the previous report (verdict + up to 4 findings + sections). The new report format, parser, page, Action Plan and handoff are in the code and read a Songcraft report whenever one is saved; they are dormant until a prompt is adopted.

## Prompt version / Outdated
Reports record the prompt hash (`specialists.py`, existing mechanism). Installing the candidate later changes the hash, so every report made with the old prompt is shown as Outdated by the existing `outdated_why` logic; no extra version field was added. `sr["format"] == "songcraft-1"` marks the new layout; its absence is the legacy layout. `SCHEMA` was not bumped.
