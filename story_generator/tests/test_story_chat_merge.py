"""เทสต์ว่า Story Chat กับ Raw Merge ใช้ merge ชุดเดียวกัน และพรอมป์ Chat สอนครบตามที่ตั้งใจ

รันด้วย:  python -m story_generator.tests.test_story_chat_merge

ปัญหาเดิม: Chat มีรายการฟิลด์ของตัวเอง (8 ฟิลด์ต่อช็อต / 4 ฟิลด์ระดับเรื่อง) ส่วน Raw Merge
มี 14/8 คำสั่งที่แตะ place/env/spacing/covers/expression/shot_scale หรือ props/events/
clothing_arc/physical_state_plan จึงถูกดรอปเงียบ ๆ ตอน merge โดยไม่มีคำเตือน

เทสต์นี้อ่าน source ด้วย ast (ฟังก์ชันพวกนี้อยู่ใน main() จึง import ตรง ๆ ไม่ได้) — จุดประสงค์คือ
กันไม่ให้สองทางแยกจากกันอีก ถ้ามีคนเพิ่มฟิลด์ที่เดียวแล้วลืมอีกที่ เทสต์นี้ต้องแดง
"""
import ast
import re
from pathlib import Path

# เทสต์นี้อ่าน main_ui.py เป็น "ข้อความ" แล้ว parse เอง ไม่ได้ import (จะได้ไม่ต้องลาก flet
# มาทั้งชุด) — ชี้ขึ้นไปหนึ่งชั้นจากโฟลเดอร์ tests ไปที่ตัวแพ็กเกจ story_generator
_SRC_PATH = Path(__file__).resolve().parent.parent / "main_ui.py"
_SRC = _SRC_PATH.read_text(encoding="utf-8-sig")   # main_ui.py มี BOM
_TREE = ast.parse(_SRC)

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


def _find_func(name):
    for node in ast.walk(_TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _const_strs(node) -> set:
    """สตริงคงที่ทุกตัวในซับทรี — ใช้ดูว่าฟังก์ชันพูดถึงคีย์ไหนบ้าง"""
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _assigned_tuple(name) -> tuple:
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            try:
                return tuple(ast.literal_eval(node.value))
            except Exception:
                return ()
    return ()


# ── A: รายการฟิลด์ของช็อต — แหล่งเดียว ─────────────────────────────────────
_SHOT_FIELDS = _assigned_tuple("_SHOT_MERGE_FIELDS")
_EXPECTED = ("place", "env", "spacing", "covers", "scene_desc", "mood", "expression",
             "clothing_state", "prop_state", "narration", "speech_char", "speech_text",
             "shot_scale", "prompt")
check("A _SHOT_MERGE_FIELDS still holds the full shot field set",
      set(_SHOT_FIELDS) == set(_EXPECTED),
      f"missing={set(_EXPECTED)-set(_SHOT_FIELDS)} extra={set(_SHOT_FIELDS)-set(_EXPECTED)}")

# สิ่งที่ส่งให้ Chat อ่าน ต้องเป็นสิ่งที่ Chat เขียนกลับได้ ไม่งั้นมันเห็นแต่แก้ไม่ได้
_build_raw = _find_func("_build_raw_data")
check("A _build_raw_data exists", _build_raw is not None)
if _build_raw:
    _emitted = _const_strs(_build_raw)
    _gap = set(_SHOT_FIELDS) - _emitted
    check("A every mergeable shot field is also sent to the assistant", not _gap, _gap)

# ── B: Chat merge ต้องเรียก _merge_raw_data ไม่ใช่มี logic ของตัวเอง ───────
_chat_merge = _find_func("_story_chat_merge_shots")
check("B _story_chat_merge_shots exists", _chat_merge is not None)
if _chat_merge:
    _calls = {n.func.id for n in ast.walk(_chat_merge)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    check("B chat merge delegates to _merge_raw_data", "_merge_raw_data" in _calls, _calls)
    # ถ้ามันยังมีรายการฟิลด์ของตัวเอง แปลว่ายังไม่ได้รวมทางจริง
    _own = _const_strs(_chat_merge) & set(_EXPECTED)
    check("B chat merge no longer carries its own field list", not _own, _own)
    _body = [n for n in _chat_merge.body if not isinstance(n, ast.Expr)]
    check("B chat merge is a thin delegation", len(_body) == 1, ast.dump(_chat_merge)[:200])

# ── C: คีย์ระดับเรื่องที่ Chat ยอมรับ ต้องตรงกับที่ merge รองรับจริง ───────
_TOP_KEYS = ("story", "story_summary", "character", "scene",
             "props", "events", "clothing_arc", "physical_state_plan")
_m = re.search(r"_known_keys = \((.*?)\)", _SRC, re.S)
_known = set(re.findall(r'"([a-z_]+)"', _m.group(1))) if _m else set()
check("C _known_keys accepts every project-level field", set(_TOP_KEYS) <= _known,
      set(_TOP_KEYS) - _known)
check("C _known_keys still accepts shots", "shots" in _known, _known)

_merge_raw = _find_func("_merge_raw_data")
if _merge_raw:
    _handled = _const_strs(_merge_raw)
    _gap = set(_TOP_KEYS) - _handled
    check("C every accepted key is actually handled by the merge", not _gap, _gap)

# ── D: สรุปใน preview ต้องนับการแก้ระดับเรื่อง ไม่ใช่แค่ช็อต ───────────────
_summary_fn = _find_func("_story_chat_change_summary")
check("D change-summary helper exists", _summary_fn is not None)
_m2 = re.search(r"_CHAT_TOP_LABELS = \{(.*?)\}", _SRC, re.S)
_labelled = set(re.findall(r'"([a-z_]+)":', _m2.group(1))) if _m2 else set()
check("D every project-level field has a preview label", set(_TOP_KEYS) <= _labelled,
      set(_TOP_KEYS) - _labelled)
check("D preview title no longer hardcodes a shot count",
      'f"📝 Proposed edits — {_story_chat_change_summary(obj)}' in _SRC)

# ── E: พรอมป์ Chat สอนครบตาม brief ────────────────────────────────────────
_sys_fn = _find_func("_build_story_chat_sys")
check("E _build_story_chat_sys exists", _sys_fn is not None)
_sys_txt = " ".join(sorted(_const_strs(_sys_fn))) if _sys_fn else ""
for _label, _probe in {
    "supervising role":        "supervising assistant for a story-to-image project",
    "three modes":             "THREE MODES",
    "discuss mode is JSON-free": "NEVER output JSON here",
    "diagnose wide / edit narrow": "DIAGNOSE WIDE, EDIT NARROW",
    "scope discipline":        "Keep every edit scoped to what was asked",
    "character first":         "CHARACTER FIRST, SHOT SECOND",
    "salience is not existence": "NOT EMPHASIZED IS NOT ABSENT",
    "visibility aware":        "out of "
                               "frame and does not belong in the prompt",
    "no verbatim sheet copy":  "do not paste the character sheet in",
    "no invention":            "invent nothing it does not",
    "no trait transfer":       "never moved onto someone else",
    "anatomy vs clothing":     "Keep anatomy, clothing, temporary state, action and props separate",
    "minimal change":          "Change the smallest amount that fixes the problem",
    "insert_after":            "insert_after",
    "no silent sanitizing":    "never quietly add clothing",
    "keep style and format":   "do not turn a",
    "user language":           "Answer in the user's language",
    "self-check before sending": "Before you send a patch",
}.items():
    check(f"E prompt teaches {_label}", _probe in _sys_txt, _probe)

# พรอมป์ต้องบอกฟิลด์ที่เขียนได้จริงครบ ไม่งั้นโมเดลไม่รู้ว่าแตะ camera/spacing ได้
_missing = [f for f in _EXPECTED if f not in _sys_txt]
check("E prompt lists every writable shot field", not _missing, _missing)
_missing_top = [f for f in _TOP_KEYS if f not in _sys_txt]
check("E prompt lists every writable project field", not _missing_top, _missing_top)

# ── F: ไม่ auto-apply — Apply/Discard ต้องยังอยู่ ─────────────────────────
check("F Apply button still present", 'ft.FilledButton("✅ Apply"' in _SRC)
check("F Discard button still present", 'ft.OutlinedButton("Discard"' in _SRC)
check("F truncated output still warns before applying",
      "Output was truncated — recovered" in _SRC)

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
