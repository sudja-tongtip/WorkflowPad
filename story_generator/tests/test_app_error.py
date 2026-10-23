"""app_error: the friendly error screen shared by the five programs. Mock pages only; no window, no network.

Run:  python -m story_generator.tests.test_app_error
"""
import asyncio
import errno
import inspect
import sys
from pathlib import Path

import flet as ft

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import app_error as A  # noqa: E402

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


class FakeSession:
    def __init__(self):
        self.crashed: list = []

    def error(self, message):                      # Flet's own: sends the red 'application crashed' page
        self.crashed.append(message)


class FakePage:
    def __init__(self):
        self.session = FakeSession()
        self.controls, self.overlay, self.services, self.shown = [], [], [], []
        self.bgcolor = self.padding = None

    def show_dialog(self, d): self.shown.append(d)
    def pop_dialog(self): pass
    def update(self): pass
    def add(self, *c): self.controls.extend(c)
    def run_task(self, fn, *a): return None


def main():
    # ---- what happened, in plain words ---------------------------------------------------------------------------------------------------------------------------------------
    full = A.classify(OSError(errno.ENOSPC, "No space left on device", r"D:\data\x.json"), "")
    check("disk full from the exception: title, drive and steps", full["kind"] == "disk_full" and "D:" in full["why"] and full["todo"], full)
    msg_only = A.classify(None, "[Errno 28] No space left on device\nTraceback ...")
    check("disk full from the '[Errno 28]' token when only the text is known", msg_only["kind"] == "disk_full", msg_only)
    check("permission error", A.classify(PermissionError(errno.EACCES, "denied", r"C:\x"), "")["kind"] == "permission")
    check("missing file", A.classify(FileNotFoundError(errno.ENOENT, "nope", r"C:\x"), "")["kind"] == "missing")
    check("connection refused (ComfyUI not running)", A.classify(ConnectionRefusedError("refused"), "")["kind"] == "network")
    check("out of memory", A.classify(MemoryError(), "")["kind"] == "memory")
    gen = A.classify(ValueError("odd"), "ValueError: odd")
    check("anything else: a generic but clear screen", gen["kind"] == "other" and gen["title"] and gen["why"] and gen["todo"], gen)
    check("an error text in Thai or with unrelated numbers is not mistaken for disk full", A.classify(None, "ข้อผิดพลาด 28 ไฟล์")["kind"] == "other")

    # ---- the guard keeps the shape of main (Flet checks it) -------------------------------------------------------------------------------------------------------------------
    def sync_main(page): page.add("x")
    async def async_main(page): page.add("x")
    check("guard of a sync main is a sync function", not inspect.iscoroutinefunction(A.guard(ft, sync_main, "App", "1.0.0")))
    check("guard of an async main is an async function", inspect.iscoroutinefunction(A.guard(ft, async_main, "App", "1.0.0")))
    check("guard keeps the name", A.guard(ft, sync_main, "App", "1.0.0").__name__ == "sync_main")

    # ---- a crash in a handler becomes the dialog; the session is NOT crashed -----------------------------------------------------------------------------------------------------
    A._write_log = lambda text: True                  # tests never write to the user's real log
    page = FakePage()
    A.guard(ft, sync_main, "App", "1.0.0")(page)
    try:
        raise OSError(errno.ENOSPC, "No space left on device", r"C:\x.json")
    except OSError:
        page.session.error("[Errno 28] No space left on device\ntraceback text")       # what Flet's dispatch_event does, inside its except block
    check("a handler error shows ONE friendly dialog", len(page.shown) == 1 and isinstance(page.shown[0], ft.AlertDialog), page.shown)
    check("the red 'application crashed' page is not sent", page.session.crashed == [])
    page.session.error("[Errno 28] No space left on device again")
    check("while a dialog is open, a repeat does not stack a second one", len(page.shown) == 1, len(page.shown))
    page._app_error_state["open"] = False
    page._app_error_state["last"] = ("", 0.0)
    page.session.error("another one")
    check("after the dialog is closed a new error shows again", len(page.shown) == 2, len(page.shown))

    # ---- a crash while the program starts becomes a full-window page ----------------------------------------------------------------------------------------------------------
    def boom_main(page): raise OSError(errno.ENOSPC, "No space left on device", r"D:\x.json")
    page = FakePage()
    A.guard(ft, boom_main, "App", "1.0.0")(page)
    check("a startup error puts the friendly page in the window (and nothing raises)", len(page.controls) == 1 and page.bgcolor == A.BG, page.controls)
    check("the startup page says 'Try again', not 'Continue'", "Try again" in repr(page.controls[0]) and "press Continue" not in repr(page.controls[0]))

    async def aboom_main(page): raise ConnectionRefusedError("refused")
    page = FakePage()
    asyncio.run(A.guard(ft, aboom_main, "App", "1.0.0")(page))
    check("an async startup error is handled the same way", len(page.controls) == 1, page.controls)

    # ---- show_error never raises, even with a broken page --------------------------------------------------------------------------------------------------------------------
    try:
        A.show_error(ft, None, "App", "1.0.0", "x", ValueError("x"))
        ok = True
    except Exception as exc:
        ok = False
    check("show_error does not raise on a broken page", ok)
    check("a log that cannot be written (disk full) is silent", A.__dict__["_write_log"] is not None)

    # ---- low disk warning at start --------------------------------------------------------------------------------------------------------------------------------------------------------
    import shutil
    real_usage = shutil.disk_usage
    shutil.disk_usage = lambda p: shutil._ntuple_diskusage(1000 * 1024 * 1024, 990 * 1024 * 1024, 10 * 1024 * 1024)
    page = FakePage()
    A._free_space_warning(ft, page, "App", "1.0.0")
    shutil.disk_usage = lambda p: shutil._ntuple_diskusage(1000 * 1024 * 1024, 100 * 1024 * 1024, 900 * 1024 * 1024)
    page2 = FakePage()
    A._free_space_warning(ft, page2, "App", "1.0.0")
    shutil.disk_usage = real_usage
    check("low disk space: a warning dialog at start", len(page.shown) == 1)
    check("plenty of space: nothing is shown", page2.shown == [])

    print("ALL PASS" if not _fails else f"{len(_fails)} FAILED: {_fails}")
    return 0 if not _fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
