"""Clipboard text for an outside critic. No model call, no network, and nothing here writes the song, the report, or a session.

The text is a user prompt plus three labeled parts: the song snapshot, the in-app model's reading, and the measurements stored with that same snapshot.
A measurement and a model's sentence are never written as the same kind of fact. A long text is returned whole; the caller shows the length.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import spec_reports as SR

WARN_CHARS = 80000

NAMES = {
    "song_critic": "Song Critic",
    "rhyme_flow": "Rhyme & Flow",
    "vocal_flow": "Vocal Flow",
    "lyric_language": "Lyric Language",
    "music_producer": "Music Producer Analysis",
}

OPENING = """นี่คือเนื้อเพลงและผลวิเคราะห์จากโปรแกรมแต่งเพลงของฉัน

อยากให้คุณช่วยศึกษาข้อมูลที่เกี่ยวข้อง แล้ววิเคราะห์อย่างอิสระในฐานะนักวิจารณ์และโค้ชแต่งเพลง

คุณไม่จำเป็นต้องเห็นด้วยกับรายงานเดิม สามารถวิจารณ์ โต้แย้ง หรือเสนอแนวคิดใหม่ได้

หากค้นคว้าจากแหล่งข้อมูลภายนอกได้ ช่วยนำหลักการแต่งเพลง ภาษาศาสตร์ หรือความรู้ด้านดนตรีที่เกี่ยวข้องมาประกอบ โดยบอกแหล่งข้อมูลและแยกข้อเท็จจริงออกจากความคิดเห็น

เป้าหมายคือช่วยให้ฉันเข้าใจจุดแข็ง จุดอ่อน และทางเลือกในการพัฒนาเพลง โดยไม่จำเป็นต้องเปลี่ยนสิ่งที่ดีอยู่แล้ว"""

_DECISION = {"keep": "Keep", "adjust": "Adjust", "either": "Either"}
_SKIP_KEYS = {"raw", "diagnostics", "debug", "prompt_hash", "backend", "model", "token", "api_key", "path", "source"}


@dataclass(frozen=True)
class Pack:
    text: str
    chars: int
    notices: tuple
    outdated: bool
    scope: str


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_clean(x) for x in value]
        return "\n".join(x for x in parts if x)
    text = str(value).strip()
    if text in ("", "-", "—"):
        return ""
    return text


def _add(lines: list, label: str, value) -> None:
    text = _clean(value)
    if text:
        lines.append(f"{label}: {text}")


def _name(spec: str, spec_name: str) -> str:
    return (spec_name or "").strip() or NAMES.get(spec, spec or "Specialist")


def _section_lines(lyrics: str) -> list:
    try:
        rows = SR.sections_of(lyrics or "")
    except Exception:
        return []
    out = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        name, first, last = row[0], row[1], row[2]
        out.append(f"{name} L{first}-L{last}")
    return out


def _song_block(title: str, lyrics: str, language: str, style: dict, settings: dict, *, lyrics_changed: bool, why: str, has_audio: bool, include_lyrics: bool) -> list:
    style, settings = style or {}, settings or {}
    lines = ["# บริบทของเพลง", "ส่วนนี้เป็นข้อมูลของเพลงจากสแนปช็อตของรายงาน ไม่ใช่คำวิจารณ์"]
    _add(lines, "ชื่อเพลง", title)
    _add(lines, "ภาษา", language or style.get("language"))
    style_bits = [str(style.get(k)).strip() for k in ("genre", "mood", "energy") if _clean(style.get(k))]
    _add(lines, "แนว / สไตล์", ", ".join(style_bits))
    _add(lines, "เสียงร้อง", style.get("vocal"))
    _add(lines, "BPM", settings.get("bpm"))
    _add(lines, "ความยาวเป้าหมาย (วินาที)", settings.get("duration"))
    _add(lines, "จังหวะ", settings.get("timesignature"))
    _add(lines, "คีย์", settings.get("keyscale"))
    if why == "inputs":
        lines.append("ค่า BPM เสียงร้อง และความยาวด้านบนเป็นค่าของเพลงตอนนี้ ไม่ใช่ค่าตอนที่วิเคราะห์ หลักฐานด้านล่างยังเป็นของสแนปช็อตเดิม")
    secs = _section_lines(lyrics) if lyrics else []
    if secs:
        lines.append("ท่อนในสแนปช็อต: " + "; ".join(secs))
    if has_audio:
        lines.append("รายงานนี้มีไฟล์เสียงประกอบ")
    else:
        lines.append("การวิเคราะห์นี้มาจากข้อความเท่านั้น ไม่ได้ฟังไฟล์เสียง")
    if not include_lyrics:
        lines.append("เนื้อเพลงเต็มไม่ได้ถูกแนบในครั้งนี้")
        return lines
    if not lyrics:
        lines.append("สแนปช็อตนี้ไม่ได้เก็บตัวเนื้อเพลงไว้ จึงไม่ใส่เนื้อปัจจุบันแทน")
        return lines
    lines.append("")
    lines.append("# เนื้อเพลง (สแนปช็อตของรายงานนี้)")
    if lyrics_changed:
        lines.append("เนื้อด้านล่างเป็นเนื้อตอนที่รายงานนี้ถูกเขียน ไม่ใช่เนื้อที่อยู่ในอิดิเตอร์ตอนนี้")
    lines.append(SR.numbered(lyrics))
    return lines


def _status_block(state: str, why: str, lyrics_changed: bool) -> list:
    if lyrics_changed or why == "lyrics":
        return ["สถานะรายงาน: ล้าสมัย — เนื้อเพลงเปลี่ยนหลังการวิเคราะห์ ข้อความด้านล่างเป็นสแนปช็อตของรายงานนี้ ไม่ใช่การวิเคราะห์ใหม่ของเนื้อที่แก้แล้ว"]
    if why == "inputs" or (state == "outdated" and why == "inputs"):
        return ["สถานะรายงาน: ล้าสมัย — ค่าเพลงหรือภาษาเปลี่ยนหลังการวิเคราะห์ หลักฐานด้านล่างเป็นของสแนปช็อตเดิม ไม่ได้คำนวณใหม่"]
    if why == "prompt" or (state == "outdated" and why == "prompt"):
        return ["สถานะรายงาน: ล้าสมัย — คำสั่งของผู้เชี่ยวชาญเปลี่ยนหลังรายงานนี้ ข้อความด้านล่างยังเป็นรายงานเดิม"]
    if state == "partial":
        return ["สถานะรายงาน: บางส่วน — บางส่วนของคำตอบไม่ได้เข้ามาในรายงาน"]
    if state == "outdated":
        return ["สถานะรายงาน: ล้าสมัย — รายงานนี้ไม่ตรงกับเพลงที่เปิดอยู่ ข้อความด้านล่างเป็นสแนปช็อตเดิม"]
    return ["สถานะรายงาน: ปัจจุบัน"]


def _refs(f: dict, lyrics: str) -> str:
    refs = f.get("refs") or []
    bits = []
    for pair in refs:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        a, b = int(pair[0]), int(pair[1])
        if a <= 0:
            continue
        bits.append(f"L{a}" if a == b else f"L{a}-L{b}")
    if not bits and f.get("first"):
        a, b = int(f.get("first") or 0), int(f.get("last") or f.get("first") or 0)
        if a > 0:
            bits.append(f"L{a}" if a == b else f"L{a}-L{b}")
    return ", ".join(bits)


def _quote_block(f: dict, lyrics: str) -> list:
    refs = []
    for pair in f.get("refs") or []:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2 and int(pair[0]) > 0:
            refs.append((int(pair[0]), int(pair[1])))
    if not refs and f.get("first"):
        a = int(f.get("first") or 0)
        b = int(f.get("last") or a)
        if a > 0:
            refs.append((a, b))
    lines = []
    if refs and lyrics:
        shown = []
        for a, b in refs:
            bit = SR.excerpt(lyrics, a, b)
            if bit:
                shown.append(bit)
        if shown:
            lines.append("บรรทัดต้นฉบับจากสแนปช็อต:")
            lines.append("\n".join(shown))
    quote = _clean(f.get("quote"))
    if quote and f.get("quote_ok") is False:
        lines.append("คำพูดที่โมเดลให้มาไม่ตรงบรรทัดของสแนปช็อต จึงไม่ใช้คำพูดนั้นแทนเนื้อ")
    elif quote and not lines:
        lines.append("คำพูดที่โมเดลให้มา:")
        lines.append(quote)
    cited = _clean(f.get("evidence")) if isinstance(f.get("evidence"), str) else ""
    if cited and cited not in "\n".join(lines):
        lines.append("ข้อความที่รายงานอ้าง:")
        lines.append(cited)
    return lines


def _evidence_rows(f: dict) -> list:
    raw = f.get("evidence")
    if not isinstance(raw, list):
        return []
    rows = []
    for e in raw:
        if isinstance(e, dict) and e.get("id") and "line_a" in e:
            rows.append(e)
    return rows


def _one_sr_item(f: dict, lyrics: str, n: int) -> list:
    kind = _clean(f.get("kind")) or _clean(SR.STATUS_KIND.get(_clean(f.get("status")), f.get("status")))
    title = _clean(f.get("title")) or _clean(f.get("aspect")) or f.get("id") or str(n)
    lines = [f"## {n}. {title}"]
    _add(lines, "ชนิด", kind)
    _add(lines, "ประเภทประเด็น", f.get("issue_type"))
    if f.get("needs_review"):
        lines.append("สถานะการตรวจ: โมเดลระบุประเภท “" + (_clean(f.get("stated_issue")) or "-") + "” และคำตัดสิน “" + (_clean(f.get("stated_decision")) or "-")
                     + "” ซึ่งขัดกัน ข้อนี้ไม่ใช่ความผิดที่ยืนยัน และไม่มีถ้อยคำให้นำไปใช้")
    _add(lines, "ความมั่นใจที่โมเดลระบุ", f.get("confidence"))
    _add(lines, "ความสำคัญ", f.get("priority"))
    _add(lines, "บรรทัด", _refs(f, lyrics))
    lines += _quote_block(f, lyrics)
    _add(lines, "สิ่งที่พบ", f.get("observation") or f.get("finding"))
    _add(lines, "ทำไมจึงสำคัญ", f.get("interpretation") or f.get("reason"))
    _add(lines, "ข้อเสนอ", f.get("recommendation") or f.get("suggestion") or f.get("action"))
    _add(lines, "ทางเลือก", f.get("options"))
    _add(lines, "ผลได้ผลเสีย", f.get("tradeoff"))
    _add(lines, "เหตุผลที่คงไว้", f.get("keep"))
    decision = _clean(f.get("decision")).lower()
    if decision:
        _add(lines, "การตัดสินใจ", _DECISION.get(decision, decision))
    _add(lines, "ความสัมพันธ์ที่โมเดลระบุ", f.get("relation"))
    cited = _evidence_rows(f)
    if cited:
        lines.append("ไอดีหลักฐานที่ฟายดิ้งนี้อ้าง (จากแถวที่รายงานเก็บไว้):")
        for e in cited:
            lines.append(_rhyme_row(e))
    unver = _clean(f.get("unverified") or f.get("unverified_observation"))
    if unver:
        lines.append("ข้อสังเกตที่ยังไม่ยืนยัน (โมเดลสังเกตเอง ไม่ใช่การวัดของโปรแกรม): " + unver)
    _add(lines, "ผลต่อความหมายที่โมเดลระบุ", f.get("meaning"))
    _add(lines, "ผลต่อสัมผัสที่โมเดลระบุ", f.get("rhyme") or f.get("rhyme_effect"))
    _add(lines, "ผลต่อพยางค์ที่โมเดลระบุ", f.get("syllables"))
    ver = _clean(f.get("verification")).lower()
    if ver in ("text_based", "needs_melody", "needs_audio"):
        _add(lines, "การตรวจเพิ่ม", {"text_based": "ตรวจจากข้อความ", "needs_melody": "ต้องตรวจกับทำนอง", "needs_audio": "ต้องตรวจกับเสียง"}[ver])
    raw_ev = f.get("evidence")
    if isinstance(raw_ev, list):
        vrows = [e for e in raw_ev if isinstance(e, dict) and e.get("id") and "syllables" in e and "line_a" not in e]
        if vrows:
            lines.append("ไอดีหลักฐานเสียงที่ฟายดิ้งนี้อ้าง (จากแถวที่รายงานเก็บไว้):")
            for e in vrows:
                lines.append(f"{e.get('id')} | L{e.get('line')} | syllables:{e.get('syllables')}")
    _add(lines, "ถ้อยคำเปรียบเทียบที่โมเดลเสนอ", f.get("alternative") or f.get("proposal"))
    return lines


def _named(items: list, kinds: set) -> list:
    out = []
    for f in items:
        if (_clean(f.get("kind")) or "").lower() not in kinds:
            continue
        text = _clean(f.get("title")) or _clean(f.get("aspect")) or _clean(f.get("observation")) or _clean(f.get("finding"))
        if text and text not in out:
            out.append(text)
    return out


def _critique_sr(sr: dict, lyrics: str, finding_id: str) -> tuple:
    """(lines, scope). One finding when finding_id is set and exists. The words are the model's."""
    sr = sr or {}
    items = []
    for f in list(sr.get("aspects") or []) + list(sr.get("findings") or []):
        if isinstance(f, dict):
            items.append(f)
    actions = [a for a in (sr.get("actions") or []) if isinstance(a, dict)]
    picked = None
    if finding_id:
        for f in items + actions:
            if str(f.get("id") or "") == finding_id:
                picked = f
                break
        if picked is None:
            return ["ไม่พบฟายดิ้งที่เลือกในสแนปช็อตนี้ จึงไม่ใส่รายงานทั้งก้อนแทน"], "finding"
        body = ["# รายงานผู้เชี่ยวชาญ (เฉพาะฟายดิ้งที่เลือก)", "ส่วนนี้เป็นความเห็นของโมเดลในโปรแกรม ไม่ใช่ข้อเท็จจริงที่ยืนยันแล้ว และไม่ใช่ผลการวัดของโปรแกรม"]
        _add(body, "ภาพรวมของรายงาน", sr.get("headline"))
        body += _one_sr_item(picked, lyrics, 1)
        return body, "finding"
    body = ["# รายงานผู้เชี่ยวชาญ", "ส่วนนี้เป็นความเห็นของโมเดลในโปรแกรม ไม่ใช่ข้อเท็จจริงที่ยืนยันแล้ว และไม่ใช่ผลการวัดของโปรแกรม"]
    _add(body, "ภาพรวม", sr.get("headline"))
    _add(body, "เหตุผลของภาพรวม", sr.get("reading"))
    strong = _named(items, {"strength"})
    concern = _named(items, {"concern", "change"})
    if strong:
        body.append("จุดแข็งที่รายงานระบุ:")
        body += ["- " + x for x in strong]
    if concern:
        body.append("จุดที่รายงานชวนให้พิจารณา:")
        body += ["- " + x for x in concern]
    if items:
        body.append("รายการ:")
        for n, f in enumerate(items, 1):
            body += _one_sr_item(f, lyrics, n)
    if actions:
        body.append("แผนที่โมเดลเสนอ (ยังไม่ได้แก้เนื้อเพลง):")
        for n, a in enumerate(actions, 1):
            body += _one_sr_item(a, lyrics, n)
    for n, sec in enumerate(sr.get("sections") or [], 1):
        if not isinstance(sec, dict):
            continue
        reading = _clean(sec.get("reading"))
        if not reading:
            continue
        body.append(f"การอ่านท่อน {n} — {sec.get('name') or '-'} ({sec.get('lines') or '-'})")
        body.append("ความเห็นของโมเดล: " + reading)
    notes = [_clean(x) for x in (sr.get("notes") or [])]
    notes = [x for x in notes if x]
    if notes:
        body.append("โน้ตของโมเดล:")
        body += ["- " + x for x in notes]
    return body, "report"


def _critique_producer(prod: dict, lyrics: str, finding_id: str) -> tuple:
    prod = prod or {}
    findings = [f for f in (prod.get("findings") or []) if isinstance(f, dict)]
    if finding_id:
        picked = next((f for f in findings if str(f.get("id") or "") == finding_id), None)
        if picked is None:
            return ["ไม่พบฟายดิ้งที่เลือกในสแนปช็อตนี้ จึงไม่ใส่รายงานทั้งก้อนแทน"], "finding"
        findings = [picked]
        scope = "finding"
        head = "# รายงานผู้เชี่ยวชาญ (เฉพาะฟายดิ้งที่เลือก)"
    else:
        scope = "report"
        head = "# รายงานผู้เชี่ยวชาญ"
    body = [head, "ส่วนนี้เป็นความเห็นของโมเดลในโปรแกรมเรื่องโครง คิว และทิศทางดนตรี ไม่ใช่ผลการวัดจากไฟล์เสียง"]
    if scope == "report":
        _add(body, "ภาพรวม", prod.get("summary"))
        strengths = [_clean(x) for x in (prod.get("strengths") or [])]
        strengths = [x for x in strengths if x]
        if strengths:
            body.append("จุดที่โมเดลบอกว่าใช้ได้:")
            body += ["- " + x for x in strengths]
    for n, f in enumerate(findings, 1):
        body += _one_sr_item(f, lyrics, n)
    if scope == "report":
        unsure = [_clean(x) for x in (prod.get("uncertain") or [])]
        unsure = [x for x in unsure if x]
        if unsure:
            body.append("สิ่งที่โมเดลบอกว่าไม่แน่ใจ:")
            body += ["- " + x for x in unsure]
    return body, scope


def _rhyme_row(e: dict) -> str:
    place = (e.get("placement") or "-").replace("_", "-")
    return f"{e.get('id')} | L{e.get('line_a')}:{e.get('word_a')} | L{e.get('line_b')}:{e.get('word_b')} | {e.get('sound') or '-'} | {place} | {e.get('role') or '-'}"


def _rhyme_evidence(data: dict, only: "list | None") -> list:
    index = data.get("evidence_index") or {}
    rows = []
    if only is not None:
        source = only
    else:
        source = [index[k] for k in sorted(index, key=lambda x: int(str(x)[1:]) if str(x)[1:].isdigit() else 10**9)]
    for e in source:
        if isinstance(e, dict) and e.get("id"):
            rows.append(_rhyme_row(e))
    lines = ["# หลักฐาน Python เรื่องสัมผัส", "ส่วนนี้เป็นการวัดเสียงจากตัวสะกดในสแนปช็อต ไม่ใช่คะแนนคุณภาพ และไม่ใช่ความเห็นของโมเดล"]
    if data.get("cross_section_pairs") is not None and only is None:
        lines.append(f"คู่ข้ามท่อนที่โปรแกรมจับได้ในสแนปช็อต: {data.get('cross_section_pairs')} (จำนวนนี้ไม่ใช่คะแนนคุณภาพ)")
    if rows:
        lines += rows
    elif only is None:
        lines.append("สแนปช็อตนี้ไม่มีแถวสัมผัสที่เก็บไว้")
    limits = [_clean(x) for x in (data.get("limitations") or [])]
    limits = [x for x in limits if x]
    if limits:
        lines.append("ข้อจำกัดของ Python:")
        lines += ["- " + x for x in limits]
    return lines


def _vocal_evidence(data: dict) -> list:
    lines = ["# หลักฐาน Python เรื่องเสียงร้อง", "ส่วนนี้นับจากข้อความในสแนปช็อต ไม่ได้ฟังทำนอง และไม่ใช่ความเห็นของโมเดล"]
    totals = data.get("totals") or {}
    if totals:
        lines.append(f"รวมพยางค์ที่จำแนก {totals.get('total', 0)} คำเป็น {totals.get('live', 0)} คำตาย {totals.get('dead', 0)} ไม่แน่ใจ {totals.get('unsure', 0)}")
    density = data.get("density") or {}
    if density.get("known"):
        lines.append(f"ความแน่นเป็นการประมาณจาก BPM {density.get('bpm')} และความยาว {density.get('duration')} วินาที ตามที่เก็บในสแนปช็อต ไม่ใช่การวัดใหม่")
    elif density:
        lines.append("ความแน่นไม่ได้ถูกประเมินในสแนปช็อตนี้" + (": " + _clean(density.get("why")) if _clean(density.get("why")) else ""))
    index = data.get("evidence_index") or {}
    if index:
        lines.append("รหัสหลักฐานของสแนปช็อตนี้ (จำนวนพยางค์ที่โปรแกรมนับ ไม่ใช่คะแนนการร้อง):")
        for eid in sorted(index, key=lambda x: int(str(x)[1:]) if str(x)[1:].isdigit() else 10**9):
            e = index.get(eid) or {}
            if isinstance(e, dict) and e.get("id"):
                lines.append(f"{eid} | L{e.get('line')} | syllables:{e.get('syllables')} | section:{e.get('section') or '-'}")
    for row in data.get("lines") or []:
        if not isinstance(row, dict):
            continue
        mark = "~" if row.get("estimated") else ""
        phrases = "+".join(str(x) for x in (row.get("phrases") or [])) or "-"
        prefix = f"{row.get('id')} | " if row.get("id") else ""
        lines.append(prefix + f"L{int(row.get('line') or 0):03d} | {row.get('syllables')}{mark} syl | phrases {phrases} | live {row.get('live', 0)} | dead {row.get('dead', 0)} | unsure {row.get('unsure', 0)} | {row.get('level') or '-'} | {row.get('text') or ''}")
    for sec in data.get("sections") or []:
        if isinstance(sec, dict) and sec.get("name"):
            lines.append(f"ท่อน {sec.get('name')} L{sec.get('first')} | live {sec.get('live', 0)} | dead {sec.get('dead', 0)} | syllables {sec.get('syllables', '-')}")
    if not (data.get("lines") or totals):
        lines.append("สแนปช็อตนี้ไม่มีตารางพยางค์ที่เก็บไว้")
    return lines


def _critic_evidence(data: dict) -> list:
    lines = ["# หลักฐานที่โปรแกรมนับจากข้อความ", "ส่วนนี้เป็นโครงและบรรทัดที่ซ้ำคำต่อคำ ไม่ใช่คำตัดสินว่าเพลงดีหรือไม่ดี"]
    outline = data.get("outline") or []
    if outline:
        bits = []
        for o in outline:
            if isinstance(o, dict):
                bits.append(f"{o.get('name')} L{o.get('first')}-L{o.get('last')} ({o.get('sung', 0)} sung lines)")
        if bits:
            lines.append("โครง: " + "; ".join(bits))
    repeats = data.get("repeats") or []
    if repeats:
        bits = []
        for r in repeats:
            if isinstance(r, dict):
                nums = ", ".join(f"L{n}" for n in (r.get("lines") or []))
                bits.append(f"“{r.get('text', '')}” ที่ {nums}")
        if bits:
            lines.append("บรรทัดที่ซ้ำคำต่อคำ: " + "; ".join(bits))
    if len(lines) == 2:
        lines.append("สแนปช็อตนี้ไม่มีโครงที่เก็บไว้")
    return lines


def _evidence_block(spec: str, data: dict, cited: "list | None") -> list:
    data = data or {}
    if spec == "rhyme_flow":
        block = _rhyme_evidence(data, cited)
        if cited is not None:
            rest = []
            cited_ids = {e.get("id") for e in cited if isinstance(e, dict)}
            index = data.get("evidence_index") or {}
            for key in sorted(index, key=lambda x: int(str(x)[1:]) if str(x)[1:].isdigit() else 10**9):
                row = index[key]
                if isinstance(row, dict) and row.get("id") not in cited_ids:
                    rest.append(_rhyme_row(row))
            if rest:
                block.append("แถวอื่นในสแนปช็อตเดียวกัน ที่ฟายดิ้งนี้ไม่ได้อ้าง:")
                block += rest
            elif not cited:
                block.append("ฟายดิ้งนี้ไม่มีไอดีหลักฐานที่ตรวจผ่าน")
        return block
    if spec == "vocal_flow":
        return _vocal_evidence(data)
    if spec == "song_critic":
        return _critic_evidence(data)
    if spec == "lyric_language":
        return ["# หลักฐานประกอบภาษา", "รายงานภาษานี้ไม่ได้เก็บตารางพยางค์แยกไว้กับสแนปช็อต โปรแกรมไม่ได้คำนวณใหม่ตอนคัดลอก"]
    if spec == "music_producer":
        return ["# หลักฐานประกอบของ Music Producer", "รายงานนี้ไม่มีตารางวัดจาก Python แยกจากความเห็นของโมเดล โครงและคิวอยู่ในรายงานด้านบน เป็นข้อเสนอของโมเดล ไม่ใช่การวัดจากไฟล์เสียง"]
    if data.get("evidence_index"):
        return _rhyme_evidence(data, cited)
    if data.get("lines") and data.get("totals"):
        return _vocal_evidence(data)
    if data.get("outline") or data.get("repeats"):
        return _critic_evidence(data)
    return ["# หลักฐานประกอบ", "รายงานนี้ไม่มีก้อนหลักฐานแยกที่เก็บไว้ โปรแกรมไม่ได้สร้างหลักฐานใหม่ตอนคัดลอก"]


def _cited_rows(sr: dict, prod: dict, finding_id: str) -> "list | None":
    if not finding_id:
        return None
    pool = []
    if sr:
        pool += list(sr.get("aspects") or []) + list(sr.get("findings") or [])
    if prod:
        pool += list(prod.get("findings") or [])
    for f in pool:
        if isinstance(f, dict) and str(f.get("id") or "") == finding_id:
            return _evidence_rows(f)
    return []


def build_pack(*, spec: str, title: str = "", lyrics_snapshot: str = "", lyrics_now: str = "", language: str = "", style: "dict | None" = None,
               settings: "dict | None" = None, state: str = "current", why: str = "", sr: "dict | None" = None, evidence: "dict | None" = None,
               producer: "dict | None" = None, finding_id: str = "", include_lyrics: bool = True, include_evidence: bool = True, include_critique: bool = True,
               has_audio: bool = False, spec_name: str = "", lyrics_changed: "bool | None" = None) -> Pack:
    """The full clipboard text. Does not shorten it, does not read the live editor, and does not recompute a rhyme or a syllable."""
    changed = lyrics_now != lyrics_snapshot if lyrics_changed is None else bool(lyrics_changed)
    if lyrics_changed is None and not lyrics_now:
        changed = False
    notices = []
    outdated = changed or why in ("lyrics", "inputs", "prompt") or state == "outdated"
    if outdated:
        notices.append("This report is outdated. The text uses the analysis snapshot.")
    parts = [OPENING, ""]
    parts += _status_block(state, why, changed)
    parts.append(f"ผู้เชี่ยวชาญ: {_name(spec, spec_name)}")
    parts.append("")
    parts += _song_block(title, lyrics_snapshot or "", language, style or {}, settings or {}, lyrics_changed=changed, why=why, has_audio=has_audio, include_lyrics=include_lyrics)
    scope = "finding" if finding_id else "report"
    if include_critique:
        parts.append("")
        if producer is not None and sr is None:
            block, scope = _critique_producer(producer, lyrics_snapshot or "", finding_id)
        else:
            block, scope = _critique_sr(sr or {}, lyrics_snapshot or "", finding_id)
        parts += block
    else:
        parts += ["", "# รายงานผู้เชี่ยวชาญ", "ผลวิเคราะห์ของโมเดลไม่ได้ถูกแนบในครั้งนี้"]
    if include_evidence:
        parts.append("")
        parts += _evidence_block(spec, evidence or {}, _cited_rows(sr, producer, finding_id) if finding_id else None)
    else:
        parts += ["", "# หลักฐานประกอบ", "หลักฐานทางเทคนิคไม่ได้ถูกแนบในครั้งนี้"]
    text = "\n".join(parts).rstrip() + "\n"
    if len(text) > WARN_CHARS:
        notices.append("This text is long. It was not shortened.")
    return Pack(text, len(text), tuple(notices), outdated, scope)
