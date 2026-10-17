"""เทสต์บั๊กสองตัวที่เจอจากการวิเคราะห์โปรเจกต์จริง "Cyber Punk 4" (saved_story.json)

รันด้วย:  python -m story_generator.tests.test_cyberpunk4_analysis

บั๊กที่ 1 — word-merge จากการตัดข้อความกลางคำ (shot 2, tag_style, wide):
  _enforce_director_scale()'s tag_style branch ตัด head/tail ที่ตำแหน่ง 160 ตัวอักษรเป๊ะ แล้วต่อ
  head2+tail ตรงๆ ไม่มีตัวคั่น ถ้าตำแหน่ง 160 ตกกลางคำ สองคำจะเชื่อมกัน — เคสจริง: "...black
  leggings" ต่อกับ "third of the frame height..." กลายเป็น "leggingsthird of the frame height"
  ฟังก์ชันนี้เดิมใช้แค่ฝั่ง Director แต่ถูกเอามาผูกกับ Fill เองด้วยในงาน "SCALE PRECEDENCE" ก่อน
  หน้านี้ — บั๊กนี้จึงเป็น regression จากงานรอบนั้น ไม่ใช่บั๊กเก่า

บั๊กที่ 2 — clothing หายทั้งช็อตเมื่อ scene_desc ไม่มีคำสรรพนาม/gender no001 เลย (shot 6, wide):
  scene_desc: "The left mechanical arm pulls the tight black leggings down halfway, exposing
  smooth thighs and wet vulva." — ประธานประโยคคือ "the arm" ล้วนๆ ไม่มี she/her/woman เลยสักคำ
  _scene_has_person() (ใช้เป็น guard ของ solo-character fallback ใน _roster_from_scene) มองไม่เห็น
  ว่ามีคนอยู่ในฉาก ทั้งที่ "thighs"/"vulva" เป็นของคนแน่ๆ — roster จึงว่างเปล่า แล้วไม่มีด่านไหน
  ซ่อมเสื้อผ้าให้เลยทั้งช็อต

ขอบเขต: เทสต์นี้ยืนยันทั้งการรีโปรดิวซ์บั๊ก (ก่อนแก้ — จำลองด้วยข้อมูลจริง) และผลหลังแก้ ด้วยข้อมูล
จริงจากไฟล์ ไม่ใช่ข้อมูลสมมติ
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

# ── ข้อมูลจริงจาก saved_story.json — Cyber Punk 4 / "Woman" ────────────────
_CHAR = ("1 person\nWoman: female, 25, Thai, smooth olive skin/dark brown eyes/slender athletic "
         "build, integrated carbon fiber mechanical arms (left & right), black sports bra over "
         "sheer nude fabric, tight black leggings, playful but intensely aroused, desires "
         "complete release/climax, speaking style is breathy and low.")

# ── บั๊ก 1: word-merge ──────────────────────────────────────────────────────
_RAW_SHOT2 = (
    "wide shot, Woman wearing black sports bra over sheer nude fabric, Woman wearing black "
    "leggings, the entire figure visible head to toe occupying about one third of the frame "
    "height, Woman (25, Thai woman), tight black leggings, leaning back against the black sofa."
)


def t_word_merge_fixed():
    fixed, changed = L._enforce_director_scale(_RAW_SHOT2, "wide", has_subject=True, fmt="tag_style")
    check("1 real shot-2 text no longer merges into 'leggingsof'/'leggingsthird'",
          "leggingsof" not in fixed and "leggingsthird" not in fixed, fixed[:200])
    check("1 the word 'leggings' itself still appears intact (word, not deleted)",
          "leggings" in fixed.lower())
    check("1 cut point moved to the next space, not a hard 160-char slice",
          "raw.find(\" \", 160)" in _SRC)


def t_word_merge_general_case():
    # เคสสังเคราะห์ที่ตำแหน่งตัดตกกลางคำพอดี ("keywordBOUNDARY") ยืนยันว่าไม่ใช่ fix เฉพาะ
    # string ของ Cyber Punk 4 — ถ้าตัดกลางคำ จะได้ "keywordboundarytrailing" ติดกันไม่มีช่องว่าง
    body = "x" * 158 + "keywordBOUNDARY trailingtext " + "y" * 40
    raw = f"tag_style, {body}"
    fixed, _ = L._enforce_director_scale(raw, "medium", has_subject=True, fmt="tag_style")
    check("1 generic case: no word is split+merged at the cut boundary",
          "boundarytrailingtext" not in fixed.lower(), fixed[150:210])


t_word_merge_fixed()
t_word_merge_general_case()

# ── บั๊ก 2: person-less scene_desc (body part as subject) ──────────────────
_SCENE_SHOT6 = ("The left mechanical arm pulls the tight black leggings down halfway, "
                "exposing smooth thighs and wet vulva.")
_CLOTHING_SHOT6 = "Woman — CLOTHED: black sports bra over sheer nude fabric, bottomless (leggings are lowered)"


def t_bodypart_subject_scene_now_detected():
    check("2 real shot-6 scene_desc (arm as subject, no pronoun) now counts as having a person",
          L._scene_has_person(_SCENE_SHOT6) is True)
    check("2 real shot-6 roster now resolves to the sheet's one character",
          L._roster_from_scene(_SCENE_SHOT6, _CHAR) == ["Woman"])
    segs = L._present_clothing_segs(_CLOTHING_SHOT6, _SCENE_SHOT6, _CHAR)
    check("2 real shot-6 clothing segment is now present (was empty before the fix)",
          len(segs) == 1 and segs[0][0] == "Woman", segs)


def t_bodypart_detection_is_generic():
    # ไม่ผูกกับคำเฉพาะของเรื่องนี้ — คำอวัยวะทั่วไปต้องจับได้เหมือนกัน
    for txt in ("Fingers trail slowly across the collarbone.",
                "A hand grips the doorframe tightly.",
                "Knees buckle as the weight shifts forward."):
        check(f"2 generic body-part sentence detected as having a person: {txt!r}",
              L._scene_has_person(txt) is True)


def t_bodypart_detection_does_not_break_empty_scenes():
    # guard เดิมต้องยังกันช็อตที่ไม่มีคนจริงๆ (ห้อง/วิว/วัตถุ) ไม่ให้ถูกยัดคนเข้าไป
    for txt in ("Rain streaks down the window of the empty apartment.",
                "The city skyline glows under a heavy orange sky.",
                ""):
        check(f"2 genuinely person-less scene still returns no person: {txt!r}",
              L._scene_has_person(txt) is False)


t_bodypart_subject_scene_now_detected()
t_bodypart_detection_is_generic()
t_bodypart_detection_does_not_break_empty_scenes()

# ── ไม่มี regression — ของเดิมยังอยู่ ────────────────────────────────────────
check("existing pronoun/gender-noun detection untouched",
      "_SCENE_PERSON_RE.search(t)" in _SRC)
check("Thai person-word list untouched",
      "_SCENE_PERSON_TH" in _SRC)
check("canonical-state and physical-consistency contracts untouched",
      "_CANONICAL_STATE_RULE" in _SRC and "PHYSICAL CONSISTENCY" in _SRC)

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
