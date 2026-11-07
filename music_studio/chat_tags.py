"""Natural chat plus optional tagged actions.

The model talks in ordinary sentences. A tag is the only thing that can become
an Apply card, and only when it names an operation the studio already supports.
Python fills the character range from the lyrics snapshot. The model does not
invent indexes. Nothing here calls an LLM or writes a song file.
"""
from __future__ import annotations

import re
import textwrap
from collections import Counter


TAGS = (
    "change_lyric",
    "insert_lyrics",
    "change_cue",
    "change_caption",
    "change_settings",
    "add_section",
    "add_hold",
    "add_line_cue",
    "move_section",
    "repeat_section",
)
SONGWRITER_TAGS = frozenset({"change_lyric", "insert_lyrics"})
PRODUCER_TAGS = frozenset({"change_cue", "change_caption", "change_settings", "add_section", "add_hold", "add_line_cue", "move_section", "repeat_section"})
STRUCTURE_TAGS = frozenset({"add_section", "add_hold", "add_line_cue", "move_section", "repeat_section"})

KIND_LABEL = {
    "change_lyric": "Lyric change",
    "insert_lyrics": "Insert lyrics",
    "change_cue": "Cue change",
    "add_section": "Add section",
    "add_hold": "Held note",
    "add_line_cue": "Line vocal cue",
    "move_section": "Move section",
    "repeat_section": "Repeat section",
}
PREVIEW_HEAD = {
    "change_cue": ("Cue now", "Cue proposed"),
    "add_section": ("Section now", "Section after"),
    "add_hold": ("Line now", "Line after"),
    "add_line_cue": ("Line now", "Line after"),
    "move_section": ("Section now", "Section after"),
    "repeat_section": ("Section now", "Section after"),
}

_NAMES = "|".join(TAGS)
_PAIRED = re.compile(rf"<({_NAMES})\b([^>]*)>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL)
_SELF = re.compile(rf"<({_NAMES})\b([^>]*)/\s*>", re.IGNORECASE | re.DOTALL)
_OPEN = re.compile(rf"<({_NAMES})\b", re.IGNORECASE)
_ATTR = re.compile(r"""([A-Za-z_][\w-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_TAG_TEXT = re.compile(r"\[[^\[\]\n]*\]")

SONGWRITER_CHAT = (
    "HOW YOU ANSWER IN THE CHAT (Songwriter)\n"
    "You are a collaborative music specialist. Understand the user's intent from the conversation. Respond naturally and directly, in the user's language. "
    "You may analyze, critique, explain, brainstorm, write, or suggest revisions as appropriate.\n"
    "Use a supported action tag only when proposing a concrete lyric change the user has requested or clearly authorized. Otherwise, respond in ordinary language. Never apply changes yourself.\n"
    "A tag is only a proposal. The lyric changes only when the user accepts it. If they cancel or reject it, answer in words: do not emit that tag again, and do not say a line was changed or restored.\n"
    "If they ask you to change a cue, the structure, or the caption, say what the words imply and name the Music Producer. Do not write that specialist's tags. Every tag you write is closed exactly like the examples below.\n"
    "Write ordinary sentences in [REPLY.1]. A proposal is [EDIT.n] or [INSERT.n]. End with [DONE] on its own line. Do not use PROPOSAL, EDITS, OPERATIONS, FIELD, KIND, JSON, or XML, and do not wrap the whole song.\n"
    "Allowed tags: change_lyric, insert_lyrics. Do not write change_cue, change_caption, add_section, add_hold, add_line_cue, move_section, or repeat_section. Those belong to the Music Producer.\n"
    "from is always the WHOLE line (or the whole lines) copied exactly from the lyric list, never a part of a line, and to is the whole new line (or lines): never send only the changed words. Copy the target id from the lyric list and the exact current text into from. Do not invent an id or a character index. One tag per change. "
    "Several alternatives for the same place are several [EDIT] tags on the same target.\n"
    "insert_lyrics may start a new section with its [Section] line. change_lyric must not add, drop, or rewrite a section tag.\n"
    "Do not put an action tag inside a sung line. A lyric line that looks like a control tag is written with one leading backslash.\n"
    "In prose say \"บรรทัด 17\". The L001 id belongs only in a tag's target or after field.\n"
    "[REPLY.1]\n<one or two natural sentences>\n"
    "[EDIT.1]\nop: change_lyric\ntarget: L003\nfrom: <<<\nexact current text\n>>>\nto: <<<\nnew text\n>>>\n"
    "[INSERT.1]\nop: insert_lyrics\nafter: L004\ntext: <<<\nthe new lines\n>>>\n"
    "[DONE]\n"
    "after: end appends. Leave the action tags out when you change nothing."
)

PRODUCER_CHAT = (
    "HOW YOU ANSWER IN THE CHAT (Music Producer)\n"
    "You are a collaborative music specialist. Understand the user's intent from the conversation. Respond naturally and directly, in the user's language. "
    "You may analyze, critique, explain, brainstorm, or suggest revisions as appropriate.\n"
    "You never write or change a sung lyric line. Do not emit change_lyric or insert_lyrics. Writing sung lyrics is the Songwriter AI's work.\n"
    "Use a supported action tag only when proposing a concrete change the user has requested or clearly authorized. An opinion, an analysis, or advice they asked you not to turn into a proposal has no tag. Never apply changes yourself.\n"
    "A tag is only a proposal. The song changes only when the user accepts it. If they cancel or reject it, answer in words: do not emit that tag again, and do not say a cue, a section, or the caption was changed or restored.\n"
    "Several alternatives for the same place are several tags on the same target. They are choices, not one combined change. When they asked for alternatives they can accept, write the tags. Do not leave those choices as prose only.\n"
    "When they ask you to write the caption for the song, use one change_caption tag. Caption ideas, with no request to set the caption, stay ordinary language.\n"
    "With that caption you may also propose Music Settings in one [SETTING.1] block: bpm, key, time_signature, groove, reason. Judge them from the genre, mood, lyrics, vocal delivery and the settings now, with no fixed formula. "
    "They are recommendations from the lyrics and the brief, not something heard in audio: never say you detected a key or a tempo. Give only the fields you can justify and leave the others out. bpm is a number; key is a note with major or minor (A minor); time_signature is 2/4, 3/4, 4/4 or 6/8; "
    "groove is a short phrase about the rhythmic feel (laid-back, syncopated, half-time): it is advice, not a time signature. Do not write the bpm, key or time signature into the caption text.\n"
    "Write ordinary sentences in [REPLY.1]. A proposal is [CUE.n], [SECTION.n], [CAPTION.1] or [SETTING.1]. End with [DONE] on its own line. Do not use PROPOSAL, EDITS, OPERATIONS, FIELD, KIND, JSON, or XML.\n"
    "Allowed tags: change_cue, add_section, add_line_cue, add_hold, move_section, repeat_section, change_caption, change_settings. There is no delete tag. Never delete a section.\n"
    "add_line_cue puts one vocal cue on its own line immediately before the target lyric. The lyric characters stay. cues is one of: whispered, raspy vocal, falsetto, powerful belting, spoken word, harmonies, call and response, ad-lib, breathy vocals, soft vocals, emotional vocals. "
    "breathy vocals, soft vocals and emotional vocals are studio guidance, not in the official vocal table. Do not cue every line. Do not stack cues. [Hold] stays after the words on the same line; never on its own line.\n"
    "A cue change always gives the WHOLE old tag line in from (copied exactly from the lyric list) and the WHOLE new tag line in to. Keep the same section name (Verse, Chorus, Bridge, Outro, and the others). Use at most 2 short cues. Use none or one when that is enough; do not pad to 2. Do not pair cues that mean the same thing, such as fast-paced with slightly faster phrasing. A standalone vocal cue is add_line_cue, not one of those 2, and only when one lyric needs it. Do not delete or rewrite a short custom cue the user wrote unless they asked. A long description belongs in the caption, not in the tag. Never send only the changed cues. To clear the cues, to is just the section tag, for example [Verse 1]. "
    "For the other tags you name the target id and the section name. A caption is English, one paragraph, and only when the user asked for one.\n"
    "A lyric line that looks like a control tag is written with one leading backslash.\n"
    "In prose say \"บรรทัด 17\". The L001 id belongs only in a tag.\n"
    "[CUE.1]\nop: change_cue\ntarget: L001\nfrom: <<<\n[Verse 1 - exact current tag line]\n>>>\nto: <<<\n[Verse 1 - whole new tag line]\n>>>\n"
    "[SECTION.1]\nop: add_section\ntarget: L001\nname: Intro\ncues: soft\n"
    "[CUE.2]\nop: add_line_cue\ntarget: L004\ncues: whispered\n"
    "[SECTION.2]\nop: add_hold\ntarget: L003\n"
    "[SECTION.3]\nop: move_section\ntarget: L006\nto: L001\n"
    "[SECTION.4]\nop: repeat_section\ntarget: L006\nto: end\n"
    "[CAPTION.1]\ntext: <<<\nthe English caption\n>>>\n"
    "[SETTING.1]\nbpm: 92\nkey: A minor\ntime_signature: 4/4\ngroove: laid-back, sparse hi-hats\nreason: one sentence, in the user's language\n"
    "[DONE]\n"
    "Leave the action tags out when you change nothing."
)


LINE_REF_RE = re.compile(r"(?<![A-Za-z0-9])L0*(\d{1,4})(?:\s*[-\u2013\u2014~]\s*L?0*(\d{1,4}))?(?!\d)")


def line_ref_parts(text: str) -> list:
    """The text of a chat answer cut into plain strings and line references: ("แถวที่ 4", 4, 4) for L004 and ("แถวที่ 14-16", 14, 16) for L014-L016. Display only: the stored answer is never changed.
    The label is Thai when the answer has Thai in it, otherwise `line 4`. A reference to line 0 is left as written."""
    text = text or ""
    thai = bool(re.search(r"[\u0E00-\u0E7F]", text))
    out, pos = [], 0
    for m in LINE_REF_RE.finditer(text):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b < 1:
            continue
        if b < a:
            a, b = b, a
        if m.start() > pos:
            out.append(text[pos:m.start()])
        span = str(a) if a == b else f"{a}-{b}"
        out.append((("แถวที่ " if thai else ("line " if a == b else "lines ")) + span, a, b))
        pos = m.end()
    if pos < len(text):
        out.append(text[pos:])
    return out


def line_refs_markdown(text: str) -> str:
    """The same, as Markdown links `[แถวที่ 4](wp-line:4-4)` that the chat opens in the editor."""
    return "".join(p if isinstance(p, str) else f"[{p[0]}](wp-line:{p[1]}-{p[2]})" for p in line_ref_parts(text))


def lyric_index(text: str) -> list:
    """One row per line of the editor string. Offsets are Python [start, end) on that same string.

    A final newline makes a phantom empty line whose start and end equal len(text). It is not a lyric target.
    """
    raw = text or ""
    parts = raw.split("\n")
    pos = 0
    last = len(parts) - 1
    out = []
    for i, line in enumerate(parts):
        start = pos
        end = pos + len(line)
        out.append({
            "id": f"L{i + 1:03d}",
            "n": i + 1,
            "text": line,
            "start": start,
            "end": end,
            "phantom": i == last and start == end == len(raw) and raw.endswith("\n"),
        })
        pos = end if i == last else end + 1
    return out


def listed_lines(text: str) -> list:
    """The lines that carry an id in a request: exactly the lines the parser can address (lyric_index without the phantom empty line that a final newline makes). Every context that prints `L001| text` uses this
    one list, so an id shown to the AI is an id the parser accepts. The text itself is never changed; a final newline simply has no line of its own (to add after the last line the answer says `after: end`)."""
    return [ln["text"] for ln in lyric_index(text or "") if not ln["phantom"]]


def _attrs(blob: str) -> dict:
    out = {}
    for m in _ATTR.finditer(blob or ""):
        out[m.group(1).lower()] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


def _xml_text(raw: str) -> str:
    if raw is None:
        return ""
    if "\n" in raw:
        return textwrap.dedent(raw).strip("\n")
    return raw


_ID_PREFIX = re.compile(r"^[ \t]*L\d{3,4}\|[ \t]?")


def strip_line_ids(text: str) -> str:
    """The numbered lyric list the model reads writes each line as `L052| words`. When EVERY non-blank line of a quoted text still carries that prefix, the model copied the list's numbering into the
    lyric: the prefixes are taken off (format only, the words stay). Text where only some lines have it is left as written."""
    rows = (text or "").split("\n")
    filled = [r for r in rows if r.strip()]
    if not filled or not all(_ID_PREFIX.match(r) for r in filled):
        return text
    return "\n".join(_ID_PREFIX.sub("", r, count=1) if r.strip() else r for r in rows)


def _child(body: str, name: str):
    m = re.search(rf"<{name}\b[^>]*>(.*?)</{name}\s*>", body or "", re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    got = _xml_text(m.group(1))
    return strip_line_ids(got) if name in ("from", "to", "text") else got


def _iter_tags(text: str) -> list:
    found = []
    for m in _PAIRED.finditer(text or ""):
        found.append((m.start(), m.end(), m.group(1).lower(), m.group(2) or "", m.group(3) or "", m.group(0)))
    for m in _SELF.finditer(text or ""):
        found.append((m.start(), m.end(), m.group(1).lower(), m.group(2) or "", "", m.group(0)))
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    out, taken = [], 0
    for item in found:
        if item[0] < taken:
            continue
        taken = item[1]
        out.append(item)
    return out


def _line_by_target(lines: list, target: str):
    t = (target or "").strip()
    m = re.fullmatch(r"L(\d+)", t, re.IGNORECASE)
    if not m:
        m = re.fullmatch(r"(?:line\s*)?(\d+)", t, re.IGNORECASE)
    if not m:
        return None
    n = int(m.group(1))
    return next((ln for ln in lines if ln["n"] == n and not ln["phantom"]), None)


def _section_of(lines: list, n: int) -> str:
    name = ""
    for ln in lines:
        if ln["n"] > n:
            break
        t = ln["text"].strip()
        if len(t) >= 2 and t.startswith("[") and t.endswith("]") and not t.lower().startswith("[hold"):
            name = t
    return name


def _tag_counter(text: str) -> Counter:
    return Counter(x for x in _TAG_TEXT.findall(text or "") if x.strip("[] ").lower() != "hold")


def _find_exact(hay: str, needle: str, lo: int = 0, hi: "int | None" = None) -> list:
    if needle == "" or hay is None:
        return []
    if hi is None:
        hi = len(hay)
    hits, k = [], lo
    while k <= hi:
        i = hay.find(needle, k, hi)
        if i < 0:
            break
        hits.append(i)
        k = i + 1
    return hits


def _own(spec: str, kind: str) -> str:
    """'' when this specialist may emit the tag, otherwise who it belongs to."""
    from . import specialists as SP
    producer = SP.is_producer(spec)
    if producer and kind in SONGWRITER_TAGS:
        return "Songwriter"
    if not producer and kind not in SONGWRITER_TAGS:
        return "Music Producer"
    return ""


def _reject(o, label: str, body: str, why: str) -> None:
    shown = (body or "").strip()
    bit = ((label + "\n") if label else "") + shown
    if why:
        bit = (bit + "\n" if bit else "") + "(not applied: " + why + ")"
    if bit.strip():
        o.rejected = ((o.rejected + "\n\n") if o.rejected else "") + bit.strip()
    o.notes.append(why)


def _edit(kind: str, lyrics: str, start: int, end: int, original: str, replacement: str, reason: str, target: str, section: str, song_id: str, sheet_id: str, snap: str, allow_repeat: bool = False) -> dict:
    first = lyrics.count("\n", 0, start) + 1
    last_at = start if end == start else end - 1
    last = lyrics.count("\n", 0, last_at) + 1
    e = {
        "id": "",
        "kind": kind,
        "first": first,
        "last": last,
        "original": original,
        "replacement": replacement,
        "reason": reason or "",
        "status": "open",
        "label": "",
        "start": start,
        "end": end,
        "target": target,
        "section": section,
        "snap_hash": snap,
        "song_id": song_id or "",
        "sheet_id": sheet_id or "",
    }
    if allow_repeat:
        e["allow_repeat"] = True
    return e


def _resolve_change(lyrics: str, lines: list, target: str, frm: str):
    """(start, end) of the exact `from` text, or a sentence. A target is searched only on that line. No similar-text search."""
    if frm == "":
        return "The lyric tag has no <from> text, so nothing was offered."
    if target:
        ln = _line_by_target(lines, target)
        if ln is None:
            return f"Target {target} is not a line in these lyrics, so nothing was offered."
        if "\n" in frm:
            if lyrics[ln["start"]:ln["start"] + len(frm)] != frm:
                return f"The old text in <from> does not match the lines from {ln['id']} as they are now, so the change looks out of date and nothing was offered."
            return ln["start"], ln["start"] + len(frm)
        hits = _find_exact(lyrics, frm, ln["start"], ln["end"])
        if not hits:
            return (f"The old text in <from> does not match {ln['id']} as it is now (the line now says: “{ln['text'].strip()[:80]}”). "
                    "The change looks out of date or quotes the wrong line, so nothing was offered.")
        if len(hits) != 1:
            return f"The text in <from> is found more than once on {ln['id']}, so nothing was offered. Quote the whole line."
        return hits[0], hits[0] + len(frm)
    hits = _find_exact(lyrics, frm)
    if len(hits) != 1:
        return "The text in <from> is missing or appears more than once, and no target id was given, so nothing was offered."
    return hits[0], hits[0] + len(frm)


def _no_change_reason(frm: str, to: str, fitted: str) -> str:
    """'' when the change is a real one. Otherwise the TRUE reason, told apart: the AI wrote the same text in <to> as in <from> (a real no-op), an empty <to>, or a <to> that differs from <from> but was reduced to it by the
    section-header check (prepare_replacement), which is a normalization issue and is said so. Nothing here changes any text."""
    if not (to or "").strip():
        return "The new text in <to> is empty, so nothing was offered. A line is not deleted by a change: write the replacement in <to>."
    if to.strip() == frm.strip():
        return "<to> is the same as <from>: the AI proposed no change to this line, so nothing was offered."
    if not fitted.strip():
        return "Nothing was left of <to> after the repeated section header was removed, so nothing was offered."
    if fitted.strip() == frm.strip():
        return "<to> differs from <from> only by a section header it repeated, which the program removes, so the change is the same as the current text and nothing was offered."
    return ""


def _whole(frm: str, lyrics: str) -> bool:
    if frm == lyrics:
        return True
    return bool(lyrics.endswith("\n")) and frm == lyrics[:-1] and frm != ""


def _append_anchor(lines: list):
    real = [ln for ln in lines if not ln["phantom"]]
    return real[-1] if real else None


def _gap_before(anchor_text: str, new_text: str) -> str:
    new_text = (new_text or "").lstrip("\n")
    if not anchor_text.strip():
        return ""
    if new_text.lstrip().startswith("["):
        return "\n\n"
    return "\n"


def conflicting_ids(edits: list, lyrics: str) -> set:
    """Ids of open tagged edits whose ranges overlap but are not the same span.

    The same span is a set of alternatives: accepting one makes the others stale. A point where one range ends and the next begins is not a conflict.
    A zero-width insert strictly inside another range is a conflict.
    """
    from . import specialist_engine as SE
    open_rows = []
    for e in edits or []:
        if SE.edit_state(e, lyrics) != "open":
            continue
        span = SE.resolve_tagged_span(e, lyrics)
        if isinstance(span, tuple):
            open_rows.append((e, span[0], span[1]))
    bad = set()
    for i, (a, a0, a1) in enumerate(open_rows):
        for b, b0, b1 in open_rows[i + 1:]:
            if a0 == b0 and a1 == b1:
                continue
            if a0 < b1 and b0 < a1:
                bad.add(a["id"])
                bad.add(b["id"])
    return bad


def _mark_alts(edits: list) -> None:
    groups: dict = {}
    for e in edits:
        if not isinstance(e.get("start"), int):
            continue
        groups.setdefault((e["start"], e["end"], e.get("original", "")), []).append(e)
    for grp in groups.values():
        if len(grp) < 2:
            continue
        for k, e in enumerate(grp, 1):
            e["alt"] = {"n": k, "of": len(grp)}


def _selection_ok(has_sel: bool, sel_bounds, start: int, end: int) -> str:
    if not has_sel or not sel_bounds:
        return ""
    try:
        sa, sb = int(sel_bounds[0]), int(sel_bounds[1])
    except (TypeError, ValueError, IndexError):
        return ""
    if sa <= start and end <= sb:
        return ""
    return "This change sits outside the selected text, so it was not offered on the selection."


def _lyric_change(o, spec: str, lyrics: str, lines: list, attrs: dict, body: str, raw_tag: str, has_sel: bool, sel_bounds, song_id: str, sheet_id: str, snap: str) -> dict | None:
    from . import section_apply as SA
    from . import specialist_engine as SE
    frm = _child(body, "from")
    to = _child(body, "to")
    if frm is None or to is None:
        _reject(o, raw_tag, "", "A change_lyric tag needs <from> and <to>, so nothing was offered.")
        return None
    if _whole(frm, lyrics):
        fitted = SA.fold_echoed_sections(lyrics, to.strip("\n"))
        _a, _b, fitted = SA.prepare_replacement(lyrics, 0, len(lyrics), fitted)
        why = SE.proposal_problem(spec, lyrics, fitted)
        if why:
            _reject(o, raw_tag, to, "A proposal was not offered for Apply: " + why)
            return None
        if fitted.strip("\n") == lyrics.strip("\n"):
            return None
        return _edit("change_lyric", lyrics, 0, len(lyrics), lyrics, fitted, _child(body, "reason") or attrs.get("reason", ""), attrs.get("target", ""), "", song_id, sheet_id, snap)
    got = _resolve_change(lyrics, lines, attrs.get("target", ""), frm)
    if isinstance(got, str):
        _reject(o, raw_tag, to, got)
        return None
    start, end = got
    ls = lyrics.rfind("\n", 0, start) + 1                                   # the rest of the first and of the last line must be blank: <from> is the whole line
    le = lyrics.find("\n", end)
    le = len(lyrics) if le < 0 else le
    if lyrics[ls:start].strip() or lyrics[end:le].strip():
        _reject(o, raw_tag, "", "The text in <from> is only a part of a line. Quote the whole line(s) in <from> and give the whole new line(s) in <to>, so nothing was offered.")
        return None
    why = _selection_ok(has_sel, sel_bounds, start, end)
    if why:
        _reject(o, raw_tag, to, why)
        return None
    a, b, fitted = SA.prepare_replacement(lyrics, start, end, to)
    extra = _tag_counter(fitted) - _tag_counter(frm)
    if extra:
        _reject(o, raw_tag, to, "A lyric change must not add or rewrite a section tag (" + next(iter(extra)) + "). A new section is an insert_lyrics tag.")
        return None
    missing = _tag_counter(frm) - _tag_counter(fitted)
    if missing:
        _reject(o, raw_tag, to, "A proposal was not offered for Apply: it dropped or changed the tag " + next(iter(missing)) + ". Section tags and cues are the Music Producer's work; they are kept as they are.")
        return None
    why = _no_change_reason(frm, to, fitted)
    if why:
        _reject(o, raw_tag, to, why)
        return None
    from .lyric_review import Item
    item = Item("probe", "edit", 1, 1, frm, fitted, "")
    done = SE.apply_change(item, lyrics, spec, False, (a, b))
    if isinstance(done, str):
        _reject(o, raw_tag, to, done)
        return None
    n = lyrics.count("\n", 0, a) + 1
    return _edit("change_lyric", lyrics, a, b, lyrics[a:b], fitted, _child(body, "reason") or attrs.get("reason", ""), attrs.get("target", "") or f"L{n:03d}", _section_of(lines, n), song_id, sheet_id, snap)


def _insert(o, lyrics: str, lines: list, attrs: dict, body: str, raw_tag: str, has_sel: bool, sel_bounds, song_id: str, sheet_id: str, snap: str, spec: str):
    from . import section_apply as SA
    from . import specialist_engine as SE
    text = _child(body, "text")
    if text is None:
        text = (attrs.get("text") or "").strip()
    else:
        text = text.strip("\n")
    if not text.strip():
        _reject(o, raw_tag, "", "An insert_lyrics tag has no text, so nothing was offered.")
        return None
    after = (attrs.get("after") or "end").strip()
    if after.lower() == "end":
        anchor = _append_anchor(lines)
        if anchor is None:
            start = end = 0
            original = ""
            replacement = text
        else:
            start, end = anchor["start"], anchor["end"]
            original = anchor["text"]
            replacement = original + _gap_before(original, text) + text
    else:
        anchor = _line_by_target(lines, after)
        if anchor is None:
            _reject(o, raw_tag, text, f"insert_lyrics after {after} is not a line in these lyrics, so nothing was offered.")
            return None
        start, end = anchor["start"], anchor["end"]
        original = anchor["text"]
        replacement = original + _gap_before(original, text) + text
    why = _selection_ok(has_sel, sel_bounds, start, end)
    if why:
        _reject(o, raw_tag, text, why)
        return None
    a, b, fitted = SA.prepare_replacement(lyrics, start, end, replacement)
    if not fitted.strip():
        _reject(o, raw_tag, text, "The insert had no lyric lines after the section check, so nothing was offered.")
        return None
    if fitted.strip() == original.strip():
        _reject(o, raw_tag, text, "The insert repeats the current line, so nothing was offered.")
        return None
    from .lyric_review import Item
    item = Item("probe", "edit", 1, 1, lyrics[a:b], fitted, "")
    done = SE.apply_change(item, lyrics, spec, False, (a, b))
    if isinstance(done, str):
        _reject(o, raw_tag, text, done)
        return None
    n = lyrics.count("\n", 0, a) + 1
    target = "end" if after.lower() == "end" else (anchor["id"] if anchor else after)
    return _edit("insert_lyrics", lyrics, a, b, lyrics[a:b], fitted, _child(body, "reason") or attrs.get("reason", ""), target, _section_of(lines, n), song_id, sheet_id, snap)


def _to_number(lines: list, raw: str) -> int:
    t = (raw or "").strip()
    if t.lower() in ("", "end", "0"):
        return 0
    ln = _line_by_target(lines, t)
    return ln["n"] if ln else -1


def _operation(o, kind: str, lyrics: str, lines: list, attrs: dict, body: str, raw_tag: str, instruction: str, song_id: str, sheet_id: str, snap: str, spec: str):
    from . import specialist_engine as SE
    # build_operation counts every split line, including a phantom trailing line, the same way the old OP parser did.
    all_rows = [ln["text"] for ln in lines]
    target = attrs.get("target") or attrs.get("at") or ""
    ln = _line_by_target(lines, target)
    if ln is None:
        _reject(o, raw_tag, "", f"{kind} needs a target line that exists, so nothing was offered.")
        return None
    name = attrs.get("name") or (_child(body, "name") or "")
    cues = attrs.get("cues") if attrs.get("cues") is not None else (_child(body, "cues") or "")
    to_n = _to_number(lines, attrs.get("to") or (_child(body, "to") or ""))
    if kind in ("move_section", "repeat_section") and to_n < 0:
        _reject(o, raw_tag, "", f"The destination {attrs.get('to', '')} is not a line in these lyrics, so nothing was offered.")
        return None
    if kind == "change_cue":
        return _cue_change(o, lyrics, ln, body, raw_tag, song_id, sheet_id, snap, spec)
    free: list = []
    op = "set_cue" if kind == "change_cue" else kind
    ev, sug, why, _ref = SE.build_operation(op, ln["n"], name, cues or "", all_rows, to_n, free)
    for hint in free:
        if hint not in o.hints:
            o.hints.append(hint)
    if why or not ev:
        _reject(o, "", raw_tag, why or f"{kind} could not be prepared.")
        return None
    if lyrics[ln["start"]:ln["start"] + len(ev)] != ev:
        at = lyrics.find(ev)
        if at < 0 or lyrics.find(ev, at + 1) >= 0:
            _reject(o, raw_tag, ev, "The operation's original text is not a single exact span, so nothing was offered.")
            return None
        start, end = at, at + len(ev)
    else:
        start, end = ln["start"], ln["start"] + len(ev)
    allow = kind in ("add_section", "repeat_section") and SE.wants_another_section(instruction)
    from .lyric_review import Item
    item = Item("probe", "edit", ln["n"], ln["n"], ev, sug, "")
    done = SE.apply_change(item, lyrics, spec, allow, (start, end))
    if isinstance(done, str):
        _reject(o, raw_tag, sug, done)
        return None
    return _edit(kind, lyrics, start, end, ev, sug, _child(body, "reason") or attrs.get("reason", ""), ln["id"], _section_of(lines, ln["n"]), song_id, sheet_id, snap, allow)


def _cue_change(o, lyrics: str, ln: dict, body: str, raw_tag: str, song_id: str, sheet_id: str, snap: str, spec: str):
    """A cue change names the line, quotes the WHOLE old tag line in <from> and gives the WHOLE new tag line in <to>. The program checks that <from> is exactly the line now and that <to> is one section tag of the
    same section; it never composes, merges or keeps parts of the cue text itself."""
    from . import specialist_engine as SE
    from . import tag_parser as TP
    from .compiler import TAG_LINE
    frm, to = _child(body, "from"), _child(body, "to")
    if frm is None or to is None:
        _reject(o, raw_tag, "", "A cue change must quote the whole old tag line in <from> and give the whole new tag line in <to>, so nothing was offered.")
        return None
    cur = ln["text"].strip()
    if frm.strip() != cur:
        _reject(o, raw_tag, "", "The old tag line in <from> is not exactly the line now, so nothing was offered. The line now is: " + cur)
        return None
    new = to.strip()
    if "\n" in new or not TAG_LINE.match(new):
        _reject(o, raw_tag, "", "<to> must be one whole section tag line such as [Verse 1 - soft vocals], so nothing was offered.")
        return None
    from . import cue_library as CL
    fmt = CL.format_tag(new)                      # format only: every cue the model wrote stays, word for word
    new = fmt.after
    reason = _child(body, "reason") or ""
    if fmt.notes:
        reason = (reason + " Note: " + "; ".join(fmt.notes) + ". Nothing was removed.").strip()
    old_p, new_p = TP.parse_tag(cur[1:-1]), TP.parse_tag(new[1:-1])
    if old_p.section:
        if new_p.name != old_p.name:
            _reject(o, raw_tag, "", "The section name in <to> must stay the same as in <from>, so nothing was offered.")
            return None
    elif new_p.name != old_p.name and not new_p.section:
        _reject(o, raw_tag, "", "The section name in <to> must stay the same as in <from>, so nothing was offered.")
        return None
    if new == cur:
        _reject(o, raw_tag, "", "<to> is the same as <from>: this tag does not change.")
        return None
    start = ln["start"] + (len(ln["text"]) - len(ln["text"].lstrip()))
    end = start + len(cur)
    if lyrics[start:end] != cur:
        _reject(o, raw_tag, "", "The line is not where it was read, so nothing was offered.")
        return None
    from .lyric_review import Item
    done = SE.apply_change(Item("probe", "edit", ln["n"], ln["n"], cur, new, ""), lyrics, spec, False, (start, end))
    if isinstance(done, str):
        _reject(o, raw_tag, new, done)
        return None
    return _edit("change_cue", lyrics, start, end, cur, new, reason, ln["id"], _section_of(_lines_of(lyrics), ln["n"]), song_id, sheet_id, snap, False)


def _lines_of(lyrics: str) -> list:
    return lyric_index(lyrics)


SETTING_FIELDS = (("bpm", "bpm"), ("keyscale", "key"), ("timesignature", "time_signature"))


def _settings(o, body: str, raw_tag: str) -> None:
    """Music Settings suggested next to a caption. Each value is checked by the same rules as the Style / Output controls (template_arrange.check_field); a value that fails is reported, never
    repaired or clamped, and the valid ones are still offered. The groove is advice: no Song Setting exists for it."""
    from . import template_arrange as TA
    if o.settings or o.settings_info:
        _reject(o, raw_tag, "", "More than one settings block was sent; the first one is used.")
        return
    got, bad = {}, []
    for field_, tag in SETTING_FIELDS:
        names = {tag, field_, tag.replace("_", "")} | ({"key_scale"} if field_ == "keyscale" else set())
        raw = next((v for v in (_child(body, n) for n in sorted(names)) if v is not None), None)
        if raw is None or not raw.strip() or raw.strip().casefold() in ("-", "none", "auto"):
            continue
        v, why = TA.check_field(field_, raw)
        if why:
            bad.append([field_, raw.strip(), why])
        else:
            got[field_] = v
    groove = re.sub(r"\s+", " ", _child(body, "groove") or "").strip()[:160]
    reason = re.sub(r"\s+", " ", _child(body, "reason") or "").strip()[:400]
    for f_, written, why in bad:
        o.notes.append(f"The suggested {f_} “{written[:40]}” was not used: {why}.")
    if not got and not groove:
        _reject(o, raw_tag, "", "The settings block had no usable value, so nothing was offered.")
        return
    o.settings = got
    o.settings_info = {"groove": groove, "reason": reason, "bad": bad}
    o.steps.append("settings: " + ", ".join(f"{k}={v}" for k, v in got.items()) + (f"; groove {groove!r}" if groove else "") + (f"; refused {len(bad)}" if bad else ""))


def _caption(o, lyrics: str, body: str, attrs: dict, raw_tag: str) -> None:
    from . import basic_caption as BC
    text = _child(body, "text")
    if text is None:
        text = attrs.get("text") or ""
    text = (text or "").strip()
    if not text:
        _reject(o, raw_tag, "", "A change_caption tag has no text, so nothing was offered.")
        return
    try:
        cap, _warns = BC.check_caption(text, lyrics)
    except BC.CaptionError as ex:
        _reject(o, raw_tag, text, f"The caption cannot be used for the style: {ex.args[0]}")
        o.steps.append(f"caption: rejected ({ex.args[0]})")
        return
    o.caption = cap
    o.steps.append(f"caption: {len(cap.split())} words, valid")


def answer(spec: str, raw: str, text: str, selected: str = "", has_sel: bool = False, instruction: str = "", after_offer: bool = False, sel_bounds=None, pending_lyrics: bool = False, corrections=None, song_id: str = "", sheet_id: str = "") :
    """Split one chat answer into the words the model wrote and the actions that can be applied.

    after_offer and pending_lyrics are accepted so older callers keep working. They do not choose a mode and they do not drop a tag.
    A well-formed tag is checked even when the words around it are only a question. Prose without a tag never becomes a card.
    """
    from . import clear_tags as CG
    from . import specialist_engine as SE
    tag_notes: list = []
    if CG.is_chat(raw or ""):
        raw, tag_notes = CG.chat_to_xml(raw or "")
    o = SE.ChatOutcome()
    lyrics = text or ""
    lines = lyric_index(lyrics)
    snap = SE.text_hash_of(lyrics)
    tags = _iter_tags(raw or "")
    parts, cursor = [], 0
    actions = []
    for start, end, kind, attr_blob, body, literal in tags:
        parts.append((raw or "")[cursor:start])
        cursor = end
        actions.append((kind, _attrs(attr_blob), body, literal))
    parts.append((raw or "")[cursor:])
    reply = "".join(parts)
    unclosed = sorted({m.group(1).lower() for m in _OPEN.finditer(reply)})
    if unclosed:
        o.notes.append("An action tag was not read because it was not closed (" + ", ".join(unclosed) + "). The text stays in the answer and nothing was applied from it.")
        o.steps.append("tags: unclosed " + ", ".join(unclosed))
    reply = re.sub(r"</?(?:b|i|u|span|font|amber|green|red|blue|purple|pink|gray)\b[^>]*>", "", SE.clean_braces(reply))
    reply = re.sub(r"\n{3,}", "\n\n", reply).strip()
    edits = []
    for kind, attrs, body, literal in actions:
        who = _own(spec, kind)
        if who:
            _reject(o, literal, body or literal, f"This is the {who}'s work, so it was not offered here.")
            o.steps.append(f"tags: {kind} left to the {who}")
            continue
        if kind == "change_caption":
            _caption(o, lyrics, body, attrs, literal)
            continue
        if kind == "change_settings":
            _settings(o, body, literal)
            continue
        made = None
        if kind == "change_lyric":
            made = _lyric_change(o, spec, lyrics, lines, attrs, body, literal, has_sel, sel_bounds, song_id, sheet_id, snap)
        elif kind == "insert_lyrics":
            made = _insert(o, lyrics, lines, attrs, body, literal, has_sel, sel_bounds, song_id, sheet_id, snap, spec)
        elif kind in STRUCTURE_TAGS or kind == "change_cue":
            made = _operation(o, kind, lyrics, lines, attrs, body, literal, instruction, song_id, sheet_id, snap, spec)
        if made:
            edits.append(made)
    for i, e in enumerate(edits, 1):
        e["id"] = f"e{i}"
    _mark_alts(edits)
    if not reply and (edits or o.caption or o.rejected):
        o.notes.append("The reply was empty. What the AI wrote for a change is under “Show what the AI wrote”, or on its card when it could be checked.")
        o.steps.append("reply empty; checked actions were kept")
    if tag_notes:
        o.notes = [n for n in tag_notes if n not in o.notes] + o.notes
    o.reply, o.proposal, o.edits = reply, "", edits
    o.steps.append(f"parsed: reply {len(reply)} chars, actions {len(edits)}, caption {bool(o.caption)}")
    _ = (selected, corrections, after_offer, pending_lyrics)
    return o
