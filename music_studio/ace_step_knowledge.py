"""ACE-Step 1.5 knowledge for the LLM, in short rules the model can act on. NOT the whole tutorial: only what a songwriting / arranging assistant needs.

Every rule is tagged by where it comes from, so an answer can tell what is confirmed from what is advice:
  (G) = stated in the official ACE-Step 1.5 Tutorial (docs/en/Tutorial.md, https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/Tutorial.md)
  (S) = Music Studio's own design guidance: a heuristic, not an ACE-Step guarantee.

Delivery: both backends (local LLM through ComfyUI, Grok) are stateless here (a fresh session and the system prompt are sent with EVERY request), so the same text is simply part of
each request's system prompt. It is built here, separate from any task rules or output format, and is versioned: change a rule -> bump KNOWLEDGE_VERSION.
No UI, no I/O.
"""
from __future__ import annotations

KNOWLEDGE_VERSION = "ace-knowledge-v2"
SOURCE = "https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/Tutorial.md"

TOPICS = {
    "caption": (
        "CAPTION (the Music Style Prompt) - the most influential input; it describes the music as a whole.\n"
        "(G) Cover: genre / sub-genre, emotion, instruments, timbre and texture, era reference, production style, vocal characteristics, how the arrangement develops. "
        "Style words, comma-separated tags or natural sentences all work.\n"
        "(G) Be specific and combine dimensions: not 'a sad song' but 'sad piano ballad with female breathy vocal'. Texture words (warm, crisp, airy, punchy) shape the mix.\n"
        "(G) Fewer details leave the model more freedom; more details constrain it. Contradicting styles degrade the result; a change over time is fine "
        "('start with soft strings, middle becomes dynamic rock, end turns to hip-hop').\n"
        "(G) Keep tempo, BPM and key OUT of the caption: they belong to the metadata fields.\n"
        "(S) 25-60 words, concrete nouns (instruments, textures), the progression across sections, no repeated empty adjectives, no contradictions, no lyrics, no artist names. "
        "The model does not follow every word: a caption is a direction, not a command. "
        "Do not turn rain, night, or fire in the lyrics into sound effects unless the user asked. High energy is drive, not a high singing register."),
    "structure": (
        "LYRICS AS A TEMPORAL SCRIPT - the lyrics field controls how the music unfolds in time.\n"
        "(G) Structure tags: Intro, Verse, Pre-Chorus, Chorus, Bridge, Outro, Build, Drop, Breakdown, Instrumental, Guitar Solo, Piano Interlude, Fade Out, Silence. "
        "Join a section with a hyphen to say how it is performed: [Chorus - anthemic]. Blank line between sections. [Instrumental] alone = no vocals.\n"
        "(S) Section functions: Intro opens the atmosphere; Verse tells or develops the story; Pre-Chorus connects and builds momentum; Chorus is the memorable main idea; "
        "Bridge changes mood, viewpoint or sound; Instrumental is a part without singing; Outro closes. Build / Drop / Breakdown only when the genre calls for them.\n"
        "(S) Not every song has every section. The template the user picks is a target frame: leaving a section empty is better than filling it with repeated or invented lines."),
    "vocal": (
        "VOCAL DELIVERY AND ENERGY TAGS.\n"
        "(G) Vocal hints: raspy vocal, whispered, falsetto, powerful belting, spoken word, harmonies, call and response, ad-lib. "
        "Energy / emotion hints: high energy, low energy, building energy, explosive, melancholic, euphoric, dreamy, aggressive. "
        "Text in (parentheses) is treated as backing vocals. UPPERCASE lyrics suggest stronger intensity. Stretching vowels is unstable.\n"
        "(S) Match the delivery to the section when it fits this song: e.g. a trap verse -> spoken word / rhythmic delivery; a soft pre-chorus -> breathy vocals; "
        "an emotional chorus can be full without belting; a bridge -> whispered / intimate. These are examples, not rules for every genre. "
        "Name powerful belting, soaring, falsetto, or operatic only when the user asked. "
        "For Thai, prefer natural pronunciation, a comfortable range, breath, and controlled peaks, and put impact in the arrangement. Do not force every song into a mid range."),
    "dynamics": (
        "ENERGY AND DYNAMICS ACROSS THE SONG.\n"
        "(S) Plan a progression that fits THIS song, for instance: intro atmospheric -> verse restrained -> pre-chorus building -> chorus full -> bridge stripped back -> final chorus "
        "expanded -> outro fading. Do not apply this formula to every song; a ballad, a trap track and a rock anthem move differently.\n"
        "(S) The caption should describe the same progression the section hints ask for."),
    "tags": (
        "TAG DISCIPLINE.\n"
        "(G) Keep tags concise and put complex style descriptions in the caption; stacked tags can be sung as lyrics and confuse the model. "
        "Caption and lyrics must agree (instruments, emotion, vocal): the model handles conflicts badly "
        "(example conflict: caption 'violin solo, classical, intimate chamber music' with [Guitar Solo - electric - distorted]).\n"
        "(S) At most one or two short hints per tag, from well known cue words. Never invent a tag and claim ACE-Step surely supports it. "
        "Keep the user's own tags, [Hold] lines, inline cues and (backing vocals) exactly as written."),
    "metadata": (
        "METADATA - these values are GUIDANCE the model samples around, not exact commands (120 BPM may come out as 118 or 122).\n"
        "(G) bpm 30-300: slow 60-80, mid-tempo 90-120, fast 130-180; extremes are less stable. keyscale like 'C major' / 'A minor'; common keys are stable, rare keys may be ignored. "
        "timesignature: 4/4 is the most reliable, 3/4 and 6/8 usually fine, 5/4 or 7/8 are advanced. duration: 30-60 s and 2-4 minutes are stable; very long may repeat or break "
        "structure. The vocal language is normally detected from the lyrics. A caption that contradicts the metadata (a 'slow ballad' with bpm 160) confuses the model.\n"
        "(S) Decide from genre, emotion, rhythm and the user's own preferences together. A sad song is not always minor and pop is not always 120 BPM. "
        "Leave the user's existing choice alone unless the new arrangement really needs another."),
    "singability": (
        "SINGABILITY.\n"
        "(G) Roughly 6-10 syllables per line; matching lines of similar sections should be about the same length (+-1-2 syllables); very different adjacent lines make the rhythm odd. "
        "Watch for lines too long for one breath, forced rhymes, adjective stacking and mixed metaphors.\n"
        "(S) A heuristic only: not every line must be 6-10 syllables. The program has no audio and no melody, so never claim to know how a line will actually be sung. "
        "When arranging, never edit the user's lyrics for singability."),
    "limits": (
        "LIMITS OF ACE-STEP AND THIS PROGRAM.\n"
        "(G) Text is a reduced description of audio, so results vary between generations and some instructions are ignored. "
        "Turbo models are the recommended daily choice (8 steps); SFT uses more steps and supports CFG; Base is needed for extract / lego / complete tasks.\n"
        "(S) This program generates a song from caption + lyrics + metadata through one ComfyUI workflow. It cannot edit audio, repaint, cover, write LRC or change the model, workflow, "
        "seed or sampler for you. Do not promise abilities the workflow does not have; separate what the guide confirms from your own suggestions."),
}

ARRANGEMENT_TOPICS = ("caption", "structure", "vocal", "dynamics", "tags", "metadata", "singability", "limits")
CHAT_TOPICS = ("caption", "structure", "vocal", "dynamics", "tags", "metadata", "singability", "limits")


def knowledge(topics=ARRANGEMENT_TOPICS) -> str:
    """The knowledge block for a system prompt (versioned header + the chosen topics)."""
    body = "\n\n".join(TOPICS[t] for t in topics)
    return f"ACE-STEP 1.5 KNOWLEDGE ({KNOWLEDGE_VERSION}; (G) = official tutorial, (S) = studio guidance, a heuristic)\n\n{body}"
