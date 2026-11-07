"""UI switches for Music Studio.

Cover and Repaint stay in the program. These two values only decide whether the window shows them and whether Generate may take the Cover path.
Turning one back to True shows that feature again. Nothing here writes a song, a take, or a setting.
"""

ENABLE_COVER = False
ENABLE_REPAINT = False

# The lyrics field: True = a plain multi-line TextField (wraps long lines, normal selection), False = the flet-code-editor CodeEditor (line numbers, no wrapping).
LYRICS_TEXTFIELD = False

# Analyze Song and the rhyme colours take their rhyme evidence from the PYTHON Rhyme Analysis (rhyme_map.python_links through python_rhyme_evidence). True = no Rhyme Analyzer AI request in Analyze Song and a saved
# AI result is not merged into the colours. False = the previous behaviour (the Rhyme Analyzer AI runs first and its answer is the evidence). The legacy Rhyme Analyzer code stays either way.
PYTHON_RHYME_PRIMARY = True
