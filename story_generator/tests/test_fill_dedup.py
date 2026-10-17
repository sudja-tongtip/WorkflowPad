"""เทสต์บั๊กจริงที่เจอจากการสืบสวน "Fill output ซ้ำ/มี Flaw-Desire-Fear หลุด"

รันด้วย:  python -m story_generator.tests.test_fill_dedup

การสืบสวน: brief ยกตัวอย่าง "wearing X ... Flaw ... Desire ... wearing X ... Flaw ... Desire ..."
ทดสอบ _dedupe_wearing_phrases() ตรงๆ ด้วยข้อความรูปแบบนี้ พบว่า **ไม่ถูกลบซ้ำเลย** — สาเหตุ:
_WEARING_AT_RE เดิมจับ "what" ยาวไปจนถึงจุด/อัฒภาคเท่านั้น ("[^.;]+?") ข้อความจริงที่คั่นด้วยจุลภาค
ล้วน (ไม่มี . หรือ ; คั่นระหว่างท่อนเสื้อผ้ากับ personality-metadata ที่หลุดเข้ามา) ทำให้ "what" กลืน
ข้อความยาวเกินจนถึงคำว่า "wearing" ตัวถัดไปด้วย — finditer() จึงเจอ match เดียว ไม่ใช่สอง ระบบเทียบ
ของซ้ำด้านล่าง (เทียบ prefix ของแต่ละ match) เลยไม่มีท่อนที่สองให้เทียบ ปล่อยข้อความซ้ำผ่านทั้งดุ้น

แก้: เพิ่มเงื่อนไขหยุดที่ "wearing" ตัวถัดไปด้วย (ไม่ต้องรอจุด/อัฒภาค) — ตอนนี้ finditer() เจอสอง
match แยกกันถูกต้อง ระบบเทียบ prefix เดิมทำงานได้ตามที่ออกแบบไว้

ส่วน Flaw/Desire/Fear ที่หลุดเข้ามาเอง (ไม่ใช่ปัญหาซ้ำ แต่เป็นเนื้อหาที่ไม่ควรอยู่ใน prompt เลย)
เป็นคนละปัญหา แก้ด้วยการเพิ่มประโยคหนึ่งใน _CANONICAL_STATE_RULE (LLM instruction) แทน — ตรงตามที่
brief แยกสองปัญหานี้ออกจากกัน (§2 = LLM instruction, §11 = deterministic dedup)

ขอบเขต: เทสต์นี้ยืนยันทั้งบั๊กเดิม (rerun ผ่าน regex เก่าจำลอง) และผลหลังแก้ ด้วยข้อความจริงจาก brief
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

# ── ข้อความจำลองตามตัวอย่าง BAD ของ brief เป๊ะ ─────────────────────────────
_BAD_DUPLICATED = (
    "The Young Woman wearing white lace bodysuit, an ivory silk collar, and white lace thigh-high "
    "stockings, personality flaw impulsive and reckless, desires validation from others, fears "
    "abandonment, The Young Woman wearing white lace bodysuit, an ivory silk collar, and white "
    "lace thigh-high stockings, kneeling on the bed with a longing expression."
)

# ── A: การสืบสวน — regex เดิม (ก่อนแก้) ไม่เจอ match ที่สอง ──────────────────
_OLD_WEARING_AT_RE = _re.compile(r'\bwearing\s+(?P<what>[^.;]+?)(?=[.;]|$)', _re.I)


def t_old_regex_only_finds_one_match():
    matches = list(_OLD_WEARING_AT_RE.finditer(_BAD_DUPLICATED))
    check("A investigation: the OLD regex pattern collapses both 'wearing' clauses into one match "
          "(proves why dedup never fired)",
          len(matches) == 1, len(matches))


t_old_regex_only_finds_one_match()

# ── B: หลังแก้ — regex ปัจจุบันเจอสอง match แยกกัน ──────────────────────────
def t_new_regex_finds_two_matches():
    matches = list(L._WEARING_AT_RE.finditer(_BAD_DUPLICATED))
    check("B fixed: current _WEARING_AT_RE finds both 'wearing' clauses as separate matches",
          len(matches) == 2, len(matches))


t_new_regex_finds_two_matches()

# ── C: หลังแก้ — _dedupe_wearing_phrases ลบท่อนซ้ำออกจริง (พฤติกรรมจริง ไม่ใช่แค่ regex) ──
def t_dedupe_removes_the_repeated_outfit_clause():
    out = L._dedupe_wearing_phrases(_BAD_DUPLICATED, "narrative")
    check("C fixed: only one 'wearing white lace bodysuit' clause survives",
          out.lower().count("wearing white lace bodysuit") == 1, out)
    check("C fixed: the trailing action after the duplicate ('kneeling on the bed...') survives",
          "kneeling on the bed" in out.lower())
    check("C fixed: the owner's name still precedes the surviving action",
          "the young woman kneeling" in out.lower() or "young woman kneeling" in out.lower(), out)
    check("C output is meaningfully shorter than the input (real duplication removed)",
          len(out) < len(_BAD_DUPLICATED))


t_dedupe_removes_the_repeated_outfit_clause()

# ── D: ไม่ hard-code เคสนี้ — เคสทั่วไปสองคนคนละชุดต้องไม่โดนตัดผิด ──────────
def t_two_different_people_not_merged_or_corrupted():
    two_people = "Lin Se wearing a red silk dress, Anna wearing a blue cotton shirt, standing by the window."
    out = L._dedupe_wearing_phrases(two_people, "narrative")
    check("D two different people's outfits both survive intact",
          "red silk dress" in out and "blue cotton shirt" in out, out)
    check("D nothing got merged/corrupted between the two clauses",
          out == two_people, out)


def t_single_long_outfit_list_untouched():
    single = "Mia wearing a black leather jacket, dark jeans, and ankle boots, walking down the street."
    out = L._dedupe_wearing_phrases(single, "narrative")
    check("D a single non-repeated long outfit list is left completely unchanged",
          out == single, out)


t_two_different_people_not_merged_or_corrupted()
t_single_long_outfit_list_untouched()

# ── E: Flaw/Desire/Fear leak — LLM instruction ถูกเพิ่มแล้ว ────────────────
check("E Fill's canonical-state rule now names flaw/desire/fear as non-visual metadata",
      "personality flaw, desire, fear" in _SRC)
check("E rule explicitly forbids copying the flaw/desire/fear phrase into the prompt",
      "never copy the flaw/desire/fear phrase itself" in _SRC)
check("E rule tells Fill to translate metadata into a physical result instead of pasting it",
      "Translate it into a visible physical result" in _SRC)

# ── F: don't-invent-elements rule (furniture/mirror/light not in state) ────
check("F Fill told not to invent furniture/mirrors/light/objects/gestures the state doesn't give",
      "DRAW ONLY WHAT THE STATE GIVES YOU" in _SRC)
check("F invention rule stays generic (examples, not an exhaustive whitelist)",
      "a piece of furniture, a mirror, a light source, an" in _SRC)

# ── G: ไม่มี regression — กฎ/ฟังก์ชันเดิมยังอยู่ครบ ─────────────────────────
check("G canonical-state rule still covers clothing/props/spacing/scale authority",
      "clothing_state defines" in _SRC and "prop_state defines" in _SRC)
check("G outfit dedup still runs unconditionally inside _dedupe_wearing_phrases",
      "_dedupe_outfit_clauses(prompt or \"\")" in _SRC)
check("G tag_style's separate token-level dedup pass untouched",
      'if fmt != "tag_style":\n        return p' in _SRC)

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
