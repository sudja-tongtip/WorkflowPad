"""เทสต์ว่าตัวตนถาวรของตัวละคร "ไม่ถูกเสก" ต้นทาง และ "ไม่หาย" ปลายทาง

รันด้วย:  python -m story_generator.tests.test_identity_propagation

สองปัญหาที่เป็นด้านตรงข้ามของกฎเดียวกัน:
A. เสกขึ้นมา — เรื่องบอกแค่ "นางฟ้า"/"โทรลล์" แต่ชีตใส่ "Thai" ให้ เพราะต้นฉบับเขียนเป็นภาษาไทย
B. หายไป — ชีตบอก "ปีกนางฟ้าใหญ่สองข้าง" ถูกต้องแล้ว แต่ prompt เต็มตัวบางช็อตไม่มีปีก

ต้นตอของ A คือกฎ PHYSICAL DETAIL IS YOURS TO DECIDE ที่สั่งห้ามเว้นว่างทุกช่อง — ถูกสำหรับ
ผม/ตา/ผิว/รูปร่าง แต่ผิดสำหรับสัญชาติ เพราะสัญชาติที่เดาเอาคือ "คนละคน" ไม่ใช่ "รายละเอียดที่เติมให้"
ต้นตอของ B คือ SCALE MAY SIMPLIFY IT ครอบคลุมแค่ "ระยะไกลลดรายละเอียด" ไม่ได้ตอบว่า
อวัยวะที่กำหนดเงาร่าง (ปีก หาง เขา แขนเกิน) ต้องอยู่ที่ระยะเต็มตัว

ขอบเขต: เทสต์ระดับ prompt contract เป็นหลัก + เทสต์พฤติกรรมจริงของตัวดึงสัญชาติจากชีต
"""
import inspect
import re as _re

from .. import llm_engine as L

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


_SRC = inspect.getsource(L)

# ── A: ต้นทาง — ห้ามเสกสัญชาติ (§3, §4, §18) ───────────────────────────────
check("A carve-out lives in BOTH outline character-sheet prompts",
      _SRC.count("{_OUTLINE_PHYSICAL_DETAIL_RULE}") == 2
      and "WHERE A PERSON OR BEING COMES FROM IS NOT ONE OF THOSE CHOICES"
      in L._OUTLINE_PHYSICAL_DETAIL_RULE,
      "ต้องมีทั้งสองรอบของ PHYSICAL DETAIL IS YOURS TO DECIDE")
check("A language is explicitly not nationality",
      "The LANGUAGE THE STORY IS WRITTEN IN tells you nothing about it" in _SRC
      and "a story written in Thai is not a story about Thai people" in _SRC)
check("A other languages named too, not just Thai",
      all(w in _SRC for w in ("Japanese, Chinese,", "English or any other language")))
check("A vague fallback ethnicity is forbidden",
      "never" in _SRC and "substitute a vague one" in _SRC
      and all(w in _SRC for w in ("Asian", "Western", "Caucasian")))
check("A unknown origin may stay unwritten",
      "write the person without one" in _SRC)
check("A non-human gets their kind in that slot",
      "FOR A NON-HUMAN, THEIR KIND GOES IN THAT PLACE INSTEAD" in _SRC
      and "A fairy has no nationality" in _SRC)
check("A identity facts are not manufactured to fill blanks",
      "DO NOT INVENT CANONICAL IDENTITY FACTS" in L._OUTLINE_PHYSICAL_DETAIL_RULE
      and "do not invent nationality" in L._OUTLINE_PHYSICAL_DETAIL_RULE
      and "CONCRETE NUMERIC ADULT AGE" in L._OUTLINE_PHYSICAL_DETAIL_RULE
      and "downstream stages read the sheet as the final word" in L._OUTLINE_PHYSICAL_DETAIL_RULE)

# ── B: Fill IDENTITY LOCK — ไม่บังคับให้เสกเมื่อชีตไม่มี ────────────────────
_LOCK = L._GENDER_LOCK_RULE
check("B lock asks for what the sheet says they ARE",
      "the sheet's word for what they ARE" in _LOCK)
check("B lock accepts a non-human kind",
      "for a non-human it is their kind" in _LOCK and "fairy" in _LOCK)
check("B lock forbids filling the gap",
      "IF THE SHEET GIVES NONE, WRITE NONE" in _LOCK
      and "not from the language the story" in _LOCK
      and "not from their name" in _LOCK)
check("B lock still bans dropping a nationality the sheet DOES give",
      "never drop it at any distance" in _LOCK)

# ── C: พฤติกรรมจริง — ตัวดึงสัญชาติต้องไม่คืนค่าให้ตัวละครที่ไม่ใช่มนุษย์ ────
for _label, _rest, _want in (
    ("fairy",         "female, 22, fae, long silver hair, green eyes",       ""),
    ("forest giant",  "male, 300, forest giant, moss-green skin",            ""),
    ("troll",         "male, 40, troll, grey warty skin, tusks",             ""),
    ("android",       "female, 3, android, synthetic white skin",            ""),
    ("no origin",     "female, 22, long silver hair, green eyes",            ""),
    ("stated Thai",   "female, 19, Thai, long black hair",                   "Thai"),
    ("stated Thai TH", "หญิง, 19, ไทย, ผมยาวสีดำ",                            "Thai"),
    ("stated Russian", "female, 28, Russian, black hair",                    "Russian"),
):
    _got = L._nat_from_sheet_rest(_rest)
    check(f"C nationality extractor: {_label} -> {_want!r}", _got == _want, repr(_got))

# ชีตนางฟ้าจริงต้องไม่ได้สัญชาติติดมาใน IDENTITY LOCK ของช็อต
_FAE_SHEET = ("Characters (1 woman + 1 man):\n"
              "Elara: female, 22, fae, long luminous silver hair, deep green eyes, "
              "two large translucent fairy wings.\n"
              "Verdan: male, 300, forest giant, moss-green skin, glowing green eyes.")
_lock_line = L._gender_lock_for_shot(_FAE_SHEET, "Elara lies on the moss")
check("C fairy shot lock invents no nationality",
      not _re.search(r'\b(Thai|Asian|Western|Caucasian|Japanese)\b', _lock_line), _lock_line[:160])
check("C fairy shot lock still carries name/age/gender",
      "Elara" in _lock_line and "22" in _lock_line and "woman" in _lock_line, _lock_line[:160])
check("C lock still tells the model to read permanent traits off the sheet",
      "permanent body traits" in _lock_line and "read from the sheet itself" in _lock_line)

_TH_SHEET = "Characters (1 woman):\nDao: female, 19, Thai, long black hair, brown eyes."
check("C a stated nationality still travels",
      "Thai" in L._gender_lock_for_shot(_TH_SHEET, "Dao waits"))

# ── D: ปลายทาง — อวัยวะถาวรต้องไม่หายที่ระยะที่มองเห็น (§6, §7, §14) ────────
_PERM = L._PERMANENT_BODY_IDENTITY_RULE
check("D decided by the camera, not by the beat",
      "WHAT THE FRAME WOULD SHOW, IT SHOWS" in _PERM
      and "never by what the beat is about" in _PERM)
check("D silhouette traits are high priority",
      "changes the SILHOUETTE" in _PERM
      and all(w in _PERM for w in ("wings", "a tail", "horns", "extra arms")))
check("D winged character without wings is a different creature",
      "a winged character drawn without wings is a different creature" in _PERM)
check("D required at full body or wider even when the beat ignores it",
      "at full body " in _PERM and "even when nothing in this beat involves it" in _PERM)
check("D out of frame is genuinely allowed",
      "are out of frame and do not belong in the words" in _PERM
      and "Never pad a close-up with parts it cannot see." in _PERM)
check("D the trait still exists even when unwritten",
      "The trait is still true of the body" in _PERM)
check("D old guarantees kept",
      "SCALE MAY SIMPLIFY IT, NEVER REMOVE IT" in _PERM
      and "ONLY WHAT THE SHEET ESTABLISHES" in _PERM
      and "never move one person's trait onto another" in _PERM)

# ── E: Checker — ตรวจเจอ ซ่อมน้อยที่สุด และไม่ over-correct (§11-13) ────────
check("E checker has a permanent-trait visibility check",
      "PERMANENT BODY TRAITS THE FRAMING WOULD SHOW" in _SRC)
check("E checker reads the traits off the sheet",
      "read each visible person's sheet for what is permanently" in _SRC)
check("E omission from the event is not removal",
      "is not removal from the body." in _SRC)
check("E checker repairs only the trait",
      "REPAIR IT AND NOTHING ELSE" in _SRC
      and "do not touch the camera, pose, action, expression, clothing, props" in _SRC)
check("E checker must not force out-of-frame traits",
      "DO NOT FORCE WHAT IS OUT OF FRAME" in _SRC
      and "never pad a close-up with body parts the camera cannot see" in _SRC)
check("E checker origin rule accepts a non-human kind",
      "ORIGIN MUST MATCH THE SHEET" in _SRC
      and "their kind for a non-human" in _SRC)
check("E checker must not invent a missing nationality",
      "WHAT THE SHEET DOES NOT SAY, YOU DO NOT ADD" in _SRC
      and "Adding an ethnicity the sheet never stated is itself the" in _SRC)
check("E attribute completeness no longer demands an invented value",
      "An attribute the SHEET does not state is not missing" in _SRC)

# ── F: ไม่แก้ด้วย keyword / ไม่แตะ schema (§15, §16, §17) ───────────────────
_BAD_FIELDS = ("visible_traits", "persistent_traits", "wing_state",
               "body_identity_map", "nationality_source", "trait_visibility")
for _f in _BAD_FIELDS:
    check(f"F no new JSON field {_f}", _f not in _SRC)

# กฎนี้ต้องไม่ถูกแก้ด้วย regex ที่คอมไพล์คำอวัยวะแฟนตาซีไว้ตัดสินใจ
_TRAIT_WORDS = (r'\bwings?\b', r'\bfairy\b', r'\bfae\b', r'\btroll\b', r'\bhorns?\b', r'\btails?\b')
_hits = []
for _n in dir(L):
    _pat = getattr(getattr(L, _n), "pattern", None)
    if not isinstance(_pat, str):
        continue
    for _w in _TRAIT_WORDS:
        if _re.search(_w, _pat, _re.I):
            _hits.append((_n, _w))
check("F no fantasy-trait keyword compiled into a regex", not _hits, _hits[:3])
check("F no hard-coded nationality fallback in code",
      not _re.search(r'(?:nationality|nat)\s*=\s*["\']Thai["\']', _SRC))

# ตัวดึงสัญชาติเดิมยังเป็นรายการ "คำที่รู้จัก" ได้ — มันอ่านสิ่งที่ชีตเขียน ไม่ได้เสกให้ใคร
check("F nationality extractor never invents when it recognises nothing",
      L._nat_from_sheet_rest("female, 22, moon-elf, silver hair") == "")

# ── G: ไม่มี regression — กฎรอบข้างยังอยู่ ─────────────────────────────────
for _label, _probe in {
    "permanent body identity":  "PERMANENT BODY IDENTITY (the character sheet is canonical",
    "persistent trait rule":    "PERMANENT BODY FACTS STAY ON THE SHEET",
    "anatomy interaction":      "PHYSICAL CONSISTENCY",
    "visibility gate":          "VISIBILITY GATE (CRITICAL",
    "identity not trimmed":     "IDENTITY IS NEVER TRIMMED BY SCALE",
    "roster is scene_desc":     "THIS SHOT'S ROSTER IS scene_desc",
    "prop state ground truth":  "PROP STATE (CRITICAL)",
}.items():
    check(f"G {_label} still present", _probe in _SRC, _probe)

check("G anatomy cleanup not undone — no per-joint choreography demanded",
      "Motion and secondary motion must remain physically coherent" in _SRC
      and "work out the limb-by-limb picture" not in _SRC)

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
