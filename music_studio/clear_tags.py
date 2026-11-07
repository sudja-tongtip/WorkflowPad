"""One grammar for application-facing LLM answers: numbered clear tags.

The parser only reads syntax. It does not judge rhymes, invent fields, or change lyrics.
Each task adapter turns a valid document into the data shape that task already used, so the
old checks still decide what is safe to show or apply. An answer in the previous JSON, XML,
or marker format is left untouched for the old parser.

A control tag is a whole line `[TYPE.n]` or `[DONE]`. `[Verse]`, `[Chorus]` and `[Hold]`
are lyric text, not control tags. A line that should stay literal even though it looks like
a control tag is written with one leading backslash: `\\[EDIT.1]`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

PARTIAL = "Partial result — some items were incomplete. The details are in the diagnostic log."
PARTIAL_SHORT = "Partial analysis — some items were incomplete."
UNREADABLE = "The analysis could not be read — please analyze again."
UNREADABLE_SECTION = "This section could not be read — please check it again."

REGISTRY = (
    "REPLY", "EDIT", "INSERT", "FINDING", "REVIEW", "RHYME", "STRENGTH", "ITEM",
    "SECTION", "END", "ENDSECTION", "LINK", "REPEAT", "STYLE", "SETTING",
    "CAPTION", "CUE", "ARRANGE", "NOTE", "ASSIGN", "HINT", "COUNT",
    "RHYTHM", "WORD", "ENDING", "META", "FORMAT", "DONE",
)
_TAG = re.compile(r"^\[([A-Z][A-Z0-9]*)(?:\.(\d+))?\]\s*$")
_FENCE = re.compile(r"^([A-Za-z_]+):\s*<<<\s*$")
_KV = re.compile(r"^([A-Za-z_]+):\s*(.*)$")
_LINE_ID = re.compile(r"^[Ll]?\s*(\d{1,5})\s*(?:\|\s*(.*))?$")
_EP = re.compile(r"^[Ll]?\s*(\d{1,5})\s*\|\s*(.+?)(?:\|\s*([A-Za-z_]+))?(?:\|\s*(\d{1,3}))?\s*$")
_TURN = re.compile(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$")
RHYME_SCHEMA = "rhyme-map-v6"
_RELATIONS = ("exact", "near", "uncertain")
_SOUNDS = ("vowel_rhyme", "consonant_rhyme", "assonance", "consonance", "alliteration", "multisyllabic", "wordplay")
_CONF = ("high", "medium", "low", "uncertain")


@dataclass
class Block:
    type: str
    number: "int | None"
    payload: str
    line: int
    issues: list = field(default_factory=list)
    usable: bool = True


@dataclass
class Document:
    blocks: list
    preamble: str = ""
    done: bool = False
    trailing: str = ""
    issues: list = field(default_factory=list)

    def of(self, *types: str) -> list:
        return [b for b in self.blocks if b.type in types and b.usable]


_ESC_INLINE = re.compile(r"\\(\[(?:[A-Z][A-Z0-9]*)(?:\.\d+)?\])")


def _unescape(payload: str) -> str:
    """A backslash before a control-shaped tag is escape syntax, including inside a lyric line."""
    out = []
    for line in (payload or "").split("\n"):
        if line.startswith("\\[") and line.endswith("]") and _TAG.match(line[1:]):
            line = line[1:]
        else:
            line = _ESC_INLINE.sub(r"\1", line)
        out.append(line)
    return "\n".join(out).strip("\n")


def parse(raw: str) -> Document:
    """Syntax only. A bad block is kept, marked not usable, and does not erase the others."""
    text = _TURN.sub("", raw or "").strip()
    lines = text.splitlines()
    if lines and re.fullmatch(r"\s*```[A-Za-z]*\s*", lines[0]) and re.fullmatch(r"\s*```\s*", lines[-1]):
        lines = lines[1:-1]
    doc = Document(blocks=[])
    pre, buf, cur = [], [], None
    seen: dict = {}
    started = False
    done_at = None

    def close():
        nonlocal cur, buf
        if cur is None:
            return
        cur.payload = _unescape("\n".join(buf))
        doc.blocks.append(cur)
        cur, buf = None, []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("\\[") and _TAG.match(stripped[1:]):
            (buf if started else pre).append(line)
            continue
        m = _TAG.match(stripped)
        if m and (m.group(1) == "DONE" or m.group(2)):
            if done_at is not None:
                doc.issues.append(f"Text after [DONE] starts at line {i} and is not used.")
                doc.trailing = "\n".join(lines[i - 1:]).strip()
                break
            close()
            started = True
            kind, num_s = m.group(1), m.group(2)
            if kind == "DONE":
                if m.group(2):
                    doc.issues.append("[DONE] does not take a number; the extra number was ignored.")
                doc.done = True
                done_at = i
                continue
            issues, usable = [], True
            if kind not in REGISTRY:
                issues.append(f"[{kind}.{num_s}] is not a tag this program reads.")
                usable = False
            number = int(num_s)
            if number < 1:
                issues.append(f"[{kind}.{num_s}] needs a number starting at 1.")
                usable = False
            elif (kind, number) in seen:
                issues.append(f"[{kind}.{number}] is repeated; the first one is kept.")
                usable = False
            else:
                seen[(kind, number)] = i
            cur = Block(kind, number, "", i, issues, usable)
            buf = []
            continue
        if started:
            buf.append(line)
        else:
            pre.append(line)
    close()
    doc.preamble = "\n".join(pre).strip()
    if doc.blocks and not doc.done:
        doc.issues.append("The answer has no [DONE] line. Complete tags above it were still read.")
    if doc.preamble and doc.blocks:
        doc.issues.append("Text before the first tag was kept only as chat wording, not as structured data.")
    return doc


def looks_like(raw: str) -> bool:
    for line in _TURN.sub("", raw or "").splitlines():
        s = line.strip()
        if s.startswith("\\["):
            continue
        m = _TAG.match(s)
        if m and (m.group(1) == "DONE" or m.group(2)):
            return True
    return False


def fields_checked(payload: str) -> tuple:
    """(fields, keys opened with <<< and never closed with >>>). A field that was never closed is not in the fields:
    its text is cut off at an unknown place, so it is not used and nothing is closed or guessed for it."""
    out, unclosed, i, rows = {}, [], 0, (payload or "").splitlines()
    while i < len(rows):
        fence = _FENCE.match(rows[i].strip())
        if fence:
            key = fence.group(1).lower()
            i += 1
            buf = []
            while i < len(rows) and rows[i].strip() != ">>>":
                buf.append(rows[i])
                i += 1
            if i < len(rows):
                i += 1
                if key not in out:
                    out[key] = "\n".join(buf).strip("\n")
            elif key not in unclosed:
                unclosed.append(key)
            continue
        kv = _KV.match(rows[i])
        if kv and kv.group(1).lower() not in out:
            out[kv.group(1).lower()] = kv.group(2).strip()
        i += 1
    return out, unclosed


_BLOCK_KEYS = frozenset({"op", "target", "after", "from", "to", "text", "reason", "cues", "name", "type"})
_REPAIRABLE = ("from", "to", "text")


def fields_repair(payload: str) -> tuple:
    """(fields, keys still unclosed, keys repaired). Same as fields_checked, plus ONE narrow repair: a fenced field of a block whose `>>>` is missing and whose text runs to the very end of the block of an answer that
    is complete (the caller has seen [DONE]). Nothing is guessed: the text is taken as written up to the end of the block, and only when
      - it is a from / to / text field and is the only field left open,
      - it is not empty and no later line looks like another field of the block (op:, target:, to: ... or another <<<),
      - it contains no >>> of its own.
    Anything else stays unclosed, so the caller rejects it with the same reason as before."""
    out, unclosed = fields_checked(payload)
    if len(unclosed) != 1 or unclosed[0] not in _REPAIRABLE:
        return out, unclosed, []
    key = unclosed[0]
    rows = (payload or "").splitlines()
    start = next((k for k, r in enumerate(rows) if (m := _FENCE.match(r.strip())) and m.group(1).lower() == key), None)
    if start is None:
        return out, unclosed, []
    tail = rows[start + 1:]
    body = "\n".join(tail).strip("\n")
    for r in tail:
        s = r.strip()
        if s == ">>>" or "<<<" in s:
            return out, unclosed, []
        kv = _KV.match(s)
        if kv and kv.group(1).lower() in _BLOCK_KEYS:
            return out, unclosed, []
    if not body.strip():
        return out, unclosed, []
    fixed = dict(out)
    fixed[key] = body
    return fixed, [], [key]


def fields(payload: str) -> dict:
    """key: value, and key: <<< ... >>> for several lines. A key is read once. Nothing is invented."""
    return fields_checked(payload)[0]


def _xml(tag: str, attrs: dict, children: dict, empty: bool = False) -> "str | None":
    for val in list(attrs.values()) + list(children.values()):
        if val and re.search(r"</\s*" + tag + r"\b", val, re.I):
            return None
    for val in children.values():
        if val and re.search(r"</\s*[A-Za-z_]+", val):
            return None
    head = tag
    for k, v in attrs.items():
        if v is None:
            continue
        if re.search(r"[&<>\"]", v):
            return None
        head += f' {k}="{v}"'
    if empty and not any(children.values()):
        return f"<{head}/>"
    body = ""
    for name, val in children.items():
        if val is None:
            continue
        body += f"<{name}>{val}</{name}>"
    return f"<{head}>{body}</{tag}>"


def _ep(token: str) -> "dict | None":
    m = _EP.match((token or "").strip())
    if not m:
        return None
    role = (m.group(3) or "").strip().lower()
    occ = m.group(4)
    out = {"file_line": int(m.group(1)), "text": m.group(2).strip()}
    if role:
        out["role"] = role
    if occ and occ.isdigit() and int(occ) > 0:
        out["occurrence"] = int(occ)
    return out


def _end_lines(payload: str) -> tuple:
    sound, ids, words, bad = "", [], [], []
    data = fields(payload)
    if data.get("sound"):
        sound = data["sound"]
    for line in (payload or "").splitlines():
        s = line.strip()
        if not s or s.lower().startswith("sound:") or _KV.match(s) and s.lower().startswith(("id:", "section:")):
            continue
        m = _LINE_ID.match(s)
        if not m:
            bad.append(s[:40])
            continue
        n = int(m.group(1))
        ids.append(n)
        if m.group(2) and m.group(2).strip():
            words.append((n, m.group(2).strip()))
    return sound, ids, words, bad


def is_rhyme(raw: str) -> bool:
    if not looks_like(raw):
        return False
    doc = parse(raw)
    kinds = {b.type for b in doc.blocks}
    return bool(kinds & {"END", "LINK", "REPEAT", "ENDSECTION"}) and not (kinds & {"FINDING", "REVIEW", "RHYME", "EDIT", "REPLY"})


def rhyme_as_json(raw: str) -> tuple:
    """(JSON text the old rhyme reader accepts, notes). Groups and pairs are copied, not re-judged."""
    doc = parse(raw)
    notes = list(doc.issues)
    sections, cur = [], None
    pairs, repeats, languages, endings = [], [], [], {}

    def drop(why: str):
        """A section that was not opened and closed with the same number is a structural error: it is not used, and nothing is made up for it."""
        nonlocal cur
        if cur is not None:
            notes.append(why)
            cur = None

    def open_section(payload: str, number: "int | None" = None):
        nonlocal cur
        if cur is not None and cur.get("number") is not None:
            drop(f"[SECTION.{cur['number']}] was not closed before the next section began, so it was left out.")
        elif cur is not None:
            sections.append(cur)
            cur = None
        f = fields(payload)
        cur = {"section_id": (f.get("id") or f.get("section") or f"s{number or len(sections) + 1}")[:12], "number": number,
               "groups": [], "sounds": [], "observations": [], "endings": {}}

    for b in doc.blocks:
        if not b.usable:
            notes.extend(b.issues)
            continue
        if b.type == "META":
            f = fields(b.payload)
            raw_langs = f.get("languages") or ""
            for bit in re.split(r"[, ]+", raw_langs):
                bit = bit.strip().lower()
                if bit and bit not in languages:
                    languages.append(bit[:8])
        elif b.type == "SECTION":
            open_section(b.payload, b.number)
        elif b.type == "END":
            if cur is None:
                open_section("")
            sound, ids, words, bad = _end_lines(b.payload)
            for item in bad:
                notes.append(f"[END.{b.number}] left out {item!r}: it is not a line id.")
            if not ids:
                notes.append(f"[END.{b.number}] has no line ids, so that group was left out.")
                continue
            cur["groups"].append(ids)
            cur["sounds"].append(sound)
            for n, w in words:
                cur["endings"].setdefault(str(n), w[:40])                          # kept with its section: a section that is left out takes its words with it
        elif b.type == "ENDSECTION":
            if cur is None:
                notes.append(f"[ENDSECTION.{b.number}] closes no open section, so it was ignored.")
            elif cur.get("number") is None or cur["number"] == b.number:
                sections.append(cur)
                cur = None
            else:
                drop(f"[SECTION.{cur['number']}] was closed by [ENDSECTION.{b.number}], a different number, so that section was left out.")
        elif b.type == "LINK":
            pair, why = _link(b)
            if pair is None:
                notes.append(why)
            else:
                pairs.append(pair)
        elif b.type == "REPEAT":
            rep, why = _repeat(b)
            if rep is None:
                notes.append(why)
            else:
                repeats.append(rep)
        elif b.type == "NOTE":
            f = fields(b.payload)
            line_n = f.get("line") or ""
            text = f.get("text") or ""
            if not line_n.isdigit() or not text:
                notes.append(f"[NOTE.{b.number}] needs line and text, so it was left out.")
                continue
            obs = {"line": int(line_n), "kind": "uncertain", "text": text[:200]}
            if cur is None:
                notes.append(f"[NOTE.{b.number}] is outside a section, so it was left out.")
            else:
                cur["observations"].append(obs)
        else:
            notes.append(f"[{b.type}.{b.number}] is not part of a rhyme answer, so it was left out.")
    if cur is not None:
        if cur.get("number") is None:
            sections.append(cur)                                                   # lines with no [SECTION] tag at all: read as one section, as before
        else:
            drop(f"[SECTION.{cur['number']}] has no [ENDSECTION.{cur['number']}] before the end of the answer, so it was left out.")
    out_secs = []
    for s in sections:
        for k, v in s["endings"].items():
            endings.setdefault(k, v)
    for s in sections:
        groups, sounds = s["groups"], s["sounds"]
        if groups and all(sounds) and len(set(sounds)) == len(sounds):
            eg = {sounds[i]: [f"L{n}" for n in groups[i]] for i in range(len(groups))}
        else:
            eg = [[f"L{n}" for n in g] for g in groups]
            if any(not x for x in sounds):
                notes.append(f"Section {s['section_id']} has an end group with no sound key. The lines are kept; the sound is not filled in.")
        out_secs.append({"section_id": s["section_id"], "end_groups": eg, "observations": s["observations"]})
    data = {"schema": RHYME_SCHEMA, "languages": languages, "endings": endings, "sections": out_secs, "pairs": pairs, "repeats": repeats}
    return json.dumps(data, ensure_ascii=False), notes


def _link(b: Block) -> tuple:
    f = fields(b.payload)
    rows = [ln.strip() for ln in b.payload.splitlines() if ln.strip() and not _KV.match(ln)]
    rel = (f.get("relation") or "").lower()
    place = (f.get("placement") or "").lower()
    sound = (f.get("sound_type") or "").lower()
    conf = (f.get("confidence") or "").lower()
    reason = f.get("reason") or ""
    a = _ep(f["a"]) if f.get("a") else None
    bep = _ep(f["b"]) if f.get("b") else None
    if a is None or bep is None:
        head = rows[0] if rows else ""
        if head and "|" in head and not _LINE_ID.match(head):
            bits = [x.strip() for x in head.split("|")]
            rel = rel or bits[0].lower()
            place = place or (bits[1].lower() if len(bits) > 1 else "")
            if len(bits) > 2 and not sound:
                sound = bits[2].lower()
            rows = rows[1:]
        eps = [_ep(x) for x in rows]
        eps = [x for x in eps if x]
        if a is None and eps:
            a = eps[0]
        if bep is None and len(eps) > 1:
            bep = eps[1]
    if a is None or bep is None:
        return None, f"[LINK.{b.number}] needs two words with their line ids, so it was left out."
    if rel not in _RELATIONS:
        return None, f"[LINK.{b.number}] has no relation (exact, near, or uncertain), so it was left out."
    if not sound:
        return None, f"[LINK.{b.number}] has no sound_type, so it was left out. The program does not choose the sound type."
    if sound not in _SOUNDS:
        return None, f"[LINK.{b.number}] has an unknown sound type, so it was left out."
    if conf and conf not in _CONF:
        return None, f"[LINK.{b.number}] has an unknown confidence, so it was left out."
    pair = {"sound_type": sound, "relation": rel, "a": a, "b": bep, "reason": reason[:160], "confidence": conf}
    if place:
        pair["placement"] = place
    return pair, ""


def _repeat(b: Block) -> tuple:
    f = fields(b.payload)
    a = _ep(f["a"]) if f.get("a") else None
    bep = _ep(f["b"]) if f.get("b") else None
    if a is None or bep is None:
        eps = [_ep(ln.strip()) for ln in b.payload.splitlines() if ln.strip()]
        eps = [x for x in eps if x]
        if len(eps) >= 2:
            a, bep = eps[0], eps[1]
    if a is None or bep is None:
        return None, f"[REPEAT.{b.number}] needs two words, so it was left out."
    return {"a": a, "b": bep}, ""


def report_incomplete(raw: str) -> bool:
    """True for a numbered-tag report whose answer never reached [DONE] (cut off, or the AI stopped): the report is not complete and is not used. A report in the older marker format is not judged here."""
    if not is_report(raw):
        return False
    doc = parse(raw)
    return bool(doc.blocks) and not doc.done


def is_report(raw: str) -> bool:
    if not looks_like(raw):
        return False
    kinds = {b.type for b in parse(raw).blocks}
    return bool(kinds & {"REVIEW", "FINDING", "RHYME", "STRENGTH", "ARRANGE", "ITEM"}) or ("CUE" in kinds and "EDIT" not in kinds and "REPLY" not in kinds)


def report_to_legacy(raw: str) -> tuple:
    """The specialist-report markers the existing report reader already checks."""
    doc = parse(raw)
    notes = list(doc.issues)
    lines = ["=========== SPECIALIST REPORT ==========="]
    saw_strength = False
    saw_cue = False
    saw_arr = False
    saw_note = False
    for b in doc.blocks:
        if not b.usable:
            notes.extend(b.issues)
            continue
        if b.type == "REVIEW":
            body = b.payload.strip()
            if body.upper().startswith(("SUMMARY:", "VERDICT:")) or "STRENGTHS:" in body.upper():
                lines.append(body)
            else:
                lines.append("SUMMARY: " + body)
        elif b.type == "STRENGTH":
            if not saw_strength:
                lines.append("STRENGTHS:")
                saw_strength = True
            text = b.payload.strip()
            lines.append(text if text[:1] in "-•*" else "- " + text)
        elif b.type == "RHYME":
            lines.append("RHYME_SECTION")
            if b.payload.strip():
                lines.append(b.payload.strip())
        elif b.type in ("FINDING", "ITEM"):
            lines.append("FINDING")
            if b.payload.strip():
                lines.append(b.payload.strip())
        elif b.type == "CUE":
            if not saw_cue:
                lines.append("SECTION CUES:")
                saw_cue = True
            if b.payload.strip():
                lines.append(b.payload.strip())
        elif b.type == "ARRANGE":
            if not saw_arr:
                lines.append("ARRANGEMENT:")
                saw_arr = True
            if b.payload.strip():
                lines.append(b.payload.strip())
        elif b.type == "NOTE":
            if not saw_note:
                lines.append("UNCERTAIN:")
                saw_note = True
            text = b.payload.strip()
            lines.append(text if text[:1] in "-•*" else "- " + text)
        elif b.type != "DONE":
            notes.append(f"[{b.type}.{b.number}] is not part of this report, so it was left out.")
    lines.append("=========== END ===========")
    return "\n".join(lines) + "\n", notes


def is_chat(raw: str) -> bool:
    if not looks_like(raw):
        return False
    doc = parse(raw)
    kinds = {b.type for b in doc.blocks}
    if kinds & {"FINDING", "REVIEW", "RHYME", "END", "LINK", "ENDSECTION", "COUNT", "RHYTHM", "WORD", "ENDING", "FORMAT"}:
        return False
    edits = [b for b in doc.blocks if b.type == "EDIT" and b.usable]
    if edits and not (kinds & {"INSERT", "CAPTION", "CUE", "SECTION"}):
        structured = False
        for b in edits:
            f = fields(b.payload)
            if any(k in f for k in ("target", "from", "to", "op", "text")):
                structured = True
        if not structured:
            return False
    if kinds & {"REPLY", "EDIT", "INSERT", "CAPTION"}:
        return "END" not in kinds and "LINK" not in kinds and "REVIEW" not in kinds and "FINDING" not in kinds
    if "CUE" in kinds or "SECTION" in kinds:
        for b in doc.blocks:
            if b.type == "SECTION" and (fields(b.payload).get("op") or fields(b.payload).get("name") or fields(b.payload).get("target")):
                return True
            if b.type == "CUE":
                return True
    return False


def chat_to_xml(raw: str) -> tuple:
    """(prose plus the XML action tags the chat checker already knows, notes)."""
    doc = parse(raw)
    notes = [x for x in doc.issues if not x.startswith("Text before")]
    parts = []
    held = False
    if doc.preamble.strip():
        parts.append(doc.preamble.strip())
    for b in doc.blocks:
        if not b.usable:
            notes.extend(b.issues)
            continue
        if b.type == "REPLY":
            if b.payload.strip():
                parts.append(b.payload.strip())
            continue
        if not doc.done:
            held = True                                    # no [DONE]: the answer may be cut off, so no action from it is offered
            continue
        xml, why = _chat_xml(b)
        if why:
            notes.append(why)
        if xml:
            parts.append(xml)
    if held:
        notes.append("The answer has no [DONE] line, so no action from it was offered. The reply text is shown.")
    return "\n".join(parts).strip(), notes


def _chat_xml(b: Block) -> tuple:
    f, unclosed, repaired = fields_repair(b.payload)           # chat_to_xml only gets here for a complete answer ([DONE] seen)
    label = f"[{b.type}.{b.number}]"
    if unclosed:
        return None, f"{label} field {', '.join(unclosed)} was opened with <<< and never closed with >>>, so nothing was offered."
    if repaired:
        xml, why = _chat_xml_fields(b, f, label)
        note = f"{label} field {repaired[0]} had no closing >>>: its text up to the end of the block was used as written. Check it in the preview."
        return xml, (why + " " + note).strip() if why else note
    return _chat_xml_fields(b, f, label)


def _chat_xml_fields(b: Block, f: dict, label: str) -> tuple:
    if b.type == "EDIT":
        op = (f.get("op") or "").strip().lower().replace("-", "_").replace(" ", "_")
        if op in ("", "change", "change_lyric"):
            if "from" not in f or "to" not in f:
                return None, f"{label} needs from and to, so nothing was offered."
            xml = _xml("change_lyric", {"target": f.get("target", "")}, {"from": f.get("from", ""), "to": f.get("to", ""), "reason": f.get("reason", "")})
            return xml, "" if xml else f"{label} quotes a closing tag, so nothing was offered."
        if op in ("insert", "insert_lyrics"):
            if "text" not in f:
                return None, f"{label} needs text, so nothing was offered."
            xml = _xml("insert_lyrics", {"after": f.get("after") or "end"}, {"text": f["text"], "reason": f.get("reason", "")})
            return xml, "" if xml else f"{label} quotes a closing tag, so nothing was offered."
        return None, f"{label} op {op or '?'} is not a lyric action, so nothing was offered."
    if b.type == "INSERT":
        if "text" not in f:
            return None, f"{label} needs text, so nothing was offered."
        xml = _xml("insert_lyrics", {"after": f.get("after") or "end"}, {"text": f["text"], "reason": f.get("reason", "")})
        return xml, "" if xml else f"{label} quotes a closing tag, so nothing was offered."
    if b.type == "CUE":
        op = (f.get("op") or "").strip().lower().replace("-", "_").replace(" ", "_")
        if op in ("add_line_cue",):
            if not f.get("target") or not f.get("cues"):
                return None, f"{label} needs target and cues, so nothing was offered."
            xml = _xml("add_line_cue", {"target": f["target"], "cues": f["cues"]}, {}, empty=True)
            return xml, "" if xml else f"{label} could not be read, so nothing was offered."
        if "from" not in f or "to" not in f:
            return None, f"{label} needs from and to, so nothing was offered."
        xml = _xml("change_cue", {"target": f.get("target", "")}, {"from": f["from"], "to": f["to"], "reason": f.get("reason", "")})
        return xml, "" if xml else f"{label} quotes a closing tag, so nothing was offered."
    if b.type == "SECTION":
        op = (f.get("op") or "add_section").strip().lower().replace("-", "_").replace(" ", "_")
        if op not in ("add_section", "add_hold", "move_section", "repeat_section"):
            return None, f"{label} op {op} is not a structure action, so nothing was offered."
        if not f.get("target"):
            return None, f"{label} needs a target, so nothing was offered."
        attrs = {"target": f["target"]}
        children = {}
        if op == "add_section":
            children = {"name": f.get("name", ""), "cues": f.get("cues", ""), "reason": f.get("reason", "")}
        elif op in ("move_section", "repeat_section"):
            if not f.get("to"):
                return None, f"{label} needs to, so nothing was offered."
            attrs["to"] = f["to"]
        xml = _xml(op, attrs, children, empty=op == "add_hold")
        return xml, "" if xml else f"{label} could not be read, so nothing was offered."
    if b.type == "CAPTION":
        text = f.get("text") if f.get("text") else b.payload.strip()
        if not text:
            return None, f"{label} is empty, so nothing was offered."
        xml = _xml("change_caption", {}, {"text": text})
        return xml, "" if xml else f"{label} could not be read, so nothing was offered."
    if b.type == "SETTING":
        kids = {}
        for want, names in (("bpm", ("bpm",)), ("key", ("key", "keyscale", "key_scale")), ("time_signature", ("time_signature", "timesignature", "time")), ("groove", ("groove",)), ("reason", ("reason",))):
            kids[want] = next((f[n] for n in names if f.get(n) is not None), None)
        if not any(kids.values()):
            return None, f"{label} has no bpm, key, time_signature or groove, so nothing was offered."
        xml = _xml("change_settings", {}, kids)
        return xml, "" if xml else f"{label} could not be read, so nothing was offered."
    if b.type == "STYLE":
        return None, f"{label} is not applied from chat. Music settings stay a separate proposal."
    return None, f"{label} is not a chat action, so nothing was offered."


def _wrap(header: str, body: str, end: str = "=========== END ===========") -> str:
    return f"{header}\n{body.rstrip()}\n{end}\n"


def is_style(raw: str) -> bool:
    return looks_like(raw) and any(b.type == "STYLE" and b.usable for b in parse(raw).blocks)


def style_to_legacy(raw: str) -> tuple:
    doc = parse(raw)
    notes = list(doc.issues)
    chunks = []
    for b in doc.blocks:
        if b.type != "STYLE":
            if b.type not in ("DONE",) and b.usable:
                notes.append(f"[{b.type}.{b.number}] is not a direction, so it was left out.")
            elif not b.usable:
                notes.extend(b.issues)
            continue
        if b.number not in (1, 2, 3):
            notes.append(f"[STYLE.{b.number}] is not direction 1, 2, or 3, so it was left out.")
            continue
        chunks.append((b.number, f"=========== DIRECTION {b.number} ===========\n{b.payload.strip()}"))
    chunks.sort()
    body = "\n\n".join(text for _n, text in chunks)
    return body + "\n\n=========== END ===========\n", notes


def is_caption(raw: str) -> bool:
    if not looks_like(raw):
        return False
    kinds = {b.type for b in parse(raw).blocks}
    return "CAPTION" in kinds and not (kinds & {"ASSIGN", "HINT", "STYLE", "EDIT", "REPLY"})


def caption_to_legacy(raw: str) -> tuple:
    doc = parse(raw)
    notes = list(doc.issues)
    caps = [b for b in doc.blocks if b.type == "CAPTION" and b.usable]
    for b in doc.blocks:
        if not b.usable:
            notes.extend(b.issues)
    if not caps:
        return raw, notes
    if len(caps) > 1:
        notes.append("More than one [CAPTION] was sent; the first one is used.")
    return _wrap("=========== CAPTION ===========", caps[0].payload.strip()), notes


def is_arrange(raw: str) -> bool:
    if not looks_like(raw):
        return False
    kinds = {b.type for b in parse(raw).blocks}
    return bool(kinds & {"ASSIGN", "HINT"}) or ("SETTING" in kinds and "CAPTION" in kinds and "STYLE" not in kinds)


def arrange_to_legacy(raw: str) -> tuple:
    doc = parse(raw)
    notes = list(doc.issues)
    buckets = {"ASSIGN": [], "HINT": [], "CAPTION": [], "SETTING": []}
    for b in doc.blocks:
        if not b.usable:
            notes.extend(b.issues)
            continue
        if b.type in buckets:
            buckets[b.type].append(b.payload.strip())
        elif b.type != "DONE":
            notes.append(f"[{b.type}.{b.number}] is not part of an arrangement, so it was left out.")
    order = (("ASSIGN", "=========== ASSIGNMENTS ==========="),
             ("HINT", "=========== SECTION STYLE ==========="),
             ("CAPTION", "=========== MUSIC STYLE ==========="),
             ("SETTING", "=========== MUSIC META ==========="))
    parts = []
    for key, header in order:
        if buckets[key]:
            parts.append(header + "\n" + "\n".join(x for x in buckets[key] if x))
    if not parts:
        return raw, notes
    return "\n\n".join(parts) + "\n\n=========== END ===========\n", notes


def is_action(raw: str) -> bool:
    """A lyric-action answer: [REPLY] and optional [EDIT] blocks whose text is the proposal, not a from/to card."""
    if not looks_like(raw):
        return False
    doc = parse(raw)
    kinds = {b.type for b in doc.blocks if b.usable}
    if "REPLY" not in kinds:
        return False
    if kinds & {"INSERT", "CAPTION", "CUE", "SECTION", "FINDING", "REVIEW", "RHYME", "END", "LINK", "SETTING", "STYLE", "ASSIGN", "HINT", "COUNT", "RHYTHM", "WORD", "ENDING", "ENDSECTION", "FORMAT"}:
        return False
    for b in doc.blocks:
        if b.type == "EDIT" and b.usable:
            f = fields(b.payload)
            if any(k in f for k in ("target", "from", "to", "op", "text")):
                return False
    return True


def action_to_legacy(raw: str) -> tuple:
    doc = parse(raw)
    notes = list(doc.issues)
    reply, proposal = [], []
    held = False
    for b in doc.blocks:
        if not b.usable:
            notes.extend(b.issues)
            continue
        if b.type == "REPLY":
            reply.append(b.payload.strip())
        elif b.type == "EDIT":
            if not doc.done:
                held = True                                # no [DONE]: the answer may be cut off, so no proposal is offered
            elif fields_checked(b.payload)[1]:
                notes.append(f"[EDIT.{b.number}] has a field opened with <<< and never closed with >>>, so nothing was offered.")
            else:
                proposal.append(b.payload.strip("\n"))
        elif b.type != "DONE":
            notes.append(f"[{b.type}.{b.number}] is not part of this action, so it was left out.")
    if held:
        notes.append("The answer has no [DONE] line, so no proposal from it was offered. The reply text is shown.")
    if doc.preamble.strip() and not reply:
        reply.append(doc.preamble.strip())
    text = ("=========== REPLY ===========\n" + "\n\n".join(reply) + "\n\n=========== PROPOSAL ===========\n"
            + "\n".join(proposal) + "\n\n=========== END ===========\n")
    return text, notes


def _rows_legacy(raw: str, kind: str, header: str) -> tuple:
    doc = parse(raw)
    notes = list(doc.issues)
    rows = []
    for b in doc.blocks:
        if not b.usable:
            notes.extend(b.issues)
            continue
        if b.type == kind:
            if b.payload.strip():
                rows.append(b.payload.strip())
        elif b.type != "DONE":
            notes.append(f"[{b.type}.{b.number}] is not part of this answer, so it was left out.")
    if not rows and not any(b.type == kind for b in doc.blocks):
        return raw, notes
    return _wrap(header, "\n".join(rows)), notes


def is_count(raw: str) -> bool:
    return looks_like(raw) and any(b.type == "COUNT" for b in parse(raw).blocks)


def is_review(raw: str) -> bool:
    if not looks_like(raw):
        return False
    kinds = {b.type for b in parse(raw).blocks}
    return "REVIEW" in kinds and "RHYME" not in kinds and "STRENGTH" not in kinds and "ARRANGE" not in kinds


def review_to_legacy(raw: str) -> tuple:
    doc = parse(raw)
    notes = list(doc.issues)
    lines = ["=========== REVIEW ==========="]
    for b in doc.blocks:
        if not b.usable:
            notes.extend(b.issues)
            continue
        if b.type == "REVIEW":
            if b.payload.strip():
                lines.append(b.payload.strip())
        elif b.type in ("FINDING", "ITEM"):
            lines.append("ITEM")
            if b.payload.strip():
                lines.append(b.payload.strip())
        elif b.type != "DONE":
            notes.append(f"[{b.type}.{b.number}] is not part of this review, so it was left out.")
    lines.append("=========== END ===========")
    return "\n".join(lines) + "\n", notes


def is_rhythm(raw: str) -> bool:
    return looks_like(raw) and any(b.type == "RHYTHM" for b in parse(raw).blocks)


def is_word(raw: str) -> bool:
    return looks_like(raw) and any(b.type == "WORD" for b in parse(raw).blocks)


def is_ending(raw: str) -> bool:
    return looks_like(raw) and any(b.type == "ENDING" for b in parse(raw).blocks)


def unwrap(raw: str, kind: str) -> tuple:
    """(text for the old parser, notes). The text is `raw` when this is not the new contract."""
    gates = {
        "rhyme": (is_rhyme, rhyme_as_json),
        "report": (is_report, report_to_legacy),
        "style": (is_style, style_to_legacy),
        "caption": (is_caption, caption_to_legacy),
        "arrange": (is_arrange, arrange_to_legacy),
        "action": (is_action, action_to_legacy),
        "count": (is_count, lambda s: _rows_legacy(s, "COUNT", "=========== ANALYSIS ===========")),
        "review": (is_review, review_to_legacy),
        "rhythm": (is_rhythm, lambda s: _rows_legacy(s, "RHYTHM", "=========== RHYTHM ===========")),
        "word": (is_word, lambda s: _rows_legacy(s, "WORD", "=========== SUGGESTIONS ===========")),
        "ending": (is_ending, lambda s: _rows_legacy(s, "ENDING", "=========== ENDINGS ===========")),
    }
    gate, conv = gates[kind]
    if not gate(raw):
        return raw, []
    return conv(raw)
