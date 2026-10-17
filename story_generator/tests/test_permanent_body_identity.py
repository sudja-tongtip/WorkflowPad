"""เทสต์ว่า "ของถาวรบนร่างกาย" จาก Character Sheet เดินทางถึง image prompt ทุกช็อต

รันด้วย:  python -m story_generator.tests.test_permanent_body_identity

ปัญหาเดิม: Outline A เก็บแขนกลเข้าชีตได้แล้ว แต่ Fill/Check/Director นิยาม "fixed identity"
เป็นลิสต์ปิด (เพศ อายุ สัญชาติ ผม ตา ผิว รูปร่าง) ของถาวรจึงเข้า prompt เฉพาะช็อตที่ event
บังเอิญพูดถึงมัน เทสต์นี้ตรวจว่าลิสต์ปิดทั้งหมดถูกเปิดแล้ว และคำสั่งเป็น generic ไม่ผูกกับแขนกล

ขอบเขต: เทสต์ระดับ prompt contract — ยืนยันว่าคำสั่งถูกส่งถึงโมเดล ไม่ได้ยืนยันว่าโมเดลทำตาม
Acceptance test จริง (Anya ทุกช็อตมีแขนกล) ต้องรัน Fill กับ LLM จริง
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


_RULE = L._PERMANENT_BODY_IDENTITY_RULE
_SRC = inspect.getsource(L)

# ── A: กฎกลางมีอยู่ และพูดครบทั้ง 4 ประเด็นของ brief ────────────────────────
check("A sheet is canonical, not the event",
      "the character sheet is canonical, the event is not" in _RULE.lower())
check("A must not wait for the event to mention it",
      "do not " in _RULE.lower() and "mention it again" in _RULE.lower())
check("A applies to every prompt where the character is visible",
      "EVERY prompt where that person is visible" in _RULE)
check("A scale may simplify but never remove",
      "SCALE MAY SIMPLIFY IT, NEVER REMOVE IT" in _RULE)
check("A never invent, never transfer",
      "ONLY WHAT THE SHEET ESTABLISHES" in _RULE
      and "never move one person's trait onto another" in _RULE)

# ── B: generic — ครอบคลุมหลายชนิด ไม่ได้แก้เฉพาะแขนกล ────────────────────
# brief ขอ scar / prosthetic leg / horns / tail เป็นอย่างน้อย
_KINDS = {
    "artificial/missing limb": ("artificial", "missing"),
    "scar":                    ("scar",),
    "tattoo/birthmark":        ("tattoo", "birthmark"),
    "horns":                   ("horns",),
    "tail":                    ("tail",),
    "wings":                   ("wings",),
    "non-human anatomy":       ("non-human",),
    "mutation":                ("mutation",),
    "implant":                 ("implant",),
}
for _kind, _words in _KINDS.items():
    check(f"B rule covers {_kind}", all(w in _RULE.lower() for w in _words), _words)

check("B rule is not written around cybernetics only",
      "cybernetic" not in _RULE.lower())
# ตัวอย่างในข้อความพรอมป์มีคำว่า mechanical/prosthetic ได้ (เป็นตัวอย่างให้โมเดลอ่าน)
# แต่ต้องไม่มี regex ตัวไหนคอมไพล์คำพวกนี้ไว้ตัดสินใจ — ถ้ามี แปลว่าโค้ดแก้เฉพาะเคส ไม่ generic
_TRAIT_WORDS = ("mechanical", "cybernetic", "prosthetic", r"\bhorns?\b", r"\btails?\b", r"\bscars?\b")
# _MANGA_DETAIL_RE เป็นตัวจัดประเภทช่องมังงะ (บีตที่พูดถึงแผลเป็น = ช่อง DETAIL) ไม่ได้ลบอะไร
# จึงไม่นับ — ตัวที่อันตรายคือ regex ที่ "ตัดทิ้ง" ข้อความ
_CLASSIFIERS = {"_MANGA_DETAIL_RE", "_MANGA_IMPACT_RE", "_MANGA_REVEAL_RE", "_MANGA_MOVE_RE"}
_regex_hits = []
for _n in dir(L):
    if _n in _CLASSIFIERS:
        continue
    _pat = getattr(getattr(L, _n), "pattern", None)
    if not isinstance(_pat, str):
        continue
    for _w in _TRAIT_WORDS:
        if _re.search(_w, _pat, _re.I):
            _regex_hits.append((_n, _w))
check("B no trait keyword compiled into a stripping regex", not _regex_hits, _regex_hits[:3])

# ── C: ลิสต์ปิดทุกที่ถูกเปิดแล้ว ────────────────────────────────────────────
check("C Fill per-person checklist includes permanent traits",
      "every permanent body trait the sheet states for them" in L._CHAR_ATTR_CHECKLIST)
check("C Fill checklist says traits survive every distance",
      "build, hair and any permanent body trait are identity" in L._CHAR_ATTR_CHECKLIST)
check("C scale-trim rule no longer closes the attribute list",
      "build, and every permanent body trait it states" in _SRC)
check("C checker compares traits against the sheet",
      "every permanent body trait the sheet states" in _SRC
      and "MISSING IDENTITY ATTRIBUTE" in _SRC)
check("C distant-scale lock keeps silhouette anatomy",
      "silhouette-scale " in _SRC and "anatomy reads at this distance" in _SRC)
check("C rule is wired into the Fill rules block",
      "{_GENDER_LOCK_RULE}\n{_IDENTITY_LOCK_FILL_RULE}\n{_PERMANENT_BODY_IDENTITY_RULE}\n" in _SRC)

# ── D: IDENTITY LOCK ต่อช็อต — ต้องไม่บอกว่าฟอร์มสั้นคือทั้งหมดของตัวตน ─────
_SHEETS = {
    "mechanical arms": "Anya: female, 25, Russian, dark hair, athletic build, both arms fully "
                       "mechanical carbon-fiber prosthetics. Wearing grey tank top.",
    "scar":            "Somchai: male, 40, Thai, short black hair, a permanent sword scar across "
                       "his left cheek. Wearing brown tunic.",
    "prosthetic leg":  "Mira: female, 30, American, red hair, her right leg is a steel prosthetic "
                       "below the knee. Wearing work overalls.",
    "horns and tail":  "Kaede: female, 20, Japanese, white hair, two small black horns and a long "
                       "scaled tail. Wearing a dark kimono.",
}
for _label, _line in _SHEETS.items():
    _sheet = "1 person\n" + _line
    _name = _line.split(":")[0]
    _lock = L._gender_lock_for_shot(_sheet, f"{_name} stands by the window")
    check(f"D {_label}: lock line produced", bool(_lock.strip()))
    check(f"D {_label}: short form marked as the MINIMUM",
          "it is the MINIMUM" in _lock, _lock[:120])
    check(f"D {_label}: lock tells the model to read traits from the sheet",
          "permanent body traits" in _lock and "read from the sheet itself" in _lock,
          _lock[-160:])
    check(f"D {_label}: identity still carries name/age/gender",
          _name in _lock)

# ── E: ไม่มี regression — กฎเดิมที่ห้ามแตะยังอยู่ ───────────────────────────
for _label, _probe in {
    "gender/age/nationality lock": "IDENTITY LOCK (CRITICAL — the VISIBILITY GATE does NOT override",
    "visibility gate":             "VISIBILITY GATE (CRITICAL",
    "identity not trimmed by scale": "IDENTITY IS NEVER TRIMMED BY SCALE",
    "roster is scene_desc":        "THIS SHOT'S ROSTER IS scene_desc",
    "no invented people":          "NEVER ADD A PERSON scene_desc DOES NOT NAME",
}.items():
    check(f"E {_label} still present", _probe in _SRC, _probe)

# ── F: ตัวกรอง micro-detail ที่ wide ต้องไม่กินอวัยวะถาวร ───────────────────
# _vis_strip ตัดรายละเอียดที่มองไม่เห็นที่ระยะไกล — ของถาวรต้องรอด
for _label, _txt in {
    "mechanical arms": "Anya (25, Russian woman), both arms fully mechanical black carbon-fiber",
    "scar":            "Somchai (40, Thai man), a long scar across his left cheek",
    "prosthetic leg":  "Mira (30, American woman), steel prosthetic right leg below the knee",
    "horns and tail":  "Kaede (20, Japanese woman), two small black horns and a long scaled tail",
}.items():
    _out = L._vis_strip(_txt, [])
    _kept = all(w in _out for w in _txt.split(",")[-1].strip().split()[:3])
    check(f"F wide-shot strip keeps {_label}", _kept, f"{_txt!r} -> {_out!r}")

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
