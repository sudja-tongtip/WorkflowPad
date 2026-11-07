"""One text-size setting for the whole program.

How it works: Flet writes every property through one descriptor (`Prop.__set__`). `install()` wraps it once, in the same way the translator does: when a text size
(`size` of a Text / TextSpan / TextStyle, `text_size` of any field, the `height` of a field / dropdown / button) is written, the control keeps the value the code wrote (its BASE
value) and receives base * factor. So the several hundred `size=` written in the code, the styles of labels and hints, the lyrics editor and the report spans all follow the setting
without being edited. `apply()` writes every kept base value again with a new factor, which is how a change takes effect at once, without a restart.

At 100% nothing is changed: the value is passed on untouched. A Text that has no size of its own takes the size of the page theme, so `theme()` gives the page a text theme scaled
the same way (None at 100%).
"""
from __future__ import annotations

import weakref

DEFAULT = 100
CHOICES = (85, 100, 115, 130, 150)                       # percent, the values offered in Settings
MIN_PCT, MAX_PCT = 70, 200                               # anything saved outside this range is pulled back into it
_LABELS = {85: "Small (85%)", 100: "Normal (100%)", 115: "Large (115%)", 130: "Larger (130%)", 150: "Largest (150%)"}

_state = {"installed": False, "factor": 1.0, "pct": DEFAULT}
_tracked: list = []                                      # weak references to the controls / styles that hold a base value
_SIZE_PROPS = ("size", "text_size")
_HEIGHT_CLASSES = ("TextField", "Dropdown", "Button", "FilledButton", "OutlinedButton", "TextButton", "ElevatedButton", "FilledTonalButton")
_SIZE_CLASSES = ("Text", "TextSpan", "TextStyle")


def label(pct: int) -> str:
    return _LABELS.get(pct, f"{pct}%")


def normalize(value) -> int:
    """The percent for any stored value: empty / missing / not a number -> 100; a number outside the range is pulled into it. Other values are kept as they are (a hand-edited 120 stays 120)."""
    try:
        if isinstance(value, bool) or value is None or (isinstance(value, str) and not value.strip()):
            return DEFAULT
        pct = int(round(float(str(value).replace("%", "").strip())))
    except Exception:
        return DEFAULT
    return max(MIN_PCT, min(MAX_PCT, pct))


def factor() -> float:
    return _state["factor"]


def percent() -> int:
    return _state["pct"]


def scale(base, f: float | None = None):
    """base * factor, in half-pixel steps. At 100% (or for a value that is not a plain number) the value is returned as it came."""
    f = _state["factor"] if f is None else f
    if f == 1.0 or isinstance(base, bool) or not isinstance(base, (int, float)):
        return base
    out = round(base * f * 2) / 2
    return int(out) if out == int(out) else out


def _is(obj, names) -> bool:
    return type(obj).__name__ in names or any(c.__name__ in names for c in type(obj).__mro__)


def _scalable(obj, name: str) -> bool:
    if name == "text_size":
        return True
    if name == "size":
        return _is(obj, _SIZE_CLASSES)
    if name == "height":
        return _is(obj, _HEIGHT_CLASSES)
    return False


def _remember(obj, name: str, base) -> None:
    try:
        store = obj.__dict__.get("_fs_base")
        if store is None:
            store = obj.__dict__["_fs_base"] = {}
            _tracked.append(weakref.ref(obj))
        store[name] = base
    except Exception:
        pass


def install(ft) -> None:
    """Wrap Flet's property setter once. Call it after the translator's install()."""
    if _state["installed"]:
        return
    from flet.controls.value_types import Prop
    original = Prop.__set__

    def _set(self, obj, value):
        name = self.name
        if (name in _SIZE_PROPS or name == "height") and isinstance(value, (int, float)) and not isinstance(value, bool) and _scalable(obj, name):
            _remember(obj, name, value)
            value = scale(value)
        original(self, obj, value)
    Prop.__set__ = _set
    _state["installed"] = True


def theme(ft):
    """A page theme whose default text sizes follow the factor (a Text with no size of its own uses them); None at 100% so the program keeps its default theme."""
    if _state["factor"] == 1.0:
        return None
    base = {      # the style sizes below are the 100% sizes: the hook multiplies them by the factor as they are written
        "body_large": 16, "body_medium": 14, "body_small": 12, "label_large": 14, "label_medium": 12, "label_small": 11,
            "title_large": 22, "title_medium": 16, "title_small": 14, "headline_small": 24, "headline_medium": 28, "headline_large": 32,
            "display_small": 36, "display_medium": 45, "display_large": 57}
    try:
        return ft.Theme(text_theme=ft.TextTheme(**{k: ft.TextStyle(size=v) for k, v in base.items()}))
    except Exception:
        return None


def apply(ft, page, pct) -> int:
    """Use a new size on everything that already exists, and on what is built later. Returns how many values were written again."""
    pct = normalize(pct)
    _state["pct"], _state["factor"] = pct, pct / 100.0
    n = 0
    alive = []
    for ref in _tracked:
        obj = ref()
        if obj is None:
            continue
        alive.append(ref)
        for name, base in list((obj.__dict__.get("_fs_base") or {}).items()):
            try:
                setattr(obj, name, base)                  # goes through the hook again: base * the new factor
                n += 1
            except Exception:
                pass
    _tracked[:] = alive
    if page is not None:
        try:
            th = theme(ft)
            page.theme = th
            page.dark_theme = th
            page.update()
        except Exception:
            pass
    return n


def set_initial(pct) -> None:
    """Before any control is built (program start): only the factor, nothing to rewrite yet."""
    pct = normalize(pct)
    _state["pct"], _state["factor"] = pct, pct / 100.0
