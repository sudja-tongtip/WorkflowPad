"""
llm_engine_tools.py — shared pipeline tools main_ui.py/ui_shot_row.py use directly: workflow scanning,
Checker (run_check_prompts), Director (run_director_prompts), Vision-check, Outline chat, Insert Shot,
Regen-prompt chat helpers, and the shot-scale/prompt-format/lock-contract compatibility helpers.
A1/A2/B/Fill (vnext2) live in their own llm_engine_a1/a2/b/fill.py modules instead; Manga vNext lives
in llm_engine_manga.py; Groom lives in llm_engine_groom.py.

Extracted from llm_engine_vn.py by static reachability trace (only what main_ui.py and ui_shot_row.py
actually import) — dead/legacy code is not included.
"""

__version__ = "20260821_3310"

import asyncio, contextvars, copy, functools, json, random, time, uuid

from contextlib import contextmanager

from pathlib import Path

from .constants import LLM_DIR, DEBUG_DIR, GENRE_MAP, lang_prompt_name
from . import llm_overrides as _overrides
from .constants import dialogue_authoring_text as _dialogue_authoring_text

NSFW_GENRES = {"erotic", "dark_erotic", "manga_hentai"}

def is_nsfw(genre: str, genre2: str = "") -> bool:
    return genre in NSFW_GENRES or genre2 in NSFW_GENRES

# กฎความปลอดภัยตายตัว ใช้เสมอทุก genre/ทุกจุดที่มีการเขียน prompt — ไม่ขึ้นกับ NSFW flag เพราะต้องกันไว้เสมอ
ANIMAL_SAFETY_RULE = (
    "- ANIMALS (ABSOLUTE RULE, any genre, any NSFW setting): any animal or non-human creature in the story "
    "must NEVER be sexualized, described nude, given human sexual anatomy, placed in a sexual act, or included "
    "in any explicit/intimate scene. It stays a normal non-sexual animal (background, companion, plot element) "
    "and is never given a human/humanoid form. If a scene would require otherwise, omit the animal from that "
    "shot entirely rather than comply."
)

import re as _re_clothing

_STATE_WORD_RE = _re_clothing.compile(r'\b(NUDE|TOPLESS|BOTTOMLESS|PARTIALLY UNDRESSED|CLOTHED)\s*:', _re_clothing.IGNORECASE)

def _dedupe_state_prefix(state_str: str) -> tuple[str, bool]:
    """เจอบางครั้งที่ LLM สับสนตอนเขียนสถานะที่กำลังเปลี่ยน แล้วยัด state word ซ้ำสองครั้งในสตริงเดียว
    เช่น "CLOTHED: silver sheer skirt half — CLOTHED: dissolved" — _clothing_level() อ่านแค่ตัวแรกเงียบๆ
    ไม่ crash แต่ข้อความที่เหลือ (ground truth ของ fill stage) อ่านไม่รู้เรื่อง ตัดให้เหลือแค่ state word
    ตัวสุดท้ายเป็นต้นไป (ถือว่าคือค่าที่โมเดลตั้งใจจะจบไว้จริง) คืน (state_str ใหม่, ถูกแก้หรือไม่)"""
    matches = list(_STATE_WORD_RE.finditer(state_str))
    if len(matches) <= 1:
        return state_str, False
    return state_str[matches[-1].start():].strip(), True

# clothing_state prefixes are outline TRACKERS. Image models treat UNDRESSED / undressed as
# "draw this person nude" and drop remaining garments (top or bottom). These helpers keep
# the field unchanged and rewrite only what Fill / Check / Director may write into a prompt.
_CLOTHING_SEG_SPLIT_RE = _re_clothing.compile(r';\s*(?=[A-Za-z฀-๿])')

_CLOTHING_SEG_NAME_RE = _re_clothing.compile(r'([^—\-]+)[—\-]\s*(.*)', _re_clothing.S)

_CLOTHING_STATE_HEAD_RE = _re_clothing.compile(
    r'^(NUDE|TOPLESS|BOTTOMLESS|PARTIALLY UNDRESSED|CLOTHED)\s*:?\s*',
    _re_clothing.I,
)

_TOP_GARMENT_RE = _re_clothing.compile(
    r'\b(top|shirt|t-shirt|tee|blouse|bra|camisole|cami|tank|sweater|hoodie|'
    r'jacket|coat|vest|crop(?:\s*top)?|chemise|corset)\b',
    _re_clothing.I,
)

_BOTTOM_GARMENT_RE = _re_clothing.compile(
    r'\b(panties|panty|underwear|briefs|thong|shorts|pants|trousers|skirt|'
    r'jeans|pantyhose|tights|boxers|knickers|leggings|hose|g-string)\b',
    _re_clothing.I,
)

def _parse_clothing_segments(cs: str) -> list[tuple[str, str, str]]:
    """Parse clothing_state into (display_name, STATE or '', garments)."""
    out: list[tuple[str, str, str]] = []
    for seg in _CLOTHING_SEG_SPLIT_RE.split(cs or ""):
        seg = seg.strip()
        if not seg:
            continue
        m = _CLOTHING_SEG_NAME_RE.match(seg)
        if m:
            name = m.group(1).strip()
            rest = m.group(2).strip()
        else:
            name, rest = "", seg
        rest, _ = _dedupe_state_prefix(rest)
        hm = _CLOTHING_STATE_HEAD_RE.match(rest)
        if hm:
            state = hm.group(1).upper()
            garments = rest[hm.end():].strip(" :,—-")
        else:
            state, garments = "", rest
        out.append((name, state, garments))
    return out

def _clothing_for_image_prompt(clothing_state: str) -> str:
    """Rewrite tracker clothing_state into image-prompt-safe wording.

    The clothing_state field itself stays a tracker
    (e.g. "Name — PARTIALLY UNDRESSED: remaining top, remaining bottom").
    The prompt must name remaining garments and coverage, never the word undressed.
    """
    segs = _parse_clothing_segments(clothing_state)
    if not segs:
        return (clothing_state or "").strip()
    parts = []
    for name, state, garments in segs:
        who = name or "character"
        # ลายต้องอ่านออกว่าเป็นลาย ก่อนจะถูกเขียนลง prompt ("clouds and bamboo" → "cloud and bamboo motifs")
        g = _normalize_garment_decor((garments or "").strip())
        # ท่อนที่ไม่มีชื่อเสื้อผ้าอยู่เลย ("standing over him") ไม่ใช่ชุด — เติม wearing หน้ามันแล้ว
        # ได้ประโยคที่วาดไม่ได้ ("Circe wearing standing over him") เก็บข้อความไว้เป็นท่าทางแทน
        _act = ""
        if g and _POSE_PROSE_RE.search(g) and not (
                _GARMENT_IDENTITY_RE.search(g) or _GARMENT_PLACE_RE.search(g)
                or _BARE_STATE_RE.search(g)):
            _act, g = g, ""
        if state == "CLOTHED":
            if not g:
                # CLOTHED ที่ไม่มีชิ้นเสื้อผ้าเลย = สถานะที่ยังไม่ถูก resolve ไม่ใช่ข้อเท็จจริงที่วาดได้
                # เดิมคืน "<ชื่อ> fully clothed" ซึ่งไม่ได้บอกเครื่องวาดว่าจะวาดอะไร มันจึงเดาชุดเองคนละ
                # แบบทุกช็อต หรือวาดเป็นเปลือย — ข้ามท่อนนี้ไปเลย แล้วให้ clothing_state_unresolved()
                # เป็นตัวรายงานว่าใครยังไม่มีชุด (ไม่เดาชุดให้ ไม่แต่งเสื้อผ้าขึ้นมาเอง)
                continue
            text = f"{who} wearing {g}"
        elif state == "PARTIALLY UNDRESSED":
            # LANGUAGE-SEMANTIC-CLEANUP-A1: preserve the already-resolved garment description
            # exactly as given, in whatever language it was written — never infer which body
            # region is "still covered" from the wording (that used to be _coverage_clause(),
            # which parsed the prose for displacement words to guess coverage; only English/Thai
            # were ever recognized, and the guess could contradict what the text actually said).
            if g:
                text = f"{who} wearing {g}, remaining garments still on"
            else:
                text = f"{who} still wearing remaining garments"
        elif state == "TOPLESS":
            # GARMENT-DELTA-A1: never assert "lower body still covered" as a blanket fact — g is
            # whatever the ledger actually wrote (a true lower garment, or a one-piece garment's
            # current displaced geometry that may NOT cover the lower body at all). Let g speak for
            # itself; inventing coverage here is exactly the opposite-half fabrication this exists
            # to stop. Matches resolved_garment_phrase's already-correct TOPLESS wording below.
            if g:
                text = f"{who} bare chest, no top, still wearing {g}"
            else:
                text = f"{who} bare chest, no top"
        elif state == "BOTTOMLESS":
            # Same principle: never assert g is worn "on the upper body" — that is a topology claim
            # the ledger never made (g could equally be a one-piece garment's current lower-body
            # geometry, e.g. "dress pooled at feet", which is not an upper garment at all).
            if g:
                text = f"{who} wearing {g}, bare from the waist down"
            else:
                text = f"{who} bare from the waist down"
        elif state == "NUDE":
            text = f"{who} nude, {g}" if g else f"{who} nude"
        else:
            text = f"{who} wearing {g}" if g else who
        if _act:
            text = f"{text}, {_act}"
        parts.append(text)
    return "; ".join(parts)

# ── Garment phrase (drawable cloth only) ─────────────────────────────────────
# tracker เขียนท่อนหลังสถานะเป็นอะไรก็ได้ที่คนเขียนเห็นว่าเกี่ยวข้อง รวมทั้งท่าทางและร่างกาย
# ("Circe — CLOTHED: standing over him", "Calypso — NUDE: flowing dynamically") พอ formatter
# กลางเติมคำว่า wearing หน้าท่อนนั้นตรง ๆ จะได้ "Circe wearing standing over him" ซึ่งวาดไม่ได้
# และเป็นข้อความที่หลุดเข้า prompt มังงะจริงมาแล้ว — helper นี้คืนเฉพาะรูปทรงผ้าที่วาดได้
# ส่วนท่าทาง/ร่างกายเป็นงานของ action clause ไม่ใช่ของ "wearing ..."
_GARMENT_PLACE_RE = _re_clothing.compile(
    r'\b(?:pooled|puddled|draped|hanging|slung|tied|wrapped|bunched|pushed|pulled|'
    r'fallen|discarded|folded|clinging|soaked|torn|open|unfastened|unbuttoned|unzipped)\b'
    r'|กอง|พาด|คลุม|พัน|เปิดออก|หลุด|ขาด',
    _re_clothing.I,
)

_BARE_STATE_RE = _re_clothing.compile(
    r'\b(?:nude|naked|bare|topless|bottomless|adorned|jewell?ery|charms?|beads?|'
    r'necklace|bracelet|anklet|crown|circlet)\b|เปลือย|ประดับ',
    _re_clothing.I,
)

# ท่าทาง/การเคลื่อนไหวที่บางครั้งถูกเขียนปนมาในช่องเสื้อผ้า — สิ่งเดียวที่ helper ตัดทิ้ง
_POSE_PROSE_RE = _re_clothing.compile(
    r'\b(?:standing|stands|sitting|sits|kneeling|kneels|lying|lies|leaning|leans|'
    r'towering|looming|walking|walks|running|runs|flowing|moving|moves|reaching|'
    r'gripping|holding|facing|turning|posed|dynamically|gracefully|over (?:him|her|them))\b'
    r'|ยืน|นั่ง|คุกเข่า|นอน|เอนกาย|เดิน|วิ่ง|หัน',
    _re_clothing.I,
)

def _clothing_user_block(cs: str, *, tracker_label: str = "clothing") -> str:
    """User-message block: keep the tracker visible, tell the LLM what to write."""
    cs = (cs or "").strip()
    if not cs:
        return ""
    return (
        f"{tracker_label} (tracker labels only — do NOT paste UNDRESSED/undressed/"
        f"nude/topless into the image prompt while garments remain): {cs}\n"
        f"Outfit to render (THIS shot's clothing is canonical — name every remaining garment; "
        f"do not omit because an earlier shot already showed them; do not walk the story to change "
        f"this list): {_clothing_for_image_prompt(cs)}\n"
    )

# คำลายที่มักมาเป็นพหูพจน์ — เขียนเป็นเอกพจน์ก่อนเติมคำว่า motifs ("silver cloud and bamboo motifs")
# ใช้ตารางตรง ๆ ไม่เดาจากรูปคำ เพราะการเดาเคยได้ "red ros motifs"
_MOTIF_SINGULAR = {
    "clouds": "cloud", "dragons": "dragon", "roses": "rose", "flowers": "flower",
    "cranes": "crane", "phoenixes": "phoenix", "birds": "bird", "leaves": "leaf",
    "waves": "wave", "stars": "star", "blossoms": "blossom", "lotuses": "lotus",
    "butterflies": "butterfly", "vines": "vine", "peonies": "peony",
}

def _normalize_garment_decor(phrase: str) -> str:
    """ทำให้ลายอ่านออกว่าเป็น "ลาย" ไม่ใช่ของที่สวมอยู่ — "embroidered with silver clouds and bamboo"
    → "embroidered with silver cloud and bamboo motifs" เครื่องวาดจึงไม่วาดไผ่เป็นเสื้อผ้า
    วลีที่บอกอยู่แล้วว่าเป็น motif/pattern/print ไม่ถูกแตะ"""
    p = (phrase or "").strip()
    if not p:
        return p
    m = _re_clothing.search(
        r'\b(embroidered|printed|patterned|decorated|woven|stitched)\s+(?:with|in)\s+([^,;]+)', p,
        _re_clothing.I)
    if not m:
        return p
    tail = m.group(2).rstrip(" .")
    if _re_clothing.search(r'\b(?:motifs?|patterns?|prints?|embroidery|design|trim)\b', tail,
                           _re_clothing.I):
        return p
    # ตัดคำที่ไม่ใช่ส่วนของลาย (ชั้นถัดไปของชุด) ออกก่อนเติมคำว่า motifs
    _cut = _re_clothing.search(r'\s+(?:over|under|beneath|above|with)\s+', tail, _re_clothing.I)
    if _cut:
        tail = tail[:_cut.start()]
    tail_s = _re_clothing.sub(
        r'\b(' + "|".join(_MOTIF_SINGULAR) + r')\b',
        lambda mm: _MOTIF_SINGULAR[mm.group(1).lower()], tail, flags=_re_clothing.I)
    return p.replace(tail, f"{tail_s} motifs", 1)

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

_GARMENT_IDENTITY_RE = _re_clothing.compile(
    r'\b(?:' + "|".join(sorted(_GARMENT_NOUNS, key=len, reverse=True)) + r')\b',
    _re_clothing.I,
)

def _name_in_text(name: str, text: str) -> bool:
    if not name or not text:
        return False
    return _re_clothing.search(
        r'(?<![A-Za-z])' + _re_clothing.escape(name) + r'(?![A-Za-z])',
        text, _re_clothing.I,
    ) is not None

# คำที่บอกว่า "ในภาพนี้มีคนอยู่" โดยไม่ต้องรู้ชื่อ — ใช้เป็น guard ของ single-character fallback
# เท่านั้น ไม่ได้ใช้ตัดสิน roster ปกติ ช็อตที่เป็นวิว/ของ/ห้องเปล่าจะไม่แมตช์ ตัวละครจึงไม่ถูกยัดเข้าไป
_SCENE_PERSON_RE = _re_clothing.compile(
    r'\b(?:woman|women|man|men|girl|boy|female|male|person|people|she|her|hers|he|him|his|they|them|'
    r'figure|silhouette)\b',
    _re_clothing.I,
)

_SCENE_PERSON_TH = ("หญิง", "ผู้หญิง", "สาว", "ชาย", "ผู้ชาย", "หนุ่ม", "เธอ", "เขา", "คน", "ร่าง")

# เคสจริง: scene_desc เขียนโดยให้อวัยวะเป็นประธานประโยคล้วน ("The left mechanical arm pulls the
# leggings down, exposing smooth thighs and wet vulva.") ไม่มีคำสรรพนาม/gender noun เลยสักคำ —
# _SCENE_PERSON_RE ข้างบนจับไม่ได้ว่ามีคนอยู่ในฉาก ทั้งที่ "thighs"/"vulva" เป็นของคนแน่ๆ ผล:
# roster ว่างเปล่า → เสื้อผ้าไม่ถูกซ่อมเลยทั้งช็อต รายการนี้ไม่ผูกกับเรื่องไหน แค่คำอวัยวะทั่วไป
_SCENE_BODY_PART_RE = _re_clothing.compile(
    r'\b(?:hands?|fingers?|arms?|legs?|thighs?|hips?|waist|chest|breasts?|nipples?|shoulders?|'
    r'neck|skin|torso|abdomen|stomach|belly|knees?|ankles?|feet|foot|mouth|lips?|cheeks?|hair|'
    r'buttocks|ass|groin|vulva|labia|clitoris|penis|cock|nape|forehead|throat)\b',
    _re_clothing.I,
)

def _scene_has_person(scene_desc: str) -> bool:
    """scene_desc บรรยายว่ามีคนอยู่ในเฟรมไหม — ไม่สนว่าคนนั้นชื่ออะไร

    ภาษาไทยเช็คด้วย substring เพราะไม่มีช่องว่างคั่นคำ (_gender_noun_from_text ที่อิงช่องว่าง
    จับ 'หญิงสาวนั่งอยู่' ไม่ได้)"""
    t = scene_desc or ""
    if not t.strip():
        return False
    return (bool(_SCENE_PERSON_RE.search(t)) or any(w in t for w in _SCENE_PERSON_TH)
            or bool(_SCENE_BODY_PART_RE.search(t)))

def _roster_from_scene(scene_desc: str, char_desc: str) -> list[str]:
    """People physically in this shot: sheet names or their aliases appearing in scene_desc,
    in scene order, always returned as the canonical name so every later stage sees one identity."""
    ident = _character_identity_map(char_desc)
    scene = scene_desc or ""
    low = scene.lower()
    hits: list[tuple[int, str]] = []
    seen: set[str] = set()
    # ชื่อยาวก่อน กัน "Ta" ไปชนใน "Ta Sai" และกัน alias สั้นชนชื่อจริง
    # POV/off-screen presence ถูกกรองออกตรงนี้ ก่อนใครจะได้เห็น roster — เขามีตัวตนในเรื่องแต่ไม่มีร่างในภาพ
    _pov = {n.lower() for n in _pov_name_list(char_desc)}
    for alias in sorted(ident, key=len, reverse=True):
        canon = ident[alias]
        if canon.lower() in seen or canon.lower() in _pov:
            continue
        i = low.find(alias)
        if i >= 0:
            seen.add(canon.lower())
            hits.append((i, canon))
    hits.sort()
    if hits:
        return [n for _, n in hits]
    # เคสจริง: Pass A ตั้งชื่อว่า "Unnamed" แต่ scene_desc เขียนว่า "The woman is lounging..."
    # ไม่มี alias เชื่อมสองคำนี้ roster จึงว่าง แล้วทุกด่านหลังเงียบไปพร้อมกัน (identity ไม่ถูกซ่อม,
    # section หาย, ด่านตรวจสุดท้ายถูกข้ามเพราะมันเช็คเฉพาะเมื่อ roster ไม่ว่าง)
    # ชีตมีคนเดียว + ภาพนี้มีคนจริง = ไม่มีตัวเลือกอื่นให้ผิด — ต้องเป็นคนคนนั้น
    # GUARD: ช็อตวิว/ห้องเปล่า (_scene_has_person เป็นเท็จ) ไม่ถูกยัดคนเข้าไป
    _solo = [ident[a] for a in ident if ident[a].lower() not in _pov]
    _solo = list(dict.fromkeys(_solo))
    if len(_solo) == 1 and _scene_has_person(scene_desc):
        return _solo
    return []

def _visible_roster(scene_desc: str, char_desc: str, spacing: str = "") -> list[str]:
    """คนที่อยู่ในช็อตนี้ = ชื่อที่ scene_desc เอ่ยถึงเท่านั้น

    scene_desc เป็นเจ้าของ roster: มันบอกว่า "ใครอยู่ตรงนี้" ส่วน spacing บอกว่า "คนที่อยู่ตรงนี้
    ยืนห่างกันแค่ไหน" — spacing จึงเพิ่มหรือลบคนไม่ได้ ถ้าสองฟิลด์ขัดกัน (scene_desc มีสองคน แต่
    spacing เขียนว่า alone) ฝั่งที่ผิดคือ spacing และต้องซ่อม spacing ไม่ใช่ตัดคนออกจากภาพ
    clothing_state / prop_state / expression ก็ไม่ใช่แหล่ง roster เช่นเดิม — มันบรรยาย roster
    ที่ถูกตัดสินแล้ว พารามิเตอร์ spacing ยังรับไว้เพื่อไม่ให้ผู้เรียกเดิมพัง แต่ไม่ถูกใช้ตัดสินอีกต่อไป"""
    return _roster_from_scene(scene_desc, char_desc)

def _coerce_text_field(value, sep: str = "; ") -> str:
    """ค่าจากโมเดล → string เสมอ list เชื่อมด้วย sep, dict/อื่น ๆ เป็น JSON, None เป็นค่าว่าง
    ไม่ทิ้งข้อมูล เพราะสิ่งที่โมเดลเขียนมามักถูกต้องอยู่แล้ว แค่ห่อผิดชนิด"""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [
            (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))
            for item in value
        ]
        return sep.join(p for p in (x.strip() for x in parts) if p)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)

def _required_roster_block(scene_desc: str, char_desc: str, spacing: str = "") -> str:
    """บรรทัด roster ที่คำนวณด้วยโค้ด ส่งให้ checker เห็นเป็นรายชื่อตรง ๆ พร้อมจำนวน และรายชื่อคนที่
    "ไม่อยู่ในช็อตนี้" — เดิม checker ได้แค่ scene_desc เป็นร้อยแก้วแล้วต้องนับเอง ซึ่งมันนับพลาดประจำ
    แล้วไปหยิบคนจากชีต/เนื้อเรื่องมาเติม (เคสจริง: รายงานว่า "Missing Pha-na" ทั้งที่ scene_desc มีสองคน)
    ว่างเมื่ออ่านชีตไม่ออก — ปล่อยให้ใช้กฎเดิม ดีกว่าใส่ roster ผิด ๆ ลงไป"""
    roster = _visible_roster(scene_desc, char_desc, spacing)
    if not roster:
        return ""
    _pov = _pov_name_list(char_desc)
    _pov_low = {n.lower() for n in _pov}
    others = [n for n in _sheet_name_list(char_desc)
              if n not in roster and n.lower() not in _pov_low]
    out = (f"REQUIRED VISIBLE ROSTER (computed from scene_desc + spacing — EXACTLY "
           f"{len(roster)} {'person' if len(roster) == 1 else 'people'}, this is the complete list "
           f"of who is in this shot): {', '.join(roster)}\n")
    if others:
        out += (f"NOT IN THIS SHOT (on the character sheet but not in this beat — the sheet is "
                f"identity only, never a roster; do NOT add them and do NOT report them as missing, "
                f"whatever clothing_state, prop_state or the full story say): {', '.join(others)}\n")
    if _pov:
        out += (f"POV / OFF-SCREEN PRESENCE (in the story, never in the picture — the camera stands "
                f"where they stand): {', '.join(_pov)}. They have no body, no face and no clothes to "
                f"draw. Never report them as missing from this shot and never add them; a look, a line "
                f"or a gesture aimed at them is aimed at the camera/viewer instead.\n")
    return out

# CHECKER-C1: deterministic containment run BEFORE the Checker LLM call. It only DETECTS the two
# conflict classes already proven live (cast/spacing, clothing mid-transition) — it never resolves
# them. Local (weak model) was observed guessing a third-version compromise through these
# contradictions even with strong "never guess" prompt wording; code-level containment removes the
# LLM's chance to guess at all for the conflicted shot, rather than trying to word the prompt harder.
_CANONICAL_SOLO_RE = _re_clothing.compile(
    r'\b(alone|solo|no one else|not yet (?:present|arrived|here|in (?:the\s+)?(?:frame|shot|scene)))\b',
    _re_clothing.I,
)

_CANONICAL_MID_TRANSITION_RE = _re_clothing.compile(
    r'\b(?:still\s+)?(?:sliding|slides|slid|pulling|pulls|pulled|peeling|peels|peeled|'
    r'slipping|slips|slipped|lowering|lowers|lowered)\s+(?:off|down|from|over)\b'
    r'|\bhalfway\s+(?:removed|off|down)\b'
    r'|\bstill\s+(?:hanging|halfway)\b'
    r'|\bbeing\s+(?:pulled|lowered|removed|slid|peeled|slipped)\b',
    _re_clothing.I,
)

_CANONICAL_GARMENT_WORD_RE = _re_clothing.compile(
    r'\b(shirt|blouse|top|t-shirt|tee|bra|camisole|tank|sweater|hoodie|jacket|coat|vest|corset|'
    r'dress|robe|kimono|gown|jumpsuit|swimsuit|bikini|'
    r'panties|underwear|briefs|thong|shorts|pants|trousers|skirt|jeans|tights|boxers|leggings)\b',
    _re_clothing.I,
)

def _precheck_canonical_conflicts(scene_desc: str, clothing_state: str, spacing: str,
                                   char_desc: str) -> list[dict]:
    """Code-level DETECT-only pass over canonical fields for this shot. Never invents which side is
    right — a hit here means the fields cannot both describe the same frozen instant, so the caller
    must contain the shot (skip semantic rewrite, preserve Current Prompt, report) rather than let
    the Checker LLM guess a compromise. Deliberately narrow: only the two conflict classes already
    proven live, only on unambiguous wording, and it never touches Outline/Fill/canonical fields."""
    conflicts: list[dict] = []
    scene = scene_desc or ""

    # A) CAST / SPACING — scene_desc's roster is >=2 people while spacing explicitly declares solo.
    # Absence of NEAR/APART/FAR, or a plain "NEAR"/"FAR" token, is NOT a conflict — only an explicit
    # solo/not-yet-present phrase counts, per spec (missing data != contradiction).
    roster = _roster_from_scene(scene, char_desc)
    if len(roster) >= 2 and _CANONICAL_SOLO_RE.search(spacing or ""):
        conflicts.append({
            "code": "CANONICAL_CAST_CONFLICT",
            "fields": ["scene_desc", "spacing"],
            "detail": (f"scene_desc names {len(roster)} people ({', '.join(roster)}) but spacing "
                       f"explicitly says solo/not yet present"),
        })

    # B) CLOTHING TRANSITION — scene_desc names a garment still mid-removal in this frozen instant,
    # while clothing_state's resolved state already claims that garment category is gone.
    cs = clothing_state or ""
    segs = _parse_clothing_segments(cs)
    state_words = {s.upper() for _, s, _ in segs if s}
    cs_low = cs.lower()
    for m in _CANONICAL_MID_TRANSITION_RE.finditer(scene):
        window = scene[max(0, m.start() - 40): m.end() + 40]
        gm = _CANONICAL_GARMENT_WORD_RE.search(window)
        if not gm:
            continue
        garment = gm.group(1).lower()
        is_top = bool(_TOP_GARMENT_RE.search(garment))
        is_bottom = bool(_BOTTOM_GARMENT_RE.search(garment))
        state_conflict = (
            (is_top and state_words & {"TOPLESS", "NUDE"}) or
            (is_bottom and state_words & {"BOTTOMLESS", "NUDE"})
        )
        name_conflict = (
            "CLOTHED" in state_words and garment not in cs_low
            and not (is_top or is_bottom)  # only the distinct-garment case (e.g. "coat"), not
        )                                  # generic top/bottom words already covered above
        if state_conflict or name_conflict:
            conflicts.append({
                "code": "CANONICAL_CLOTHING_CONFLICT",
                "fields": ["scene_desc", "clothing_state"],
                "detail": (f"scene_desc shows '{garment}' mid-transition but clothing_state "
                           f"({cs.strip()[:60]}) does not carry it as still present"),
            })
            break  # one clothing conflict is enough signal for containment
    return conflicts

def _prop_user_block(ps: str) -> str:
    ps = (ps or "").strip()
    if not ps:
        return ""
    return (
        "objects (GROUND TRUTH for objects in THIS shot — owner, hand, "
        "held vs placed; repair the prompt to match; never move a prop for a "
        f"prettier picture): {ps}\n"
    )

# Page mode = เลย์เอาต์ ไม่ใช่สไตล์การวาด — Krea2 อ่านคำว่า "manga" เป็นคำสั่งเรนเดอร์ (เส้นหมึก
# ขาวดำ การ์ตูน) แล้วทิ้งสไตล์จริงของโปรเจกต์ไปทั้งภาพ ชื่อโหมดใน UI ยังเป็น "Manga Page" ได้
# แต่คำนี้ต้องไม่ไหลลง prompt ที่ส่งให้โมเดลวาด สไตล์การวาดมาจาก Image Style ที่ผู้ใช้เลือกเท่านั้น
# (ถ้าผู้ใช้เลือกสไตล์ manga/anime เอง คำนั้นมาจาก STYLE_PRESETS ไม่ใช่จากเลย์เอาต์ตรงนี้)
_PAGE_STYLE_NEUTRAL_RULE = (
    "PAGE VOCABULARY: the page layout is not a drawing style. Never write \"manga\", \"manga page\", "
    "\"manga panel\", \"manga composition\", \"anime\", \"cartoon\", \"comic drawing\", \"cel shading\" "
    "or \"line art\" into the prompt. Use neutral layout words only: N-panel page, multi-panel page, "
    "panel borders, panel region, dominant panel, narrow panel, horizontal panel, diagonal panel, "
    "upper-left region. Rendering style comes from the project's image style, never from the fact "
    "that this is a page."
)

def _framing_digest(prompt: str, words: int = 30) -> str:
    """ดึงเฉพาะช่วงเปิดของ prompt (กล้อง+แสง) มาเป็น context ให้ Director —
    ไม่ส่ง prompt เต็ม เพราะจะกลายเป็นต้นฉบับให้ LLM ก็อปวางทั้งก้อน"""
    head = " ".join((prompt or "").replace("\\n", " ").split()[:words]).strip()
    return head + ("..." if head else "(none)")

# กฎกลาง ใช้ร่วมทุก style: คำผ้าบางเดี่ยวๆ มักถูก image model ตีเป็น nude ทั้งที่ state ยัง CLOTHED
# คำพวกนี้เป็นคำเสี่ยง ไม่ใช่คำแบน — ใช้ได้เมื่อเรื่องเขียนชัด ไม่งั้นเปลี่ยนเป็น opaque/solid
_SHEER_RISK_WORDS = (
    "sheer, translucent, see-through, chiffon, organza, gauze, mesh, thin lace"
)

_SHEER_COVERAGE_RULE = (
    "- SHEER/THIN-FABRIC RULE (CRITICAL): risk words (not a ban): "
    f"{_SHEER_RISK_WORDS}. Whenever clothing_state is CLOTHED (or PARTIALLY UNDRESSED that is still "
    "meant to read as covered) — never NUDE/TOPLESS — do not leave those words unqualified. An image "
    "model reads a bare mention as an invitation to render nudity. If the story does NOT explicitly "
    "call for thin/see-through fabric, switch to opaque/solid/\"fabric intact\" wording. If the story "
    "DOES call for it, keep the word and state exactly where the fabric still covers the body in the "
    "same breath. Do not invent thicker fabric than the story needs.\n"
    "  GOOD: \"CLOTHED: sheer silver skirt wrapped around her hips and thighs, fabric still fully covering "
    "her torso, breasts, and legs\"\n"
    "  GOOD (story never asked for sheer): \"CLOTHED: opaque silver skirt, fabric intact, covering hips and legs\"\n"
    "  BAD (unqualified risk word, reads as nude): \"sheer silver skirt glowing in moonlight\"\n"
    "  If clothing_state is CLOTHED, the prompt overall must never read as nudity — no bare-skin emphasis."
)

# ใช้เฉพาะตอน CLOTHED — ชุดไม่ครบ/เปิดไหล่โดยไม่ยืนยัน coverage ทำให้โมเดลภาพวาดโป๊
_CLOTHED_COMPLETENESS_RULE = (
    "- CLOTHED OUTFIT COMPLETENESS (CRITICAL — CLOTHED only; never rewrite PARTIALLY UNDRESSED/"
    "TOPLESS/NUDE): a CLOTHED character must read as wearing a complete outfit. Name both upper and "
    "lower coverage, OR one garment that covers the whole body (dress, kimono, robe, jumpsuit, "
    "one-piece swimsuit). FORBIDDEN while CLOTHED: only a blouse/top/off-shoulder piece with no "
    "lower-body garment named. Off-shoulder or open neckline is allowed only if the SAME phrase "
    "states breasts and torso remain fully covered. Write this the same way in clothing_state and "
    "in any outfit sentence in scene_desc/prompt so they do not disagree. Do not change pose, mood, "
    "expression, or camera to satisfy this rule."
)

_PROMPT_CLOTHING_WORDING_RULE = (
    "- CLOTHING TRACKER vs IMAGE PROMPT (CRITICAL): CLOTHED / PARTIALLY UNDRESSED / TOPLESS / NUDE "
    "are outline TRACKER LABELS in the clothing_state field. NEVER paste those labels — or the words "
    "undressed, partially undressed, half-naked, stripped naked, disrobed — into the image prompt "
    "while any garment is still listed. An image model reads UNDRESSED as nude and drops the top "
    "or the bottom even when the field names remaining clothes.\n"
    "  Write the GARMENT LIST as still worn, plus what those garments still cover.\n"
    "  * CLOTHED → name every garment as worn. Never write undressed/nude/topless.\n"
    "  * PARTIALLY UNDRESSED → name every remaining garment as STILL WORN and state coverage "
    "(breasts/torso if a top remains; hips/crotch if panties/bottoms remain). FORBIDDEN in the "
    "prompt while any garment remains: undressed, partially undressed, half-naked, nude, topless, "
    "naked, stripped.\n"
    "    GOOD: \"Name wearing the remaining top and remaining bottom, remaining garments still on, "
    "breasts covered by the top, hips and crotch covered by the bottom\"\n"
    "    BAD: \"Name — PARTIALLY UNDRESSED: remaining top, remaining bottom\" "
    "(UNDRESSED makes the model go nude)\n"
    "  * TOPLESS → write \"bare chest, no top\" AND name every remaining lower garment as still worn. "
    "Do not let topless erase the bottom.\n"
    "  * NUDE → only when clothing_state is NUDE with no remaining garments. Then nude is correct.\n"
    "  Matching the garment list is enough. Do not require the prompt to contain the tracker prefix."
)

_CAMERA_VARIETY_RULE = (
    "- CAMERA VARIETY RULE (CRITICAL): do NOT reuse the same framing/angle/composition for more than 2 "
    "consecutive shots — the CAMERA ALREADY USED lines above are the angles you just wrote; choose "
    "something different unless the story genuinely holds a locked frame (e.g. a static two-shot dialogue "
    "exchange). FORBIDDEN: defaulting to the same \"extremely wide shot, subject on the left third, vast "
    "negative space\" block copy-pasted across many shots — this reads as repetitive and hides the story.\n"
    "  Vary BOTH distance and angle across the sequence, guided by story position: opening/establishing shots "
    "→ wide; mid-story shots where detail matters (an outfit change, a gesture, an object) → medium or close-up; "
    "emotionally key or climactic shots → close-up on face, hands, or a specific meaningful detail. Also vary "
    "height (low/eye-level/high) and composition (centered, over-shoulder, Dutch angle) when the mood calls "
    "for it — don't let every shot default to the same safe wide/negative-space choice."
)

# ผลทดสอบจริง (5 ชุด seed เดียวกัน): การเติมรายละเอียดฉากให้ลึกขึ้น "ไม่" ทำให้ได้ระยะกว้าง
# แต่การ "ลบ" คำที่มองเห็นได้เฉพาะระยะใกล้ออก ทำให้ได้ทันที — คำเดียวชนะการบรรยายฉากทั้งย่อหน้า
# เพราะ image model ต้องซูมเข้าไปเพื่อทำให้สิ่งนั้นมองเห็นได้ ทับคำสั่งกล้องที่เขียนไว้
_VISIBILITY_GATE_RULE = (
    "- VISIBILITY GATE (CRITICAL — overrides every other rule about what to include): before writing "
    "ANY detail, ask whether it is resolvable at this shot's assigned SHOT SCALE. At \"wide\" and "
    "\"extreme wide\", the following are NOT resolvable and must not appear ANYWHERE in the prompt — "
    "not in a Subject section, not in Lighting, not in Climax, not in Environment, not as a tag, not "
    "in a narrative sentence: eyes or eye colour as a visible feature, gaze direction or what someone "
    "is looking at in detail, lips or mouth shape, individual fingers or hand placement on small "
    "objects, skin texture or skin tone as a lit surface, fabric weave or garment texture, small marks "
    "stains or patterns on an object, and any light described as falling on skin or on a face. "
    "At \"full body\", avoid the finest of these (skin texture, fabric weave, small marks) but posture "
    "and general gesture are fine. At \"medium\" and \"close-up\" all of them are correct and expected.\n"
    "  Naming an unresolvable detail forces the model to zoom in to show it, which overrides the camera "
    "framing you asked for. ONE such word outweighs an entire paragraph of landscape description — this "
    "was measured, not assumed.\n"
    "  Write the equivalent at the right scale instead: \"head bowed over something held at her chest\" "
    "not \"eyes fixed on the glue mark, lips parted, fingers steady on the rim\"; \"warm rim light along "
    "the roof edge and the reeds\" not \"gentle shadows on pale skin\".\n"
    "  Also NOT resolvable at \"wide\" / \"extreme wide\": the placement of a hand or limb on ANY surface "
    "or object (not just small ones) — \"right hand pushing on the ground\" is out; any small handheld "
    "object being manipulated (a pipe, a hook, a latch, a cup) — name it only as part of the silhouette, "
    "never as an action performed on it; the CONDITION of a garment (torn, frayed, ragged, fluttering) as "
    "opposed to its colour and shape; and small ground detail (footprints, cracks, pebbles, individual "
    "blades of grass).\n"
    "  Write the body as a SILHOUETTE at these distances: where the figure is in the scene, which way "
    "it faces, and WHAT THE WHOLE BODY IS DOING in THIS beat — the same action as scene_desc, not a "
    "default upright stand. Idle standing is only for a genuinely still beat. Nothing "
    "about what any single body part is doing.\n"
    "  WHEN THE MAIN ACTION IS A HAND ACTION (tearing, feeding a sheet, hanging something on a rack, "
    "gripping, pushing off the ground): do NOT drop the action — restate it at silhouette level so the "
    "story beat survives while the hand does not. \"hunched over the work\" for a close hand task; "
    "\"reaching up\" for hanging; \"crouched low\" for lifting; "
    "\"one arm extended toward the other person\" for offering something. The viewer at this distance sees a body shape performing "
    "an action — write that shape. Never write which hand, never write what the hand is touching.\n"
    "  SUBJECT-MATTER EXCEPTION: the gate assumes body detail is decoration. When the body IS the subject "
    "matter of the shot — the story beat is physical contact, pain, injury, treatment, craft performed on "
    "a body, or an object held and worked on — one such detail may stay at \"full body\", chosen as the "
    "SINGLE most important physical tell of that beat (a clenched jaw, a hand gripping fabric, a tendon "
    "standing out). Keep exactly one; it carries the beat. This exception does NOT apply at \"wide\" or "
    "\"extreme wide\" — if the beat needs the body detail to work, the shot should not have been assigned "
    "those distances in the first place (see the \"chamber\" preset). And it never licenses a list: one "
    "tell, not three.\n"
    "  MOUTH AND SMILE AT FULL BODY: an open mouth, visible teeth, a broad smile, or a head thrown back "
    "reads clearly at \"full body\" — these are silhouette-scale, not micro-expression. Allow them there. "
    "Only the fine shape of the lips and the subtle set of the brow stay gated at that distance. At "
    "\"wide\" and \"extreme wide\" the face remains out of reach as before — if a beat depends on a smile "
    "being seen, that beat should not have been assigned those distances."
)

# ── ชีตเป็นภาษาของเรื่อง แต่ prompt เป็นอังกฤษ: สั่ง "copy verbatim" จึงเป็นคำสั่งที่ทำตามไม่ได้ ──
# โมเดลมีทางเลือกแค่ก๊อปจริง (ได้ "Not specified age, Thai ไทย man", "ถ่านเรืองแสง" กลาง prompt
# อังกฤษ) หรือไม่ก๊อปแล้วทิ้งข้อมูลไปเลย (สัญชาติหายทั้งเรื่อง ตัวละครเปลี่ยนเชื้อชาติข้ามช็อต)
# แทนที่ด้วยสามชั้น: บอกว่าต้องมี "อะไร" (checklist) → ให้แปลเป็นอังกฤษเอง (render) → แล้วล็อก
# คำอังกฤษของตัวเองไว้ใช้ซ้ำ (stabilize) ซึ่งเป็น verbatim ชนิดเดียวที่ไม่ข้ามภาษา
# แทนที่จะแก้ทีละเคส (สีเสื้อเพี้ยน → กฎสี, วัสดุเพี้ยน → กฎวัสดุ, ของเปลี่ยนมือ → กฎ prop, ...)
# ที่จริงเป็นปัญหาเดียวกันหมด: Fill ไม่ได้ปฏิบัติต่อ state ที่ resolve มาแล้วว่าเป็น authority
# ก้อนเดียวนี้ประกาศว่าอะไรคือ canonical ของช็อตนี้ ให้โมเดล reasoning เอาเองว่า "เปลี่ยนความหมาย"
# คืออะไร — ไม่ต้องมีลิสต์ปิดแยกทุกแอตทริบิวต์ (สี/วัสดุ/ลาย/แขนเสื้อ/กระดุม/รองเท้า/เครื่องประดับ)
_CANONICAL_STATE_RULE = (
    "- CANONICAL STATE: Character Sheet defines persistent identity. clothing_state defines "
    "current clothing. prop_state defines current props. spacing defines current spatial "
    "relationships. shot_scale defines framing. Preserve these facts IN MEANING — wording may "
    "vary, but never invent, remove, replace, or change what they state. If clothing_state says "
    "\"black leather jacket\", writing it as a red jacket or a cotton jacket changes canonical "
    "state just as surely as dropping the jacket entirely — this covers every attribute a field "
    "states, not only the ones with their own rule below. scene_desc defines the current drawable "
    "action/beat — it decides what is happening, never what the body, clothes, props or camera are.\n"
    "  THE CHARACTER SHEET ALSO CARRIES PLANNING METADATA — personality flaw, desire, fear, "
    "speaking style — that exists to help you judge HOW someone reacts, never as text to place in "
    "the image prompt. Translate it into a visible physical result (a flaw of recklessness becomes "
    "a specific reckless posture or expression) and never copy the flaw/desire/fear phrase itself.\n"
    "  DRAW ONLY WHAT THE STATE GIVES YOU: a piece of furniture, a mirror, a light source, an "
    "object, or a gesture that none of place/env/prop_state/scene_desc actually supplies is an "
    "invention, not an enhancement — leave it out rather than add texture the shot never asked for."
)

# CHECKER-B1: generalizes the clothing-only contradiction pattern that already existed (a state word
# fighting its own garment list) into every canonical field pair — added because scene_desc vs
# spacing/cast conflicts had no equivalent gate at all, so Checker would guess through them by
# picking whichever field it trusted more and silently rewriting the prompt around that guess.
_CANONICAL_CONFLICT_GATE_RULE = (
    "- CANONICAL CONFLICT GATE — run this BEFORE judging the prompt: read scene_desc, clothing_state, "
    "prop_state, spacing, expression, and place/env together and check whether they can all describe "
    "the SAME frozen instant. Conflict examples: scene_desc shows a garment mid-removal while "
    "clothing_state already lists it fully absent (or the reverse); scene_desc shows a second person "
    "visibly present/approaching while spacing or the roster says they are not yet here (or the "
    "reverse); expression names a reaction scene_desc contradicts; place/env names somewhere scene_desc "
    "does not support.\n"
    "  IF THE FIELDS CONFLICT: report it — [OUTLINE STATE CONTRADICTION] or [CANONICAL FIELD CONFLICT] "
    "— name the conflicting fields in one short phrase, and STOP there for that fact. Do NOT invent a "
    "compromise, do NOT silently pick one field over the other, do NOT consult the Full Story to decide "
    "which one is \"really\" true, and do NOT rewrite the prompt into a third, guessed version. The "
    "conflict is an upstream canonical shot-data defect, not yours to resolve — preserve and report it. "
    "Leave the Current Prompt as-is for that fact unless "
    "it has a separate, independent error you can repair on its own terms.\n"
    "  IF THE FIELDS ARE COMPATIBLE: proceed to the normal prompt-vs-canonical checks below as usual."
)

# CHECKER-VNEXT-NATIVE-CANONICAL-RULE-A1: Normal vNext's own shot dict never had clothing_state/
# prop_state/spacing/expression/place/scene_desc/shot_scale to begin with (it carries shot_desc,
# clothing, objects, location, camera, visible_people, lighting) — injecting the legacy-vocabulary
# _CANONICAL_STATE_RULE/_CANONICAL_CONFLICT_GATE_RULE above into a Normal-vNext Checker prompt talks
# about fields that do not exist in the data Checker is actually looking at. Deliberately NOT a
# rewrite of the shared constants above — the old manga_page pipeline's shot dicts still use the
# legacy field names (scene_desc/clothing_state/... — see _roster_from_scene, _visible_roster and
# friends) and still need the legacy wording. These VNEXT variants are selected only at the
# Normal-vNext Checker injection site (_ck_v3 and not manga_page), leaving the legacy path byte-for-
# byte untouched. "Spacing" is deliberately never named as if it were its own canonical field here —
# per CHECKER-VNEXT-SPACING-NOT-A-FIELD-A1, spatial relationship is canonical only insofar as
# shot_desc (or the Current Prompt/source truth) actually establishes it.
_CANONICAL_STATE_RULE_VNEXT = (
    "- CANONICAL STATE: Character Sheet defines persistent identity. clothing defines "
    "current clothing. objects defines current props/objects. location defines the current place. "
    "camera defines framing. Preserve these facts IN MEANING — wording may "
    "vary, but never invent, remove, replace, or change what they state. If clothing says "
    "\"black leather jacket\", writing it as a red jacket or a cotton jacket changes canonical "
    "state just as surely as dropping the jacket entirely — this covers every attribute a field "
    "states, not only the ones with their own rule below. shot_desc defines the current drawable "
    "action/beat — including any spatial relationship it establishes between people or things — it "
    "decides what is happening, never what the body, clothes, objects or camera are.\n"
    "  THE CHARACTER SHEET ALSO CARRIES PLANNING METADATA — personality flaw, desire, fear, "
    "speaking style — that exists to help you judge HOW someone reacts, never as text to place in "
    "the image prompt. Translate it into a visible physical result (a flaw of recklessness becomes "
    "a specific reckless posture or expression) and never copy the flaw/desire/fear phrase itself.\n"
    "  DRAW ONLY WHAT THE STATE GIVES YOU: a piece of furniture, a mirror, a light source, an "
    "object, or a gesture that none of location/objects/shot_desc actually supplies is an "
    "invention, not an enhancement — leave it out rather than add texture the shot never asked for."
)

_CANONICAL_CONFLICT_GATE_RULE_VNEXT = (
    "- CANONICAL CONFLICT GATE — run this BEFORE judging the prompt: read shot_desc, clothing, "
    "objects, location, camera, visible_people and lighting together and check whether they can all "
    "describe the SAME frozen instant. Conflict examples: shot_desc shows a garment mid-removal while "
    "clothing already lists it fully absent (or the reverse); shot_desc shows a second person "
    "visibly present/approaching while visible_people or the roster says they are not yet here (or the "
    "reverse); location names somewhere shot_desc "
    "does not support.\n"
    "  IF THE FIELDS CONFLICT: report it — [OUTLINE STATE CONTRADICTION] or [CANONICAL FIELD CONFLICT] "
    "— name the conflicting fields in one short phrase, and STOP there for that fact. Do NOT invent a "
    "compromise, do NOT silently pick one field over the other, do NOT consult the Full Story to decide "
    "which one is \"really\" true, and do NOT rewrite the prompt into a third, guessed version. The "
    "conflict is an upstream canonical shot-data defect, not yours to resolve — preserve and report it. "
    "Leave the Current Prompt as-is for that fact unless "
    "it has a separate, independent error you can repair on its own terms.\n"
    "  IF THE FIELDS ARE COMPATIBLE: proceed to the normal prompt-vs-canonical checks below as usual."
)

# ── มือเกิน: ชีตบอกว่าร่างนี้มีอะไร แต่ไม่มีด่านไหนถามว่า "อวัยวะนั้นว่างอยู่หรือเปล่า" ──
# เคสจริง: มือหนึ่งถือแก้ว อีกมือถือบุหรี่ แล้วประโยคถัดมาเขียน "her fingers" ทำอย่างที่สาม
# ไวยากรณ์ถูกทุกอย่าง แต่ image model อ่านว่าต้องมีมือที่สาม แล้ววาดให้จริง ๆ
# ความผิดอยู่ที่ต้นทาง 100% — prompt สั่งวาดสามมือเอง ไม่ใช่โมเดลวาดพลาด
# ตรรกะเดียวกับ ONE PERSON, ONE BLOCK, ONE BODY ย่อส่วนลงมาที่ระดับแขนขา:
# การบรรยายซ้ำโดยไม่บอกว่าเป็นของเดิม = ร่างใหม่ / อวัยวะใหม่
_ANATOMY_INTERACTION_RULE = (
    "- PHYSICAL CONSISTENCY:\n"
    "  Canonical anatomy is fixed — never invent, remove, duplicate, normalize, or replace a limb "
    "or body trait: mechanical stays mechanical, missing stays missing, and a four-armed character "
    "may use all four at once — judge against what THIS body has, never a generic human default.\n"
    "  Resolve each shot as ONE physically possible frozen moment: if the actions asked for conflict "
    "for the anatomy available, simplify or stage them — never invent another limb to fit them all.\n"
    "  Body parts, prosthetics, wings, tails and the like remain part of their owner — never a "
    "separate entity in the roster, cast list or spacing.\n"
    "  prop_state is authoritative for what is held, by whom, and where — do not soften, relocate "
    "or drop a prop it names because scene_desc stays silent about it this beat."
)

_IDENTITY_LOCK_CHECKER_RULE = (
    "- IDENTITY LOCK BLOCK (CRITICAL): the prompt must contain a labeled IDENTITY LOCK for every "
    "person this shot actually renders. Validate each entry against Outline / current canonical state: "
    "correct character, sex, core appearance, CURRENT clothing_state (not the Character Sheet default "
    "outfit), garments on the correct owner. Intended rendered people must not be missing from the "
    "lock; the lock must not introduce an off-frame person.\n"
    "  If a lock line is wrong, CORRECT it in place against Outline. If already correct, preserve it "
    "verbatim.\n"
    "  NEVER delete IDENTITY LOCK. Never summarize it away. Never replace it with ordinary prose. "
    "Never treat it as redundant verbosity. Never invent identity or clothing. Never restore the "
    "default outfit over current clothing. Never add unrelated characters.\n"
    "  Preserve the selected prompt format: Narrative stays Narrative, Comma stays Comma, Sectioned "
    "stays Sectioned, Manga stays Manga. Correct lock CONTENT only — do not redesign the prompt."
)

_MINIMAL_V3_CHECKER_IDENTITY_RULE = (
    "- IDENTITY (Minimal V3 — CRITICAL): this prompt was written under the Minimal V3 contract, "
    "where an IDENTITY LOCK is REQUIRED whenever a named character is visible — solo shots "
    "included.\n"
    "  The lock is one line per visible person: Name = WOMAN|MAN, age when known, short permanent "
    "look, any silhouette-changing body fact (mechanical arms, prosthetic, wings, tail, horns), "
    "then a short current-outfit anchor. No action or expression inside it.\n"
    "  NEVER DELETE THE LOCK and never summarize it away into prose. If a line is wrong, correct "
    "it against the Outline in place; if it is right, preserve it word for word. If the lock is "
    "missing entirely and a named character is visible, add it. This applies at every shot_scale, "
    "extreme wide included — the lock is the continuity authority regardless of distance.\n"
    "  It appears ONCE: once per prompt, or once per page in page mode — never repeated per panel "
    "and never duplicated as a second block.\n"
    "  Clothing appears once, separately from identity. Do not duplicate clothing.\n"
    "  Never invent identity or clothing. Never restore the Character Sheet default outfit when a "
    "current clothing state exists. Never add unrelated characters.\n"
    "  A permanent body fact never disappears because the shot is wide — reduce its detail, never "
    "the fact itself.\n"
    "  Preserve the selected prompt format. Do not convert tag_style into prose or labeled "
    "sections. CLIMAX is optional in this contract: never add one, and never demand one."
)

_IDENTITY_LOCK_DIRECTOR_RULE = (
    "- IDENTITY LOCK IS READ-ONLY: preserve the IDENTITY LOCK block verbatim. Do not delete, rewrite, "
    "summarize, shorten, change sex, change appearance, change clothing, swap clothing between people, "
    "add or remove characters, restore a default outfit, or reinterpret identities for artistic reasons. "
    "You may READ it as context for camera, composition, staging, and spatial arrangement. If something "
    "inside the lock looks wrong, do not repair it — identity validation belongs to Checker against Outline."
)

# LOCATION LOCK downstream ownership (Minimal V3 only) — same pattern as IDENTITY LOCK above:
# Fill authors it, Checker verifies/corrects against place/env/canonical_location, Editor/Director
# preserve it read-only. Camera/composition/scale may still change freely around it.
_MINIMAL_V3_CHECKER_LOCATION_RULE = (
    "- LOCATION (Minimal V3 — CRITICAL): this prompt was written under the Minimal V3 contract, "
    "where a LOCATION LOCK is REQUIRED exactly once (once per page in page mode).\n"
    "  It names only WHERE the image currently is, from this shot's place/env/canonical_location "
    "under their existing CURRENT-LOCATION AUTHORITY — never a previous, future, or background "
    "place env also happens to mention. Normally one compact line; a supplied place identity is "
    "never collapsed to only its surface (a market on sand stays the market, not just \"sand\").\n"
    "  NEVER DELETE THE LOCK and never summarize it away into prose or fold it into Environment. "
    "If it names the wrong place, correct it against place/env/canonical_location in place; if it "
    "is already correct, preserve it word for word. If it is missing entirely, add it. If it "
    "appears more than once, keep only one.\n"
    "  Never invent architecture, weather, light, or population into it. Never move it away from "
    "immediately after IDENTITY LOCK.\n"
    "  Preserve the selected prompt format. A close-up or wide shot keeps the same compact lock — "
    "scale never changes whether it is present or what it says."
)

_LOCATION_LOCK_DIRECTOR_RULE = (
    "- LOCATION LOCK IS READ-ONLY: preserve the LOCATION LOCK block verbatim. Do not delete, "
    "rewrite, shorten, expand, or move it, and never change its stated place because the camera, "
    "composition, or shot scale changes. You may READ it as context for staging and lighting. If "
    "it looks wrong, do not repair it — location validation belongs to Checker against place/env/ "
    "canonical_location. Zoom, framing, and composition remain fully yours to change; the lock's "
    "own content is the only thing off-limits."
)

# OBJECT LOCK downstream ownership (vNext lock-contract engines only) — same three-role pattern as
# IDENTITY LOCK / LOCATION LOCK above: Fill authors it (permanent physical identity only, never
# current state), Checker may narrowly repair it against canonical props evidence, Director
# preserves it verbatim as pure read-only context. It is OPTIONAL — present exactly once only when
# this shot's own current objects actually call for it, absent entirely otherwise.
_MINIMAL_V3_CHECKER_OBJECT_RULE = (
    "- OBJECT (vNext lock contract — narrow authority): whenever this shot's own props/objects data "
    "names a persistent physical object that is actually present in THIS shot, an OBJECT LOCK block "
    "is required exactly once, immediately after LOCATION LOCK: one line per present object, "
    "<canonical name> = permanent class/type, stable material, stable color, stable shape/design, "
    "stable size only when the source establishes one. This is PERMANENT identity — it must never "
    "contain current state (parked/moving, open/closed right now, wet, damaged right now, who is "
    "holding it, current location, current owner, speed, orientation); that belongs in the shot body.\n"
    "  Your authority over OBJECT LOCK is narrow but covers two distinct repairs, both anchored to "
    "your own canonical props evidence, never to your own guess: (1) TYPE/MATERIAL/COLOR DRIFT — "
    "the props sheet says \"blue compact hatchback\" and the lock says \"black SUV\" — repair the "
    "type/color/material to match canonical evidence. (2) STATE CONTAMINATION — the lock contains "
    "wording that describes what is happening to the object right now (parked/moving, open/closed, "
    "wet, damaged right now, held, current location/owner, speed, orientation) instead of only its "
    "permanent identity — strip that wording back out so the lock states ONLY the permanent identity "
    "your canonical props evidence supports, and let the current-state fact live in the shot body "
    "instead (it almost always already does, since Fill's body describes the same object's current "
    "state independently of the lock). Both repairs are the SAME authority — matching the lock's "
    "content to canonical permanent identity — applied to two different kinds of drift. You may NOT "
    "redesign it, upgrade its specificity beyond canonical evidence, or invent a brand/model the "
    "source never established. A genuinely PERMANENT fact stays even if it superficially resembles "
    "a state word — e.g. canonical evidence describing a missing part as part of the object's fixed "
    "design, not a recent event — judge that from your own canonical evidence, never by banning "
    "particular words. If OBJECT LOCK is already clean (permanent identity only, canonical-accurate), "
    "preserve it verbatim.\n"
    "  NEVER make an object appear in the lock that this shot's own current objects/shot_desc does "
    "not itself contain — the props/world sheet alone proves what an object looks like, never that "
    "it is present in this particular shot. If this shot has no current physical object at all, the "
    "block must be absent entirely — never write \"OBJECT LOCK:\" with no content, and never write "
    "the word \"None\".\n"
    "  Multiple present objects stay inside the SAME single OBJECT LOCK block as separate lines — "
    "never split into more than one OBJECT LOCK block, and never merge two distinct objects into one "
    "hybrid line."
)

# PATCH3-CHECKER-OBJECT-LOCK-SURGICAL-REPAIR-A1: minimal addendum to the OBJECT LOCK repair rule
# above — tells the model WHEN and HOW to declare its repair as scoped to OBJECT LOCK only, so
# Python can splice just that block into the original prompt instead of accepting a whole rewritten
# prompt (which risks unrelated collateral drift elsewhere). This text only asks the model to name
# the scope and supply the corrected block; Python performs the actual splice
# (`_v3_splice_object_lock_replacement`), never the model.
_MINIMAL_V3_CHECKER_OBJECT_SCOPED_REPAIR_RULE = (
    "\n  SCOPED OBJECT-LOCK REPAIR: when the ONLY material defect you proved is inside OBJECT LOCK "
    "and fixing it needs no change anywhere else in the prompt, do not rewrite the full prompt — "
    "set \"repair_scope\":\"object_lock\" and return the COMPLETE corrected OBJECT LOCK block (its "
    "\"OBJECT LOCK:\" header line plus content lines, nothing else) in \"object_lock_replacement\": "
    "canonical permanent identity only, for objects actually present in this shot, never current "
    "state. If the defect cannot be completely solved inside OBJECT LOCK alone — it also needs a "
    "change to IDENTITY LOCK, LOCATION LOCK, the body, or anything else — do not claim object_lock "
    "scope; use the normal full-prompt repair instead and leave repair_scope as \"none\"."
)

_OBJECT_LOCK_DIRECTOR_RULE = (
    "- OBJECT LOCK IS READ-ONLY (when present): preserve the OBJECT LOCK block verbatim, in its "
    "existing position immediately after LOCATION LOCK. Do not delete, duplicate, move, shorten, "
    "expand, or redesign it, and never add current-state wording (open/closed, wet, held, location, "
    "owner, speed) into it. You may READ it as context for camera, composition, and staging. If it "
    "looks wrong, do not repair it — object identity validation belongs to Checker against canonical "
    "props evidence. If this shot's incoming prompt has no OBJECT LOCK block at all, do not add one."
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

def _pov_name_list(char_desc: str) -> list:
    """canonical names ของคนที่เป็น POV/off-screen — คนพวกนี้ไม่มีร่างให้วาด"""
    out: list = []
    seen: set = set()
    for raw_name, rest in _iter_sheet_entries(char_desc or ""):
        if not _is_pov_entry(rest):
            continue
        canon, _al = _split_name_aliases(raw_name)
        if canon and canon.lower() not in seen:
            seen.add(canon.lower())
            out.append(canon)
    out.sort(key=lambda n: -len(n))
    return out

# คำเรียกกลาง ๆ ที่ scene_desc ใช้แทนคนที่ชีตไม่ได้ตั้งชื่อจริงให้ — ผูกกับคำเพศของคนคนนั้น
_GENERIC_ALIASES = {
    "woman": ("woman", "the woman", "young woman", "she", "her"),
    "girl":  ("girl", "the girl", "young girl", "she", "her"),
    "man":   ("man", "the man", "young man", "he", "him"),
    "boy":   ("boy", "the boy", "young boy", "he", "him"),
}

def _character_identity_map(char_desc: str) -> dict:
    """{ชื่อเรียกใด ๆ (ตัวเล็ก): canonical name} — รวมทั้งชื่อจริงและ alias ทุกตัว
    ใช้เป็นจุดเดียวที่แปลงชื่อให้เป็น identity เดียว ก่อนทำ roster/clothing/props/spacing"""
    out = {}
    _unnamed: list = []          # (canon, gender_noun) ของคนที่ชีตไม่ได้ตั้งชื่อจริงให้
    _noun_count: dict = {}
    for raw_name, _rest in _iter_sheet_entries(char_desc or ""):
        canon, aliases = _split_name_aliases(raw_name)
        if not canon:
            continue
        out[canon.lower()] = canon
        for a in aliases:
            out[a.lower()] = canon
        _noun = _gender_noun_from_text(_rest) or _gender_noun_from_text(canon)
        if _noun:
            _noun_count[_noun] = _noun_count.get(_noun, 0) + 1
        if _UNNAMED_MARK_RE.search(canon) and _noun:
            _unnamed.append((canon, _noun))
    # ชีตที่ตั้งชื่อว่า "Unnamed"/"ไม่มีชื่อ" ทำให้ scene_desc ("the woman") จับคู่ไม่ได้เลย
    # ผูกคำเรียกกลาง ๆ ให้ — เฉพาะเมื่อคำเพศนั้นมีคนเดียวในเรื่อง ไม่งั้น "the woman" จะกำกวม
    # และเราจะย้ายตัวตนข้ามคนโดยไม่ตั้งใจ ซึ่งแย่กว่าปล่อยให้ resolve ไม่ได้
    for canon, noun in _unnamed:
        if _noun_count.get(noun, 0) != 1:
            continue
        for a in _GENERIC_ALIASES.get(noun, ()):
            out.setdefault(a, canon)
    return out

# ชื่อที่แปลว่า "ยังไม่มีชื่อ" — Pass A ตั้งให้คนที่เรื่องไม่ได้เรียกชื่อ ถ้าคนคนนั้นคือ POV ก็ไม่ควรมีบรรทัดของตัวเอง
_UNNAMED_MARK_RE = _re_clothing.compile(r'\b(?:unnamed|no name|nameless|ไม่มีชื่อ)\b', _re_clothing.I)

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

def _sheet_rest_by_name(char_desc: str) -> dict:
    """{ชื่อ: ข้อความหลังชื่อ} จากชีต Name: หรือ Name | ..."""
    out = {}
    for name, rest in _iter_sheet_entries(char_desc):
        out[name] = rest
    return out

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

def _parse_character_id_locks(char_desc: str) -> list[tuple[str, str, str]]:
    """คืน [(name, gender_noun, age), ...] จาก Name: หรือ Name | gender | age | ..."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for name, rest in _iter_sheet_entries(char_desc):
        noun = _gender_noun_from_text(rest) or _gender_noun_from_text(name)
        age = _age_from_sheet_rest(rest)
        if not noun and not age:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((name, noun, age))
    return out

def _gender_lock_for_shot(char_desc: str, *fields: str) -> str:
    """บรรทัดล็อกเพศ+อายุเฉพาะคนที่โผล่ในช็อตนี้ — ถ้าจับชื่อไม่ได้ ส่งทั้งชีต"""
    locks = _parse_character_id_locks(char_desc)
    if not locks:
        return ""
    blob = " ".join(fields).lower()
    picked = [(n, g, a) for n, g, a in locks if n.lower() in blob] if blob.strip() else locks
    if not picked:
        picked = locks
    # สัญชาติมาจากชีตบรรทัดเดียวกัน — เดิมไม่เคยถูกส่งมาที่ prompt เลย ตัวละครไทยจึงกลายเป็น
    # ฝรั่งบ้างเอเชียบ้างสลับไปมาข้ามช็อต เพราะ image model เดาเชื้อชาติเองทุกครั้งที่เรนเดอร์
    _nats = {n: _nat_from_sheet_rest(r) for n, r in _sheet_rest_by_name(char_desc).items()}
    bits = []
    for n, g, a in picked:
        _nat = _nats.get(n, "")
        _who = " ".join(x for x in (_nat, g) if x)      # "Thai woman"
        part = n
        if _who and a:
            part = f"{n} ({a}, {_who})"
        elif _who:
            part = f"{n} ({_who})"
        elif a:
            part = f"{n} ({a})"
        bits.append(part)
    return (
        "IDENTITY LOCK (copy this short form every shot, even at wide — it is the MINIMUM, "
        "not the whole of a person's identity): "
        + "; ".join(bits)
        + ". Format: Name (age, slim woman, hair) plus the sheet nationality before the gender noun. "
        "Digits only — not \"22-year-old\". "
        "Nationality is identity, not face detail: keep the same nationality word on every shot, at "
        "every distance. Do not change the age number between shots. "
        "This short form omits permanent body traits only to stay short — any the character sheet "
        "states (artificial or missing limb, scar, horns, tail, non-human anatomy, implant) ride "
        "along with it into every shot where that person is visible, read from the sheet itself."
    )

def _unescape_literal_nl(text: str) -> str:
    """แปลงลำดับอักขระ backslash-n ที่หลุดมาเป็นตัวหนังสือ ให้เป็นการขึ้นบรรทัดจริง
    โมเดลบางตัวตอบเป็นข้อความล้วนแต่ยังใส่ escape ของ JSON มาด้วย ('...frame height.\\n\\nSubject: ...')
    ซึ่งไหลเข้า prompt ตรง ๆ และทำให้ prompt แบบ sectioned เสียรูป (หัวข้อไม่ขึ้นบรรทัดใหม่)
    prompt จริงไม่มีเหตุให้มี backslash ตามด้วย n อยู่แล้ว จึงแปลงได้อย่างปลอดภัย"""
    if not text:
        return text
    return (text.replace(chr(92) + 'r' + chr(92) + 'n', chr(10))
                .replace(chr(92) + 'n', chr(10))
                .replace(chr(92) + 'r', chr(10)))

_POSITIVE_EXPRESSION_RULE = (
    "- POSITIVE PHYSICAL VOCABULARY (CRITICAL): \"physical only\" does NOT mean \"tense only\". Warmth, "
    "relief, amusement, tenderness, and calm all have physical forms that an image model renders "
    "perfectly well — corners of the mouth lifting, cheeks rounding and pushing up under the eyes, "
    "eyes creasing at the outer corners, brows lifting and softening at the inner ends, jaw "
    "unclenching, lips parting into a smile, teeth showing, shoulders dropping away from the ears, "
    "head tilting back, a breath released, hands opening. Reach for these as readily as for "
    "clenched/tight/furrowed/tense.\n"
    "  ARC CHECK: if the story genuinely contains beats that soften, resolve, or turn warm, do NOT let "
    "them pass with a tense face — missing them is the most common failure, and tension in every single "
    "shot flattens the emotional arc the way one repeated camera angle flattens the visual one.\n"
    "  This is a check against MISSING warmth that is really there — it is NOT a quota. If the story "
    "sustains grief, dread, fear, or submission with no relief, keep it there for as many shots as the "
    "story keeps it. NEVER invent a smile, a laugh, or a lightened beat the story does not support just "
    "to add variety: a fabricated warm beat inside a dark sequence reads as an error, not as range."
)

# ── Expression intensity — one semantic authority ────────────────────────────
# แยกสองอย่างที่เคยปนกัน:
#   EXPRESSION TRUTH  = ตัวละครรู้สึก/แสดงอะไร (เรื่องเป็นคนตัดสิน Outline เก็บรักษา)
#   EXPRESSION INTENSITY = อารมณ์เดิมนั้นถูกแสดงออกแรงแค่ไหนในภาพ (ผู้ใช้เป็นคนเลือก)
# เดิมค่านี้ถูกส่งเข้าไปตั้งแต่ Pass A ทำให้การเลื่อนสไลด์ความแรง เปลี่ยน "เรื่อง" ไปด้วย และต้องรัน
# Outline ใหม่ทั้งรอบ ตอนนี้มันทำงานที่ปลายทาง (Fill/Checker) เท่านั้น — เปลี่ยนระดับแล้วรัน Fill ใหม่พอ
_EXPRESSION_LEVELS = ("natural", "expressive", "heightened")

# ค่าเก่าที่บันทึกไว้ใน settings/preset ต้องไม่พัง — "overact" คือ heightened ในชื่อเดิม
_EXPRESSION_ALIASES = {
    "overact": "heightened", "over-act": "heightened", "overacted": "heightened",
    "heighten": "heightened", "dramatic": "heightened", "intense": "heightened",
    "subtle": "natural", "realistic": "natural", "": "expressive",
}

def normalize_expression_intensity(intensity: str) -> str:
    """ค่าจาก settings/preset (รวมค่าเก่า "overact") → หนึ่งในสามระดับ ค่าที่อ่านไม่ออกถือเป็น expressive
    ซึ่งเป็นค่าเริ่มต้นของระบบ: ภาพนิ่งไม่มีเสียงและการเคลื่อนไหวช่วย อารมณ์จึงต้องอ่านออกที่ medium shot"""
    v = (intensity or "").strip().lower()
    v = _EXPRESSION_ALIASES.get(v, v)
    return v if v in _EXPRESSION_LEVELS else "expressive"

# ระยะภาพต่อช็อต — แจกด้วยโค้ด ไม่ปล่อยให้ LLM เลือกเอง เพราะประวัติที่ผ่านมา
# มันยุบไปหาค่าเดียวทุกครั้ง (รอบแรกยุบไป extremely wide ทั้งเรื่อง รอบต่อมายุบไป close-up ทั้งเรื่อง)
_SCALE_LADDERS = {
    "epic":     ["extreme wide", "wide", "full body", "wide", "medium", "wide"],
    "balanced": ["wide", "full body", "medium", "close-up", "wide", "medium"],
    "intimate": ["wide", "medium", "close-up", "medium", "close-up", "full body"],
    # สำหรับเรื่องที่อยู่ในสถานที่เดียวทั้งเรื่องและเนื้อหาอยู่ที่ตัวคน (ห้อง รถ ห้องขัง ร้านเล็ก)
    # ไม่มี wide/extreme wide เลย เพราะในพื้นที่ปิด การถอยออกไม่ได้เพิ่มข้อมูลอะไร
    # มีแต่ทำให้เสียจุดโฟกัส — full body คือระยะกว้างสุดที่มีความหมาย
    "chamber":  ["full body", "medium", "close-up", "medium", "full body", "close-up"],
}

# ศัพท์กล้อง ("wide shot") อ่อนเกินไปสำหรับ diffusion model — มันตอบสนองกับคำที่บอก
# "สัดส่วนของตัวละครในเฟรม" ตรงๆ ดีกว่ามาก ผลรันจริง: ช็อตที่สำเร็จเป็น extreme wide
# ส่วนช็อตที่ล้มเหลว 3 ใน 4 เป็น wide เฉยๆ — ต้องมีคำบอกขนาดกำกับเสมอ
_SCALE_HINTS = {
    # "vast landscape around her" เป็นข้อเท็จจริงของสถานที่ ไม่ใช่ของระยะภาพ — ห้องนอนสี่ก้าว
    # ที่ถ่าย extreme wide ก็ยังเป็นห้องสี่ก้าว การเขียนคำนี้ติดมากับระยะทำให้โมเดลเติม
    # "vast interior space" ให้ห้องในอพาร์ตเมนต์ทุกครั้ง (เจอจริงในผลรัน)
    "extreme wide": ("the entire figure visible head to toe as a small distant figure "
                     "occupying roughly one-eighth of the frame height, the surrounding space "
                     "dominating the frame — however large or small that space actually is"),
    "wide":         ("the entire figure visible head to toe occupying about one third of the "
                     "frame height, the environment dominating the frame"),
    "full body":    "the entire figure head to toe filling most of the frame height",
    "medium":       "waist-up framing",
    "close-up":     "the face or hands filling the frame",
}

# ชุดสำรองสำหรับช็อตที่ไม่มีตัวละคร — hint ปกติอ้างอิง "the figure" เสมอ ถ้าใช้กับช็อตห้องว่าง
# จะได้ "ห้องเล็กจิ๋วในพื้นที่ว่างมหาศาล" ทั้งที่เป็นห้องปิดสี่ก้าวไม่มี vast empty space เลย
_SCALE_HINTS_NO_SUBJECT = {
    "extreme wide": "the full space visible from corner to corner, no figure in frame",
    "wide":         "the space seen in full with its main furniture and walls visible",
    "full body":    "a broad view of the space",
    "medium":       "a section of the space filling the frame",
    "close-up":     "a single object or surface filling the frame",
}

def _scale_with_hint(scale: str, has_subject: bool = True) -> str:
    """ชื่อระยะ + วลีบอกสัดส่วน — ช็อตที่ไม่มีตัวละครใช้ชุดคำที่อ้างอิงพื้นที่แทนตัวคน
    ไม่งั้นจะได้ 'ห้องเล็กจิ๋วในพื้นที่ว่างมหาศาล' ในห้องปิดที่ไม่มีพื้นที่ว่างเลย"""
    table = _SCALE_HINTS if has_subject else _SCALE_HINTS_NO_SUBJECT
    hint = table.get(scale, "")
    return f"{scale} ({hint})" if hint else (scale or "medium")

_NO_SUBJECT_MARKERS = ("no character", "no one", "empty room", "empty space", "nobody")

def _shot_has_subject(expression: str) -> bool:
    """เช็คว่าช็อตนี้มีตัวละครไหม จาก expression field — ว่างหรือมีข้อความบ่งชี้ว่าไม่มีคน = ไม่มีตัวละคร"""
    e = (expression or "").strip().lower()
    if not e:
        return False
    return not any(m in e for m in _NO_SUBJECT_MARKERS)

_PENETRATIVE_WORDS = (
    'penetrat', 'intercourse', 'thrust', 'ชู้', 'เย็ด',
)

def _is_penetrative_scene(scene_desc: str, clothing_state: str) -> bool:
    """เช็คคร่าวๆ ว่าช็อตนี้เป็นฉาก penetrative sex — ใช้ตัดสิน force_wide เท่านั้น
    ไม่ใช่ตัวตรวจ NSFW ทั่วไป (นั่นมี _is_sexual_shot ใน fill loop อยู่แล้ว คนละจุดประสงค์)"""
    text = f"{scene_desc or ''} {clothing_state or ''}".lower()
    return any(w in text for w in _PENETRATIVE_WORDS)

def _assign_shot_scale(idx: int, total: int, preset: str = "balanced", force_wide: bool = False) -> str:
    """แจกระยะภาพแบบวนรอบตาม preset — ช็อตแรกเปิดเรื่องด้วยระยะกว้างสุดของ ladder เสมอ
    force_wide ใช้เมื่อฉากนั้นชนกับกฎ PENETRATIVE SEX ที่บังคับ wide/full-body เสมอ"""
    if force_wide:
        return "full body"
    ladder = _SCALE_LADDERS.get(preset, _SCALE_LADDERS["balanced"])
    if idx == 0:
        return ladder[0]
    return ladder[idx % len(ladder)]

def _sheet_name_list(char_desc: str) -> list[str]:
    """Sheet names in original spelling, longest first so 'Ta Sai' matches before 'Ta'."""
    out: list[str] = []
    seen: set[str] = set()
    for n, _r in _iter_sheet_entries(char_desc):
        # ชื่อในชีตอาจเขียนเป็น "Krit (ชายช่างภาพ)" — คืนเฉพาะชื่อ canonical ไม่เอาวงเล็บ alias
        n, _aliases = _split_name_aliases(n)
        if not n:
            continue
        key = n.lower()
        if key not in seen:
            seen.add(key)
            out.append(n)
    out.sort(key=lambda n: -len(n))
    return out

# กันไม่ให้บรรยายสีหน้าแบบ micro-expression ยาวเกินจนกิน "น้ำหนัก" ของรายละเอียดที่ต่างกันจริงระหว่างช็อต
# (เช่นผ้าละลายถึงระดับไหน ดอกไม้ ประกายแสง) — diffusion model เรนเดอร์ micro-expression ละเอียดขนาดนั้นไม่ออกอยู่แล้ว
# PERF-F3 SITE 2: proven live loss — a resolved secondary body-performance cue ("tense shoulders",
# the only thing distinguishing Heightened from Expressive on an otherwise-identical action) was
# being trimmed under this same budget as ordinary decorative detail once Director's own added
# camera/lighting clauses pushed the rewrite toward the ~110-word ceiling. Fix is trim-order only —
# no new category invented, no protection for tension wording in general (see NOT PROTECTED below).
_PROMPT_BUDGET_RULE = (
    "- LENGTH BUDGET (CRITICAL): keep the whole prompt under ~110 words after the mandatory facts. "
    "NEVER TRIM (outrank the word cap): character identity minimum (name, age digits, nationality, "
    "gender noun), every remaining garment from clothing_state, the clothing/exposure state those "
    "garments imply, the main action/pose, spacing/depth/relative scale, prop ownership, location, "
    "and any already-resolved body-performance geometry (weight shift, stance, brace, torso/"
    "shoulder participation, limb geometry, contact response, pose asymmetry, line of action) that "
    "is the thing materially distinguishing THIS shot's resolved physical commitment from a "
    "neighboring intensity level — a body cue only counts here when removing it would make two "
    "otherwise-similar shots read as the same pose; ordinary tension/mood wording that adds no new "
    "pose information is NOT protected and stays trimmable. "
    "Trim first: mood adjectives, decorative atmosphere, repeated beauty, extra lighting adjectives, "
    "generic emotional tension wording that adds no new pose information, micro-expression beyond "
    "the visibility gate, fabric weave/texture. At FAR/wide, drop face/skin/finger detail before any "
    "garment, spatial, or body-performance fact. Facial-expression wording "
    "must fit in ONE short clause of max 12 words (e.g. \"eyes closed, mouth trembling\") — never a "
    "multi-clause anatomical breakdown of brow + jaw + mouth + shoulders + breathing. An image model "
    "cannot render micro-expressions, and the extra words dilute the details that actually differ "
    "between shots. Spend leftover words on what CHANGED since the previous shot instead."
)

# เพดาน 110 คำข้างบนใช้กับ style ที่เขียนเป็นก้อนเดียวได้ แต่กับ style ที่สั่งให้กรอก 6 section แบบ
# "vivid, dense prose ... extremely precise about anatomy" มันขัดกันเองในโปรมต์เดียวกัน ผลรันจริง 27/27
# ช็อตเมินเพดานนี้ทั้งหมด (เฉลี่ย 356 คำ = 3.2 เท่า) และยิ่งเขียนยิ่งบวม (ช็อตท้ายยาวกว่าช็อตต้น 38%)
# กฎที่ทำตามไม่ได้ = ไม่มีกฎ จึงเปลี่ยนเป็นเพดานรายช่องที่ทำตามได้จริงและตรวจได้
# ก่อนหน้านี้ section เคยใช้เพดานรวม 110 คำแบบเดียวกับ narrative — รันจริง 27/27 ช็อตเมินเพดานนั้น
# (เฉลี่ย 356 คำ = 3.2 เท่า) จึงเปลี่ยนเป็นเพดานรายช่องที่ตรวจได้จริง แต่ตัวเลขเป๊ะรายช่องกลายเป็น
# ระบบกฎที่สองทับ format เดิม — คงไว้แค่หลักการที่พิสูจน์แล้วว่าจำเป็น (dense ไม่ใช่ยาว, อย่าย้ำของเดิม)
# ตัดตัวเลขเพดานรายช่องและกฎเปรียบเทียบความยาวข้ามช่อง (Subject สั้นกว่า Environment ฯลฯ) ออก
_SECTION_BUDGET_RULE = (
    "- LENGTH: each section is a few dense sentences, not a paragraph — every word carries a fact, not "
    "decoration. Facial expression fits in one short clause, not a breakdown of brow/jaw/mouth/shoulders. "
    "Spend words on what changed since the previous shot; do not restate unchanged setting or lighting."
)

# ชีตตัวละคร/props เป็นที่เดียวที่ตัดสินใจว่าอะไรหน้าตายังไง (ดู PHYSICAL DETAIL IS YOURS TO DECIDE ใน
# outline) แต่ฝั่ง fill ไม่เคยมีกฎนี้ พอชีตมีช่องว่าง (เคสจริง: ชีตตกตัวละครไปทั้งคน) fill เขียนคำว่า
# "unspecified colour" ลง prompt ที่ส่งให้ diffusion model ตรง ๆ — 24 จาก 27 ช็อต
_NO_HEDGE_RULE = (
    "- NO HEDGE WORDS IN THE PROMPT (CRITICAL): this text is read by an image model, which cannot draw "
    "a hedge. STRICTLY FORBIDDEN anywhere in the prompt: \"unspecified\", \"unknown\", \"not stated\", "
    "\"n/a\", \"implied presence\", \"presumably\", \"possibly\", \"appears to be\", \"some kind of\", "
    "\"of indeterminate colour\". If the sheets do not say what colour or material something is, either "
    "DECIDE one concrete value that fits the person, place and era and write it as a plain fact, or drop "
    "the detail entirely — \"trousers\" is a usable prompt, \"trousers in an unspecified colour\" is not."
)

# นิยาม section ด้านบน (Subject: ...hair, eyes, skin... / Outfit: ...material... / Pose: ...hands/props)
# สั่งทุกอย่างที่ VISIBILITY GATE ห้ามที่ระยะไกล และไม่มีเงื่อนไข scale เลย — เป็นต้นตอที่แรงกว่ากฎอื่น
# เพราะเป็น "สเปกของช่องที่ต้องกรอก" ไม่ใช่กฎลอยๆ โมเดลจึงกรอกครบทุกช่องเสมอ บล็อกนี้เขียนสเปกชุดที่สอง
# สำหรับ wide/extreme wide ทับของเดิม (ไม่แก้ของเดิม เพราะระยะใกล้ยังต้องใช้ครบ)
_SECTION_DEFS_AT_DISTANCE = (
    "\n  SECTION DEFINITIONS CHANGE AT DISTANCE (when the assigned SHOT SCALE is \"wide\" or \"extreme "
    "wide\", these REPLACE the three definitions above — the originals describe closer scales only):\n"
    "    Subject: name + gender noun (woman/man/girl/boy) + rough age + build as a silhouette + hair "
    "length/colour + garment colours ONLY. FORBIDDEN here: eye colour, skin tone, face shape, hair "
    "texture, and any facial expression. Write what the shape of the person reads as from far away, "
    "e.g. \"Name (22, slim woman): a small distant woman at the far side of the space, holding an object out "
    "in front of her with both arms\" — the action of THIS beat, written as a "
    "far-away body shape. PLACEHOLDER BAN: \"doing this beat\", \"performing this beat\", \"doing the "
    "action\", \"in this moment\" and any equivalent stand-in phrase are FORBIDDEN in the output — they "
    "are describing the instruction instead of the picture, and the image model can draw nothing from "
    "them. Distance removes DETAIL (fingers, grip, face), never the action itself: name the action in "
    "whole-body terms every time. "
    "Never \"a figure\" or \"a silhouette\" without woman/man.\n"
    "    Outfit: garment colour and overall shape only, e.g. \"dark outer layer over a light top\". "
    "FORBIDDEN here: fabric/material names, weave, fit, layering, and any phrase itemising which "
    "body parts are covered. The clothing_state category itself still stays accurate.\n"
    "    Pose: whole-body silhouette only. If the beat has an action, the silhouette MUST show that "
    "same action — not a default upright stand, and not a stock action from a list. Only a genuinely "
    "still beat may read as standing or sitting at ease. State which way the body faces. FORBIDDEN here: hands, fingers, grip, what a hand is doing to a prop, and gaze direction. A "
    "prop the character holds is named as part of the silhouette, e.g. \"the object visible only as "
    "a tiny shape held against the body\".\n"
    "    If this format folds Pose into Subject, the Pose limits above apply inside Subject, and any "
    "instruction elsewhere to be \"extremely precise about anatomy and contact\" or to give \"exact "
    "hand/limb positions\" is SUSPENDED at these two scales — precision there means precision of the "
    "SILHOUETTE.\n"
    "    Climax (if this format has one): state the whole composition — the small figure against the "
    "space — never a facial or hand close-up beat.\n"
    "    At these two scales the Subject, Outfit and Pose sections should be SHORT — roughly one sentence "
    "each — and Environment must be the longest section in the prompt.\n"
    "    Do not write these instructions, their labels, or phrases like \"colour and shape only\" into the "
    "output; they describe what to write, not text to copy."
)

# Identity Lock / Location Lock / Spatial Lock / Interaction Lock / Final Identity Check เป็น
# section จริงของ Minimal V3 — ถ้า regex ไม่รู้จัก ตัวตรวจฟอร์แมตจะมองไม่เห็นแล้วเดาว่า prompt
# ไม่ใช่ sectioned
_SECTION_LABEL_RE = _re_clothing.compile(
    r'^(Camera|Subject|Outfit|Environment|Lighting|Pose|Climax'
    r'|Identity\s+Lock|Identity|Location\s+Lock|Object\s+Lock|Spatial\s+Lock|Interaction\s+Lock'
    r'|Final\s+Identity\s+Check)\s*:',
    _re_clothing.I | _re_clothing.M,
)

def _detect_prompt_format(prompt: str) -> str:
    """เดาฟอร์แมตจากข้อความที่ Fill เขียนไว้แล้ว — Director ต้องตามนี้ ไม่ตาม dropdown ตอนรัน"""
    t = (prompt or "").strip()
    if not t:
        return "narrative"
    if _re_clothing.search(r'\b\d+-panel (?:manga )?page\b|\b\d+\s+asymmetric panels', t, _re_clothing.I):
        return "manga_page"
    labels = {m.group(1).lower() for m in _SECTION_LABEL_RE.finditer(t)}
    # IDENTITY LOCK / LOCATION LOCK อยู่ในทุกฟอร์แมตของ Minimal V3 จึงไม่ได้แปลว่าเป็น sectioned
    # และไม่ควรทำให้การนับ label เข้าใจผิด ตัวที่บอกว่าเป็น sectioned จริงคือ Camera/Subject/...
    _locks = {"identity lock", "identity", "location lock", "object lock", "spatial lock",
              "interaction lock", "final identity check"}
    _sec_labels = labels - _locks
    if "climax" in _sec_labels and ("camera" in _sec_labels or "subject" in _sec_labels):
        return "sectioned_detailed"
    if len(_sec_labels) >= 2:
        return "sectioned_detailed"
    # ตัดบล็อกล็อกออกก่อนตัดสินว่าที่เหลือเป็นแท็กหรือร้อยแก้ว — บล็อกล็อกมีขึ้นบรรทัดและจุด
    # ของตัวเอง ซึ่งเดิมทำให้ prompt แบบแท็กถูกอ่านเป็น narrative แล้ว Director ก็ยุบเป็นย่อหน้า
    body = t
    for _lock_re in (r'(?im)^[ \t]*IDENTITY\s+LOCK\s*:', r'(?im)^[ \t]*LOCATION\s+LOCK\s*:',
                     r'(?im)^[ \t]*OBJECT\s+LOCK\s*:'):
        _m = _re_clothing.search(_lock_re, body)
        if _m:
            _after = body[_m.end():]
            _blank = _re_clothing.search(r'\n[ \t]*\n', _after)
            body = (body[:_m.start()] + (_after[_blank.end():] if _blank else "")).strip()
    commas = body.count(",")
    periods = body.count(".")
    if commas >= 8 and periods <= 2 and "\n" not in body[:180]:
        return "tag_style"
    # แท็กที่เขียนบรรทัดละแท็ก (ตามโครงที่ V3 สั่ง) — จุดน้อย จุลภาคท้ายบรรทัดเป็นตัวคั่นจริง
    _lines = [x.strip() for x in body.splitlines() if x.strip()]
    if len(_lines) >= 3 and periods <= 2 and sum(1 for x in _lines if x.endswith(",")) >= len(_lines) - 1:
        return "tag_style"
    return "narrative"

def _visibility_gate_for(scale: str) -> str:
    """VISIBILITY GATE ตามระยะที่ช็อตถูก assign — ตัวเต็ม (4.4k) เกือบทั้งก้อนเป็นข้อห้ามของ wide/extreme
    wide ช็อต medium/close-up จึงแบกข้อความที่ตัวมันเองบอกว่า "correct and expected at your scale" ไปฟรีๆ
    scale="" = ผู้เรียกไม่รู้ระยะ (director/editor ที่ยังไม่ได้แยก) → คืนตัวเต็มเหมือนเดิม"""
    s = (scale or "").strip().lower()
    if s in ("wide", "extreme wide"):
        return _VISIBILITY_GATE_RULE
    if s in ("medium", "close-up"):
        return (
            "- VISIBILITY GATE: at this shot's assigned SHOT SCALE every level of detail is resolvable — "
            "eyes and gaze, lips and mouth shape, fingers and hand placement, skin texture and tone, fabric "
            "weave, small marks on an object, and light falling on skin or on a face are all correct and "
            "expected here. Describe them. (The gate exists to strip these at wide distances; it does not "
            "restrict anything at this one.)"
        )
    if s == "full body":
        return (
            "- VISIBILITY GATE (CRITICAL — overrides every other rule about what to include): at \"full "
            "body\", avoid only the finest detail — skin texture, fabric weave, and small marks, stains or "
            "patterns on an object do not resolve at this distance. Posture, general gesture, hands, gaze, "
            "and facial expression are fine and expected.\n"
            "  SUBJECT-MATTER EXCEPTION: when the body IS the subject matter of the shot — the story beat is "
            "physical contact, pain, injury, treatment, craft performed on a body, or an object held and "
            "worked on — one fine detail may stay, chosen as the SINGLE most important physical tell of that "
            "beat (a clenched jaw, a hand gripping fabric, a tendon standing out). Keep exactly one; it "
            "carries the beat. It never licenses a list: one tell, not three.\n"
            "  MOUTH AND SMILE: an open mouth, visible teeth, a broad smile, or a head thrown back reads "
            "clearly at this distance — these are silhouette-scale, not micro-expression. Allow them. Only "
            "the fine shape of the lips and the subtle set of the brow stay gated here."
        )
    return _VISIBILITY_GATE_RULE

def chat_camera_semantics_rule() -> str:
    """กติกากล้องสำหรับ Chat (ทั้งเรื่อง) — ภาษาเดียวกับที่ Fill/Director ใช้อยู่แล้ว

    Chat ได้ system prompt ของตัวเอง จึงไม่เคยเห็นกฎกล้องของไปป์ไลน์เลย ผลคือมันแนะนำให้ "ซูมเข้า"
    เพื่อให้เห็นรายละเอียดที่เขียนไว้ แทนที่จะตัดรายละเอียดนั้นทิ้ง และตีความ POV เป็นกล้องจริงในมือคน
    เป้าหมายที่ระยะภาพดึงมาจาก _SCALE_HINTS ตัวเดียวกับที่ Fill ใช้ ไม่ได้พิมพ์ซ้ำ — ตัวเลขจึงไม่มีวันเพี้ยนจากกัน"""
    _targets = "\n".join(
        f"    {name.upper():<13} {hint}"
        for name, hint in (("extreme wide", _SCALE_HINTS["extreme wide"]),
                           ("wide", _SCALE_HINTS["wide"]),
                           ("full body", _SCALE_HINTS["full body"]),
                           ("medium", _SCALE_HINTS["medium"]),
                           ("close-up", _SCALE_HINTS["close-up"]))
    )
    return (
        "CAMERA SEMANTICS — six separate controls, never synonyms for each other:\n"
        "    SHOT SCALE      how far the camera is: how much of the frame the subject fills.\n"
        "    CAMERA ANGLE    the camera's height and tilt: low, eye level, high.\n"
        "    VIEW DIRECTION  which side of the subject we see: front, three-quarter, profile, back.\n"
        "    POV             whose viewpoint in the scene the picture is taken from.\n"
        "    SPACING         how far apart the CHARACTERS stand from each other.\n"
        "    COMPOSITION     where things sit inside the frame.\n"
        "Changing one of these never means changing another.\n\n"
        "SHOT SCALE IS ASSIGNED, NOT A STYLE CHOICE. Each shot carries a \"shot_scale\" and it is "
        "authoritative. You may suggest a different angle, view direction or composition freely; you "
        "may NOT quietly move a shot from extreme wide to medium because the face would read better. "
        "Distance is a story decision that was already made. Change it only when the user asks for it "
        "in so many words (\"make it closer\", \"pull back\").\n"
        "WHAT EACH SCALE MEANS IN THE PICTURE (read this to judge whether a shot's scale reads "
        "correctly — these are meanings to reason with, never literal sentences to paste into a "
        "prompt, and never a reason to drop or restructure the required prompt format above):\n"
        + _targets + "\n"
        "When a prompt's own wording needs to convey the size, say it in your own words, not just the "
        "bare label — \"wide shot\" alone tells an image model very little — but every rewritten "
        "prompt still keeps the exact structure already required above (IDENTITY LOCK block included) "
        "and stays in the project's own established prompt format; this camera guidance never "
        "replaces that format.\n\n"
        "VISIBILITY GATE — before you add or keep any detail, ask whether it can actually be seen at "
        "this shot's scale.\n"
        "    At extreme wide and wide, keep: the silhouette, what they are wearing as whole garments, "
        "the large action of the body, the shape of a big prop, the place, where people stand and how "
        "far apart, the light and the weather. Drop: iris colour, lips, brows, skin texture, "
        "individual fingers, knuckles, fabric weave, micro-expression — none of it resolves at that "
        "distance, and asking for it fights the framing you were given.\n"
        "    At medium, the face and gaze carry real weight; at close-up they carry the shot.\n"
        "NEVER ZOOM IN TO SAVE A DETAIL. If a detail cannot be seen at the assigned scale, the detail "
        "goes — the scale stays. \"Extreme wide + detailed red irises\" is fixed by deleting the irises, "
        "never by making it a medium shot.\n\n"
        "POV IS A VIEWPOINT, NOT A DEVICE. When a shot says POV, it means the picture is taken from "
        "where an off-screen person stands — that person is never drawn. It does NOT mean a camera, a "
        "phone, a selfie, a photographer, a recording device, or a handheld-photo look, unless the "
        "story genuinely contains one.\n"
        "    \"spacing: POV: looking back at Lin Se\" means a first-person viewpoint looking back "
        "across the scene at her.\n"
        "    Prefer wording like \"first-person viewpoint\", \"facing the viewer\", \"looking directly "
        "toward the viewer\", \"seen through the viewer's eyes\". Avoid \"looking into the camera "
        "lens\", \"POV angle\", \"handheld camera\" — an image model reads those as an actual "
        "photography setup and will draw one.\n"
        "    Camera words are still right for framing itself: low-angle shot, high-angle shot, wide "
        "shot, close-up.\n\n"
        "SPACING IS NOT CAMERA DISTANCE. \"spacing\" says how far the characters are from each other; "
        "\"shot_scale\" says how far the camera is from them. FAR does not mean a wide shot, and NEAR "
        "does not mean a close-up. Two people standing far apart can still be shot close.\n\n"
        "AN EDIT CHANGES ONLY WHAT WAS ASKED FOR. \"Make this low angle\" changes the angle and leaves "
        "the scale, the POV, the spacing, the action and the clothing exactly as they were. \"Move her "
        "to the right of frame\" changes composition only. \"Make it first-person\" changes POV only. "
        "\"Make it closer\" is the one that may change shot scale.\n\n"
        "WHEN YOU REPORT A CAMERA PROBLEM, SAY WHICH KIND IT IS — tag it [SHOT SCALE], [ANGLE], "
        "[VIEW DIRECTION], [POV], [SPACING], [COMPOSITION] or [VISIBILITY GATE], then say what to do. "
        "For example: \"[VISIBILITY GATE] This extreme wide prompt asks for iris colour and trembling "
        "lips. Keep the scale; drop those and keep the silhouette, the robe, the raised sword and the "
        "mist.\"\n\n"
        "VARIETY LIVES IN ANGLE AND COMPOSITION. Consecutive shots should not repeat the same framing "
        "— vary the height, the side you see, what fills the foreground, where the subject sits in the "
        "frame. Variety never justifies overriding an assigned distance."
    )

def _build_prompt_format_rule(prompt_style: str, scale: str = "",
                              camera: bool = True, variety: bool = True) -> tuple[str, str]:
    """คืน (format_rule_text, prompt_field_desc) ตาม prompt_style — ใช้ร่วมกันทุกจุดที่เขียน/แก้ prompt
    (Fill, Editor, Director, Chat) เพื่อให้ output format เดียวกันตลอด pipeline
    scale = ระยะกล้องของช็อตนั้น ("" = ผู้เรียกไม่รู้ระยะ → คงพฤติกรรมเดิมทุกอย่าง)"""
    # _SECTION_DEFS_AT_DISTANCE เขียนกำกับตัวเองว่า "when the assigned SHOT SCALE is wide or extreme
    # wide, these REPLACE the three definitions above" — ที่ระยะใกล้จึงไม่มีผลใดๆ ตัดทิ้งได้
    _sc = (scale or "").strip().lower()
    # "sectioned" ถูกยุบเข้ากับ "sectioned_detailed" แล้ว (ต่างกันแค่ 188 chars และผู้ใช้แยกไม่ออก
    # ในการใช้งานจริง) เก็บชื่อเดิมไว้เป็น alias เพื่อให้ช็อต/สเตตที่บันทึกไว้ก่อนหน้ายังเปิดได้
    if prompt_style == "sectioned":
        prompt_style = "sectioned_detailed"
    _secdefs = _SECTION_DEFS_AT_DISTANCE if _sc in ("", "wide", "extreme wide") else ""
    # camera=False = ผู้เรียกสั่งห้ามเขียนกล้องอยู่แล้ว (fill แบบ narrative/tag_style ที่ปล่อยให้
    # Director ใส่กล้องทีหลัง) กฎ "อย่าใช้มุมซ้ำ" จึงไม่มีอะไรให้บังคับ และขัดกับคำสั่งห้ามในโปรมต์เดียวกัน
    # variety=False = ให้ checker เป็นคนจับมุมซ้ำแทน — fill ได้ระยะมาจาก ladder อยู่แล้ว กฎนี้จึงเป็น
    # งาน "ย้อนดูช็อตก่อนหน้าแล้วเทียบ" ซึ่งเป็นหน้าที่ของ checker ที่เห็นช็อตก่อน ๆ ครบกว่า
    _cam_rule = ("\n" + _CAMERA_VARIETY_RULE) if (camera and variety) else ""
    if prompt_style == "tag_style":
        _tag_open = (
            "  CAMERA TAGS FIRST: the assigned SHOT SCALE tag opens the list (e.g. \"extreme wide shot\", "
            "\"full body shot\"), never at the end.\n"
            if camera else
            "  Do NOT write camera, shot-scale, angle, or lighting tags — Director adds those later.\n"
        )
        format_rule = (
            "- FORMAT (CRITICAL — ATOMIC TAG STYLE): write/keep the prompt as a comma-separated list of SHORT, "
            "ATOMIC phrases — each phrase naming exactly ONE attribute or action, then a comma, "
            "then the next. This is NOT prose with commas — it is a tag list.\n"
            "  STRICTLY FORBIDDEN connective/linking words that turn phrases into sentences: \"where\", \"as\", "
            "\"which\", \"while\", \"that\", \"who\", \"-\" (dash), \"The scene is\", any "
            "gerund clause chaining two ideas together (e.g. \"observing X, establishing Y\"). If you catch "
            "yourself writing a conjunction, STOP and split it into two separate comma-tags instead.\n"
            + _tag_open +
            "  After camera tags (if any), write the IDENTITY LOCK block (Name = WOMAN|MAN, short "
            "look, CURRENT clothing), then the comma-separated tags. The rest stays a tag list — "
            "do not convert it into prose or labeled sections.\n"
            "  HIDDEN ORDER (keep this sequence even though there are no section labels): "
            "spatial tags bound to names, then each present person in turn (identity, then one "
            "\"Name wearing <garment>\" tag per remaining garment, then that person's pose/action), "
            "then props, then environment"
            + (" / lighting" if camera else "") + ".\n"
            "  SPATIAL TAGS ARE OWNED: bind every spatial phrase to the actual sheet name from "
            "scene_desc (left foreground, distant background, much smaller in frame, empty space "
            "between the two named people). FORBIDDEN loose: \"woman left\", \"distant background\", "
            "\"negative space\" with no owner. Never invent a name that is not in scene_desc.\n"
            "  GARMENT TAGS ARE OWNED: every remaining garment is \"<sheet name> wearing <garment>\". "
            "FORBIDDEN when two or more people are in the shot: a colour or garment tag with no wearer. "
            "UNCHANGED DOES NOT MEAN OMITTED — the image model has no memory; name every garment again.\n"
            "  GOOD: owner-bound spatial tags, then \"<sheet name> wearing <garment>\" once per remaining piece.\n"
            "  BAD (loose garments): a name, then a colour, then a garment, then another name — the image model binds those to the wrong body.\n"
            "  BAD (still prose, forbidden): \"a deep forest floor drenched in humid air, where shafts of "
            "sunlight slice through the foliage\" — split into tags instead.\n"
            "  No section labels like \"Camera:\"/\"Subject:\"/\"Lighting:\"."
        )
        field_desc = "<comma-separated list of short atomic tags (max ~3-6 words each), no connective words, no section labels, no text overlay instructions>"
    elif prompt_style == "__retired_sectioned__":
        format_rule = (
            "- FORMAT (CRITICAL — LABELED SECTIONS): write/keep the prompt as a fixed sequence of labeled "
            "sections, each on its own line as \"Label: prose sentence(s).\" — vivid descriptive prose WITHIN "
            "each section, but sections themselves are explicit and always in this exact order (skip a section "
            "entirely, don't leave it blank, if it truly doesn't apply to this shot):\n"
            "  Environment: location, backdrop, set dressing, time of day.\n"
            "  Lighting: light source, direction, quality (soft/hard), color temperature, mood.\n"
            "  Camera: framing, camera angle/height, lens feel, shot type.\n"
            "  Subject: who is in the shot — name, age, nationality, hair, eyes, skin, body type, facial expression.\n"
            "  Outfit: every remaining garment by name, color, type — render the MEANING of clothing_state "
            "(who is wearing what). Never paste tracker labels CLOTHED / PARTIALLY UNDRESSED / TOPLESS / "
            "NUDE into the image prompt.\n"
            "  Pose: body position, action, where they're looking, what they're doing with hands/props.\n"
            "  Each section is 1-3 sentences of natural prose, not tags. Do NOT add sections beyond this list "
            "(no \"Mood:\", no \"Story:\") and do NOT merge two sections into one line.\n"
            "  Example line format: \"Pose: She stands with her back gently arched, head turned over her "
            "shoulder to meet the camera, one hand resting on the nearby railing.\"\n"
            "  The prompt must OPEN with the assigned SHOT SCALE as a short lead phrase before the first "
            "section label (e.g. \"Extreme wide shot.\" then a newline then \"Environment: ...\"), and the "
            "Camera section must repeat that same scale. Do not substitute a different distance in either place."
            + _secdefs
        )
        field_desc = ("<labeled-section prompt: one line per section in the fixed order Environment / Lighting / "
                      "Camera / Subject / Outfit / Pose, each as \"Label: prose sentence(s).\", skip a section "
                      "only if genuinely not applicable, no text overlay instructions>")
    elif prompt_style == "sectioned_detailed":
        format_rule = (
            "- FORMAT (CRITICAL — LABELED SECTIONS): write the prompt using these exact labeled sections. "
            "Each section on its own line as \"Label: ...\", separated by a blank line (\\n\\n) between "
            "sections, in exactly this order, no extra sections. Compose the shot as one coherent image "
            "first, then express it through these labels — the sections organize the prose, they are not "
            "a second set of rules on top of the ones already given above.\n"
            "  Camera: framing and angle. Open with the assigned SHOT SCALE — never substitute a "
            "different distance, except for penetrative sex or group scenes, where full-body/wide "
            "framing that clearly shows the action wins over the assigned scale.\n"
            "  Identity Lock: IDENTITY LOCK block — Name = WOMAN|MAN, short look, CURRENT clothing; "
            "only people in this image. Own section; do not fold it into Subject.\n"
            "  Subject: who is in the shot, their pose and expression.\n"
            "  Outfit: what each present person is wearing right now.\n"
            "  Environment: this shot's location, as seen from this camera position.\n"
            "  Lighting: light source, direction, quality, mood.\n"
            "  Climax: the single most important visual or emotional moment of this shot."
        )
        field_desc = (
            "<sectioned_detailed prompt with exact labels in order: "
            "Camera / Identity Lock / Subject / Outfit / Environment / Lighting / Climax>"
        )
    else:
        _narr_open = (
            "- The paragraph must OPEN with the assigned SHOT SCALE stated in plain language before anything "
            "else (e.g. \"An extreme wide landscape view in which a small distant woman...\"), and the "
            "amount of detail throughout must match that distance per the VISIBILITY GATE below.\n"
            if camera else
            "- Do NOT open with camera, shot scale, angle, or lighting — Director adds those later. "
            "Write story content only. Detail level still follows the VISIBILITY GATE for the assigned distance.\n"
        )
        format_rule = (
            "- FORMAT: ONE continuous flowing paragraph, vivid prose — no section labels like "
            "\"Camera:\"/\"Subject:\"/\"Lighting:\".\n"
            + _narr_open +
            "- After any opening scale phrase, write the IDENTITY LOCK block, then ONE flowing prose "
            "paragraph. The lock is labeled lines; the body stays Narrative prose — do not convert "
            "it into sections.\n"
            "- HIDDEN ORDER inside the one paragraph: spatial layout first (who is left/right, which "
            "depth plane, what of this place sits BETWEEN them), then each present person in turn "
            "(identity, then an outfit clause naming every remaining garment, then action/expression), "
            "then props, then environment.\n"
            "- UNCHANGED DOES NOT MEAN OMITTED. The image model has no memory. Every garment still worn "
            "in this shot must be named again on the person who wears it. FORBIDDEN: \"both still dressed "
            "as before\". Bind outfits and spatial layout to the sheet names from scene_desc — never invent a name."
        )
        field_desc = "<ONE continuous flowing English prompt paragraph, no section labels, no text overlay instructions>"
    format_rule = (format_rule + "\n" + _SHEER_COVERAGE_RULE + "\n" + _CLOTHED_COMPLETENESS_RULE
                   + "\n" + _PROMPT_CLOTHING_WORDING_RULE
                   + _cam_rule
                   # style ที่มี section ได้เพดานรายช่อง (เพดานรวม 110 คำขัดกับสเปก section ในโปรมต์เดียวกัน
                   # จนถูกเมินทุกช็อต) ส่วน style ก้อนเดียว/tag ยังใช้เพดานรวมเหมือนเดิม
                   + "\n" + (_SECTION_BUDGET_RULE if prompt_style in ("sectioned", "sectioned_detailed")
                             else _PROMPT_BUDGET_RULE)
                   + "\n" + _NO_HEDGE_RULE
                   + "\n" + _visibility_gate_for(scale))
    return format_rule, field_desc

def _build_genre_str(genre_key: str, genre2_key: str = "", include_structure: bool = True) -> str:
    """สร้าง genre context string สำหรับ inject ใน system prompt (รองรับ 2 genres)
    include_structure=False สำหรับ Pass A — โครงสำเร็จรูปของแนวทำให้โมเดลแปะป้ายลง events
    แทนการเขียนว่าใครทำอะไรในเรื่องนี้"""
    parts = []
    g1 = GENRE_MAP.get(genre_key) if genre_key and genre_key != "none" else None
    g2 = GENRE_MAP.get(genre2_key) if genre2_key and genre2_key != "none" else None

    if g1 and not g2:
        label, tone, structure = g1
        parts.append(f"Story Genre: {label}")
        if tone:      parts.append(f"Tone & Atmosphere: {tone}")
        if include_structure and structure:
            parts.append(f"Narrative Structure: {structure}")
    elif g1 and g2:
        l1, t1, s1 = g1
        l2, t2, s2 = g2
        parts.append(f"Primary Genre: {l1} (dominant — story structure follows this)")
        if t1: parts.append(f"  Tone: {t1}")
        if include_structure and s1:
            parts.append(f"  Structure: {s1}")
        parts.append(f"Secondary Genre: {l2} (layer this flavor into the story — enhances but does not override primary)")
        if t2: parts.append(f"  Tone flavor: {t2}")
        if include_structure and s2:
            parts.append(f"  Additional beats: {s2}")
        if include_structure:
            parts.append(f"Blend rule: follow {l1} narrative arc; infuse {l2} energy/tension/tone throughout.")
    elif g2:
        label, tone, structure = g2
        parts.append(f"Story Genre: {label}")
        if tone:      parts.append(f"Tone & Atmosphere: {tone}")
        if include_structure and structure:
            parts.append(f"Narrative Structure: {structure}")

    return "\n".join(parts)

from .comfy_api  import comfy_queue, comfy_history

# ── Runtime-settable LLM parameters ──────────────────────────────────
_llm_tokens_groom:   int = 12288

_llm_tokens_outline: int = 6144

_llm_tokens_fill:    int = 8192

_llm_tokens_chat:    int = 8000    # Outline Chat — เดิม hardcode 8000 ฝั่ง Grok, ใช้ outline ฝั่ง local

_llm_tokens_story_a: int = 8192    # เพดานรอบ A (story pass) — 4096 ไม่พอเมื่อ events เยอะ:

#   output ถูกตัดกลางคัน แล้วฟิลด์ที่อยู่ท้ายสคีมา (เดิมคือ scene/props) หายเงียบ ๆ
_llm_timeout:        int = 120

# n_ctx: 0 = ใช้ค่าที่ workflow json ตั้งไว้ (ปลอดภัยเสมอ เพราะโมเดลถูกโหลดมาด้วย ctx เท่านั้นจริง ๆ)
# ตั้งค่า > 0 ได้เมื่อผู้ใช้โหลดโมเดลด้วย ctx ที่ใหญ่กว่าเอง — workflow json บางไฟล์ตั้งไว้แค่ 16384 (gemma4_llm)
# ซึ่งเป็น input+output รวมกัน พอ input เป็นภาษาไทย (กินโทเคนมากกว่าอังกฤษเท่าตัว) บวกกฎ ~26k ตัวอักษร
# ที่เหลือให้เขียน output จึงหดลงเรื่อย ๆ จนชุดท้าย ๆ เขียนได้แค่ช็อตเดียวแล้วช็อตหายไปเงียบ ๆ
# (เจอจริง: RAW หดจาก 7,495 ตัวอักษรในชุด 3 เหลือ 1,196 ในชุด 8 — ช็อตหาย 9 จาก 52)
_llm_n_ctx:          int = 0        # 0 = ไม่ทับค่าใน workflow (ค่าปลอดภัย)

_grok_temperature: float = 0.7

# ผู้ใช้พิมพ์เอง ต่อท้าย system prompt ของทุกคำขอ Grok (Groom/Outline/Fill/Checker/
# Director/Chat) — ใช้ที่เดียวใน _call_grok_api()/_grok_chat_completions() ไม่ใช่ Local
_grok_custom_sys:    str = ""

def set_llm_params(tokens_outline: int = None, tokens_fill: int = None,
                   timeout: int = None, grok_temperature: float = None,
                   tokens_groom: int = None,
                   tokens_chat: int = None, tokens_story_a: int = None,
                   n_ctx: int = None, grok_custom_sys: str = None):
    global _llm_tokens_groom, _llm_tokens_outline, _llm_tokens_fill, _llm_timeout, _grok_temperature
    global _llm_tokens_chat, _llm_tokens_story_a, _llm_n_ctx, _grok_custom_sys
    if tokens_groom    is not None: _llm_tokens_groom    = max(512, tokens_groom)
    if tokens_outline  is not None: _llm_tokens_outline  = max(512, tokens_outline)
    if tokens_fill     is not None: _llm_tokens_fill     = max(512, tokens_fill)
    if tokens_chat     is not None: _llm_tokens_chat     = max(512, tokens_chat)
    if tokens_story_a  is not None: _llm_tokens_story_a  = max(512, tokens_story_a)
    if n_ctx           is not None: _llm_n_ctx           = 0 if n_ctx <= 0 else max(4096, n_ctx)
    if timeout         is not None: _llm_timeout         = max(30, timeout)
    if grok_temperature is not None: _grok_temperature   = max(0.0, min(2.0, grok_temperature))
    if grok_custom_sys is not None: _grok_custom_sys     = grok_custom_sys

# ── Unified LLM Debug Mode (task3) ───────────────────────────────────
# Default OFF. When ON, every Local/Grok call in Groom/Outline/Fill/
# Checker/Director writes one JSON under debug/ and asks that SAME call for a
# short decision report. Debug-off must not change prompts, response format,
# call count, or write any debug files.
_llm_debug_logging: bool = False

_llm_debug_dir: Path | None = None

_llm_debug_meta: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "llm_debug_meta", default=None)

_llm_debug_last_path: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_debug_last_path", default=None)

_DEBUG_REPORT_START = "<<<DEBUG_DECISION_REPORT>>>"

_DEBUG_REPORT_END = "<<<END_DEBUG_DECISION_REPORT>>>"

_DEBUG_INSTRUCTION = (
    "\n\nAfter the complete normal output, append this short block. "
    "Do not put it inside JSON or the main output. One short line per field. "
    "Do not write chain-of-thought.\n"
    f"{_DEBUG_REPORT_START}\n"
    "understood_task: <what this stage should produce>\n"
    "key_decisions: <important choices you made>\n"
    "conflicts: <whole-ledger clothing audit from Shot 1 through the last shot — "
    "clothing token vs remaining garments, omitted inventory, garment both worn and placed, "
    "\"partially off\", or cast-count mismatch if header/lines/source disagree "
    "(e.g. source requires 6 men including Somchai, but current sheet has only 5 male lines); "
    "name the failing shot(s) (e.g. Shot 11 still has skirt both worn and placed on ground); "
    "or none after auditing clothing continuity from Shot 1 through the last shot — "
    "none only after clothing self-check of ALL shots and cast count both passed. "
    "Do not claim none from checking only the final shot>\n"
    "reason: <why this output>\n"
    f"{_DEBUG_REPORT_END}\n"
)

_DEBUG_REPORT_KEYS = ("understood_task", "key_decisions", "conflicts", "reason")

_NATIVE_THINKING_KEYS = ("reasoning_content", "reasoning", "thinking")

def set_llm_debug_logging(enabled: bool, debug_dir: str | Path | None = None):
    """One toggle for all stages/providers. debug_dir is for tests; production uses DEBUG_DIR."""
    global _llm_debug_logging, _llm_debug_dir
    _llm_debug_logging = bool(enabled)
    _llm_debug_dir = Path(debug_dir) if debug_dir else None

def _llm_debug_on() -> bool:
    return bool(_llm_debug_logging)

@contextmanager
def _llm_debug_scope(stage: str = "", **extra):
    """Attach stage / shot / batch ids to the current call. Nested scopes merge."""
    prev = _llm_debug_meta.get() or {}
    cur = dict(prev)
    if stage:
        cur["stage"] = stage
    for k, v in extra.items():
        if v is not None:
            cur[k] = v
    token = _llm_debug_meta.set(cur)
    try:
        yield
    finally:
        _llm_debug_meta.reset(token)

def _llm_debug_stage(stage: str):
    """Mark a pipeline entry point so every LLM call inside it is tagged."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            with _llm_debug_scope(stage):
                return await fn(*args, **kwargs)
        return wrapper
    return deco

def _apply_debug_instruction(user_p: str) -> str:
    if not _llm_debug_on():
        return user_p
    text = user_p or ""
    if _DEBUG_REPORT_START in text:
        return text
    # Outline Step 1/2 use JSON debug_final_check only — a trailing
    # DEBUG_DECISION_REPORT would make the raw response no longer one JSON object.
    meta = _llm_debug_meta.get() or {}
    if meta.get("outline_pass") in ("story", "shots"):
        return text
    if meta.get("skip_debug_decision_report"):
        return text
    return text + _DEBUG_INSTRUCTION

def _parse_debug_report_block(block: str) -> dict:
    out = {k: "" for k in _DEBUG_REPORT_KEYS}
    t = (block or "").strip()
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            if isinstance(obj, dict):
                for k in _DEBUG_REPORT_KEYS:
                    if k in obj and obj[k] is not None:
                        out[k] = str(obj[k]).strip()
                return out
        except Exception:
            pass
    current = None
    buf: list[str] = []

    def _flush():
        nonlocal current, buf
        if current:
            out[current] = " ".join(buf).strip()
        buf = []

    for line in t.splitlines():
        low = line.strip()
        key = None
        for k in _DEBUG_REPORT_KEYS:
            prefix = k + ":"
            if low.lower().startswith(prefix):
                key = k
                break
        if key:
            _flush()
            current = key
            buf = [low.split(":", 1)[1].strip()]
        elif current:
            buf.append(low)
    _flush()
    return out

def _split_debug_decision_report(text: str) -> tuple[str, dict | None]:
    """Return (main_output, report_or_None). Strips the tagged block from main_output."""
    if not text or _DEBUG_REPORT_START not in text:
        return text, None
    start = text.find(_DEBUG_REPORT_START)
    end = text.find(_DEBUG_REPORT_END, start)
    if end < 0:
        block = text[start + len(_DEBUG_REPORT_START):]
        main = text[:start].rstrip()
    else:
        block = text[start + len(_DEBUG_REPORT_START):end]
        main = (text[:start] + text[end + len(_DEBUG_REPORT_END):]).strip()
    report = _parse_debug_report_block(block)
    if not any(report.values()):
        report = {"raw": block.strip()}
    return main, report

def _extract_native_thinking(raw_response):
    """Copy provider thinking fields as returned. None if the provider did not expose any."""
    if not isinstance(raw_response, dict):
        return None
    hits = {}

    def consider(d):
        if not isinstance(d, dict):
            return
        for k in _NATIVE_THINKING_KEYS:
            if k in d and d[k] not in (None, "", [], {}):
                hits[k] = d[k]

    consider(raw_response)
    choices = raw_response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        c0 = choices[0]
        consider(c0)
        msg = c0.get("message")
        consider(msg if isinstance(msg, dict) else None)
    outputs = raw_response.get("outputs")
    if isinstance(outputs, dict):
        for nout in outputs.values():
            consider(nout)
    return hits or None

def _debug_jsonable(obj, _depth: int = 0):
    if _depth > 8:
        return str(obj)
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _debug_jsonable(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_debug_jsonable(v, _depth + 1) for v in obj]
    return str(obj)

def _debug_slug(s: str, fallback: str = "unknown") -> str:
    out = []
    for ch in (s or "").strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in "-_" and out and out[-1] not in "-_":
            out.append("-")
    slug = "".join(out).strip("-_")
    return slug or fallback

def _write_llm_debug_record(
    *,
    provider: str,
    model: str | None,
    system_prompt: str,
    user_prompt: str,
    parameters: dict,
    raw_response,
    raw_content: str | None,
    native_thinking,
    decision_report: dict | None,
    finish_reason,
    token_usage,
    elapsed_ms: float,
    call_id: str,
    error: str | None = None,
) -> str | None:
    """Write one debug JSON. Never raises. Returns the filename stem or None."""
    if not _llm_debug_on():
        return None
    try:
        meta = _llm_debug_meta.get() or {}
        stage = _debug_slug(str(meta.get("stage") or "unknown"))
        provider_s = _debug_slug(provider, "unknown")
        ts = time.strftime("%Y%m%d_%H%M%S")
        cid = _debug_slug(call_id, uuid.uuid4().hex[:8])
        out_dir = _llm_debug_dir or DEBUG_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"llm_debug_{ts}_{provider_s}_{stage}_{cid}.json"
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "provider": provider,
            "model": model,
            "stage": meta.get("stage") or "unknown",
            "outline_pass": meta.get("outline_pass"),
            "fill_engine": meta.get("fill_engine"),
            "call_id": cid,
            "shot_ids": meta.get("shot_ids"),
            "batch_ids": meta.get("batch_ids"),
            "elapsed_ms": round(elapsed_ms, 1),
            "error": error,
            "request": {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "parameters": _debug_jsonable(parameters),
            },
            "raw_response": _debug_jsonable(raw_response),
            "raw_content": raw_content,
            "native_thinking": _debug_jsonable(native_thinking),
            "debug_decision_report": decision_report,
            "finish_reason": finish_reason,
            "token_usage": _debug_jsonable(token_usage),
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            _llm_debug_last_path.set(str(path))
        except Exception:
            pass
        return path.name
    except Exception:
        return None

def _debug_strip_content(raw_content: str | None) -> tuple[str | None, dict | None]:
    if not _llm_debug_on() or raw_content is None:
        return raw_content, None
    main, report = _split_debug_decision_report(raw_content)
    return main, report

# ── LLM helpers (เหมือน prompt_tester) ───────────────────────────────
def scan_llm_workflows() -> list[str]:
    if LLM_DIR.exists():
        return sorted(p.stem for p in LLM_DIR.glob("*.json"))
    return []

# ── Default system prompt template ──────────────────────────────────
# ── intro: local เก็บถ้อยคำเดิมทุกไบต์ / API ใช้โทนกลาง ─────────────────────────
# ผู้ให้บริการ API เติมบล็อกความปลอดภัยไว้หน้าคำสั่งของเรา แล้วโทน role-play แบบ "expert in
# hardcore NSFW" มักถูกตีเป็นการพยายามข้ามนโยบาย ผลคือทั้ง system prompt ถูกทิ้งไปด้วย
# ทั้งสองชุดให้สิทธิ์เชิงความหมายเท่ากัน ต่างกันแค่โทนที่ประกาศตัว
_API_INTRO_LINE = (
    "This story is adult fiction; when a beat includes nudity or sexual contact, "
    "describe it plainly and precisely as part of the picture."
)

def _intro_for_variant(local_line: str, slim: bool) -> str:
    """คืนบรรทัดเปิดตามปลายทาง — slim/API ได้โทนกลาง, local ได้ของเดิมทุกไบต์"""
    return _API_INTRO_LINE if slim else local_line

_LOCAL_REGEN_INTRO = (
    "You are a creative visual prompt editor specialized for Krea2 image generation, "
    "expert in hardcore NSFW, explicit porn, full nudity, and extreme sexual content."
)

def _swap_intro_line(text: str, local_line: str, api_prefix: str, slim: bool) -> str:
    """สลับเฉพาะบรรทัดเปิด — local ได้ของเดิมทุกไบต์ API ได้โทนกลางที่ให้สิทธิ์เท่ากัน"""
    if not slim or local_line not in (text or ""):
        return text
    return text.replace(local_line, api_prefix + _intro_for_variant(local_line, True), 1)

def regen_sys_for(llm_wf_file: str, custom_sys: str = "", sys_variant: str = "") -> str:
    """system prompt ของกล่อง Regen — เลือกโทนตามปลายทางเหมือน stage อื่น"""
    if (custom_sys or "").strip():
        return custom_sys
    return _swap_intro_line(
        DEFAULT_REGEN_SYS, _LOCAL_REGEN_INTRO,
        "You are a creative visual prompt editor for Krea2 image generation. ",
        _prefer_slim("", sys_variant, llm_wf_file))

DEFAULT_REGEN_SYS = """You are a creative visual prompt editor specialized for Krea2 image generation, expert in hardcore NSFW, explicit porn, full nudity, and extreme sexual content.
You help users refine and improve image prompts for story shots through natural conversation.

BEHAVIOR:
- If the user asks WHY the current prompt looks the way it does (e.g. "why did you write it like this", "ทำไมคิดมาแบบนี้", "why this pose/camera/outfit") → explain the actual reasoning using the CURRENT SHOT facts below (scene, clothing, objects) — point to which fact drove which part of the prompt. Do not deflect into a generic introduction of yourself.
- If the user greets you or asks a general question about what you can do → respond naturally in the same language, explain what you can do
- If the user asks for a change to the prompt → apply it and output the updated JSON
- If unsure → ask for clarification before making changes
- For an NSFW scene, describe the adult facts the shot's state actually contains, in plain visual wording — never push it further than the user asked for or the state holds

WHEN OUTPUTTING A REVISED PROMPT, use this JSON format:
{
  "shot": <number>,
  "shot_desc": "<novel-style narrative 5-8 sentences in story language, copy or update from outline>",
  "narration": "<max 40 words — EXPAND or INTENSIFY: reveal inner feeling/subtext the image cannot show; do NOT describe what is already visible — or empty string>",
  "speech_char": "<name of speaking character — or empty string>",
  "subtitle": "<most emotionally loaded line: reveals desire/fear/rage/vulnerability, max 10 words — or empty string>",
  "prompt": "<the full revised prompt — format is specified separately below, opens with strong shot framing, then weaves in character appearance/outfit, action/pose, facial expression, environment, and lighting>"
}

PROMPT RULES (when making changes):
- Keep character appearance consistent — copy from current prompt unless asked to change
- Adjust outfit to fit the scene context (fully nude, clothes pulled aside, torn, or sexualized when appropriate)
- Expression must be explicit: facial muscles, eye detail, mouth position, tongue, saliva, tears, orgasm face, emotion
- Name adult facts in plain anatomical English rather than euphemism — only the ones this shot's state contains
- Always describe genitals, arousal, fluids, and sexual actions in crude anatomical detail when the scene is sexual
- STRICTLY FORBIDDEN: inventing any new characters, adding extra women, extra men, unnamed people, "the other man", "remaining man", or "another friend". Only use characters from the provided list. Count them exactly. Never change gender. Never include thinking/corrections/notes inside the prompt.
- CLOTHING CONTINUITY — MID-ACT RULE: within the same continuous undress/sex act, clothing only moves forward, never magically re-dresses mid-act. A scene break does not re-dress anyone by itself — a time skip or new place may come with a clothing change, but the change must be something the story does, shown in shot_desc"standing around", "standing near", "positioned nearby", "observing".
- OPENING RULE (Krea2 CRITICAL): the prompt MUST open with the camera framing/shot type as the very first thing described, since Krea2 weights early tokens heavily. For a penetrative sex or oral shot, open with a strong full-body or wide shot that clearly shows the entire bodies from head to toe and the sexual act. STRICTLY FORBIDDEN during a penetrative sex or oral shot specifically: "close-up", "tight shot", "focus on face", "portrait", "headshot", "from the waist up", "from mid-thigh up", "upper body", "waist-up", "torso only". Must explicitly say the full bodies and sexual act are clearly visible in frame. For every other shot type (undressing, touching, an emotional beat, a detail), these words are normal and expected — do not force a wide/full-body shot there.
- Always output the FULL revised prompt, not just the changed part

You can respond in Thai or English depending on what the user uses."""

# ── Loop-based LLM story (two-pass: outline → full prompt) ──────────
async def _call_llm_node(wf_template: dict, system_prompt: str,
                          user_text: str, loop, cid: str,
                          max_tokens: int = 8192, on_log=None,
                          n_ctx: int | None = None,
                          temperature: float | None = None,
                          session_id: str | None = None,
                          reset_session: bool = True) -> str | None:
    """reset_session (B-SELF-AUDIT-A2): True (default, every existing caller) starts the session fresh
    on each call; False continues the persistent ComfyUI session `session_id` so only the NEW user
    message needs to be sent.
    Call LLM node หนึ่งครั้ง คืน raw text output
    n_ctx=None = ใช้ค่าใน workflow ตามที่โหลดโมเดลไว้ (เช่น 12288) — ห้ามทับเป็น 32768
    เพราะโมเดลที่โหลดด้วย ctx สั้นกว่าจะ queue ไม่ขึ้นหรือพังตอนรัน
    session_id=None (ปกติ) = สุ่มใหม่ทุกครั้งเหมือนเดิม ผู้เรียกที่อยากรู้ session ที่ใช้จริง
    (เช่น Fill ที่จะเก็บไว้ให้ chat ต่อทีหลัง) ส่งค่ามาเองได้"""
    user_text = _apply_debug_instruction(user_text)
    t0 = time.time()
    cid = cid or uuid.uuid4().hex
    if on_log:
        await on_log(f"📋 SYSTEM PROMPT:\n{system_prompt}")
        await on_log(f"📤 USER:\n{user_text}")
    import copy
    wf = copy.deepcopy(wf_template) if wf_template else {}
    _hit = False
    local_model = None
    node_params = {
        "max_tokens": max_tokens,
        "n_ctx": n_ctx if n_ctx is not None else _llm_n_ctx,
        "temperature": temperature,
    }
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        if node.get("class_type") == "LLMSessionChatNode":
            wf[nid]["inputs"]["user_text"]     = user_text
            wf[nid]["inputs"]["system_prompt"] = system_prompt
            wf[nid]["inputs"]["session_id"]    = session_id or uuid.uuid4().hex[:8]
            wf[nid]["inputs"]["reset_session"] = bool(reset_session)
            wf[nid]["inputs"]["max_tokens"]    = max_tokens
            # n_ctx: ทับได้ก็ต่อเมื่อผู้ใช้ตั้งค่าไว้เอง (_llm_n_ctx > 0) — ค่าเริ่มต้น 0 = ปล่อยตาม
            # workflow เพราะโมเดลที่โหลดด้วย ctx สั้นกว่าจะ queue ไม่ขึ้นหรือพังตอนรัน
            _nc = n_ctx if n_ctx is not None else _llm_n_ctx
            _overrides.apply(wf[nid]["inputs"], n_ctx=(_nc if _nc and _nc > 0 else None), temperature=temperature)      # Program mode: the Settings / caller values; Workflow mode: the workflow's own
            local_model = (wf[nid].get("inputs") or {}).get("model")
            node_params = {
                "max_tokens": wf[nid]["inputs"].get("max_tokens"),
                "n_ctx": wf[nid]["inputs"].get("n_ctx"),
                "temperature": wf[nid]["inputs"].get("temperature"),
            }
            _hit = True
    raw_txt = None
    last_hist = None
    last_status = None
    err = None
    if not _hit:
        if on_log:
            await on_log("❌ workflow ไม่มี LLMSessionChatNode — ไม่ได้ส่งเข้า Comfy")
        err = "no LLMSessionChatNode"
    else:
        try:
            pid = await loop.run_in_executor(None, comfy_queue, wf, cid)
            if on_log: await on_log(f"✅ Queued pid={pid[:8]}")
        except Exception as e:
            if on_log: await on_log(f"❌ Queue: {e}")
            err = f"queue: {e}"
            pid = None
        if pid:
            deadline = time.time() + _llm_timeout
            while time.time() < deadline:
                await asyncio.sleep(2)
                try:
                    hist = await loop.run_in_executor(None, comfy_history, pid)
                except Exception:
                    continue
                if pid not in hist: continue
                last_hist = hist[pid]
                last_status = last_hist.get("status", {}).get("status_str", "")
                for nout in last_hist.get("outputs", {}).values():
                    for txt in nout.get("text", []):
                        if txt and isinstance(txt, str):
                            raw_txt = txt
                            break
                    if raw_txt:
                        break
                if raw_txt:
                    break
                if last_status in ("success", "error"):
                    break
            if raw_txt is None and err is None:
                err = last_status or "timeout"
    if _llm_debug_on():
        _stripped, report = _debug_strip_content(raw_txt)
        fname = _write_llm_debug_record(
            provider="local",
            model=local_model,
            system_prompt=system_prompt,
            user_prompt=user_text,
            parameters=node_params,
            raw_response=last_hist,
            raw_content=raw_txt,
            native_thinking=_extract_native_thinking(last_hist) if last_hist else None,
            decision_report=report,
            finish_reason=last_status,
            token_usage=(last_hist or {}).get("usage") if isinstance(last_hist, dict) else None,
            elapsed_ms=(time.time() - t0) * 1000,
            call_id=cid[:8] if cid else uuid.uuid4().hex[:8],
            error=err,
        )
        if fname and on_log:
            try:
                await on_log(f"🛠 LLM debug → {fname}")
            except Exception:
                pass
        return _stripped
    return raw_txt

GROK_MARKER = "[Grok API]"   # ต้องตรงกับ _GROK_OPTION ใน main_ui.py — ใช้เทียบว่าเลือก backend ไหน

# grok-4-latest now aliases a reasoning flagship (4.5/4.6). Unset effort
# defaults to "high" — that is the main reason outline/fill via Grok feels stuck.
_GROK_REASONING_EFFORT = "low"

def _grok_usage_line(resp: dict, elapsed_s: float, max_tokens: int, effort: str | None) -> str:
    usage = resp.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    served = resp.get("model") or ""
    # xAI ไม่มี session ฝั่ง server — ทุก call ต้องส่ง system prompt ซ้ำทั้งก้อน แต่ prefix ที่ซ้ำ
    # อาจโดน automatic prompt caching กินไปแล้ว ถ้าไม่ log ตัวเลขนี้ก็เดาไม่ออกว่าการส่งซ้ำ
    # แพงจริงแค่ไหน (cached ยิ่งเยอะ = การส่ง system prompt ซ้ำยิ่งแทบไม่มีต้นทุน)
    p_details = usage.get("prompt_tokens_details") or {}
    cached = p_details.get("cached_tokens")
    prompt_tok = usage.get("prompt_tokens")
    if isinstance(cached, int) and isinstance(prompt_tok, int) and prompt_tok > 0:
        cache_str = f"  cached={cached} ({100 * cached / prompt_tok:.0f}% ของ prompt)"
    elif cached is not None:
        cache_str = f"  cached={cached}"
    else:
        cache_str = "  cached=n/a"
    return (
        f"⏱ Grok {elapsed_s:.1f}s  model={served or '?'}  "
        f"prompt={usage.get('prompt_tokens', '?')}  "
        f"out={usage.get('completion_tokens', '?')}  "
        f"reasoning={details.get('reasoning_tokens', '?')}"
        f"{cache_str}  "
        f"max_tokens={max_tokens}  effort={effort or 'default'}"
    )

async def _grok_chat_completions(messages: list[dict], max_tokens: int,
                                 api_key: str, model: str,
                                 on_log=None, temperature: float | None = None) -> dict | None:
    """POST /v1/chat/completions. Returns the raw response dict, or None."""
    import urllib.request, urllib.error
    temp = _grok_temperature if temperature is None else temperature
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temp,
        "reasoning_effort": _GROK_REASONING_EFFORT,
    }
    loop = asyncio.get_event_loop()

    def _post(body: dict):
        req = urllib.request.Request(
            "https://api.x.ai/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=max(_llm_timeout, 60)) as r:
            return json.loads(r.read())

    t0 = time.time()
    effort_used = _GROK_REASONING_EFFORT
    try:
        resp = await loop.run_in_executor(None, _post, payload)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        # Older aliases reject reasoning_effort — retry once without it.
        if e.code == 400 and "reasoning" in body.lower():
            payload.pop("reasoning_effort", None)
            effort_used = None
            if on_log:
                await on_log("⚠ Grok rejected reasoning_effort — retrying without it")
            try:
                resp = await loop.run_in_executor(None, _post, payload)
            except urllib.error.HTTPError as e2:
                body2 = e2.read().decode()
                if on_log: await on_log(f"❌ HTTP {e2.code}: {body2[:300]}")
                return None
            except Exception as e2:
                if on_log: await on_log(f"❌ Error: {e2}")
                return None
        else:
            if on_log: await on_log(f"❌ HTTP {e.code}: {body[:300]}")
            return None
    except Exception as e:
        if on_log: await on_log(f"❌ Error: {e}")
        return None

    if on_log:
        await on_log(_grok_usage_line(resp, time.time() - t0, max_tokens, effort_used))
    return resp

async def _call_grok_api(sys_p: str, user_p: str, max_tokens: int,
                          api_key: str, model: str = "grok-4-latest", on_log=None,
                          temperature: float | None = None,
                          extra_messages: list | None = None) -> str | None:
    """เรียก Grok API แบบ one-shot (ไม่มี session) — ใช้ sys/user prompt เดียวกับที่ local LLM ใช้
    extra_messages = follow-up turns after the original user message (assistant + repair user)."""
    if not api_key:
        if on_log: await on_log("❌ Grok API key ว่าง — ใส่ก่อนใน Settings")
        return None
    user_p = _apply_debug_instruction(user_p)
    if _grok_custom_sys.strip():
        sys_p = sys_p + "\n\n" + _grok_custom_sys.strip()
    t0 = time.time()
    temp = _grok_temperature if temperature is None else temperature
    messages = [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}]
    if extra_messages:
        messages.extend(extra_messages)
    if on_log:
        await on_log(f"📤 Grok API → model={model}  max_tokens={max_tokens}  effort={_GROK_REASONING_EFFORT}"
                     + (f"  followup={len(extra_messages)} msgs" if extra_messages else ""))
        await on_log(f"📋 SYSTEM PROMPT:\n{sys_p}")
        await on_log(f"📤 USER TEXT:\n{user_p}")
        if extra_messages:
            await on_log(f"📤 FOLLOW-UP ({len(extra_messages)}):\n{extra_messages[-1].get('content','')[:1200]}")
    resp = await _grok_chat_completions(
        messages,
        max_tokens, api_key, model, on_log=on_log, temperature=temperature,
    )
    _choice = (resp.get("choices") or [{}])[0] if isinstance(resp, dict) else {}
    if not isinstance(_choice, dict):
        _choice = {}
    raw = (_choice.get("message") or {}).get("content", "")
    if _llm_debug_on():
        _stripped, report = _debug_strip_content(raw if resp is not None else None)
        fname = _write_llm_debug_record(
            provider="grok",
            model=(resp.get("model") if isinstance(resp, dict) else None) or model,
            system_prompt=sys_p,
            user_prompt=user_p,
            parameters={
                "max_tokens": max_tokens,
                "temperature": temp,
                "reasoning_effort": _GROK_REASONING_EFFORT,
            },
            raw_response=resp,
            raw_content=raw if resp is not None else None,
            native_thinking=_extract_native_thinking(resp) if resp else None,
            decision_report=report,
            finish_reason=_choice.get("finish_reason"),
            token_usage=resp.get("usage") if isinstance(resp, dict) else None,
            elapsed_ms=(time.time() - t0) * 1000,
            call_id=uuid.uuid4().hex[:8],
            error=None if resp is not None else "no response",
        )
        if fname and on_log:
            try:
                await on_log(f"🛠 LLM debug → {fname}")
            except Exception:
                pass
    if resp is None:
        return None
    # งบ output หมดกลางคัน — ไม่มีใครเช็คมาก่อน ผลคือ response ที่ถูกตัดเห็นเป็นแค่ "ช็อตหาย"
    # เฉย ๆ เดาสาเหตุไม่ได้เลย ยิ่งตอน one-call ที่ไม่มี recovery มาเก็บช็อตที่หาย การรู้ว่า
    # "หายเพราะงบหมด" กับ "หายเพราะโมเดลข้ามเอง" ต่างกันคนละเรื่องในการแก้
    if _choice.get("finish_reason") == "length" and on_log:
        _usage = resp.get("usage") or {}
        _reason_tok = (_usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        await on_log(f"⚠ Grok: คำตอบถูกตัดกลางคันเพราะชนเพดาน max_tokens={max_tokens} "
                     f"(ใช้ไป out={_usage.get('completion_tokens', '?')} "
                     f"ในนั้นเป็น reasoning={_reason_tok if _reason_tok is not None else '?'}) "
                     f"— ช็อตท้าย ๆ ที่หายไปเกิดจากสาเหตุนี้ ไม่ใช่โมเดลข้ามเอง")
    if on_log: await on_log(f"RAW (Grok):\n{raw}")
    if _llm_debug_on():
        return _stripped
    return raw

async def _call_llm_generic(llm_wf_file: str, wf_template: dict | None, sys_p: str, user_p: str,
                             loop, max_tokens: int, on_log=None,
                             grok_api_key: str = "", grok_model: str = "grok-4-latest",
                             temperature: float | None = None,
                             n_ctx: int | None = None,
                             extra_messages: list | None = None,
                             session_id: str | None = None,
                             reset_session: bool = True) -> str | None:
    """reset_session=False (B-SELF-AUDIT-A2, Local only): continue the existing `session_id` session —
    the caller then passes ONLY the new message as user_p and no extra_messages (nothing is repacked).
    dispatch ไปที่ local LLM (ComfyUI) หรือ Grok API ตัวเดียวกัน — ใช้ sys_p/user_p ชุดเดียวกันทั้งคู่
    extra_messages: Grok continues the same conversation; local packs equivalent context.
    session_id: local เท่านั้น (Grok เป็น stateless call เดี่ยวใน _call_grok_api ไม่มี session) —
    ผู้เรียกที่อยากรู้ session ที่ใช้จริง (เช่น Fill เก็บไว้ให้ปุ่ม chat ต่อทีหลัง) ส่งมาเองได้ ไม่ส่ง = สุ่มใหม่"""
    if llm_wf_file == GROK_MARKER:
        return await _call_grok_api(sys_p, user_p, max_tokens, grok_api_key, grok_model,
                                    on_log=on_log, temperature=temperature,
                                    extra_messages=extra_messages)
    packed = user_p
    if extra_messages:
        bits = [user_p]
        for m in extra_messages:
            role = (m or {}).get("role") or "user"
            content = (m or {}).get("content") or ""
            if role == "assistant":
                bits.append("CURRENT OUTPUT:\n" + content)
            else:
                bits.append(content)
        packed = "\n\n".join(bits)
    return await _call_llm_node(wf_template, sys_p, packed, loop, uuid.uuid4().hex,
                                 max_tokens=max_tokens, on_log=on_log, temperature=temperature,
                                 n_ctx=n_ctx, session_id=session_id, reset_session=reset_session)

# ── Grok batch plumbing (Fill/Checker/Director) ──────────────────────────────
# local model วิ่งทีละช็อตเหมือนเดิมเสมอ — ก้อนนี้ใช้เฉพาะ backend Grok ที่ context ใหญ่พอจะ
# เห็นหลายช็อตพร้อมกัน จุดประสงค์เดียว: ลดจำนวนครั้งที่ system prompt (character sheet, story,
# กฎทั้งชุด) ถูกส่งซ้ำ ไม่ใช่เขียนกฎชุดใหม่ — sys prompt ยังมาจาก _fill_sys_for/_editor_sys_for/
# _director_sys_for ตัวเดิมเป๊ะ แค่เรียกครั้งเดียวแล้วยัดหลายช็อตลง user message เดียว
#
# โหมดที่ Grok ใช้จริงคือ one_call: ทุกช็อตของ stage อยู่ในคำขอเดียว ไม่ chunk ไม่จัดกลุ่มตาม sys
# prompt ไม่มี recovery call (ดู _prefetch_grok_batch) — ค่า chunk ข้างล่างเหลือไว้ให้ผู้เรียกที่ไม่ได้
# ขอ one_call เท่านั้น
_GROK_BATCH_CHUNK_SHOTS = 6

# canonical-identity (age/nationality/sex/permanent identity บังคับต่อตัวละครใน IDENTITY LOCK) +
# บังคับแปลเป็นอังกฤษล้วน แต่ละช็อตยาวขึ้นจริง โดยเฉพาะช็อตที่มีหลายตัวละคร ของเดิม 2600 ทำให้
# response ของทั้งคำขอ (one-call, ไม่มี recovery) โดนตัดกลางคันแล้วช็อตท้าย ๆ หายทั้งแถบ
# Checker ไม่ได้คืนแค่ผลตรวจ — schema ของมันคือ "prompt": <prompt ฉบับแก้เต็มทั้งอัน> เท่ากับ
# output ของ Fill ทั้งก้อน บวก issues + reason + JSON overhead อีก งบจึงต้องไม่น้อยกว่าของ Fill
# (เดิมตั้งไว้ 900 เพราะเข้าใจผิดว่าส่วนใหญ่ตอบ keep_original:true แบบสั้น — แต่เคสนั้นก็ยังต้อง
# copy prompt เต็มกลับมา) วัดจาก 715 ช็อตในไฟล์เรื่องจริง: prompt median ~1.4k ตัวอักษร p90 ~2.3k
# ยาวสุด ~4.4k = ราว 350/750/1500 token ช็อตเดียว 900 จึงเฉียดเพดานตั้งแต่ p90
_llm_tokens_check_grok_batch:    int = 3000   # ต่อช็อต (prompt เต็ม + issues + reason)

# Director คืน schema เดียวกับ Checker เป๊ะ (prompt เต็ม + keep_original/issues/reason + copy
# scene_desc/narration/speech_char/speech_text/dialogue_position) จึงต้องได้งบเท่ากัน ไม่ใช่เท่า Fill
# ที่คืน prompt ล้วน — วัดจาก 715 ช็อตจริง: ผลรวมที่ต้องคืนต่อช็อต p90 ~2.6k ตัวอักษร ยาวสุด ~4.9k
_llm_tokens_director_grok_batch: int = 3000   # ต่อช็อต (prompt เต็ม + issues + reason + copy fields)

# เพดานของทั้งคำขอ — กันไม่ให้เรื่องยาวมากขอ output เกินที่ API ยอมรับจนคำขอถูกปฏิเสธทั้งก้อน
# ชนเพดานแล้ว "ไม่" หั่นเป็นหลายคำขอ (จะผิด one-call invariant) แค่ขอเท่าเพดานแล้วเตือนใน log
_GROK_MAX_OUTPUT_TOKENS = 100_000

# xAI นับ reasoning token เป็น completion token ด้วย = มันกิน max_tokens ร่วมกับคำตอบจริง
# (ดู reasoning_tokens ใน _grok_usage_line) และ reasoning ไม่ได้โตตามจำนวนช็อตแบบเชิงเส้น
# มีส่วนที่เป็นค่าคงที่ต่อคำขอ สูตร per_shot x n จึงไม่มีที่เผื่อให้มันเลย — บวกก้อนคงที่ไว้
# ครอบ reasoning + เปลือก JSON ของ batch ({"shots":[...]}, escaping, ชื่อ field ซ้ำทุกช็อต)
_GROK_BATCH_HEADROOM_TOKENS = 4000

def _grok_batch_budget(per_shot: int, n_shots: int) -> int:
    """งบ output ของ "ทั้งคำขอ" = งบต่อช็อต x จำนวนช็อต + ที่เผื่อ reasoning/JSON ตัดที่เพดาน API"""
    return min(max(1, n_shots) * per_shot + _GROK_BATCH_HEADROOM_TOKENS, _GROK_MAX_OUTPUT_TOKENS)

# ── Fill engine "Simple V2" ──────────────────────────────────────────────────
# ทางเลือกที่สองของ Fill ที่มาจากผลการทดลอง A/B: แทนที่กฎยาว ~50-60k ตัวอักษรของ Legacy ด้วย
# คำสั่งสั้นที่บอก "เจตนา" อย่างเดียว แล้วปล่อยให้โมเดล reasoning เอง — Legacy ไม่ถูกแตะเลย
# ทั้งสองทางใช้ user payload ก้อนเดียวกันเป๊ะ เพื่อให้เปลี่ยนตัวแปรเดียวคือขนาด system prompt
FILL_ENGINE_LEGACY = "legacy"

FILL_ENGINE_SIMPLE_V2 = "simple_v2"

FILL_ENGINE_MINIMAL_V3 = "minimal_v3"

# FILL-VNEXT-LOCK-CONTRACT-A1: the only Fill engine actually reachable today (run_fill_prompts /
# Minimal V3's own generator were removed earlier this session). run_fill_vnext now writes its own
# native IDENTITY LOCK / LOCATION LOCK contract (see _build_fill_vnext_form) and stamps this value
# onto shot["_fill_engine"] so Checker/Director/vision-check know a real lock-bearing prompt is
# downstream — never FILL_ENGINE_MINIMAL_V3, which would claim a generator that never ran.
FILL_ENGINE_VNEXT = "vnext"

_FILL_ENGINE_ALIASES = {
    "legacy": FILL_ENGINE_LEGACY,
    "simple_v2": FILL_ENGINE_SIMPLE_V2,
    "simplev2": FILL_ENGINE_SIMPLE_V2,
    "v2": FILL_ENGINE_SIMPLE_V2,
    "minimal_v3": FILL_ENGINE_MINIMAL_V3,
    "minimalv3": FILL_ENGINE_MINIMAL_V3,
    "v3": FILL_ENGINE_MINIMAL_V3,
    "vnext": FILL_ENGINE_VNEXT,
}

# Fill engines whose prompts carry the IDENTITY LOCK / LOCATION LOCK contract — Checker, Director
# and vision-check all gate their lock-aware behavior on membership here instead of a single literal
# comparison to FILL_ENGINE_MINIMAL_V3, so vNext-produced prompts get the same downstream protection
# Minimal V3 prompts always did (Minimal V3's own generator is unreachable now, but the constant and
# its downstream rule text stay in case it's ever revived).
_LOCK_CONTRACT_ENGINES = (FILL_ENGINE_MINIMAL_V3, FILL_ENGINE_VNEXT)

# ── sys_variant routing ──────────────────────────────────────────────────────
# sys_variant คุม "ขนาดของ system prompt" อย่างเดียว ห้ามไปแตะ fill_engine ซึ่งคุม "สัญญาเชิง
# พฤติกรรม" ของ Fill — สองแกนนี้ต้องแยกกันเด็ดขาด ไม่งั้นการเปลี่ยน backend จะเปลี่ยนความหมาย
# ของ prompt ไปด้วยโดยไม่มีใครสั่ง
#
# หมายเหตุการตัดสินใจ (intentional): Grok/API + auto = slim โดยตั้งใจ เพราะโมเดล API ทำตาม
# สัญญาสั้นได้ดีและคิดเงินตาม token ส่วน local + auto = full เหมือนเดิมทุกไบต์
SYS_VARIANT_FULL = "full"

SYS_VARIANT_SLIM = "slim"

def _prefer_slim(grok_api_key: str, override: str = "", llm_wf_file: str = "") -> bool:
    """เลือก slim ไหม — override ชนะ auto เสมอ

    override: "" = auto (slim เมื่อวิ่งผ่าน API), "full"/"slim" = บังคับ
    llm_wf_file: ส่งมาเมื่อผู้เรียกรู้ backend จริง — เป็นสัญญาณที่แม่นกว่า grok_api_key
    เพราะผู้ใช้ที่เซฟคีย์ Grok ไว้แต่รันด้วย Comfy ต้องได้ full เหมือนเดิม
    """
    o = (override or "").strip().lower()
    if o == SYS_VARIANT_SLIM:
        return True
    if o == SYS_VARIANT_FULL:
        return False
    if (llm_wf_file or "").strip():
        return llm_wf_file == GROK_MARKER
    return bool((grok_api_key or "").strip())

def _sys_variant_of(grok_api_key: str, override: str = "", llm_wf_file: str = "") -> tuple:
    """คืน (variant, reason) ไว้ log ให้เห็นว่าใครเป็นคนเลือก"""
    slim = _prefer_slim(grok_api_key, override, llm_wf_file)
    o = (override or "").strip().lower()
    reason = "explicit" if o in (SYS_VARIANT_FULL, SYS_VARIANT_SLIM) else "auto"
    return (SYS_VARIANT_SLIM if slim else SYS_VARIANT_FULL), reason

def _sys_metrics_line(stage: str, variant: str, reason: str, sys_p: str,
                      retries: int = 0, repairs: int = 0) -> str:
    """บรรทัดวัดผลมาตรฐานของทุก stage — token เป็นค่าประมาณจาก chars/4"""
    _c = len(sys_p or "")
    return (f"⚙ {stage}: variant={variant} ({reason}) sys={_c/1000:.1f}k chars "
            f"≈{_c // 4} tok retries={retries} repairs={repairs}")

def normalize_fill_engine(value: str | None) -> str:
    """Map UI/saved/alias values to a known engine. Empty or unknown → Legacy."""
    raw = (value or "").strip()
    if not raw:
        return FILL_ENGINE_LEGACY
    key = raw.lower().replace("-", "_").replace(" ", "_")
    return _FILL_ENGINE_ALIASES.get(key, FILL_ENGINE_LEGACY)

def fill_engine_label(engine: str | None) -> str:
    e = normalize_fill_engine(engine)
    if e == FILL_ENGINE_VNEXT:
        return "vNext"
    if e == FILL_ENGINE_MINIMAL_V3:
        return "Minimal V3"
    if e == FILL_ENGINE_SIMPLE_V2:
        return "Simple V2"
    return "Legacy"

# IDENTITY LOCK เป็นชั้นผูกตัวตน ไม่ใช่ฟอร์แมต — ต้องมีทุกช็อตที่มีตัวละครที่มีชื่อ แม้ช็อตเดี่ยว
# เพราะสิ่งที่หายบ่อยที่สุดคือของที่ "เห็นชัดจนไม่มีใครคิดว่าจะหาย": เพศ ทรงผม แขนกล ชุดของใคร
_V3_IDENTITY_LOCK_BLOCK = (
    "IDENTITY LOCK — REQUIRED whenever a named character is visible, solo shots included.\n"
    "  One line per visible person: Name = WOMAN|MAN, age when known, short permanent look, "
    "any permanent body fact that changes the silhouette (mechanical arms, prosthetic leg, "
    "missing limb, wings, tail, horns, extra arms, major implant).\n"
    "  Clothing: only for people clothing_state actually dresses. Missing = omit, not invent — "
    "no outfit, no placeholder, no borrowing another person's garments.\n"
    "  It is a REAL BLOCK: the literal line \"IDENTITY LOCK:\" on its own line, then one line per "
    "person. Never inline it into a sentence or a tag run, and never place it at the end of the "
    "prompt.\n"
    "  Shape of one line: the person's own name, an equals sign, WOMAN or MAN, then a comma list "
    "of their age (when known), short permanent look, and — only when this shot dresses them — "
    "their current garments.\n"
    "  No action, no pose, no expression inside the lock. Names come from VISIBLE PEOPLE only; "
    "look up each one's own appearance in the CHARACTER SHEET below.\n"
)

# LOCATION LOCK — renderer-facing anchor for WHERE this image currently is, same role as
# IDENTITY LOCK for WHO. Written once per prompt (once per page in manga mode). This block is
# the structural/placement half; the semantic content rules live in the vNext Fill/Checker/
# Director prompt builders that assemble the CHARACTER SHEET / PROPS section around it.
_V3_LOCATION_LOCK_BLOCK = (
    "LOCATION LOCK -- REQUIRED exactly once (once per page in page mode): a labeled block naming "
    "only WHERE the image currently is -- current place identity, plus a current support surface "
    "or one or two established fixed anchors only when useful. Normally ONE compact line.\n"
    "  It is a REAL BLOCK: the literal line \"LOCATION LOCK:\" on its own line, immediately after "
    "IDENTITY LOCK, then the compact location line. Never inline it into a sentence or a tag "
    "run.\n"
    "  No people, no crowd, no cast, no action, no weather/light invention inside it -- those "
    "belong elsewhere. Never the surface alone when an actual place identity is supplied (a "
    "market on sand stays \"open-air market on sand\", never just \"sand\").\n"
)

# task3 patch (sample cleanup): a fenced fake example still gave a small local model concrete
# words to copy (names, counts, furniture, a bracket placeholder) — every leak found this session
# traced back to the model treating some piece of example content as literal. No content-bearing
# sample survives that; structure is stated in prose only, nothing to copy.
# format-drift patch: prose that DESCRIBED whitespace ("a blank line, then X") was itself a
# copyable phrase — Local started literally writing "blank line" / "empty line" into prompts.
# Layout is now shown by an actual diagram with real line breaks below, using only abstract
# <angle-bracket> placeholders (no names/counts/content) plus the one literal required string
# "IDENTITY LOCK:" — nothing in the diagram is prose a model could mistake for content to copy.
_V3_SHAPE_NARRATIVE = (
    "  REQUIRED SHAPE, in order, nothing before or after it:\n"
    "  1. One opening line: THIS shot's own shot_scale value written out as words, then the "
    "people count.\n"
    "  2. The IDENTITY LOCK block exactly as specified above.\n"
    "  3. The LOCATION LOCK block exactly as specified above.\n"
    "  4. ONE coherent paragraph of flowing grammatical prose, with each described action "
    "attached to its stated subject, covering the shot's action, place, and light — no "
    "section labels inside it.\n"
    "  Layout (each line below is its own line in your reply; the gaps between rows are real):\n"
    "  <opening line>\n"
    "\n"
    "  IDENTITY LOCK:\n"
    "  <one line per person>\n"
    "\n"
    "  LOCATION LOCK:\n"
    "  <compact current location>\n"
    "\n"
    "  <narrative paragraph>\n"
)

_V3_SHAPE_TAG = (
    "  REQUIRED SHAPE, in order, nothing before or after it:\n"
    "  1. Opening tags: THIS shot's own shot_scale value written out as words, then the people "
    "count.\n"
    "  2. The IDENTITY LOCK block exactly as specified above.\n"
    "  3. The LOCATION LOCK block exactly as specified above.\n"
    "  4. The spatial core: one or two short grammatical sentences, not tags.\n"
    "  5. The remaining facts as one comma-separated run of short tags — not one tag per "
    "source field. No additional prose sentences, no section labels anywhere outside the "
    "lock.\n"
    "  Layout (each line below is its own line in your reply; the gaps between rows are real):\n"
    "  <opening tags>\n"
    "\n"
    "  IDENTITY LOCK:\n"
    "  <one line per person>\n"
    "\n"
    "  LOCATION LOCK:\n"
    "  <compact current location>\n"
    "\n"
    "  <spatial core sentence(s)>\n"
    "\n"
    "  <comma-separated tags>\n"
)

_V3_SHAPE_SECTIONED = (
    "  REQUIRED SHAPE: labeled sections, each its own line followed by its content, in this "
    "exact order: CAMERA, IDENTITY LOCK, LOCATION LOCK, SUBJECT, ENVIRONMENT, LIGHTING. Add "
    "CLIMAX after LIGHTING only when the current shot names a focal beat. Nothing before CAMERA "
    "or after the last section present.\n"
    "  Layout (each line below is its own line in your reply; the gaps between rows are real):\n"
    "  CAMERA: <framing and people count>\n"
    "\n"
    "  IDENTITY LOCK:\n"
    "  <one line per person>\n"
    "\n"
    "  LOCATION LOCK:\n"
    "  <compact current location>\n"
    "\n"
    "  SUBJECT: <action and spatial relation>\n"
    "\n"
    "  ENVIRONMENT: <place and atmosphere>\n"
    "\n"
    "  LIGHTING: <supplied light>\n"
)

def _minimal_v3_format(prompt_style: str, manga_page: bool = False) -> str:
    """Output contract only — identity binding is required, prose invention is not."""
    if manga_page:
        return (
            "PANEL PAGE (multi-panel page): copy the PAGE PLAN header, then ONE page-wide "
            "IDENTITY LOCK, then ONE page-wide LOCATION LOCK, then one paragraph per panel. Each "
            "panel's paragraph MUST contain, "
            "as its own explicit clauses — never left to be inferred from panel role, "
            "[ESTABLISHING]-style tags, page-region/percentage, or panel shape — BOTH: what "
            "dominates that panel's own composition (with its substance), and how the subject "
            "reads in size/distance relative to that same panel's frame/camera. When a "
            "camera_occupancy fact is supplied for a panel, serialize it as given — never "
            "re-resolve or invert it, and never leave it as page-plan context only. Occupancy "
            "is never phrased as subject-to-environment (\"small within a vast setting\") — "
            "always subject-to-panel-frame/camera. Subject may lead instead when performance "
            "dominates that panel. Identity is established once for the page and never "
            "redefined per panel; clothing once. Location is likewise established once for the "
            "page and never repeated inside each panel's paragraph.\n"
            + _V3_IDENTITY_LOCK_BLOCK
            + _V3_LOCATION_LOCK_BLOCK
            + _PAGE_STYLE_NEUTRAL_RULE
        )
    st = (prompt_style or "narrative").strip().lower()
    if st == "sectioned":
        st = "sectioned_detailed"
    if st == "tag_style":
        return (
            "tag_style: FOUR parts in this exact order.\n"
            "  1. Opening tags: write out THIS shot's own shot_scale value as real words (if "
            "shot_scale is \"medium shot\", the prompt starts \"medium shot,\" — never the literal "
            "text \"shot_scale\" or a bracketed placeholder), then the resolved people count.\n"
            "  2. The IDENTITY LOCK block.\n"
            "  3. The LOCATION LOCK block.\n"
            "  4. A SPATIAL CORE: one or two short grammatical sentences, not tags, serializing "
            "RENDER's resolved Composition prominence and Camera occupancy as two distinct frame "
            "relations — what dominates/fills the frame (with its selected substance), and how "
            "the subject reads relative to that same frame/camera: both its size/distance AND its "
            "resolved visible-frame occupancy (how much of the frame it fills — e.g. only a minor "
            "portion at wide/extreme-wide, most of the frame height at tighter scales, when that "
            "occupancy is resolved). Size/distance wording alone never substitutes for stating "
            "that frame-occupancy consequence — they are two parts of the one relation, not "
            "interchangeable restatements of it. The resolved "
            "Composition owner (the environment/space substance itself, e.g. the ground, the "
            "grove, the terrain) stays the grammatical subject of that dominance — light, glow, "
            "or illumination may describe how it's lit but never substitutes as what dominates. "
            "Follow "
            "RENDER's resolved hierarchy: when environment leads, the environment/frame "
            "relationship comes first, then subject/frame occupancy; when subject/performance "
            "leads, the core may open from the subject/frame relationship instead. Blocking's "
            "own placement or spacing (centered, left/right, foreground/midground/background) "
            "may still appear elsewhere in the reply but never stands in for this occupancy "
            "relation. Serializes only what THINK/RENDER already resolved — never a new "
            "geometry, environment feature, position, camera distance, or action.\n"
            "  5. The remaining resolved facts — action, expression/performance, props, "
            "lighting, atmosphere/world interaction — as short comma-separated tags.\n"
            + _V3_IDENTITY_LOCK_BLOCK
            + _V3_LOCATION_LOCK_BLOCK
            + "  Everything outside the lock and the spatial core stays tag-like: no additional "
            "prose paragraph, no section labels (Camera:/Subject:/Environment:), no stage "
            "labels. The spatial core is the only prose exception — it is not narrative and "
            "never grows past two sentences; the isolated spatial relationship must never be "
            "left as tags alone (e.g. \"vast environment, small distant figure\" without a "
            "grammatical sentence tying them together is a format failure). State each fact "
            "once; clothing appears only in the lock. Keep the owning person's name attached at "
            "any point ownership would otherwise be ambiguous, especially with more than one "
            "person visible. Skip any resolved category that is empty — never invent a tag or a "
            "spatial detail to fill a gap.\n"
            + _V3_SHAPE_TAG
        )
    if st == "sectioned_detailed":
        return (
            "sectioned_detailed: labeled sections, each own line. CAMERA/IDENTITY LOCK/LOCATION "
            "LOCK stay "
            "first; SUBJECT/ENVIRONMENT then follow RENDER's hierarchy — ENVIRONMENT before "
            "SUBJECT when environment leads, else SUBJECT before ENVIRONMENT; LIGHTING last:\n"
            "CAMERA / IDENTITY LOCK / LOCATION LOCK / SUBJECT / ENVIRONMENT / LIGHTING.\n"
            + _V3_IDENTITY_LOCK_BLOCK
            + _V3_LOCATION_LOCK_BLOCK
            + "  Each section places the SAME resolved moment from THINK above into its labeled "
            "slot — sections are containers for what THINK already resolved, not invitations to "
            "elaborate further. One simple fact deserves one simple sentence.\n"
            "  CAMERA: the resolved framing, people count, and — when occupancy was resolved — "
            "how the subject reads within the frame; no camera prose beyond that.\n"
            "  LOCATION LOCK is its own section, immediately after IDENTITY LOCK — never folded "
            "into or duplicated inside ENVIRONMENT.\n"
            "  SUBJECT: the resolved action, blocking, established contact, and frame occupancy "
            "when not already placed in CAMERA — not appearance or clothing again (that is the "
            "lock's job).\n"
            "  ENVIRONMENT: the resolved place and, when environment leads, its selected "
            "substance — that substance itself (the ground, the grove, the terrain) stays the "
            "grammatical subject of an explicit dominance-over-the-frame/composition clause; "
            "never left implicit in a plain place description, never a bare label, and never "
            "replaced by light/glow/illumination as the thing that dominates — that belongs in "
            "LIGHTING instead. Adds no architecture beyond what THINK resolved.\n"
            "  LIGHTING: the resolved light — invents no direction, source, or time of day; "
            "describes illumination only, never the frame-dominance clause itself.\n"
            "  CLIMAX is OPTIONAL: add it after LIGHTING only when the current shot names a focal "
            "beat, and only as a literal restatement of that beat — never invented to fill the "
            "section.\n"
            "  No other sections beyond CAMERA / IDENTITY LOCK / LOCATION LOCK / SUBJECT / "
            "ENVIRONMENT / LIGHTING / optional CLIMAX: no stage labels "
            "(Moment:/Performance:/Composition:), and no "
            "separate SPATIAL LOCK / INTERACTION LOCK / FINAL IDENTITY CHECK section. Names are "
            "not redundant lock content: keep the stated actor explicit when carrying an action "
            "into SUBJECT; after that, natural pronouns may continue while the subject stays "
            "clear.\n"
            + _V3_SHAPE_SECTIONED
        )
    return (
        "narrative: FOUR parts in this exact order.\n"
        "  1. One opening line: THIS shot's own shot_scale value exactly as supplied, written out "
        "as words — never conjugated or paraphrased (\"extreme wide\" stays \"extreme wide\", "
        "never \"extremely wide\"; if shot_scale is \"medium shot\", the line starts \"medium "
        "shot,\" — never the literal text \"shot_scale\" or a bracketed placeholder) — then, on "
        "the same line, the OUTPUT contract's \"exactly\" cast count.\n"
        "  2. The IDENTITY LOCK block.\n"
        "  3. The LOCATION LOCK block.\n"
        "  4. ONE coherent paragraph translating that SAME resolved moment into flowing "
        "grammatical prose per RENDER above — actor/action/target direction stays exactly as "
        "resolved.\n"
        + _V3_IDENTITY_LOCK_BLOCK
        + _V3_LOCATION_LOCK_BLOCK
        + "  Neither lock is ever placed at the end. After them: no section labels, no stage labels, "
        "no second tag block, no literary interpretation, no cinematic embellishment beyond "
        "what THINK already resolved. Static clothing stays in the lock, described once; only a "
        "garment actively changing as part of this moment may appear again here, attached to its "
        "own owner — never a full outfit restatement, never inherited by another person. The "
        "shot-scale/framing label belongs to the opening line only, never repeated there or "
        "reopened as a bridge like \"The extreme wide shot captures...\" — that only restates "
        "it. The paragraph follows RENDER's resolved hierarchy: when environment leads, open "
        "with an explicit clause naming what dominates the frame/composition, then an explicit "
        "clause stating the subject's resolved size/distance relative to frame/camera, then "
        "action, then remaining light/atmosphere; when subject/performance leads, the occupancy "
        "clause may open instead. Neither clause is left implicit or inferable only from action "
        "or place description — each states its own relation outright, in its own words, every "
        "resolved fact staying attached to its own person, per RENDER above. Blocking's supplied "
        "placement (centered, left/right, foreground/midground/background) may still appear when "
        "canonical, but never in place of either explicit clause. This paragraph-opening rule "
        "governs every shot_scale EXCEPT extreme wide, where the CONCRETE DEPTH RULE below "
        "applies instead.\n"
        "  CONCRETE DEPTH RULE (narrative only — applies only when THIS shot's own shot_scale is "
        "extreme wide): the full IDENTITY LOCK stays exactly as required above, in its normal "
        "position — this rule changes only how the paragraph itself is realized, never whether "
        "or where the lock appears. Resolve the paragraph as physical depth, in this order: "
        "(a) NEAR/FOREGROUND — when current evidence supports an environment element that can "
        "plausibly extend close to camera, describe THAT SAME material as physically near and "
        "visually substantial there — genuinely occupying the near part of the frame, not merely "
        "labeled as present. Naming something \"in the foreground\" without a physical-proximity "
        "reading is NOT enough: weak (\"grass is visible in the foreground\", \"trees stand in "
        "the foreground\") versus strong (\"tall wind-bent grass fills the near foreground close "
        "to the camera\", \"a dark doorway edge occupies the immediate foreground\"). Convey "
        "nearness with whatever natural phrase fits — close to camera, immediate/near foreground, "
        "entering the frame from near the camera, physically between camera and subject, visibly "
        "occupying the near portion of the scene — no fixed wording is required, only the "
        "physical fact of nearness and visible bulk. This is spatial realization of the SAME "
        "environment material already established (e.g. a continuous tall-grass field may show "
        "some of that grass near the camera; a forest's leaves/branches may extend near the "
        "camera; a corridor's own wall/doorway edge may sit near the camera) — never a new object "
        "category invented for this clause. (b) MIDDLE DISTANCE — the open space extending "
        "between camera and the subject/far environment; (c) FAR BACKGROUND — the current fact "
        "establishing distance (tree line, horizon, far wall, distant structure); (d) SUBJECT "
        "PLACEMENT — place each visible named subject's small/distant scale ONCE inside that "
        "depth, then continue any action/state for that same subject without re-introducing a "
        "second independent spatial placement — naming a subject's location a second time reads "
        "as a second person, not a continuation. The near-foreground clause (a) describes "
        "environment, not the subject — for extreme wide, keep the subject out of that clause and "
        "place them farther into the depth (middle distance or beyond) unless the canonical shot "
        "explicitly puts them near camera. (e) LIGHT UPPER FOREGROUND (optional) — when the SAME "
        "material already used for the near/lower foreground in (a) is the kind of thing that can "
        "plausibly continue upward or into the upper frame (tall grass's own blades/seed heads "
        "leaning up, a forest's own leaves/branches reaching up, a corridor's own eave/archway/"
        "overhang, a cave's own rock overhang, and similar), a LIGHT version of that SAME material "
        "may additionally be described entering part of the upper foreground, near camera. This is "
        "spatial elaboration of material already established by canonical_location/place/env/"
        "scene_desc/props, never a new object category — grass may only extend as more grass, "
        "leaves only as more leaves, never as a branch, roof, arch, lantern, bird, hanging cloth, "
        "or pole unless that thing is itself already supported by current evidence. Keep this cue "
        "LIGHT: \"a few\", \"lightly\", \"partially\", \"leaning into part of the upper foreground\" "
        "— never large, never entering from both the left AND right edges as a matched pair, never "
        "described as framing/enclosing the whole image or the whole sky. The open sky/open space "
        "above and beyond it must stay the dominant, clearly readable read; this cue adds one small "
        "near-camera depth touch, it does not turn an open landscape into a tunnel. Whatever the "
        "shot's current time-of-day, lighting, horizon, and far background facts are (e.g. the "
        "sunset, the distant tree line) must still be stated plainly and remain fully legible in "
        "the paragraph — the upper cue is secondary and must never replace, crowd out, or compete "
        "with those facts. Do not use abstract meta-composition phrasing anywhere in this "
        "paragraph — \"environment/space dominant relative to frame/composition\", \"capturing "
        "<name>\", \"resolved hierarchy\", \"visual prominence\" and similar are FORBIDDEN; say "
        "the concrete visual fact instead (\"the field stretches toward the tree line...\", \"she "
        "appears small and distant...\"). Never restate or reference this rule, its labels, or "
        "the fact that a rule was applied — output prose only, no rule name, no parenthetical "
        "self-description of the paragraph's construction. Prefer short declarative sentences, "
        "one spatial job per sentence, over one long comma-chained sentence carrying every depth "
        "relation at once — no fixed template or sentence count is required. Use only as many "
        "depth anchors as needed for a readable image — normally one lower-foreground anchor, at "
        "most one light upper-foreground touch, one middle-distance region, and one far anchor; "
        "this is not an environment inventory.\n"
        + _V3_SHAPE_NARRATIVE
    )

def _minimal_v3_field_desc(prompt_style: str, manga_page: bool = False) -> str:
    if manga_page:
        return ("multi-panel page prompt following PAGE PLAN; one page-wide IDENTITY LOCK + one "
                "page-wide LOCATION LOCK; clothing once; identity/location never redefined per "
                "panel")
    st = (prompt_style or "narrative").strip().lower()
    if st == "sectioned":
        st = "sectioned_detailed"
    if st == "tag_style":
        return ("IDENTITY LOCK + LOCATION LOCK blocks plus one comma-separated tag "
                "representation; clothing once; no prose paragraph and no second tag block")
    if st == "sectioned_detailed":
        return ("sectioned labels CAMERA / IDENTITY LOCK / LOCATION LOCK / SUBJECT / "
                "ENVIRONMENT / LIGHTING; CLIMAX only when the shot names a focal beat")
    return ("IDENTITY LOCK + LOCATION LOCK blocks, then one narrative paragraph; clothing once; "
            "no section labels and no tag block")

# ── duplicate-candidate guard: a retried/repaired candidate must REPLACE the previous one, ──────
# never append to it. เจอจริง: response เดียวมี opening+IDENTITY LOCK ซ้ำ 5 รอบ ไม่มี body เลย —
# โมเดลเขียนคำตอบซ้ำในเรสปอนส์เดียว (ไม่ใช่ retry ของ Python ต่อกัน — แต่ละ retry ของ Python
# reassign shot_data ใหม่ทั้งก้อนอยู่แล้ว ไม่เคย append) ผสมกับ _split_fill_final_check เดิมที่ตัด
# แค่คู่ START/END แรก ทำให้ candidate ที่ 2 เป็นต้นไปหลุดรอดเข้า prompt สุดท้าย — เช็คนี้ล้วน ๆ
# นับ/แพทเทิร์น ไม่ตัดสินความหมาย
_V3_IDENTITY_LOCK_LINE_RE = _re_clothing.compile(r'(?im)^[ \t]*IDENTITY LOCK\s*:\s*$')

_V3_LOCATION_LOCK_LINE_RE = _re_clothing.compile(r'(?im)^[ \t]*LOCATION LOCK\s*:\s*$')

# FILL-VNEXT-OBJECT-LOCK-A1: third lock, optional (present only when this shot has a current
# physical object) — same "standalone line, nothing after the colon" requirement as the two locks
# above, so a collapsed "OBJECT LOCK: Car = ..." is rejected by the standalone-line regex the same
# way a collapsed IDENTITY/LOCATION LOCK already is.
_V3_OBJECT_LOCK_LINE_RE = _re_clothing.compile(r'(?im)^[ \t]*OBJECT LOCK\s*:\s*$')

def _v3_lock_block_text(prompt: str, lock_match) -> str:
    """Structural-only extraction of a standalone LOCK block's content — everything right after
    `lock_match`'s own line, up to the next blank line or the next recognized section/lock label,
    whichever comes first. Used only for verbatim-preservation COMPARISONS (e.g. Director must not
    change OBJECT LOCK's content at all); never used to judge or repair meaning."""
    tail = prompt[lock_match.end():]
    _blank = _re_clothing.search(r'\n[ \t]*\n', tail)
    _next_label = _SECTION_LABEL_RE.search(tail)
    _end = len(tail)
    if _blank:
        _end = min(_end, _blank.start())
    if _next_label:
        _end = min(_end, _next_label.start())
    return tail[:_end].strip()

def _vnext_preceded_by_blank_line(prompt: str, pos: int) -> bool:
    """Structural-only: True when prompt[:pos] ends in real content followed by a genuine blank
    line (a non-whitespace char, then a newline, optional trailing spaces, then a second blank
    newline, optional trailing spaces) immediately before `pos` — i.e. whatever starts at `pos` is
    properly separated from what came before it by one blank line, rather than running directly
    off the previous line with none. Never reads what the content actually says, only how the
    whitespace around it is laid out."""
    head = prompt[:pos]
    return bool(_re_clothing.search(r'\S[ \t]*\n[ \t]*\n\s*\Z', head))

# VNEXT-BLOCK-SPACING-A1: shared structural check, reused by both Fill's own validator
# (_vnext_fill_validate_structure) and the Director/Checker/Vision-Check shared reject guard
# (_director_format_structure_ok) — that a genuine blank line separates every recognized block
# (the opening line/section, IDENTITY LOCK / LOCATION LOCK / OBJECT LOCK, and — for
# sectioned_detailed — every CAMERA/SUBJECT/ENVIRONMENT/LIGHTING/CLIMAX section label) from
# whatever precedes it, and that the body/prose which follows the LAST recognized block (when one
# exists) is likewise separated from it by a blank line. Whitespace/line-layout only — never reads
# block content, never repairs anything, only reports what it finds so the caller can reject/retry.
#
# `fmt`, when given, only changes the LAST-block-to-body check: sectioned_detailed's final section
# (LIGHTING, or CLIMAX when present) legitimately has nothing after it at all — its own content is
# the end of the prompt, so there is no separate "body" whose boundary could be missing. narrative/
# tag_style always require a body after their last lock, so that boundary is still checked for them
# (an already-missing body is reported by the caller's own separate "missing body" check, not this
# one — but a body that exists and runs directly into the last lock with no blank line is exactly
# the defect this function exists to catch).
def _vnext_block_spacing_violations(prompt: str, fmt: str = "") -> list:
    p = prompt or ""
    if not p.strip():
        return []
    id_matches = list(_V3_IDENTITY_LOCK_LINE_RE.finditer(p))
    loc_matches = list(_V3_LOCATION_LOCK_LINE_RE.finditer(p))
    obj_matches = list(_V3_OBJECT_LOCK_LINE_RE.finditer(p))
    all_labels = list(_SECTION_LABEL_RE.finditer(p))
    _lock_names = {"identity lock", "identity", "location lock", "object lock"}
    sec_labels = [m for m in all_labels if m.group(1).strip().lower() not in _lock_names]

    _named = {}
    for m in id_matches:
        _named[m.start()] = ("IDENTITY LOCK", m.end())
    for m in loc_matches:
        _named[m.start()] = ("LOCATION LOCK", m.end())
    for m in obj_matches:
        _named[m.start()] = ("OBJECT LOCK", m.end())
    for m in sec_labels:
        _named.setdefault(m.start(), (m.group(1).strip().upper(), m.end()))
    if not _named:
        return []

    starts = sorted(_named)
    issues = []

    first = starts[0]
    if p[:first].strip() and not _vnext_preceded_by_blank_line(p, first):
        issues.append(f"missing blank line before {_named[first][0]} "
                       f"(runs directly into the opening content before it)")
    for prev_start, cur_start in zip(starts, starts[1:]):
        if not _vnext_preceded_by_blank_line(p, cur_start):
            prev_name = _named[prev_start][0]
            cur_name = _named[cur_start][0]
            issues.append(f"missing blank line between {prev_name} and {cur_name} — they ran "
                           f"directly into each other with no blank line separating them")

    _fmt = (fmt or "").strip().lower()
    if _fmt != "sectioned_detailed":
        # narrative/tag_style (or unknown/unspecified — treat like narrative, the only other form
        # this helper is ever actually invoked against): the LAST block's own content ends at the
        # first blank-line gap after its label; whatever follows that gap is the body. If the tail
        # has no blank-line gap anywhere but still has real content, that content can only be the
        # body run directly into the lock's own content with zero separation — the defect this
        # patch targets. (Content-only, nothing-follows tails are caught by the caller's own
        # separate "missing body" check, not this one — this one only fires when there IS trailing
        # content and it is NOT properly separated.)
        last_start = starts[-1]
        last_end = _named[last_start][1]
        tail = p[last_end:]
        if tail.strip() and not _re_clothing.search(r'\n[ \t]*\n', tail):
            issues.append(f"missing blank line between {_named[last_start][0]} and the body/"
                           f"content that follows it")
    return issues

# PATCH3-CHECKER-OBJECT-LOCK-SURGICAL-REPAIR-A1: structural-only span finder for an EXISTING
# OBJECT LOCK block — start of its "OBJECT LOCK:" label line through the end of its content, using
# the exact same label/blank-line/next-section boundary rule every other OBJECT LOCK boundary
# helper in this file already uses (_v3_lock_block_text / _vnext_strip_disallowed_object_lock).
# Returns None when no OBJECT LOCK block exists. Pure boundary detection — never reads what the
# block's content actually says.
def _v3_object_lock_block_span(prompt: str) -> tuple | None:
    p = prompt or ""
    m = _V3_OBJECT_LOCK_LINE_RE.search(p)
    if not m:
        return None
    tail = p[m.end():]
    _blank = _re_clothing.search(r'\n[ \t]*\n', tail)
    _next_label = _SECTION_LABEL_RE.search(tail)
    _end = len(tail)
    if _blank:
        _end = min(_end, _blank.start())
    if _next_label:
        _end = min(_end, _next_label.start())
    return m.start(), m.end() + _end

# PATCH3-CHECKER-OBJECT-LOCK-SURGICAL-REPAIR-A1: pure structural splice for the Checker's scoped
# OBJECT LOCK repair contract (see _MINIMAL_V3_CHECKER_OBJECT_SCOPED_REPAIR_RULE / run_check_prompts
# repair_scope=="object_lock" handling). The LLM has already decided WHAT the corrected OBJECT LOCK
# block should say and supplies it complete (its own "OBJECT LOCK:" header line plus content); this
# function only locates the ORIGINAL prompt's existing OBJECT LOCK block by structural boundary
# (via `_v3_object_lock_block_span`) and replaces exactly that span, copying everything before and
# after it byte-for-byte. It never reads/compares/judges the semantic content of either block — no
# color/noun/keyword comparison, no inferring what should be there — it only counts header lines
# and locates block extent. Returns (new_prompt, "") on success, or (None, reason) when the splice
# cannot be performed unambiguously (missing/malformed replacement, duplicate headers, or no
# existing OBJECT LOCK block in the original to splice into — this function deliberately does NOT
# support introducing a brand-new block or removing the block entirely; see run_check_prompts'
# repair_scope handling and task notes for why that is out of scope for this patch).
def _v3_splice_object_lock_replacement(original_prompt: str, replacement_text: str) -> tuple:
    original = original_prompt or ""
    repl = (replacement_text or "").strip()
    if not repl:
        return None, "empty object_lock_replacement"
    repl_headers = list(_V3_OBJECT_LOCK_LINE_RE.finditer(repl))
    if len(repl_headers) != 1:
        return None, f"replacement has {len(repl_headers)} OBJECT LOCK headers (need exactly 1)"
    if repl_headers[0].start() != 0:
        return None, "replacement must start with the OBJECT LOCK: header line"
    repl_content = repl[repl_headers[0].end():].strip()
    if not repl_content:
        return None, "replacement OBJECT LOCK block has no content"
    span = _v3_object_lock_block_span(original)
    if span is None:
        return None, "original prompt has no existing OBJECT LOCK block to splice into"
    start, end = span
    before = original[:start].rstrip(" \t")
    after = original[end:]
    spliced = before + repl + after
    return spliced, ""

# DIRECTOR-FORMAT-AUTHORITY-PRODUCTION-A1: structural-only reject guard for a Director (and, by
# reuse, Checker — see run_check_prompts' own call site) candidate rewrite under Minimal V3 / vNext
# lock-contract engines. Pure counts/labels/ordering/standalone-line checks plus one verbatim
# comparison for OBJECT LOCK specifically — never semantic (never judges whether a garment, action,
# location, light, or object identity is "correct"). Shared by the initial Director acceptance, the
# Local consequence-repair acceptance, and the Grok batch acceptance so all three paths reject a
# structurally-broken candidate the same way instead of trusting updated["prompt_style"], which can
# stay correct even when the prompt text itself is corrupted.
def _director_format_structure_ok(before_prompt: str, after_prompt: str, expected_format: str,
                                  minimal_v3: bool = True,
                                  allow_object_lock_repair: bool = False) -> tuple:
    if not minimal_v3:
        return True, ""
    fmt = (expected_format or "narrative").strip().lower()
    if fmt in ("", "manga_page"):
        return True, ""  # manga has its own dedicated structural protection — untouched here
    before = before_prompt or ""
    after = after_prompt or ""
    if not after.strip():
        return False, "empty candidate"

    id_matches = list(_V3_IDENTITY_LOCK_LINE_RE.finditer(after))
    if len(id_matches) != 1:
        return False, f"identity lock not standalone (found {len(id_matches)}, need exactly 1)"
    loc_matches = list(_V3_LOCATION_LOCK_LINE_RE.finditer(after))
    if len(loc_matches) != 1:
        return False, f"location lock missing or duplicated (found {len(loc_matches)}, need exactly 1)"
    if id_matches[0].start() > loc_matches[0].start():
        return False, "identity lock does not precede location lock"

    # FILL-VNEXT-OBJECT-LOCK-A1: OBJECT LOCK is OPTIONAL — max 1, must sit after LOCATION LOCK, and
    # when it existed in `before` it must survive into `after` VERBATIM (count/order/presence stay
    # structural-only for every prior lock; OBJECT LOCK additionally gets this one verbatim check
    # because Director specifically has zero semantic authority over it at all — see
    # _OBJECT_LOCK_DIRECTOR_RULE — so any content drift after Director can only mean corruption).
    # PATCH2-CHECKER-OBJECT-REPAIR-A1: Checker (unlike Director) DOES have narrow, evidence-based
    # repair authority over OBJECT LOCK content (see _MINIMAL_V3_CHECKER_OBJECT_RULE) — this same
    # structural gate is reused for Checker's own format-acceptance check (run_check_prompts' call
    # site), so without this flag a legitimate Checker repair (e.g. stripping state contamination
    # Fill wrote into the lock) was being rejected by the verbatim-equality check below as if it
    # were corruption, silently discarding the fix and keeping the contaminated original. The flag
    # changes ONLY whether content differences are tolerated — count/order/presence/duplication
    # stay exactly as strict either way, for both callers.
    before_obj_matches = list(_V3_OBJECT_LOCK_LINE_RE.finditer(before))
    after_obj_matches = list(_V3_OBJECT_LOCK_LINE_RE.finditer(after))
    if len(after_obj_matches) > 1:
        return False, f"object lock duplicated (found {len(after_obj_matches)})"
    if after_obj_matches and after_obj_matches[0].start() < loc_matches[0].start():
        return False, "object lock does not follow location lock"
    if len(before_obj_matches) == 1 and len(after_obj_matches) == 1:
        if (not allow_object_lock_repair
                and _v3_lock_block_text(before, before_obj_matches[0]) != _v3_lock_block_text(after, after_obj_matches[0])):
            return False, "object lock content changed (must be preserved verbatim)"
    elif len(before_obj_matches) == 1 and not after_obj_matches:
        return False, "object lock removed"
    elif not before_obj_matches and after_obj_matches:
        return False, "object lock introduced where none existed before"

    after_family = _detect_prompt_format(after)
    if after_family != fmt:
        return False, f"format family changed ({fmt} -> {after_family})"

    _lock_label_names = {"identity lock", "identity", "location lock", "object lock"}
    if fmt == "sectioned_detailed":
        before_labels = [m.group(1).strip().lower() for m in _SECTION_LABEL_RE.finditer(before)]
        after_labels = [m.group(1).strip().lower() for m in _SECTION_LABEL_RE.finditer(after)]
        if before_labels and after_labels != before_labels:
            return False, f"section label sequence changed ({before_labels} -> {after_labels})"
    else:
        stray = [m.group(1).strip().lower() for m in _SECTION_LABEL_RE.finditer(after)
                 if m.group(1).strip().lower() not in _lock_label_names]
        if stray:
            return False, f"section labels introduced in {fmt} ({stray})"

    # VNEXT-BLOCK-SPACING-A1: a candidate can pass every count/order/label check above and still
    # jam blocks together with zero blank-line separation between them (labels/content all present
    # and correctly ordered, just run into each other) — reject that the same way as any other
    # structural defect, so it falls back to the prior prompt exactly like every other reject here.
    _spacing_issues = _vnext_block_spacing_violations(after, fmt)
    if _spacing_issues:
        return False, _spacing_issues[0]

    return True, ""

# FILL-VNEXT-OBJECT-LOCK-A1 / task "16": one shared structural-signature helper instead of Checker
# and Director each growing their own slightly-different notion of "is this still the same
# structure" — reports format family, per-lock counts, lock order, section labels/order, and
# whether any body content exists. Structural only: never hashes or compares semantic prose.
def _vnext_prompt_structure_signature(prompt: str) -> dict:
    p = (prompt or "").strip()
    id_m = list(_V3_IDENTITY_LOCK_LINE_RE.finditer(p))
    loc_m = list(_V3_LOCATION_LOCK_LINE_RE.finditer(p))
    obj_m = list(_V3_OBJECT_LOCK_LINE_RE.finditer(p))
    _locks = ([("identity", m.start()) for m in id_m] + [("location", m.start()) for m in loc_m]
              + [("object", m.start()) for m in obj_m])
    _locks.sort(key=lambda x: x[1])
    labels = [m.group(1).strip().lower() for m in _SECTION_LABEL_RE.finditer(p)]
    _lock_names = {"identity lock", "identity", "location lock", "object lock"}
    section_labels = [l for l in labels if l not in _lock_names]
    _last_lock_end = max([m.end() for m in (id_m + loc_m + obj_m)], default=0)
    return {
        "format_family": _detect_prompt_format(p),
        "identity_lock_count": len(id_m),
        "location_lock_count": len(loc_m),
        "object_lock_count": len(obj_m),
        "lock_order": [name for name, _ in _locks],
        "section_labels": section_labels,
        "section_order": section_labels,
        "body_present": bool(p[_last_lock_end:].strip()) if _last_lock_end else bool(p),
    }

# V3-PROMPT-BLOCK-SPACING-NORMALIZATION-A1: deterministic, non-LLM presentation whitespace fix for
# an already-structurally-valid Minimal V3 narrative prompt (line endings, trailing whitespace, and
# the blank line around each structural boundary). This is NOT semantic — it never rewrites words,
# never reconstructs a missing/duplicate lock, never changes format family, and never renames a
# label's literal text. Any skeleton it cannot unambiguously recognize as a valid, single, correctly
# ordered narrative IDENTITY LOCK / LOCATION LOCK pair is returned completely unchanged — malformed-
# skeleton repair remains the Director structural guard's job, never this function's.
def _normalize_v3_prompt_block_spacing(prompt: str) -> str:
    if not prompt:
        return prompt
    original = prompt
    p = prompt.replace("\r\n", "\n").replace("\r", "\n")

    if _re_clothing.search(r'(?im)^\s*\d+[\s-]*panel', p):
        return original  # multi-panel page — untouched, out of scope for this helper

    id_matches = list(_V3_IDENTITY_LOCK_LINE_RE.finditer(p))
    loc_matches = list(_V3_LOCATION_LOCK_LINE_RE.finditer(p))
    if len(id_matches) != 1 or len(loc_matches) != 1:
        return original  # missing/duplicate lock — not this helper's job, no-op
    if id_matches[0].start() > loc_matches[0].start():
        return original  # LOCATION before IDENTITY — malformed, no-op

    if _detect_prompt_format(p) != "narrative":
        return original  # tag_style/sectioned_detailed/unknown — narrative only in this round

    # FILL-VNEXT-OBJECT-LOCK-A1: OBJECT LOCK is optional — 0 or 1 only; >1 is malformed, no-op.
    obj_matches = list(_V3_OBJECT_LOCK_LINE_RE.finditer(p))
    if len(obj_matches) > 1:
        return original

    _lock_label_names = {"identity lock", "identity", "location lock", "object lock"}
    if any(m.group(1).strip().lower() not in _lock_label_names for m in _SECTION_LABEL_RE.finditer(p)):
        return original  # a stray Camera:/Subject:/... label means this isn't plain narrative

    # VNEXT-BLOCK-SPACING-A1: a candidate can be missing a blank-line boundary between blocks (the
    # jammed-lines defect this patch targets) while still matching every check above — label counts
    # correct, order correct, no stray labels. Reconstructing "\n\n" joins unconditionally below
    # would silently REPAIR that missing boundary into valid spacing, which is exactly the
    # "reconstructing invalid structure into valid structure" this helper's own docstring forbids.
    # Treat a missing boundary as an unrecognized skeleton and no-op, same as every other case above
    # — the structural guard (_director_format_structure_ok) is the one responsible for rejecting a
    # candidate shaped like this; this helper only ever tidies spacing that was already correct.
    if _vnext_block_spacing_violations(p, "narrative"):
        return original

    opening = p[:id_matches[0].start()].rstrip()
    if not opening:
        return original  # nothing before the lock — not the expected skeleton, no-op

    id_label = p[id_matches[0].start():id_matches[0].end()].strip()
    loc_label = p[loc_matches[0].start():loc_matches[0].end()].strip()

    identity_block = p[id_matches[0].end():loc_matches[0].start()]
    identity_lines = [ln.rstrip() for ln in identity_block.split("\n") if ln.strip()]
    if not identity_lines:
        return original  # empty IDENTITY LOCK content — malformed, no-op

    after_lines = p[loc_matches[0].end():].split("\n")
    _i = 0
    while _i < len(after_lines) and not after_lines[_i].strip():
        _i += 1
    if _i >= len(after_lines):
        return original  # nothing after LOCATION LOCK at all — malformed, no-op
    location_line = after_lines[_i].rstrip()

    rest_lines = after_lines[_i + 1:]

    # FILL-VNEXT-OBJECT-LOCK-A1: optional OBJECT LOCK block sits right after the location line —
    # recognize it ONLY when it is already an unambiguous, non-empty standalone block; otherwise
    # leave the whole prompt untouched rather than guess.
    obj_label = None
    obj_lines: list[str] = []
    _j = 0
    while _j < len(rest_lines) and not rest_lines[_j].strip():
        _j += 1
    if obj_matches and _j < len(rest_lines) and _re_clothing.match(r'^[ \t]*OBJECT LOCK\s*:\s*$', rest_lines[_j]):
        obj_label = rest_lines[_j].strip()
        _k = _j + 1
        while _k < len(rest_lines) and rest_lines[_k].strip():
            obj_lines.append(rest_lines[_k].rstrip())
            _k += 1
        if not obj_lines:
            return original  # empty OBJECT LOCK content — malformed, no-op
        body_lines = rest_lines[_k:]
    elif obj_matches:
        return original  # an OBJECT LOCK exists somewhere but not right after LOCATION LOCK — skip
    else:
        body_lines = rest_lines

    _b = 0
    while _b < len(body_lines) and not body_lines[_b].strip():
        _b += 1
    body = "\n".join(body_lines[_b:]).rstrip()
    if not body:
        return original  # no body prose after the location/object block — malformed, no-op

    return (
        opening + "\n\n"
        + id_label + "\n" + "\n".join(identity_lines) + "\n\n"
        + loc_label + "\n" + location_line + "\n\n"
        + (obj_label + "\n" + "\n".join(obj_lines) + "\n\n" if obj_label else "")
        + body
    )

def _parse_grok_batch_shots(raw: str | None) -> list[dict]:
    """แกะ {"shots": [...]} จาก Grok batch response — กู้ item ที่วงเล็บปิดครบแม้ response ถูกตัดกลางคัน
    ไม่เคยพยายามซ่อม item ที่พัง แค่ข้ามมันไป (ปล่อยให้ตัวเรียกเห็นว่า shot นั้นหาย แล้วรายงานว่าล้มเหลว)"""
    if not raw or not raw.strip():
        return []
    t = raw.strip()
    t = _re_clothing.sub(r'^```(?:json)?\s*', '', t)
    t = _re_clothing.sub(r'```\s*$', '', t).strip()
    try:
        s = t.index('{'); e = t.rindex('}') + 1
        obj = json.loads(t[s:e])
        items = obj.get("shots") if isinstance(obj, dict) else None
        if isinstance(items, list):
            return [it for it in items if isinstance(it, dict)]
    except Exception:
        pass
    # response ถูกตัดกลางคัน — ไล่ดึงเฉพาะ object ที่ { } ปิดครบภายใน "shots": [ ... ]
    m = _re_clothing.search(r'"shots"\s*:\s*\[', t)
    if not m:
        return []
    items: list[dict] = []
    i, n = m.end(), len(t)
    while i < n:
        while i < n and t[i] in ' \t\r\n,':
            i += 1
        if i >= n or t[i] != '{':
            break
        depth = 0; in_s = False; esc = False; start = i; closed = False
        while i < n:
            c = t[i]
            if esc:
                esc = False
            elif c == '\\' and in_s:
                esc = True
            elif c == '"':
                in_s = not in_s
            elif not in_s and c == '{':
                depth += 1
            elif not in_s and c == '}':
                depth -= 1
                if depth == 0:
                    i += 1; closed = True
                    break
            i += 1
        if not closed:
            break   # object สุดท้ายไม่ปิด = ถูกตัดกลางคัน หยุดตรงนี้ ไม่เดา
        try:
            it = json.loads(t[start:i])
            if isinstance(it, dict):
                items.append(it)
        except Exception:
            pass
    return items

def _index_batch_by_shot(items: list[dict]) -> tuple[dict[int, dict], list[dict]]:
    """map ผล batch เข้ากับเลข shot — เลขซ้ำให้ตัวหลังทับตัวก่อน (ตัวสุดท้ายชนะ)
    item ที่ไม่มีเลข shot ที่แปลงเป็น int ได้ ถูกแยกไว้ต่างหากให้ผู้เรียก log แล้วทิ้ง"""
    by_shot: dict[int, dict] = {}
    unknown: list[dict] = []
    for it in items:
        try:
            n = int(it.get("shot"))
        except (TypeError, ValueError):
            unknown.append(it)
            continue
        by_shot[n] = it
    return by_shot, unknown

async def _grok_batch_call(sys_prompt: str, shots_payload: list[dict], max_tokens: int,
                           grok_api_key: str, grok_model: str, stage_label: str,
                           on_log=None, extras: dict | None = None) -> list[dict]:
    """ยิง shots_payload (compact dict ต่อช็อต) เป็นคำขอ Grok เดียว คืน list ของ item ที่ parse ได้
    sys_prompt คือของเดิมที่ _fill_sys_for/_editor_sys_for/_director_sys_for สร้างไว้แล้ว —
    มี Character Sheet/Scene/Props/Story/กฎทั้งชุดอยู่ในนั้นแล้วครั้งเดียว ไม่ต้องแนบซ้ำใน user message"""
    user_p = (
        f"Return ONLY this JSON shape, one object per shot below (any order):\n"
        '{"shots": [{"shot": <shot number>, ...fields for that shot...}]}\n\n'
        f"SHOTS TO PROCESS ({len(shots_payload)}):\n"
        + json.dumps(shots_payload, ensure_ascii=False)
    )
    if on_log:
        await on_log(f"⚡ Grok {stage_label} batch: {len(shots_payload)} shots → 1 request")
    # เดิม on_log=None ตรงนี้ตลอด — batch call ไม่เคย log system/user/RAW response เลยสักตัวอักษร
    # ต่างจาก per-shot call ที่ log ครบ ผลคือตอน shot หายจาก batch (เช่น response ว่าง/ตัดกลาง/
    # parse ไม่ออก) ไม่มีทางรู้เลยว่า Grok ตอบอะไรมาจริงๆ — และตอน one-call ไม่มีคำขอรอบสองให้แก้ตัว
    _shot_ids = [p.get("shot") for p in shots_payload
                 if isinstance(p, dict) and p.get("shot") is not None]
    _stage = _debug_slug(stage_label, "unknown")
    with _llm_debug_scope(_stage, shot_ids=_shot_ids or None,
                          batch_ids=[f"{_stage}:{len(shots_payload)}"]):
        raw = await _call_grok_api(sys_prompt, user_p, max_tokens, grok_api_key, grok_model, on_log=on_log)
    items = _parse_grok_batch_shots(raw)
    if extras is not None:
        # FILL-CREATIVE-DESIGN-LEDGER-A1: hand back the batch's top-level "fill_design_ledger" string
        # as-is (opaque) — only a caller that passes `extras` (Normal Fill) ever reads it.
        # FILL-DESIGN-LEDGER-HOTFIX-A1: the KEY's presence is the signal — "" is recorded (explicit empty
        # snapshot); an absent (or non-string) field records nothing.
        try:
            _lt = _re_clothing.sub(r'^```(?:json)?\s*', '', (raw or "").strip())
            _lt = _re_clothing.sub(r'```\s*$', '', _lt).strip()
            _lo = json.loads(_lt[_lt.index('{'):_lt.rindex('}') + 1])
            _lv = _lo.get("fill_design_ledger") if isinstance(_lo, dict) else None
            if isinstance(_lv, list):
                _lv = "\n".join(str(x) for x in _lv)
            if isinstance(_lv, str):
                extras["fill_design_ledger"] = _lv.strip()
        except Exception:
            pass
    # batch เดี่ยว (1 ช็อต) เจอจริงว่าบางครั้ง Grok เมิน "ห่อเป็น {"shots":[...]}"} แล้วตอบ prompt
    # ล้วนตรงๆ แบบเดียวกับตอนเรียกทีละช็อต — parser เดิมเข้มงวด (ต้องมี "shots" array) เลยเห็นเป็น
    # "ไม่มีอะไรเลย" ทั้งที่ Grok ตอบมาให้ถูกต้องแล้ว เกิดกับ Fill เท่านั้น (Checker/Director ต้องการ
    # field อื่นด้วย เช่น keep_original ซึ่งข้อความเปล่าเดาไม่ได้ จึงไม่ใช้ fallback นี้กับสองด่านนั้น)
    # batch เดี่ยวอีกแบบที่เจอ: Grok ตอบ JSON ถูกต้องแต่เป็น "รูปทรงของการเรียกทีละช็อต"
    # ({"prompt": ...} ไม่ห่อ {"shots":[...]}) เดิมรอดเพราะตกไป fallback ยิงเดี่ยวแล้วได้รูปทรงนี้พอดี
    # ตอนนี้ไม่มี fallback แล้ว (one-call mode) ถ้าไม่รับตรงนี้ = เสียช็อตทั้งที่ Grok ตอบมาครบ
    # รู้เลข shot แน่นอนเพราะ batch มีช็อตเดียว จึงเดาไม่ผิด และใช้ได้ทุก stage (field อื่นของ
    # Checker/Director ติดมาในอ็อบเจกต์เดียวกันอยู่แล้ว)
    if not items and len(shots_payload) == 1:
        try:
            _t = (raw or "").strip()
            _t = _re_clothing.sub(r'^```(?:json)?\s*', '', _t)
            _t = _re_clothing.sub(r'```\s*$', '', _t).strip()
            _obj = json.loads(_t[_t.index('{'):_t.rindex('}') + 1])
        except Exception:
            _obj = None
        if isinstance(_obj, dict) and "shots" not in _obj and _obj.get("prompt"):
            _obj = dict(_obj)
            _obj["shot"] = shots_payload[0].get("shot")
            if on_log:
                await on_log(f"⚡ Grok {stage_label} batch: response เป็นรูปทรงเรียกเดี่ยว "
                             f"— ใช้เป็นผลของ shot {_obj['shot']} (batch เดี่ยวเท่านั้น)")
            items = [_obj]
    if not items and len(shots_payload) == 1 and stage_label.lower().startswith("fill"):
        _lenient = (raw or "").strip()
        _lenient = _re_clothing.sub(r'^```(?:json)?\s*', '', _lenient)
        _lenient = _re_clothing.sub(r'```\s*$', '', _lenient).strip()
        # ถ้าขึ้นต้นด้วย "{" คือ Grok พยายาม JSON แล้วแต่ parse ไม่ผ่าน (เช่นตัดกลางคัน) — ปล่อยให้
        # เป็น "ไม่มีอะไรเลย" ตามเดิมดีกว่าเอาเศษ JSON ที่พังไปแปะเป็นเนื้อ prompt ตรงๆ
        if _lenient and not _lenient.lstrip().startswith("{"):
            _shot_num = shots_payload[0].get("shot")
            if on_log:
                await on_log(f"⚡ Grok {stage_label} batch: response ไม่ได้ห่อ JSON แต่มีเนื้อหา "
                            f"— ใช้เป็น prompt ของ shot {_shot_num} ตรงๆ (batch เดี่ยวเท่านั้น)")
            items = [{"shot": _shot_num, "prompt": _lenient}]
    return items

async def _prefetch_grok_batch(stage_label: str, targets: list[tuple[int, str, dict]],
                               max_tokens: int, grok_api_key: str, grok_model: str,
                               on_log=None, chunk_size: int = _GROK_BATCH_CHUNK_SHOTS,
                               stop_event=None, one_call: bool = False,
                               strict_no_recovery: bool = False,
                               extras: dict | None = None) -> dict[int, dict]:
    """targets: [(shot_num, sys_prompt, compact_payload), ...] คืน {shot_num: item_dict}

    max_tokens คืองบ output ของ "ทั้งคำขอ" ไม่ใช่ต่อช็อต — ผู้เรียกที่ใช้ one_call ต้องคูณจำนวน
    ช็อตมาก่อน (ดู _grok_batch_budget)

    โหมดเดิม (one_call=False, ไม่มีผู้เรียกในโค้ดตอนนี้): จัดกลุ่มตาม sys_prompt ที่ตรงกันเป๊ะ
    แล้ว chunk ทีละ chunk_size ต่อ 1 คำขอ 1 primary call ต่อ chunk + 1 recovery call เฉพาะ shot
    ที่หาย — shot ที่ยังหายหลัง recovery ไม่อยู่ใน dict ที่คืน ผู้เรียกต้อง fallback เอง

    one_call=True (โหมดของ Grok): กด 1 ครั้ง = ยิง API หลักครั้งเดียวจริงต่อ stage ไม่ว่าจะกี่ช็อต
    ไม่ chunk และ "ไม่จัดกลุ่มตาม sys_prompt" เลย — คำขอเดียวใช้ sys_prompt ก้อนแรกเป็นก้อนกลาง
    ความต่างรายช็อต (scale / ของถือ / บทพูด) อยู่ใน payload ของแต่ละช็อตอยู่แล้ว ไม่ใช่ในระบบ
    sys prompt การจัดกลุ่มจึงเป็นการหั่นคำขอโดยไม่ได้ข้อมูลเพิ่ม

    PREFETCH-SCOPE-A1: one_call=True by itself still allows ONE bounded recovery call for shots
    missing from the primary response — this is the pre-existing behavior Checker/Director/Fill
    Manga have always had (a second request only for the shots that came back missing, never a
    third). strict_no_recovery=True (Normal Grok Fill's own flag, set only at its call site) removes
    that recovery entirely — a missing shot is reported and left out of the returned dict, no second
    HTTP request of any kind is issued. Checker/Director/Fill Manga must keep passing
    strict_no_recovery=False (the default) so their behavior is unchanged from before this session's
    Fill-batching work; only Normal Fill's call site passes strict_no_recovery=True."""
    if not targets:
        return {}
    if one_call and max_tokens >= _GROK_MAX_OUTPUT_TOKENS and on_log:
        await on_log(f"⚠ Grok {stage_label}: {len(targets)} ช็อต ขอ output เกินเพดาน "
                     f"— ขอเท่าเพดาน {_GROK_MAX_OUTPUT_TOKENS} ช็อตท้าย ๆ อาจถูกตัด "
                     f"(ยังคงยิงคำขอเดียวตามเดิม ไม่หั่นเป็นหลายคำขอ)")
    if one_call:
        # ก้อนเดียว ไม่แยกตาม sys_prompt — รับประกันเชิงโครงสร้างว่าออกไปคำขอเดียวเสมอ
        groups: dict[str, list[tuple[int, dict]]] = {
            targets[0][1]: [(n, p) for n, _s, p in targets]
        }
        chunk_size = max(1, len(targets))
        _distinct = len({s for _n, s, _p in targets})
        if _distinct > 1 and on_log:
            await on_log(f"⚠ Grok {stage_label}: ได้ sys prompt {_distinct} แบบ — ใช้ก้อนแรกก้อนเดียว "
                         f"สำหรับทุกช็อต (one-call mode ไม่หั่นคำขอ)")
    else:
        groups = {}
        for shot_num, sys_p, payload in targets:
            groups.setdefault(sys_p, []).append((shot_num, payload))

    result: dict[int, dict] = {}
    calls = 0
    for sys_p, items in groups.items():
        for start in range(0, len(items), chunk_size):
            if stop_event and stop_event.is_set():
                return result
            chunk = items[start:start + chunk_size]
            wanted = {n for n, _ in chunk}
            resp = await _grok_batch_call(sys_p, [p for _, p in chunk], max_tokens,
                                          grok_api_key, grok_model, stage_label, on_log, extras)
            calls += 1
            by_shot, unknown = _index_batch_by_shot(resp)
            if unknown and on_log:
                await on_log(f"⚠ Grok {stage_label} batch: {len(unknown)} item ไม่มีเลข shot ที่ใช้ได้ — ข้าม")
            extra = set(by_shot) - wanted
            if extra and on_log:
                await on_log(f"⚠ Grok {stage_label} batch: ได้ shot {sorted(extra)} ที่ไม่ได้ขอ — ข้าม")
            result.update({n: it for n, it in by_shot.items() if n in wanted})
            missing = sorted(wanted - set(result))
            if missing and strict_no_recovery:
                # PREFETCH-SCOPE-A1: strict_no_recovery=True means EXACTLY one primary API request
                # for the whole stage, full stop — no bounded recovery, no second request of any
                # kind. Only Normal Grok Fill's call site passes this. Checker/Director/Fill Manga
                # never set it, so they fall through to the bounded-recovery branch below exactly as
                # they did before this session's Fill-batching work touched this function.
                if on_log:
                    await on_log(f"⚠ Grok {stage_label}: ขาด shot {missing} จากคำขอเดียว — "
                                f"ทำเครื่องหมายว่าล้มเหลว ไม่ยิงซ้ำ (strict mode, ไม่มี recovery)")
            elif missing:
                # bounded recovery: หนึ่งคำขอเพิ่มสูงสุด เฉพาะ shot ที่หายจากคำขอแรก (ไม่ใช่ทั้ง
                # chunk) — ใช้ทั้ง one_call และไม่ใช่ one_call เหมือนกัน (เว้น strict_no_recovery)
                # ไม่ retry ซ้ำหลังจากนี้อีก
                if stop_event and stop_event.is_set():
                    return result
                if on_log:
                    await on_log(f"⚠ Grok {stage_label}: ขาด shot {missing} จากคำขอแรก — ยิง recovery "
                                f"รอบเดียว (bounded, เฉพาะ shot ที่หาย)")
                recov = await _grok_batch_call(
                    sys_p, [p for n, p in chunk if n in missing], max_tokens,
                    grok_api_key, grok_model, f"{stage_label} recovery", on_log)
                calls += 1
                by_shot2, _ = _index_batch_by_shot(recov)
                result.update({n: it for n, it in by_shot2.items() if n in missing})
                still_missing = sorted(set(missing) - set(result))
                if still_missing and on_log:
                    if one_call:
                        await on_log(f"⚠ Grok {stage_label}: ยังขาด shot {still_missing} หลัง bounded "
                                    f"recovery — ทำเครื่องหมายว่าล้มเหลว ไม่ยิงซ้ำอีก (one-call mode)")
                    else:
                        await on_log(f"⚠ Grok {stage_label} batch: ยังขาด shot {still_missing} หลัง recovery "
                                    f"— shot เหล่านี้จะ fallback ไปเรียกทีละช็อตตามปกติ")
    if on_log:
        await on_log(f"✅ Grok {stage_label}: {calls} API request total — ได้ {len(result)}/{len(targets)} shots")
        _failed = sorted({n for n, _s, _p in targets} - set(result))
        if _failed:
            await on_log(f"⚠ Grok {stage_label}: shot ที่ไม่ได้ผล {_failed} (ไม่ยิงซ้ำ)")
    return result

def _source_lang_note(source_lang: str, desc_lang: str) -> str:
    """บอกภาษาต้นฉบับให้โมเดลตรง ๆ แทนการให้เดาเอง และสั่งให้แปลเมื่อภาษาเขียนต่างจากต้นฉบับ
    ว่างเมื่อผู้ใช้ไม่ได้เลือก source language (พฤติกรรมเดิม: ให้โมเดลตรวจภาษาเอง)"""
    _src = (source_lang or "").strip()
    if not _src:
        return ""
    _src_label = lang_prompt_name(_src)
    _out_label = lang_prompt_name(desc_lang or _src)
    if _src_label == _out_label:
        return (f"- SOURCE LANGUAGE: the pasted story is written in {_src_label}. Do not assume any other "
                f"language, and do not translate it — you are writing in {_out_label} as well.\n")
    return (f"- SOURCE LANGUAGE: the pasted story is written in {_src_label}, and you are writing in "
            f"{_out_label}. TRANSLATE as you go: every field you write must be {_out_label} only, with no "
            f"{_src_label} words, phrases, or script left in it — not in a name, a place, an object, or an "
            f"age note. Translate the MEANING of each beat; never copy a {_src_label} fragment because it "
            f"has no neat equivalent — find the nearest {_out_label} wording instead.\n"
            f"- NAMES ARE PART OF THE TRANSLATION: write every character name and every prop name in "
            f"{_out_label} script too — romanize a name that has no {_out_label} form (one Latin "
            f"spelling per person) and then use that ONE spelling in every field of every shot, never the "
            f"original script and never a second romanization of the same person.\n")

def _clothing_arc_lock_block(arc: str) -> str:
    """STORY-STATE-LEDGER-AND-TRANSFER-INSTANT-A1: the old free-prose per-person timeline was a
    no-op here (STATE INHERITANCE in Step 2 already covers generic "copy previous unless caused"
    continuity) — but the new STATE_LEDGER_V1 from Pass A is canonical item IDENTITY plus exact
    before/after transfer facts B cannot re-derive from STATE INHERITANCE alone (e.g. which
    physical object "the green shawl" and "jade cloak" both refer to, or who currently holds an
    item that left frame several shots ago). Surface it verbatim; B still authors current-shot
    clothing_state/prop_state prose itself."""
    _arc = (arc or "").strip()
    if not _arc or "STATE_LEDGER_V1" not in _arc:
        return ""
    return (
        "STORY STATE LEDGER (canonical item identity + before/after transfer truth from Pass A — "
        "use this as ground truth for who currently holds/wears/carries each tracked item; you "
        "still write this shot's own clothing_state/prop_state/scene_desc prose, in your own "
        "words, reflecting whichever ledger state is current as of this shot):\n"
        f"{_arc}"
    )

def _stamp_checker_prompt_trace(*, prompt_before: str, checker_llm_prompt: str,
                                prompt_after: str, shot: int | None = None) -> None:
    """Record that Checker LLM output was kept without semantic Python rewrite. Never raises."""
    if not _llm_debug_on():
        return
    try:
        path_s = _llm_debug_last_path.get()
        if not path_s:
            return
        path = Path(path_s)
        if not path.exists():
            return
        rec = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rec, dict):
            return
        rec["prompt_before_checker"] = prompt_before
        rec["checker_llm_prompt"] = checker_llm_prompt
        rec["prompt_after_checker"] = prompt_after
        rec["python_semantic_rewrite_applied"] = False
        if shot is not None:
            traces = rec.get("checker_prompt_traces")
            if not isinstance(traces, list):
                traces = []
            traces.append({
                "shot": shot,
                "prompt_before_checker": prompt_before,
                "checker_llm_prompt": checker_llm_prompt,
                "prompt_after_checker": prompt_after,
                "python_semantic_rewrite_applied": False,
            })
            rec["checker_prompt_traces"] = traces
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return

# R1-B: deterministic STEP2 structural guard. Detects three malformation patterns confirmed by
# live testing (not semantic judgment — audit_outline_shots already covers semantic ledger
# checks; this is schema/serialization only): a required clothing_state missing/empty for a
# shot with visible people, a forbidden literal placeholder ("empty string" etc.) standing in
# for a real empty value, and clothing_state/prop_state label text that leaked into scene_desc
# instead of populating its own field. Works on the PARSED shot dicts — confirmed sufficient
# because the leaked text survives parsing into scene_desc verbatim; no raw-LLM-text threading
# needed. code/group vocabulary matches _ISSUE_CODES/_ISSUE_GROUPS so these issues can feed
# straight into format_outline_repair_prompt/run_outline_shots_llm_repair unchanged.


# Fill copies these prefixes verbatim. Percentages are REGION ANCHORS, not exact rectangles —
# panel borders may be diagonal/irregular. Each preset keeps a different page balance.

# ลำดับที่ picker หมุนใช้ต่อจำนวนแผง — สองแบบขึ้นไปต่อจำนวน เพื่อไม่ให้หน้าติดกันหน้าตาเหมือนกัน

# คำที่บอกว่าบีตนั้นเป็นการปะทะ / การเปิดเผย / ภาพรายละเอียด / การเคลื่อนที่ — _manga_panel_role()
# ใช้ตัดสินบทบาทของแผง นิยามชุดนี้หายไประหว่างการจัดระเบียบไฟล์ ทำให้ manga page ล้มด้วย
# NameError ที่ pack_manga_pages — กู้กลับมาตามเดิม ไม่ได้แก้ pattern


# บทบาทของแผง — คำช่วยวางแผนเท่านั้น ห้ามให้ไปโผล่เป็นตัวหนังสือในภาพ


# MANGA-PRESENT-A1: explicit panel-line format — the LLM (Outline B, manga scene_desc) now states
# panel count/boundaries/role/event-ancestry directly, as SCENE: + P<n> [ROLE] [E#]: lines (see
# _STEP2_SCENE_DESC_MANGA / the slim swap in _step2_contracts_slim). This parser only recognizes that
# EXISTING structure — it never reads prose and invents panel boundaries or roles itself. When a page's
# scene_desc is not in this format (old saved outlines, a model that ignored the format), it returns
# None and the caller falls back to the legacy expand_events_to_panel_beats/_manga_panel_role path —
# that fallback is untouched by this round.
# ป้ายกำกับ P<n> เดียวกัน แต่ไม่ยึด ^ (ไม่ anchor ต้นบรรทัด) — ใช้แค่ "หา" ตำแหน่งที่ต้องขึ้นบรรทัดใหม่
# เมื่อโมเดลลืมขึ้นบรรทัดหลัง SCENE: (พบจริงจากรัน production: "...Dao's neck. P1 [ESTABLISHING]..."
# อยู่บรรทัดเดียวกับ SCENE) — นี่คือ syntax tolerance (ตำแหน่งขึ้นบรรทัด) ไม่ใช่การตีความความหมาย ตัว
# P<n>/[ROLE]/[E#]/เนื้อหา ยังต้องเป็นสิ่งที่โมเดลเขียนไว้ตรง ๆ เป๊ะเหมือนเดิมทุกตัวอักษร


# ── Manga panel contract ─────────────────────────────────────────────────────
# Narrative เป็นรูปแบบการเขียน ไม่ใช่ใบอนุญาตให้แต่งข้อเท็จจริงใหม่ — Manga Fill เคยย้ายตัวละครข้าม
# สถานที่ เสกของ เปลี่ยนสีผม และเล่าเหตุการณ์ทั้งช่วงลงในแผงเดียว เพราะสิ่งที่มันได้รับคือร้อยแก้ว
# ที่ตีความได้หลายทาง สัญญาแผงคือชุดข้อเท็จจริงเดียวกันนั้นในรูปที่ "ตรวจได้ด้วยโค้ด": ใครอยู่ ที่ไหน
# ทำอะไร ใส่อะไร ถือของอะไร ระยะภาพไหน — Fill ได้หน้าที่เดียวคือเขียนให้อ่านลื่น
# สัญญาเป็นของภายใน ไม่เคยถูกพิมพ์ลง prompt สุดท้าย (ดู _MANGA_CONTRACT_OUTPUT_RULE)


# สีผมที่ประกาศไว้ในชีต — ใช้ตรวจว่าปลายทางเปลี่ยนสีผมของใครหรือเปล่า


_PANEL_SPLIT_RE = _re_clothing.compile(
    r'(?:^|\n)\s*(?:P|Panel\s*)(\d+)\s*(?:[:.\)-]|—)', _re_clothing.I)

def _sanitize_check_json(raw: str) -> str:
    """แก้ dialogue field ที่ LLM ส่งมาเป็น {}{} ติดกัน ให้เป็น [{},{}] แทน"""
    import re as _re_sc
    out = []
    i = 0
    while i < len(raw):
        m = _re_sc.search(r'"dialogue"\s*:\s*', raw[i:])
        if not m:
            out.append(raw[i:])
            break
        out.append(raw[i: i + m.start()])
        out.append(raw[i + m.start(): i + m.end()])
        i = i + m.end()
        if i < len(raw) and raw[i] == '{':
            # scan object แรก
            depth = 0; end = i
            while end < len(raw):
                if raw[end] == '{': depth += 1
                elif raw[end] == '}':
                    depth -= 1
                    if depth == 0: end += 1; break
                end += 1
            # ดูว่ามี { object ถัดมาไหม
            rest = raw[end:]
            if _re_sc.match(r'\s*\{', rest):
                all_objs = raw[i:end]
                while True:
                    rest = raw[end:]
                    em = _re_sc.match(r'\s*(\{)', rest)
                    if not em: break
                    s2 = end + em.start(1)
                    depth2 = 0; end2 = s2
                    while end2 < len(raw):
                        if raw[end2] == '{': depth2 += 1
                        elif raw[end2] == '}':
                            depth2 -= 1
                            if depth2 == 0: end2 += 1; break
                        end2 += 1
                    all_objs += ',' + raw[s2:end2]
                    end = end2
                out.append('[' + all_objs + ']')
                i = end
            else:
                out.append(raw[i:end])
                i = end
        else:
            out.append(raw[i:])
            i = len(raw)
    return ''.join(out)

def _normalize_dialogue(dlg):
    """normalize dialogue field หลัง JSON parse — แปลง escaped JSON string, {}{}, หรือ duplicate keys เป็น list"""
    import json as _j, re as _r, ast as _ast
    if isinstance(dlg, (list, dict)):
        return dlg
    if not isinstance(dlg, str):
        return dlg
    s = dlg.strip()
    # ลอง json.loads ตรงๆ
    try:
        parsed = _j.loads(s)
        return parsed
    except Exception:
        pass
    # ลอง ast.literal_eval (single-quote)
    try:
        parsed = _ast.literal_eval(s)
        if isinstance(parsed, (list, dict)):
            return parsed
    except Exception:
        pass
    # แก้ duplicate "type": keys → array
    if s.startswith('{'):
        fixed = _r.sub(r',\s*"type"\s*:', '},{"type":', s, count=10)
        if fixed != s:
            try:
                return _j.loads('[' + fixed + ']')
            except Exception:
                pass
    # {}{} ติดกัน
    if _r.search(r'\}\s*\{', s):
        try:
            return _j.loads('[' + _r.sub(r'\}\s*\{', '},{', s) + ']')
        except Exception:
            pass
    return dlg

# AGENT-STORY-TRUTH-ROUTING-A1: shared story-source resolution for Checker/Director/Editor.
# Authority order: exact A1 macro_events content (verbatim) outranks everything else these agents
# reason from. Grok gets ALL blocks (global reasoning over the whole sequence); Local gets only
# the block(s) this ONE shot actually draws from, resolved from its "uses" field — never guessed.
def _a1_blocks_labeled(a1: dict | None) -> str:
    """ALL of A1's macro_events, labeled [E1]/[E2]/... in story order, content verbatim. Used for
    Editor (both backends) and Grok Checker/Director — global reasoning over the whole story."""
    if not isinstance(a1, dict):
        return ""
    events = a1.get("macro_events") or []
    parts = []
    for e in events:
        eid = str(e.get("id") or "").strip()
        content = str(e.get("content") or "").strip()
        if not eid or not content:
            continue
        parts.append(f"[{eid}]\n{content}")
    return "\n\n".join(parts)

def _a1_all_parent_ids(a1: dict | None) -> list[str]:
    if not isinstance(a1, dict):
        return []
    return [str(e.get("id") or "").strip() for e in (a1.get("macro_events") or []) if e.get("id")]

def _resolve_canonical_props(props_desc: str, a1: dict | None) -> str:
    """PROPS-SOURCE-OF-TRUTH-A1: props_desc (props_field) is the live, user-editable, persisted
    canonical Props sheet — a1["important_objects"] is only its A1-time initial seed and goes
    stale the moment the user edits Props in the Outline Editor or reopens a saved project whose
    props_desc diverged from outline_vnext_a1. props_desc wins whenever non-empty; the a1 copy is
    a fallback for callers that still only have a1 (e.g. a synthesized stand-in a1, or a direct
    caller that hasn't been updated to pass props_desc) — never a second simultaneous authority.
    Pure precedence resolution, shared by Checker and Fill — no schema, no side effects."""
    return str(props_desc or (a1 or {}).get("important_objects", "") or "").strip()

def _resolve_shot_parent_blocks(shot: dict, a1: dict | None) -> tuple[list[str], str]:
    """Derive THIS shot's parent macro-event id(s) from its 'uses' field (split each id on the
    first '.', dedupe, sort by story order), then pull each id's exact a1.macro_events content
    verbatim. Returns (parent_ids, labeled_text). If 'uses' is missing/unresolvable, returns
    ([], "") — callers must log parents=NONE and fall back to this shot/context only, never guess
    a parent block."""
    if not isinstance(a1, dict) or not isinstance(shot, dict):
        return [], ""
    events = a1.get("macro_events") or []
    order = {str(e.get("id") or "").strip(): i for i, e in enumerate(events)}
    content_by_id = {str(e.get("id") or "").strip(): str(e.get("content") or "").strip() for e in events}
    raw_uses = shot.get("uses")
    if not raw_uses:
        return [], ""
    if isinstance(raw_uses, str):
        raw_uses = [raw_uses]
    parent_ids: list[str] = []
    for u in raw_uses:
        u = str(u).strip()
        if not u:
            continue
        pid = u.split(".", 1)[0]
        if pid in content_by_id and pid not in parent_ids:
            parent_ids.append(pid)
    if not parent_ids:
        return [], ""
    parent_ids.sort(key=lambda pid: order.get(pid, 0))
    labeled = "\n\n".join(f"[{pid}]\n{content_by_id[pid]}" for pid in parent_ids)
    return parent_ids, labeled

@_llm_debug_stage("checker")
async def run_check_prompts(
    shots_data: list[dict],
    llm_wf_file: str,
    character_desc: str,
    scene_desc_global: str,
    requirement: str,
    story_text: str,
    story_summary: str,
    on_shot,           # async (idx, shot_data)
    on_err,            # async (msg)
    on_log=None,
    on_progress=None,
    stop_event: asyncio.Event | None = None,
    single_shot_idx: int | list[int] | set[int] | tuple[int, ...] | None = None,
    extra_req: str = "",
    image_style: str = "",
    genre: str = "",
    genre2: str = "",
    dialogue_style: str = "",
    dialogue_lang: str = "th",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    sys_variant: str = "",
    prompt_style: str = "narrative",
    manga_page: bool = False,
    outline_mode: str = "cinematic",
    props_desc: str = "",
    desc_lang: str = "",
    source_lang: str = "",
    fill_engine: str = "",
    narration_type: str = "caption",
    clothing_arc: str = "",
    a1: dict | None = None,
):
    """
    บรรณาธิการ LLM: ตรวจ prompt แต่ละ shot ว่าตรงกับ shot_desc (และฟิลด์ canonical อื่นๆ ของช็อตนั้น)
    ของเรื่องหรือไม่ ถ้าไม่ตรงหรือมีปัญหา → แก้ไข prompt ให้ แล้วส่งกลับ

    fill_engine: contract ที่ Fill ใช้เขียน prompt — Minimal V3 ห้ามแทรก IDENTITY LOCK แบบ Legacy
    ถ้าไม่ส่งมา อ่านจาก shot._fill_engine ที่ Fill ปั๊มไว้

    narration_type: ค่าเดียวกับที่ Outline B ใช้ ("caption" | "bridge") — ไม่ใช่ field ใหม่, ส่งมาจาก
    ค่า setting เดิมที่ caller มีอยู่แล้ว (main_ui _narration_type[0]). NARRATION-LYRIC-A1: ก่อนหน้านี้
    Checker ไม่รู้ narration_type เลย แล้วสั่ง "TEXT must EXPAND/INTENSIFY inner feeling — never just
    describe" แบบไม่มีเงื่อนไข ทำให้ Caption ที่ถูกต้องอยู่แล้ว (บรรยายสิ่งที่เห็น) โดนตีว่าผิดและถูกเขียน
    ใหม่ให้กลายเป็น Bridge — ตอนนี้แยกเงื่อนไขให้ตรงกับ mode ที่ Outline B ใช้จริง
    """
    # CHECKER-C1: single_shot_idx เดิมรับได้แค่ int เดียวหรือ None (ทั้งหมด) — auto-chain ฝั่ง Grok
    # ต้องการ "เฉพาะช็อตที่เพิ่งผ่าน Fill รอบนี้" ซึ่งเป็นเซตย่อยของ shots_data ไม่ใช่ทั้งหมดและไม่ใช่
    # ตัวเดียว — ขยายให้รับ list/set/tuple ได้ด้วย (mirror ของ DIRECTOR-C1 ใน run_director_prompts)
    # โดยยังคง behavior เดิมทุกจุดที่ส่ง int หรือ None มา
    _target_idxs: set[int] | None
    if single_shot_idx is None:
        _target_idxs = None
    elif isinstance(single_shot_idx, int):
        _target_idxs = {single_shot_idx}
    else:
        _target_idxs = set(single_shot_idx)
    _narr_bridge = narration_type == "bridge"
    _ck_narration_core = (
        "CORE PRINCIPLE (LITERARY BRIDGE): the IMAGE already carries what happens/who/where. TEXT must "
        "convey feeling, stakes, or inner cost as a continuing thread — anchor it to one concrete fact "
        "already in this shot or the story (an object, a surface, a physical sensation), never a "
        "generic abstract label (surrender, emptiness, ruin, darkness swallowing her) unless the source "
        "itself earns that word. BAD: \"She surrendered to the darkness.\" GOOD: \"The floor was still "
        "cold under her knees when she stopped fighting it.\""
    ) if _narr_bridge else (
        "CORE PRINCIPLE (VISUAL CAPTION): state one exact visible/physical fact from this instant, "
        "optionally followed by one meaning earned from that fact. Describing what a viewer sees IS the "
        "job here — do not flag a caption merely for describing the visible scene. Flag it only when it "
        "is a bare Name+verb+place stub with no meaning earned, or when it paraphrases shot_desc almost "
        "word-for-word. BAD: \"Mai kneels in the dark.\" GOOD: \"Knees on wet concrete, the dark makes "
        "the room feel bigger than the person kneeling in it.\""
    )
    _ck_narration_rewrite_trigger = (
        "If it only restates the pose with no earned meaning, or reads as a generic abstract slogan with "
        "no concrete anchor → REWRITE, anchored to one real fact in this shot/story — never invented "
        "backstory/memory/countdown the source does not establish"
    ) if _narr_bridge else (
        "If it is a bare Name+verb+place stub, or paraphrases shot_desc almost word-for-word → REWRITE "
        "as one exact image fact plus one meaning earned from it — never invented backstory"
    )
    _ck_narration_quality_short = (
        "TEXT must convey feeling/stakes anchored to one concrete fact, as a continuing thread — never a "
        "generic abstract slogan (surrender, emptiness, ruin, darkness swallowing her) unless the source "
        "itself earns that word."
    ) if _narr_bridge else (
        "TEXT must state one exact visible fact from this instant, optionally with one meaning earned "
        "from it — describing what is seen is correct here. Flag only a bare Name+verb+place stub or a "
        "near word-for-word shot_desc paraphrase."
    )
    _ck_narration_field_desc = (
        "rewrite if it only restates the pose or is a generic abstract slogan with no concrete anchor; "
        "else omit this key"
    ) if _narr_bridge else (
        "rewrite if it is a bare Name+verb+place stub or paraphrases shot_desc; else omit this key"
    )
    # sys_variant คุมขนาด system prompt เท่านั้น ไม่แตะสัญญาเชิงพฤติกรรมของ stage ใด
    _variant, _variant_why = _sys_variant_of(grok_api_key, sys_variant, llm_wf_file)
    if on_log:
        await on_log(f"⚙ Checker: variant={_variant} ({_variant_why})")
    _src_note = _source_lang_note(source_lang, desc_lang or dialogue_lang)
    _ck_engine = normalize_fill_engine(fill_engine)
    if not (fill_engine or "").strip():
        for _s in shots_data or []:
            if isinstance(_s, dict) and _s.get("_fill_engine"):
                _ck_engine = normalize_fill_engine(_s.get("_fill_engine"))
                break
    _ck_v3 = _ck_engine in _LOCK_CONTRACT_ENGINES
    # CHECKER-VNEXT-NATIVE-CANONICAL-RULE-A1: which canonical-rule vocabulary to inject is a question
    # of what FIELD NAMES the shot dict actually carries, not of prompt FORMAT — manga_page only
    # changes the output format (multi-panel prose) of shots that already went through run_b_vnext /
    # run_fill_vnext, so a manga_page=True shot still carries native fields (shot_desc/clothing/
    # objects/location/camera), same as manga_page=False. _ck_v3 alone is the real native-vs-legacy
    # field-schema signal (same gate _id_rule/_loc_rule/_obj_rule already use above/below, deliberately
    # WITHOUT "and not manga_page" — that qualifier belongs only to structurally single-image features
    # like the OBJECT LOCK scoped repair, never to which field names exist in the data). Only a shot
    # whose _fill_engine is neither "minimal_v3" nor "vnext" (an old save file predating this
    # migration, or a manual edit) is _ck_v3=False and still needs the legacy
    # _CANONICAL_STATE_RULE/_CANONICAL_CONFLICT_GATE_RULE wording.
    _ck_is_vnext_native = _ck_v3
    _ck_canon_state_rule = _CANONICAL_STATE_RULE_VNEXT if _ck_is_vnext_native else _CANONICAL_STATE_RULE
    _ck_canon_gate_rule = _CANONICAL_CONFLICT_GATE_RULE_VNEXT if _ck_is_vnext_native else _CANONICAL_CONFLICT_GATE_RULE
    _id_rule = _MINIMAL_V3_CHECKER_IDENTITY_RULE if _ck_v3 else _IDENTITY_LOCK_CHECKER_RULE
    _loc_rule = _MINIMAL_V3_CHECKER_LOCATION_RULE if _ck_v3 else ""
    _obj_rule = ((_MINIMAL_V3_CHECKER_OBJECT_RULE + _MINIMAL_V3_CHECKER_OBJECT_SCOPED_REPAIR_RULE)
                 if _ck_v3 else "")
    _id_editor_never = (
        "- NEVER delete the IDENTITY LOCK — it is required for every visible named character, solo "
        "shots included. Correct a wrong lock line against the Outline; if it is already correct, "
        "preserve it. Never duplicate it, never repeat identity or clothing elsewhere, and never "
        "replace the block with prose."
        if _ck_v3 else
        "- NEVER delete IDENTITY LOCK. Correct a wrong lock line against Outline; if already correct, preserve it. Never summarize it away or replace it with prose."
    )
    _loc_editor_never = (
        "- NEVER delete the LOCATION LOCK — required exactly once. Correct it against this shot's "
        "location field if wrong; if already correct, preserve it verbatim. Never "
        "duplicate it, never fold it into Environment, and never rewrite it just because the "
        "camera or composition changed."
        if _ck_v3 else ""
    )
    _obj_editor_never = (
        "- OBJECT LOCK, when this shot has one: correct it only against your own canonical props "
        "evidence, for two kinds of drift — wrong type/material/color/shape, OR current-state "
        "wording (parked/moving, open/closed, wet, damaged right now, held, current location/owner, "
        "speed, orientation) that leaked into the lock instead of the permanent identity — strip "
        "state wording back out so only canonical permanent identity remains. Never redesign it "
        "beyond canonical evidence, never invent detail canonical evidence doesn't support, never "
        "make an absent object appear, never split it into more than one block. If it is already "
        "clean and canonical-accurate, preserve it verbatim. If this shot has no current physical "
        "object at all, do not add an OBJECT LOCK block."
        if _ck_v3 else ""
    )
    if _ck_v3:
        _format_rule = _minimal_v3_format(prompt_style, manga_page)
        _prompt_field_desc = _minimal_v3_field_desc(prompt_style, manga_page)
    else:
        _format_rule, _prompt_field_desc = _build_prompt_format_rule(prompt_style)
    if on_log:
        await on_log(f"Checker identity contract: {fill_engine_label(_ck_engine)}")
    wf_template = None
    if llm_wf_file != GROK_MARKER:
        llm_wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not llm_wf_path.exists():
            await on_err(f"ไม่พบ {llm_wf_path}"); return
        try:
            wf_template = json.loads(llm_wf_path.read_text(encoding="utf-8"))
        except Exception as e:
            await on_err(str(e)); return

    loop = asyncio.get_event_loop()
    num = len(shots_data)

    # สร้าง outline summary เพื่อให้ LLM เห็นภาพรวม
    outline_lines = []
    for i, s in enumerate(shots_data):
        outline_lines.append(f"Shot {i+1}: {s.get('shot_desc','')}")
    outline_summary = "\n".join(outline_lines)

    char_str  = (
        "Character sheet (IDENTITY SOURCE — who appears is shot_desc; name spelling, age, "
        f"nationality, gender, hair, build come from this sheet): {character_desc}"
        if character_desc else "ไม่ระบุตัวละคร"
    )
    # PROPS-DEDUP-CHECKER: ONE resolved canonical Props source (props_desc, falling back to
    # a1["important_objects"] only when props_desc is empty — see _resolve_canonical_props),
    # serialized into exactly ONE Props block below. Previously this legacy props_str (built
    # straight from props_desc, no a1 fallback) and a second "_ck_canonical_objects" block (with
    # the fallback) were BOTH injected into the same effective Checker prompt — redundant and
    # inconsistent (only the second had the fallback). Now props_str itself carries the resolved
    # value and is the only Props block Checker ever sends.
    _ck_canonical_objects = _resolve_canonical_props(props_desc, a1)
    props_str = f"Props sheet (the fixed permanent facts of each object, written in the story's language — for an object present in this shot, every fact must be correctly represented ONCE, in the single place this prompt's format contract assigns it — the OBJECT LOCK when this shot has one, otherwise the body; already represented there, it is NOT also owed a repeat elsewhere in the prompt. Its type/material/color never upgrades into a fancier or more specific class than given, e.g. a plain car never becomes a \"luxury sedan\"): {_ck_canonical_objects}" if _ck_canonical_objects else ""
    scene_str = f"Scene: {scene_desc_global}" if scene_desc_global else "ไม่ระบุฉาก"
    req_str   = f"Special requirements: {requirement}" if requirement else ""
    extra_str = f"\n⚠ EXTRA REQUEST FROM USER: {extra_req}" if extra_req else ""
    style_str = f"Image Style: {image_style}" if image_style else ""
    genre_str  = _build_genre_str(genre, genre2)
    _nsfw = is_nsfw(genre, genre2)
    _dlg_style_labels = {
        "speech_bubble":    "Speech Bubble — injected in prompt by system",
        "narration_box":    "Narration Box AI — prompt ends with 'comic book style manga text overlay:...' suffix — MUST be preserved",
        "narration_box_pil":"Narration Box PIL — NOT in prompt",
        "subtitle_bar":     "Subtitle Bar PIL — NOT in prompt",
        "none":             "None",
    }
    dlg_style_str = f"Dialogue Display Style: {_dlg_style_labels.get(dialogue_style, dialogue_style)}" if dialogue_style else ""
    story_ctx = ""
    # AGENT-STORY-TRUTH-ROUTING-A1: when a1 (exact A1 macro_events) is available, story truth is
    # sourced from it instead of raw story_text — Local gets only the ONE shot's resolved parent
    # E[n] block(s) (injected per-shot into the user prompt below, never here in the shared/cached
    # system prompt), Grok gets ALL blocks (appended once to the shared Grok batch system prompt at
    # the batch-build site below). Leave story_ctx empty here in both cases so the cached
    # _editor_sys_for() template never bakes in story text that would be wrong for the other
    # backend. Fallback to the old "Full Story" full-text behavior only when no a1 was passed
    # (back-compat for any caller not yet updated) — never a silent quality regression.
    if not a1 and story_text:
        story_ctx += f"Full Story:\n{story_text}\n\n"
    _ck_all_blocks = _a1_blocks_labeled(a1) if a1 else ""
    # (canonical Props resolution now happens once, above, into props_str — see
    # PROPS-DEDUP-CHECKER. OBJECT LOCK repair evidence, when it exists, will read that same
    # props_str/_ck_canonical_objects value; this comment intentionally left as a pointer, not a
    # second computation.)

    # editor_sys เดิมสร้างก้อนเดียวใช้ร่วมทุกช็อต จึงแบก VISIBILITY GATE ตัวเต็ม (กฎของ wide/extreme
    # wide เกือบทั้งก้อน) ไปกับช็อต medium/close-up ที่กฎนั้นบอกเองว่า "correct and expected"
    # แยกตามระยะแล้ว memoize เหมือน run_fill_prompts — ladder วนแค่ 5 ระยะ
    _editor_sys_cache: dict[str, str] = {}

    # STORY-STATE-LEDGER-AND-TRANSFER-INSTANT-A1: reuse the existing shot dict Checker already
    # receives (clothing/objects/shot_desc) — no new per-shot context object, only a
    # static issue-class block plus the same ledger text B already saw, so Checker can compare a
    # shot's decided state against canonical ground truth instead of only against the Full Story.
    _ck_ledger_block = _clothing_arc_lock_block(clothing_arc)
    _ck_state_issue_classes = (
        "\nTRACKED ITEM LEDGER (canonical evidence for items the story moves between people and places "
        "— repair the PROMPT against it, never rewrite the canonical fields themselves):\n"
        f"{_ck_ledger_block}\n"
        "Report, rather than silently resolve: a tracked item whose holder or mode in this shot "
        "conflicts with the ledger's state as of this shot; a transfer this shot covers that the "
        "prompt shows neither happening nor already done; an item drawn on a character or in a place "
        "the ledger does not support; and any canonical field still carrying \"(Implied)\", "
        "\"probably\", \"maybe\" or other unfinished reasoning instead of a decided fact.\n"
        if _ck_ledger_block else ""
    )

    def _editor_sys_for(scale: str) -> str:
        key = ("" if manga_page else (scale or "medium").strip().lower()) + f"|{int(_ck_v3)}|{_variant}"
        if key in _editor_sys_cache:
            return _editor_sys_cache[key]
        if _ck_v3:
            _fmt_rule = _minimal_v3_format(prompt_style, manga_page)
        else:
            _fmt_rule, _ = _build_prompt_format_rule(prompt_style, "" if manga_page else (scale or "medium").strip().lower())
        editor_sys = f"""You are an independent quality judge for image prompts.

ROLE
You did not design this shot and you are not here to make it prettier. You answer exactly one question:
does the Current Prompt faithfully express the canonical shot? When it does, you leave it alone. When it
does not, you make the SMALLEST repair that makes it faithful again. You repair the PROMPT; you never
rewrite the canonical facts themselves.

MATERIAL / SOURCE OF TRUTH
The exact story source for this shot, the production bible below, the canonical Props sheet, this shot's
own canonical fields (shot_desc, clothing, objects, location, camera, plus visible_people and lighting
where relevant), the surrounding shots, and the prompt's required format and lock contract.

{story_ctx}{char_str}
{props_str}
{scene_str}
{style_str}
{genre_str}
{dlg_style_str}
{req_str if req_str else ""}{extra_str}
{_ck_state_issue_classes}
{_src_note}GOAL
Make the prompt faithful to canonical truth with the smallest necessary repair. Judge semantically, not by
keyword matching: wording that differs from the canonical phrasing but depicts the same real fact is
CORRECT and must not be flagged. A prompt that is merely not the one you would have written is correct.

YOUR AUTHORITY — you may repair only a justified mismatch: a wrong story fact; a missing required canonical
fact; an unsupported invented fact; the wrong visible cast; identity drift; clothing drift; a
clothing-state contradiction; object identity drift; object state or ownership drift; a location
contradiction; a spacing contradiction; a violation of the required lock or format structure.

YOU MAY NOT: improve the composition because you prefer another one, make the action more dramatic,
strengthen an emotion for style, choose a nicer camera, add a person, add a prop, add scenery, redesign the
shot, or resolve an ambiguous upstream canonical conflict by guessing. If the canonical evidence itself
conflicts, REPORT the conflict in `issues` and leave the prompt as it is — never invent the answer.

{_ck_canon_state_rule}
(shot_desc, clothing, objects and camera are never yours to improve away from.
Repair the PROMPT to match them, never the reverse.)

{_ck_canon_gate_rule}

HARD INVARIANTS — these state WHAT must be true of the prompt. They do not tell you how to reason, and
they are not a checklist of defects to hunt for.
  • VISIBLE CAST is exactly the people this shot's shot_desc names — nobody missing, nobody extra. The
    character sheet supplies identity, never presence; clothing and objects describe
    the roster but never extend it. A person the story merely places in the same room, boat or house, whom
    shot_desc does not name, is NOT in this shot — leave them out and do not report their absence. A
    person shot_desc DOES name must be in the prompt with a real body position and role in the action,
    however distant they are.
  • IDENTITY comes from the character sheet and only from it: name in Latin script, age digits, nationality
    for a human or kind for a non-human, an explicit gender noun, build, hair, and every permanent body
    trait the sheet states (a prosthetic or missing limb, a scar, wings, a tail, horns, non-human anatomy).
    A sheet trait that the framing WOULD show and the prompt omits is a defect; a trait the framing cannot
    show (wings on a face close-up) is correctly absent. WHAT THE SHEET DOES NOT SAY, YOU DO NOT ADD — an
    invented nationality, eye colour or any other unsupported trait is itself the defect, and one person's
    trait must never migrate onto another. Never leave a present person as "a woman", "a figure" or a
    silhouette with no identifying label and no gender noun. A name in another script resolves to that same
    person, written in English — never paste the sheet's non-English text into an already-correct English
    prompt.
  • CURRENT STATE is this shot's own fields. Clothing: each remaining garment is owner-bound on the person
    wearing it ("Name wearing <piece>"), for background and distant people exactly as much as for the
    leads; distance may drop weave and texture, never a garment's colour or noun. The tracker prefixes
    (CLOTHED / PARTIALLY UNDRESSED / TOPLESS / NUDE) are labels for the field, never words to paste into an
    image prompt — judge the state by its plain meaning against what the prompt actually depicts.{" NUDE means no remaining garment is drawn back on; TOPLESS means the chest reads bare while any lower garments the field still lists stay worn; PARTIALLY UNDRESSED means the listed garments stay worn and are not removed further; CLOTHED means no nudity or dropped garment appears." if _nsfw else ""} Objects: the objects field
    decides which story objects are in this frame, who holds them, in which hand, and what is set down
    where — held never becomes placed, or the reverse, without the field saying so, and how an object is
    attached to the world (hung, resting, tied, planted, carried) persists until the field changes it.
  • NO UNSUPPORTED PERSON OR OBJECT. The Props sheet describes WHAT a persistent object permanently is; it
    can never by itself make an absent object appear in a shot. Presence comes from this shot's
    objects/shot_desc alone.
  • CANONICAL COMPLETENESS BY REPRESENTATION AUTHORITY. For every person and object that IS in this shot,
    the relevant stable facts their canonical entry supports must survive SOMEWHERE in the prompt, in
    whichever single place this format's lock contract assigns responsibility for that layer of fact —
    IDENTITY LOCK for a person's permanent identity, OBJECT LOCK for a persistent object's permanent
    identity, the body for current visible action/state/composition. A fact that is already correctly
    represented in its owning lock is FULLY REPRESENTED — the body does not also owe it a repeat, and its
    absence from the body is not a defect. Flag an omission only when the fact is missing from the place
    that actually owns it: e.g. canonical "dark-blue compact hatchback, four-door" rendered in OBJECT LOCK
    as only "dark-blue compact hatchback" has lost a permanent identifying fact from its owning lock and
    must be repaired there; the same OBJECT LOCK already stating "four-door" does not need the body to say
    "four-door" again when the body later just says "the car races along the road" — that is correct as
    written. Likewise a lock never owes the body's current transient state (wet, moving, door open), and
    the body never owes the lock's permanent identity a second time. Never upgrade a type into a fancier
    class than the sheet gives (a plain car never becomes a "luxury sedan"), and never add detail the
    canonical evidence does not support.
  • LOCATION AND SPACE. The prompt may contain only what is visible from THIS shot's location; a landmark
    from an abandoned location or a place-noun shot_desc never names is invention. Spacing is how far the
    PEOPLE stand, not how close the camera is — a FAR or APART gap must be physically visible (separate
    depth planes, different relative scale, a large uninterrupted stretch of this environment or a named
    separator BETWEEN them, an interaction that is not a facing two-shot), and a NEAR gap must not be
    stretched into distance the story does not have. Repairing spacing changes only spatial and
    compositional language — never pose, clothing, identity, event or speech.
  • PHYSICAL POSSIBILITY. Every light in the prompt comes from something this scene contains; at night,
    indoors or under heavy cloud an overhead shaft, high-key light or rim light has no emitter. Anatomy and
    interaction must be physically possible for the established bodies.
{_ANATOMY_INTERACTION_RULE}
  • LANGUAGE. The image prompt is 100% English in Latin script — any word left in the story's own script is
    a defect, replaced by a romanized or translated equivalent.
  • "READS" IS AMBIGUOUS. "reads small", "reads distant", "the composition reads as..." registers to an
    image model as the literal act of reading and has produced hallucinated books and reading poses.
    Rewrite it to the direct drawable meaning ("appears small and distant", "dominates the composition")
    and change nothing else — never the scale or distance fact itself, and never by adding a negative prop
    ("no book"). A literal reading action the source supports stays exactly as written.
  • STYLE COMPATIBILITY. The prompt's vocabulary must not clash with the selected Image Style
    (photographic detail for Realistic; no photo vocabulary for illustrated styles; no lens/aperture
    language for painterly styles). Fix word choice only, and only for a real clash — this is a format
    check, never a creative-quality pass.
  • STRUCTURE. The prompt's format family and its lock contract survive the repair unchanged.
{_id_rule}
{_loc_rule}
{_obj_rule}
{"PANEL CONSISTENCY (manga mode): panels are cuts of the SAME moment. Appearance, clothing state, setting and light must be identical across every panel of this shot, and each panel must show a different angle or zoom of the same ongoing action, not a later event." if manga_page else ""}
  • ANIMALS: an animal described in the character list keeps its exact canonical description wherever it
    appears. ABSOLUTE, regardless of genre: an animal is never sexualized, described nude, given human
    sexual anatomy, or placed in a sexual act. Rewrite it into a normal non-sexual element or remove it.

NARRATION & SPEECH
{_ck_narration_core}
dialogue_style controls what the outline stage AUTO-GENERATES; it is never a ban on a field that already
has text. An EMPTY field stays empty — never invent narration or a line of dialogue, however natural one
would feel. A FILLED field stays filled and is judged normally, even for a style that would not have
generated it. Language must be {lang_prompt_name(dialogue_lang)}; narration is at most 40 words; speech is
at most 10 words, belongs to a character shot_desc actually names, and must sound like that specific
person rather than generic AI dialogue. {_ck_narration_rewrite_trigger}. Narration that names an emotion
outright ("she was terrified") is rewritten to show it physically. If you report a narration or speech
issue, you must fix that field in the output JSON. speech_char is never yours to judge or return — it
keeps the outline's own spelling in its own script and is copy-only; only the image prompt is English.
{_POSITIVE_EXPRESSION_RULE}

REPAIR DISCIPLINE
- BURDEN OF PROOF — KEEP IS THE DEFAULT: you need a specific piece of canonical evidence that the Current
  Prompt contradicts or is materially incomplete against, IN THE PLACE RESPONSIBLE FOR REPRESENTING THAT
  FACT, before you may report an issue. Do not edit merely because a canonical source contains more detail
  than one sentence of the prompt states, and do not treat a fact already correctly represented in its
  owning lock as missing because the body does not also repeat it. A stylistic preference, an alternate
  valid wording for the same real fact, an optional detail, or something invisible/unnecessary at this
  shot's scale is not an error. If you cannot point to the canonical evidence and the specific place it
  proves wrong or incomplete, KEEP the prompt unchanged.
- If everything is faithful → keep_original: true, issues: [].
- Flag only a MATERIAL error — one that would change what the generated image shows. A wording preference,
  a stylistic difference, or a harmless omission invisible at the assigned distance is not an issue, and
  neither is prose that could merely be "better". You are a safety net, not a rewrite pass.
- If you do find issues → keep_original: false, list them, then make the smallest rewrite that fixes them.
  Read the whole original prompt first and rewrite it in the SAME format, preserving everything that was
  already correct — appearance, outfit state, camera, lighting, unrelated details.
- SURGICAL SCOPE: touch only the block(s) your listed issues are actually about. An issue about OBJECT LOCK
  authorizes changing OBJECT LOCK, never IDENTITY LOCK; an issue about the body authorizes changing the
  body, never a lock. Copy every block your issues do not name byte-for-byte from the input, including
  every trait already in IDENTITY LOCK/LOCATION LOCK/OBJECT LOCK — a repair for one fact is never an
  invitation to also shorten, "clean up" or re-word a block nothing was reported wrong with.
- REPAIR COMPLETENESS — before you output: re-read your own revised prompt against every issue you just
  listed and confirm each one is actually gone from it, not merely mentioned in `issues`. If you cannot
  make a repair you can justify from canonical evidence, do not invent one — report the conflict in
  `issues` and return the prompt unchanged instead.
- KEEP/EDIT CONSISTENCY: keep_original=true means the prompt is semantically ACCEPTED AS-IS — do not
  intentionally rewrite it; when keep_original=true, output prompt="KEEP" (the literal sentinel string,
  never a rewritten version of the input and never the input copied back). This is enforced
  deterministically downstream: the application layer reuses the original prompt whenever it sees the
  "KEEP" sentinel, so anything else you put in `prompt` for a keep_original=true response is simply never
  read.
  keep_original=false means at least one material defect was proven AND the returned prompt (or, for a
  scoped OBJECT LOCK repair, the returned object_lock_replacement) resolves it — never pair it with an
  empty `issues` list, and never leave the reported defect still present.
- For adult scenes keep the same plain visual wording as the original — never make a shot more explicit
  than its canonical state.
{_id_editor_never}
{_loc_editor_never}
{_obj_editor_never}
- DIALOGUE OVERLAY: with dialogue_style="narration_box" the prompt ends with a "comic book style manga text
  overlay:..." suffix — PRESERVE it exactly and edit only the body before it. With "speech_bubble" do not
  add or remove bubble text yourself. With "narration_box_pil" or "subtitle_bar" the prompt should contain
  no text-overlay instruction.
- You fix the prompt, not the story: never output an empty clothing-related field, never reorder
  shots, and never rewrite shot_desc / clothing / objects / location
  themselves — a wrongness in one of those fields is an upstream canonical shot-data defect; report it in
  `issues` and leave it as-is rather than guessing a fix. Canonical fact outranks visual improvement. Do not
  emit clothing or objects in the output.
{("MULTI-PANEL FORMAT LOCK: prompt opens with 'N-panel page...' (N is a digit) — SACRED. NEVER rewrite it into single-image format. Fix only content inside panel descriptions." if manga_page else _fmt_rule)}

BEFORE YOU SUBMIT — QUALITY JUDGE'S CHECK
Silently ask yourself these questions and act on what they expose. Do not output the questions or any
reasoning.
1. PROOF — what specific canonical evidence proves a material mismatch? If you cannot name it, KEEP.
2. REPRESENTATION — is the allegedly missing fact actually required in the component you are about to edit,
   or is it already correctly represented in its own lock (so the body owes it nothing further)?
3. FIDELITY — does the prompt preserve the intended story moment, cast, identity, current state, location
   and spacing?
4. COMPLETENESS — for required canonical facts, in the place that owns them, is anything materially
   missing?
5. INVENTION — did the prompt add anything the canonical evidence does not support?
6. REPAIR — if you found a defect, does your revised prompt completely fix it without changing unrelated
   correct content?
7. STRUCTURE — does it still satisfy the required prompt format and lock contract?
8. KEEP — if no material mismatch is proven, return the original prompt unchanged.

Output ONLY a valid JSON object, nothing else.

Output format — TRANSPORT SIZE MATTERS: never copy back text you did not change; every field below that
you leave at its input value must be OMITTED from your JSON entirely, not repeated verbatim.
{{
  "shot": <number>,
  "keep_original": <true|false>,
  "issues": ["<short English, max 8 words>"],
  "reason": "<one-line English, max 10 words — 'OK' if no issues>",
  "repair_scope": "<'object_lock' only when the entire proven defect is solved inside OBJECT LOCK alone with no other change needed — otherwise 'none'>",
  "object_lock_replacement": "<only when repair_scope='object_lock' — the complete corrected OBJECT LOCK block, header line plus content, nothing else; omit/empty otherwise>",
  "prompt": "<when keep_original=true (including when repair_scope='object_lock'), return exactly the sentinel string \"KEEP\" — do NOT copy the original prompt text back, it is discarded and never read; when keep_original=false, the full revised prompt in the required format — {_prompt_field_desc}>",
  "narration": "<OMIT this key unless you are rewriting it — wrong language, empty-of-earned-meaning, or too long; when present, must be in {lang_prompt_name(dialogue_lang)}>",
  "subtitle": "<OMIT this key unless you are rewriting it — wrong language or genuinely wrong for this character/moment; when present, must be in {lang_prompt_name(dialogue_lang)}>"
}}
speech_char is never part of your output — it is copy-only and stays whatever it already was."""

        editor_sys = _re_clothing.sub(r'\n{3,}', '\n\n', editor_sys).strip() + "\n"
        _editor_sys_cache[key] = editor_sys
        return editor_sys

    # System prompt ย่อสำหรับ attempt 4 (ลด token)
    editor_sys_slim = f"""You are an independent quality judge for image prompts. You did not design this
shot and you are not here to improve it. One question only: does the Current Prompt faithfully express this
shot's canonical facts (shot_desc, clothing, objects, location, camera)? Repair the
PROMPT with the smallest change when it drifts; never rewrite the canonical facts, and never touch a prompt
that already matches them. Judge semantically — different wording for the same real fact is correct.
{char_str}
{props_str}
{style_str}
{req_str if req_str else ""}{extra_str}
{_ck_canon_state_rule}

{_ck_canon_gate_rule}
{("TRACKED ITEM STATE: also report a tracked item whose holder/mode conflicts with the story state ledger "
  "below, a covered ledger transfer the prompt does not show, an item drawn on a character or place the "
  "ledger does not support, and any canonical field still carrying \"(Implied)\"/hedging instead of a "
  "decided fact — report those, never silently resolve them.\n"
  + _ck_ledger_block) if _ck_ledger_block else ""}

HARD INVARIANTS
{"⚠ MULTI-PANEL MODE — the prompt is intentionally 'N-panel page...' (N is a digit). Do NOT flag panel layout, per-panel framing, multiple paragraphs or non-continuous prose; none of those is a violation here." if manga_page else ""}
- CANONICAL STATE MAY ITSELF BE WRONG — DO NOT LAUNDER IT. Your normal job is to make the prompt match this
  shot's canonical state; but where that state contradicts ITSELF (a state word fighting its own garment
  list, a garment belonging to a different character, a garment worn with no moment that put it back on, a
  person in the state who is not in this shot), report it in `issues`, say which character and which two
  facts disagree, and do NOT invent a resolution inside the prompt — that fix belongs upstream. A
  deliberate user edit that is internally consistent is NOT a contradiction: accept the new state rather
  than dragging it back to an older one because continuity feels safer.
- THE QUESTION YOU ARE ANSWERING is whether the prompt matches this shot's canonical beat and state, never
  "could this be more explicit?" Adding exposure, contact or a stronger act is a defect, not a fix.
- VISIBLE CAST is exactly this shot_desc's people; identity comes from the character sheet (name, age,
  nationality or kind, explicit gender noun, build, hair, permanent body traits) and nothing may be
  invented that the sheet does not state.
- CURRENT STATE: clothing's garment list is ground truth, owner-bound per person, including
  secondary and background people; the objects field decides which objects are present, who holds them and where
  they sit. The Props sheet gives appearance only — it never puts an object into a shot.
- CANONICAL COMPLETENESS BY REPRESENTATION AUTHORITY: for each present person and object, the permanent
  facts their canonical entry supports must survive in the ONE place this format assigns them (IDENTITY
  LOCK for a person, OBJECT LOCK for an object, otherwise the body). A fact already correct there is fully
  represented — the body owes it no repeat, and its absence from the body alone is not a defect. Flag an
  omission only where the fact is missing from the place that owns it. KEEP is the default: without
  specific canonical evidence naming the owning place that is wrong or incomplete, do not edit.
- Anatomy must be physically possible for the established bodies; a limb, prosthetic, wing or tail is never
  invented, dropped, duplicated, normalized, or promoted into a second person. Every visible hand names its
  contact point, and where the beat has no exposure or contact for someone, anatomy is not described for
  them at all.
- Light comes only from a source this scene contains. Place and spacing are faithful — FAR/APART needs
  visible distance evidence, NEAR must not be stretched.
- Expression must not contradict what shot_desc establishes for this beat. How strongly an emotion is
  performed is a user setting, not yours to re-judge: a restrained rendering of the right emotion is
  correct. A conscious present character is never "expressionless"/"blank". If subtitle is non-empty, the
  speaker's mouth is not closed or pressed. Do not flag "calm"/"relaxed"/"soft" when the prompt says what
  that looks like.
{_POSITIVE_EXPRESSION_RULE}
- Prompt wording must be compatible with the Image Style if one is set — flag only a real clash, never a
  preference. Generic-but-complete environment/lighting wording is not a defect; do not rewrite for
  vividness.
- ANIMALS (ABSOLUTE, any genre): never sexualized, nude, given human sexual anatomy, or in an explicit act.
- NARRATION/SPEECH — dialogue_style={"'" + dialogue_style + "'"}: none→all empty; speech_bubble→narration
  empty; narration_box_pil→speech empty (narration≤60w); subtitle_bar→narration empty (speech≤40w). A
  forbidden field is simply emptied — never judged, never reported. An empty allowed field stays empty.
  Language={"Thai" if dialogue_lang=="th" else "English"}. Quality, for allowed and already-filled fields
  only: {_ck_narration_quality_short}
- DIALOGUE OVERLAY: with dialogue_style="narration_box" the prompt ends with a "comic book style manga text
  overlay:..." suffix — keep it unchanged and edit only the body before it.
- STRUCTURE: {"MULTI-PANEL FORMAT LOCK — the prompt starts with 'N-panel page...' (N is a digit); this format is SACRED and never becomes a single-image prompt. Only edit content inside the panel descriptions." if manga_page else _format_rule}
{("- SCOPED OBJECT-LOCK REPAIR: if the ONLY proven defect is inside OBJECT LOCK and needs no other change, "
  "set repair_scope=\"object_lock\" and put just the corrected OBJECT LOCK block (header line + content, "
  "nothing else) in object_lock_replacement instead of rewriting the whole prompt; otherwise leave "
  "repair_scope=\"none\" and use the normal prompt field.") if (_ck_v3 and not manga_page) else ""}
- You fix the prompt text only: never empty an already-filled clothing/narration, never reorder
  shots, never rewrite shot_desc/clothing/objects — a defect there is an upstream
  canonical shot-data defect; report it in `issues` rather than resolving it yourself.

BEFORE YOU SUBMIT, silently ask: what specific canonical evidence proves a mismatch, and is it missing from
the place that actually owns that fact (not merely unrepeated elsewhere)? same story moment? exactly the
intended cast, with supported identity? current clothing/object/ownership state right? place and space
faithful? anything invented? if you list an issue, does your revised prompt actually remove it, with
everything else unchanged? format and locks intact? and if no material mismatch is proven — keep_original
true, prompt field set to the sentinel "KEEP" (never the copied original text — it is discarded and never
read).
TRANSPORT SIZE MATTERS: omit narration/subtitle entirely from your JSON when you did not change them —
never copy them back unchanged. speech_char is never part of your output; it is copy-only.
Output ONLY valid JSON:
{{"shot":<n>,"keep_original":<bool>,"issues":["<short English max 8 words>"],"reason":"<English max 10 words>","repair_scope":"<object_lock|none>","object_lock_replacement":"<only if repair_scope=object_lock>","prompt":"<\"KEEP\" if keep_original=true, else {_prompt_field_desc}>","narration":"<omit unless changed — {_ck_narration_field_desc}>","subtitle":"<omit unless rewritten — rewrite only if wrong language or genuinely wrong for this character>"}}"""


    import re as _re3

    def _parse_check(raw_text):
        t = raw_text.strip()
        # ลอก markdown fence ทุกรูปแบบ (รวม ```json\n{ และ ``` ท้าย)
        t = _re3.sub(r'```+(?:json)?\s*', '', t).strip()
        # หา JSON object
        try:
            s = t.index('{'); e = t.rindex('}') + 1
            chunk = t[s:e]
        except ValueError:
            return None
        # pass 1: ลอง parse ตรงๆ (พร้อม sanitize dialogue field)
        try:
            chunk_s = _sanitize_check_json(chunk)
            obj = json.loads(chunk_s)
            if isinstance(obj, dict) and "prompt" in obj:
                if "dialogue" in obj: obj["dialogue"] = _normalize_dialogue(obj["dialogue"])
                return obj
        except Exception:
            pass
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict) and "prompt" in obj:
                if "dialogue" in obj: obj["dialogue"] = _normalize_dialogue(obj["dialogue"])
                return obj
        except Exception:
            pass
        # pass 2: fix unescaped newlines/tabs ภายใน string values
        # ใช้ state machine ที่ handle escaped chars (\", \\) ถูกต้อง
        res = []
        in_s = False
        esc = False
        for c in chunk:
            if esc:
                res.append(c)
                esc = False
                continue
            if c == '\\':
                res.append(c)
                esc = True
                continue
            if c == '"':
                in_s = not in_s
                res.append(c)
                continue
            if in_s and c == '\n':
                res.append('\\n')
                continue
            if in_s and c == '\r':
                continue
            if in_s and c == '\t':
                res.append('\\t')
                continue
            res.append(c)
        fixed = ''.join(res)
        try:
            obj = json.loads(fixed)
            if isinstance(obj, dict) and "prompt" in obj:
                if "dialogue" in obj: obj["dialogue"] = _normalize_dialogue(obj["dialogue"])
                return obj
        except Exception:
            pass
        # pass 3: partial recovery — JSON ถูก truncate ลองปิดให้แล้ว parse
        try:
            s = t.index('{')
            partial = t[s:]
            # ปิด string ที่ค้างอยู่ถ้ามี
            in_s2 = False; esc2 = False; res2 = []
            for c in partial:
                if esc2: res2.append(c); esc2 = False; continue
                if c == '\\': res2.append(c); esc2 = True; continue
                if c == '"': in_s2 = not in_s2; res2.append(c); continue
                if in_s2 and c == '\n': res2.append('\\n'); continue
                if in_s2 and c == '\r': continue
                res2.append(c)
            partial_fixed = ''.join(res2)
            if in_s2: partial_fixed += '"'   # ปิด string ที่ค้าง
            # ปิด array และ object ที่ค้าง
            depth_obj = partial_fixed.count('{') - partial_fixed.count('}')
            depth_arr = partial_fixed.count('[') - partial_fixed.count(']')
            if depth_arr > 0: partial_fixed += ']' * depth_arr
            if depth_obj > 0: partial_fixed += '}' * depth_obj
            obj = json.loads(partial_fixed)
            if isinstance(obj, dict) and "prompt" in obj:
                if "dialogue" in obj: obj["dialogue"] = _normalize_dialogue(obj["dialogue"])
                return obj
        except Exception:
            pass
        return None

    MAX_RETRY = 3
    # เก็บ completed shots (พร้อม prompt ที่อาจแก้แล้ว) เพื่อส่งเป็น continuity context
    completed_prompts: list[dict] = []

    # ── Grok batch prefetch ──────────────────────────────────────────────────
    # local ไม่แตะเลย — Checker เดิมใช้ completed_prompts (ผลที่ตัวเองซ่อมแล้ว) เป็น continuity
    # ของช็อตถัดไป แบบค่อยๆ ลู่เข้าหากันทีละช็อต — batch เห็นทั้งชุดพร้อมกันแทน (เป็น trade-off ที่
    # ตั้งใจ: ได้ full-sequence visibility แลกกับ progressive self-correction)
    _grok_check_prefetch: dict[int, dict] = {}
    if llm_wf_file == GROK_MARKER:
        _batch_targets: list[tuple[int, str, dict]] = []
        for _bi, _bs in enumerate(shots_data):
            _b_shot_num = _bi + 1
            if _target_idxs is not None and _bi not in _target_idxs:
                continue
            if not _bs.get("prompt", "").strip():
                continue
            # scale เดียวสำหรับทุกช็อตเหมือนฝั่ง Fill — ไม่งั้น batch ถูกหั่นตาม scale
            # ไม่มี shot_scale field ใน payload อีกต่อไป — payload มีแค่ native fields ด้านล่าง
            _b_sys = _editor_sys_for("medium")
            if _ck_all_blocks:
                _b_sys += f"\n\nSTORY SOURCE (exact A1 blocks, verbatim — GROUND TRUTH):\n{_ck_all_blocks}\n"
            # PROPS-DEDUP-CHECKER: canonical Props already sent once via props_str inside
            # _editor_sys_for("medium") above (Grok batch always uses the non-slim template) — do
            # NOT inject _ck_canonical_objects again here, that used to duplicate the same sheet.
            _b_lock = _gender_lock_for_shot(
                character_desc, _bs.get("shot_desc", ""), _bs.get("clothing", ""))
            _b_payload = {
                "shot": _b_shot_num,
                "shot_desc": _bs.get("shot_desc", ""),
                "clothing": _bs.get("clothing", ""),
                "objects": _bs.get("objects", ""), "camera": _bs.get("camera", ""),
                "narration": _bs.get("narration", ""), "speech_char": _bs.get("speech_char", ""),
                "subtitle": _bs.get("subtitle", ""), "identity_lock": _b_lock,
                "current_prompt": _bs.get("prompt", ""),
            }
            _b_payload = {k: v for k, v in _b_payload.items() if v not in ("", None)}
            _batch_targets.append((_b_shot_num, _b_sys, _b_payload))
        if on_log:
            _ck_all_parents = _a1_all_parent_ids(a1)
            await on_log(
                f"CHECKER backend=grok mode=batch shots={len(_batch_targets)} "
                f"parents={','.join(_ck_all_parents) if _ck_all_parents else 'NONE'}")
        _grok_check_prefetch = await _prefetch_grok_batch(
            "Checker", _batch_targets, _grok_batch_budget(_llm_tokens_check_grok_batch, len(_batch_targets)),
            grok_api_key, grok_model, on_log=on_log, stop_event=stop_event, one_call=True,
            strict_no_recovery=True)

    for i, shot in enumerate(shots_data):
        if stop_event and stop_event.is_set():
            if on_log: await on_log("⏹ หยุด Check"); return

        shot_num = i + 1

        # ถ้ากำหนด _target_idxs → shot อื่นๆ ใช้เป็น context แต่ไม่ตรวจ ไม่ call on_shot
        if _target_idxs is not None and i not in _target_idxs:
            completed_prompts.append(shot)
            continue

        if on_progress: on_progress(shot_num, num)

        # ข้าม shot ที่ไม่มี prompt
        if not shot.get("prompt", "").strip():
            if on_log: await on_log(f"⏭ Shot {shot_num} ไม่มี prompt — ข้าม")
            completed_prompts.append(shot)
            await on_shot(i, shot); continue

        # strip narration_box overlay suffix ก่อนส่งให้ editor (เพื่อไม่ให้ confused)
        _raw_prompt = shot.get("prompt", "")
        _overlay_suffix = ""
        _OVERLAY_MARKER = ", comic book style manga text overlay:"
        if dialogue_style == "narration_box" and _OVERLAY_MARKER in _raw_prompt:
            _idx = _raw_prompt.index(_OVERLAY_MARKER)
            _overlay_suffix = _raw_prompt[_idx:]
            _raw_prompt     = _raw_prompt[:_idx]

        # CHECKER-C1: deterministic containment — same for both backends (canonical contradiction
        # is backend-independent). A hit here skips the semantic LLM pass entirely for this shot:
        # Current Prompt is preserved and the conflict is reported, never guessed through.
        _canon_conflicts = _precheck_canonical_conflicts(
            shot.get("shot_desc", ""), shot.get("clothing", ""),
            "", character_desc,
        )
        if _canon_conflicts:
            if on_log:
                await on_log(
                    f"🛑 Shot {shot_num}: canonical field conflict — Checker skipped, prompt preserved\n"
                    + "\n".join(f"  • [{c['code']}] {c['detail']}" for c in _canon_conflicts)
                )
            updated = dict(shot)
            updated["_check_status"] = "conflict"
            updated["_check_issues"] = [f"[CANONICAL FIELD CONFLICT] {c['detail']}" for c in _canon_conflicts]
            updated["_check_reason"] = "Canonical fields conflict — upstream shot-data defect, not guessed"
            completed_prompts.append(updated)
            await on_shot(i, updated)
            continue

        # ── สร้าง continuity context จาก shots ที่ตรวจไปแล้วทั้งหมด ──
        continuity_ctx = ""
        # ถ้ายังไม่มี previous shots → ใช้ default outfit จาก character_desc เป็น anchor
        if not completed_prompts and character_desc:
            continuity_ctx = (
                "=== CONTINUITY REFERENCE ===\n"
                f"PRE-STORY DEFAULT STATE: All characters are fully clothed in their default outfits as described below.\n"
                + ("Character list (default outfit is the STARTING state — clothing only progresses forward from here):\n"
                   if _nsfw else
                   "Character list (default outfit is the STARTING state — outfit may change either direction from here as the story requires):\n")
                + f"{character_desc}\n\n"
            )
        elif completed_prompts:
            continuity_ctx = "=== CONTINUITY REFERENCE (previous shots) ===\n"
            total_prev = len(completed_prompts)
            for j, prev_s in enumerate(completed_prompts):
                p_prompt  = prev_s.get("prompt", "").strip()
                scene     = prev_s.get("shot_desc", "")
                clothing  = prev_s.get("clothing", "").strip()
                is_recent = (j >= total_prev - 2)
                clothing_line = (
                    f"  outfit in prompt: {_clothing_for_image_prompt(clothing)}\n"
                    if clothing else ""
                )
                if is_recent:
                    continuity_ctx += f"--- Shot {j+1}: {scene}\n{clothing_line}{p_prompt}\n\n"
                else:
                    continuity_ctx += f"--- Shot {j+1}: {scene}\n{clothing_line}\n"
            continuity_ctx = continuity_ctx.rstrip() + "\n\n"

        # ── upcoming shots context (shot ถัดไปอีก 2 shot) ──
        upcoming_ctx = ""
        upcoming = shots_data[i + 1: i + 3]   # parameter shots_data คือ copy ที่ได้รับมา
        if upcoming:
            upcoming_ctx = "=== UPCOMING SHOTS (for narrative flow — do NOT reveal these yet) ===\n"
            for k, nxt in enumerate(upcoming):
                upcoming_ctx += (
                    f"Shot {shot_num + k + 1}: {nxt.get('shot_desc', '')}\n"
                )
            upcoming_ctx += "\n"

        _narr_ctx = shot.get('narration','')
        _sc_ctx   = shot.get('speech_char','')
        _st_ctx   = shot.get('subtitle','')
        _clothing_ctx = shot.get('clothing', '').strip()
        _prop_ctx = shot.get('objects', '').strip()
        _id_lock = _gender_lock_for_shot(
            character_desc,
            shot.get("shot_desc", ""),
            shot.get("clothing", ""),
        )

        # PROFESSIONAL-AUTONOMY-CLEANUP-A2: the deterministic SETUP/ESCALATION/CLIMAX/AFTERMATH arc
        # calculation (based on shot index as a % of the sequence) and its injection into the Checker
        # prompt (_arc, _arc_hint, "[Arc: ...]", "arc_position: ...") have been REMOVED. The Checker is
        # an independent judge with exact story grounding (canonical evidence + neighbouring shots) —
        # it infers narrative importance from that evidence, not from Python guessing where the climax
        # "should" be by percentage of shot count. A quiet story with no climax, or one whose major
        # event happens early, no longer gets a Python-imposed arc label.

        _overlay_note = ("NOTE: this shot uses narration_box style — the overlay suffix has been stripped from the prompt below for clarity. Focus on the prompt body only; do NOT put overlay text into the narration field.\n" if _overlay_suffix else "")

        # AGENT-STORY-TRUTH-ROUTING-A1: Local per-shot call gets ONLY this shot's resolved parent
        # E[n] block(s) (from its "uses" field), never a guess — if unresolvable, log parents=NONE
        # and reason from this shot/context only (no full story text injected either).
        _ck_parent_ids, _ck_parent_blocks = ([], "") if llm_wf_file == GROK_MARKER else _resolve_shot_parent_blocks(shot, a1)
        _ck_parent_ctx = (
            f"=== STORY SOURCE (exact, this shot's grounding) ===\n{_ck_parent_blocks}\n\n"
            if _ck_parent_blocks else ""
        )
        # PROPS-DEDUP-CHECKER: canonical Props already sent once via props_str, which is now baked
        # into BOTH the non-slim system prompt (_editor_sys_for) and editor_sys_slim — do NOT
        # inject _ck_canonical_objects again here, that used to duplicate the same sheet in the
        # user prompt on top of the system prompt.
        if on_log and llm_wf_file != GROK_MARKER:
            await on_log(
                f"CHECKER backend=local mode=single shot={shot_num} "
                f"parents={','.join(_ck_parent_ids) if _ck_parent_ids else 'NONE'}")

        user_p = (
                f"{_ck_parent_ctx}"
                f"{continuity_ctx}"
                f"{upcoming_ctx}"
                f"=== SHOT {shot_num} TO REVIEW NOW ===\n"
                + (f"location: {shot.get('location','').strip()}\n" if shot.get('location','').strip() else "")
                + f"shot_desc: {shot.get('shot_desc','')}\n"
                + _required_roster_block(shot.get('shot_desc', ''), character_desc, '')
                + f"{_overlay_note}"
                f"{_clothing_user_block(_clothing_ctx) if _clothing_ctx else ''}"
                f"{_prop_user_block(_prop_ctx) if _prop_ctx else ''}"
                + (f"{_id_lock}\n" if _id_lock else "")
                + f"narration: {_narr_ctx}\n"
                f"speech_char: {_sc_ctx}\n"
                f"subtitle: {_st_ctx}\n\n"
                f"Current prompt:\n{_raw_prompt}\n\n"
                f"Judge this shot now. The Current prompt above is what you review; everything "
                f"above it is the canonical evidence you review it against. The story language for "
                f"narration and speech is {lang_prompt_name(dialogue_lang)}. dialogue_style is "
                f"'{dialogue_style}': an EMPTY narration/speech field stays empty — writing a line "
                f"into one is invented dialogue, and \"subtitle is missing\" is never an issue; a "
                f"FILLED field stays filled and is judged normally, never blanked because the current "
                f"style would not have generated it. If you report a narration or speech issue you "
                f"must fix that field in the output JSON.\n"
                f"Repair only what genuinely mismatches the canonical evidence, with the smallest "
                f"change that fixes it, and output the JSON."
            )

        result_data = None
        # Grok: เหมือน Fill — คำขอเดียวของ stage คือความพยายามทั้งหมด ช็อตที่ไม่อยู่ใน prefetch
        # ล้มเหลวและคง prompt เดิม ไม่ยิงซ้ำ (slim ladder ที่ attempt>MAX_RETRY เป็นของ local ล้วน)
        _max_check_attempts = 1 if llm_wf_file == GROK_MARKER else MAX_RETRY + 1
        for attempt in range(1, _max_check_attempts + 1):  # +1 สำหรับ fallback attempt (local เท่านั้น)
            if stop_event and stop_event.is_set():
                if on_log: await on_log("⏹ หยุด Check"); return
            if on_log:
                await on_log(f"🔍 Check shot {shot_num}/{num}" + (f" retry {attempt}" if attempt > 1 else ""))

            # attempt ที่ 4+ → ตัด context เหลือแค่ 5 shot ก่อนหน้า ไม่มี upcoming
            if attempt > MAX_RETRY:
                slim_ctx = ""
                if completed_prompts:
                    last5 = completed_prompts[-5:]
                    offset = len(completed_prompts) - len(last5)
                    slim_ctx = "=== CONTINUITY (last 5 shots) ===\n"
                    total_last5 = len(last5)
                    for j, ps in enumerate(last5):
                        is_recent = (j >= total_last5 - 2)
                        if is_recent:
                            slim_ctx += f"--- Shot {offset+j+1}: {ps.get('shot_desc','')}\n{ps.get('prompt','').strip()}\n\n"
                        else:
                            slim_ctx += f"--- Shot {offset+j+1}: {ps.get('shot_desc','')}\n\n"
                cur_user_p = (
                        f"{_ck_parent_ctx}"
                        f"{slim_ctx}"
                    f"{upcoming_ctx}"
                    f"=== SHOT {shot_num} TO REVIEW NOW ===\n"
                    + (f"location: {shot.get('location','').strip()}\n" if shot.get('location','').strip() else "")
                    + f"shot_desc: {shot.get('shot_desc','')}\n"
                    + (f"{_clothing_user_block(_clothing_ctx)}" if _clothing_ctx else "")
                    + (f"{_prop_user_block(_prop_ctx)}" if _prop_ctx else "")
                    + f"narration: {shot.get('narration','')}\n"
                    f"speech_char: {shot.get('speech_char','')}\n"
                    f"subtitle: {shot.get('subtitle','')}\n\n"
                    f"Current prompt:\n{_raw_prompt}\n\n"
                    f"Judge this shot now against the canonical evidence above, repairing only a "
                    f"genuine mismatch with the smallest change that fixes it.\n"
                    f"IMPORTANT — output must be SHORT:\n"
                    f"- 'issues': max 2 items, each under 15 words\n"
                    f"- 'reason': under 15 words\n"
                    f"- 'prompt': write 'KEEP' either way (the full rewrite happens in a separate "
                    f"step, do NOT write the prompt here)\n"
                    f"Output ONLY the JSON, nothing else."
                )
                if on_log:
                    await on_log(f"⚡ attempt {attempt} slim SYS:\n{editor_sys_slim}")
                    await on_log(f"⚡ attempt {attempt} slim USER:\n{cur_user_p}")
            else:
                cur_user_p = user_p

            if attempt == 1 and shot_num in _grok_check_prefetch:
                _b_item = _grok_check_prefetch.pop(shot_num)
                raw = json.dumps(_b_item, ensure_ascii=False)
                if on_log: await on_log(f"⚡ shot {shot_num}: ใช้ผลจาก Grok batch")
            elif llm_wf_file == GROK_MARKER:
                raw = None
                if on_log:
                    await on_log(f"⚠ shot {shot_num}: ไม่มีผลจาก Grok batch — ใช้ prompt เดิม (one-call mode)")
            else:
                raw = await _call_llm_generic(
                    llm_wf_file, wf_template,
                    (editor_sys_slim if _variant == SYS_VARIANT_SLIM
                     else _editor_sys_for(shot.get("camera") or "")),
                    cur_user_p, loop, max_tokens=_llm_tokens_fill,
                    on_log=None,
                    grok_api_key=grok_api_key, grok_model=grok_model,
                )
            if not raw:
                if on_log: await on_log(f"⚠ attempt {attempt} ไม่ได้ output")
                continue

            if on_log and attempt == 1:
                await on_log(f"RAW check shot {shot_num}:\n{raw}")

            result_data = _parse_check(raw)
            if result_data:
                break
            if on_log: await on_log(f"⚠ attempt {attempt} parse ไม่ได้ — raw snippet:\n{raw[:300]}")

        if not result_data:
            if on_log: await on_log(f"⚠ shot {shot_num} Check ล้มเหลว — ใช้ prompt เดิม")
            shot["_check_status"] = "error"
            shot["_check_issues"] = []
            shot["_check_reason"] = "ตรวจสอบไม่สำเร็จ"
            completed_prompts.append(shot)
            await on_shot(i, shot)
            continue

        # ── merge ผลกลับเข้า shot_data ──
        updated          = dict(shot)
        # ตัวตรวจ/ผู้กำกับส่ง prompt กลับมาเป็น array ของประโยคได้เหมือนฝั่ง outline/fill — แปลงก่อน .strip()
        new_prompt       = _coerce_text_field(result_data.get("prompt", ""), sep=" ").strip()
        old_prompt       = _coerce_text_field(shot.get("prompt", ""), sep=" ").strip()
        keep             = result_data.get("keep_original", True)
        issues           = result_data.get("issues", [])
        if isinstance(issues, str):
            issues = [issues] if issues.strip() else []
        elif not isinstance(issues, list):
            issues = []
        reason           = _coerce_text_field(result_data.get("reason", ""), sep="; ")

        # KEEP-INVARIANT-A1 (task: surgical object-lock repair + keep-invariant): keep_original=true
        # is a deterministic TRANSPORT contract, not a suggestion for Python to weigh against the
        # returned `prompt` text. AUDIT FINDING: before this patch, only the "KEEP"-literal / short
        # attempt-4-fallback cases below redirected to old_prompt — a keep_original=true result that
        # carried a full, syntactically valid rewritten `prompt` (e.g. one that silently dropped an
        # IDENTITY LOCK trait) flowed straight through untouched, because neither branch below fired
        # for it (new_prompt wasn't literally "KEEP" and wasn't under 100 chars). That is now fixed
        # here, unconditionally, before any other check: whatever the model put in `prompt` when it
        # declared keep_original=true is discarded outright — never compared, never merged, never
        # judged — and the original prompt is used instead. This must run BEFORE the scoped
        # object-lock branch and the structural guard below so neither of them can ever see a
        # keep_original=true candidate that isn't already just the original prompt.
        if keep:
            new_prompt = old_prompt
        # attempt 4 ส่ง prompt สั้นๆ ("KEEP" หรือ description) ไม่ใช่ prompt จริง
        # ถ้า prompt สั้นกว่า 100 ตัวหรือเป็น "KEEP" → ใช้ prompt เดิม
        elif new_prompt.upper() == "KEEP" or (not keep and len(new_prompt) < 100):
            new_prompt = old_prompt
            if on_log:
                await on_log(f"⚠ Shot {shot_num}: attempt 4 ได้ issues แต่ไม่มี prompt ใหม่ — ใช้ prompt เดิม + แสดง badge")

        # PATCH3-CHECKER-OBJECT-LOCK-SURGICAL-REPAIR-A1 (task: surgical object-lock repair): scoped
        # repair transport. When the model declares repair_scope="object_lock" it is asserting the
        # ENTIRE proven defect is solved inside OBJECT LOCK alone and supplies ONLY the corrected
        # block — Python performs a pure structural splice of that block into the ORIGINAL prompt
        # (never the model's own "prompt" field, which is ignored entirely in this branch) so
        # everything outside OBJECT LOCK is byte-identical BY CONSTRUCTION, not merely by a guard
        # catching drift after the fact. repair_scope is read verbatim from the model's own declared
        # field — never inferred from `issues` text or any other content. Only meaningful for vNext
        # lock-contract engines outside manga mode, and never when keep_original=true (already
        # forced to old_prompt above). On any splice failure (missing/malformed replacement,
        # duplicate headers, or no existing OBJECT LOCK block to splice into) the surgical repair is
        # rejected and the original prompt is preserved — this branch never falls back to accepting
        # the model's whole-prompt `prompt` field for the same defect, since that would recreate the
        # collateral-damage failure this patch exists to remove.
        _ck_repair_scope = str(result_data.get("repair_scope", "") or "").strip().lower()
        _ck_scoped_status = "none"
        if _ck_v3 and not manga_page and not keep and _ck_repair_scope == "object_lock":
            _obj_repl_raw = _coerce_text_field(result_data.get("object_lock_replacement", ""), sep="\n").strip()
            _obj_repl = _unescape_literal_nl(_obj_repl_raw)
            _spliced, _splice_err = _v3_splice_object_lock_replacement(old_prompt, _obj_repl)
            if on_log:
                await on_log(
                    f"SCOPED OBJECT-LOCK REPAIR shot={shot_num}: repair_scope=object_lock\n"
                    f"CURRENT OBJECT LOCK BLOCK:\n{_v3_lock_block_text(old_prompt, _V3_OBJECT_LOCK_LINE_RE.search(old_prompt)) if _V3_OBJECT_LOCK_LINE_RE.search(old_prompt) else '(none)'}\n"
                    f"OBJECT LOCK REPLACEMENT:\n{_obj_repl}")
            if _spliced is None:
                _ck_scoped_status = "rejected"
                new_prompt = old_prompt
                keep = True
                issues = list(issues) + ["Scoped object-lock repair rejected — kept original"]
                reason = reason or _splice_err
                if on_log:
                    await on_log(f"🚫 SCOPED_REPAIR_REJECTED shot={shot_num}: {_splice_err} — keeping original")
            else:
                new_prompt = _spliced
                _ck_scoped_status = "spliced"

        # CHECKER-FORMAT-AUTHORITY-A1: pre-existing gap found while auditing the vNext OBJECT LOCK
        # task — Checker had NO structural reject guard at all (unlike Director's
        # DIRECTOR-FORMAT-AUTHORITY-PRODUCTION-A1 gate below in run_director_vnext), so a Checker
        # candidate could freely change format family, drop/duplicate a lock, or reorder sections
        # and it would go straight into updated["prompt"] with zero structural pushback. Reuses the
        # same shared structural guard Director uses (_director_format_structure_ok) rather than a
        # second parallel implementation. Runs unconditionally (not gated on new_prompt != old_prompt)
        # for the same reason Director's own guard was fixed to run unconditionally: a keep_original
        # result must not silently pass through a prompt that was ALREADY structurally broken
        # entering Checker.
        _ck_candidate = new_prompt if new_prompt else old_prompt
        if _ck_v3 and not manga_page:
            _ck_in_fmt = (shot.get("prompt_style") or "").strip() or _detect_prompt_format(old_prompt)
            _ck_fmt_ok, _ck_fmt_reason = _director_format_structure_ok(
                old_prompt, _ck_candidate, _ck_in_fmt, minimal_v3=True,
                allow_object_lock_repair=True)
            if on_log:
                _ck_sig_in = _vnext_prompt_structure_signature(old_prompt)
                _ck_sig_out = _vnext_prompt_structure_signature(_ck_candidate)
                await on_log(
                    f"CHECKER INPUT: shot={shot_num} _fill_engine={shot.get('_fill_engine','')} "
                    f"prompt_style={_ck_in_fmt} family={_ck_sig_in['format_family']} "
                    f"ID={_ck_sig_in['identity_lock_count']} LOC={_ck_sig_in['location_lock_count']} "
                    f"OBJ={_ck_sig_in['object_lock_count']} order={_ck_sig_in['lock_order']}")
                await on_log(
                    f"CHECKER OUTPUT: shot={shot_num} family={_ck_sig_out['format_family']} "
                    f"ID={_ck_sig_out['identity_lock_count']} LOC={_ck_sig_out['location_lock_count']} "
                    f"OBJ={_ck_sig_out['object_lock_count']} order={_ck_sig_out['lock_order']} "
                    f"structure_accept={'YES' if _ck_fmt_ok else 'NO'} "
                    f"reason={_ck_fmt_reason if not _ck_fmt_ok else ''}")
            if not _ck_fmt_ok:
                if on_log:
                    await on_log(f"🚫 CHECKER FORMAT REJECT: shot={shot_num} expected={_ck_in_fmt} "
                                 f"reason={_ck_fmt_reason}" +
                                 (" (already broken before Checker)" if _ck_candidate == old_prompt else ""))
                if _ck_scoped_status == "spliced":
                    # the splice itself was structurally clean (validated in
                    # _v3_splice_object_lock_replacement), but the guard still rejected the result —
                    # e.g. the replacement's own content tripped a spacing/format check. Same
                    # preserve-original outcome as any other structural reject; just label it as a
                    # scoped rejection rather than a generic one for the debug trace.
                    _ck_scoped_status = "rejected"
                new_prompt = old_prompt
                issues = list(issues) + ["Checker format reject — kept original"]
                reason = reason or "format reject"
                keep = True
            elif _ck_scoped_status == "spliced":
                _ck_scoped_status = "accepted"
            if on_log and _ck_scoped_status in ("spliced", "accepted", "rejected"):
                await on_log(
                    f"FINAL STATUS shot={shot_num}: "
                    f"{'SCOPED_REPAIR_ACCEPTED' if _ck_scoped_status == 'accepted' else 'SCOPED_REPAIR_REJECTED'}")

        # Checker LLM output is the final semantic prompt. Allowed post-check work is
        # syntax-only: KEEP/short fallback, overlay suffix restore, newline unescape, plus the
        # structural format-reject fallback above.
        _checker_llm_prompt = new_prompt if new_prompt else old_prompt
        if _overlay_suffix and _OVERLAY_MARKER not in (new_prompt if new_prompt else old_prompt):
            _base = (new_prompt if new_prompt else old_prompt).rstrip()
            new_prompt = _base + _overlay_suffix
            old_prompt = old_prompt  # ไม่เปลี่ยน old_prompt เพื่อ compare ถูกต้อง

        updated["prompt"] = _unescape_literal_nl(new_prompt if new_prompt else old_prompt)
        if on_log:
            await on_log("Checker output accepted without semantic Python rewrite")
        _stamp_checker_prompt_trace(
            prompt_before=old_prompt,
            checker_llm_prompt=_checker_llm_prompt,
            prompt_after=updated["prompt"],
            shot=shot_num,
        )
        updated["_check_status"] = "ok" if keep else "edited"
        updated["_check_issues"] = [iss[:200] for iss in issues if isinstance(iss, str) and not iss.startswith("Camera:") and not iss.startswith("Subject:")]
        updated["_check_reason"] = reason[:200] if reason else ""

        # issue ที่พูดถึงคนนอก roster = ตัวตรวจเข้าใจผิดว่าใครอยู่ในช็อต (เคสจริง: "Missing Pha-na"
        # ทั้งที่ scene_desc มีแค่สองคน) — ตัดทิ้งจากรายการ issue ไม่แก้ prompt เอง
        _shot_roster = _visible_roster(shot.get("shot_desc", "") or "", character_desc, "")
        if _shot_roster:
            _off = [n for n in _sheet_name_list(character_desc) if n not in _shot_roster]
            if _off:
                _bogus = [iss for iss in updated["_check_issues"]
                          if any(_name_in_text(n, iss) for n in _off)]
                if _bogus:
                    updated["_check_issues"] = [iss for iss in updated["_check_issues"]
                                                if iss not in _bogus]
                    if on_log:
                        await on_log(f"🚫 shot {shot_num}: ทิ้ง issue ที่อ้างถึงคนนอก roster "
                                     f"({', '.join(_off)}) — {'; '.join(_bogus)[:80]}")

        # dialogue_style ห้าม field ไหน ก็ตัด issue ที่พูดถึง narration/speech ทิ้งไปเลย
        # เพราะ field นั้นจะถูกบังคับว่างอยู่ดี — ไม่ควรมี issue ค้างให้ user งง ว่าทำไม editor ยัง "เช็ค narration"
        _forbidden_kw = []
        if dialogue_style == "none":
            _forbidden_kw = ["narration", "speech"]
        elif dialogue_style in ("speech_bubble", "subtitle_bar"):
            _forbidden_kw = ["narration"]
        elif dialogue_style == "narration_box_pil":
            _forbidden_kw = ["speech"]
        if _forbidden_kw:
            updated["_check_issues"] = [iss for iss in updated["_check_issues"]
                                         if not any(kw in iss.lower() for kw in _forbidden_kw)]
            if not updated["_check_issues"] and updated["_check_status"] == "edited" and new_prompt == old_prompt:
                updated["_check_status"] = "ok"
                updated["_check_reason"] = ""

        # อัปเดต narration/subtitle จาก editor — รับค่าจริง block แค่ placeholder ตรงๆ
        # NORMAL-VNEXT-CHECKER-OUTPUT-BLOAT-A1: speech_char REMOVED from this loop — it was never
        # anything but copy-only in the prompt contract (verified: Checker is never granted repair
        # authority over it, only "speech_char keeps the outline's own spelling... copy-only"), and
        # is no longer part of the output schema at all — reading it here would silently accept a
        # stray/legacy value from a non-conforming response instead of leaving the field exactly as
        # Fill last wrote it. narration/subtitle now use an OMIT-WHEN-UNCHANGED contract on the wire
        # (see output schema above) instead of always-echo — .get(_fld, "") already defaults to ""
        # for an omitted key, and the "field already has a value, model sent nothing → keep existing"
        # guard two lines below is exactly the fallback-to-existing-value behavior that contract needs.
        _E_PLACEHOLDER = {"KEEP","COPY","UNCHANGED","COPY UNCHANGED","<COPY>","<COPY UNCHANGED>",
                          "<UNCHANGED>","<KEEP>","<COPY VERBATIM>","N/A","NONE"}
        for _fld in ("narration", "subtitle"):
            _new_val = result_data.get(_fld, "")
            if not isinstance(_new_val, str): continue
            _enorm = _new_val.strip().upper().strip("<>").strip()
            if _enorm in _E_PLACEHOLDER: continue
            _stripped = _new_val.strip()
            # ถ้า field เดิมมีค่าอยู่และ editor ส่งมาว่าง → ไม่ลบทิ้ง (อาจเป็น LLM ลืม output)
            if not _stripped and shot.get(_fld, "").strip(): continue
            updated[_fld] = _stripped

        # TEXT-CHANNEL-A1: removed the unconditional dialogue_style-based blank here — it was
        # overriding the "don't delete if the LLM forgot to output it" guard immediately above on
        # every Checker pass, including single-shot re-checks of already-populated fields. Field
        # presence is render intent; dialogue_style governs generation/presentation, not deletion.

        if on_log:
            if keep:
                await on_log(f"✅ Shot {shot_num}: ตรงกันดีแล้ว — prompt ไม่เปลี่ยน")
            else:
                issue_lines = "\n".join(f"  • {iss}" for iss in updated["_check_issues"]) if updated["_check_issues"] else f"  • {reason}"
                changed = new_prompt != old_prompt
                await on_log(
                    f"✏ Shot {shot_num}: แก้ไข {'(prompt เปลี่ยนแล้ว ✓)' if changed else '(prompt เหมือนเดิม ⚠)'}\n"
                    f"{issue_lines}"
                )

        completed_prompts.append(updated)
        await on_shot(i, updated)

    if on_log:
        await on_log(_sys_metrics_line(
            "Checker", _variant, _variant_why,
            editor_sys_slim if _variant == SYS_VARIANT_SLIM else _editor_sys_for("medium")))
        await on_log(f"✅ Check เสร็จ {num} shots")

# ค่าเดิมที่เคยเซฟไว้ใน state ของ user ต้องยังใช้ได้ — map เข้าชื่อใหม่แทนการรีเซ็ตเป็น default เงียบๆ
_DIR_COMPOSITION_ALIAS = {"light": "balanced", "push": "cinematic"}

_DIR_ENV_ALIAS = {"on": "natural", "strong": "emphasized"}

_DIR_MOTION_ALIAS = {"scene": "natural", "sharpen": "dynamic", "freeze": "peak"}

# เจตนาของแต่ละ preset ระยะภาพ — ส่งไปเฉพาะตอน user สั่ง override ladder เท่านั้น
# ถ้าไม่ส่ง โมเดลจะเห็นแค่ชื่อระยะรายช็อตแล้วไม่รู้ว่าทั้งซีเควนซ์กำลังเล่าด้วยภาษาแบบไหน
_DIR_SCALE_INTENT = {
    "epic": ("SEQUENCE SCALE INTENT — EXPANSIVE: distances lean wide and extreme wide. The environment and "
             "the size of the space carry the frame; a character may read as a small full-body figure "
             "against the location. Describe terrain, sky, architecture, distance and weather at that "
             "scale — never facial micro-detail."),
    "balanced": ("SEQUENCE SCALE INTENT — BALANCED: distances move across the ladder. Whatever the shot "
                 "is assigned, the action and the geography of the space must both stay readable — the "
                 "viewer should always be able to tell where everyone is and what they are doing."),
    "intimate": ("SEQUENCE SCALE INTENT — INTIMATE: distances lean medium and medium close. Reactions, "
                 "gaze, gesture and the tension between people carry the frame; the environment becomes "
                 "context described in a short phrase."),
    "chamber": ("SEQUENCE SCALE INTENT — CHAMBER: distances lean medium and close, spatial depth is "
                "compressed. Faces, hands and close interaction carry the frame; describe the immediate "
                "surroundings only, not distant vistas."),
}

# แรงอะไรก็ตามที่เกิดขึ้น ต้องไหลต่อไปยังของที่ห้อยอยู่บนตัวจริงๆ ไม่ใช่ผมสะบัดไปทางหนึ่ง
# ชายเสื้อสะบัดไปอีกทางหนึ่ง — ภาพที่ออกมาจะดูผิดทันทีแม้บรรยายมาถูกทุกอย่าง
# นี่คือ invariant ทางฟิสิกส์ ("แรงเดียวต้องไปทางเดียวกัน") ไม่ใช่ recipe เทคนิคกล้อง จึงยังอยู่ต่อ
_DIR_SECONDARY_MOTION = (
    "SECONDARY MOTION: hair, garment fabric and anything already held or worn must move physically "
    "coherently with the same force and direction — never invent a scarf, ribbon or accessory that "
    "is not already on the character just to show motion."
)

# Director แก้ prompt ที่มีอยู่ตรงหน้า ไม่ได้ resolve โปรเจกต์ใหม่ — ถ้า user แก้ชื่อตัวละครหรือทรงผม
# ในกล่อง prompt เอง Director ต้องถ่ายภาพคนที่ prompt เขียนไว้ ไม่ใช่ดึงคนใน Character sheet กลับมา
# ความไม่ตรงกันระหว่าง prompt กับชีตเป็นเรื่องของ Checker ไม่ใช่ของ Director
_DIRECTOR_CONTENT_LOCK = (
    "CONTENT LOCK — THE CURRENT PROMPT IS THE CONTENT AUTHORITY.\n"
    "  Everything already written in the Current prompt is locked content, and it may contain "
    "deliberate hand edits by the user that differ from the project's sheets. Preserve exactly, as "
    "written in the Current prompt: character names and identities, appearance (hair, skin, build, "
    "age wording), garments and how they are worded, props and how they are worded, the action, and "
    "the environment.\n"
    "  You may compress a detail ONLY when visibility at the assigned shot scale requires it. "
    "Compression may delete a detail that cannot be seen at that distance. It may NEVER replace a "
    "detail with a different value, and never restore a name, a garment, a hairstyle or an object "
    "that the Current prompt does not have.\n"
    "  IDENTITY LOCK is READ-ONLY. Preserve the IDENTITY LOCK block verbatim — do not delete, "
    "rewrite, summarize, shorten, change sex, change appearance, change clothing, swap clothing "
    "between people, add or remove characters, restore a default outfit, or reinterpret identities "
    "for artistic reasons. You may READ it for camera and staging. If it looks wrong, do not repair "
    "it — identity validation belongs to Checker against Outline.\n"
    "  If a name or a detail looks wrong to you, it is not yours to fix — leave it. Reporting a "
    "mismatch against the project sheets is the Checker's job, not the Director's.\n"
    "  THE ONE EXCEPTION IS CAMERA GEOMETRY: the assigned SHOT SCALE given in the user message outranks "
    "the camera wording inside the prompt. If the prompt says \"close-up\" and the assigned scale is "
    "wide, the shot becomes wide — that correction is exactly your job. Spatial relationship (how far "
    "people stand) is NOT a separate field given to you — it is canonical content already locked into "
    "the Current prompt itself; preserve it exactly, the same as every other locked fact. "
    "Structural camera state wins on camera; the Current prompt wins on everything visible in it."
)

# NOTE (PROFESSIONAL-AUTONOMY-CLEANUP-A2): the single-image deterministic keyword consequence
# validator that used to live here (_DIR_CONSEQUENCE_SIGNALS, _DIR_NEUTRAL, director_pass_consequences(),
# director_consequence_fix_request()) has been REMOVED. It grep'd the Director's returned prompt for a
# fixed catalogue of camera/composition/lighting/environment/motion keywords and forced a repair call
# when a professionally valid but differently-worded solution didn't match them — Python deciding what
# "good cinematography" looks like, which is exactly the violation this cleanup pass exists to remove.
# _DIR_ENV_FORCE and _DIR_MOTION_VERBS below are KEPT — manga's own consequence checker
# (director_manga_pass_consequences, out of scope for this cleanup) still consumes them.
# คำที่บอกว่าฉากนี้มีแรงจากสิ่งแวดล้อมให้ตอบสนองจริง ถ้าไม่มีเลย environment pass ไม่มีอะไรให้ทำ
_DIR_ENV_FORCE = ("wind", "breeze", "gust", "rain", "drizzle", "storm", "snow", "mist", "fog",
                  "haze", "dust", "smoke", "heat", "cold", "steam", "spray", "current")

# ถ้า prompt ไม่ได้บรรยายการเคลื่อนไหว motion pass ก็ไม่มีอะไรให้แช่ — ท่านิ่งต้องอยู่นิ่งตามสัญญา
_DIR_MOTION_VERBS = ("walk", "run", "sprint", "stride", "step", "leap", "jump", "climb", "fall",
                     "rush", "chase", "swing", "throw", "reach", "turn", "lunge", "crawl", "ride",
                     "push", "pull", "carry", "dance", "flee", "charge", "wade", "march")

# ── MANGA PASS CONSEQUENCE VERIFICATION (DIRECTOR-B2) ────────────────────────
# single-image ตรวจ "มีร่องรอยไหม" กับ prompt ก้อนเดียว — ใช้กับ manga ตรงๆ ไม่ได้ เพราะ Chaotic ที่ทำ
# Dutch tilt แค่แผงเดียวถือว่า "ผ่าน" แล้ว (ทำทุกแผงคือ over-application ไม่ใช่ผ่านมากกว่า) และ Peak ที่
# ทำแค่แผงเดียวก็ถือว่าผ่านเหมือนกัน จึงต้องแยกพาเนลก่อนตรวจ ไม่ใช่ grep ทั้งก้อน
_MANGA_PANEL_SPLIT_RE2 = _re_clothing.compile(
    r'(?:^|\n|(?<=[.!?])\s+|,\s+)Panel\s*(\d+)\s*(\([^)]{0,40}\))?\s*(?:[:.\)-]|—)', _re_clothing.I)

def _manga_panel_texts(prompt: str) -> list:
    """แยกเนื้อความรายแผง — ลองตัวตัดเดิม (_panel_texts, ป้าย P1/Panel 1:) ก่อน แล้วค่อยลองตัวที่ทน
    วงเล็บระยะภาพ ("Panel 1 (wide):") ถ้าทั้งคู่แยกได้น้อยกว่า 2 แผง คืนลิสต์ว่าง — ผู้เรียกต้องข้ามการ
    ตรวจแทนที่จะเดาโครงสร้างแล้วเสี่ยงพังหน้า (ตามโจทย์ B2 ข้อ 6)

    วงเล็บระยะภาพ (ถ้ามี) ถูกเก็บเป็น group(2) แล้วแปะกลับหน้าเนื้อความของแผงนั้น — ไม่งั้นคำว่า wide/
    medium/close-up ที่อยู่ในวงเล็บหัวแผงจะหายไปจากขอบเขตที่ _manga_scale_category_counts อ่าน"""
    p = prompt or ""
    marks = list(_PANEL_SPLIT_RE.finditer(p))
    if len(marks) >= 2:
        out = []
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(p)
            out.append(p[m.end():end].strip())
        return out
    marks2 = list(_MANGA_PANEL_SPLIT_RE2.finditer(p))
    if len(marks2) >= 2:
        out = []
        for i, m in enumerate(marks2):
            end = marks2[i + 1].start() if i + 1 < len(marks2) else len(p)
            _paren = (m.group(2) or "").strip("() ")
            body = p[m.end():end].strip()
            out.append((f"{_paren} {body}".strip() if _paren else body))
        return out
    return []

# หลักฐานของแต่ละ control แยกคนละคลาสคำ กัน "off-centre" ตัวเดียวไปนับเป็นหลักฐานให้ทั้ง Composition
# และ Camera Energy พร้อมกัน (ปัญหาที่ audit DIRECTOR-A1 เจอ) — Camera ต้องเป็นคำระดับกล้อง/ขอบฟ้า/มุม
# เอียงเท่านั้น ไม่ใช่คำระดับตำแหน่งวางตัวแบบที่ Composition ใช้
_MANGA_CAMERA_EVIDENCE_RE = {
    "tense": _re_clothing.compile(
        r'\b(off[- ]?axis|canted|dutch(?:[- ]angle)?|tilt(?:ed|s)?|unstable horizon|oblique|'
        r'compressed (?:visual )?pressure|steep angle|skewed horizon)\b', _re_clothing.I),
    "chaotic": _re_clothing.compile(
        r'\b(dutch(?:[- ]angle)?|canted|tilt(?:ed|s)?|unstable horizon|oblique|skewed(?: horizon)?|'
        r'aggressive (?:off[- ]?axis|perspective)|steep angle|thrown off)\b', _re_clothing.I),
}

_MANGA_COMPOSITION_EVIDENCE_RE = {
    "cinematic": _re_clothing.compile(
        r'\b(foreground|midground|middle ground|background|leading line|depth layer|layers? of depth|'
        r'negative space|dominant panel|hierarchy|directional (?:flow|composition)|frames? the|'
        r'framed by|recedes|receding|stacked)\b', _re_clothing.I),
    "bold": _re_clothing.compile(
        r'\b(far off[- ]?centre|far off[- ]?center|off[- ]?centre|off[- ]?center|asymmetr\w*|'
        r'negative space|dwarf|towering|dominat\w*|extreme crop|aggressive crop|edge of (?:the )?frame|'
        r'symmetr\w*)\b', _re_clothing.I),
}

_MANGA_LIGHTING_EVIDENCE_RE = {
    "enhance": _re_clothing.compile(
        r'\b(light|lit|glow|shadow|sunlight|moonlight|lamplight|backlit|streetlight)\b', _re_clothing.I),
    "dramatic": _re_clothing.compile(
        r'\b(shadow|silhouett\w*|contrast|cast|separat\w*|rim of light|pool of light|edge of light|'
        r'deep shadow|hard (?:light|shadow)|brightly lit|in darkness)\b', _re_clothing.I),
    "soft": _re_clothing.compile(
        r'\b(soft|diffus\w*|gentle|even|muted|hazy|low contrast|broad(?:ened)?)\b', _re_clothing.I),
}

_MANGA_ENV_WIDE_EVIDENCE_RE = _re_clothing.compile(
    r'\b(ground|surface|street|across the|spatial|atmospher\w*|distance|landscape|space)\b', _re_clothing.I)

_MANGA_ENV_CLOSE_EVIDENCE_RE = _re_clothing.compile(
    r'\b(wet hair|damp|droplet|skin|fabric|cloth\w*|streaming|clinging)\b', _re_clothing.I)

_MANGA_ENV_ACTION_EVIDENCE_RE = _re_clothing.compile(
    r'\b(pulled by|spray|displaced|whipp\w*|driven|lashing|slanting)\b', _re_clothing.I)

_MANGA_MOTION_EVIDENCE_RE = _re_clothing.compile(
    r'\b(weight|lean\w*|hunched|stride|mid-|foot|heel|shoulder|momentum|forward|shifting|braced|'
    r'extend\w*|compress\w*|impact|contact|reach\w*|balance|airborne|off the ground|transferr\w*)\b',
    _re_clothing.I)

_MANGA_SCALE_WORDS = {
    "wide": ("extreme wide", "wide"), "medium": ("medium", "full body", "full-body"),
    "close": ("close-up", "close up", "close"),
}

# กลุ่มคำที่หมายถึงแหล่งแสงเดียวกัน (streetlamp/streetlight/lamp ล้วนคือไฟถนนดวงเดียวกัน) — เทียบเป็น
# กลุ่ม ไม่เทียบทีละคำเป๊ะๆ กัน false positive เวลาโมเดลแค่เปลี่ยนคำพ้องของแหล่งเดิม (โจทย์ B2 ข้อ 43
# "false positive เยอะเกิน" เป็นเงื่อนไข REVERT — จุดนี้เจอจริงตอนทดสอบสด: "lamp" ไปตรงกับ "streetlamp"
# ที่มีอยู่แล้วในเบสไลน์ ทำให้ lighting ถูกตัดสิน fail ทั้งที่ไม่ได้คิดแหล่งใหม่จริง)
_MANGA_LIGHT_SOURCE_GROUPS = (
    ("sun", "sunlight", "sunlit"),
    ("moon", "moonlight", "moonlit"),
    ("lamp", "lamplight", "streetlamp", "streetlight", "street lamp", "street light", "lamplit"),
    ("candle", "candlelight", "candlelit"),
    ("fire", "firelight", "campfire", "bonfire", "firelit"),
    ("window",),
    ("neon",),
    ("spotlight",),
    ("headlight", "headlamp"),
    ("flashlight", "torch", "torchlight"),
    ("lantern", "lantern light"),
    ("star", "starlight"),
)

def _manga_scale_category_counts(panels: list) -> dict:
    counts = {"wide": 0, "medium": 0, "close": 0}
    for p in panels:
        pl = (p or "").lower()
        for cat, words in _MANGA_SCALE_WORDS.items():
            if any(w in pl for w in words):
                counts[cat] += 1
                break   # หนึ่งแผงนับเข้าหมวดแรกที่เจอเท่านั้น กันแผงเดียวถูกนับซ้ำหลายหมวด
    return counts

def _manga_evidence_panels(panels: list, rx) -> list:
    """คืน index ของแผงที่มีหลักฐานตรง rx — ใช้ทั้งเช็คว่ามีอย่างน้อยหนึ่งแผง และเช็ค over-application"""
    return [i for i, p in enumerate(panels) if rx.search(p or "")]

def director_manga_pass_consequences(prompt: str, *, shot_scale_override: str, composition: str,
                                     camera_energy: str, lighting: str, environment: str,
                                     motion: str, baseline_prompt: str = "") -> dict:
    """ตรวจ manga prompt ที่ Director เขียนกลับมา ว่า control แต่ละตัวทิ้งร่องรอยที่วาดได้จริงหรือเปล่า —
    ระดับ PAGE (ทุกแผงเหมือนกันหมด = gimmick ซ้ำ ไม่ใช่ "ผ่านมากกว่า") ไม่ใช่ระดับ prompt ก้อนเดียวแบบ
    single-image ตรวจอย่างเดียว ไม่แก้ prompt เอง — ผู้เรียกตัดสินใจว่าจะซ่อมไหมจากผลตรงนี้เท่านั้น

    คืน dict: {control: {"status": "pass"|"weak"|"not_applicable"|"fail", "evidence": [...]}}
    บวก "_gimmick": รายชื่อ control ที่ over-apply (หลักฐานเดียวกันซ้ำทุกแผง) และ
    "_light_source_risk": รายชื่อคำแหล่งแสงที่โผล่ใน output แต่ไม่มีใน baseline_prompt เลย
    ("fail" ใช้เฉพาะ motion เมื่อ verb เปลี่ยนหมวด — ไม่ใช่แค่ขาดร่องรอย นั่นคือเปลี่ยนเหตุการณ์ ไม่ใช่แค่อ่อน)"""
    comp = _DIR_COMPOSITION_ALIAS.get((composition or "balanced").strip().lower(),
                                       (composition or "balanced").strip().lower())
    cam = (camera_energy or "natural").strip().lower()
    light = (lighting or "enhance").strip().lower()
    env = _DIR_ENV_ALIAS.get((environment or "natural").strip().lower(),
                              (environment or "natural").strip().lower())
    mot = _DIR_MOTION_ALIAS.get((motion or "natural").strip().lower(),
                                 (motion or "natural").strip().lower())
    scale_key = (shot_scale_override or "").strip().lower()

    result: dict = {}
    gimmick: list = []
    panels = _manga_panel_texts(prompt)
    if not panels:
        # แยกแผงไม่ได้ — ไม่เดาโครงสร้าง ทุก control ได้ not_applicable พร้อมเหตุผลเดียวกัน
        for k in ("shot_scale", "composition", "camera_energy", "lighting", "environment", "motion"):
            result[k] = {"status": "not_applicable", "evidence": ["panel parsing skipped — no reliable panel boundaries found"]}
        result["_gimmick"] = []
        result["_light_source_risk"] = []
        result["_panels_parsed"] = 0
        return result
    n = len(panels)

    # ── SHOT SCALE ── page tendency, ไม่ใช่บังคับทุกแผงให้เป็นระยะเดียว
    counts = _manga_scale_category_counts(panels)
    if scale_key == "epic":
        st = "pass" if counts["wide"] >= 1 and counts["wide"] >= counts["close"] else "weak"
        ev = [f"wide={counts['wide']} medium={counts['medium']} close={counts['close']}"]
    elif scale_key == "intimate":
        st = "pass" if (counts["medium"] + counts["close"]) >= 1 and (counts["medium"] + counts["close"]) >= counts["wide"] else "weak"
        ev = [f"wide={counts['wide']} medium={counts['medium']} close={counts['close']}"]
    elif scale_key == "chamber":
        st = "pass" if counts["wide"] == 0 or n <= 2 else "weak"
        ev = [f"wide={counts['wide']} medium={counts['medium']} close={counts['close']}"]
    elif scale_key == "balanced":
        distinct = sum(1 for v in counts.values() if v > 0)
        st = "pass" if (distinct >= 2 or n <= 2) else "weak"
        ev = [f"distinct scale categories present={distinct}"]
    else:
        # Follow Outline — ไม่มี baseline ให้เทียบตรงๆ ในหลายที่ที่ฟังก์ชันนี้ถูกเรียก จึงผ่านเสมอ
        # ตราบใดที่ไม่ใช่ทุกแผงยุบเหลือหมวดเดียว (นั่นคือสัญญาณ normalize ที่ต้องรายงาน)
        distinct = sum(1 for v in counts.values() if v > 0)
        st = "pass" if (distinct >= 2 or n <= 2) else "weak"
        ev = [f"distinct scale categories present={distinct} (Follow Outline)"]
    result["shot_scale"] = {"status": st, "evidence": ev}

    # ── COMPOSITION ──
    if comp == "off":
        result["composition"] = {"status": "not_applicable", "evidence": ["off — preserve, no consequence required"]}
    elif comp in ("cinematic", "bold"):
        rx = _MANGA_COMPOSITION_EVIDENCE_RE[comp]
        hits = _manga_evidence_panels(panels, rx)
        if not hits:
            result["composition"] = {"status": "weak", "evidence": []}
        else:
            result["composition"] = {"status": "pass", "evidence": [panels[i][:80] for i in hits[:2]]}
            if len(hits) == n and n > 2:
                gimmick.append("composition")
    else:  # balanced
        rx = _MANGA_COMPOSITION_EVIDENCE_RE.get("cinematic")
        hits = _manga_evidence_panels(panels, rx) or _manga_evidence_panels(
            panels, _re_clothing.compile(r'\b(third|left|right|centre|center)\b', _re_clothing.I))
        result["composition"] = {"status": "pass" if hits else "weak", "evidence": [panels[i][:80] for i in hits[:2]]}

    # ── CAMERA ENERGY ──
    if cam in ("stable", "natural"):
        result["camera_energy"] = {"status": "pass", "evidence": [f"{cam} — no strong mechanism required"]}
    else:
        rx = _MANGA_CAMERA_EVIDENCE_RE.get(cam, _MANGA_CAMERA_EVIDENCE_RE["tense"])
        hits = _manga_evidence_panels(panels, rx)
        if not hits:
            result["camera_energy"] = {"status": "weak", "evidence": []}
        else:
            result["camera_energy"] = {"status": "pass", "evidence": [panels[i][:80] for i in hits[:2]]}
            if len(hits) == n and n > 2:
                gimmick.append("camera_energy")

    # ── LIGHTING ──
    if light == "scene":
        result["lighting"] = {"status": "pass", "evidence": ["scene — preserve, no consequence required"]}
    else:
        rx = _MANGA_LIGHTING_EVIDENCE_RE.get(light, _MANGA_LIGHTING_EVIDENCE_RE["enhance"])
        hits = _manga_evidence_panels(panels, rx)
        if not hits:
            result["lighting"] = {"status": "weak", "evidence": []}
        else:
            result["lighting"] = {"status": "pass", "evidence": [panels[i][:80] for i in hits[:2]]}
            if light == "dramatic" and len(hits) == n and n > 2:
                gimmick.append("lighting")
    # story-truth safety net: light-source noun ที่โผล่ใน output แต่ไม่มีเลยใน baseline — ตามโจทย์ B2 ข้อ 11
    # "invented source ไม่ใช่ความสำเร็จ มันคือ Story Truth failure" จึงต้อง downgrade lighting เป็น fail
    # ทันที ไม่ใช่แค่ log เฉยๆ — ไม่งั้น repair round จะไม่มีทางรู้ว่าต้องแก้เรื่องนี้เลย
    src_risk = []
    if baseline_prompt:
        base_low = baseline_prompt.lower()
        out_low = (prompt or "").lower()
        for group in _MANGA_LIGHT_SOURCE_GROUPS:
            in_output = [w for w in group if w in out_low]
            if in_output and not any(w in base_low for w in group):
                src_risk.append(in_output[0])
    if src_risk:
        result["lighting"] = {
            "status": "fail",
            "evidence": [f"invented light source not in baseline: {', '.join(src_risk)}"],
        }

    # ── ENVIRONMENT RESPONSE ──
    force_present = any(f in (baseline_prompt or prompt or "").lower() for f in _DIR_ENV_FORCE)
    if env == "off":
        result["environment"] = {"status": "not_applicable", "evidence": ["off — no amplification required"]}
    elif not force_present:
        result["environment"] = {"status": "not_applicable", "evidence": ["no established environmental force in the page"]}
    elif env == "natural":
        hits = _manga_evidence_panels(panels, _MANGA_ENV_WIDE_EVIDENCE_RE) or \
               _manga_evidence_panels(panels, _MANGA_ENV_CLOSE_EVIDENCE_RE)
        result["environment"] = {"status": "pass" if hits else "weak", "evidence": [panels[i][:80] for i in hits[:2]]}
    else:  # emphasized
        wide_hits = _manga_evidence_panels(panels, _MANGA_ENV_WIDE_EVIDENCE_RE)
        close_hits = _manga_evidence_panels(panels, _MANGA_ENV_CLOSE_EVIDENCE_RE)
        action_hits = _manga_evidence_panels(panels, _MANGA_ENV_ACTION_EVIDENCE_RE)
        all_hits = sorted(set(wide_hits) | set(close_hits) | set(action_hits))
        if not all_hits:
            result["environment"] = {"status": "weak", "evidence": []}
        else:
            result["environment"] = {"status": "pass", "evidence": [panels[i][:80] for i in all_hits[:2]]}
            # gimmick เฉพาะเมื่อทุกแผงใช้ "คลาสหลักฐานเดียวกันเป๊ะ" (ไม่ใช่แค่ทุกแผงมี evidence บางอย่าง)
            if n > 2 and (len(wide_hits) == n or len(close_hits) == n or len(action_hits) == n):
                gimmick.append("environment")

    # ── MOTION ──
    motion_verbs_present = any(v in (baseline_prompt or prompt or "").lower() for v in _DIR_MOTION_VERBS)
    if mot == "natural":
        result["motion"] = {"status": "pass", "evidence": ["natural — preserve existing phase"]}
    elif not motion_verbs_present:
        result["motion"] = {"status": "not_applicable", "evidence": ["no motion verb in the page — nothing to sharpen"]}
    else:
        # verb-lock: หมวดคำกริยาที่ baseline มี ต้องยังอยู่ใน output — เปลี่ยนหมวด = fail ไม่ใช่แค่ weak
        base_low = (baseline_prompt or prompt or "").lower()
        out_low = (prompt or "").lower()
        base_verbs = {v for v in _DIR_MOTION_VERBS if v in base_low}
        out_verbs = {v for v in _DIR_MOTION_VERBS if v in out_low}
        verb_violation = bool(base_verbs) and not (base_verbs & out_verbs) and bool(out_verbs)
        hits = _manga_evidence_panels(panels, _MANGA_MOTION_EVIDENCE_RE)
        if verb_violation:
            result["motion"] = {"status": "fail",
                                 "evidence": [f"baseline verbs {sorted(base_verbs)} absent from output; "
                                              f"output verbs {sorted(out_verbs)} instead — event changed, not sharpened"]}
        elif not hits:
            result["motion"] = {"status": "weak", "evidence": []}
        else:
            result["motion"] = {"status": "pass", "evidence": [panels[i][:80] for i in hits[:2]]}
            if mot == "peak" and len(hits) == n and n > 2:
                gimmick.append("motion")

    result["_gimmick"] = gimmick
    result["_light_source_risk"] = src_risk
    result["_panels_parsed"] = n
    return result

# ค่าที่ user ตั้งใจให้เป็น no-op (off/stable/natural/scene) ไม่ต้องมี set แยกกรองซ้ำ — ดีเทกเตอร์ข้างบน
# คืน "pass"/"not_applicable" ให้ค่าพวกนี้ตรงๆ อยู่แล้ว ไม่มีทางออกมาเป็น "weak"/"fail" (โจทย์ B2 ข้อ 24)


def director_manga_consequence_fix_request(weak: dict, prompt: str) -> str:
    """ข้อความรอบซ่อมเดียวของ manga — เอ่ยเฉพาะ control ที่ weak/fail จริง ล็อกทุกอย่างที่ผ่านแล้วไว้
    ไม่ให้รื้อทั้งหน้าใหม่ (โจทย์ B2 ข้อ 17-18)"""
    _asks = {
        "shot_scale": "adjust the page's scale tendency per the SHOT SCALE instruction already given — "
                      "without forcing every panel to one distance",
        "composition": "give the page's key panel(s) real hierarchy/placement evidence per the "
                       "COMPOSITION instruction already given",
        "camera_energy": "give the appropriate panel(s) real camera-orientation/tilt mechanics per the "
                         "CAMERA ENERGY instruction already given — never just a placement word",
        "lighting": "give the appropriate panel(s) real light-treatment evidence per the LIGHTING "
                   "instruction already given, using ONLY the light source(s) already established",
        "environment": "show the existing environmental force acting on the panel-appropriate surface "
                       "per the ENVIRONMENT RESPONSE instruction already given",
        "motion": "sharpen the ONE panel with the page's strongest existing action phase, same action "
                 "verb, per the MOTION instruction already given — do not touch other panels' action",
    }
    _lines = "\n".join(f"  - {k}: {_asks.get(k, 'make its visual consequence explicit')}" for k in weak)
    return (
        "The Manga page below is missing the visible consequence of some selected Director passes. A "
        "pass that leaves no drawable mark, or that leaves the same mark on every panel, did not "
        "happen as intended.\n"
        f"{_lines}\n"
        "REPAIR ONLY THESE. Everything else on the page is already correct and LOCKED: panel count, "
        "panel order, every story beat, cast, clothing state, props, dialogue, expression and its "
        "intensity, and every control that is NOT listed above (its current result already passed — "
        "keep it exactly as it is). Do not add a new panel, merge panels, or rewrite the page from "
        "scratch. Do not apply the same fix to every panel — the page-level rule about distributing "
        "strength to the panel(s) that earn it still applies.\n\n"
        f"Current Manga page:\n{prompt}\n\nOutput the same JSON object as before, panel count and order "
        f"unchanged."
    )

# ── CHARACTER INSTANCE LOCK ──────────────────────────────────────────────────
# หนึ่งตัวละครที่ resolve แล้ว = ร่างเดียวในภาพ ข้อมูลที่ถูกกล่าวถึงซ้ำคือการเติมให้ร่างเดิม
# ไม่ใช่การสร้างร่างใหม่ ต้นเหตุที่เห็นจากภาพจริงคือโครงของ prompt: ชื่อเดิมถูกแนะนำตัวใหม่
# หลายรอบตลอด prompt ยาว ๆ แล้วโมเดลภาพตีความว่าเป็นคนละคน
# ด่านนี้ "ลบ" อย่างเดียว ไม่เรียบเรียงประโยคใหม่ — การย้ายวลีใน prose เสี่ยงกินข้อเท็จจริง
# ซึ่งเป็นสิ่งที่ทั้งไปป์นี้หวงที่สุด ส่วนที่ต้องเรียบเรียงเป็นหน้าที่ของกฎฝั่ง Fill/Director

# ภาพที่ "ต้องมี" คนเดิมมากกว่าหนึ่งร่างจริง ๆ — เจอคำพวกนี้แล้วปิดการตรวจทั้งหมด
_INSTANCE_MULTI_OK_RE = _re_clothing.compile(
    r'(?i)\b(twins?|triplets?|clones?|duplicates?|doppelg\w+|'
    r'mirror|mirrored|reflection|reflected|reflects?|'
    r'photograph|photo|portrait|painting|poster|billboard|statue|mannequin|'
    r'screen|monitor|television|projection|hologram|'
    r'ghost|apparition|spirit|memory of|younger self|older self)\b'
)

# การ "แนะนำตัว" = ชื่อตามด้วยวงเล็บระบุตัวตน (Mei (24, Thai woman, long black hair))
# ครั้งแรกคือการสร้างร่าง ครั้งที่สองขึ้นไปคือการบอกโมเดลว่ามีอีกคนหน้าตาแบบเดียวกัน
_INSTANCE_INTRO_TMPL = r'(?<![A-Za-z])%s\s*\(([^)]{3,120})\)'

# คำที่สร้างคนใหม่ขึ้นมาจากคนเดิม — "a woman matching Mei", "another man resembling Ton"
_INSTANCE_CLONE_TMPL = (
    r'(?i)\b(?:a|an|another|a second|one more)\s+(?:\w+[-\s]){0,3}'
    r'(?:woman|man|girl|boy|figure|person|silhouette)\s+'
    r'(?:matching|resembling|identical to|just like|who looks like|similar to)\s+%s\b'
)

# คนที่ไม่มีชื่อและไม่ผูกกับใครใน roster — Director/Sanity ห้ามเสกขึ้นมาเพื่อจัดองค์ประกอบ
# NOTE: "distant figure" เป็นภาษาทางการของ spacing FAR ในระบบนี้ จึงนับเป็น extra เฉพาะเมื่อ
# วรรคนั้นไม่มีชื่อใครใน roster อยู่เลย ไม่งั้นทุกช็อต FAR จะโดนฟ้องผิดทั้งหมด
_INSTANCE_ANON_RE = _re_clothing.compile(
    r'(?i)\b(?:a|an|another|one|two|three|several|some|a few|a group of|a crowd of)\s+'
    r'(?:\w+[-\s]){0,3}'
    r'(distant figures?|background figures?|bystanders?|passers[-\s]?by|passerby|onlookers?|'
    r'strangers?|crowd|villagers?|townsfolk|silhouettes? of (?:a )?(?:man|woman|person)|'
    r'shadowy figures?|other (?:people|men|women))\b'
)

def visible_instance_audit(prompt: str, scene_desc: str = "", char_desc: str = "",
                           spacing: str = "") -> list:
    """เทียบ 'จำนวนร่างที่ควรมี' กับ 'จำนวนร่างที่ prompt สื่อออกไป' — รายงานอย่างเดียว

    roster มาจาก scene_desc เหมือนทุกด่าน (POV ถูกกรองออกแล้วโดย _visible_roster)
    ปิดการตรวจเมื่อช็อตนั้นตั้งใจให้มีหลายร่างของคนเดียวจริง ๆ (ฝาแฝด กระจก รูปถ่าย ผี)"""
    out: list = []
    p = prompt or ""
    if not p.strip() or not (char_desc or "").strip():
        return out
    if _INSTANCE_MULTI_OK_RE.search(p) or _INSTANCE_MULTI_OK_RE.search(scene_desc or ""):
        return out
    roster = _visible_roster(scene_desc, char_desc, spacing)
    for name in roster:
        _intro = _re_clothing.findall(_INSTANCE_INTRO_TMPL % _re_clothing.escape(name), p)
        if len(_intro) > 1:
            out.append(f"{name} ถูกแนะนำตัวพร้อมรูปพรรณ {len(_intro)} ครั้งใน prompt เดียว — "
                       f"โมเดลภาพอ่านเป็นคนละร่างได้ ควรเหลือครั้งเดียว")
        if _re_clothing.search(_INSTANCE_CLONE_TMPL % _re_clothing.escape(name), p):
            out.append(f"{name} ถูกอ้างถึงเป็น 'คนที่หน้าตาเหมือน {name}' — นั่นคือการสร้างร่างที่สอง "
                       f"ของคนเดิม ต้องอ้างถึงด้วยชื่อตรง ๆ")
    for _m in _INSTANCE_ANON_RE.finditer(p):
        _start = max(0, p.rfind(".", 0, _m.start()), p.rfind(";", 0, _m.start()))
        _clause = p[_start:p.find(".", _m.end()) if p.find(".", _m.end()) > 0 else len(p)]
        if not any(_name_in_text(n, _clause) for n in roster):
            out.append(f"'{_m.group(1)}' อยู่ในภาพโดยไม่มีชื่อใครใน roster ผูกอยู่ — "
                       f"คนในภาพทุกคนต้องมาจาก roster ของช็อตนี้ ({', '.join(roster) or 'ว่าง'})")
    return out

# ── MANGA PAGE DIRECTOR ARCHITECTURE (DIRECTOR-B1) ───────────────────────────
# ก่อนหน้านี้ manga_page=True ทำให้ _pass_blocks="" ทั้งก้อน — dropdown ทั้งหกตัวไม่มีผลอะไรกับ
# manga เลย เพราะกฎเดิมของ single-image พูดถึง "หนึ่งเฟรมนิ่งเดียว" (one frozen moment) ตรงๆ ซึ่งใช้กับ
# หน้า manga ไม่ได้ (หน้าเดียวมีหลายพาเนล แต่ละพาเนลคือโมเมนต์ของตัวเอง) จึงต้องแปลเป็นคำสั่งคนละชุด
# ไม่ใช่แค่เปิดสวิตช์ก้อนเดิม — ระดับ PAGE (hierarchy/rhythm ข้ามพาเนล) แยกจากระดับ PANEL (การจัดฉาก
# ภายในพาเนลเดียว) และย้ำว่าห้ามใช้ลูกเล่นเดียวซ้ำทุกพาเนล (เช่น Dutch tilt ทุกพาเนล) ซึ่งทำให้หน้ากระดาษ
# แบนลงเป็นมุขเดียว แม้แต่ละพาเนลจะทำตามคำสั่งถูกต้องก็ตาม
_DIR_MANGA_PAGE_RULE = (
    "MANGA PAGE DIRECTION — THINK IN TWO LEVELS, PAGE AND PANEL.\n"
    "  PAGE LEVEL (yours to direct): build a visual hierarchy across the existing panels. Identify which "
    "panel already carries the page's strongest story beat — from what the page already contains, never "
    "a beat you invent — and let that panel read as the dominant one; the other panels support it through "
    "contrast, not repetition. Vary framing, camera treatment and composition across panels so the page "
    "has rhythm. THE SAME TREATMENT ON EVERY PANEL IS A FAILED PAGE even when each panel individually "
    "obeys its instruction — a page that is Dutch-tilted, off-centre, or shadow-heavy in every single "
    "panel reads as one repeated gimmick, not direction. Distribute strength deliberately: put the "
    "boldest treatment on the panel(s) that earn it, keep others calmer for contrast.\n"
    "  PANEL LEVEL (yours to direct): inside each panel, apply the selected controls as concrete, "
    "drawable staging — the same standard as single-image mode: never a bare adjective, always the "
    "visible consequence (which plane is lit, where the subject sits, which way the camera leans).\n"
    "  HARD LOCKS AT BOTH LEVELS: preserve the exact panel count and order the Current prompt already "
    "has — never merge panels, split one into two, add a panel, or drop one. Never collapse the page into "
    "a single unified image or a flowing paragraph without panel boundaries; the 'N-panel page...' opening "
    "and every panel boundary are structure, not style, and outrank every setting below. Each panel keeps "
    "its own existing story beat, cast, clothing state, props, and dialogue exactly as written, and each "
    "panel stays one clearly drawable action phase — you are re-photographing panels that already exist, "
    "never writing a new beat, merging two beats into one panel, or reordering what happens."
)

# scale_preset ของ manga คือ "แนวโน้มทั้งหน้า" ไม่ใช่การบังคับสเกลรายพาเนล (พาเนลไม่มีฟิลด์ shot_scale
# ของตัวเอง มันคือข้อความในพรอมป์เดียวกัน) — Follow Outline (ค่าว่าง) ต้องมีคำสั่งของตัวเอง ไม่ใช่แค่ไม่พูดถึง
_DIR_MANGA_SCALE_INTENT = {
    "": (
        "SHOT SCALE — FOLLOW OUTLINE (page tendency): keep every panel's existing framing/distance "
        "wording exactly as already written. Do not normalize panels toward one shared scale, do not "
        "widen or tighten any panel to make the page more uniform — whatever distance each panel already "
        "reads at is correct and stays that way."
    ),
    "balanced": (
        "SHOT SCALE — BALANCED (page tendency): maintain the scale variety the page already has, or add "
        "deliberate variety if panels currently read too similar. Avoid every panel landing at the same "
        "close or wide distance. Where a panel's story beat needs a specific framing to stay readable, "
        "keep that framing — scale variety serves the page rhythm, it never overrides what a panel needs "
        "to tell its beat clearly."
    ),
    "epic": (
        "SHOT SCALE — EPIC (page tendency): bias the page toward wider, spatially expansive panels — the "
        "location and the scale of the space should dominate more panels than not. A closer reaction or "
        "detail panel is still allowed, and should be kept, where the story needs that beat read up close. "
        "Do not make every panel wide; an all-wide page loses the contrast that makes the wide panels read "
        "as expansive."
    ),
    "intimate": (
        "SHOT SCALE — INTIMATE (page tendency): bias the page toward medium-close and close panels where "
        "reaction, gaze and gesture carry the beat. Retain one establishing or spatial panel where the "
        "page needs the reader to know where they are. Do not make every panel a face close-up; an "
        "all-close page loses the geography the reader needs to follow the scene."
    ),
    "chamber": (
        "SHOT SCALE — CHAMBER (page tendency): favor medium, full-body and medium-close panels that keep "
        "the contained spatial relationship between characters readable. Avoid an expansive establishing "
        "panel the story doesn't need — this preset is for scenes that stay in one confined space, where "
        "pulling back does not add information. Preserve the actual geography of that space across panels."
    ),
}

# Composition ต้องคุมสองระดับพร้อมกัน (page hierarchy + panel placement) — ข้อความจึงยาวกว่าตัวอื่นเล็กน้อย
_DIR_MANGA_COMPOSITION = {
    "off": (
        "COMPOSITION — OFF: preserve each panel's existing composition and the page's existing visual "
        "hierarchy. No intentional rearrangement at either level."
    ),
    "balanced": (
        "COMPOSITION — BALANCED: give the page a readable flow — a clear order the eye moves through the "
        "panels in — and a balanced hierarchy where panels don't all carry equal visual weight. Inside "
        "each panel, state where the subject sits and let the depth read in layers rather than flat. "
        "Avoid repetitive same-centre staging across consecutive panels."
    ),
    "cinematic": (
        "COMPOSITION — CINEMATIC: give the page one clear dominant visual beat (from what the page already "
        "contains) and let supporting panels build depth and contrast around it — foreground/subject/"
        "background layering, directional composition, leading lines from geometry already in that panel. "
        "Stronger hierarchy across the page without changing panel count. FORBIDDEN: inventing a new noun "
        "in any panel to compose with."
    ),
    "bold": (
        "COMPOSITION — BOLD: use the strongest graphic choices — extreme off-centre placement, large "
        "deliberate negative space, aggressive cropping — selectively, usually on one or two key panels "
        "the beat earns, not on every panel. Panels that are not carrying the page's strongest beat should "
        "stay comparatively restrained so the bold panel(s) actually read as bold by contrast. FORBIDDEN: "
        "the same aggressive off-centre framing repeated across most or all panels."
    ),
}

# Camera Energy ต้อง "กระจาย" ความแรงข้ามพาเนล ไม่ใช่แปะ mode เดียวกันซ้ำทุกพาเนล (เจอปัญหานี้ในการ audit)
_DIR_MANGA_CAMERA = {
    "stable": (
        "CAMERA ENERGY — STABLE: keep a mostly level, composed camera across the page — level horizons, "
        "restrained angles close to eye level. Right for calm, formal, or quiet pages. Minimal orientation "
        "gimmicks in any panel."
    ),
    "natural": (
        "CAMERA ENERGY — NATURAL: organic variation across panels — mostly level, with an angle change "
        "only where a panel's beat motivates it (a low angle for a power beat, a high angle for a small/"
        "vulnerable beat). No imposed tension and no imposed stillness."
    ),
    "tense": (
        "CAMERA ENERGY — TENSE: raise visual pressure on the panel(s) that carry tension — push the "
        "subject off-centre, a stronger high or low angle, a subtle Dutch tilt where that panel's beat "
        "supports it. Retain calmer, more level panels elsewhere on the page for contrast — a page that "
        "is uniformly tense reads as flat, not tense. Never move the camera closer to raise pressure."
    ),
    "chaotic": (
        "CAMERA ENERGY — CHAOTIC: put the strongest camera instability — obvious Dutch tilt, unstable "
        "horizon, aggressive perspective — on the panel(s) that are the page's genuine high-energy beat "
        "(battle, collapse, violent confrontation, panic). Vary the angle pressure panel to panel rather "
        "than repeating one canted angle everywhere. FORBIDDEN: applying Dutch/canted angle to every panel "
        "on the page by default — that flattens the page into one repeated gimmick instead of a peak."
    ),
}

_DIR_MANGA_CAMERA_LOCK = (
    "CAMERA ENERGY CHANGES CAMERA PRESENTATION ONLY, NEVER CHARACTER PERFORMANCE: it never makes a "
    "character act more violently, more emotional, or faster, and it never changes the event. It also "
    "never changes camera distance, POV, spacing, or where characters physically stand."
)

# Lighting: เหมือน single-image (ห้ามคิดแหล่งใหม่) แต่เพิ่มกฎ continuity ข้ามพาเนล — ปัญหาที่ audit เจอ
# คือถ้าไม่พูดถึงเรื่องนี้ตรงๆ โมเดลมักให้แต่ละพาเนลมีแหล่งแสงของตัวเอง ทั้งที่เป็นฉากเดียวกันทั้งหน้า
_DIR_MANGA_LIGHTING = {
    "scene": (
        "LIGHTING — FOLLOW SCENE: keep the light in every panel exactly as the page already has it. Name "
        "the one physical source that exists (moon, window, lamp, sun, fire) and its direction; do not "
        "raise or lower contrast beyond what the page already implies in any panel."
    ),
    "enhance": (
        "LIGHTING — ENHANCE: the SAME physical source in every panel, made more readable — state its "
        "direction and the surfaces it lands on per panel, with a clearer subject/environment separation. "
        "Do not invent a source a panel does not already have."
    ),
    "dramatic": (
        "LIGHTING — DRAMATIC: the SAME physical source throughout the page, staged for stronger tonal "
        "separation and shadow hierarchy. The strongest contrast may concentrate on the page's dominant "
        "panel; other panels keep the same source and stay tonally continuous with it rather than each "
        "inventing their own high-contrast treatment. FORBIDDEN: a second light source, or a source that "
        "does not exist in the established scene, in any panel."
    ),
    "soft": (
        "LIGHTING — SOFT: the SAME physical source in every panel, broadened — soft-edged shadows, lower "
        "contrast, gentle tonal transitions. Continuity across panels matters more than variety here."
    ),
}

_DIR_MANGA_LIGHTING_CONTINUITY = (
    "CROSS-PANEL LIGHT CONTINUITY (CRITICAL): every panel on this page belongs to the same established "
    "scene unless the story itself already changes location or time within the page. All panels keep the "
    "same physical light source; only its angle, exposure, and how much compositional emphasis it gets may "
    "vary panel to panel. FORBIDDEN: panel 1 daylight, panel 2 an invented neon source, panel 3 invented "
    "moonlight — inventing a different light source per panel unless the story already establishes that "
    "change."
)

# Environment Response: ต้องขึ้นกับสเกลของแต่ละพาเนล — พาเนลกว้างตอบสนองที่ระดับพื้นที่/บรรยากาศ
# พาเนล close ตอบสนองที่ระดับผิว/ผม/ผ้า และห้ามใช้คำบรรยายเดียวกันซ้ำทุกพาเนล
_DIR_MANGA_ENVIRONMENT = {
    "off": (
        "ENVIRONMENT RESPONSE — OFF: no Director-driven amplification of weather/world physics in any "
        "panel. Leave hair, cloth, and surfaces as the page already describes them."
    ),
    "natural": (
        "ENVIRONMENT RESPONSE — NATURAL: let whatever environmental force the page already establishes "
        "act on visible surfaces the way it physically would, in each panel where that panel's scale can "
        "show it. Do not invent a weather the page did not state."
    ),
    "emphasized": (
        "ENVIRONMENT RESPONSE — EMPHASIZED: take the environmental force the page ALREADY has and make it "
        "unmistakable, distributed according to what each panel actually shows — a wide panel reads the "
        "force at the environment/atmosphere level (rain on the ground, wind moving mist or dust across "
        "space); a close panel reads it at the character/material level (wet hair, damp fabric, skin); an "
        "action panel reads it as wind or spray pulled by the movement itself. Do NOT repeat identical "
        "weather wording in every panel — the force is the same, but its visible consequence changes with "
        "what each panel is actually framing. NOT WEATHER INTENSITY: this raises visual readability only — "
        "a light breeze stays a light breeze in every panel, never escalated into a storm. FORBIDDEN to "
        "invent a force the page did not state."
    ),
}

# Motion: ความต่างที่ใหญ่สุดจาก single-image — หน้าเดียวมีหลายพาเนลที่ "ได้รับอนุญาต" ให้เป็นจังหวะ
# ต่อเนื่องของแอ็กชันเดียวกันอยู่แล้ว (โครงพาเนลเดิมเป็นคนอนุญาต ไม่ใช่ Director ไปสร้างเอง)
_DIR_MANGA_MOTION = {
    "natural": (
        "MOTION — NATURAL: preserve each panel's existing action phase exactly. Add only the minimum "
        "wording needed to make each panel's body legible. Do not add motion, escalate it, or rewrite a "
        "still panel into mid-action."
    ),
    "dynamic": (
        "MOTION — DYNAMIC: keep the SAME action verb each panel already has, and make the body mechanics "
        "of that same action more legible — weight, lean, which foot is loaded, momentum direction. You "
        "may let the contrast between panels' phases read more clearly (an earlier panel's coiled stance "
        "vs a later panel's release), but never invent a phase a panel does not already depict. FORBIDDEN: "
        "promoting an action (standing does not become walking, walking does not become running)."
    ),
    "peak": (
        "MOTION — PEAK: identify the panel that already holds the strongest existing action phase and "
        "sharpen that ONE panel toward the clearest, most committed instant of the SAME action already "
        "there — real body mechanics (weight transfer, which leg, how far the torso has rotated), not a "
        "label. The other panels keep their existing role and phase unchanged; do not turn every panel "
        "into peak action. FORBIDDEN: inventing an extra action beat, jumping the story forward, or "
        "changing which action any panel depicts."
    ),
}

# ห้ามเรียงลำดับ authorized sequence ใหม่ หรือเร่งเรื่องเกินกว่าที่พาเนลที่มีอยู่บอกไว้แล้ว
_DIR_MANGA_MOTION_SEQUENCE_LOCK = (
    "PANEL SEQUENCE IS AUTHORIZED, NOT YOURS TO EXTEND: the existing panels may already depict sequential "
    "micro-beats of one action (a run building across panels, a reach becoming a grab) — that authorization "
    "comes from the page as already written, never from you. Preserve the panel order and what each panel "
    "already depicts. Do NOT collapse the whole page into one frozen instant, invent a new micro-event "
    "between panels, reorder beats, or accelerate the story beyond what the panels already contain."
)

def _director_manga_pass_blocks(lighting: str, composition: str, environment: str,
                                motion: str = "natural", camera_energy: str = "natural",
                                shot_scale_override: str = "") -> str:
    """Manga-specific translation of the six Director controls — PAGE level (hierarchy/rhythm across
    panels) + PANEL level (concrete staging inside one panel), never the single-image blocks verbatim.
    Every clause stays backend-identical (Local/Grok share the same _director_sys_for/_director_sys_slim
    call sites) and format-lock/panel-count/order protection lives in _DIR_MANGA_PAGE_RULE, which always
    ships first so it outranks every option below it."""
    light = (lighting or "enhance").strip().lower()
    comp = (composition or "balanced").strip().lower()
    comp = _DIR_COMPOSITION_ALIAS.get(comp, comp)
    env = (environment or "natural").strip().lower()
    env = _DIR_ENV_ALIAS.get(env, env)
    mot = (motion or "natural").strip().lower()
    mot = _DIR_MOTION_ALIAS.get(mot, mot)
    cam = (camera_energy or "natural").strip().lower()
    scale_key = (shot_scale_override or "").strip().lower()

    parts: list[str] = [_DIR_MANGA_PAGE_RULE]
    parts.append(_DIR_MANGA_SCALE_INTENT.get(scale_key, _DIR_MANGA_SCALE_INTENT[""]))
    parts.append(_DIR_MANGA_COMPOSITION.get(comp, _DIR_MANGA_COMPOSITION["balanced"]))
    parts.append(_DIR_MANGA_CAMERA.get(cam, _DIR_MANGA_CAMERA["natural"]))
    parts.append(_DIR_MANGA_CAMERA_LOCK)
    parts.append(_DIR_MANGA_LIGHTING.get(light, _DIR_MANGA_LIGHTING["enhance"]))
    parts.append(_DIR_MANGA_LIGHTING_CONTINUITY)
    parts.append(_DIR_MANGA_ENVIRONMENT.get(env, _DIR_MANGA_ENVIRONMENT["natural"]))
    parts.append(_DIR_MANGA_MOTION.get(mot, _DIR_MANGA_MOTION["natural"]))
    parts.append(_DIR_MANGA_MOTION_SEQUENCE_LOCK)
    return "\n".join(parts)

# ── PROFESSIONAL-AUTONOMY-CLEANUP-A2 ─────────────────────────────────────────
# แทนที่ _director_pass_blocks เดิมทั้งฟังก์ชัน — เวอร์ชันเก่าแปล dropdown แต่ละตัวเป็น "สูตรเทคนิค"
# (เอียงกล้อง/foreground-midground-background/mid-stride ฯลฯ) ซึ่งเป็นการสั่ง HOW ให้ LLM ทำตาม แล้ว
# Python ยังไล่เช็ค keyword ย้อนหลังว่าทำตามสูตรจริงไหม (ดู director_pass_consequences ที่ถูกลบไปแล้ว)
# ฟังก์ชันใหม่ทำหน้าที่เดียว: บอกว่า "user อยากได้ผลลัพธ์แบบไหน" (WHAT the user wants to see) แล้วปล่อยให้
# Director (LLM, ROLE/GOAL/AUTHORITY prompt ใน _director_sys_for) เป็นคนตัดสินใจ HOW เอง ต่อช็อต
# ระยะภาพ (shot scale) ไม่อยู่ในนี้ — มันเป็นเรขาคณิตแข็ง (hard geometry) ที่คุมแยกอยู่แล้วผ่าน SHOT SCALE
# / spacing ใน user message และ DISTANCE LOCK ใน _director_sys_for ไม่ใช่รสนิยมที่มาปนกับ brief นี้
_DIR_COMPOSITION_MEANING = {
    "off": "OFF — leave the existing framing and arrangement as it stands; do not restage for its own sake.",
    "balanced": "BALANCED — clear, deliberate visual organization: give the frame one legible hierarchy and real depth.",
    "cinematic": "CINEMATIC — stronger visual hierarchy and depth; make the frame read as deliberately composed.",
    "bold": "BOLD — a strong, graphically confident frame; push the composition further than a balanced default.",
}

_DIR_CAMERA_MEANING = {
    "stable": "STABLE — a settled, composed camera with no imposed tension.",
    "natural": "NATURAL — whatever camera treatment suits this beat, with no imposed mood.",
    "tense": "TENSE — increase visual pressure.",
    "chaotic": "CHAOTIC — strong visual instability, an unmistakable loss of control on screen.",
}

_DIR_LIGHTING_MEANING = {
    "scene": "FOLLOW SCENE — keep the light exactly as the shot already has it; add no new intention.",
    "enhance": "ENHANCE — make the existing light source read clearly.",
    "dramatic": "DRAMATIC — the user wants stronger dramatic light/shadow impact while using physically supported light.",
    "soft": "SOFT — gentle, diffused treatment of the existing light.",
}

_DIR_ENVIRONMENT_MEANING = {
    "off": "OFF — do not amplify environmental force beyond what is already written.",
    "natural": "NATURAL — let the environment already present act on visible surfaces plausibly.",
    "emphasized": "EMPHASIZED — make the existing environment's effect more visually prominent.",
}

_DIR_MOTION_MEANING = {
    "natural": "NATURAL — keep the pose and action exactly as written, only as legible as it needs to be.",
    "dynamic": "DYNAMIC — energetic treatment of the same action; make it feel visually active without changing the action.",
    "peak": "PEAK MOMENT — the strongest readable phase of the same action, if it is in motion; a still action stays still.",
}

def _director_user_brief(lighting: str, beauty: str, environment: str,
                         motion: str = "scene", scale_preset: str = "",
                         camera_energy: str = "natural") -> str:
    """สถานะจาก dropdown Director แปลเป็น "เจตนาของ user" ล้วนๆ ไม่ใช่สูตรเทคนิคกล้อง

    แต่ละบรรทัดบอกว่า user อยากเห็นผลลัพธ์แบบไหน (WHAT visual quality) — ไม่บอกว่าต้องทำอย่างไร (HOW).
    การเลือก "วิธี" ที่เหมาะกับช็อตนี้เป็นดุลพินิจของ Director (LLM) ล้วนๆ
    lighting: scene|enhance|dramatic|soft
    beauty (composition): off|balanced|cinematic|bold  (รับค่าเก่า light/push ด้วย)
    environment: off|natural|emphasized                (รับค่าเก่า on/strong ด้วย)
    motion: natural|dynamic|peak                       (รับค่าเก่า scene/sharpen/freeze ด้วย)
    scale_preset: ใส่เมื่อ user สั่ง override ladder เท่านั้น — เนื้อหาที่เพิ่มยังเป็นคำอธิบาย "ภาษาของซีเควนซ์"
    ไม่ใช่สูตรกล้อง จึงยังเก็บไว้เป็น hard/narrative geometry เดิม"""
    light = (lighting or "enhance").strip().lower()
    beau = (beauty or "balanced").strip().lower()
    beau = _DIR_COMPOSITION_ALIAS.get(beau, beau)
    env = (environment or "natural").strip().lower()
    env = _DIR_ENV_ALIAS.get(env, env)
    mot = (motion or "natural").strip().lower()
    mot = _DIR_MOTION_ALIAS.get(mot, mot)
    cam = (camera_energy or "natural").strip().lower()

    lines: list[str] = ["USER DIRECTING BRIEF"]
    _intent = _DIR_SCALE_INTENT.get((scale_preset or "").strip().lower())
    if _intent:
        lines.append(_intent + " Detail level always follows the distance each shot ended up with — "
                      "a preset never licenses detail the assigned scale cannot show.")

    lines.append("\nComposition intention:")
    lines.append(_DIR_COMPOSITION_MEANING.get(beau, _DIR_COMPOSITION_MEANING["balanced"]))
    lines.append("\nCamera energy:")
    lines.append(_DIR_CAMERA_MEANING.get(cam, _DIR_CAMERA_MEANING["natural"]))
    lines.append("\nLighting intention:")
    lines.append(_DIR_LIGHTING_MEANING.get(light, _DIR_LIGHTING_MEANING["enhance"]))
    lines.append("\nEnvironment response:")
    lines.append(_DIR_ENVIRONMENT_MEANING.get(env, _DIR_ENVIRONMENT_MEANING["natural"]))
    lines.append("\nMotion treatment:")
    lines.append(_DIR_MOTION_MEANING.get(mot, _DIR_MOTION_MEANING["natural"]))

    lines.append(
        "\nInterpret these controls together as one creative brief. They describe the result the user "
        "wants, not the cinematographic technique you must use. Choose the implementation yourself for "
        "this specific shot — the right visual solution depends on what this shot already contains, and "
        "it may differ from shot to shot even with the same controls selected."
    )
    # invariant ทางเรขาคณิต (ไม่ใช่รสนิยม): camera energy เปลี่ยนแรงกดดันภาพ ไม่ใช่ระยะกล้อง — ยังคงไว้
    # เป็นประโยคเดียวสั้นๆ ไม่ใช่สูตร เพราะมันเป็นสัญญาโครงสร้างเดียวกับ DISTANCE LOCK ใน _director_sys_for
    lines.append(
        "Camera energy changes orientation and visual pressure only — it never changes camera distance, "
        "POV, spacing, or where characters physically stand; those stay exactly as assigned."
    )
    if mot != "natural" or env != "off":
        lines.append(_DIR_SECONDARY_MOTION)
    return "\n".join(lines)

@_llm_debug_stage("director")
async def run_director_prompts(
    shots_data: list[dict],
    llm_wf_file: str,
    character_desc: str,
    scene_desc_global: str,
    requirement: str,
    story_text: str,
    story_summary: str,
    on_shot,
    on_err,
    on_log=None,
    on_progress=None,
    stop_event: asyncio.Event | None = None,
    single_shot_idx: int | list[int] | set[int] | None = None,
    extra_req: str = "",
    image_style: str = "",
    image_size: str = "",
    genre: str = "",
    genre2: str = "",
    dialogue_style: str = "",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    sys_variant: str = "",
    prompt_style: str = "narrative",
    manga_page: bool = False,
    shot_scale_override: str = "",   # "" = ใช้ค่าที่ outline แจกไว้แล้วในแต่ละช็อต
    shot_scale_preset: str = "balanced",
    props_desc: str = "",
    lighting_pass: str = "enhance",       # scene|enhance|dramatic|soft
    composition_pass: str = "balanced",   # off|balanced|cinematic|bold
    camera_energy: str = "natural",       # stable|natural|tense|chaotic
    environment_pass: str = "natural",    # off|natural|emphasized
    motion_pass: str = "natural",         # natural|dynamic|peak
    beauty_pass: str = "",                # ชื่อเดิมของ composition_pass — ยังรับไว้เผื่อ state เก่า
    fill_engine: str = "",                # contract ที่ Fill ใช้เขียน prompt — ว่าง = อ่านจาก shot._fill_engine
    a1: dict | None = None,
):
    """
    Director LLM: แก้ Camera angle/framing และ Lighting ให้สอดคล้องกับ mood
    และปรับ "ระดับรายละเอียด" ของทุก section ให้ตรงกับ shot_scale ที่กำหนด
    (ลบสิ่งที่มองไม่เห็นที่ระยะนั้น แล้วเขียนใหม่ในระดับที่เห็นได้)
    ห้ามเปลี่ยนข้อเท็จจริงของเรื่อง — ท่าทาง สถานะเสื้อผ้า จำนวนตัวละคร prop สถานที่
    (ส่วนพวกนั้นยังเป็นหน้าที่ของ editor)
    """
    # DIRECTOR-C1: single_shot_idx เดิมรับได้แค่ int เดียวหรือ None (ทั้งหมด) — auto-chain ฝั่ง Grok
    # ต้องการ "เฉพาะช็อตที่เพิ่งผ่าน Fill/Checker รอบนี้" ซึ่งเป็นเซตย่อยของ shots_data ไม่ใช่ทั้งหมดและ
    # ไม่ใช่ตัวเดียว — ขยายให้รับ list/set ได้ด้วย โดยยังคง behavior เดิมทุกจุดที่ส่ง int หรือ None มา
    _target_idxs: set[int] | None
    if single_shot_idx is None:
        _target_idxs = None
    elif isinstance(single_shot_idx, int):
        _target_idxs = {single_shot_idx}
    else:
        _target_idxs = set(single_shot_idx)
    # sys_variant คุมขนาด system prompt เท่านั้น ไม่แตะสัญญาเชิงพฤติกรรมของ stage ใด
    _variant, _variant_why = _sys_variant_of(grok_api_key, sys_variant, llm_wf_file)
    if on_log:
        await on_log(f"⚙ Director: variant={_variant} ({_variant_why})")
    # manga มีระบบ multi-panel framing ของตัวเอง (หลายระยะในภาพเดียว) ที่ขัดกับ shot scale โดยธรรมชาติ —
    # ปิดสิทธิ์ scale-audit/rewrite ทั้งหมดในโหมดนี้
    # ตาม outline ก็ต้อง AUDIT รายละเอียดตามระยะที่แจกไว้แล้ว — เดิมปิด rewrite ไว้เลย
    # ทำให้ผิว/ตา/นิ้วหลุดค้างในช็อต wide
    # config เก่าอาจยังส่ง beauty_pass= และค่าเก่าอย่าง light/push/on/strong/scene/sharpen/freeze มา
    # แปลงที่ปากทางครั้งเดียว ข้างในจึงเห็นแต่ชื่อใหม่
    composition_pass = (composition_pass or beauty_pass or "balanced").strip().lower()
    composition_pass = _DIR_COMPOSITION_ALIAS.get(composition_pass, composition_pass)
    environment_pass = _DIR_ENV_ALIAS.get((environment_pass or "natural").strip().lower(),
                                          (environment_pass or "natural").strip().lower())
    motion_pass = _DIR_MOTION_ALIAS.get((motion_pass or "natural").strip().lower(),
                                        (motion_pass or "natural").strip().lower())
    camera_energy = (camera_energy or "natural").strip().lower()
    # NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §4: this ladder-assigned camera-DISTANCE CATEGORY
    # (wide/medium/close/full body, from _assign_shot_scale) is Director's own derived HOW-level
    # control value — it is NOT the same thing as B's native `camera` field (a free-text framing
    # INTENTION like "low angle, wide-to-medium"). There is no honest native equivalent to alias
    # this onto, so it is kept as its own private runtime-metadata key ("_dir_scale_category",
    # underscore-prefixed like the other Director/Fill runtime stamps such as _fill_engine) rather
    # than resurrecting the legacy "shot_scale" story-field name or inventing a fake mapping onto
    # `camera`. Persisted on the shot only so a later single-shot Director re-run doesn't reassign
    # a different category to a shot whose sequence position hasn't changed.
    _allow_scale_rewrite = not manga_page
    if not manga_page:
        _preset = (shot_scale_override or shot_scale_preset or "balanced")
        for _i, _s in enumerate(shots_data):
            if shot_scale_override or not (_s.get("_dir_scale_category") or "").strip():
                # override = แจก ladder ใหม่ทั้งซีเควนซ์
                # ตาม outline แต่ช็อตไม่มี category (ช็อตแทรก/ของเก่า) = อย่า default เป็น medium
                _s["_dir_scale_category"] = _assign_shot_scale(
                    _i, len(shots_data), _preset,
                    force_wide=_is_penetrative_scene(_s.get("shot_desc", ""), _s.get("clothing", "")),
                )
    # Director เป็นด่านที่เติมกล้องและแสง ถ้ามันไม่รู้ว่า Fill เขียนด้วยสัญญาไหน มันจะจัดรูป
    # กลับไปเป็นแบบ Legacy (ป้าย Pose:/Climax:, ล็อกแบบเก่า) แล้วลบสิ่งที่ Minimal V3 ตั้งใจทำทิ้ง
    _dir_engine = normalize_fill_engine(fill_engine)
    if not (fill_engine or "").strip():
        for _s in shots_data or []:
            if isinstance(_s, dict) and _s.get("_fill_engine"):
                _dir_engine = normalize_fill_engine(_s.get("_fill_engine"))
                break
    _dir_v3 = _dir_engine in _LOCK_CONTRACT_ENGINES
    if on_log:
        # แจกแจงทีละบรรทัด — เวลาภาพออกมาแรงหรือเนือยกว่าที่คิด จะได้รู้ทันทีว่า control ไหนทำ
        await on_log(
            "🎬 DIRECTOR PASS\n"
            f"   Fill contract: {fill_engine_label(_dir_engine)}\n"
            f"   Scale       : {shot_scale_override or 'Follow Outline'}"
            + ("  (scale audit disabled — manga)" if manga_page else "") + "\n"
            f"   Composition : {composition_pass}\n"
            f"   Camera      : {camera_energy}\n"
            f"   Lighting    : {lighting_pass or 'enhance'}\n"
            f"   Environment : {environment_pass}\n"
            f"   Motion      : {motion_pass}"
        )
        # ค่าที่ engine ได้รับจริง — ไว้เทียบกับ UI SNAPSHOT ที่ฝั่งหน้าจอ log ไว้ตอนกดปุ่ม
        # ถ้าสองก้อนนี้ไม่ตรงกัน แปลว่า dropdown ส่งค่าไม่ตรงกับที่แสดงอยู่
        await on_log(
            "🎬 DIRECTOR ENGINE ARGS\n"
            f"   shot_scale_override={shot_scale_override!r}\n"
            f"   composition_pass={composition_pass!r}\n"
            f"   camera_energy={camera_energy!r}\n"
            f"   lighting_pass={(lighting_pass or 'enhance')!r}\n"
            f"   environment_pass={environment_pass!r}\n"
            f"   motion_pass={motion_pass!r}"
        )
    _pass_blocks = _director_manga_pass_blocks(
        lighting_pass, composition_pass, environment_pass, motion_pass,
        camera_energy=camera_energy, shot_scale_override=shot_scale_override,
    ) if manga_page else _director_user_brief(
        lighting_pass, composition_pass, environment_pass, motion_pass,
        scale_preset=(shot_scale_override or ""), camera_energy=camera_energy,
    )

    _format_lock = (
        "- FORMAT LOCK (CRITICAL — outranks the Fill-dialog dropdown and every format example): "
        "keep the SAME format as the incoming Current prompt.\n"
        "  • labeled sections (Camera:/Subject:/Outfit:/Environment:/Lighting:/Pose:/Climax:) "
        "→ keep those labels and order. Do NOT flatten into one paragraph.\n"
        "  • comma-separated short tags → stay tags. Do NOT write prose sentences.\n"
        "  • one flowing paragraph → stay one paragraph. Do NOT add Camera:/Subject: labels.\n"
        "  • multi-panel page ('N-panel page...') → keep that structure.\n"
        "Never convert from one of these into another. The user message names the incoming format. "
        "Edit camera, lighting, and detail INSIDE that format only."
    )
    if _dir_v3:
        # V3 เขียนมาให้แล้วว่า "อะไรอยู่ในภาพ" — Director แตะได้แค่กล้อง/แสง/ระดับรายละเอียด
        _format_lock += (
            "\n- MINIMAL V3 PROMPT — this prompt was written as a strict serializer of one resolved "
            "shot. Extra rules that outrank your usual freedom:\n"
            "  • IDENTITY LOCK is a required block, and it is a BLOCK: the line \"IDENTITY LOCK:\" "
            "stands on its own line, one person per line under it, a blank line after it. Return it "
            "with those line breaks intact. Never delete it, never fold it into a sentence or into "
            "the paragraph, never duplicate it, and never move it to the end. Leave its lines "
            "exactly as written unless a line contradicts the Outline.\n"
            "  • KEEP THE LINE STRUCTURE of what you were given: the opening framing line, the "
            "lock block, then the body. Returning everything as one long run-on paragraph is a "
            "format failure even when every fact survives.\n"
            "  • Do NOT copy the framing hint you were given in SHOT SCALE into the prompt text. "
            "Write the framing in your own words; never paste a parenthetical description of what "
            "the scale means.\n"
            "  • A permanent body fact (mechanical arms, prosthetic, wings, tail, horns) never "
            "disappears — at wide you may reduce its detail, never remove it.\n"
            "  • Do NOT add a Pose: or Climax: section, and do not demand one. If the incoming "
            "prompt has no Climax, that is correct.\n"
            "  • Do NOT add rendering-style words of your own — film grain, analog, anamorphic, "
            "low contrast, colour grading, cinematic film still, artistic framing, neo-noir as a "
            "preset, anime/cartoon/cel shading. Style comes from the selected Image Style only.\n"
            "  • Do NOT invent pose, gaze direction, expression detail, body angle, handedness, "
            "architecture, furniture, weather or time of day. You add camera framing and lighting "
            "treatment; everything about WHO and WHAT stays exactly as written.\n"
            "  • Lighting: you may shape how the supplied light reads. Do not invent a new source, "
            "a direction, or a time of day the prompt does not already state."
        )
    _format_rule = _format_lock
    _prompt_field_desc = "complete prompt in the SAME format as Current prompt — do not convert"
    wf_template = None
    if llm_wf_file != GROK_MARKER:
        llm_wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not llm_wf_path.exists():
            await on_err(f"ไม่พบ {llm_wf_path}"); return
        try:
            wf_template = json.loads(llm_wf_path.read_text(encoding="utf-8"))
        except Exception as e:
            await on_err(str(e)); return

    loop = asyncio.get_event_loop()
    num  = len(shots_data)

    outline_lines = []
    for i, s in enumerate(shots_data):
        outline_lines.append(f"Shot {i+1}: {s.get('shot_desc','')}")
    outline_summary = "\n".join(outline_lines)

    # character_desc / props_desc ไม่ถูกส่งเข้า Director อีกแล้ว (ดู CONTENT LOCK) — ยังรับพารามิเตอร์ไว้
    # เพราะ validator หลัง Director และผู้เรียกเดิมยังใช้ แต่ไม่ไหลเข้า prompt
    req_str   = f"Special requirements: {requirement}" if requirement else ""
    extra_str = f"\n⚠ EXTRA REQUEST FROM USER: {extra_req}" if extra_req else ""
    style_str = f"Image Style: {image_style}" if image_style else ""
    size_str  = f"Canvas Size: {image_size}" if image_size else ""
    genre_str  = _build_genre_str(genre, genre2)
    _dir_dlg_labels = {
        "speech_bubble":    "Speech Bubble — injected in prompt by system",
        "narration_box":    "Narration Box AI — prompt ends with 'comic book style manga text overlay:...' suffix — MUST be preserved",
        "narration_box_pil":"Narration Box PIL — NOT in prompt",
        "subtitle_bar":     "Subtitle Bar PIL — NOT in prompt",
        "none":             "None",
    }
    dlg_style_str = f"Dialogue Display Style: {_dir_dlg_labels.get(dialogue_style, dialogue_style)}" if dialogue_style else ""
    # director ปรับกล้อง/แสง/ระดับรายละเอียด แต่ต้องรู้ว่าเรื่องอยู่ตรงไหนถึงจะไม่ขัดกับฉาก —
    # เดิมได้แค่ summary ซึ่งตอนนี้เลิกผลิตแล้ว จึงส่งเนื้อเรื่องจริงไปแทน
    # Director ไม่ได้รับเนื้อเรื่อง ชีตตัวละคร และชีต props อีกต่อไป — มันตัดสินใจเรื่อง "ภาพ" ของช็อต
    # ที่เขียนไว้แล้วเท่านั้น ข้อมูลพวกนั้นมีแต่ทำให้มันเอาค่าจากชีตมาทับสิ่งที่ user แก้ใน prompt เอง
    # (เจอจริง: user เปลี่ยนชื่อตัวละครใน prompt แล้ว Director ดึงชื่อเดิมจากชีตกลับมา)
    # AGENT-STORY-TRUTH-ROUTING-A1: Director previously had NO story-source grounding at all
    # (story_ctx stayed empty and unused) — now it gets exact A1 story truth so it can reason about
    # a TRUE story moment instead of only the already-written prompt: Local gets this ONE shot's
    # resolved parent E[n] block(s) (from "uses", injected per-shot below — never a guess), Grok
    # gets ALL blocks appended once to the shared batch system prompt.
    story_ctx = ""
    _dir_all_blocks = _a1_blocks_labeled(a1) if a1 else ""

    # director_sys เดิมสร้างก้อนเดียวใช้ทุกช็อต จึงแบก DISTANCE LOCK และรายการ FORBIDDEN ของ
    # wide/extreme wide ไปกับช็อต close-up ที่กฎพวกนั้นไม่มีทางถูกทริกเกอร์
    _director_sys_cache: dict[str, str] = {}

    # สร้าง slim ก่อน _director_sys_for เพราะฟังก์ชันนั้นอ้างถึงมัน — ถ้าปล่อยไว้ท้ายไฟล์
    # การเรียก _director_sys_for ก่อนบรรทัดนี้ทำงานจะพังด้วย NameError เงียบ ๆ
    director_sys_slim = f"""You are the cinematographer photographing this shot. The Current prompt already
says WHAT is in the picture; you decide only HOW it is photographed, then return the whole prompt in its
own format. Keep every story fact exactly as written — cast, identity, pose and action verb, clothing and
clothing state, prop identity/state/ownership, location, weather, time of day, dialogue, and spacing (how
far the PEOPLE stand, which depth plane, the gap between them). The only content change you may make is
the LEVEL OF DETAIL a fact is described at, so it matches the assigned SHOT SCALE; at wide/extreme wide
drop skin, eyes, fingers and fabric weave, but keep the garment identity and the gender noun. IDENTITY
LOCK, LOCATION LOCK and OBJECT LOCK blocks are READ-ONLY: preserve their content and placement exactly.
{_format_rule}

USER DIRECTING BRIEF — the controls below were chosen by the user for this run. Read them together as one
creative brief and find the visual solution THIS shot needs; do not translate each one into a separate
canned trick.
{_pass_blocks}
{style_str}
{req_str if req_str else ""}{extra_str}

The assigned SHOT SCALE is explicit geometry, not a mood preference — photograph at that distance. Spacing
is story geometry too: never close a FAR/APART gap for readability or romance; when spacing is FAR, make
the distance physically obvious (depth planes, relative scale, this environment BETWEEN them) right after
the framing, never by tightening the camera. Describe visual EFFECTS, not specs ("shallow depth of field,
background softly blurred", not "f/1.4"). Never write a mood word into the prompt — show it through the
visual choices. Never use "reads/reading" as visual-analysis shorthand (write "appears small and distant",
"dominates the composition"); a literal reading action stays as written. Any dialogue overlay suffix
already in the prompt is copied VERBATIM; for narration_box you may change only a position word.
Expression, gaze direction and speaking state already in the prompt are story content — preserve them; you
may enrich a thin expression by at most one short clause, never replace or neutralize one.

BEFORE YOU SUBMIT, silently ask: does this genuinely fulfil the user's brief as one coherent image? Is it
still exactly the same story moment? Is the important action easy to read? Are scale, spacing and depth
physically clear? Is the light physically possible in this scene? Does the presentation serve the real
action rather than manufacturing motion? Does the frame work beside its neighbours without forcing
variation? Did every locked fact and the prompt's structure survive?

keep_original:true only if the framing and lighting are already right for this brief.
Output ONLY valid JSON:
{{"shot":<n>,"keep_original":<bool>,"issues":["<short English max 8 words>"],"reason":"<English max 10 words>","prompt":"{_prompt_field_desc}","dialogue_position":"<top|bottom>"}}"""

    def _director_sys_for(scale: str) -> str:
        # routing เป็นคนเลือก slim — เดิม director_sys_slim ถูกสร้างไว้แต่ไม่มีใครเรียกเลย
        if _variant == SYS_VARIANT_SLIM:
            return director_sys_slim
        key = "" if manga_page else (scale or "medium").strip().lower()
        if key in _director_sys_cache:
            return _director_sys_cache[key]
        _far = key in ("wide", "extreme wide")
        _r_dist_lock = (
            '   - DISTANCE LOCK: the opening stays at the assigned distance. FORBIDDEN substitutes: '
            '"medium shot", "MCU", "close-up", "tight shot", "portrait", "headshot", "waist-up", '
            '"half-body", "upper body", "chest up", "bust shot". Two consecutive assigned-wide shots '
            'both stay wide — distance is never the variety knob.\n'
        ) if _far else ""
        _r_audit_far = (
            '  FORBIDDEN leftovers at this distance: skin, pores, eye colour, lips, individual fingers, '
            'fabric weave, light on a face. Gender noun (woman/man/girl/boy) STAYS — never collapse to '
            '"a figure" or "a silhouette" without the noun.\n'
        ) if _far else ""
        director_sys = f"""You are a professional cinematographer and visual director working on a single
still frame (not video).

ROLE
You own HOW this moment is photographed. You do NOT own WHAT happens in it. The Current prompt you are
given is the resolved story moment — the content authority, possibly containing the user's own hand edits.
Your output is that same moment, re-photographed: the complete prompt, in the same format it arrived in.

MATERIAL
  A. CURRENT PROMPT — the resolved content to photograph, and the authority on every visible fact.
  B. USER DIRECTING BRIEF — the Director controls below are the user's creative requests for this run.
  C. SEQUENCE CONTEXT — the neighbouring shots, for visual relationship and rhythm.
  D. PRODUCTION CONTEXT — image style, canvas, and the physically established environment and light.

GOAL
Photograph the exact resolved story moment as effectively as possible while fulfilling the user's selected
directing intentions. The controls say WHAT visual treatment the user wants; YOU choose how to achieve it,
and the right answer depends on this specific shot. "Camera energy = tense" does not mean a Dutch angle —
it may be camera height, depth compression, obstruction, asymmetry, stillness, negative space or framing
pressure. Solve the whole brief as ONE coherent image, never as a list of independent tricks appended to
each other.

AUTHORITY ORDER
  1. Canonical content / story truth in the Current prompt.
  2. Hard physical geometry: the assigned SHOT SCALE. This is explicit geometry, not a soft preference —
     if the prompt says "close-up" and the assigned scale is wide, the shot becomes wide, and that
     correction is exactly your job. Spatial relationship (how far people stand) is not a separately
     assigned value — it is canonical content already locked into the Current prompt (item 1 above);
     preserve it exactly, never adjust it for readability or romance.
  3. The user directing brief (composition, camera energy, lighting, environment response, motion, and any
     extra request). Inside your HOW domain the user's creative direction has priority over your taste.
  4. The existing sequence context.
  5. Your own professional judgment.
A directing request never grants authority to rewrite WHAT. Where a Director-only request conflicts with
locked story content, the story content survives.

YOU MAY DECIDE: camera angle and height, composition and subject placement, foreground / midground /
background organisation, depth treatment, negative space, visual hierarchy and attention priority, the
lighting treatment of physically supported sources, contrast and shadow, how motion or stillness is
presented, the level of visible detail, and the visual relationship with neighbouring frames.

YOU MAY NOT CHANGE: visible cast, identity, canonical appearance, clothing, clothing state, object
identity, object state, ownership, the story action or the meaning of a pose, location, dialogue text, the
story event, spacing, or source truth. IDENTITY LOCK, LOCATION LOCK and OBJECT LOCK are READ-ONLY —
preserve their content and structural placement exactly as the active prompt contract requires.
{_IDENTITY_LOCK_DIRECTOR_RULE}
{_LOCATION_LOCK_DIRECTOR_RULE}
{_OBJECT_LOCK_DIRECTOR_RULE}
You point the camera and set the light; you do not move the actors or dress them. Rewording a pose
("lying supine" → "reclining"), removing or adding a garment, changing a garment's colour or type,
collapsing named garments into "dark clothing", or adding exposure the original did not have are all
changes of WHAT, not HOW. Copy pose, action and clothing language as close to verbatim as the scale allows.
Preserve established tracked-item identity and current ownership/state; do not turn "the same <item>" into
a newly introduced object and do not undo a visible transfer action.

{_DIRECTOR_CONTENT_LOCK}

{("Global Scene (REFERENCE ONLY — for judging what light and weather are physically possible here. It may NEVER overwrite, correct or restore anything the Current prompt already says): " + scene_desc_global) if scene_desc_global else ""}
{style_str}
{size_str}
{genre_str}
{dlg_style_str}
{req_str if req_str else ""}{extra_str}

USER DIRECTING BRIEF (the user's selected controls for this run — read them together, as one brief)
{_pass_blocks}

{_format_lock}

STYLE VOCABULARY — write in the register the selected Image Style actually renders in: photographic detail
for Realistic, angle and composition for Anime/Manga/Comic/Pixel Art, composition and brushwork for
Painterly styles, render terms for 3D. Realistic is the one fixed requirement: the prompt must open with
"photorealistic, a photograph of" before the framing. With no style selected, add no prefix at all.
Describe visual EFFECTS, never technical specs — "shallow depth of field, subject sharp, background softly
blurred", not "85mm f/1.4"; "expansive wide framing", not "24mm"; a film look as an effect, never a stock
name. CANVAS SIZE decides horizontal vs vertical composition; it never changes the assigned SHOT SCALE.

{"PANEL CAMERA FRAMING — multi-panel mode is ON. Do NOT add a global framing prefix. Weave camera framing INSIDE each individual panel description; each panel may use its own framing to create reading rhythm. FORBIDDEN: any global framing sentence before 'N-panel page...'." if manga_page else """FRAMING — the camera is described first (the opening tag(s) in tag_style, the opening sentence in narrative style, or the "Camera:" section in sectioned style):
   - The SHOT DISTANCE is already assigned for this shot (given as "SHOT SCALE" in the user message) and is
     not yours to widen, tighten or substitute. The content was written for that distance.
{_r_dist_lock}   - Include the frame-occupancy phrase supplied with the assigned SHOT SCALE, not only the shot-type name.
   - Angle, height, view direction, subject placement, layering and negative space are all yours to choose,
     for this shot and this brief.
   - PENETRATIVE SEX scenes (explicit intercourse/oral/penetration in progress) are the one hard exception:
     the framing must be wide/full-body so the act reads clearly, even if that contradicts the assigned
     scale. FORBIDDEN there and on any assigned wide/extreme wide shot: "close-up", "tight shot", "portrait",
     "headshot", "upper body", "waist-up", "torso only", "half-body", "chest up", "bust shot", "medium shot",
     "MCU", "face shot", "hip/knee/thigh up". Non-penetrative intimate, undressing, transformation or quiet
     emotional beats are NOT covered by that exception — the assigned scale governs them normally.
   - NEVER use "read/reads/reading" as visual-analysis shorthand for scale, distance, prominence or
     silhouette clarity — it is ambiguous to an image model and has produced hallucinated books and reading
     poses. Write the drawable fact instead ("appears small and distant", "occupies a small portion of the
     frame", "dominates the composition", "is clearly visible"). A literal reading action stays as written.

SCALE AUDIT (always — the assigned SHOT SCALE is ground truth even when you did not reassign the ladder):
  If the prompt contains detail that cannot be seen at this distance, REMOVE it and restate at a
  visible level.
{_r_audit_far}  ALLOWED — rewrite Subject / Environment / Lighting / Climax to change the DETAIL LEVEL:
    * remove anything not resolvable at the assigned scale and restate it at a level that IS visible —
      "eyes fixed on the glue mark, lips parted" becomes "head bowed over the lantern at her chest";
      "right hand pushing on the ground" becomes "climbing on all fours up the slope"; "gentle shadows
      on pale skin" becomes "warm rim light along the roof edge". Garment DETAIL (weave, sheen) may drop
      at wide; garment IDENTITY (colour + type, wearer) may not, and never becomes a placeholder such as
      "doing this beat".
    * expand the environment with depth layers when moving wider, using only places and objects already
      named in the Current prompt — never invent a location, building, or object.
    * tighten the environment to a short phrase when moving closer.
  FORBIDDEN — these are story facts and are never yours to change:
    * the pose or main action verb (standing / kneeling / climbing / lifting the lantern) — the motion
      control may sharpen how that same verb is written, never swap it
    * clothing and any garment state
    * objects — who holds which story object, or where it sits this shot
    * the number or identity of characters present — every character the Current prompt names must still
      appear under that same name; FORBIDDEN to add one it does not name (no doorway watchers or distant
      silhouettes), and FORBIDDEN to rename one
    * a prop's material, colour, shape, or size, as the Current prompt words them
    * location, weather, time of day
    * spacing — how far the people stand, which depth plane each occupies, and the gap between them
  THE TEST: you may change how MUCH DETAIL the same thing is described with. You may never change the
  camera distance name, and you may never introduce a noun that is not already in the Current prompt."""}

   SPACING IS STORY GEOMETRY, NOT CAMERA GEOMETRY: character-to-character distance is whatever the Current
   prompt already establishes and is preserved exactly — it is not a separate field given to you, it is
   locked content within the prompt itself. Do not bring characters closer for readability or romance. When spacing
   is FAR, make the distance physically obvious — one figure near, the other much smaller and further, a
   large uninterrupted stretch of this environment between them, never both large on the same depth plane —
   and put that evidence right after the framing, where an image model weights it. Always state where each
   character sits in the frame, and never crop a named character out to simplify it.

   SEQUENCE RHYTHM: look at the previous shots above and judge whether this frame works beside them. Vary
   the treatment where variation serves the sequence, and hold it where repetition is doing real work — a
   locked frame, a held beat, a deliberate rhyme. Repetition is not automatically a defect, and forcing a
   different angle merely to avoid repeating one is not direction.

LIGHT — light this shot the way it needs to be lit for the user's brief and this moment. You may use
   source, direction, quality, colour temperature, contrast and shadow as freely as the scene physically
   supports; how much of that a given shot needs is your call, not a checklist to complete. What is NOT
   negotiable is physical possibility:
   - Every light must come from something that exists in this scene. "Harsh directional light from above",
     "strong rim light" or "a shaft from the upper right" have no emitter at night, indoors, or under storm
     cloud; under overcast or rain the sky is a dim, flat, sourceless fill and contrast comes only from the
     practical light the scene names.
   - The key source is fixed per scene and across the sequence: name the same physical source the earlier
     shots named, and change only its direction, distance, intensity, quality and the shadows it throws.
     Never invent a second source, a sun in a night scene or a candle the scene does not have because the
     mood changed — reach for composition and shadow instead.
   - A source only belongs in the prompt if it is large enough to read at the assigned distance. At wide
     and extreme wide, light the landscape, sky and silhouettes — never skin, a face or fabric texture, and
     never name an ember, a candle wick or a phone screen as what lights the frame. At medium and close-up,
     light on skin and fabric is correct and expected.
   - Never a lone vague adjective ("dramatic lighting", "nice light") standing in for the actual light.

DIALOGUE VISUAL STYLING — adjust only HOW dialogue is displayed, never the words:
   TEXT CONTENT IS SACRED — never change a word of narration/subtitle.
   - dialogue_position "top"/"bottom" based on composition (characters fill the opposite half from the text).
   - Narration box and speech bubble styling may follow the tone of the beat; keep boxes in corners/edges,
     never over faces or action.
   - Apply by editing only the visual descriptor words in the "comic book style manga text overlay:..."
     suffix — the quoted text itself stays 100% identical.

PRESERVE (all of these read the CURRENT PROMPT, never a project sheet):
1) Copy narration and speech fields unchanged.
2) Garments: exactly the pieces the Current prompt names, in its own wording — never removed, added,
   recoloured, retyped, or moved up or down the nudity scale, and never with tracker words (PARTIALLY
   UNDRESSED / UNDRESSED) pasted in. You do not re-resolve clothing; a disagreement with the project's
   clothing state is the Checker's finding, not your repair.
3) The pose keeps the SAME verb the Current prompt uses. The motion control may rewrite HOW that verb is
   described (mid-stride, weight, airborne foot); it never substitutes a different action.
4) GAZE DIRECTION IS SACRED — preserve it exactly; never insert "staring at the camera" unless the original
   said so.
5) EXPRESSION STATE LOCK: the defining expression already in the Current prompt is story content. Compress
   its wording for scale if you must, but never remove, neutralize, reverse or swap it — and never
   re-intensify or soften it toward what you think the mood should produce. You MAY enrich a genuinely thin
   expression by at most ONE short clause (max 12 words); that permission never licenses replacing one.
6) SPEAKING STATE LOCK: when speech_char and subtitle are non-empty that character is speaking in this
   frozen instant. If their face is readable at this scale keep a visible speaking-mouth cue; at wide or
   extreme wide carry it through posture instead. Never turn an active speaker into a closed, pressed-lips
   pose.
7) CONTENT FACTS SURVIVE THE REWRITE AS THE PROMPT WROTE THEM: a person keeps the name, age wording,
   nationality, gender noun, build and hair the Current prompt gives them; an object keeps its material,
   shape, colour and size. Compress for scale if you must; never drop one and never swap one for a
   different value.
{_POSITIVE_EXPRESSION_RULE}
- DIALOGUE OVERLAY RULE (CRITICAL): ANY dialogue overlay suffix — "comic book style manga text overlay:..."
  OR "speech bubble with text..." — must be PRESERVED EXACTLY as-is. Only permitted change: a position word
  in narration_box. Speech bubbles: copy 100% unchanged.
{("MULTI-PANEL FORMAT LOCK: the prompt starts with 'N-panel page...' (N is a digit) — this format is SACRED. NEVER rewrite it into a standard single-image prompt. NEVER add a camera framing prefix before the panel count. Only refine the panel descriptions for lighting/framing woven INSIDE each panel description." if manga_page else _format_rule)}
{ANIMAL_SAFETY_RULE}

BEFORE YOU SUBMIT — CINEMATOGRAPHER'S CHECK
Silently ask yourself these questions and fix what they expose. Do not output the questions or any
reasoning.
1. USER BRIEF — does the result genuinely fulfil the user's selected controls, as one coherent creative
   brief rather than a pile of separate effects?
2. SAME MOMENT — is this still exactly the resolved story moment it arrived as?
3. VISUAL PRIORITY — does the composition make the important action or relationship easy to understand?
4. CAMERA & COMPOSITION — do these particular decisions serve THIS moment and THIS brief?
5. SPATIAL READABILITY — are the assigned scale, the spacing and the depth physically clear?
6. LIGHT — does the lighting satisfy the requested intention while staying physically plausible for the
   scene as supplied?
7. ENVIRONMENT — does the environment participate at the requested level without inventing scenery?
8. MOTION / STILLNESS — does the presentation serve the actual action? Do not manufacture motion where a
   deliberate stillness is stronger and truthful.
9. SEQUENCE RHYTHM — does the frame work beside its neighbours? Do not force variation merely to avoid
   repetition.
10. CONTENT & STRUCTURE — did every locked fact and every structural contract of the prompt survive?

keep_original: true only when the framing and lighting already serve this brief and this moment.
issues: specific problems in English, max 8 words each.
Output ONLY a valid JSON object.

Output format:
{{
  "shot": <number>,
  "keep_original": <true|false>,
  "issues": ["<short English, max 8 words>"],
  "reason": "<one-line English, max 10 words — 'OK' if no issues>",
  "prompt": "<the complete prompt in the required format below: framing first, then story content with lighting woven in — {_prompt_field_desc}>",
  "dialogue_position": "<top or bottom — choose based on composition>"
}}"""

        # บล็อก {_r_dist_lock}/{_r_audit_far} อยู่ในสตริงธรรมดาที่ซ้อนอยู่ใน f-string (ก้อน "FRAMING")
        # ตัว {...} ในนั้นจึงไม่ถูกแทนค่าตอนสร้าง ต้อง substitute เองหลัง f-string ประกอบเสร็จ
        director_sys = (director_sys
                        .replace("{_r_dist_lock}", _r_dist_lock)
                        .replace("{_r_audit_far}", _r_audit_far))
        director_sys = _re_clothing.sub(r'\n{3,}', '\n\n', director_sys).strip() + "\n"
        _director_sys_cache[key] = director_sys
        return director_sys


    import re as _re_dir

    def _parse_director(raw_text):
        t = raw_text.strip()
        t = _re_dir.sub(r'```+(?:json)?\s*', '', t).strip()
        try:
            s = t.index('{'); e = t.rindex('}') + 1
            chunk = t[s:e]
        except ValueError:
            return None
        # parse ตรง
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict) and "prompt" in obj:
                return obj
        except Exception:
            pass
        # fix unescaped chars
        res = []; in_s = False; esc = False
        for c in chunk:
            if esc:
                res.append(c); esc = False; continue
            if c == '\\':
                res.append(c); esc = True; continue
            if c == '"':
                in_s = not in_s; res.append(c); continue
            if in_s and c == '\n': res.append('\\n'); continue
            if in_s and c == '\r': continue
            if in_s and c == '\t': res.append('\\t'); continue
            res.append(c)
        try:
            obj = json.loads(''.join(res))
            if isinstance(obj, dict) and "prompt" in obj:
                return obj
        except Exception:
            pass
        # partial recovery — JSON ถูก truncate
        try:
            s2 = t.index('{')
            partial = t[s2:]
            in_s2 = False; esc2 = False; res2 = []
            for c in partial:
                if esc2: res2.append(c); esc2 = False; continue
                if c == '\\': res2.append(c); esc2 = True; continue
                if c == '"': in_s2 = not in_s2; res2.append(c); continue
                if in_s2 and c == '\n': res2.append('\\n'); continue
                if in_s2 and c == '\r': continue
                res2.append(c)
            partial_fixed = ''.join(res2)
            if in_s2: partial_fixed += '"'
            depth_obj = partial_fixed.count('{') - partial_fixed.count('}')
            depth_arr = partial_fixed.count('[') - partial_fixed.count(']')
            if depth_arr > 0: partial_fixed += ']' * depth_arr
            if depth_obj > 0: partial_fixed += '}' * depth_obj
            obj = json.loads(partial_fixed)
            if isinstance(obj, dict) and "prompt" in obj:
                return obj
        except Exception:
            pass
        return None

    prev_prompts: list[dict] = []   # เก็บ camera/lighting context ของ shot ก่อนหน้า

    # ── Grok batch prefetch ──────────────────────────────────────────────────
    # local ไม่แตะเลย — Director เห็นทั้งซีเควนซ์พร้อมกันได้ประโยชน์เรื่อง camera rhythm มากสุด
    # ในสามด่าน แต่ canonical facts (ตัวละคร/anatomy/เสื้อผ้า/action/prop/สถานที่) ยังล็อกเหมือนเดิม
    # เพราะ sys prompt (_director_sys_for) ที่ห้ามแตะของพวกนี้ไม่ได้เปลี่ยนเลย
    _grok_director_prefetch: dict[int, dict] = {}
    if llm_wf_file == GROK_MARKER:
        _batch_targets: list[tuple[int, str, dict]] = []
        for _bi, _bs in enumerate(shots_data):
            _b_shot_num = _bi + 1
            if _target_idxs is not None and _bi not in _target_idxs:
                continue
            if not _bs.get("prompt", "").strip():
                continue
            _b_scale = (_bs.get("_dir_scale_category") or "").strip() or "medium"
            # scale เดียวสำหรับทุกช็อตเหมือนฝั่ง Fill/Checker — scale_category (ladder category ของ
            # Director เอง ไม่ใช่ legacy shot_scale story-field ที่ถูกลบไปแล้ว) จริงอยู่ใน payload
            _b_sys = _director_sys_for("medium")
            if _dir_all_blocks:
                _b_sys += f"\n\nSTORY SOURCE (exact A1 blocks, verbatim — for judging the TRUE story moment, never a reason to invent a new one):\n{_dir_all_blocks}\n"
            # DIRECTOR-FORMAT-AUTHORITY-PRODUCTION-A1 (Grok parity): เดิม batch payload ไม่มีฟิลด์
            # incoming format ต่อช็อตเลย ต่างจาก local ที่ได้ "_keep_fmt" ต่อช็อตเสมอ — ตอนนี้ใช้
            # authority เดียวกัน (shot.prompt_style ก่อน ไม่งั้น detect จาก Current prompt) แค่ระบุ
            # ชื่อ format แบบกระชับ ส่วนสัญญาโครงสร้าง V3 ที่ใช้ร่วมกันมาจาก _b_sys (_format_lock)
            # อยู่แล้วซึ่งครอบทุกช็อตในแบตช์เดียวกัน ไม่ต้องพ่วงก้อนสัญญาซ้ำต่อช็อต
            _b_in_fmt = (_bs.get("prompt_style") or "").strip() or _detect_prompt_format(_bs.get("prompt", ""))
            _b_payload = {
                "shot": _b_shot_num,
                "shot_desc": _bs.get("shot_desc", ""),
                # NORMAL-VNEXT-DIRECTOR-CLEANUP-A1: wire key renamed shot_scale → scale_category —
                # this is Director's own private ladder category (_dir_scale_category), never the
                # removed legacy "shot_scale" per-shot story field; the old wire name risked exactly
                # that confusion even though the Python variable already avoided it.
                "scale_category": _b_scale,
                "incoming_format": _b_in_fmt,
                "current_prompt": _bs.get("prompt", ""),
            }
            _b_payload = {k: v for k, v in _b_payload.items() if v not in ("", None)}
            _batch_targets.append((_b_shot_num, _b_sys, _b_payload))
        if on_log:
            _dir_all_parents = _a1_all_parent_ids(a1)
            await on_log(
                f"DIRECTOR backend=grok mode=batch shots={len(_batch_targets)} "
                f"parents={','.join(_dir_all_parents) if _dir_all_parents else 'NONE'}")
        _grok_director_prefetch = await _prefetch_grok_batch(
            "Director", _batch_targets, _grok_batch_budget(_llm_tokens_director_grok_batch, len(_batch_targets)),
            grok_api_key, grok_model, on_log=on_log, stop_event=stop_event, one_call=True,
            strict_no_recovery=True)

    for i, shot in enumerate(shots_data):
        if stop_event and stop_event.is_set():
            if on_log: await on_log("⏹ หยุด Director"); return

        shot_num = i + 1

        # single shot mode: shot อื่นเป็นแค่ context ไม่ต้อง progress
        if _target_idxs is not None and i not in _target_idxs:
            prev_prompts.append(shot)
            continue

        if on_progress: on_progress(shot_num, num)

        if not shot.get("prompt", "").strip():
            if on_log: await on_log(f"⏭ Shot {shot_num} ไม่มี prompt — ข้าม")
            prev_prompts.append(shot)
            await on_shot(i, shot)
            continue

        # ── previous shots context (full prompt for flow, since there are no section labels) ──
        cam_ctx = ""
        if prev_prompts:
            cam_ctx = "=== PREVIOUS SHOTS (for visual flow) ===\n"
            total_prev = len(prev_prompts)
            for j, ps in enumerate(prev_prompts):
                p = ps.get("prompt", "").replace("\\n", "\n").strip()
                scene = ps.get("shot_desc", "")  # เต็ม ไม่ตัด
                is_recent = (j >= total_prev - 2)
                if is_recent:
                    cam_ctx += (
                        f"Shot {j+1}: {scene}\n"
                        f"Framing/lighting used: {_framing_digest(p)}\n\n"
                    )
                else:
                    cam_ctx += f"Shot {j+1}: {scene}\n"
            cam_ctx += "\n"

        # upcoming shots
        upcoming_ctx = ""
        upcoming = shots_data[i + 1: i + 3]
        if upcoming:
            upcoming_ctx = "=== UPCOMING SHOTS (narrative reference) ===\n"
            for k, nxt in enumerate(upcoming):
                upcoming_ctx += f"Shot {shot_num+k+1}: {nxt.get('shot_desc','')}\n"
            upcoming_ctx += "\n"

        _scale = (shot.get("_dir_scale_category") or "").strip() or "medium"
        _in_fmt = (shot.get("prompt_style") or "").strip() or _detect_prompt_format(shot.get("prompt", ""))
        if _in_fmt == "manga_page" or manga_page:
            _keep_fmt = (
                "INCOMING FORMAT: multi-panel page. Keep 'N-panel page...' as the first words. "
                "Do not rewrite into a single-image paragraph or labeled sections."
            )
        elif _dir_v3:
            # DIRECTOR-FORMAT-AUTHORITY-PRODUCTION-A1: the legacy/general _build_prompt_format_rule()
            # below is the pre-V3 contract — it doesn't know LOCATION LOCK exists at all, and for
            # sectioned_detailed it names a different section list (Outfit, no Location Lock) than
            # Minimal V3's own. Feeding it to a V3 Director call is what caused the proven format
            # drift (DIRECTOR-FORMAT-DRIFT-FIRST-LOSS-A1). For V3, reuse the SAME structural
            # authority Fill itself used to write this prompt — framed as preservation, not a
            # fresh-generation instruction, so Director edits inside the existing skeleton only.
            _keep_fmt = (
                f"INCOMING FORMAT: {_in_fmt} (Minimal V3). PRESERVE the exact serializer family and "
                "block skeleton already present in Current prompt — this is preservation, not "
                "regeneration. Do not rebuild the prompt from scratch and do not convert it into a "
                "different format. Edit camera/lighting/composition/detail level inside that same "
                "skeleton only. The structural contract this format follows (the skeleton to keep, "
                "not new content to author):\n" + _minimal_v3_format(_in_fmt, manga_page=False)
            )
        else:
            _keep_body, _ = _build_prompt_format_rule(_in_fmt)
            _keep_fmt = f"INCOMING FORMAT: {_in_fmt}. KEEP this format — do not convert.\n{_keep_body}"
        if on_log:
            await on_log(f"🎬 Shot {shot_num} incoming format: {_in_fmt}")
        # ground truth ที่ director ยังไม่เคยเห็น — ต้องมีก่อนจะยอมให้เขียนเนื้อหาใหม่ตาม scale ได้อย่างปลอดภัย
        # (ใช้ prev_prompts ตัวเดียวกับที่ cam_ctx ใช้ข้างบน แบบเดียวกับ completed_prompts ใน run_check_prompts)
        _prev_cs = prev_prompts[-1].get("clothing", "").strip() if prev_prompts else ""

        # AGENT-STORY-TRUTH-ROUTING-A1: Local per-shot call gets ONLY this shot's resolved parent
        # E[n] block(s) (from "uses"), never a guess — parents=NONE + this shot/context only if
        # unresolvable.
        _dir_parent_ids, _dir_parent_blocks = ([], "") if llm_wf_file == GROK_MARKER else _resolve_shot_parent_blocks(shot, a1)
        _dir_parent_ctx = (
            f"=== STORY SOURCE (exact, this shot's grounding) ===\n{_dir_parent_blocks}\n\n"
            if _dir_parent_blocks else ""
        )
        if on_log and llm_wf_file != GROK_MARKER:
            await on_log(
                f"DIRECTOR backend=local mode=single shot={shot_num} "
                f"parents={','.join(_dir_parent_ids) if _dir_parent_ids else 'NONE'}")

        # สร้าง user prompt ใหม่ได้ตามบริบทรอบข้างที่ยังส่งอยู่ (ใช้ตอน retry ลดบริบท)
        def _director_user_p(_cam: str, _upc: str) -> str:
            return (
                f"{_dir_parent_ctx}"
                f"{_cam}"
                f"{_upc}"
                f"=== SHOT {shot_num} TO REVIEW ===\n"
                # ไม่ส่ง shot_desc / clothing / ชุดของช็อตก่อน อีกต่อไป —
                # Current prompt คือความจริงของเนื้อหา ส่งฟิลด์พวกนี้ไปเท่ากับให้มันเอามาทับสิ่งที่
                # user แก้ไว้เอง เหลือเฉพาะข้อความ (ต้องคัดลอกตรงตัว)
                # และเรขาคณิตกล้องที่ Director มีสิทธิ์บังคับจริง
                f"narration: {shot.get('narration','')}\n"
                f"speech_char: {shot.get('speech_char','')}\n"
                f"subtitle: {shot.get('subtitle','')}\n"
                f"speaking_state: {'ACTIVE — preserve the visible speaking state already in Current prompt' if (shot.get('speech_char','') or '').strip() and (shot.get('subtitle','') or '').strip() else 'NONE'}\n"
                + ("" if manga_page else f"SHOT SCALE (assigned, fixed): {_scale if _dir_v3 else _scale_with_hint(_scale, _shot_has_subject(shot.get('expression','')))}\nChoose the angle and composition freely; the camera distance is not yours to change.\n")
                + f"{_keep_fmt}\n"
                + "\n"
                f"Current prompt (THE CONTENT AUTHORITY — every name, appearance, garment, prop, action, "
                f"spatial relationship and place in it is locked and may be the user's own edit; only the "
                f"assigned SHOT SCALE above outranks it, and only on camera geometry):\n{shot.get('prompt','')}\n\n"
                f"Review the framing, lighting, narration box placement, and the detail level of every section against "
                f"the assigned SHOT SCALE, then rewrite the full prompt IN THE SAME FORMAT as Current prompt. "
                f"Preserve every fact the Current prompt states, exactly as it states it; change only how closely "
                f"those facts are described. "
                f"Flow from/to surrounding shots. Output the JSON."
            )
        user_p = _director_user_p(cam_ctx, upcoming_ctx)

        # เดิมยิงครั้งเดียวแล้วยอมแพ้ = ช็อตนั้นไม่ได้ผ่าน Director เลย (เจอกับ local LLM ที่ context
        # ไม่พอแล้วคืนค่าว่าง) ตอนนี้ลองใหม่โดยลดบริบทรอบข้างลงทีละขั้น เหมือน ladder ของ fill:
        # ช็อตก่อนหน้า + ช็อตถัดไป → ช็อตก่อนหน้าอย่างเดียว → ไม่มีบริบทรอบข้างเลย
        result_data = None
        # Grok: คำขอเดียวของ stage คือความพยายามทั้งหมด เหมือน Fill/Checker — ladder ลด context
        # ทีละขั้นด้านบนออกแบบมาสำหรับ local LLM ที่ context จำกัด ไม่มีผลกับ Grok เลย ยิงเดี่ยวสำรอง
        # ครั้งเดียวพอ
        _max_dir_attempts = 1 if llm_wf_file == GROK_MARKER else 3
        for _d_att in range(1, _max_dir_attempts + 1):
            if stop_event and stop_event.is_set():
                if on_log: await on_log("⏹ หยุด Director")
                return
            _u_now = (user_p if _d_att == 1 else
                      _director_user_p(cam_ctx if _d_att == 2 else "", ""))
            if on_log:
                await on_log(
                    f"🎥 Shot {shot_num}/{num} — scale {_scale or 'as written'} · "
                    f"comp {composition_pass} · cam {camera_energy} · light {lighting_pass or 'enhance'} · "
                    f"env {environment_pass} · motion {motion_pass}"
                    + (f" · retry {_d_att} (ลดบริบทรอบข้าง)" if _d_att > 1 else ""))
            if _d_att == 1 and shot_num in _grok_director_prefetch:
                _b_item = _grok_director_prefetch.pop(shot_num)
                raw = json.dumps(_b_item, ensure_ascii=False)
                if on_log: await on_log(f"⚡ shot {shot_num}: ใช้ผลจาก Grok batch")
            elif llm_wf_file == GROK_MARKER:
                raw = None
                if on_log:
                    await on_log(f"⚠ shot {shot_num}: ไม่มีผลจาก Grok batch — ใช้ prompt เดิม (one-call mode)")
            else:
                raw = await _call_llm_generic(
                    llm_wf_file, wf_template,
                    _director_sys_for(_scale),
                    _u_now, loop, max_tokens=_llm_tokens_fill,
                    on_log=on_log if _d_att == 1 else None,
                    grok_api_key=grok_api_key, grok_model=grok_model,
                )
            if on_log and raw and _d_att == 1:
                await on_log(f"RAW director shot {shot_num}:\n{raw}")
            result_data = _parse_director(raw) if raw else None
            if result_data:
                break
            if on_log:
                await on_log(f"⚠ Director shot {shot_num} attempt {_d_att} ไม่ได้ผลลัพธ์")

        if not result_data:
            if on_log: await on_log(f"⚠ shot {shot_num} Director ล้มเหลว — ใช้ prompt เดิม")
            shot["_dir_status"] = "error"
            shot["_dir_issues"] = []
            shot["_dir_reason"] = "ตรวจสอบไม่สำเร็จ"
            prev_prompts.append(shot)
            await on_shot(i, shot)
            continue

        updated          = dict(shot)
        # ตัวตรวจ/ผู้กำกับส่ง prompt กลับมาเป็น array ของประโยคได้เหมือนฝั่ง outline/fill — แปลงก่อน .strip()
        new_prompt       = _coerce_text_field(result_data.get("prompt", ""), sep=" ").strip()
        old_prompt       = _coerce_text_field(shot.get("prompt", ""), sep=" ").strip()
        keep             = result_data.get("keep_original", True)
        issues           = result_data.get("issues", [])
        if isinstance(issues, str):
            issues = [issues] if issues.strip() else []
        elif not isinstance(issues, list):
            issues = []
        reason           = _coerce_text_field(result_data.get("reason", ""), sep="; ")

        if not keep and not new_prompt:
            new_prompt = old_prompt

        _use = new_prompt if new_prompt else old_prompt

        # DIRECTOR-FORMAT-AUTHORITY-PRODUCTION-A1: structural-only reject guard. Instruction repair
        # alone (the V3-aware _keep_fmt above) is not sufficient — the diagnostic round proved RAW
        # Director can still drift format even against an explicit, correct instruction. Reject a
        # structurally-broken candidate and fall back to the old (pre-Director) prompt rather than
        # ever reconstructing/reformatting it in Python.
        # FILL-VNEXT-LOCK-CONTRACT-A1: this used to gate on `_use != old_prompt`, so a
        # keep_original result (_use IS old_prompt) skipped the check entirely — an already-broken
        # incoming prompt (e.g. corrupted upstream by Checker) would pass through Director silently
        # whenever Director itself made no further change. Validate _use unconditionally instead;
        # when _use == old_prompt this only detects and reports a pre-existing defect (there is no
        # better candidate to fall back to), it never introduces a new drift.
        if _dir_v3 and not manga_page:
            _fmt_ok, _fmt_reason = _director_format_structure_ok(old_prompt, _use, _in_fmt, minimal_v3=True)
            if on_log:
                _dir_sig_in = _vnext_prompt_structure_signature(old_prompt)
                _dir_sig_out = _vnext_prompt_structure_signature(_use)
                await on_log(
                    f"DIRECTOR INPUT: shot={shot_num} _fill_engine={shot.get('_fill_engine','')} "
                    f"prompt_style={_in_fmt} family={_dir_sig_in['format_family']} "
                    f"ID={_dir_sig_in['identity_lock_count']} LOC={_dir_sig_in['location_lock_count']} "
                    f"OBJ={_dir_sig_in['object_lock_count']} order={_dir_sig_in['lock_order']}")
                await on_log(
                    f"DIRECTOR OUTPUT: shot={shot_num} family={_dir_sig_out['format_family']} "
                    f"ID={_dir_sig_out['identity_lock_count']} LOC={_dir_sig_out['location_lock_count']} "
                    f"OBJ={_dir_sig_out['object_lock_count']} order={_dir_sig_out['lock_order']} "
                    f"structure_accept={'YES' if _fmt_ok else 'NO'} "
                    f"reason={_fmt_reason if not _fmt_ok else ''}")
            if not _fmt_ok:
                if on_log:
                    await on_log(f"🚫 DIRECTOR FORMAT REJECT: shot={shot_num} expected={_in_fmt} "
                                 f"reason={_fmt_reason}" + (" (already broken before Director)" if _use == old_prompt else ""))
                _use = old_prompt
                new_prompt = old_prompt
                issues = list(issues) + ["Director format reject — kept original"]
                reason = reason or "format reject"

        # PROFESSIONAL-AUTONOMY-CLEANUP-A2: the deterministic single-image "did this creative pass
        # leave a keyword trace" check + forced repair call (director_pass_consequences() /
        # director_consequence_fix_request()) has been REMOVED. It rejected professionally valid
        # Director output whenever it didn't contain a Python-chosen keyword list for the selected
        # control — Python judging cinematographic quality, which this cleanup removes. The
        # single-image Director's creative choices are no longer second-guessed by a keyword scan;
        # only structural/format guards (above) and manga's own separate consequence checker
        # (below, out of scope for this cleanup) still run.

        # V3-PROMPT-BLOCK-SPACING-NORMALIZATION-A1: presentation-only whitespace pass, applied only
        # AFTER the structural guard above has already accepted (or reverted) _use — never a
        # substitute for that guard, and a no-op on anything not an unambiguously valid V3
        # narrative skeleton (manga, tag_style, sectioned_detailed, and any malformed candidate all
        # pass through unchanged).
        if _dir_v3 and not manga_page and _use:
            _use = _normalize_v3_prompt_block_spacing(_use)
            new_prompt = _use

        # MANGA: deterministic PAGE+PANEL consequence check, ไม่ใช้ตัวตรวจ single-image ตรงๆ (DIRECTOR-B2)
        if manga_page and _use:
            _manga_cons = director_manga_pass_consequences(
                _use, shot_scale_override=shot_scale_override, composition=composition_pass,
                camera_energy=camera_energy, lighting=lighting_pass or "enhance",
                environment=environment_pass, motion=motion_pass, baseline_prompt=old_prompt,
            )
            # status "weak"/"fail" ไม่มีทางเกิดกับค่าที่เป็น intentional no-op อยู่แล้ว (ดีเทกเตอร์คืน
            # "pass"/"not_applicable" ให้ตรงๆ) — กรองแค่ status ก็พอ ไม่ต้องเช็ค _MANGA_INTENTIONAL_NOOP ซ้ำ
            _weak = {
                k: v for k, v in _manga_cons.items()
                if not k.startswith("_") and v["status"] in ("weak", "fail")
            }
            if on_log:
                _summary = ", ".join(f"{k}={v['status']}" for k, v in _manga_cons.items() if not k.startswith("_"))
                await on_log(f"🎏 shot {shot_num}: manga consequence — {_summary}"
                             + (f" | gimmick: {', '.join(_manga_cons['_gimmick'])}" if _manga_cons.get("_gimmick") else "")
                             + (f" | light-source risk: {', '.join(_manga_cons['_light_source_risk'])}" if _manga_cons.get("_light_source_risk") else ""))
            if _weak and llm_wf_file == GROK_MARKER:
                # โจทย์ B2 ห้ามยิง repair แบบต่อหน้า/ต่อช็อตให้ Grok (ขัดกับ one-call batch intent) —
                # ตรวจแล้วรายงานอย่างเดียว ผลชุดแรกใช้ต่อ ปัญหา routing แบบ batch repair เป็นงานรอบหน้า
                if on_log:
                    await on_log(f"⚠ shot {shot_num}: manga weak controls — {', '.join(_weak)} "
                                 f"— Grok batch repair deferred (no per-shot Grok repair call)")
            elif _weak:
                if on_log:
                    await on_log(f"🔧 shot {shot_num}: manga weak controls — {', '.join(_weak)} — ขอรอบซ่อมเฉพาะจุด (ครั้งเดียว)")
                _mfix_raw = await _call_llm_generic(
                    llm_wf_file, wf_template,
                    _director_sys_for(_scale),
                    director_manga_consequence_fix_request(_weak, _use), loop,
                    max_tokens=_llm_tokens_fill, on_log=None,
                    grok_api_key=grok_api_key, grok_model=grok_model,
                )
                _mfix_data = _parse_director(_mfix_raw) if _mfix_raw else None
                _mfix_p = _coerce_text_field(_mfix_data.get("prompt", ""), sep=" ").strip() if _mfix_data else ""
                # โครงสร้างต้องรอด: ขึ้นต้นแบบ N-panel เดิม และแยกได้จำนวนแผงเท่าเดิม ไม่งั้นทิ้งผลซ่อม
                _orig_panels = _manga_cons.get("_panels_parsed", 0)
                _mfix_panels = len(_manga_panel_texts(_mfix_p)) if _mfix_p else 0
                _struct_ok = (
                    _mfix_p and len(_mfix_p) > 60
                    and _re_clothing.match(r'^\s*\d+[\s-]*panel', _mfix_p, _re_clothing.I)
                    and (_orig_panels == 0 or _mfix_panels == _orig_panels)
                )
                if _struct_ok:
                    _mstill = director_manga_pass_consequences(
                        _mfix_p, shot_scale_override=shot_scale_override, composition=composition_pass,
                        camera_energy=camera_energy, lighting=lighting_pass or "enhance",
                        environment=environment_pass, motion=motion_pass, baseline_prompt=old_prompt,
                    )
                    _still_weak = {k: v for k, v in _mstill.items()
                                   if not k.startswith("_") and v["status"] in ("weak", "fail")}
                    if len(_still_weak) < len(_weak):
                        _use = _mfix_p
                        new_prompt = _mfix_p
                        if on_log:
                            await on_log(f"✅ shot {shot_num}: manga repair ได้ผล — เหลือ weak: "
                                         f"{', '.join(_still_weak) if _still_weak else 'ไม่มี'}")
                    elif on_log:
                        await on_log(f"⚠ shot {shot_num}: manga repair ไม่ช่วย — ใช้ผลรอบแรก")
                elif on_log:
                    await on_log(f"⚠ shot {shot_num}: manga repair ทำโครงสร้างพัง (panel count/รูปแบบเปลี่ยน) — ทิ้งผลซ่อม ใช้ผลรอบแรก")

        # SEMANTIC-PASSTHROUGH-E2E-A1.1: removed the legacy-engine calls to _enforce_director_scale()
        # (regex-rewrote the framing sentence when the assigned scale word was missing) and
        # _enforce_canonical_spacing()/_repair_prompt_spacing() (deleted "together/close" wording and
        # spliced in a new invented spatial clause). Both were already inactive under the default
        # Minimal V3 engine (_dir_v3 gate); they are now removed from the legacy engine too, so no
        # selectable production path rewrites Director's prompt text. Director's own LLM contract
        # owns scale/spacing under either engine — report only, matching the V3 branch's own pattern.
        if not manga_page and _scale and on_log:
            if _scale.lower() not in (_use or "").lower():
                await on_log(f"📏 shot {shot_num}: prompt ไม่ได้เขียนระยะ '{_scale}' ไว้ "
                             f"(ไม่ได้แก้ให้ — Director เป็นเจ้าของข้อความ)")
        # SEMANTIC-PASSTHROUGH-E2E-A1: removed the active, unconditional calls to
        # visual_sanity_pass() (regex-rewrote composition/light/speech/meta wording based on
        # deciding what those words "really" meant) and consolidate_instances() (deleted/collapsed
        # a second character introduction or a "looks like X" clone phrase based on deciding it was
        # a duplicate reference to the same person). Both ran on every Director shot regardless of
        # Fill Engine setting — unlike _enforce_director_scale/_enforce_canonical_spacing just above,
        # which are already gated off by the Minimal V3 default. Director's own LLM contract already
        # forbids inventing new story facts; if these classes of artifact recur live, the fix belongs
        # in that contract, not a Python rewrite pass after it.
        if on_log:
            for _v in visible_instance_audit(
                    _use, shot.get("shot_desc", ""), character_desc, ""):
                await on_log(f"👥 shot {shot_num}: {_v}")
        updated["prompt"]      = _unescape_literal_nl(_use)
        updated["prompt_style"] = _in_fmt if _in_fmt != "manga_page" else (shot.get("prompt_style") or "narrative")
        # เตือนเฉยๆ ถ้ายาวเกิน budget ไม่ตัดอัตโนมัติ เพราะจะตัดกลางประโยคแล้วพัง — เตือนใกล้เพดานจริง (~110)
        # แทนที่จะรอถึง 140 เพื่อให้เห็นชัดว่ากฎ LENGTH BUDGET มีผลจริงแค่ไหน
        _wc = len(updated["prompt"].split())
        if on_log and _wc > 120:
            await on_log(f"⚠ shot {shot_num}: prompt ยาว {_wc} คำ (เกินเพดาน ~110)")
        updated["_dir_status"] = "ok" if keep else "edited"
        updated["_dir_issues"] = [iss[:200] for iss in issues if isinstance(iss, str) and not iss.startswith("Camera:") and not iss.startswith("Subject:")]
        updated["_dir_reason"] = reason[:200] if reason else ""

        # extract dialogue_position จาก director result
        # อัปเดต narration/speech จาก director
        # director ห้ามเปลี่ยน text เด็ดขาด — รับแค่ dialogue_position
        dlg_pos = result_data.get("dialogue_position", "").strip().lower()
        if dlg_pos in ("top", "bottom"):
            updated["dialogue_position"] = dlg_pos

        if on_log:
            if keep:
                await on_log(f"✅ Shot {shot_num}: กล้อง/แสงเหมาะสมแล้ว")
            else:
                issue_lines = "\n".join(f"  • {iss}" for iss in updated["_dir_issues"]) if updated["_dir_issues"] else f"  • {reason}"
                changed = new_prompt != old_prompt
                await on_log(
                    f"🎬 Shot {shot_num}: แก้ไข {'(prompt เปลี่ยนแล้ว ✓)' if changed else '(เหมือนเดิม ⚠)'}\n"
                    f"{issue_lines}"
                )

        prev_prompts.append(updated)
        await on_shot(i, updated)

    if on_log:
        await on_log(_sys_metrics_line("Director", _variant, _variant_why,
                                       _director_sys_for("medium")))
        await on_log(f"✅ Director เสร็จ {num} shots")

def _shot_summary_line(i: int, s: dict) -> str:
    """One compact line summarising a shot for insert context.

    NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §5: shots_data is native-shaped (shot_desc/subtitle/clothing)
    since boundaries 1-4 — native-first, legacy-fallback so an old pre-migration loaded project
    (still scene_desc/speech_text/clothing_state on disk, never upgraded in place) still shows
    something here instead of going silently blank."""
    desc = s.get("shot_desc") or s.get("scene_desc", "")
    narration = s.get("narration", "").strip()
    speech_char = s.get("speech_char", "").strip()
    speech_text = (s.get("subtitle") or s.get("speech_text", "")).strip()
    clothing = (s.get("clothing") or s.get("clothing_state", "")).strip()

    parts = [f"Shot {i+1}: {desc}"]
    if narration:
        parts.append(f"  [narration] {narration}")
    if speech_char and speech_text:
        parts.append(f"  [{speech_char}] \"{speech_text}\"")
    elif speech_text:
        parts.append(f"  [speech] \"{speech_text}\"")
    if clothing:
        parts.append(f"  [clothing] {clothing}")
    return "\n".join(parts)

def _shot_detail_block(num: int, s: dict) -> str:
    """Full detail block for prev/next shot.

    NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §5: native-first, legacy-fallback reads — see
    _shot_summary_line's docstring above for why."""
    lines = [f"Shot {num}:"]
    lines.append(f"  scene: {s.get('shot_desc') or s.get('scene_desc', '')}")
    _people = str(s.get("visible_people") or "").strip()
    _objects = str(s.get("objects") or s.get("prop_state") or "").strip()
    if _people:     lines.append(f"  visible people: {_people}")
    if _objects:    lines.append(f"  objects: {_objects}")
    narration = s.get("narration", "").strip()
    speech_char = s.get("speech_char", "").strip()
    speech_text = (s.get("subtitle") or s.get("speech_text", "")).strip()
    clothing = (s.get("clothing") or s.get("clothing_state", "")).strip()
    prompt = s.get("prompt", "").strip()
    if narration:   lines.append(f"  narration: {narration}")
    if speech_char and speech_text:
        lines.append(f"  speech ({speech_char}): \"{speech_text}\"")
    elif speech_text:
        lines.append(f"  speech: \"{speech_text}\"")
    if clothing:    lines.append(f"  clothing state: {clothing}")
    if prompt:      lines.append(f"  prompt (use environment/lighting/setting from this):\n    {prompt}")
    return "\n".join(lines)

def _build_insert_context(shots_data: list[dict], insert_after: int) -> tuple[str, str, str, str]:
    """Return (all_shots_block, prev_block, next_block, position_hint)"""
    all_shots_block = "\n".join(
        _shot_summary_line(i, s) for i, s in enumerate(shots_data)
    )

    if insert_after == 0:
        position_hint = "This new shot will be inserted BEFORE the current Shot 1."
        prev_block = "None (new opening shot)"
        next_block = _shot_detail_block(1, shots_data[0]) if shots_data else "None"
    elif insert_after >= len(shots_data):
        position_hint = f"This new shot will be appended AFTER Shot {len(shots_data)} (new ending)."
        prev_block = _shot_detail_block(len(shots_data), shots_data[-1]) if shots_data else "None"
        next_block = "None (new last shot)"
    else:
        position_hint = f"This new shot will be inserted BETWEEN Shot {insert_after} and Shot {insert_after+1}."
        prev_block = _shot_detail_block(insert_after, shots_data[insert_after - 1])
        next_block = _shot_detail_block(insert_after + 1, shots_data[insert_after])

    return all_shots_block, prev_block, next_block, position_hint

async def run_insert_shot(
    instruction: str,
    insert_after: int,
    shots_data: list[dict],
    story_text: str,
    char_desc: str,
    scene_desc: str,
    story_summary: str,
    llm_wf_file: str,
    dialogue_style: str = "speech_bubble",
    dialogue_lang: str = "en",
    genre: str = "",
    clarification_answers: dict | None = None,
    on_log=None,
    props_desc: str = "",
    desc_lang: str = "",
    source_lang: str = "",
    narration_type: str = "caption",
) -> dict:
    """
    Phase 1: returns {"needs_clarification": True, "questions": [...]}
             or      {"needs_clarification": False, "shot": {...}}
    Phase 2: pass clarification_answers → always returns shot dict directly
    """
    llm_wf_path = LLM_DIR / f"{llm_wf_file}.json"
    if not llm_wf_path.exists():
        if on_log: await on_log(f"ไม่พบ {llm_wf_path}")
        return {"error": "workflow not found"}
    try:
        wf_template = json.loads(llm_wf_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}

    loop = asyncio.get_event_loop()
    genre_str   = _build_genre_str(genre)
    _nsfw       = is_nsfw(genre)
    char_str    = f"Character: {char_desc}" if char_desc else ""
    props_str   = f"Props (LOCKED canonical descriptions — use verbatim every time the object appears): {props_desc}" if props_desc else ""
    scene_str   = f"Scene setting: {scene_desc}" if scene_desc else ""
    summary_str = f"Story: {story_text}" if story_text else ""
    # scene_desc ใช้ภาษาคำบรรยาย ส่วน narration/speech ใช้ภาษาบทพูด (แยกกันได้ตั้งแต่มี desc_lang)
    _lang_label = lang_prompt_name(dialogue_lang)
    _desc_label = lang_prompt_name(desc_lang or dialogue_lang)
    _src_note = _source_lang_note(source_lang, desc_lang or dialogue_lang)

    all_shots_block, prev_block, next_block, position_hint = _build_insert_context(shots_data, insert_after)

    # Dialogue authoring: the SAME rules and limits B Normal uses (constants.dialogue_authoring_text),
    # so an inserted shot's narration / speech_char / subtitle follow the current Dialogue Style.
    _dialogue_rule = _dialogue_authoring_text(dialogue_style, dialogue_lang, narration_type)
    _text_empty = "empty string when this shot has none"

    # Insert Shot writes B TRUTH ONLY, in the current B Normal shape: visible_people / shot_desc
    # (eight blocks) / story / objects / narration / speech_char / subtitle. No location / clothing /
    # camera / lighting fields — continuity facts live inside the eight blocks, and Fill owns HOW.
    shot_schema = f"""{{
  "visible_people": "visible cast, comma-separated canonical names only — a fact anchor, not a description",
  "shot_desc": "exactly eight blocks in this order, written in {_desc_label} (the block labels stay in English): (Beat: ...) (Emotion: ...) (Body: ...) (Appearance: ...) (Setting: ...) (Interaction: ...) (State: ...) (Light: ...)",
  "story": "one concise poetic narrative passage in {_desc_label} fusing the eight blocks — every person, action, state, object, clothing detail and setting detail must already be in them; no new facts",
  "objects": "present object(s) and essential current state only — a fact anchor; {_text_empty}",
  "narration": "per the dialogue rule; {_text_empty}",
  "speech_char": "per the dialogue rule; {_text_empty}",
  "subtitle": "per the dialogue rule; {_text_empty}"
}}"""

    _shot_desc_contract = """=== SHOT_DESC CONTRACT ===
shot_desc uses exactly these eight blocks, in this order:
(Beat: ...) (Emotion: ...) (Body: ...) (Appearance: ...) (Setting: ...) (Interaction: ...) (State: ...) (Light: ...)
Beat = current visible event/action. Emotion = perception, gaze, expression, and emotional reaction supported by the story. Body = pose, direction, spacing, limb placement, blocking, and physical contact. Appearance = current clothing and visible appearance. Setting = current physical environment. Interaction = interaction with people, objects, surfaces, or environment. State = persistent current visual/physical condition inherited from earlier events. Light = established or physically plausible visible light source.
No camera, framing, lens, angle, shot scale, or composition anywhere in shot_desc or story — another stage decides HOW the image is photographed.
story is the imaginative seed for that stage: one concise poetic passage built ONLY from the eight blocks (poetic wording is allowed, new facts are not)."""

    if clarification_answers:
        answers_block = "\n".join(f"- {q}: {a}" for q, a in clarification_answers.items())
        clarify_section = f"\nUser has answered the clarifying questions:\n{answers_block}\n"
        clarify_instruction = "Now generate the shot using these answers. Output ONLY the shot JSON, no other text."
    else:
        clarify_section = ""
        clarify_instruction = ""

    sys_p = f"""You are a story shot outline writer. You are INSERTING a new shot into an existing story sequence.
{_src_note}{genre_str}
{char_str}
{props_str}
{scene_str}
{summary_str}

=== FULL STORY SEQUENCE (for continuity) ===
{all_shots_block}

=== INSERTION POINT ===
{position_hint}
Previous shot: {prev_block}
Next shot: {next_block}

=== CONTINUITY RULES — MANDATORY ===
- {"Clothing/undress state must be CONSISTENT with surrounding shots — MID-ACT RULE: no magical re-dress within the same continuous undress/sex act. A SCENE BREAK IS NOT A CAUSE: a time skip, a new place or a new beat may come with a clothing change, but never causes one by itself — the change has to be something the story physically does, shown in shot_desc." if _nsfw else "Outfit must be CONSISTENT with surrounding shots — it may change either direction for ordinary story reasons (weather, disguise, damage, costume change), just match what the previous/next shots establish."} Never skip steps.
- ENVIRONMENT (CRITICAL): extract the exact location, setting, props, lighting, and atmosphere from the previous and next shot prompts above. The new shot MUST use the same environment — same room/place/surface/light source/color palette. Do NOT invent a new location.
- Characters, setting, mood must flow naturally between previous shot and next shot.
- Do NOT introduce new characters not already in the story.
- Do NOT invent a new location or a continuity jump unless the user's instruction explicitly requires one.
- Infer the inserted shot's CURRENT clothing, physical state, location, objects, character positions/contact and light/environment from the previous and next shots, and write those facts INTO the eight shot_desc blocks (Appearance, Setting, Body, State, Interaction, Light) — there are no separate clothing/location/lighting fields.
- The new shot must fit seamlessly — a reader should not notice it was added later.

{_shot_desc_contract}

{_dialogue_rule}
{clarify_section}
{"" if clarification_answers else """=== CLARIFICATION ===
If the user's instruction is ambiguous about important continuity truth (which character, the action or contact, clothing/state, object involvement, position in continuity), you MAY ask up to 3 clarifying questions with 3-4 options each.
Never ask about camera, framing or composition — another stage decides HOW the image is photographed.
If the instruction is clear enough, skip clarification and generate the shot directly.

Output ONE of these two JSON formats:

FORMAT A — ask for clarification:
{
  "needs_clarification": true,
  "questions": [
    {"id": "q1", "question": "question text", "options": ["option1", "option2", "option3"]},
    {"id": "q2", "question": "question text", "options": ["option1", "option2", "option3"]}
  ]
}

FORMAT B — generate shot directly:
{
  "needs_clarification": false,
  "shot": """ + shot_schema + """
}

Output ONLY valid JSON, no other text."""}
{clarify_instruction if clarification_answers else ""}"""

    if clarification_answers:
        user_p = f"Generate the new shot. Original instruction: {instruction}"
    else:
        user_p = f"Insert a new shot. User instruction: {instruction}"

    raw = await _call_llm_node(wf_template, sys_p, user_p, loop,
                                uuid.uuid4().hex, max_tokens=_llm_tokens_fill, on_log=on_log)
    if not raw:
        return {"error": "LLM no response"}

    import re as _re
    m = _re.search(r'\{[\s\S]*\}', raw)
    if not m:
        return {"error": "parse failed"}
    try:
        obj = json.loads(m.group())
    except Exception:
        return {"error": "JSON parse failed"}

    if clarification_answers:
        # phase 2: obj is the shot directly (or wrapped)
        shot = obj.get("shot", obj)
        return {"needs_clarification": False, "shot": shot}

    return obj

# ── Session chat (เหมือน prompt_tester) ─────────────────────────────
_grok_chat_histories: dict[str, list] = {}   # session_id -> [{"role":"user"/"assistant","content":...}, ...] เก็บ history ฝั่งเราแทน (Grok API ไม่มี session ในตัว)

async def _run_grok_chat(user_text: str, session_id: str, system_prompt: str,
                          api_key: str, model: str = "grok-4-latest",
                          on_log=None, reset: bool = False, max_tokens: int = 4096) -> str | None:
    """Chat กับ Grok — จำลอง session โดยเก็บ history ไว้ฝั่งเรา ส่ง messages ทั้งชุดทุกครั้ง"""
    if not api_key:
        if on_log: await on_log("❌ Grok API key ว่าง — ใส่ก่อนใน Settings")
        return None
    if reset or session_id not in _grok_chat_histories:
        _grok_chat_histories[session_id] = []
    history = _grok_chat_histories[session_id]
    history.append({"role": "user", "content": user_text})

    if _grok_custom_sys.strip():
        system_prompt = system_prompt + "\n\n" + _grok_custom_sys.strip()

    if on_log:
        await on_log(f"📤 CHAT USER TEXT (Grok):\n{user_text}")
        await on_log(f"📋 CHAT SYSTEM PROMPT:\n{system_prompt}")

    resp = await _grok_chat_completions(
        [{"role": "system", "content": system_prompt}] + history,
        max_tokens, api_key, model, on_log=on_log,
    )
    if resp is None:
        history.pop()
        return None

    raw = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    if on_log: await on_log(f"RAW chat response (Grok):\n{raw}")
    history.append({"role": "assistant", "content": raw})
    return raw

# ต่อท้าย system prompt เดิม "เฉพาะตอนคุยต่อ" เท่านั้น (ไม่แตะ turns[]['system_prompt'] ที่เก็บไว้ —
# นั่นคือของจริงที่ยิงตอนสร้างช็อต) กันปัญหาที่พบจริง: system prompt เดิมสั่ง "Output the JSON
# only — no prose" ไว้ท้ายสุด พอผู้ใช้ถามคำถามที่ฟังดูเหมือนชี้จุดบกพร่องตาม schema (เช่นพูดถึงชื่อ
# field ตรง ๆ) โมเดล (ทั้ง Grok และ Local) จะสลับกลับไปโหมด "แก้/สร้างช็อตใหม่ให้ตรง schema" แทนที่
# จะตอบเป็นคำอธิบาย — ผู้ใช้ยืนยันให้เติมกันไว้ ไม่ใช่ system prompt ใหม่/แยก แค่ข้อความสั้น ๆ
# ต่อท้ายของเดิมบอกว่าโหมดคุยต่อนี้ไม่ใช่โหมดสร้างช็อต
_OUTLINE_CHAT_NOTE = (
    "\n\nหมายเหตุสำหรับบทสนทนาที่ต่อจากนี้: ข้อความถัดไปจากผู้ใช้เป็นคำถามเกี่ยวกับช็อตที่เขียนไปแล้ว "
    "ไม่ใช่คำสั่งให้สร้างหรือแก้ช็อตใหม่ — ตอบเป็นคำอธิบายเหตุผลด้วยภาษาธรรมดา ไม่ต้อง output เป็น JSON "
    "หรือช็อตชุดใหม่ เว้นแต่ผู้ใช้จะขอให้แก้ไข/สร้างช็อตจริง ๆ"
)

async def run_outline_chat_message(chat_capture: dict, chat_session_id: str, user_text: str,
                                    grok_api_key: str = "", on_log=None) -> str | None:
    """คุยต่อจาก conversation จริงของ Outline B (STEP 2 shots pass) — ไม่ประกอบ system prompt ใหม่ ไม่ห่อ
    user_text ด้วยข้อความ/คำสั่งเพิ่มเติมใด ๆ ส่งสิ่งที่ผู้ใช้พิมพ์ตรง ๆ เป็น user turn ถัดไป บน
    system prompt + history ชุดเดียวกับที่ยิงตอนสร้างช็อตจริง (มาจาก run_outline_shots_pass(chat_capture=...))
    บวกโน้ตสั้น ๆ ท้าย system prompt ตัวเดียว (_OUTLINE_CHAT_NOTE) กันหลุดกลับไปโหมด JSON — เพิ่มตามที่
    ผู้ใช้ยืนยันขอเอง หลังพบว่าทั้ง Grok และ Local สลับไปสร้างช็อตใหม่แทนตอบคำถามจริงในการใช้งานจริง

    chat_capture: ต้องมี ['turns'] (อย่างน้อย 1 รายการ {system_prompt,user_prompt,raw_response}),
    ['backend'] ('grok'/'local'), และสำหรับ local: ['llm_wf_file']+['session_id']
    chat_session_id: ตัวจัดการฝั่งนี้สำหรับ Grok เท่านั้น (เก็บ history ใน _grok_chat_histories) —
    Local ไม่ใช้พารามิเตอร์นี้ ใช้ chat_capture['session_id'] ซึ่งคือ ComfyUI session จริงที่มี
    conversation ของ Outline B อยู่แล้วฝั่งเซิร์ฟเวอร์เสมอ"""
    turns = chat_capture.get("turns") or []
    if not turns:
        return None
    last_sys = turns[-1].get("system_prompt", "") + _OUTLINE_CHAT_NOTE
    if chat_capture.get("backend") == "grok":
        if chat_session_id not in _grok_chat_histories:
            hist: list = []
            for t in turns:
                hist.append({"role": "user", "content": t.get("user_prompt", "")})
                hist.append({"role": "assistant", "content": t.get("raw_response", "")})
            _grok_chat_histories[chat_session_id] = hist
        return await _run_grok_chat(user_text, chat_session_id, last_sys, grok_api_key,
                                     chat_capture.get("grok_model") or "grok-4-latest",
                                     on_log=on_log, reset=False)
    llm_wf_file = chat_capture.get("llm_wf_file")
    session_id = chat_capture.get("session_id")
    if not llm_wf_file or not session_id:
        return None
    llm_wf_path = LLM_DIR / f"{llm_wf_file}.json"
    if not llm_wf_path.exists():
        return None
    wf_template = json.loads(llm_wf_path.read_text(encoding="utf-8"))
    return await run_llm_chat(user_text, wf_template, session_id, last_sys,
                               on_log=on_log, reset=False)

async def run_llm_chat(user_text: str, wf_template: dict | None, session_id: str,
                       system_prompt: str, on_log=None,
                       reset: bool = False, max_tokens: int = 4096,
                       grok_api_key: str = "", grok_model: str = "grok-4-latest") -> str | None:
    """Chat แบบ session ไม่ reset ทุกครั้ง — LLM จำ conversation. wf_template=None ⇒ ใช้ Grok API แทน"""
    if wf_template is None:
        return await _run_grok_chat(user_text, session_id, system_prompt, grok_api_key, grok_model,
                                     on_log=on_log, reset=reset, max_tokens=max_tokens)
    import copy
    loop = asyncio.get_event_loop()
    wf = copy.deepcopy(wf_template)
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        if node.get("class_type") == "LLMSessionChatNode":
            wf[nid]["inputs"]["user_text"]       = user_text
            wf[nid]["inputs"]["system_prompt"]   = system_prompt
            wf[nid]["inputs"]["session_id"]      = session_id
            wf[nid]["inputs"]["reset_session"]   = reset
            wf[nid]["inputs"]["max_tokens"]      = max_tokens
            _overrides.apply(wf[nid]["inputs"], n_ctx=32768, temperature=0.7, repeat_penalty=1.1)      # Project Assistant chat: Program mode writes these, Workflow mode keeps the workflow's

    cid = uuid.uuid4().hex
    if on_log:
        await on_log(f"📤 CHAT USER TEXT:\n{user_text}")
        await on_log(f"📋 CHAT SYSTEM PROMPT:\n{system_prompt}")
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, cid)
        if on_log: await on_log(f"✅ Chat queued pid={pid[:8]}")
    except Exception as e:
        if on_log: await on_log(f"❌ Queue: {e}")
        return None

    _timeout_s = 120
    deadline = time.time() + _timeout_s
    while time.time() < deadline:
        await asyncio.sleep(2)
        try:
            hist = await loop.run_in_executor(None, comfy_history, pid)
        except Exception as _ex:
            if on_log: await on_log(f"⚠ Chat history poll error: {_ex}")
            continue
        if pid not in hist: continue
        for nout in hist[pid].get("outputs", {}).values():
            for txt in nout.get("text", []):
                if txt and isinstance(txt, str):
                    if on_log: await on_log(f"RAW chat response:\n{txt}")
                    return txt
        _status = hist[pid].get("status", {}).get("status_str", "")
        if _status == "error":
            if on_log: await on_log(f"❌ Chat ComfyUI error (pid={pid[:8]})")
            return None
        if _status == "success":
            if on_log: await on_log(f"⚠ Chat finished (pid={pid[:8]}) but no text output found in result")
            return None
    if on_log: await on_log(f"⏱ Chat timeout after {_timeout_s}s (pid={pid[:8]}) — ComfyUI ไม่ตอบกลับภายในเวลา")
    return None

async def run_vision_check(
    shot_data: dict,
    caption: str,
    llm_wf_file: str,
    mode: str,              # "editor" หรือ "director"
    character_desc: str,
    scene_desc_global: str,
    requirement: str,
    story_text: str,       # เนื้อเรื่องเต็ม — เลิกใช้ summary ตั้งแต่ 3ff18e1 แล้ว
    image_style: str = "",
    image_size: str = "",
    extra_req: str = "",
    genre: str = "",
    on_log=None,
    stop_event=None,
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    prompt_style: str = "narrative",
    manga_page: bool = False,
    fill_engine: str = "",   # ว่าง = อ่านจาก shot_data._fill_engine
) -> dict | None:
    """
    Vision-assisted prompt improvement:
    JoyCaption (local, แยกต่างหาก) อ่านภาพจริงแล้วสรุปเป็น caption ข้อความ
    LLM (local หรือ Grok — รับแค่ข้อความ ไม่ได้เห็นภาพเอง) ใช้ caption นั้นปรับ prompt ให้ดีขึ้น
    """
    wf_template = None
    if llm_wf_file != GROK_MARKER:
        llm_wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not llm_wf_path.exists():
            if on_log: await on_log(f"❌ ไม่พบ {llm_wf_path}")
            return None
        try:
            wf_template = json.loads(llm_wf_path.read_text(encoding="utf-8"))
        except Exception as e:
            if on_log: await on_log(f"❌ {e}")
            return None

    loop = asyncio.get_event_loop()
    char_str  = f"Character: {character_desc}" if character_desc else ""
    scene_str = f"Global Scene: {scene_desc_global}" if scene_desc_global else ""
    req_str   = f"Special requirements: {requirement}" if requirement else ""
    style_str = f"Image Style: {image_style}" if image_style else ""
    size_str  = f"Canvas Size: {image_size}" if image_size else ""
    extra_str = f"\n⚠ EXTRA REQUEST: {extra_req}" if extra_req else ""
    genre_str  = _build_genre_str(genre)
    summary_str = f"Story:\n{story_text}\n\n" if story_text else ""
    # เหมือน Checker/Director: ถ้า prompt เขียนด้วยสัญญา V3 ต้องใช้สัญญานั้น ไม่ใช่ของ Legacy
    # ไม่งั้น editor จะ "แก้" prompt ที่ถูกอยู่แล้วให้กลับไปเป็นรูปแบบเก่า
    _ed_engine = normalize_fill_engine(
        fill_engine or (shot_data or {}).get("_fill_engine") or "")
    if _ed_engine in _LOCK_CONTRACT_ENGINES:
        _format_rule = _minimal_v3_format(prompt_style, manga_page)
        _prompt_field_desc = _minimal_v3_field_desc(prompt_style, manga_page)
        _format_rule += (
            "\n- LOCK BLOCKS ARE STRUCTURE, NOT PROSE: every lock block present in the original "
            "prompt — IDENTITY LOCK, LOCATION LOCK, and OBJECT LOCK when the original has one — "
            "must still be there, once each, in the same place, with its own label alone on its "
            "own line and a blank line around it. Never delete one, never duplicate one, never "
            "fold one into the paragraph, and never add one the original did not have. Their "
            "content is read-only here: a lock is edited only when the caption itself proves that "
            "specific locked fact rendered wrong. Do not add Pose:/Climax: sections, rendering-"
            "style words, or invented pose, gaze, expression detail or architecture."
        )
    else:
        _format_rule, _prompt_field_desc = _build_prompt_format_rule(prompt_style)

    _vision_evidence_rule = """CRITICAL EVIDENCE PRINCIPLE — CAPTION SILENCE IS NOT PROOF OF ABSENCE.
JoyCaption is not an exhaustive inventory of the image. A detail the caption does not mention is NOT
evidence that the render omitted it. A repair normally requires POSITIVE evidence of a mismatch: the
caption clearly describing something different from what the prompt asked for.
  VALID evidence: the prompt intends two people and the caption clearly describes three; the prompt says a
  blue object and the caption clearly describes a red one; a wide frame was intended and the caption
  explicitly describes a tight portrait.
  NOT sufficient: the caption never mentions shoes; the caption never mentions an object's small
  distinguishing mark; the caption does not mention hair texture. Do not overcorrect on silence, and do not
  rewrite on a caption that is merely vague or uncertain.

THE CAPTION IS EVIDENCE OF FAILURE, NEVER A NEW SOURCE OF TRUTH. It describes what the image model got
wrong; it never tells you what the shot is supposed to be. When the caption reports a value that
contradicts the prompt, the PROMPT's value is the intended one — make the prompt state that intended value
more clearly or more forcefully so the next render obeys it. Copying the rendered mistake into the prompt
(a car the prompt calls dark-blue rewritten as red because the render came out red) is the single worst
failure available to you. For the same reason you never import a later story event, a new action, or any
content the caption happens to describe: the shot stays the same moment it already was."""

    _vision_check_block = """BEFORE YOU SUBMIT — DIAGNOSTIC CHECK
Silently ask yourself these questions and act on what they expose. Do not output the questions or any
reasoning.
1. EVIDENCE — does the caption positively establish a mismatch, or is it merely silent or uncertain?
2. INTENT — what was the original prompt actually trying to produce?
3. MISMATCH — is the rendered result meaningfully different from that intent?
4. PROMPT CAUSE — is some wording weak, ambiguous, conflicting, or easy for the image model to misread?
5. MINIMUM FIX — what is the smallest wording change likely to improve the next render?
6. PRESERVATION — can the fix leave everything that already rendered correctly untouched?
7. MODE SCOPE — are you staying inside your authority for this mode?
8. STRUCTURE — did the prompt format and the lock contract survive?
9. NO-EVIDENCE CASE — if no meaningful mismatch is established, keep_original=true and return the prompt
   exactly as it is."""

    if mode == "editor":
        sys_p = f"""You are an evidence-based diagnostic visual editor.

ROLE
The image already exists. A JoyCaption model has read it and written a caption describing what actually
appeared. You diagnose render failures from that evidence and change the prompt's wording only where the
evidence supports it.

MATERIAL
The caption of the actual render, the original prompt that produced it, this shot's canonical information,
and the prompt's required format and lock contract.

{char_str}
{scene_str}
{genre_str}
{style_str}
{req_str}{extra_str}

{"⚠ MULTI-PANEL MODE — the prompt is 'N-panel page...' format (N is a digit). Do NOT flag panel structure as a problem. Do NOT try to convert to single-image format." if manga_page else ""}

GOAL
Identify the smallest prompt-level change, supported by actual render evidence, that is likely to correct a
meaningful mismatch on the next generation. Do not redesign a shot that came out correct.

{_vision_evidence_rule}

AUTHORITY (mode = editor)
You may repair render fidelity for canonical visual content — cast, identity, clothing, objects, action,
environment, or any other mismatch the caption visibly proves. You do this by making the prompt's wording
clearer or more specific about what was already intended; you never change what the shot IS. "More precise"
means clearer, never a different story fact and never a more exposed state than the canonical one.

RULES
- The original prompt is the BASE — keep everything that rendered correctly, and change only what the
  caption reveals went wrong.
{("MULTI-PANEL FORMAT LOCK: keep 'N-panel page...' (N is a digit) as the opening. Never rewrite to single-image format." if manga_page else _format_rule)}
- Output ONLY valid JSON
{ANIMAL_SAFETY_RULE}

{_vision_check_block}

Output:
{{"shot": <n>, "keep_original": <bool>, "issues": ["<short English max 8 words>"], "reason": "<English max 10 words>",
"prompt": "<original, or minimally revised — {_prompt_field_desc}>", "scene_desc": "<copy unchanged>", "dialogue": "<copy unchanged>"}}"""

    else:  # director
        sys_p = f"""You are an evidence-based diagnostic visual director.

ROLE
The image already exists. A JoyCaption model has read it and written a caption describing what actually
appeared — including how the frame was composed and lit. You diagnose visual-direction failures from that
evidence and adjust only the prompt's framing, composition and lighting wording.

MATERIAL
The caption of the actual render, the original prompt that produced it, this shot's canonical information,
and the prompt's required format and lock contract.

{char_str}
{style_str}
{size_str}
{genre_str}
{req_str}{extra_str}

{"⚠ MULTI-PANEL MODE — the prompt is 'N-panel page...' format (N is a digit). Do NOT add a global framing prefix. Do NOT flag panel structure. Do NOT convert to single-image format." if manga_page else ""}

GOAL
Identify the smallest change to the visual direction, supported by actual render evidence, that is likely
to correct a meaningful framing, composition or lighting mismatch on the next generation. Do not redesign a
frame that came out right.

{_vision_evidence_rule}

AUTHORITY (mode = director)
You may repair only visual-direction failures: framing, composition, visual hierarchy, and lighting
treatment. Locked story content is not yours to touch here — every character, outfit, pose, expression,
object and environment detail from the original prompt is preserved exactly, and so are the lock blocks.
Describe visual EFFECTS, not technical specs ("shallow depth of field", not "f/1.4").

RULES
{"" if manga_page else "- Weave framing and lighting into the existing structure; do not append a new format around it."}
{("MULTI-PANEL FORMAT LOCK: keep 'N-panel page...' (N is a digit) as the opening. Framing words go INSIDE each panel description, never as a global prefix. Never rewrite to single-image format." if manga_page else _format_rule)}
- Preserve all non-framing/lighting content from the original prompt
- Output ONLY valid JSON

{_vision_check_block}

Output:
{{"shot": <n>, "keep_original": <bool>, "issues": ["<short English max 8 words>"], "reason": "<English max 10 words>",
"prompt": "<original, or minimally revised — {_prompt_field_desc}>", "scene_desc": "<copy unchanged>", "dialogue": "<copy unchanged>"}}"""

    user_p = (
        f"{summary_str}"
        f"=== WHAT JOYCAPTION SAW IN THE ACTUAL GENERATED IMAGE ===\n"
        f"{caption}\n\n"
        f"=== ORIGINAL PROMPT (what was sent to generate the image) ===\n"
        f"{shot_data.get('prompt', '')}\n\n"
        f"=== SHOT INFO ===\n"
        f"Shot number: {shot_data.get('shot', 1)}\n"
        f"scene_desc: {shot_data.get('scene_desc', '')}\n"
        f"mood: {shot_data.get('mood', '')}\n"
        f"dialogue: {shot_data.get('dialogue', '')}\n\n"
        f"Compare the caption of the ACTUAL image against the original prompt and this shot's canonical\n"
        f"information. Where the caption POSITIVELY establishes a meaningful mismatch, make the smallest\n"
        f"wording change likely to fix it on the next render, leaving everything that rendered correctly\n"
        f"untouched. Where the caption is merely silent, vague or uncertain about a detail, that is not\n"
        f"evidence of a mismatch → keep_original: true and return the prompt unchanged.\n"
        f"Output the JSON."
    )

    if on_log:
        await on_log(f"👁 Vision {mode} — caption:\n{caption[:300]}...")
        await on_log(f"📤 USER:\n{user_p[:500]}...")

    import re as _re_v
    def _parse_vision(raw):
        t = raw.strip()
        t = _re_v.sub(r'```+(?:json)?\s*', '', t).strip()
        try:
            s = t.index('{'); e = t.rindex('}') + 1
            chunk = t[s:e]
        except ValueError:
            return None
        for attempt_txt in [chunk]:
            try:
                return json.loads(attempt_txt)
            except Exception:
                pass
            # fix escaped chars
            res = []; in_s = False; esc = False
            for c in attempt_txt:
                if esc: res.append(c); esc = False; continue
                if c == '\\': res.append(c); esc = True; continue
                if c == '"': in_s = not in_s; res.append(c); continue
                if in_s and c == '\n': res.append('\\n'); continue
                if in_s and c == '\r': continue
                res.append(c)
            fixed = ''.join(res)
            # partial recovery
            in_s2 = False; esc2 = False; res2 = []
            for c in fixed:
                if esc2: res2.append(c); esc2 = False; continue
                if c == '\\': res2.append(c); esc2 = True; continue
                if c == '"': in_s2 = not in_s2; res2.append(c); continue
                if in_s2 and c == '\n': res2.append('\\n'); continue
                res2.append(c)
            pf = ''.join(res2)
            if in_s2: pf += '"'
            pf += ']' * max(0, pf.count('[') - pf.count(']'))
            pf += '}' * max(0, pf.count('{') - pf.count('}'))
            try:
                obj = json.loads(pf)
                if isinstance(obj, dict) and "prompt" in obj:
                    return obj
            except Exception:
                pass
        return None

    for attempt in range(1, 4):
        if stop_event and stop_event.is_set(): return None
        raw = await _call_llm_generic(
            llm_wf_file, wf_template, sys_p, user_p, loop, max_tokens=_llm_tokens_fill,
            on_log=on_log if attempt == 1 else None,
            grok_api_key=grok_api_key, grok_model=grok_model,
        )
        if not raw:
            if on_log: await on_log(f"⚠ attempt {attempt} ไม่ได้ output")
            continue
        if on_log and attempt == 1:
            await on_log(f"RAW vision {mode}:\n{raw[:500]}")
        result = _parse_vision(raw)
        if result:
            return result
        if on_log: await on_log(f"⚠ attempt {attempt} parse ไม่ได้")

_FILL_LEDGER_START = "<<<FILL_DESIGN_LEDGER>>>"
