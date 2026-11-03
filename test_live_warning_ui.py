"""The comfortable-section chip and the line note, then a switch to another song. No full app and no song files.

Run: python test_live_warning_ui.py
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

import flet as ft

from music_studio import i18n as I18N
from music_studio import live_insights as L

OUT = Path(__file__).resolve().parent / "_sel_preview_shots"
NORMAL = ["ฉันยังรอเธอตรงที่เดิม", "ริมหน้าต่างที่ฝนเพิ่งหยุด", "ยังมีกุญแจวางอยู่บนโต๊ะ", "แต่ไม่มีเธอกลับมา"]
LONG = "ฉันยังรอเธอตรงที่เดิมนะริมหน้าต่างที่ฝนเพิ่งหยุดตกลงมาอีกแล้ว"
SONG_A = "[Chorus]\n" + "\n".join(NORMAL[:2] + [LONG] + NORMAL[3:]) + "\n"
SONG_B = "[Verse]\n" + "\n".join(NORMAL) + "\n[Chorus]\n" + "\n".join(NORMAL) + "\n"
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    line = ("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else "")
    print(line, flush=True)
    if not ok:
        FAILS.append(line)


def pack(text: str, dur: int = 180):
    ins = L.analyze(text)
    dn = L.density(ins, L.DensitySettings(bpm=100, timesig="4", duration=dur, speed="normal"))
    return dn, L.health(ins, dn)


def main(page: ft.Page):
    page.title = "Live warning"
    page.bgcolor = "#F8FAFC"
    page.padding = 16
    page.window.width = 920
    page.window.height = 640
    I18N.install(ft)
    I18N.set_lang("th")

    def view(title: str, text: str):
        dn, items = pack(text)
        level = dn.sections[0].level if dn.sections else "unknown"
        bg, fg = {"comfortable": ("#D1FAE5", "#047857"), "moderate": ("#EDE9FE", "#6D28D9"), "dense": ("#FEF3C7", "#B45309"), "very_dense": ("#FFEDD5", "#C2410C")}.get(level, ("#E2E8F0", "#475569"))
        chip = ft.Container(ft.Text(L.LEVEL_NAME[level], size=12, weight=ft.FontWeight.BOLD, color=fg), bgcolor=bg, border_radius=12, padding=8)
        tone = L.health_tone([x for x in items if x.kind == "long_line"] or items)
        note_col = "#EA580C" if tone == "warning" else "#7C3AED"
        lines = [x for x in items if x.kind in ("long_line", "uneven")]
        body = ft.Text(lines[0].summary if lines else "No section, line or structure looks risky for these settings.", size=13, color="#334155")
        card = ft.Container(ft.Column([
            ft.Text(title, size=12, color="#64748B"),
            ft.Row([ft.Text(dn.sections[0].name if dn.sections else "", size=16, weight=ft.FontWeight.W_600), chip], spacing=8),
            ft.Container(body, bgcolor="#F5F3FF" if tone != "warning" else "#FFF7ED", border_radius=8, padding=10, border=ft.Border.all(1, note_col)),
        ], spacing=8), width=400, padding=12, border_radius=12, bgcolor="#FFFFFF", border=ft.Border.all(1, "#E2E8F0"))
        return card, chip, body, level, lines

    before, chip_a, note_a, level_a, lines_a = view("Song A · sheet 1", SONG_A)
    after, chip_b, note_b, level_b, lines_b = view("Song B · sheet 2", SONG_B)
    status = ft.Text("", size=12, color="#334155")
    shot = ft.Screenshot(ft.Column([
        ft.Text("Lyrics density", size=18, weight=ft.FontWeight.BOLD, color="#0F172A"),
        ft.Row([before, after], spacing=16),
        status,
    ], spacing=12))
    page.add(shot)

    async def go():
        try:
            await asyncio.sleep(0.4)
            page.update()
            check("song A section is comfortable", level_a == "comfortable" and "ร้องสบาย" in (chip_a.content.value or ""))
            check("song A line note names the line and not a rushed section", lines_a and lines_a[0].lines == [4] and "ร้องสบาย" in (note_a.value or "") and "เฉพาะบรรทัดนี้ที่ยาว" in (note_a.value or "") and "อาจถูกร้องรวบ" not in (note_a.value or ""))
            check("song B has no line warning", level_b == "comfortable" and lines_b == [] and "ไม่พบท่อน" in (note_b.value or ""))
            check("the other sheet does not keep song A's line", "20" not in (note_b.value or "") and note_a.value != note_b.value)
            status.value = "sheet 1 ร้องสบาย + one long line  |  sheet 2 no line warning"
            page.update()
            OUT.mkdir(parents=True, exist_ok=True)
            png = await shot.capture(pixel_ratio=1.0, delay=ft.Duration(milliseconds=250))
            (OUT / "08_warnings.png").write_bytes(png)
            print("shot  08_warnings", flush=True)
        except Exception:
            FAILS.append(traceback.format_exc())
            print(traceback.format_exc(), flush=True)
        (OUT / "result_warnings.txt").write_text("\n".join(FAILS) if FAILS else "ok", encoding="utf-8")
        await page.window.destroy()

    page.run_task(go)


if __name__ == "__main__":
    ft.run(main, port=0, view=ft.AppView.FLET_APP)
    text = (OUT / "result_warnings.txt").read_text(encoding="utf-8") if (OUT / "result_warnings.txt").exists() else "no result"
    if text != "ok":
        print(text, flush=True)
        sys.exit(1)
    print("ALL PASS", flush=True)
