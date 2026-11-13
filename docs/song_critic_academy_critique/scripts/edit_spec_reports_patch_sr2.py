# -*- coding: utf-8 -*-
import sys
p = sys.argv[1]
raw = open(p, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")


def sub(old, new):
    global t
    assert t.count(old) == 1, (t.count(old), old[:90])
    t = t.replace(old, new)


sub('''def _finding_text(f: dict, lyrics_then: str, full: bool = True, old_numbers: bool = False) -> str:
    refs = _refs_of(f)
    where = refs_label(refs) if refs else (f"lines not readable: {f['lines_invalid']}" if f.get("lines_invalid") else "whole song")
    out = [f"[{f.get('kind', '?')}{(' · ' + f['priority']) if f.get('priority') else ''}] lines {where}" + (" (numbered as in the OLD lyrics of the analysis)" if old_numbers and refs else "")]
    rows = lines_of(lyrics_then)''', '''def section_at(rows: list, n: int) -> str:
    """The name of the section (the last [tag] line above line n, a [Hold] cue is not a section) or an empty string: a fact read from the lyrics, never a guess."""
    for i in range(min(n, len(rows)) - 1, -1, -1):
        m = re.match(r"^\\s*\\[([^\\]]+)\\]\\s*$", rows[i] or "")
        if m and not m.group(1).strip().lower().startswith("hold"):
            return m.group(1).strip()
    return ""


def _finding_text(f: dict, lyrics_then: str, full: bool = True, old_numbers: bool = False) -> str:
    refs = _refs_of(f)
    where = refs_label(refs) if refs else (f"lines not readable: {f['lines_invalid']}" if f.get("lines_invalid") else "whole song")
    rows = lines_of(lyrics_then)
    sec = section_at(rows, refs[0][0]) if refs else ""
    st = f.get("status")
    out = [f"[{f.get('kind', '?')}{(' · ' + f['priority']) if f.get('priority') else ''}] " + (f"aspect {f['aspect']} · status {st} · " if f.get("aspect") else "") + f"lines {where}" + (f" · section: {sec}" if sec else "")
           + (" (numbered as in the OLD lyrics of the analysis)" if old_numbers and refs else "")]''')
sub('''    for lab, key in (("Found", "observation"), ("Why it matters", "interpretation"), ("Suggested action", "recommendation"), ("Trade-off / keep", "keep")):''',
    '''    for lab, key in (("Summary", "summary"), ("Found", "observation"), ("Why it matters", "interpretation"), ("Suggested action", "recommendation"), ("Trade-off / keep", "keep")):''')
sub('''    head = [HANDOFF_HEAD, f"Song: {title or '-'} · song id {song_id} · sheet id {sheet_id}",''', '''    import hashlib
    h_now, h_then = (hashlib.sha1((x or "").encode("utf-8")).hexdigest()[:10] for x in (lyrics_now, lyrics_then))
    head = [HANDOFF_HEAD, f"Song: {title or '-'} · song id {song_id} · sheet id {sheet_id} · lyrics version (hash) now {h_now}, the report was made on {h_then}" + (" (the same)" if h_now == h_then else " (DIFFERENT)"),''')
open(p, "wb").write((t.replace("\n", "\r\n") if crlf else t).encode("utf-8"))
print("ok")
