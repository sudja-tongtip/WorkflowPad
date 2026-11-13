"""Shared by the UI-facing tests: builds the real window of music_studio.py on a fake Flet page (in a temporary app-data folder) and finds the closures of main() by walking the control handlers.
No window is opened and nothing real is touched: it shows the control TREE the app builds, not pixels."""
import asyncio
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class FakePage:
    def __init__(self):
        self.controls, self.overlay, self.data = [], [], {}
        self.width, self.height = 1500, 900
        self.theme_mode = self.theme = self.dark_theme = None
        self.window = type("W", (), {"width": 1500, "height": 900, "min_width": 0, "min_height": 0, "on_event": None, "icon": None, "title_bar_hidden": False})()
        self.session = type("S", (), {"id": "x"})()
        self.services, self.pubsub, self.client_ip = [], None, "127.0.0.1"
        self.ran = []

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)

        def f(*a, **k):
            if n in ("run_task", "run_thread"):
                self.ran.append(a[0] if a else None)
                try:
                    r = a[0](*a[1:], **k)
                    if asyncio.iscoroutine(r):
                        r.close()
                except Exception:
                    pass
            return None
        return f

    def update(self, *a, **k):
        pass

    def add(self, *c):
        self.controls += list(c)


class App:
    def __init__(self):
        tmp = tempfile.mkdtemp()
        for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
            os.environ[k] = tmp
        sys.path.insert(0, str(ROOT))
        import flet as ft
        self.ft = ft
        spec = importlib.util.spec_from_file_location("ms_app", str(ROOT / "music_studio.py"))
        self.M = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.M)
        self.page = FakePage()
        self.M.main(self.page)
        self.found, self._seen, self.cells = {}, set(), {}
        self.refresh()

    def _visit(self, fn, depth=0):
        if id(fn) in self._seen or depth > 40:
            return
        self._seen.add(id(fn))
        self.found.setdefault(fn.__name__, fn)
        for cell in (fn.__closure__ or ()):
            try:
                v = cell.cell_contents
            except ValueError:
                continue
            if isinstance(v, types.FunctionType):
                self._visit(v, depth + 1)

    def _walk(self, c, depth=0):
        ft = self.ft
        if c is None or depth > 40:
            return
        for attr in ("on_click", "on_change", "on_submit", "on_focus", "on_blur", "on_tap"):
            fn = getattr(c, attr, None)
            if isinstance(fn, types.FunctionType):
                self._visit(fn)
        for attr in ("content", "controls", "tabs", "actions", "leading", "title", "trailing"):
            v = getattr(c, attr, None)
            if isinstance(v, (list, tuple)):
                for x in v:
                    self._walk(x, depth + 1)
            elif v is not None and (hasattr(v, "__dict__") or isinstance(v, ft.Control)):
                self._walk(v, depth + 1)

    def refresh(self):
        for c in self.page.controls + self.page.overlay:
            self._walk(c)
        for _ in range(30):
            for fn in list(self.found.values()):
                self._visit(fn)
        self.cells.clear()
        for fn in self.found.values():
            for name, cell in zip(fn.__code__.co_freevars, fn.__closure__ or ()):
                self.cells.setdefault(name, []).append(cell)

    def set_cell(self, name, value):
        for cell in self.cells.get(name, []):
            cell.cell_contents = value

    def get(self, name):
        return self.cells[name][0].cell_contents

    # ── reading a control tree ──
    def texts(self, ctrl, skip_tiles=True, out=None, depth=0):
        """The visible text of a control tree (the TextSpans of a Text are joined). A closed ExpansionTile keeps its children out of the result when `skip_tiles`."""
        ft = self.ft
        out = [] if out is None else out
        if ctrl is None or depth > 80:
            return out
        if isinstance(ctrl, ft.Text):
            out.append((ctrl.value or "") + "".join(getattr(s, "text", "") or "" for s in (ctrl.spans or [])))
        if isinstance(ctrl, ft.ExpansionTile):
            title = ctrl.title
            if isinstance(title, ft.Control):
                self.texts(title, skip_tiles, out, depth + 1)
            if not skip_tiles or ctrl.expanded:                     # a CLOSED tile keeps its children out of what is visible
                for x in ctrl.controls or []:
                    self.texts(x, skip_tiles, out, depth + 1)
            return out
        for attr in ("content", "controls"):
            sub = getattr(ctrl, attr, None)
            if isinstance(sub, (list, tuple)):
                for x in sub:
                    self.texts(x, skip_tiles, out, depth + 1)
            elif sub is not None and isinstance(sub, ft.Control):
                self.texts(sub, skip_tiles, out, depth + 1)
        return out

    def find(self, ctrl, cls, out=None, depth=0):
        ft = self.ft
        out = [] if out is None else out
        if ctrl is None or depth > 80:
            return out
        if isinstance(ctrl, cls):
            out.append(ctrl)
        subs = []
        for attr in ("content", "controls", "title"):
            sub = getattr(ctrl, attr, None)
            if isinstance(sub, (list, tuple)):
                subs += list(sub)
            elif sub is not None and isinstance(sub, ft.Control):
                subs.append(sub)
        for x in subs:
            self.find(x, cls, out, depth + 1)
        return out
