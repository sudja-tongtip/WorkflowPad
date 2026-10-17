"""เทสต์ว่าทั้งสี่ด่านถูกสอนเรื่อง "อวัยวะเกิน/อวัยวะถูกเข้าใจผิด" ผ่านกฎ PHYSICAL CONSISTENCY ที่สั้นลง

รันด้วย:  python -m story_generator.tests.test_anatomy_interaction

ประวัติ: กฎ _ANATOMY_INTERACTION_RULE เดิมยาว ~1900 ตัวอักษร เขียนแบบ tutorial (อธิบาย
เหตุผล + ตัวอย่างละเอียดทุกกรณี) ภายหลังพบว่า reasoning model ไม่จำเป็นต้องมีคำอธิบายยาวขนาดนั้น
จึงถูกบีบเหลือ 4 invariant สั้น ๆ (PHYSICAL CONSISTENCY block) ตามหลัก:
"บอกความจริงที่เป็น canon ให้ครบ ไม่ต้องสอนวิธีคิด" — เทสต์นี้เขียนใหม่ให้ตรวจกฎชุดใหม่
(ไม่ใช่ของเก่าที่ถูกลบไปแล้วโดยตั้งใจ)

ขอบเขต: เทสต์ระดับ prompt contract — ยืนยันว่า invariant ถูกส่งถึงโมเดล ไม่ได้ยืนยันว่าโมเดลทำตาม
"""
import inspect
import re as _re

from .. import llm_engine as L
from .. import main_ui as U

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


_RULE = L._ANATOMY_INTERACTION_RULE
_SRC = inspect.getsource(L)
_UI = inspect.getsource(U)
_low = _RULE.lower()

# ── A: กฎกลางสั้นแต่ครบ 4 invariant ─────────────────────────────────────────
check("A rule is headed PHYSICAL CONSISTENCY",
      _RULE.strip().startswith("- PHYSICAL CONSISTENCY:"))
check("A invariant 1: canonical anatomy is fixed (no invent/remove/duplicate/normalize/replace)",
      "canonical anatomy is fixed" in _low
      and all(w in _low for w in ("never invent", "remove", "duplicate", "normalize", "replace")))
check("A invariant 1 gives concrete anchors: mechanical/missing",
      "mechanical stays mechanical" in _low and "missing stays missing" in _low)
check("A invariant 1: four-armed character may use all four at once (judged against THIS body)",
      "a four-armed character" in _low and "may use all four at once" in _low
      and "never a generic human default" in _low)
check("A invariant 2: resolve one physically possible frozen moment",
      "resolve each shot as one physically possible frozen moment" in _low)
check("A invariant 2: conflict resolution is simplify/stage, never invent a limb",
      "simplify or stage them" in _low and "never invent another limb" in _low)
check("A invariant 3: body part stays part of its owner, not a separate entity",
      "remain part of their owner" in _low
      and "never a separate entity in the roster, cast list or spacing" in _low)
check("A invariant 3 covers prosthetics/wings/tails generically",
      all(w in _low for w in ("prosthetics", "wings", "tails")))
check("A invariant 4: prop_state is authoritative",
      "prop_state is authoritative for what is held, by whom, and where" in _low)
check("A invariant 4 forbids softening/relocating/dropping a silent prop",
      "do not soften, relocate or drop a prop" in _low
      and "scene_desc stays silent" in _low)

check("A rule is compact (well under the old ~1900-char tutorial)",
      len(_RULE) < 900, len(_RULE))

# ── B: generic — ไม่ hard-code เคสตัวอย่าง ──────────────────────────────────
for _kind in ("mechanical", "missing"):
    check(f"B rule names {_kind} as an anatomy anchor", _kind in _low)
check("B rule does not assert a fixed human hand count",
      not _re.search(r"\b(two hands|exactly two|both humans have)\b", _low))
for _banned in ("siang", "carbon fiber arm", "whisky", "whiskey", "cigarette", "fairy", "elara"):
    check(f"B rule text does not hard-code {_banned!r}", _banned not in _low)

# ของถาวรบนร่างกายต้องไม่ถูก regex ตัวไหนคอมไพล์ไว้ตัดสินใจ — กันไม่ให้แก้ปัญหาด้วย keyword
_BAD_PAT = (r"third hand", r"extra hand", r"hand_count", r"max_hands")
_hits = [n for n in dir(L)
         if isinstance(getattr(getattr(L, n), "pattern", None), str)
         and any(_re.search(p, getattr(L, n).pattern, _re.I) for p in _BAD_PAT)]
check("B no hand-counting regex was introduced", not _hits, _hits)
check("B no hand-count arithmetic was introduced",
      not _re.search(r"\b(max_hands|hand_count|num_hands)\b", _SRC))

# ── C: ต่อสายเข้าครบทั้งสี่ด่าน ─────────────────────────────────────────────
check("C Fill carries the shared rule",
      "{_PERMANENT_BODY_IDENTITY_RULE}\n{_ANATOMY_INTERACTION_RULE}\n" in _SRC)
check("C Fill resolves fields into one frozen moment (compact instruction, not a procedure)",
      "Resolve all supplied state fields" in _SRC
      and "ONE physically possible frozen moment" in _SRC)
check("C Fill no longer carries the old 'allocate before you write' procedural wording",
      "ALLOCATE BEFORE YOU WRITE" not in _SRC
      and "work out the limb-by-limb picture" not in _SRC)

check("C Checker long-form carries the shared rule directly (not a hand paraphrase)",
      _SRC.count("{_ANATOMY_INTERACTION_RULE}") >= 2)
check("C Checker audits independently, does not assume Fill was right",
      "audit against the sheet independently" in _SRC.lower()
      or "don't assume the writing stage got it right" in _SRC.lower())
check("C Checker repairs only the contradicting clause",
      "Repair only the contradicting clause" in _SRC)
check("C Checker retry-ladder (slim) variant also compact",
      "C2) PHYSICAL CONSISTENCY:" in _SRC)

check("C Director: current prompt is anatomy authority",
      "CURRENT PROMPT is content authority" in _SRC
      and "preserve its anatomy, limb ownership and props" in _SRC)
check("C Director: forbids adding a gesture with no free limb",
      "her free hand brushes" in _SRC and "invents a third hand" in _SRC)
check("C Director no longer carries the old verbose walk-back paragraph",
      "Silently walk the result back through" not in _SRC)

check("C Chat understands the three-hands question",
      "Why does she have three hands?" in _UI)
check("C Chat diagnoses before editing",
      "In REVIEW, explain the cause in those terms" in _UI
      and "do not edit yet" in _UI)

# ── D: ไม่ over-correct — เคสที่ถูกต้องต้องไม่ถูกสอนให้แก้ (พิสูจน์จาก integration text) ──
check("D chat: armrest case is not an error",
      "rest on an armrest at once and that is not an error" in _UI)

# ── E: ไม่มี regression — กฎเดิมรอบข้างยังอยู่ ──────────────────────────────
for _label, _probe in {
    "permanent body identity":  "PERMANENT BODY IDENTITY (the character sheet is canonical",
    "one person one body":      "ONE PERSON, ONE BLOCK, ONE BODY",
    "prop state ground truth":  "PROP STATE (CRITICAL)",
    "expression match section": "D) EXPRESSION MATCH",
    "director motion coherence": "Motion and secondary motion must remain physically coherent",
}.items():
    check(f"E {_label} still present", _probe in _SRC, _probe)

check("E chat character-first block still present",
      "CHARACTER FIRST, SHOT SECOND" in _UI)
check("E chat merge contract untouched",
      "only the shot objects you actually changed" in _UI)

# ── F: ไม่มีการเพิ่มฟิลด์ลง schema ─────────────────────────────────────────
check("F no limb_map field added to the project schema",
      "limb_map" not in _SRC and "limb_map" not in _UI)

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
