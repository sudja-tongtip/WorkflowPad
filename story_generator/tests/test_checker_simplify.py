"""เทสต์ว่า Checker เป็น safety net ไม่ใช่ระบบ semantic checklist คู่ขนานกับ Fill

รันด้วย:  python -m story_generator.tests.test_checker_simplify

พบว่า Checker มี "P) CROSS-FIELD CONSISTENCY" และ "Q) SOURCE-FACT PRESERVATION" ซึ่งพูดเรื่อง
เดียวกัน (scene_desc/clothing_state/prop_state/spacing เป็น canonical, ห้ามแก้ ให้แก้ prompt แทน)
ซ้ำกับหมวด B/C ที่มีอยู่แล้ว และซ้ำในตัวเองสองสไตล์ (P พูดเป็นตัวอย่าง, Q พูดเป็นลิสต์แห้ง) —
ทั้งสองหมวดถูกแทนที่ด้วยการ wire _CANONICAL_STATE_RULE (กฎเดียวกับที่ Fill ใช้อยู่แล้ว) เข้ามาแทน
ทั้งใน sys prompt หลักและตัว retry-ladder (editor_sys_slim)

เพิ่มด้วย: คำสั่งชัดว่า Checker ควรมองข้ามความต่างเล็กน้อย/สไตล์/omission ที่มองไม่เห็นที่ระยะนั้น —
"flag only material errors" — ซึ่งเดิมไม่มีระบุตรงไหนเลย มีแต่ "keep_original: true ถ้าถูก"

ขอบเขต: เทสต์ prompt-contract ยืนยันว่ากฎซ้ำถูกลบ กฎที่ใช้ร่วมถูกผูก และของเดิมที่ไม่ซ้ำยังอยู่ครบ
"""
import inspect

from .. import llm_engine as L

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


_SRC = inspect.getsource(L)

# ── A: กฎกลางถูกผูกเข้า Checker ทั้งสองรูปแบบ (primary + retry-ladder) ──────
check("A Checker's primary sys prompt carries the shared canonical-state rule",
      _SRC.count("{_CANONICAL_STATE_RULE}") >= 3)  # Fill + Checker primary + Checker slim
check("A Checker's retry-ladder (slim) variant also carries the shared rule",
      "{req_str if req_str else \"\"}{extra_str}\n{_CANONICAL_STATE_RULE}" in _SRC)

# ── B: P)/Q) redundant sections removed (both primary and slim variants) ───
check("B primary Checker: P) CROSS-FIELD CONSISTENCY section removed",
      "P) CROSS-FIELD CONSISTENCY (CRITICAL — repair the PROMPT, never the tracker):" not in _SRC)
check("B primary Checker: Q) SOURCE-FACT PRESERVATION section removed",
      "Q) SOURCE-FACT PRESERVATION (canonical fact > visual improvement" not in _SRC)
check("B slim Checker: P) CROSS-FIELD one-liner removed",
      "P) CROSS-FIELD: prompt must match scene_desc roster" not in _SRC)
check("B slim Checker: Q) SOURCE FACTS one-liner removed",
      "Q) SOURCE FACTS: left/right hand, prop owner/location" not in _SRC)

# ── C: "flag only material errors, ignore minor/stylistic" now explicit ────
check("C primary Checker: told to flag only material errors",
      "Flag only MATERIAL errors" in _SRC)
check("C primary Checker: told to ignore stylistic preference/minor differences",
      "Ignore\n  wording preference, minor stylistic differences" in _SRC
      or "wording preference, minor stylistic differences" in _SRC)
check("C primary Checker: harmless omission invisible at distance is not an issue",
      "harmless omission invisible at the assigned" in _SRC)
check("C primary Checker: repair means smallest fix, not re-authoring",
      "make the smallest rewrite" in _SRC and "repair, don't re-author" in _SRC)
check("C slim Checker: same material-error framing carried through",
      "Flag only material errors; a harmless omission invisible at this distance is not one" in _SRC)

# ── D: ไม่มี regression — เนื้อหาที่ไม่ซ้ำ (audit-specific, not pure restatement) ──
for _label, _probe in {
    "spacing evidence checklist (C3, distinct/checkable)": "VISUAL EVIDENCE, not a keyword",
    "prop attachment persistence (distinct failure class)": "PROP ATTACHMENT: how an object is held to the world",
    "framing repetition check (distinct)": "FRAMING REPETITION",
    "place bleed check (distinct)": "PLACE BLEED",
    "light source check (distinct)": "LIGHT NEEDS A SOURCE THAT EXISTS HERE",
    "tracker-word leak check (distinct, app-specific)": "N) TRACKER-WORD LEAK",
    "physical-consistency (anatomy) audit still present": "PHYSICAL CONSISTENCY — audit against the sheet independently",
    "keep_original true-when-correct contract intact": "If everything is correct",
}.items():
    check(f"D {_label} still present", _probe in _SRC, _probe)

# ── E: Director untouched by this task (out of scope changes verified minimal) ──
check("E Director's content-lock (its own equivalent canonical-authority rule) still present",
      "CURRENT PROMPT is content authority" in _SRC)
check("E Director does not duplicate the tracker-field-based canonical rule "
      "(it works from the resolved prompt, not raw fields — different, correct authority model)",
      _SRC.count("{_CANONICAL_STATE_RULE}") == 3)  # Fill + Checker x2, not a 4th copy in Director

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
