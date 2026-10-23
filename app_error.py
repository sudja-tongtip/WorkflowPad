"""app_error.py - one friendly error screen for all five WorkflowPad programs.

Why: when something fails inside a button handler (for example "[Errno 28] No space left on device" while saving), Flet shows its own red "The application encountered an error" banner, replaces the
whole window and the program cannot be used any more. This module keeps the program alive and shows a clear dialog instead: what happened in plain words, what to do about it, the technical
details (copyable) and the choice to Continue or Close. A failure while the program starts shows the same information as a full-window page with "Try again".

Nothing about the user is sent anywhere. The details are written to %LOCALAPPDATA%\\WorkflowPad\\logs\\errors.log (a failed write, for example on a full disk, is silent).
One module, used the same way by every program:  ft.app(target=app_error.guard(ft, main, APP_NAME, APP_VERSION))
The `ft` module is passed in: this file imports nothing from the UI.
"""

from __future__ import annotations

import asyncio
import errno
import inspect
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

LOG_DIR = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "WorkflowPad" / "logs"
LOG_FILE = LOG_DIR / "errors.log"
LOW_DISK_BYTES = 200 * 1024 * 1024          # a warning (not an error) when the program's data drive has less than this left
_DEDUPE_SECONDS = 2.0

# the same dark look as the programs
BG, PANEL, PANEL2, BORDER = "#141414", "#1E1E1E", "#2A2A2A", "#383838"
TEXT1, TEXT2, TEXT3 = "#F3F4F6", "#9CA3AF", "#6B7280"
RED, RED_BG = "#F87171", "#3B1114"
AMBER, AMBER_BG = "#FBBF24", "#2A220E"
BLUE = "#60A5FA"


# ── what happened, in plain words ───────────────────────────────────────────────────────────────────────
def _errno_of(exc, message: str) -> int | None:
    """The OS error number: from the exception when there is one, else from the '[Errno N]' token Python puts in the text of an OSError."""
    if isinstance(exc, OSError) and exc.errno:
        return int(exc.errno)
    m = re.search(r"\[Errno (\d+)\]", message or "")
    return int(m.group(1)) if m else None


def _drive_of(exc, fallback: str = "") -> str:
    name = getattr(exc, "filename", None) or fallback or sys.executable
    drive = os.path.splitdrive(str(name))[0]
    return drive or os.path.splitdrive(str(Path.home()))[0]


def classify(exc, message: str = "") -> dict:
    """{'kind', 'title', 'why', 'todo': [steps]} for an error. Decided from the exception type / OS error number only; no word of the user's data or language is read."""
    code = _errno_of(exc, message)
    if code == errno.ENOSPC:
        drive = _drive_of(exc)
        return {"kind": "disk_full", "title": "The disk is full",
                "why": f"There is no free space left on drive {drive or 'C:'}, so the program could not save its data. Nothing you already saved is lost; only the last action could not be written.",
                "todo": ["Free some space: delete old images or downloads and empty the Recycle Bin.",
                         "Then press Continue and repeat the last action.",
                         "If this keeps happening, move the output folder to a drive with more space."]}
    if isinstance(exc, PermissionError) or code in (errno.EACCES, errno.EPERM):
        name = getattr(exc, "filename", None)
        return {"kind": "permission", "title": "The program is not allowed to write there",
                "why": "Windows refused access" + (f" to:\n{name}" if name else " to a file or folder.") + "\nThe file may be open in another program, or the folder is protected.",
                "todo": ["Close other programs that may be using the file.", "Choose a folder you own (for example inside Documents) and try again."]}
    if isinstance(exc, FileNotFoundError) or code == errno.ENOENT:
        name = getattr(exc, "filename", None)
        return {"kind": "missing", "title": "A file or folder could not be found",
                "why": "The program looked for something that is not there" + (f":\n{name}" if name else ".") + "\nIt may have been moved, renamed or deleted.",
                "todo": ["Check that the folder or file still exists.", "Select it again in the program."]}
    if isinstance(exc, (ConnectionError, TimeoutError)) or type(exc).__name__ in ("URLError", "ConnectionRefusedError"):
        return {"kind": "network", "title": "Cannot reach the other program",
                "why": "The connection failed. For image and LLM work this is usually ComfyUI (127.0.0.1:8188) that is not running, still starting, or busy; for Grok it is the internet connection.",
                "todo": ["Start ComfyUI and wait until it has finished loading.", "Check your internet connection if you use Grok.", "Then press Continue and try again."]}
    if isinstance(exc, MemoryError):
        return {"kind": "memory", "title": "The computer ran out of memory",
                "why": "There was not enough free memory for this action (often a very large image).",
                "todo": ["Close other programs, especially ComfyUI models you do not need.", "Try a smaller image or fewer items at once."]}
    return {"kind": "other", "title": "Something unexpected went wrong",
            "why": "The program hit an error it could not handle. It is still running, but the last action did not finish.",
            "todo": ["Press Continue and try the action again.", "If it happens again, press Copy details and send them with a description of what you did."]}


def _details_text(app_name: str, version: str, message: str, exc) -> str:
    tb = ""
    if exc is not None and getattr(exc, "__traceback__", None) is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    body = tb or message or "(no details)"
    return f"{app_name} {version}\nPython {sys.version.split()[0]} on {sys.platform}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n{body.strip()}\n"


def _write_log(text: str) -> bool:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write("=" * 70 + "\n" + text + "\n")
        return True
    except OSError:
        return False                    # e.g. the disk is full: the dialog still shows everything


def _open_log_folder() -> None:
    target = LOG_DIR if LOG_DIR.exists() else (LOG_DIR.parent if LOG_DIR.parent.exists() else Path.home())
    try:
        os.startfile(str(target))       # Windows
    except Exception:
        pass


# ── the pieces of the screen ──────────────────────────────────────────────────────────────────────────────
def _body(ft, page, info: dict, details: str, app_name: str, version: str, *, on_primary, primary_label: str, on_close_program, dismiss_note: str = ""):
    """The card shared by the dialog and the full-window page. Returns (content column, action buttons row)."""
    state = {"copied": False}

    def side(color):
        s = ft.BorderSide(1, color)
        return ft.Border(top=s, right=s, bottom=s, left=s)

    icon = ft.Container(content=ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED if info["kind"] != "low_disk" else ft.Icons.WARNING_AMBER_ROUNDED,
                                         color=RED if info["kind"] != "low_disk" else AMBER, size=26),
                        width=48, height=48, border_radius=24, bgcolor=RED_BG if info["kind"] != "low_disk" else AMBER_BG, alignment=ft.Alignment(0, 0))
    header = ft.Row([
        icon,
        ft.Column([ft.Text(info["title"], size=17, weight=ft.FontWeight.W_700, color=TEXT1),
                   ft.Text(f"{app_name}  ·  Version {version}", size=11, color=TEXT3)], spacing=2, tight=True, expand=True),
    ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    why = ft.Text(info["why"], size=13, color=TEXT2, selectable=True)
    steps = ft.Container(
        content=ft.Column([ft.Text("What you can do", size=12, weight=ft.FontWeight.W_600, color=AMBER)] +
                          [ft.Row([ft.Text("•", size=12, color=AMBER), ft.Text(t, size=12, color=TEXT1, expand=True)], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START) for t in info["todo"]],
                          spacing=6, tight=True),
        bgcolor=AMBER_BG, border=side("#5B4A14"), border_radius=10, padding=ft.Padding(left=14, top=10, right=14, bottom=12))

    det_box = ft.Container(
        content=ft.Text(details, size=10, color=TEXT2, selectable=True, font_family="Consolas"),
        bgcolor=BG, border=side(BORDER), border_radius=8, padding=10, visible=False, height=170)
    det_box.content = ft.Column([ft.Text(details, size=10, color=TEXT2, selectable=True, font_family="Consolas")], scroll=ft.ScrollMode.AUTO)
    toggle = ft.TextButton("Show technical details", icon=ft.Icons.KEYBOARD_ARROW_DOWN, style=ft.ButtonStyle(color=TEXT2))

    def _toggle(e):
        det_box.visible = not det_box.visible
        toggle.text = "Hide technical details" if det_box.visible else "Show technical details"
        toggle.icon = ft.Icons.KEYBOARD_ARROW_UP if det_box.visible else ft.Icons.KEYBOARD_ARROW_DOWN
        page.update()

    toggle.on_click = _toggle

    clip = None
    try:
        clip = ft.Clipboard()
        page.services.append(clip)
    except Exception:
        clip = None
    copy_btn = ft.TextButton("Copy details", icon=ft.Icons.CONTENT_COPY_ROUNDED, style=ft.ButtonStyle(color=TEXT2))

    async def _copy(e):
        try:
            if clip is not None:
                await clip.set(details)
                copy_btn.text, copy_btn.icon = "Copied ✓", ft.Icons.CHECK_ROUNDED
                page.update()
        except Exception:
            pass

    copy_btn.on_click = _copy
    log_btn = ft.TextButton("Open log folder", icon=ft.Icons.FOLDER_OPEN_ROUNDED, style=ft.ButtonStyle(color=TEXT2), on_click=lambda e: _open_log_folder())
    close_btn = ft.TextButton("Close program", style=ft.ButtonStyle(color=RED), on_click=on_close_program)
    primary = ft.FilledButton(primary_label, on_click=on_primary)

    content = ft.Column([header, why, steps, ft.Row([toggle, copy_btn, log_btn], spacing=4, wrap=True), det_box]
                        + ([ft.Text(dismiss_note, size=10, color=TEXT3)] if dismiss_note else []), spacing=14, tight=True)
    actions = ft.Row([close_btn, primary], alignment=ft.MainAxisAlignment.END, spacing=10)
    return content, actions


def _debug() -> None:
    """With APP_ERROR_DEBUG=1 a failure INSIDE the error screen itself is printed (it is otherwise silent: an error screen must never raise)."""
    if os.environ.get("APP_ERROR_DEBUG"):
        traceback.print_exc()


def _make_closer(page):
    async def _close(e=None):
        try:
            await page.window.destroy()
        except Exception:
            os._exit(0)
    return _close


# ── the dialog (the program keeps running) ───────────────────────────────────────────────────────────────
def show_error(ft, page, app_name: str, version: str, message: str, exc=None, *, info: dict | None = None, log: bool = True) -> None:
    """Show the friendly dialog for an error and keep the program alive. Safe to call from any handler; never raises."""
    try:
        st = page.data if isinstance(getattr(page, "data", None), dict) else None
        reg = getattr(page, "_app_error_state", None)
        if reg is None:
            reg = {"open": False, "last": ("", 0.0)}
            try:
                page._app_error_state = reg
            except Exception:
                pass
        info = info or classify(exc, message)
        details = _details_text(app_name, version, message, exc)
        now = time.time()
        if log:
            _write_log(details)
        if reg["open"] or (reg["last"][0] == info["title"] and now - reg["last"][1] < _DEDUPE_SECONDS):
            return                                     # a dialog is already up, or the same error repeats right away: one dialog is enough
        reg["last"] = (info["title"], now)
        reg["open"] = True

        def _done(e=None):
            reg["open"] = False
            try:
                page.pop_dialog()
                page.update()
            except Exception:
                pass

        content, actions = _body(ft, page, info, details, app_name, version, on_primary=_done, primary_label="Continue", on_close_program=_make_closer(page),
                                 dismiss_note="The program is still running. Closing this window does not undo anything you already saved.")
        dlg = ft.AlertDialog(modal=True, bgcolor=PANEL, shape=ft.RoundedRectangleBorder(radius=14),
                             content=ft.Container(content=content, width=560, padding=ft.Padding(left=6, top=6, right=6, bottom=0)),
                             actions=[actions], actions_padding=ft.Padding(left=20, top=0, right=20, bottom=16),
                             on_dismiss=lambda e: reg.__setitem__("open", False))
        page.show_dialog(dlg)
    except Exception:
        _debug()


def _install(ft, page, app_name: str, version: str) -> None:
    """Replace Flet's 'the application crashed' banner for this page by the friendly dialog, and route stray asyncio task errors there too."""
    try:
        def _error(message: str):
            show_error(ft, page, app_name, version, str(message), sys.exc_info()[1])
        page.session.error = _error
    except Exception:
        try:
            page.on_error = lambda e: show_error(ft, page, app_name, version, str(getattr(e, "data", "")), sys.exc_info()[1])
        except Exception:
            pass
    try:
        loop = asyncio.get_running_loop()

        def _loop_handler(lp, context):
            exc = context.get("exception")
            if isinstance(exc, Exception):
                show_error(ft, page, app_name, version, str(context.get("message") or exc), exc)
            lp.default_exception_handler(context)

        loop.set_exception_handler(_loop_handler)
    except RuntimeError:
        pass                                           # sync main: no running loop here


def _free_space_warning(ft, page, app_name: str, version: str) -> None:
    """A gentle heads-up at start when the data drive is nearly full (before it turns into a failed save)."""
    try:
        data_dir = Path(os.environ.get("APPDATA") or Path.home())
        free = shutil.disk_usage(str(data_dir)).free
        if free < LOW_DISK_BYTES:
            drive = os.path.splitdrive(str(data_dir))[0] or "C:"
            info = {"kind": "low_disk", "title": "Your disk is almost full",
                    "why": f"Only {free // (1024 * 1024)} MB are left on drive {drive}. The program can still start, but saving prompts, settings and images may fail soon.",
                    "todo": ["Free some space: delete old images or downloads and empty the Recycle Bin.", "The program keeps working meanwhile."]}
            show_error(ft, page, app_name, version, f"Low disk space: {free} bytes free on {drive}", None, info=info, log=False)
    except Exception:
        pass


# ── a failure while the program starts: a full-window page ─────────────────────────────────────────────
def _show_fatal(ft, page, app_name: str, version: str, exc: BaseException, retry) -> None:
    try:
        message = f"{type(exc).__name__}: {exc}"
        details = _details_text(app_name, version, message, exc)
        _write_log(details)
        info = classify(exc, message)
        info = dict(info, todo=[t.replace("Continue", "Try again") for t in info["todo"]])      # on this page the button is called Try again
        page.controls.clear()
        try:
            page.overlay.clear()
        except Exception:
            pass
        page.bgcolor = BG
        page.padding = 0

        def _again(e=None):
            page.controls.clear()
            page.update()
            retry()

        content, actions = _body(ft, page, info, details, app_name, version, on_primary=_again, primary_label="Try again", on_close_program=_make_closer(page),
                                 dismiss_note=f"{app_name} could not finish starting.")
        card = ft.Container(content=ft.Column([content, ft.Divider(height=1, color=BORDER), actions], spacing=14, tight=True),
                            width=620, bgcolor=PANEL, border_radius=14, padding=26,
                            border=ft.Border(top=ft.BorderSide(1, BORDER), right=ft.BorderSide(1, BORDER), bottom=ft.BorderSide(1, BORDER), left=ft.BorderSide(1, BORDER)))
        page.add(ft.Container(content=card, alignment=ft.Alignment(0, 0), expand=True))
        page.update()
    except Exception:
        _debug()


# ── the entry point used by every program ──────────────────────────────────────────────────────────────
def guard(ft, main, app_name: str, version: str):
    """Wrap a Flet `main(page)` (sync or async): errors in its handlers become the friendly dialog, an error while it starts becomes a full-window page with Try again."""
    if inspect.iscoroutinefunction(main):
        async def wrapped(page):
            _install(ft, page, app_name, version)
            try:
                await main(page)
            except Exception as exc:
                _show_fatal(ft, page, app_name, version, exc, lambda: page.run_task(wrapped, page))
                return
            _free_space_warning(ft, page, app_name, version)
    else:
        def wrapped(page):
            _install(ft, page, app_name, version)
            try:
                main(page)
            except Exception as exc:
                _show_fatal(ft, page, app_name, version, exc, lambda: wrapped(page))
                return
            _free_space_warning(ft, page, app_name, version)
    wrapped.__name__ = getattr(main, "__name__", "main")
    wrapped.__doc__ = getattr(main, "__doc__", None)
    return wrapped
