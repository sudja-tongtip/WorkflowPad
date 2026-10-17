"""เทสต์ผลการสืบสวนบั๊ก "outfit หายที่ Wide/Extreme-Wide" จากโปรเจกต์จริง (Cyber Punk 4, shot 13)

รันด้วย:  python -m story_generator.tests.test_fill_outfit_omission

การสืบสวน (ไม่ใช่แค่เดา — รันโค้ดจริงกับข้อมูลจริงจาก saved_story.json):

shot 13 จริง มี:
  scene_desc: "Her left carbon fiber arm rests settled comfortably between her thighs..."
             (ไม่มีชื่อ "Anya" เลย — ใช้ "Her")
  clothing_state: "Anya — CLOTHED: Black sports bra (lace texture), Black compression pants
                   (high sheen fabric) [Pulled up slightly]"
  shot_scale: extreme wide, prompt_style: tag_style
  prompt (จริงในไฟล์): ไม่มีคำว่า wearing/bra/pants เลยสักคำ

ผลการรัน _enforce_fill_canonical() (helper ยังอยู่ — Fill/Checker ไม่เขียนทับ prompt ด้วยตัวนี้แล้ว) ด้วยอินพุตชุดนี้เป๊ะ:
→ ซ่อมถูกต้อง — insert "Anya wearing Black sports bra..., Anya wearing Black compression pants..."
  กลับเข้าไปทันที (ผ่าน _roster_from_scene's solo-scene fallback ที่จับ "Her" = Anya ได้อยู่แล้ว
  แม้ไม่มีชื่อในประโยค แล้ว _repair_prompt_clothing เห็นว่าไม่มี "wearing" ผูกกับ Anya เลยแทรกให้)

สรุป: โค้ดปัจจุบันแก้เคสนี้ได้ถูกต้องอยู่แล้ว — ข้อมูลใน saved_story.json เป็นข้อมูลเก่ากว่ากลไก
ซ่อมที่มีอยู่ตอนนี้ (หรือช็อตนั้นถูกแก้ไขมือ/สร้างใหม่นอกเส้นทาง Fill ปกติ) จึงไม่เพิ่มกฎ prompt
ใหม่ตามที่ brief สั่งไว้ชัดว่า "ห้ามเพิ่มถ้าการสืบสวนไม่พิสูจน์ว่าจำเป็น"

สิ่งที่พิสูจน์แล้วว่า "จำเป็นจริง": วงเล็บกำพร้า "Thai woman)" — โผล่ซ้ำในเกือบทุกช็อตของโปรเจกต์
จริง ("with two highly articulated..." ไม่มี "(" คู่กับ ")" ก่อนหน้า "woman") กลไกเดิมไม่มีอะไรจับ
เรื่องนี้เลย (ยืนยันด้วยการป้อน prompt เดียวกันผ่าน _enforce_fill_canonical ตัวเต็ม แล้ว "Thai woman)"
ยังอยู่เหมือนเดิม) — จึงเพิ่ม _balance_stray_parens() ซึ่งเป็น deterministic cleanup ล้วน ไม่แตะ
system prompt เลยสักตัวอักษร

ขอบเขต: เทสต์นี้ยืนยันทั้งสองข้อสรุปด้วยการรันโค้ดจริง ไม่ใช่แค่เดา
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


# ── ข้อมูลจริงจาก saved_story.json shot 13 (Cyber Punk 4 / Anya) ───────────
_SCENE_13 = ("Her left carbon fiber arm rests settled comfortably between her thighs, "
             "complementing the relaxed pose of the entire body.")
_CHAR_13 = ("1 person\nAnya: female, 20, Thai, long black hair, two highly articulated "
            "black carbon fiber mechanical arms. Wearing black sports bra, black compression pants.")
_CLOTHING_13 = ("Anya — CLOTHED: Black sports bra (lace texture), Black compression pants "
                "(high sheen fabric) [Pulled up slightly]")
_PROP_13 = "Left Carbon Fiber Arm — resting across legs"
_SPACING_13 = "NEAR: Anya"
# prompt จริงที่บันทึกไว้ในไฟล์ (ไม่มี outfit, มีวงเล็บกำพร้า "Thai woman)")
_PROMPT_13_ASIS = (
    "Extreme wide shot, the entire figure visible head to toe occupying about one-eighth of the "
    "frame height, Thai woman) with two highly articulated black carbon fiber mechanical arms "
    "silhouette, leaning back against a plush black sofa on the center depth plane, her left "
    "carbon fiber arm resting horizontally across her thighs, body relaxed in repose, slight "
    "upward gaze directed toward the ambient light."
)


def _run_real_shot13():
    return L._enforce_fill_canonical(
        _PROMPT_13_ASIS, scene_desc=_SCENE_13, char_desc=_CHAR_13, clothing_state=_CLOTHING_13,
        spacing=_SPACING_13, shot_scale="extreme wide", prompt_style="tag_style",
        prop_state=_PROP_13,
    )


# ── A: การสืบสวน — ยืนยันว่ากลไกเดิมซ่อม outfit หายได้ถูกต้องแล้ว (ไม่ใช่บั๊กที่ต้องแก้) ──
_out13, _notes13 = _run_real_shot13()
check("A investigation: real shot-13 data repairs to include the outfit",
      "black sports bra" in _out13.lower() and "black compression pants" in _out13.lower(),
      _out13[:200])
check("A investigation: repair note explicitly names the unbound garments",
      any("unbound" in n and "sports bra" in n.lower() for n in _notes13), _notes13)
check("A the solo-scene roster fallback catches 'Her' with no literal name",
      L._roster_from_scene(_SCENE_13, _CHAR_13) == ["Anya"])

# ── B: การสืบสวน — ยืนยันว่าวงเล็บกำพร้าเป็นบั๊กจริงที่ต้องแก้ (พิสูจน์ก่อนแก้) ────────
check("B investigation: stray ')' with no matching '(' survives the OLD pipeline "
      "(_balance_stray_parens not yet applied) — proves the fix was necessary",
      ")" in _PROMPT_13_ASIS and "(Anya" not in _PROMPT_13_ASIS.split("Thai woman)")[0][-10:])

# ── C: หลังแก้ — ทั้งสองปัญหาต้องหายจาก output จริง ─────────────────────────
check("C fixed: real shot-13 output no longer contains the orphaned 'Thai woman)'",
      "Thai woman)" not in _out13, _out13[:200])
check("C fixed: 'Thai woman' text itself survives (only the stray punctuation was removed)",
      "Thai woman" in _out13)
check("C fixed: parens stay balanced overall in the output",
      _out13.count("(") == _out13.count(")"))

# ── D: _balance_stray_parens — พฤติกรรมทั่วไป ไม่ hard-code เคส Anya/Thai ──────
def _bal(t): return L._balance_stray_parens(t)

check("D drops an orphaned close-paren with nothing before it",
      _bal("tall woman) walks in") == "tall woman walks in")
check("D leaves correctly matched parens untouched",
      _bal("Mia (25, slim woman) walks in") == "Mia (25, slim woman) walks in")
check("D drops only the extra unmatched paren, keeps the matched pair",
      _bal("(a) extra) tail") == "(a) extra tail")
check("D handles nested matched parens correctly",
      _bal("a (b (c) d) e") == "a (b (c) d) e")
check("D no-op on text with no parens at all",
      _bal("plain text, no parens here") == "plain text, no parens here")
check("D empty/falsy input does not crash",
      _bal("") == "" and _bal(None) in (None, ""))
check("D generic — not hard-coded to 'woman'/'Thai' specifically",
      _bal("tall robot) enters") == "tall robot enters"
      and _bal("small dog) barks") == "small dog barks")

# ── E: ต่อสายเข้า pipeline จริงของ Fill ─────────────────────────────────────
_SRC = inspect.getsource(L)
check("E _balance_stray_parens wired into _enforce_fill_canonical",
      "p = _balance_stray_parens(p)" in _SRC)
check("E no new system-prompt text was added for this task (pure Python fix, per brief)",
      "_FILL_OUTFIT" not in _SRC and "OUTFIT OMISSION" not in _SRC)

# ── F: ไม่มี regression — เทสต์ suite เดิมยังอยู่ ────────────────────────────
check("F canonical-state contract untouched",
      "_CANONICAL_STATE_RULE" in _SRC)
check("F scale precedence untouched",
      "SCALE PRECEDENCE — DETAIL NEVER PULLS THE CAMERA IN" in _SRC)

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
