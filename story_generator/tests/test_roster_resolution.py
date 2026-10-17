"""เทสต์การ resolve roster เมื่อชีตตั้งชื่อเป็นคำบรรยาย และด่านกันโครง prompt พัง

รันด้วย:  python -m story_generator.tests.test_roster_resolution

เคสจริงที่พบ: Pass A ตั้งชื่อตัวละครว่า "Unnamed" ส่วน scene_desc เขียนว่า "The woman is
lounging..." ไม่มี alias เชื่อมกัน roster จึงว่าง แล้วทุกด่านหลังเงียบพร้อมกัน — identity ไม่ถูก
ซ่อม, Subject/Outfit หายจาก prompt, และด่านตรวจสุดท้ายถูกข้ามเพราะมันเช็คเฉพาะตอน roster ไม่ว่าง

P0-1 roster ที่ resolve ไม่ได้ต้อง log   P0-2 fallback ชีตคนเดียว (มี guard)
P1-3 validator ของ labeled section       P1-4 alias ของชื่อ "Unnamed" + อายุแบบช่วง
"""
from .. import llm_engine as L

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


# เคสจริงจากผู้ใช้ — ชีตชื่อ "Unnamed", อายุ "20s", scene เรียกว่า "The woman"
_SHEET1 = ("1 woman\nUnnamed: female, 20s, Asian (inferred), dark hair/dark eyes/"
           "medium-toned skin/athletic build, fully mechanical arms made of sleek carbon-fiber "
           "and black plating. default outfit: tight black pants over dark sports bra")
_SCENE1 = "The woman is lounging on the dark sofa while holding a whisky glass and cigarette."

# ── P1-4a: alias กลาง ๆ ต้องชี้กลับไปหาคนคนเดียวกัน ────────────────────────
_ident = L._character_identity_map(_SHEET1)
for _a in ("unnamed", "woman", "the woman", "young woman", "she", "her"):
    check(f"P1-4 alias '{_a}' resolves", _ident.get(_a) == "Unnamed", _ident)

# ── P1-4b: อายุแบบช่วง อ่านออก และไม่เสกอายุที่เรื่องไม่ได้บอก ─────────────
for _rest, _want in [
    ("female, 20s, Asian, dark hair", "20s"),
    ("female, mid-30s, Thai, black hair", "mid-30s"),
    ("male, early 40s, American", "early-40s"),
    ("female, 25, Thai, long hair", "25"),          # ตัวเลขตรง ๆ ยังทำงานเหมือนเดิม
    ("female, 18, Thai", "18"),
    ("หญิง อายุ 23 ปี ผมยาว", "23"),
]:
    check(f"P1-4 age from {_rest[:28]!r} -> {_want!r}",
          L._age_from_sheet_rest(_rest) == _want, repr(L._age_from_sheet_rest(_rest)))
check("P1-4 age range is not replaced by an invented number",
      "25" != L._age_from_sheet_rest("female, 20s, Asian"))

# ── P0-2: fallback ทำงานในเคสจริง ─────────────────────────────────────────
check("P0-2 real failing case now resolves",
      L._roster_from_scene(_SCENE1, _SHEET1) == ["Unnamed"],
      L._roster_from_scene(_SCENE1, _SHEET1))
check("P0-2 visible_roster agrees",
      L._visible_roster(_SCENE1, _SHEET1, "NEAR: Woman alone") == ["Unnamed"])

# ── P0-2 GUARD: ช็อตที่ไม่มีคน ต้องไม่ถูกยัดตัวละครเข้าไป ──────────────────
for _label, _scene in {
    "empty room":      "An empty room, dark sofa, gold light strips across the floor.",
    "prop close-up":   "Close-up of the whisky glass resting on the table.",
    "exterior":        "The city skyline at dusk, rain on the windows.",
    "blank":           "",
}.items():
    check(f"P0-2 guard: no one added for {_label}",
          L._roster_from_scene(_scene, _SHEET1) == [],
          L._roster_from_scene(_scene, _SHEET1))

# ── P0-2 GUARD: ชีตหลายคน ต้องไม่เดา ─────────────────────────────────────
_SHEET2 = ("2 women\nUnnamed: female, 20s, Thai, dark hair\n"
           "Other: female, 30s, Thai, red hair")
check("P0-2 guard: two-person sheet does not guess",
      L._roster_from_scene("The woman walks in", _SHEET2) == [],
      L._roster_from_scene("The woman walks in", _SHEET2))
check("P1-4 guard: ambiguous gender noun gets no generic alias",
      L._character_identity_map(_SHEET2).get("the woman") is None,
      L._character_identity_map(_SHEET2))

# ── ชื่อจริงยังต้องทำงานเหมือนเดิม (ไม่ regress) ──────────────────────────
_SHEET3 = "2 people\nDao: female, 22, Thai, long black hair\nKrit: male, 30, Thai, short hair"
check("no regression: named characters resolve by name",
      L._roster_from_scene("Dao hands the letter to Krit", _SHEET3) == ["Dao", "Krit"],
      L._roster_from_scene("Dao hands the letter to Krit", _SHEET3))
check("no regression: only the named person appears",
      L._roster_from_scene("Dao sits alone", _SHEET3) == ["Dao"])
check("no regression: scene with no cast name and 2 people stays empty",
      L._roster_from_scene("An empty courtyard", _SHEET3) == [])

# ── P0-1: roster ที่ resolve ไม่ได้ ต้องมี note ออกมา ─────────────────────
_p, _notes = L._enforce_fill_canonical(
    "Camera: wide shot.\n\nEnvironment: a room.",
    scene_desc="The woman walks in", char_desc=_SHEET2, prompt_style="sectioned_detailed",
)
check("P0-1 unresolved roster produces a loud note",
      any("roster CHECK FAILED" in n for n in _notes), _notes)
_p, _notes2 = L._enforce_fill_canonical(
    "Camera: wide shot.\n\nEnvironment: a room.",
    scene_desc="An empty courtyard", char_desc=_SHEET2, prompt_style="sectioned_detailed",
)
check("P0-1 a genuinely empty shot stays quiet",
      not any("roster CHECK FAILED" in n for n in _notes2), _notes2)

# ── P1-3: validator ของ labeled section ──────────────────────────────────
_BAD = ("Camera: Medium shot, angled down.\n\nEnvironment: a luxurious apartment.\n\n"
        "Lighting: gold light cuts across.\n\nClimax: the glass held aloft.")
_GOOD = ("Camera: Medium shot.\n\nSubject: Anya (25, athletic woman): reclining.\n\n"
         "Outfit: Anya wearing tight black pants.\n\nEnvironment: an apartment.\n\n"
         "Lighting: gold light.\n\nClimax: the glass held aloft.")
check("P1-3 catches the real broken prompt",
      L._missing_prompt_sections(_BAD, "sectioned_detailed", True) == ["Subject", "Outfit"],
      L._missing_prompt_sections(_BAD, "sectioned_detailed", True))
check("P1-3 complete prompt passes",
      L._missing_prompt_sections(_GOOD, "sectioned_detailed", True) == [])
check("P1-3 Subject/Outfit not demanded when nobody is in the shot",
      L._missing_prompt_sections(_BAD, "sectioned_detailed", False) == [])
check("P1-3 'sectioned' style is checked too",
      L._missing_prompt_sections(_BAD, "sectioned", True) == ["Subject", "Outfit"])
for _style in ("narrative", "tag_style", ""):
    check(f"P1-3 non-labeled style {_style or 'empty'!r} is not checked",
          L._missing_prompt_sections(_BAD, _style, True) == [])
check("P1-3 manga pages are exempt",
      L._missing_prompt_sections(_BAD, "sectioned_detailed", True, manga_page=True) == [])
check("P1-3 empty prompt is left to the existing empty-prompt path",
      L._missing_prompt_sections("", "sectioned_detailed", True) == [])
check("P1-3 label match is anchored to line start",
      L._missing_prompt_sections("Camera: x\n\nEnvironment: he studies the Subject: of the "
                                 "painting\n\nLighting: y\n\nClimax: z",
                                 "sectioned_detailed", True) == ["Subject", "Outfit"])

# ── IDENTITY LOCK ของเคสจริง ต้องมีอายุกลับมา ────────────────────────────
_lock = L._gender_lock_for_shot(_SHEET1, _SCENE1)
check("identity lock carries the age range", "(20s, woman)" in _lock, _lock[:200])

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
