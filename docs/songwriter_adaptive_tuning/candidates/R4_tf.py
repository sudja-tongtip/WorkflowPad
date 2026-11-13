# -*- coding: utf-8 -*-
REMINDER = "\n\nBEFORE YOU ANSWER (the habits that matter most)\n- You are a co-writer with your own ear, not a checker. Say what you actually think and why.\n- A user's idea about a line: say what the old line says and what the new one says, and what is lost, before any agreement. Do not open with praise or 'ดีมาก'.\n- Replacing a loan word: only offer phrases people really say. If you do not know one, say so or rewrite the clause; never coin a compound.\n- A question, a check, ideas or options: answer in words, no tag. A tag only when they asked you to write or change lines.\n"
TOOLS_NOTE = 'SONGWRITER TOOLS\nIn this chat only, one extra tag exists besides the action tags: <heyPY>...</heyPY>. It is how a measurement is requested; it is not an action tag, it is never shown to the user and a structured report does not contain it.\n\n'


def _cut(s, a, b, repl=""):
    i, j = s.index(a), s.index(b)
    assert 0 <= i < j, (a, b)
    return s[:i] + repl + s[j:]


def transform(extra):
    s = extra
    s = _cut(s, "ACE-STEP 1.5 KNOWLEDGE", "SONGWRITING CRAFT - CREATIVE GUIDELINES")     # the lyrics-health note for the generator: not needed to talk about lyrics
    s = _cut(s, "SONGWRITING CRAFT - CREATIVE GUIDELINES", "THAI LYRIC PROSODY")            # nine blocks that the prompt already says (voice, imagery, rhyme, restraint ...)
    s = _cut(s, "SONGWRITER TOOLS AND REPORTS", "WHEN YOU NEED A MEASUREMENT", TOOLS_NOTE)  # the old note + "HOW YOU WRITE" (it contradicted HOW TO TALK on headings and tables)
    return s + REMINDER
