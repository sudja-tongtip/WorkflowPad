# -*- coding: utf-8 -*-
"""make_cand.py <name> <head md file>  -> cand/<name>_songwriter.md (head + the production REPORT section, untouched) and cand/<name>_tf.py (a transform of the program-added chat text: see TF_SPEC in this file)."""
import sys
from pathlib import Path

SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
name, head = sys.argv[1], Path(sys.argv[2])
prod = Path("D:/AI/MyApps/WorkflowPad/music_studio/prompts/specialists/songwriter.md").read_text(encoding="utf-8").replace("\r\n", "\n")
import re
rep = prod[re.search(r"(?m)^# \[REPORT\]$", prod).start():]
plines = prod.split(chr(10))
comment = plines[1] if plines[1].startswith("<!--") else ""
h = head.read_text(encoding="utf-8").rstrip(chr(10))
h = h.replace("# [COMMON]" + chr(10), "# [COMMON]" + chr(10) + (comment + chr(10) if comment else ""), 1)
md = h + chr(10) * 2 + rep
(SCR / "cand" / f"{name}_songwriter.md").write_text(md, encoding="utf-8")

TOOLS_NOTE = ("SONGWRITER TOOLS\nIn this chat only, one extra tag exists besides the action tags: <heyPY>...</heyPY>. It is how a measurement is requested; it is not an action tag, it is never shown to the user "
              "and a structured report does not contain it.\n\n")
tf = f'''# -*- coding: utf-8 -*-
TOOLS_NOTE = {TOOLS_NOTE!r}


def _cut(s, a, b, repl=""):
    i, j = s.index(a), s.index(b)
    assert 0 <= i < j, (a, b)
    return s[:i] + repl + s[j:]


def transform(extra):
    s = extra
    s = _cut(s, "ACE-STEP 1.5 KNOWLEDGE", "SONGWRITING CRAFT - CREATIVE GUIDELINES")     # the lyrics-health note for the generator: not needed to talk about lyrics
    s = _cut(s, "SONGWRITING CRAFT - CREATIVE GUIDELINES", "THAI LYRIC PROSODY")            # nine blocks that the prompt already says (voice, imagery, rhyme, restraint ...)
    s = _cut(s, "SONGWRITER TOOLS AND REPORTS", "WHEN YOU NEED A MEASUREMENT", TOOLS_NOTE)  # the old note + "HOW YOU WRITE" (it contradicted HOW TO TALK on headings and tables)
    return s
'''
(SCR / "cand" / f"{name}_tf.py").write_text(tf, encoding="utf-8")
print("written", name)
