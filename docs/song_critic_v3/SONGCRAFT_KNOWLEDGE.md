# SONGCRAFT_KNOWLEDGE (Song Critic V3)

What the Song Critic is taught about songcraft, where it comes from, and how it reaches the model.

## How it is loaded (not just written down)
The distilled principles are the `# Songcraft Reference` section of the candidate prompt (`CANDIDATE_PROMPT.md`, lines 20-26, about 1.6 KB). The prompt file is read by `specialists.py`, passed through `SP.check` (extra sections are allowed) and sent as the system message of every Song Critic request. It is therefore in each of the 10 candidate raw requests (`RAW_REQUESTS/*_candidate_request.json`). It is short on purpose: the long form below is for the maintainer and is NOT sent.
The production prompt (`music_studio/prompts/specialists/song_critic.md`) has **no** Songcraft Reference and is unchanged, because the candidate was not adopted (see QUALITY_COMPARISON.md).

## Principles (long form, for the maintainer)
1. **Verses develop the idea.** A verse that says the same thing in new words stalls the song ("stagnant verse"). Each verse should move the idea: a new fact, a new angle, a consequence.
2. **A chorus/refrain takes its meaning from the verses around it.** Over stagnant verses a refrain wears thin; over developing verses each return can mean more. A contrast (bridge, change of view) can renew a last chorus.
3. **The title/hook is a foundation** the sections move toward. A specific hook works harder than a general one, and it can be developed (not only repeated).
4. **Images work when** they are specific (a detail only this song has), tied to the central idea, and consistent in tone.
5. **Openings** are read by what they promise and whether the song keeps or answers it. Patterns used as vocabulary (a song may combine several): Scene, Emotional Declaration, Question, In Medias Res, Dialogue, Surprise/Contradiction, Hook/Thesis First, Atmospheric/Symbolic. They are reading tools, not rules; the UI says so.
6. **Endings** resolve, deliberately stay open, or return to the opening with a changed meaning. The review judges the payoff of what the song set up.
7. **Intent is not result.** "It is deliberate" or "it is abstract" does not prove that it works, and "it repeats" does not prove that it fails. Judge each by its effect on a listener; say why.
8. **No requirement of structure.** No song has to have a story arc, hook, bridge, climax or twist. `no_issue` and `insufficient` are normal answers.
9. **Scope.** No rhyme, syllable, singability or word-choice judgments (other specialists), no claims about melody or audio, no replacement lyrics.

## Sources and how far they were used
| Source | What it supports | Status |
|---|---|---|
| Pat Pattison, Berklee / strongwriter.com, notes on *verse development* | principles 1, 2 (stagnant verses, refrain meaning from context, contrast renews a chorus) | read and paraphrased; no text copied |
| Pat Pattison, Berklee Online article on the *song title* | principle 3 (title as foundation the song moves toward) | read and paraphrased |
| Berklee Online, *Lyric Writing* course outline (the second course) | topics list: section purpose, development, imagery | outline only (public course page); no course material used |
| Opening-pattern list (principle 5) | a working vocabulary | **general lyric-craft vocabulary, assembled by the author of this change. It is NOT a verified citation of Berklee or Pattison** and the UI does not attribute it to them. |

Honest limit: this is a distilled paraphrase of public articles and an outline, not a licensed curriculum. It was not checked by a songwriting teacher. The wording "After Pat Pattison (Berklee)" in the prompt means "in the tradition of", not "endorsed by".
