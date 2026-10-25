"""Deterministic lyrics compiler (no LLM, no I/O).

Input is the lyrics text exactly as the user sees it in Text mode. Output:
  ace      text for ACE-Step: the same words, with HTML/XML tags removed (the model would SING them) and light layout cleanup
  original only the words to sing: tag lines and the [Hold] marker removed
  for_ai   the ACE-Step text plus a short legend of the techniques used
  lines    per-line classification for Render mode
  warnings list of (level, line_no, message): "warning" = something was changed or will not work, "info" = experimental technique
Original words are never reworded: only tag markup is removed, and every removal is reported.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

STRUCT_WORDS = {"intro": "intro", "verse": "verse", "pre-chorus": "prechorus", "pre chorus": "prechorus", "prechorus": "prechorus", "chorus": "chorus",
                "bridge": "bridge", "outro": "outro", "instrumental": "instr", "guitar solo": "solo", "electric guitar solo": "solo", "drum break": "drums",
                "breakdown": "break", "build": "build", "drop": "drop", "interlude": "instr", "solo": "solo", "hook": "chorus", "refrain": "chorus",
                "final chorus": "chorus", "fade out": "outro"}
VOCAL_WORDS = ("whisper", "falsetto", "powerful belting", "belting", "spoken word", "harmon", "ad-lib", "raspy", "soft vocals", "breathy", "gritty", "choir", "emotional vocals", "operatic", "male", "female", "duet", "call and response")

TAG_LINE = re.compile(r"^\s*\[[^\[\]]*\]\s*$")
HOLD_PREFIX = re.compile(r"^(\s*)\[Hold\]\s*", re.IGNORECASE)
MARKUP = re.compile(r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s[^<>\n]*)?/?>")        # <whisper>, </strong>, <hold:long>, <br/>


@dataclass
class Compiled:
    ace: str = ""
    original: str = ""
    for_ai: str = ""
    lines: list = field(default_factory=list)       # [(kind, text)]: kind = tag|hold|text|blank, tag also carries its class in .tagclass via lines_tag
    tag_class: dict = field(default_factory=dict)   # line index -> section class (verse, chorus, ...)
    warnings: list = field(default_factory=list)    # (level, line_no, text)


def tag_class_of(line: str) -> str:
    """Section class of a [Tag - descriptor] line ('verse', 'chorus', ..., 'cue' for a bare vocal cue, 'other')."""
    inner = line.strip()[1:-1].split(" - ", 1)[0].strip().lower()
    base = re.sub(r"\s*\d+$", "", inner)
    if base in STRUCT_WORDS:
        return STRUCT_WORDS[base]
    if base == "hold":
        return "hold"
    if any(w in inner for w in VOCAL_WORDS) or "energy" in inner:
        return "cue"
    return "other"


def compile_lyrics(text: str) -> Compiled:
    r = Compiled()
    src = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ace_lines, orig_lines = [], []
    used_hold = used_cue = used_harm = False
    for n, raw in enumerate(src, 1):
        line = raw.rstrip()
        # unsupported HTML/XML tags: remove the tag, keep the words, say so
        if MARKUP.search(line):
            found = sorted({m.group(0) for m in MARKUP.finditer(line)})
            line = MARKUP.sub("", line).rstrip()
            r.warnings.append(("warning", n, "Removed tags the model would sing instead of obey: " + " ".join(found)))
        if not line.strip():
            r.lines.append(("blank", ""))
            ace_lines.append("")
            orig_lines.append("")
            continue
        if TAG_LINE.match(line):
            cls = tag_class_of(line)
            r.lines.append(("tag", line.strip()))
            r.tag_class[len(r.lines) - 1] = cls
            ace_lines.append(line.strip())
            if cls == "other":
                r.warnings.append(("info", n, f"{line.strip()} is not a documented ACE-Step tag; it is sent as written"))
            if cls == "cue":
                used_cue = True
            continue
        m = HOLD_PREFIX.match(line)
        if m:
            used_hold = True
            r.lines.append(("hold", line.strip()))
            ace_lines.append(line.strip())
            orig_lines.append(line[m.end():])
            continue
        if "(" in line and ")" in line:
            used_harm = True
        r.lines.append(("text", line))
        ace_lines.append(line)
        orig_lines.append(line)
    # collapse runs of blank lines to one (layout only)
    def squeeze(ls):
        out = []
        for x in ls:
            if x == "" and out and out[-1] == "":
                continue
            out.append(x)
        while out and out[0] == "":
            out.pop(0)
        while out and out[-1] == "":
            out.pop()
        return out
    r.ace = "\n".join(squeeze(ace_lines))
    r.original = "\n".join(squeeze(orig_lines))
    if not any(k in ("text", "hold") for k, _ in r.lines):
        r.warnings.append(("warning", 0, "There are no sung lines yet (only tags)"))
    if used_hold:
        r.warnings.append(("info", 0, "[Hold] and stretched vowels are prompt techniques: the model may or may not hold the note"))
    if used_cue:
        r.warnings.append(("info", 0, "Vocal cues in [brackets] are hints; results vary by language and voice"))
    legend = []
    if used_hold:
        legend.append("- A line starting with [Hold] asks for a held note on the stretched vowels (romanized / elongated spelling).")
    if used_cue:
        legend.append("- [Section - descriptor] sets the style of that part, for example [Chorus - powerful belting].")
    if used_harm:
        legend.append("- Words in (parentheses) are backing vocals / harmonies.")
    legend.append("- Lines are sung in order; '...' marks a pause.")
    r.for_ai = r.ace + "\n\n---\nNotes about the markup (ACE-Step 1.5 lyrics format):\n" + "\n".join(legend)
    return r


# ── language of the lyrics ────────────────────────────────────────────────────────────────────────────────
_SCRIPTS = (("th", 0x0E00, 0x0E7F), ("ko", 0xAC00, 0xD7AF), ("ko", 0x1100, 0x11FF), ("ja", 0x3040, 0x30FF), ("zh", 0x4E00, 0x9FFF), ("ru", 0x0400, 0x04FF),
            ("ar", 0x0600, 0x06FF), ("hi", 0x0900, 0x097F))


def detect_language(text: str) -> str:
    """ACE-Step language code guessed from the SCRIPT of the sung words (tag lines like [Verse] are ignored). Thai, Japanese, Korean, Chinese, Russian, Arabic and Hindi are
    certain from the letters; Latin letters cannot tell English from Spanish or French, so they give 'en' (set the language by hand for those)."""
    counts: dict[str, int] = {}
    latin = 0
    for line in (text or "").splitlines():
        if TAG_LINE.match(line):
            continue
        for ch in line:
            o = ord(ch)
            for code, lo, hi in _SCRIPTS:
                if lo <= o <= hi:
                    counts[code] = counts.get(code, 0) + 1
                    break
            else:
                if ch.isalpha() and o < 0x250:
                    latin += 1
    if counts.get("ja"):                                   # kana is Japanese even when kanji (zh range) are mixed in
        return "ja"
    total = sum(counts.values()) + latin
    if counts and total:
        code, n = max(counts.items(), key=lambda kv: kv[1])
        if n / total >= 0.25:
            return code
    return "en"
