# -*- coding: utf-8 -*-
"""เนื้อหาหน้า Help — อ่านจากไฟล์ในโฟลเดอร์ helper/ ที่รากโปรเจกต์

ผู้ใช้เขียน system prompt เองแล้วเก็บไว้เป็นไฟล์ (เช่น helper/system_promt_1) หน้านี้แค่เอามาแสดง
ให้ก๊อปไปเปิด session กับ AI ตัวอื่น แล้วค่อยวาง JSON ที่ export ตามไป

หนึ่งไฟล์ = หนึ่งแท็บ เรียงตามชื่อไฟล์ — เพิ่มหัวข้อใหม่คือหย่อนไฟล์ลงโฟลเดอร์นี้ ไม่ต้องแก้โค้ด
อ่านสดทุกครั้งที่เปิด dialog ไม่ได้ cache ไว้ เพราะผู้ใช้แก้ไฟล์อยู่เรื่อย ๆ แล้วอยากเห็นผลทันที
"""
from pathlib import Path

from .constants import APP_ROOT

HELP_DIR = APP_ROOT / "helper"

# ไฟล์ที่ไม่ใช่ข้อความ — ไม่ต้องเอามาทำแท็บ
_SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".exe", ".db"}
_MAX_CHARS = 400_000       # กันไฟล์หลุดขนาดมาทำ UI ค้าง

_EMPTY_NOTE = (
    "No help files found.\n\n"
    "Put a text file in this folder and it becomes a tab here:\n"
    "    {folder}\n\n"
    "One file = one tab, sorted by file name. The text is shown read-only so it can be copied "
    "into a new session with another AI."
)


def _read_one(path: Path) -> str:
    """อ่านไฟล์เดียวแบบทนพัง — ไฟล์ที่อ่านไม่ออกไม่ควรทำให้ทั้ง dialog เปิดไม่ได้"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as ex:
        return f"⚠ Could not read {path.name}: {ex}"
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n\n… (truncated at {_MAX_CHARS:,} characters)"
    # ไฟล์จาก Windows เป็น CRLF — TextField แสดงบรรทัดว่างเกินถ้าไม่แปลง
    return text.replace("\r\n", "\n").replace("\r", "\n")


def load_help_tabs() -> list:
    """[(ชื่อแท็บ, เนื้อหา)] จากไฟล์ในโฟลเดอร์ helper/ — คืนหมายเหตุหนึ่งแท็บเมื่อยังไม่มีไฟล์เลย"""
    try:
        files = sorted(
            (p for p in HELP_DIR.iterdir()
             if p.is_file() and p.suffix.lower() not in _SKIP_SUFFIX and not p.name.startswith(".")),
            key=lambda p: p.name.lower(),
        )
    except FileNotFoundError:
        files = []
    except Exception as ex:
        return [("Help", f"⚠ Could not read {HELP_DIR}: {ex}")]
    if not files:
        return [("Help", _EMPTY_NOTE.format(folder=HELP_DIR))]
    return [(p.stem or p.name, _read_one(p)) for p in files]
