"""
ui_lightbox.py — Lightbox overlay (fullscreen image viewer)
"""
__version__ = "20260727_2000"

import asyncio
import flet as ft
from pathlib import Path
from .ui_helpers import po, br


def build_lightbox(page: ft.Page, gallery_images: dict, thumb_actions_ref: list | None = None,
                   on_open_editor=None, llm_busy_ref: list | None = None):
    """
    สร้าง lightbox overlay พร้อม state
    thumb_actions_ref: [dict] — dict เดียวกับที่การ์ดรูปใช้ (path -> {"shot_idx","fix","save","edit","reload","delete"})
    ห่อด้วย ref list เพราะ registry ตัวจริงถูกสร้างหลัง lightbox (ui_shot_row.build_shot_col ยังไม่รันตอนนี้)
    llm_busy_ref: [bool] เดียวกับ main_ui — UI-UNFREEZE-A1: ระหว่าง LLM รันอยู่ การลบรูปต้อง sync แค่
    lightbox overlay เอง ไม่ใช่ page.update() ทั้งหน้า (ทั้งหน้ารวม storyboard ใหญ่ + log ที่กำลังไหลรัว)
    คืน: (lb_overlay, open_lb_fn)
    """
    _llm_busy_ref = llm_busy_ref if llm_busy_ref is not None else [False]
    _lb_item: list = [None]
    _lb_list: list = []
    _confirm_open: list[bool] = [False]
    _thumb_actions_ref = thumb_actions_ref if thumb_actions_ref is not None else [{}]

    lb_img     = ft.Image(src="", fit="contain", expand=True)
    lb_counter = ft.Text("", size=11, color="white")

    def _update_lb_counter():
        if _lb_list and _lb_item[0] in _lb_list:
            i = _lb_list.index(_lb_item[0])
            lb_counter.value = f"{i+1} / {len(_lb_list)}"
        else:
            lb_counter.value = ""

    def close_lb(e=None):
        lb_overlay.visible = False
        page.on_keyboard_event = None   # คืน keyboard ให้ text field ทั่วแอปใช้ arrow/delete ได้ปกติ
        page.update()

    def lb_prev(e=None):
        if not _lb_list:
            return
        idx = _lb_list.index(_lb_item[0]) if _lb_item[0] in _lb_list else 0
        _lb_item[0] = _lb_list[(idx - 1) % len(_lb_list)]
        _update_lb_counter()
        _lb_reload_img_from_disk(_lb_item[0], "lb_prev")

    def lb_next(e=None):
        if not _lb_list:
            return
        idx = _lb_list.index(_lb_item[0]) if _lb_item[0] in _lb_list else 0
        _lb_item[0] = _lb_list[(idx + 1) % len(_lb_list)]
        _update_lb_counter()
        _lb_reload_img_from_disk(_lb_item[0], "lb_next")

    async def _do_delete(e=None):
        path = _lb_item[0]
        if not path:
            return
        # หา next ก่อนลบ
        if len(_lb_list) > 1:
            idx = _lb_list.index(path) if path in _lb_list else 0
            next_path = _lb_list[(idx + 1) % len(_lb_list)]
            if next_path == path:
                next_path = None
        else:
            next_path = None

        # ลบจาก gallery_images
        for k in list(gallery_images.keys()):
            gallery_images[k] = [img for img in gallery_images[k] if img["path"] != path]
        # ลบจาก _lb_list
        if path in _lb_list:
            _lb_list.remove(path)

        # ล้าง src ก่อน ให้ Flet release file handle บน Windows
        lb_img.src = next_path or ""
        page.pop_dialog()
        _confirm_open[0] = False

        if not _lb_list:
            # ไม่เหลือรูปแล้ว — ปิด lightbox
            lb_overlay.visible = False
            page.on_keyboard_event = None
        elif next_path and next_path in _lb_list:
            _lb_item[0] = next_path
            _update_lb_counter()

        # UI-UNFREEZE-A1: ระหว่าง LLM ยังรันอยู่ page.update() ทั้งหน้าต้องแข่งกับ storyboard ใหญ่ +
        # log ที่กำลังไหลรัว sync แค่ lightbox overlay เอง (ยังครบทุกอย่างที่ผู้ใช้เห็นอยู่ตอนนี้จริง)
        if _llm_busy_ref[0]:
            try: lb_overlay.update()
            except Exception: pass
        else:
            page.update()

        # ลบไฟล์ — retry ถ้า Windows ยัง lock อยู่
        p = Path(path)
        for attempt in range(6):
            try:
                p.unlink(missing_ok=True)
                break
            except PermissionError:
                if attempt < 5:
                    await asyncio.sleep(0.3)
            except Exception:
                break

        # อัปเดต thumbnail row ใน shot card (ถ้า registry มี callback "delete" ให้)
        entry = _thumb_actions_ref[0].get(path)
        if entry:
            fn = entry.get("delete")
            if fn:
                result = fn(None)
                if asyncio.iscoroutine(result):
                    await result

    def _open_delete_confirm(e=None):
        if not _lb_item[0]:
            return
        page.show_dialog(lb_delete_dlg)
        _confirm_open[0] = True

    def _cancel_delete(e=None):
        page.pop_dialog()
        _confirm_open[0] = False

    lb_delete_dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Delete this image?", size=14, weight=ft.FontWeight.W_600),
        content=ft.Text("The image will be permanently deleted from the gallery.", size=12),
        actions=[
            ft.TextButton("Cancel", on_click=_cancel_delete),
            ft.FilledButton(
                "Delete",
                on_click=_do_delete,
                style=ft.ButtonStyle(bgcolor="#DC2626", color="white"),
                autofocus=True,
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    # NOTE: ไม่ใส่ใน page.overlay — ใช้ page.show_dialog()/page.pop_dialog() แทน (Flet เวอร์ชันนี้
    # ต้องใช้ dialog stack API ถึงจะ sync client/server ถูกต้อง toggling .open ตรงๆ ใช้ไม่ได้)

    def _refresh_after_action(shot_idx):
        """gallery_images ถูกแก้โดย action (fix/reload/delete) ไปแล้ว — sync _lb_list/lb_img ให้ตรง"""
        all_combined = []
        for idx in sorted(gallery_images.keys()):
            all_combined.extend(img["path"] for img in gallery_images.get(idx, []))
        _lb_list.clear(); _lb_list.extend(all_combined)
        if not _lb_list:
            close_lb(); return
        # ไปที่รูปล่าสุดของ shot เดิม ถ้ามี ไม่งั้นก็รูปแรกที่เหลือ
        _this_shot_paths = [img["path"] for img in gallery_images.get(shot_idx, [])]
        _lb_item[0] = _this_shot_paths[-1] if _this_shot_paths else _lb_list[0]
        lb_img.src = _lb_item[0]
        _update_lb_counter()
        page.update()

    def _lb_reload_img_from_disk(path: str, caller: str = "?"):  # caller unused, kept for call sites
        """Force re-read current image from disk as base64 (bypasses Flet cache)."""
        import base64, mimetypes
        try:
            mime = mimetypes.guess_type(path)[0] or "image/jpeg"
            lb_img.src = f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode()}"
        except Exception:
            lb_img.src = path
        page.update()

    async def _lb_run_action(action_key: str):
        path = _lb_item[0]
        if not path: return
        entry = _thumb_actions_ref[0].get(path)
        if not entry: return
        fn = entry.get(action_key)
        if not fn: return
        shot_idx = entry.get("shot_idx")
        result = fn(None)
        if asyncio.iscoroutine(result):
            await result
        # "reload" and "save" stay on the same image; "fix" navigates to new result
        if action_key in ("reload", "save", "edit"):
            _lb_reload_img_from_disk(path, f"lb_run_action:{action_key}")
        else:
            _refresh_after_action(shot_idx)

    # Auto-reload on any window event when lightbox is open
    # (Flet 0.85 sends e.data=None for all window events, so we can't filter by event type)
    def _on_window_event(e):
        if lb_overlay.visible and _lb_item[0]:
            _lb_reload_img_from_disk(_lb_item[0], "window_event")
    page.window.on_event = _on_window_event

    def _on_keyboard(e: ft.KeyboardEvent):
        if not lb_overlay.visible:
            page.on_keyboard_event = None  # safety: clear stale handler
            return
        if _confirm_open[0]:
            # ขณะ dialog เปิด: Enter = ยืนยันลบ, Escape = ยกเลิก
            if e.key == "Enter":
                asyncio.create_task(_do_delete())
            elif e.key == "Escape":
                _cancel_delete()
            return
        if e.key == "Arrow Left":
            lb_prev()
        elif e.key == "Arrow Right":
            lb_next()
        elif e.key == "Escape":
            close_lb()
        elif e.key == "Delete":
            _open_delete_confirm()

    # NOTE: ผูก on_keyboard_event เฉพาะตอน lightbox เปิดอยู่เท่านั้น (ใน open_lb/close_lb) —
    # ถ้าผูกไว้ตลอดตั้งแต่ตอนแอปเริ่ม จะไปแย่ง arrow/delete จาก text field ทั่วทั้งแอป
    # แม้ตัว handler เองจะ check lb_overlay.visible แล้ว return เฉยๆ ก็ตาม

    def open_lb(path: str, all_paths: list):
        _lb_item[0] = path
        _lb_list.clear()
        all_combined = []
        for idx in sorted(gallery_images.keys()):
            all_combined.extend(img["path"] for img in gallery_images.get(idx, []))
        _lb_list.extend(all_combined if all_combined else all_paths)
        _update_lb_counter()
        lb_overlay.visible = True
        page.on_keyboard_event = _on_keyboard
        _lb_reload_img_from_disk(path, "open_lb")

    lb_prev_btn = ft.IconButton(
        ft.Icons.CHEVRON_LEFT, icon_color="white", icon_size=40,
        bgcolor="#55000000", on_click=lb_prev,
        width=56, height=56,
    )
    lb_next_btn = ft.IconButton(
        ft.Icons.CHEVRON_RIGHT, icon_color="white", icon_size=40,
        bgcolor="#55000000", on_click=lb_next,
        width=56, height=56,
    )
    lb_close_btn = ft.IconButton(
        ft.Icons.CLOSE, icon_color="white", icon_size=24,
        bgcolor="#55000000", on_click=close_lb,
        width=40, height=40,
    )
    lb_delete_btn = ft.IconButton(
        ft.Icons.DELETE_OUTLINE_ROUNDED, icon_color="#FF6B6B", icon_size=22,
        bgcolor="#55000000", on_click=_open_delete_confirm,
        tooltip="Delete (Del)",
        width=40, height=40,
    )
    def _on_editor_click(e):
        if not on_open_editor or not _lb_item[0]: return
        on_open_editor(_lb_item[0])

    lb_editor_btn = ft.IconButton(
        ft.Icons.EDIT_ROUNDED, icon_color="#7DD3FC", icon_size=20,
        bgcolor="#55000000",
        on_click=_on_editor_click,
        tooltip="Open in Image Editor",
        width=40, height=40,
        visible=on_open_editor is not None,
    )
    lb_fix_btn = ft.IconButton(
        ft.Icons.BUILD_ROUNDED, icon_color="white", icon_size=20,
        bgcolor="#55000000", on_click=lambda e: asyncio.create_task(_lb_run_action("fix")),
        tooltip="Fix (regenerate with same seed)",
        width=40, height=40,
    )
    lb_save_btn = ft.IconButton(
        ft.Icons.SAVE_ALT_ROUNDED, icon_color="white", icon_size=20,
        bgcolor="#55000000", on_click=lambda e: asyncio.create_task(_lb_run_action("save")),
        tooltip="Save image",
        width=40, height=40,
    )
    lb_edit_btn = ft.IconButton(
        ft.Icons.OPEN_IN_NEW_ROUNDED, icon_color="white", icon_size=20,
        bgcolor="#55000000", on_click=lambda e: asyncio.create_task(_lb_run_action("edit")),
        tooltip="Edit in external app",
        width=40, height=40,
    )
    lb_reload_btn = ft.IconButton(
        ft.Icons.REFRESH_ROUNDED, icon_color="white", icon_size=20,
        bgcolor="#5500AA88", on_click=lambda e: asyncio.create_task(_lb_run_action("reload")),
        tooltip="Reload after external edit",
        width=40, height=40,
    )

    lb_overlay = ft.Container(
        visible=False, expand=True,
        bgcolor="#CC000000",
        content=ft.Column([
            ft.Container(
                content=ft.Row([
                    ft.Container(expand=True),
                    lb_counter,
                    ft.Container(expand=True),
                    lb_editor_btn, lb_fix_btn, lb_save_btn, lb_edit_btn, lb_reload_btn,
                    lb_delete_btn,
                    lb_close_btn,
                ], alignment=ft.MainAxisAlignment.END),
                padding=po(left=8, right=8, top=8, bottom=4),
            ),
            ft.Container(
                expand=True,
                content=ft.Row([
                    lb_prev_btn,
                    ft.Container(content=lb_img, expand=True,
                                 padding=po(left=8, right=8, top=0, bottom=0)),
                    lb_next_btn,
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
            ),
            ft.Container(height=16),
        ], spacing=0),
    )

    return lb_overlay, open_lb
