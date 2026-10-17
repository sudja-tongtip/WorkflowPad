"""
llm_engine_groom_cast_review.py — Outline character/outfit cast-review UI helpers (outline_cast_review_rows,
apply_cast_review_outfits, detect_sheet_field_leaked_as_name) and groom_final_check_issues, extracted from
the old llm_engine_groom_legacy.py by static reachability trace (only what llm_engine_groom.py actually
imports). Groom itself never calls these — they back the Outline Step 1 character/outfit review dialog.
"""

__version__ = "20260821_3310"

import asyncio, contextvars, copy, functools, json, random, time, uuid

from contextlib import contextmanager

from pathlib import Path

from .constants import LLM_DIR, DEBUG_DIR, GENRE_MAP, lang_prompt_name

import re as _re_clothing

_GARMENT_NOUNS = {
    "shirt", "t-shirt", "tee", "blouse", "bra", "camisole", "cami", "tank",
    "sweater", "hoodie", "jacket", "coat", "vest", "chemise", "corset", "top",
    "crop", "panties", "panty", "underwear", "briefs", "thong", "shorts",
    "pants", "trousers", "skirt", "jeans", "pantyhose", "tights", "boxers",
    "knickers", "leggings", "hose", "dress", "robe", "kimono", "gown",
    "jumpsuit", "onesie", "swimsuit", "bikini", "shoes", "boots", "heels",
    "sandals", "loafers", "sneakers", "oxfords", "pumps", "socks", "stockings",
    "hat", "cap", "belt", "tie", "scarf", "gloves", "cloak", "cape", "uniform",
    "suit", "blazer", "cardigan", "raincoat", "overcoat",
    # เพิ่มชิ้นที่เจอจริงในเรื่องแต่ยังไม่มีในรายการ — ชื่อชิ้นที่หายไปทำให้ชุดตั้งต้นถูกมองว่า "ไม่มีชุด"
    "bodysuit", "leotard", "lingerie", "nightgown", "nightdress", "negligee",
    "sarong", "tunic", "shawl", "veil", "apron", "overalls", "kilt", "sari",
    # ชุดประจำชาติ/ชุดเฉพาะที่เจอในเรื่องจริง — ถ้าคำหลักของชุดไม่อยู่ในรายการนี้ ตัวตรวจจะมองว่า
    # "ท่อนนี้ไม่มีเสื้อผ้าอยู่เลย" แล้วไปหยิบคำอื่นในวลีมาเป็นชุดแทน (เคสจริง: Hanfu → bamboo)
    "hanfu", "cheongsam", "qipao", "yukata", "haori", "hakama", "kaftan", "abaya",
    "dashiki", "poncho", "mantle", "doublet", "jerkin", "surcoat", "chiton", "toga",
    "peplos", "salwar", "kurta", "lehenga", "dirndl", "ao dai", "barong",
    "kimono", "yukata", "hakama", "poncho", "trunks", "loincloth", "collar",
    "garter", "chemise", "slip", "bralette", "camisole", "sweatpants",
    "choker", "necklace", "bracelet", "anklet", "earrings", "ribbon", "sash",
    "headband", "bandana", "mask", "blindfold", "cuffs", "harness", "corselet",
    "tracksuit", "wetsuit", "armor", "armour", "cassock", "habit", "gi",
}

# ชื่อชิ้นเสื้อผ้าภาษาไทย — ภาษาไทยไม่มีช่องว่างระหว่างคำ จึงใช้ \b ไม่ได้ ต้องเทียบแบบ substring
# ถ้าไม่มีรายการนี้ ชีตที่เขียนชุดเป็นภาษาไทยจะสร้างบัญชีเสื้อผ้าไม่ได้เลยสักชิ้น แล้วทุกด่านที่ตาม
# มา (ถอด ดัน จัดกลับ) ก็ไม่มีอะไรให้ทำงานด้วย
_TH_GARMENT_NOUNS = (
    "บอดี้สูท", "ชุดชั้นใน", "ชุดนอน", "ชุดว่ายน้ำ", "ชุดกระโปรง", "ชุดเดรส", "เดรส", "ชุด",
    "เสื้อคลุม", "เสื้อกล้าม", "เสื้อยืด", "เสื้อเชิ้ต", "เสื้อ", "กางเกงใน", "กางเกง",
    "กระโปรง", "ผ้าซิ่น", "ผ้าคลุม", "ผ้าพันคอ", "ผ้าถุง", "ยกทรง", "บรา", "ถุงน่อง",
    # คำยืมที่เขียนด้วยอักษรไทย — เจอจริงในไฟล์เรื่อง ("แจ็คเก็ตทวินเนอร์") ถ้าไม่มีในรายการ
    # ชีตที่ระบุชุดไว้ถูกต้องจะถูกมองว่า "ไม่มีชุด"
    "แจ็คเก็ต", "แจ๊คเก็ต", "เสื้อโค้ท", "โค้ท", "สเวตเตอร์", "ฮู้ด", "ฮูด", "เดรส",
    "ยีนส์", "เชิ้ต", "สูทเชิ้ต", "บู๊ท", "บูท", "สนีกเกอร์", "ส้นสูง", "ชุดว่ายน้ำ",
    "ถุงเท้า", "รองเท้า", "หมวก", "เข็มขัด", "โสร่ง", "กิโมโน", "ฮันฝู", "ชุดฮั่นฝู",
)

def _gender_noun_from_text(text: str) -> str:
    """แปลงข้อความชีตเป็นคำเพศภาษาอังกฤษที่ image prompt ใช้ได้ — ว่างถ้าเดาไม่ได้"""
    blob = f" {(text or '').lower().replace(',', ' ').replace(':', ' ').replace('—', ' ')} "
    checks = (
        (" girl ", "girl"), (" boy ", "boy"),
        (" woman ", "woman"), (" female ", "woman"),
        (" man ", "man"), (" male ", "man"),
        (" หญิง ", "woman"), (" ผู้หญิง ", "woman"), (" สาว ", "woman"),
        (" ชาย ", "man"), (" ผู้ชาย ", "man"), (" หนุ่ม ", "man"),
    )
    for token, noun in checks:
        if token in blob:
            return noun
    return ""

_SHEET_COUNT_HEAD_RE = _re_clothing.compile(
    r'^\d+\s+(?:visible\s+)?(?:women|woman|men|man|girls?|boys?)\b',
    _re_clothing.I,
)

def _split_name_aliases(raw_name: str) -> tuple:
    """"Krit (ชายช่างภาพ, photographer)" → ("Krit", ["ชายช่างภาพ", "photographer"])
    ชื่อที่ไม่มีวงเล็บคืน alias ว่าง — รูปแบบนี้คือที่ Pass A ถูกสั่งให้เขียนเมื่อเรื่องไม่ได้ตั้งชื่อไว้"""
    raw = (raw_name or "").strip()
    m = _re_clothing.match(r'^(.*?)[\s]*[\(（]([^)）]*)[\)）]\s*$', raw)
    if not m:
        return raw, []
    canon = m.group(1).strip()
    inner = m.group(2)
    aliases = []
    # ชีตถูกสั่งให้เขียน "Name (aliases: Him, The Person Ahead, viewer)" — "aliases:" เป็นป้ายกำกับ
    # ไม่ใช่ชื่อเรียก ถ้าไม่ตัดออก alias ตัวแรกจะกลายเป็น "aliases: him" ซึ่งไม่มีวันแมตช์อะไรเลย
    inner = _re_clothing.sub(r'^\s*(?:aliases?|alias|also known as|aka)\s*[:：]\s*', '', inner,
                             flags=_re_clothing.I)
    # เดิมบรรทัดนี้มีอักขระ backspace จริงอยู่ในสตริงแทนที่จะเป็น \b ของ regex — ตัวคั่น aka/หรือ
    # จึงไม่เคยทำงาน alias ที่เขียนด้วยคำพวกนี้เลยถูกอ่านรวมเป็นชื่อเดียว
    for part in _re_clothing.split(r'[,;/]|\baka\b|\bหรือ\b', inner):
        part = part.strip(" .")
        if part:
            aliases.append(part)
    return (canon or raw), aliases

# ── POV / off-screen presence ────────────────────────────────
# บางเรื่องมีคนที่ "มีอยู่ในเรื่อง" แต่ "ไม่ควรเห็นตัว": กล้องยืนแทนสายตาของเขา หรือเรื่องบอกเองว่าไม่เห็นเขา
# ถ้าปล่อยให้เขาเป็นตัวละครที่มองเห็นได้ ชีตจะเสกอายุ/ผม/หน้า/เสื้อผ้าให้ แล้วทุกช็อตก็มีคนยืนอยู่ในภาพที่
# เรื่องตั้งใจให้ว่าง — เขายังเป็น identity เดียวเชิงเรื่อง (alias ทุกคำ resolve มาที่เขา) แต่ไม่เข้า visible roster
_POV_MARK_RE = _re_clothing.compile(
    r'POV\s*[/_\- ]?\s*OFF[\s\-_]?SCREEN|OFF[\s\-_]?SCREEN\s+PRESENCE|POV[_\s]?OFFSCREEN'
    # ชีตเขียนสถานะนี้ได้หลายแบบ — ทุกแบบต้องอ่านออกเป็นสถานะเดียวกัน ไม่งั้นด่านหลังบางด่าน
    # จะมองว่าคนนี้เป็นตัวละครปกติแล้ววาดร่างให้
    r'|never (?:visually )?(?:depicted|shown|rendered|seen on screen)'
    r'|not (?:visually )?depicted|never appears on screen'
    r'|represented by the camera|camera/viewer position'
    r'|กล้องแทนสายตา|ไม่ปรากฏตัวในภาพ|ไม่ถูกวาด',
    _re_clothing.I,
)

def _is_pov_entry(rest: str) -> bool:
    """บรรทัดชีตนี้เป็น POV/off-screen presence ไหม — ตัดสินจากป้ายที่ Pass A ถูกสั่งให้เขียนเท่านั้น
    ไม่เดาจากเนื้อความ เพราะเดาผิดทางนี้แปลว่าตัวละครจริงหายไปจากภาพทั้งเรื่อง"""
    return bool(_POV_MARK_RE.search(rest or ""))

# "ชีตผิดตั้งแต่ต้น" เป็นความเสียหายที่เงียบที่สุด เพราะทุกด่านหลังทำงานถูกต้องกับข้อมูลผิด แล้ว
# Checker ก็รายงานว่า "ตรงกับชีต" ตรวจแบบ deterministic ตรงนี้ก่อนชีตจะกลายเป็น canonical
# ทุกข้อรายงานอย่างเดียว ไม่แก้ให้เอง — การเดาว่าใครเป็นเจ้าของที่ถูกต้อง คือบั๊กเดิมในอีกทิศทาง
# ข้อความที่ "ดูเหมือนบอกว่าใส่เสื้อผ้า" แต่ไม่มีชิ้นไหนให้วาดจริง — ตรวจอย่างเดียว ไม่เขียนทับ
# แยกจาก _GENERIC_OUTFIT_RE โดยตั้งใจ: ตัวนั้นถูกใช้ "แทนที่" ข้อความใน prompt ด้วยชุด canonical
# ส่วนตัวนี้ใช้ตัดสินว่าชุดยัง unresolved อยู่ ถ้าเอาไปรวมกันจะกลายเป็นการแก้ให้เงียบ ๆ แทนที่จะรายงาน
_OUTFIT_PLACEHOLDER_RE = _re_clothing.compile(
    r'\b(?:unspecified|unknown|not\s+specified|undefined|unclear|tbd|n/?a)\b'
    r'|\bimplied\s+(?:attire|clothing|clothes|outfit|garments?|by\b)'
    r'|\b(?:fully|properly|appropriately)\s+(?:clothed|dressed)\b'
    r'|\b(?:normal|ordinary|everyday|typical|standard|generic|simple|plain|casual|basic|suitable|appropriate)'
    r'\s+(?:clothes|clothing|attire|outfit|garments?|wear)\b'
    r'|\b(?:an?\s+)?outfit\b(?!\s*[:\-—])'
    r'|\bdressed\s+(?:normally|appropriately|casually)\b'
    r'|ไม่ระบุ|ไม่แน่ชัด|แต่งกายทั่วไป|ใส่เสื้อผ้าปกติ',
    _re_clothing.I,
)

# คนที่เรื่องบอกเองว่าเปลือย — ชีตที่ไม่มีชื่อชิ้นเสื้อผ้าเลยถือว่าถูกต้องสำหรับคนกลุ่มนี้
_SHEET_NUDE_RE = _re_clothing.compile(
    r'\b(?:nude|naked|unclothed|bare(?:\s+skinned)?|wears?\s+nothing|no\s+clothing|'
    r'no\s+clothes|fully\s+exposed)\b|เปลือยกาย|เปลือยเปล่า|ไม่สวมเสื้อผ้า|ไม่ใส่เสื้อผ้า',
    _re_clothing.I,
)

def _text_has_garment(text: str) -> bool:
    """ข้อความนี้เอ่ยชื่อชิ้นเสื้อผ้าที่วาดได้จริงอย่างน้อยหนึ่งชิ้นไหม

    ใช้คลังคำที่มีอยู่แล้วทั้งอังกฤษ (_GARMENT_NOUNS) และไทย (_TH_GARMENT_NOUNS) — คำไทยไม่มี
    ช่องว่างคั่นจึงต้องเทียบแบบ substring ไม่ใช่ token"""
    low = (text or "").lower()
    if not low.strip():
        return False
    if any(t in low for t in _TH_GARMENT_NOUNS):
        return True
    _toks = {w for w in _re_clothing.split(r'[^A-Za-z\-]+', low) if w}
    return bool(_toks & _GARMENT_NOUNS) or any(
        w.rstrip("s") in _GARMENT_NOUNS for w in _toks)

def _sheet_line_is_nude(rest: str) -> bool:
    """บรรทัดนี้บอกว่าคนนี้เปลือยตามเรื่องหรือเปล่า — ต้องไม่ใช่แค่คำว่า bare ที่ติดมากับอย่างอื่น
    ("bare feet", "bare shoulders" ไม่ได้แปลว่าเปลือยทั้งตัว)"""
    low = (rest or "").lower()
    if _re_clothing.search(r'\bbare\s+(?:feet|foot|shoulders?|arms?|legs?|hands?|midriff|back)\b', low):
        low = _re_clothing.sub(
            r'\bbare\s+(?:feet|foot|shoulders?|arms?|legs?|hands?|midriff|back)\b', ' ', low)
    return bool(_SHEET_NUDE_RE.search(low))

def _iter_sheet_entries(char_desc: str):
    """Yield (name, rest) from Name: ... or Name | gender | age | ... lines.

    Skips the count header (\"3 women, 6 men\") and Animals: lines.
    """
    for line in (char_desc or "").splitlines():
        line = (line or "").strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith(("animal", "สัตว์", "animals:")):
            continue
        if _SHEET_COUNT_HEAD_RE.match(line):
            continue
        name, rest = "", ""
        first_pipe = line.find("|")
        # ชื่อที่มี alias ในวงเล็บเขียนได้ว่า "Name (aliases: A, B): ..." — โคลอนตัวแรกอยู่ในวงเล็บ
        # ถ้าตัดตรงนั้น ชื่อจะกลายเป็น "Name (aliases" และ alias ทั้งชุดหลุดไปอยู่ฝั่งคำบรรยาย
        # หาโคลอนที่อยู่นอกวงเล็บแทน — นี่คือตัวที่คั่นชื่อกับเนื้อจริง
        _depth = 0
        first_colon = -1
        for _k, _c in enumerate(line):
            if _c in "([{（":
                _depth += 1
            elif _c in ")]}）":
                _depth = max(0, _depth - 1)
            elif _c == ":" and _depth == 0:
                first_colon = _k
                break
        if first_pipe >= 0 and (first_colon < 0 or first_pipe < first_colon):
            parts = [p.strip() for p in line.split("|")]
            name = parts[0].strip().strip("-•*").strip()
            rest = ", ".join(p for p in parts[1:] if p)
        elif first_colon >= 0:
            name = line[:first_colon].strip().strip("-•*").strip()
            rest = line[first_colon + 1:].strip()
        else:
            continue
        # เพดานความยาวมีไว้กันประโยคร้อยแก้วถูกอ่านเป็นชื่อ — แต่ชื่อที่มี alias ในวงเล็บ
        # ("Dao (หญิงสาว, the young woman)") ยาวเกิน 40 ได้ตามปกติ วัดเฉพาะชื่อจริง
        _canon_only, _ = _split_name_aliases(name)
        if not name or len(_canon_only or name) >= 40:
            continue
        if name.lower().startswith("this story"):
            continue
        yield name, rest

# ฟิลด์ในสคีมา "character" ของ Outline Pass A (gender/age/nationality/hair/eyes/skin/build/
# Outfit/personality/desire/fear/speaking style) ต้องอยู่บรรทัดเดียวกับ Name: ของคนนั้น — ถ้า LLM
# เผลอขึ้นบรรทัดใหม่ให้คำพวกนี้ (พบจริง: "Outfit" ทั้งบรรทัด) _iter_sheet_entries จะอ่านเป็นคนใหม่
# ชื่อ "Outfit" ทันที เพราะมันมองแค่ "มี : ก่อน" ไม่เช็คว่าคำนั้นเป็นชื่อจริงไหม ตัวนี้ตรวจจับ (ไม่ซ่อม)
# เคสนั้นไว้ให้ UI เตือนผู้ใช้แทนที่จะปล่อยให้การ์ดตัวละครปลอมโผล่เงียบๆ
_SHEET_RESERVED_FIELD_WORDS = {
    "outfit", "age", "gender", "nationality", "personality", "desire", "fear",
    "speaking", "speaking style", "hair", "eyes", "skin", "build",
    "permanent body fact", "body fact", "name",
}

def detect_sheet_field_leaked_as_name(char_desc: str) -> str:
    """คืนคำที่หลุดมาเป็น "ชื่อคน" ถ้าเจอ (เช่น "Outfit") หรือ "" ถ้าชีตปกติดี — ไม่แก้อะไร แค่ตรวจ."""
    for raw_name, _rest in _iter_sheet_entries(char_desc or ""):
        canon, _al = _split_name_aliases(raw_name)
        name = (canon or raw_name or "").strip().lower()
        if name in _SHEET_RESERVED_FIELD_WORDS:
            return raw_name.strip()
    return ""

def _nat_from_sheet_rest(rest: str) -> str:
    """ดึงสัญชาติจากชีตหลัง Name: — 'female, 19, Thai, ผมยาว...' → 'Thai'
    ชีตเขียนเรียง gender, age, nationality เสมอตาม schema จึงมองที่ 4 ช่องแรกพอ
    รับทั้งคำอังกฤษ (Thai/Japanese/...) และคำในภาษาของเรื่อง (ไทย/ญี่ปุ่น) โดยแปลงเป็นคำอังกฤษ
    ที่ image prompt ใช้ได้ — ถ้าไม่รู้จักคำนั้นก็คืนค่าตามที่เขียนไว้ (โมเดลแปลต่อได้)"""
    import re as _re_nat
    _MAP = {
        "ไทย": "Thai", "คนไทย": "Thai", "ญี่ปุ่น": "Japanese", "เกาหลี": "Korean",
        "จีน": "Chinese", "อเมริกัน": "American", "อังกฤษ": "British", "ฝรั่งเศส": "French",
        "รัสเซีย": "Russian", "อินเดีย": "Indian", "เวียดนาม": "Vietnamese", "ลาว": "Lao",
        "พม่า": "Burmese", "กัมพูชา": "Cambodian", "ฟิลิปปินส์": "Filipino", "เยอรมัน": "German",
        "อิตาลี": "Italian", "สเปน": "Spanish", "บราซิล": "Brazilian", "แอฟริกัน": "African",
    }
    _KNOWN = {
        "thai", "japanese", "korean", "chinese", "taiwanese", "vietnamese", "filipino", "indonesian",
        "malay", "indian", "american", "british", "english", "irish", "scottish", "french", "german",
        "italian", "spanish", "portuguese", "brazilian", "mexican", "russian", "ukrainian", "polish",
        "dutch", "swedish", "norwegian", "danish", "finnish", "greek", "turkish", "arab", "egyptian",
        "persian", "iranian", "israeli", "african", "nigerian", "kenyan", "australian", "canadian",
        "lao", "burmese", "cambodian", "nepali", "pakistani", "bangladeshi", "mongolian", "tibetan",
    }
    head = (rest or "").split(",")[:4]
    for seg in head:
        s = seg.strip().strip("()[]")
        if not s:
            continue
        for th, en in _MAP.items():
            if th in s:
                return en
        # "Japanese/American" หรือ "Thai-Chinese" → เก็บทั้งก้อนถ้าทุกท่อนเป็นสัญชาติที่รู้จัก
        parts = [x for x in _re_nat.split(r'[/\-–]', s) if x.strip()]
        if parts and all(x.strip().lower() in _KNOWN for x in parts):
            return "/".join(x.strip().capitalize() for x in parts)
    return ""

def _age_from_sheet_rest(rest: str) -> str:
    """ดึงอายุจากช่วงต้นของชีตหลัง Name: — 'female, 18,' หรือ 'หญิง อายุ 23 ปี'"""
    import re as _re_age
    head = ",".join((rest or "").split(",")[:4])
    m = _re_age.search(r'อายุ\s*(\d{1,2})\s*ปี?', head)
    if not m:
        m = _re_age.search(r'\b(\d{1,2})\s*(?:-?year-?old|years?\s*old|ปี)\b', head, _re_age.I)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 99:
            return str(n)
    for seg in head.split(","):
        s = seg.strip()
        if s.isdigit():
            n = int(s)
            if 5 <= n <= 90:
                return str(n)
    # ช่วงอายุ ("20s", "mid-30s", "early 40s", "วัย 20") — เดิมคืนค่าว่างเงียบ แล้ว IDENTITY LOCK
    # กลายเป็น "Name (woman)" ไม่มีอายุเลย คืนช่วงตามที่ชีตเขียนดีกว่าเสกอายุที่เรื่องไม่ได้บอก
    m = _re_age.search(r'\b(early|mid|late)?[\s-]*([1-9]\d)s\b', head, _re_age.I)
    if m:
        q, dec = m.group(1), m.group(2)
        if 10 <= int(dec) <= 90:
            return f"{q.lower()}-{dec}s" if q else f"{dec}s"
    m = _re_age.search(r'วัย\s*([1-9]\d)', head)
    if m and 10 <= int(m.group(1)) <= 90:
        return f"{m.group(1)}s"
    return ""

from .comfy_api  import comfy_queue, comfy_history

_GROOM_CHECK_KEYS = ("characters", "opening_outfits", "props", "environment",
                     "physical_lighting", "events", "continuity", "genre_payoff")

def groom_final_check_issues(check: dict | None) -> list:
    """(หมวด, สถานะ, หมายเหตุ) เฉพาะหมวดที่ไม่ผ่าน — ใช้วาด UI ไม่ได้ตัดสินอะไรเพิ่ม"""
    out = []
    for k in _GROOM_CHECK_KEYS:
        item = ((check or {}).get("checks") or {}).get(k) or {}
        st = str(item.get("status") or "").upper()
        if st in ("WARNING", "FAIL"):
            out.append((k, st, str(item.get("note") or "").strip()))
    return out

def _visible_sheet_people(char_desc: str) -> list[tuple[str, str, str]]:
    """(canonical_name, gender_noun, rest) for drawable humans — POV/off-screen omitted."""
    out: list[tuple[str, str, str]] = []
    seen: set = set()
    for raw_name, rest in _iter_sheet_entries(char_desc or ""):
        if _is_pov_entry(rest):
            continue
        canon, _al = _split_name_aliases(raw_name)
        name = (canon or raw_name or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append((name, _gender_noun_from_text(rest) or "", rest))
    return out

def outline_cast_review_rows(char_desc: str) -> list[dict]:
    """Compact roster for the Step 1 character + outfit review gate.

    Outfit completeness is a REVIEW flag (empty / placeholder / missing Outfit:),
    never a Python semantic verdict about underwear or 'complete enough'.
    """
    rows: list[dict] = []
    _gender = {"woman": "Female", "girl": "Female", "man": "Male", "boy": "Male"}
    for name, gender_noun, rest in _visible_sheet_people(char_desc):
        age = _age_from_sheet_rest(rest)
        age_s = str(age) if str(age).isdigit() else ""
        nat = _nat_from_sheet_rest(rest)
        labeled = _sheet_outfit_text(rest)
        if labeled:
            garments = [p.strip() for p in labeled.split(",") if p.strip()]
            outfit_text = labeled.strip()
        else:
            garments = [p.strip() for p in (rest or "").split(",")
                        if p.strip() and _text_has_garment(p)]
            outfit_text = ", ".join(garments)
            if not outfit_text:
                ph = _OUTFIT_PLACEHOLDER_RE.search(rest or "")
                if ph:
                    outfit_text = ph.group(0)
        if _sheet_line_is_nude(rest) and not garments:
            garments = ["nude"]
            outfit_text = outfit_text or "nude"
        warn = _opening_outfit_review_flag(rest)
        rows.append({
            "name": name,
            "gender": _gender.get(gender_noun, gender_noun or ""),
            "age": age_s,
            "nationality": nat,
            "outfit": garments,
            "outfit_line": " · ".join(garments),
            "outfit_text": outfit_text,
            "outfit_ok": not warn,
            "outfit_warning": warn,
        })
    return rows

_OUTFIT_LABEL_RE = _re_clothing.compile(r'(?i)\bOutfit\s*:')

_OUTFIT_CAPTURE_RE = _re_clothing.compile(
    r'(?i)\bOutfit\s*:\s*(.*?)(?=\s*(?:Permanent Body Fact|Personality|Desire|Fear|'
    r'Speaking(?:\s+style)?)\s*:|$)',
    _re_clothing.S,
)

def _sheet_outfit_text(rest: str) -> str:
    """Garment inventory after an Outfit: label, or empty if the label is missing."""
    m = _OUTFIT_CAPTURE_RE.search(rest or "")
    return (m.group(1).strip(" ;.") if m else "")

def _opening_outfit_review_flag(rest: str) -> str:
    """Flag empty / placeholder / missing Outfit: only. No completeness authority."""
    if _sheet_line_is_nude(rest):
        return ""
    labeled = _sheet_outfit_text(rest)
    if not _OUTFIT_LABEL_RE.search(rest or ""):
        return "missing Outfit field"
    if not labeled.strip():
        return "empty Outfit field"
    if _OUTFIT_PLACEHOLDER_RE.search(labeled):
        return "placeholder outfit"
    return ""

def _rewrite_sheet_outfit_rest(rest: str, new_outfit: str) -> str:
    new_outfit = (new_outfit or "").strip().strip(" ;.")
    if not new_outfit:
        return rest or ""
    m = _OUTFIT_CAPTURE_RE.search(rest or "")
    if m:
        return ((rest or "")[:m.start(1)] + new_outfit + (rest or "")[m.end(1):]).strip()
    ins = f"Outfit: {new_outfit}"
    marker = _re_clothing.search(
        r'(?i)\b(?:Permanent Body Fact|Personality|Desire|Fear|Speaking(?:\s+style)?)\s*:',
        rest or "")
    if marker:
        head = (rest or "")[:marker.start()].rstrip(" ,;")
        tail = (rest or "")[marker.start():].lstrip(" ,;")
        if head:
            return f"{head}, {ins}, {tail}"
        return f"{ins}, {tail}"
    head = (rest or "").rstrip(" ,;")
    return f"{head}, {ins}" if head else ins

def _apply_outfit_to_sheet_line(line: str, new_outfit: str) -> str:
    stripped = (line or "").strip()
    if not stripped:
        return line
    _depth = 0
    first_colon = -1
    first_pipe = stripped.find("|")
    for _k, _c in enumerate(stripped):
        if _c in "([{（":
            _depth += 1
        elif _c in ")]}）":
            _depth = max(0, _depth - 1)
        elif _c == ":" and _depth == 0:
            first_colon = _k
            break
    if first_pipe >= 0 and (first_colon < 0 or first_pipe < first_colon):
        parts = [p.strip() for p in stripped.split("|")]
        rest = ", ".join(p for p in parts[1:] if p)
        return f"{parts[0]} | {_rewrite_sheet_outfit_rest(rest, new_outfit)}"
    if first_colon >= 0:
        name = stripped[:first_colon]
        rest = stripped[first_colon + 1:].strip()
        return f"{name}: {_rewrite_sheet_outfit_rest(rest, new_outfit)}"
    return line

def apply_cast_review_outfits(char_desc: str, outfits: dict | None) -> str:
    """Write user-confirmed opening outfits back onto the character sheet."""
    mapping = {}
    for k, v in (outfits or {}).items():
        key = str(k or "").strip().lower()
        if key:
            mapping[key] = str(v if v is not None else "").strip()
    if not mapping:
        return char_desc or ""
    out_lines = []
    for line in (char_desc or "").splitlines():
        stripped = (line or "").strip()
        hit = None
        for raw_name, _rest in _iter_sheet_entries(stripped):
            canon, aliases = _split_name_aliases(raw_name)
            name = (canon or raw_name or "").strip()
            keys = [name.lower()] + [
                a.strip().lower() for a in (aliases or []) if a and str(a).strip()]
            for k in keys:
                if k in mapping:
                    hit = mapping[k]
                    break
            break
        if not hit:
            out_lines.append(line)
            continue
        out_lines.append(_apply_outfit_to_sheet_line(stripped, hit))
    return "\n".join(out_lines)
