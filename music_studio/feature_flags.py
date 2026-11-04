"""UI switches for Music Studio.

Cover and Repaint stay in the program. These two values only decide whether the window shows them and whether Generate may take the Cover path.
Turning one back to True shows that feature again. Nothing here writes a song, a take, or a setting.
"""

ENABLE_COVER = False
ENABLE_REPAINT = False

# The lyrics field: True = a plain multi-line TextField (wraps long lines, normal selection), False = the flet-code-editor CodeEditor (line numbers, no wrapping).
LYRICS_TEXTFIELD = True
