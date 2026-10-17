"""เทสต์บั๊กรุนแรงจริงที่เจอจาก log ผู้ใช้ — Outfit: บวมซ้ำ 3 เท่าจนพังทั้งบรรทัด (sectioned format)

รันด้วย:  python -m story_generator.tests.test_sectioned_outfit_duplication

การสืบสวน (จำลองข้อมูลจริงจาก log — Dao/Krit, sectioned_detailed, PARTIALLY UNDRESSED):
ป้อน prompt ที่ Outfit: ถูกต้องสมบูรณ์แล้ว (ทั้ง Dao และ Krit ครบทุกชิ้น) เข้า
_enforce_fill_canonical() — ก่อนแก้ ได้ผลลัพธ์:

  "Outfit: Dao wearing silk collar Dao wearing white lace thigh-high stockings ...
   Dao wearing silk collar wearing black shirt Krit wearing dark trousers ...
   Dao wearing silk collar wearing black shirt ... Dao wearing silk collar, white
   lace thigh-high stockings, ... (ประโยคเดิมที่ถูกอยู่แล้ว)"

ชื่อ/ชุดของคนสองคนถูกสับกันจนอ่านไม่รู้เรื่อง ("Dao wearing silk collar wearing black
shirt") ซ้ำ 3 รอบเต็ม

รากของปัญหา (คนละจุดกับที่เคยแก้เรื่อง _WEARING_AT_RE ไปก่อนหน้า): _repair_prompt_clothing()
มีฟีเจอร์ "outfit lifted early" — ถ้าชุดถูกต้องครบแล้วแต่ตำแหน่งไม่ "early พอ" จะ copy ชุดไปแปะ
เพิ่มไว้หน้าอีกชุด ฟีเจอร์นี้ตั้งใจไว้สำหรับ narrative (ป้องกัน environment ยาวๆ กลบชุดจนโมเดล
ภาพไม่สนใจ) โดยเช็คด้วย _wearing_is_early() ที่ใช้ _before_long_env() — ตัดข้อความตาม ". "
แบบ narrative ล้วนๆ ไม่รู้จัก section label เลย สำหรับ sectioned_detailed ฟังก์ชันนี้จึงตอบ "ไม่
early" เสมอไม่ว่าจะจริงหรือไม่ ทำให้ _enforce_fill_canonical (ที่เรียกฟังก์ชันซ่อมนี้ผ่าน 3 รอบ
interleaved) แปะซ้ำ Outfit: ทุกรอบทั้งที่ถูกอยู่แล้ว

จุดที่สองที่ทำให้อาการหนักขึ้น: _insert_owner_outfits()'s sectioned branch เช็คว่า "มีอยู่แล้ว
หรือยัง" จาก head = _before_long_env(prompt) เดียวกัน (แทนที่จะเช็คจากเนื้อบรรทัด Outfit: เอง)
เช็คจึงเท็จเสมอด้วยเหตุผลเดียวกัน — สองจุดนี้รวมกันทำให้ทุกช็อต sectioned ที่มี clothing_state
โดนแปะซ้ำอย่างน้อย 1 ครั้ง และช็อตที่ enforcement วนหลายรอบ (เช่นตอน visibility gate ทำงานด้วย)
โดนซ้ำเพิ่มตามจำนวนรอบ

แก้สองจุด: (1) ปิดฟีเจอร์ "outfit lifted early" สำหรับ sectioned/sectioned_detailed ทั้งหมด
(ไม่มีแนวคิด "สายไป" สำหรับ format ที่มีตำแหน่งของตัวเองอยู่แล้ว), (2) เปลี่ยนเช็ค "มีอยู่แล้ว
หรือยัง" ใน _insert_owner_outfits ให้ดูจากเนื้อบรรทัด Outfit: เองแทน head แบบ narrative

ขอบเขต: เทสต์พฤติกรรมจริงของ _enforce_fill_canonical/_repair_prompt_clothing/_insert_owner_outfits
ด้วยข้อมูลที่จำลองจาก log จริง ไม่ยิง LLM จริง
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

# ── ข้อมูลจำลองจาก log จริง (Dao/Krit, sectioned_detailed, PARTIALLY UNDRESSED) ──
_SCENE = ("Dao lifts the hem of her bodysuit with both hands, tears flowing down her "
          "cheeks, while Krit holds the DSLR camera and speaks.")
_CHAR = ("2 people\nDao: female, 25, Thai, long slightly messy black hair with thin bangs, "
         "innocent face, fair skin, slender build.\n"
         "Krit: male, 30, Thai, short black hair, sharp eyes, athletic build.")
_CLOTHING = ("Dao — PARTIALLY UNDRESSED: silk collar, white lace thigh-high stockings, "
             "white lace bodysuit with cutouts and waist ties pushed out of place, still on "
             "the body; Krit — CLOTHED: black shirt, dark trousers, black shoes")
_PROP = "Krit — holds DSLR camera"
_SPACING = "NEAR: Dao and Krit near mirror"
_PROMPT_ALREADY_CORRECT = (
    "Camera: Medium shot, waist-up framing, angled to capture Dao facing the tall mirror "
    "with her reflection visible, Krit positioned behind her to the left holding the camera.\n\n"
    "Subject: Dao (25, Thai woman, long slightly messy black hair with thin bangs, innocent "
    "face, fair skin, slender build) stands before the mirror lifting the hem of her bodysuit "
    "with both hands, body oriented toward the mirror, head slightly tilted, mouth open, eyes "
    "on her reflection with tears flowing down her cheeks. Krit (30, Thai man, short black "
    "hair, sharp eyes, athletic build) stands behind Dao holding the DSLR camera, mouth open "
    "mid-sentence as he speaks.\n\n"
    "Outfit: Dao wearing silk collar, white lace thigh-high stockings, white lace bodysuit "
    "with cutouts and waist ties pushed out of place still on the body. Krit wearing black "
    "shirt and dark trousers, black shoes.\n\n"
    "Environment: Bedroom with dark-brown wooden wardrobe and tall mirror opposite, polished "
    "wood floor reflecting warm dim lamplight, single closed window.\n\n"
    "Lighting: Warm dim lamplight from bedside lamp casting golden shadows on skin and fabric.\n\n"
    "Climax: Dao lifting the hem of her bodysuit exposing herself while tears flowing while "
    "Krit speaks and holds the camera."
)


def _run():
    return L._enforce_fill_canonical(
        _PROMPT_ALREADY_CORRECT, scene_desc=_SCENE, char_desc=_CHAR, clothing_state=_CLOTHING,
        spacing=_SPACING, shot_scale="medium", prompt_style="sectioned_detailed", prop_state=_PROP,
    )


_OUT, _NOTES = _run()

# ── A: บั๊กหลัก — Outfit: ที่ถูกต้องอยู่แล้วต้องไม่ถูกแปะซ้ำเลย ──────────────
check("A a correct Outfit line produces zero repair notes (nothing was actually wrong)",
      _NOTES == [], _NOTES)
check("A the Outfit section is not duplicated (each garment phrase appears exactly once)",
      _OUT.count("silk collar") == 1 and _OUT.count("black shoes") == 1, _OUT)
check("A no cross-person name/garment mangling (\"Dao wearing silk collar wearing black shirt\")",
      "wearing silk collar wearing" not in _OUT.lower())
check("A the Outfit line text is byte-identical to what the model originally wrote",
      "Outfit: Dao wearing silk collar, white lace thigh-high stockings, white lace bodysuit "
      "with cutouts and waist ties pushed out of place still on the body. Krit wearing black "
      "shirt and dark trousers, black shoes.\n\n" in _OUT, _OUT)

# ── B: ยืนยันด้วย unit เล็กที่ตรงจุดกว่า — เรียก _repair_prompt_clothing ตรงๆ ──
_p2, _n2 = L._repair_prompt_clothing(
    _PROMPT_ALREADY_CORRECT, _CLOTHING, "sectioned_detailed", _SCENE, _CHAR)
check("B _repair_prompt_clothing alone: no 'outfit lifted early' note for sectioned format",
      not any("outfit lifted early" in n for n in _n2), _n2)
check("B _repair_prompt_clothing alone: prompt text unchanged when already correct",
      _p2 == _PROMPT_ALREADY_CORRECT)

# ── C: ระบบซ่อมของจริงยังใช้ได้เมื่อ Outfit: ขาดจริง (ไม่ใช่แค่ปิดฟีเจอร์ทิ้งเฉยๆ) ──
_prompt_missing = (
    "Camera: medium shot.\n\nSubject: Dao (25, Thai woman) stands before the mirror.\n\n"
    "Outfit: Dao wearing black leather jacket.\n\n"
    "Environment: room.\n\nLighting: warm.\n\nClimax: she looks up."
)
_p3, _n3 = L._repair_prompt_clothing(
    _prompt_missing, "Dao — CLOTHED: black leather jacket, white silk collar",
    "sectioned_detailed", "Dao stands before the mirror.", "1 person\nDao: female, 25, Thai.")
check("C a genuinely missing garment is still detected and repaired for sectioned format",
      any("unbound" in n and "silk collar" in n for n in _n3), _n3)
check("C the repair inserts exactly one clean mention, no duplication",
      _p3.count("silk collar") == 1, _p3)
check("C the pre-existing correct garment (jacket) is preserved, not overwritten",
      "black leather jacket" in _p3)

# ── D: narrative format ยังใช้ 'lift early' ได้ตามเดิม (ปิดเฉพาะ sectioned) ──
_narr_prompt = (
    "wide shot, the entire figure visible, Mia (28, slim Russian woman, black hair) stands "
    "by the tall window overlooking the city lights, gazing out into the night, black silk "
    "dress catching faint light. mia wearing black silk dress somewhere deep in a long "
    "environment paragraph that goes on and on describing the room in extensive detail with "
    "more than one hundred and eighty characters to trigger the long-env cutoff logic that "
    "this function uses to find the opening portion of a narrative prompt for lifting "
    "garments early so they read before the environment dump takes over entirely."
)
_p4, _n4 = L._repair_prompt_clothing(
    _narr_prompt, "Mia — CLOTHED: black silk dress", "narrative",
    "Mia stands by the window.", "1 person\nMia: female, 28, Russian, black hair.")
check("D narrative format: 'outfit lifted early' still fires when genuinely needed (untouched)",
      any("outfit lifted early" in n for n in _n4), _n4)

# ── E: ต่อสายเข้าโค้ดจริง — ยืนยันจุดแก้ทั้งสองมีอยู่ ──────────────────────
check("E _repair_prompt_clothing gates 'lift early' off for sectioned formats (guard #1)",
      '_sectioned = fmt in ("sectioned", "sectioned_detailed")' in _SRC
      and "if pieces and not _sectioned and not _wearing_is_early" in _SRC)
check("E the second 'lift early' branch also gates on sectioned format (guard #2)",
      'elif (pieces and fmt not in ("sectioned", "sectioned_detailed")' in _SRC)
check("E _insert_owner_outfits checks against the actual Outfit: line content, "
      "not the narrative-oriented _before_long_env head (guard #3)",
      'm_outfit = _re_clothing.search(r\'(?im)^Outfit:\\s*(.*)$\', prompt or "")' in _SRC
      and "_outfit_line = m_outfit.group(1)" in _SRC)

# ── F: ไม่มี regression — ของเดิมยังอยู่ครบ ─────────────────────────────────
check("F canonical-state / physical-consistency contracts untouched",
      "_CANONICAL_STATE_RULE" in _SRC and "PHYSICAL CONSISTENCY" in _SRC)
check("F the earlier _WEARING_AT_RE fix (narrative-style dedup) is still in place",
      r"(?=[.;]|\bwearing\b|$)" in _SRC)

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
