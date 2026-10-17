"""เทสต์ว่า "อวัยวะไม่ใช่คน" และ "prop ไม่หายไปเพราะ scene_desc เงียบ" ยังอยู่หลังบีบกฎให้สั้น

รันด้วย:  python -m story_generator.tests.test_physical_consistency

ปัญหาเดิม (จากโปรเจกต์จริง Cyber Punk 4 / Siang): เรื่องต้นฉบับบอกว่ามีแขนกลคาร์บอนไฟเบอร์
สองข้าง แต่ prop_state ของช็อตหนึ่งเขียน "Carbon Fiber Arm — operating near legs/torso" ราวกับ
เป็นวัตถุที่ถูก track และ spacing ของอีกช็อตเขียน "Siang left + Carbon Fiber Arm right" ราวกับ
แขนกลเป็นตัวละครที่สอง ช็อตถัดมายังทำให้บุหรี่ที่เคยถืออยู่ "absent from grasp but implied near
the hand" ทั้งที่ prop_state ไม่ได้บอกว่าถูกทิ้ง

รอบนี้ _ANATOMY_INTERACTION_RULE ถูกบีบจาก ~1900 ตัวอักษร (tutorial-style, มีตัวอย่างเยอะ) เหลือ
4 invariant สั้น (~600 ตัวอักษร) — เทสต์นี้เขียนใหม่ให้ตรวจว่า "ownership" กับ "prop continuity"
สองอย่างที่เพิ่งเพิ่มไปตอนก่อนบีบ ยังรอดอยู่ในกฎที่บีบแล้ว ไม่ได้หายไปพร้อมความยาว

ขอบเขต: เทสต์ระดับ prompt contract — ยืนยันว่าคำสั่งถูกส่งถึงโมเดล ไม่ได้ยืนยันว่าโมเดลทำตาม
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


_RULE = L._ANATOMY_INTERACTION_RULE
_SRC = inspect.getsource(L)
_low = _RULE.lower()

# ── A: ownership — อวัยวะไม่ใช่ entity ที่สอง ────────────────────────────────
check("A rule: body parts remain part of their owner",
      "remain part of their owner" in _low)
check("A rule: never a separate entity in roster/cast/spacing",
      "never a separate entity in the roster, cast list or spacing" in _low)
check("A rule covers prosthetics/wings/tails generically (not just 'limb')",
      all(w in _low for w in ("prosthetics", "wings", "tails")))

# ── B: prop continuity — prop_state เป็นความจริง ไม่ใช่สิ่งที่ scene_desc ยืนยันซ้ำ ──
check("B rule: prop_state is authoritative for who holds what",
      "prop_state is authoritative for what is held, by whom, and where" in _low)
check("B rule: forbids softening/relocating/dropping a prop scene_desc doesn't repeat",
      "do not soften, relocate or drop a prop it names" in _low
      and "scene_desc stays silent about it" in _low)

# ── C: ไม่ hard-code เคสตัวอย่าง (ห้ามผูกกับ Siang/Carbon Fiber Arm/whiskey) ──
for _banned in ("siang", "carbon fiber arm", "whiskey", "sofa"):
    check(f"C rule text does not hard-code {_banned!r}", _banned not in _low)

# ── D: ต่อสายเข้า Fill / Checker (ทั้ง sys หลักและ retry-ladder) / Outline ──
check("D Fill inherits the shared rule (interpolated, not restated)",
      _SRC.count("{_ANATOMY_INTERACTION_RULE}") >= 2)
check("D Checker long-form C2 interpolates the same shared rule",
      "C2) PHYSICAL CONSISTENCY — audit against the sheet independently" in _SRC)
check("D Checker retry-ladder (slim) compact variant covers the same ground",
      "any body part/prosthetics/wing/tail promoted into a second roster or spacing entity"
      .replace("prosthetics", "prosthetic") in _SRC
      or "promoted into a second roster or spacing entity" in _SRC)
check("D Checker retry-ladder variant covers prop drift too",
      "any prop drifting from what prop_state says" in _SRC)

check("D outline prop_state schema forbids tracking a body part as an object",
      "NEVER a body part, limb or prosthetic" in L._PROP_STATE_SCHEMA)
check("D outline spacing schema forbids listing a body part as a person",
      "Only PEOPLE go in this list" in _SRC
      and "never a limb, prosthetic, wing or tail as if it were a second person" in _SRC)

check("D Director preserves anatomy/ownership/props as content authority",
      "CURRENT PROMPT is content authority" in _SRC
      and "preserve its anatomy, limb ownership and props" in _SRC)

# ── E: ไม่มี regression — ของเดิมรอบข้างยังอยู่ ─────────────────────────────
for _label, _probe in {
    "permanent body identity intact": "PERMANENT BODY IDENTITY (the character sheet is canonical",
    "checker prop-state ground truth": "PROP STATE (CRITICAL)",
}.items():
    check(f"E {_label} still present", _probe in _SRC, _probe)

# ── F: บีบสั้นจริง — ไม่ใช่แค่ย้ายคำ ─────────────────────────────────────────
check("F rule is meaningfully shorter than the pre-cleanup ~1900-char version",
      len(_RULE) < 900, len(_RULE))

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
