"""เทสต์การเปิด/ปิดปุ่มของ Review Outline ตามเฟส และการ์ดของ Continue to Fill

รันด้วย:  python -m story_generator.tests.test_outline_phase

เทสต์เฉพาะส่วนที่เป็น pure logic (ตารางความสามารถต่อเฟส + การ์ด) ไม่แตะ Flet —
ตัวที่เอาผลไปแปะใส่ปุ่มจริงคือ _set_outline_phase() ใน main_ui ซึ่งอ่านจากตารางเดียวกันนี้
"""
from ..main_ui import OUTLINE_PHASES, outline_phase_caps, can_continue_to_fill

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


# ── A1: Pass A กำลังวิ่ง — commit ไม่ได้เลย เหลือแค่ Cancel ──────────────────────
_a = outline_phase_caps("PASS_A")
check("A1 loading shown while Pass A runs", _a["loading"] is True, _a)
check("A1 editor unavailable", _a["editor"] is False, _a)
check("A1 regenerate unavailable", _a["regen"] is False, _a)
check("A1 save unavailable", _a["save"] is False, _a)
check("A1 continue-to-fill unavailable", _a["fill"] is False, _a)
check("A1 shot fields locked", _a["fields"] is False, _a)

# ── A2: รอเลือกจำนวนช็อต / Pass B — ยังคง commit ไม่ได้ ─────────────────────────
for _p in ("WAITING_COUNT", "PASS_B"):
    _c = outline_phase_caps(_p)
    check(f"A2 {_p} save still unavailable", _c["save"] is False, _c)
    check(f"A2 {_p} fill still unavailable", _c["fill"] is False, _c)
    check(f"A2 {_p} editor still unavailable", _c["editor"] is False, _c)
    check(f"A2 {_p} loading still shown", _c["loading"] is True, _c)

# ── A3: สำเร็จ — ทุกอย่างเปิด ──────────────────────────────────────────────────
_r = outline_phase_caps("READY")
check("A3 loading hidden", _r["loading"] is False, _r)
check("A3 editor available", _r["editor"] is True, _r)
check("A3 regenerate available", _r["regen"] is True, _r)
check("A3 save available", _r["save"] is True, _r)
check("A3 continue-to-fill available", _r["fill"] is True, _r)
check("A3 shot fields editable", _r["fields"] is True, _r)

# ── A4: Pass A ล้ม — กู้ได้อย่างเดียว ห้ามทำเหมือน outline ใช้ได้ ────────────────
_f = outline_phase_caps("FAILED")
check("A4 fill unavailable after failure", _f["fill"] is False, _f)
check("A4 save unavailable after failure", _f["save"] is False, _f)
check("A4 editor unavailable after failure", _f["editor"] is False, _f)
check("A4 recovery (regenerate) available", _f["regen"] is True, _f)
check("A4 loading hidden after failure", _f["loading"] is False, _f)

# ── ทุกเฟสต้องตอบครบทุกคีย์ และเฟสที่ไม่รู้จักต้องปลอดภัยไว้ก่อน ──────────────────
_keys = {"loading", "editor", "regen", "save", "fill", "fields"}
check("all phases answer every capability",
      all(set(outline_phase_caps(p)) == _keys for p in OUTLINE_PHASES),
      [p for p in OUTLINE_PHASES if set(outline_phase_caps(p)) != _keys])
_u = outline_phase_caps("SOMETHING_ELSE")
check("unknown phase cannot commit", _u["save"] is False and _u["fill"] is False, _u)

# ── B4: การ์ดของ handler — ปุ่มที่ปิดไว้ยังถูกยิงได้จากจังหวะ event ของ Flet ──────
check("B4 ready + shots + idle → allowed", can_continue_to_fill("READY", 12, False) is True)
check("B4 blocked while Pass B runs", can_continue_to_fill("PASS_B", 12, False) is False)
check("B4 blocked while Pass A runs", can_continue_to_fill("PASS_A", 12, False) is False)
check("B4 blocked after failure", can_continue_to_fill("FAILED", 12, False) is False)
check("B4 blocked with no shots", can_continue_to_fill("READY", 0, False) is False)
check("B4 blocked while LLM busy", can_continue_to_fill("READY", 12, True) is False)

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
