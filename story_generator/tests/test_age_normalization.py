"""เทสต์ว่าอายุที่เขียนเป็นช่วง ("20s") ถูกตั้งเป็นตัวเลขเดียวครั้งเดียวต้นทาง ไม่ใช่ให้ Fill เดาเอง

รันด้วย:  python -m story_generator.tests.test_age_normalization

ปัญหาเดิม: Character Sheet บางโปรเจกต์ (Siang / Cyber Punk 4) มีอายุเป็น "20s" แทนตัวเลข แล้ว
Fill เคยถูกสั่งว่า "settle on one plausible number ... reuse everywhere" — เป็นคำสั่งให้โมเดล
เดาเลขเองทุกครั้งที่ถูกเรียก ซึ่งไม่มีอะไรการันตีว่าจะเดาเลขเดิมซ้ำข้าม call (โดยเฉพาะตอนแยก
เป็นหลาย Grok batch call ที่ context ไม่เห็นกัน) แก้โดยตั้งเลขให้ครั้งเดียวแบบ deterministic
ที่ชั้น Python ก่อนชีตจะกลายเป็น canonical แล้วบอก Fill ว่า "อายุมีเลขอยู่แล้ว ก็อปอย่างเดียว"

ขอบเขต: เทสต์ระดับ prompt contract + พฤติกรรมจริงของ _normalize_sheet_ages (ฟังก์ชัน pure,
ทดสอบตรงได้โดยไม่ต้องเรียก LLM)
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

# ── A: พฤติกรรมจริงของ _normalize_sheet_ages ────────────────────────────────
def _age_line(char_desc, idx=1):
    out, _ = L._normalize_sheet_ages(char_desc)
    return out.splitlines()[idx]

check("A '20s' settles to a fixed digit (midpoint)",
      _age_line("1 woman\nSiang: female, 20s, Thai.") == "Siang: female, 25, Thai.")
check("A 'early-30s' settles lower than the midpoint",
      _age_line("1 woman\nMia: female, early-30s, Russian.") == "Mia: female, 32, Russian.")
check("A 'late 40s' settles higher than the midpoint",
      _age_line("1 woman\nDao: female, late 40s, Thai.") == "Dao: female, 48, Thai.")
check("A a sheet that already has a digit age is left untouched",
      _age_line("1 woman\nAnna: female, 25, Thai.") == "Anna: female, 25, Thai.")
check("A a sheet with no parseable age at all is left untouched",
      _age_line("1 woman\nElla: female, unknown age, elf.") == "Ella: female, unknown age, elf.")

check("A settling is deterministic — same input always settles to the same number",
      L._normalize_sheet_ages("1 woman\nX: female, 20s, Thai.")[0]
      == L._normalize_sheet_ages("1 woman\nX: female, 20s, Thai.")[0])

check("A produces a log note naming the character and the change",
      "Siang: 20s" in L._normalize_sheet_ages("1 woman\nSiang: female, 20s, Thai.")[1])
check("A no note when nothing changed",
      L._normalize_sheet_ages("1 woman\nAnna: female, 25, Thai.")[1] == "")

check("A multi-character sheet: only the range-age entries change",
      L._normalize_sheet_ages(
          "2 women\nAnna: female, 25, Thai.\nMia: female, 30s, Russian."
      )[0].splitlines() == ["2 women", "Anna: female, 25, Thai.", "Mia: female, 35, Russian."])

# ── B: ต่อสายเข้าจุดที่ชีตกลายเป็น canonical (3 checkpoint) ─────────────────
check("B wired into Fill's own sheet-normalization checkpoint",
      "_repair_sheet_count(char_desc)\n    char_desc, _age_note = _normalize_sheet_ages(char_desc)" in _SRC)
check("B wired into both Outline-A sheet-normalization checkpoints",
      _SRC.count("_age_note1 = _normalize_sheet_ages(char_from_llm)") == 1
      and _SRC.count("_age_note = _normalize_sheet_ages(char_from_llm)") == 1)
check("B age note gets logged (not silently dropped)",
      _SRC.count('await on_log(f"🎂 {_age_note') >= 2)

# ── C: Fill ไม่ถูกสั่งให้เดาอายุเองอีกต่อไป ──────────────────────────────────
check("C Fill no longer told to settle/guess an age itself",
      "settle on one plausible number" not in _SRC)
check("C Fill told age is already a settled digit — copy, don't re-guess",
      "Age is a settled digit already on the sheet" in _SRC
      and "never re-guess or re-settle a number yourself" in _SRC)
check("C Fill still forbidden from writing '22-year-old' wording",
      'digits only, never "18-year-old"' in _SRC)

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
