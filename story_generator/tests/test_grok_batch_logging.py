"""เทสต์บั๊กจริงที่เจอจาก session.log — Grok batch เงียบ ไม่ log อะไรเลย และ batch เดี่ยวหายซ้ำๆ

รันด้วย:  python -m story_generator.tests.test_grok_batch_logging

การสืบสวน (จาก log จริงของแอปวันที่ 2026-08-24): ช็อต 1 (extreme wide, batch เดี่ยว 1 ช็อต) ถูกส่ง
เข้า Grok batch แล้ว "หาย" ไป — ยิง recovery ซ้ำก็ยังหาย ต้อง fallback ไปเรียกทีละช็อตถึงจะสำเร็จ
(เสียเวลาไป ~77 วินาทีจากทั้งหมด — primary batch 11s + recovery 12s + fallback เต็ม ๆ อีกรอบ)
ระหว่างนั้น log ไม่มี "RAW (Grok)" หรือ system/user prompt ของ batch call เลยสักครั้ง — เพราะ
_grok_batch_call ตั้ง on_log=None ให้ _call_grok_api ทุกครั้ง ต่างจาก per-shot call ที่ log ครบ
ผลคือดีบักไม่ได้เลยว่า Grok ตอบอะไรมาจริงๆ ตอนที่ "หาย"

เมื่อ fallback เรียกทีละช็อตสำเร็จ พบว่า Grok ตอบเป็น "plain prose" ตรงๆ ไม่ห่อ JSON เลย
(เหมือนกับตอนเรียกทีละช็อตปกติที่ _parse_shot ยอมรับ plain text ได้อยู่แล้ว) แต่ _parse_grok_batch_shots
เข้มงวดกว่า ต้องมี {"shots": [...]} เท่านั้น — สมมติฐาน: Grok อาจตอบ plain prose แบบเดียวกันตอน
batch ด้วย (โดยเฉพาะ batch ขนาด 1 ที่ไม่ต่างจากการเรียกเดี่ยวมากนัก) แล้ว parser เข้มงวดมองว่า
"ไม่มีอะไรเลย" ทั้งที่ Grok ตอบถูกต้องแล้ว

แก้สองจุด: (1) log RAW response ของ batch call ด้วย เหมือน per-shot call, (2) batch ขนาด 1 ของ
Fill เท่านั้น ถ้า parse แบบเข้มงวดไม่เจออะไรและ response ไม่ได้พยายาม JSON (ไม่ขึ้นต้นด้วย "{")
ให้ถือว่าทั้งข้อความคือ prompt ของช็อตนั้นตรงๆ — เหมือนที่ _parse_shot ทำอยู่แล้วสำหรับการเรียกเดี่ยว

ขอบเขต: เทสต์พฤติกรรมจริงผ่าน mock Grok — ไม่ยิง API จริง แต่ยืนยันด้วย field/logic จริงของโค้ด
"""
import asyncio
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

# ── A: batch call ตอนนี้ log RAW response แล้ว ─────────────────────────────
check("A _grok_batch_call passes on_log through to _call_grok_api (was hardcoded None)",
      "grok_model, on_log=on_log)" in _SRC)
check("A no more silent on_log=None for the batch API call",
      "grok_api_key, grok_model, on_log=None)" not in _SRC)


# ── B: พฤติกรรมจริง — batch เดี่ยว 1 ช็อตที่ Grok ตอบ plain prose ถูกกู้คืน ──
async def t_single_shot_plain_prose_recovered():
    async def fake(sys_p, user_p, max_tokens, api_key, model="grok-4-latest", on_log=None, temperature=None):
        return ("Dao (24, Thai woman, long black hair) left silhouette turning to face right, "
                "Krit (32, Thai man) right silhouette holding camera, bedroom by mirror.")
    orig = L._call_grok_api
    L._call_grok_api = fake
    try:
        items = await L._grok_batch_call("SYS", [{"shot": 1, "scene_desc": "x"}], 4096,
                                         "key", "grok-4-latest", "Fill")
        check("B real-world case: plain-prose response for a size-1 Fill batch is recovered",
              len(items) == 1 and items[0].get("shot") == 1, items)
        check("B recovered item's prompt is the actual prose, not JSON garbage",
              "silhouette" in items[0].get("prompt", ""), items)
    finally:
        L._call_grok_api = orig


async def t_truncated_json_not_treated_as_prose():
    async def fake(sys_p, user_p, max_tokens, api_key, model="grok-4-latest", on_log=None, temperature=None):
        return '{"shot": 1, "prompt": "cut off mid'
    orig = L._call_grok_api
    L._call_grok_api = fake
    try:
        items = await L._grok_batch_call("SYS", [{"shot": 1}], 4096, "key", "grok-4-latest", "Fill")
        check("B a truncated JSON attempt is NOT swallowed as literal prose "
              "(stays empty, goes to the normal recovery path instead)",
              items == [], items)
    finally:
        L._call_grok_api = orig


async def t_multi_shot_batch_not_lenient():
    async def fake(sys_p, user_p, max_tokens, api_key, model="grok-4-latest", on_log=None, temperature=None):
        return "some plain prose that is not JSON"
    orig = L._call_grok_api
    L._call_grok_api = fake
    try:
        items = await L._grok_batch_call("SYS", [{"shot": 1}, {"shot": 2}], 4096,
                                         "key", "grok-4-latest", "Fill")
        check("B lenient fallback never fires for a multi-shot batch (ambiguous which shot it is)",
              items == [], items)
    finally:
        L._call_grok_api = orig


async def t_checker_and_director_never_lenient():
    async def fake(sys_p, user_p, max_tokens, api_key, model="grok-4-latest", on_log=None, temperature=None):
        return "plain prose with no keep_original/issues/reason fields"
    orig = L._call_grok_api
    L._call_grok_api = fake
    try:
        for stage in ("Checker", "Director"):
            items = await L._grok_batch_call("SYS", [{"shot": 1}], 4096, "key", "grok-4-latest", stage)
            check(f"B lenient fallback never fires for {stage} "
                  "(needs keep_original/issues fields plain prose can't supply)",
                  items == [], items)
    finally:
        L._call_grok_api = orig


async def t_empty_response_stays_empty():
    async def fake(sys_p, user_p, max_tokens, api_key, model="grok-4-latest", on_log=None, temperature=None):
        return ""
    orig = L._call_grok_api
    L._call_grok_api = fake
    try:
        items = await L._grok_batch_call("SYS", [{"shot": 1}], 4096, "key", "grok-4-latest", "Fill")
        check("B a genuinely empty response stays empty, not fabricated into a fake shot",
              items == [], items)
    finally:
        L._call_grok_api = orig


asyncio.run(t_single_shot_plain_prose_recovered())
asyncio.run(t_truncated_json_not_treated_as_prose())
asyncio.run(t_multi_shot_batch_not_lenient())
asyncio.run(t_checker_and_director_never_lenient())
asyncio.run(t_empty_response_stays_empty())

# ── C: ไม่มี regression — normal JSON-wrapped batch response ยังทำงานปกติ ──
async def t_normal_json_batch_still_works():
    import json as _json
    async def fake(sys_p, user_p, max_tokens, api_key, model="grok-4-latest", on_log=None, temperature=None):
        return _json.dumps({"shots": [{"shot": 1, "prompt": "a normal wrapped prompt"},
                                      {"shot": 2, "prompt": "another one"}]})
    orig = L._call_grok_api
    L._call_grok_api = fake
    try:
        items = await L._grok_batch_call("SYS", [{"shot": 1}, {"shot": 2}], 4096,
                                         "key", "grok-4-latest", "Fill")
        check("C normal multi-shot JSON-wrapped batch response unaffected by the fallback change",
              len(items) == 2 and {it["shot"] for it in items} == {1, 2}, items)
    finally:
        L._call_grok_api = orig


asyncio.run(t_normal_json_batch_still_works())

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
