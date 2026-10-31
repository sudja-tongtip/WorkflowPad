"""UI language for Music Studio (Thai / English).

The program's texts are written in English in the code. This module translates them where Flet stores them: every text property of a control (a Text's value, a tooltip, a label,
a hint, a button's text ...) goes through `tr`, so one dictionary (locales/<lang>.json) covers the whole window, including texts built while the program runs. The original English
text is remembered on the control, so changing the language rewrites the open window in place (`refresh`).

What is translated and what is not (the table in the project notes):
  - menus, buttons, dialogs, tooltips, status lines: translated (th.json). English is the identity (en.json has nothing to add).
  - the lyrics, the captions, the prompts and everything sent to ACE-Step or an LLM are never touched: this module only sees text a control shows.

A locale file holds three parts:
  "strings"  exact English text -> translation
  "prefixes" English text that STARTS a longer text ("The last analysis failed: ") -> translation of that start; the rest of the text is kept as it is
  "patterns" regular expression -> template (\\1, \\2 ... are the groups) for texts with a number or name inside
"""
from __future__ import annotations

import json
import os
import re
import weakref
from pathlib import Path

LOCALES = Path(__file__).with_name("locales")
LANGS = ("th", "en")
DEFAULT_LANG = "th"

_lang = {"code": DEFAULT_LANG}
_data: dict = {}                       # code -> {"strings": {}, "prefixes": [(prefix, text)], "patterns": [(re, template)]}
_tracked: list = []                    # weakrefs of the controls that hold a translated text
_missing: dict = {}                    # untranslated texts seen while the language is not English (only when MS_I18N_LOG is set)
_FIELDS = {"tooltip", "label", "hint_text", "helper_text", "helper", "error_text", "text", "title", "subtitle", "message", "content", "value"}
_installed = {"done": False}


def lang() -> str:
    return _lang["code"]


def _load(code: str) -> dict:
    if code in _data:
        return _data[code]
    d = {"strings": {}, "prefixes": [], "patterns": []}
    try:
        raw = json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))
        d["strings"] = dict(raw.get("strings") or {})
        d["prefixes"] = sorted(((k, v) for k, v in (raw.get("prefixes") or {}).items()), key=lambda kv: -len(kv[0]))
        pats = [(re.compile(p), t) for p, t in (raw.get("patterns") or [])]
        d["patterns"] = sorted(pats, key=lambda pt: -len(re.sub(r"\(\.[*+]\??\)|\\", "", pt[0].pattern)))       # the pattern with the most fixed text first: "Line (.*?): (.*?)" must not hide a full message
    except Exception:
        pass                                                     # a missing / damaged locale file: the English text is shown
    _data[code] = d
    return d


def set_lang(code: str) -> str:
    _lang["code"] = code if code in LANGS else DEFAULT_LANG
    _load(_lang["code"])
    return _lang["code"]


def _basic(text: str, d: dict) -> "str | None":
    """The translation of the whole text by an exact entry or a pattern, or None."""
    hit = d["strings"].get(text)
    if hit is not None:
        return hit
    stripped = text.strip()
    if stripped != text and stripped:
        hit = d["strings"].get(stripped)
        if hit is not None:
            i = text.index(stripped)
            return text[:i] + hit + text[i + len(stripped):]
    for rx, tpl in d["patterns"]:
        m = rx.fullmatch(text)
        if m:
            try:
                return m.expand(tpl)
            except Exception:
                break
    return None


def tr(text: str) -> str:
    """The text in the current language (the same text when there is no translation)."""
    code = _lang["code"]
    if code == "en" or not text:
        return text
    d = _load(code)
    hit = _basic(text, d)
    if hit is not None:
        return hit
    for sep in ("\n", " · "):                                      # a help text of several lines, or "All Songs · 25 tracks": each part is translated alone
        if sep in text:
            parts = text.split(sep)
            done = [tr(x) if x.strip() else x for x in parts]
            if done != parts:
                return sep.join(done)
    if text.startswith("• "):                                       # a bullet line of a list of messages
        return "• " + tr(text[2:])
    for prefix, to in d["prefixes"]:
        if text.startswith(prefix):
            rest = text[len(prefix):]
            return to + (tr(rest) if re.search(r"[A-Za-z]{3}", rest) else rest)       # "The last analysis failed: <message>": the message is translated too when it is a known one
    sentences = re.split(r"(?<=[.!?])\s+", text)                    # "Press Analyze Rhymes to update it. The pairs below ...": the longest run of sentences that has a translation, then the rest
    if len(sentences) > 1:
        out, i, changed = [], 0, False
        while i < len(sentences):
            for j in range(len(sentences), i, -1):
                got = _basic(" ".join(sentences[i:j]), d)
                if got is not None:
                    out.append(got); i, changed = j, True
                    break
            else:
                out.append(sentences[i]); i += 1
        if changed:
            return " ".join(out)
    log = os.environ.get("MS_I18N_LOG")
    if log and re.search(r"[A-Za-z]{3}", text) and text not in _missing:         # a development aid: which texts still have no translation
        _missing[text] = 1
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps(text, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return text


def missing() -> dict:
    return dict(_missing)


def _remember(obj, name: str, value: str) -> None:
    store = obj.__dict__.get("_i18n")
    if store is None:
        store = obj.__dict__["_i18n"] = {}
        _tracked.append(weakref.ref(obj))
    store[name] = value


def install(ft) -> None:
    """Hook Flet's property setter once: from now on every text a control receives is translated, and its English original is kept."""
    if _installed["done"]:
        return
    from flet.controls.value_types import Prop
    original = Prop.__set__
    text_cls = ft.Text
    option_cls = ft.DropdownOption
    span_cls = ft.TextSpan                                       # spans carry the user's lyrics (the rhyme colours): never translated

    def _set(self, obj, value):
        if type(value) is str and value and self.name in _FIELDS and (self.name != "value" or isinstance(obj, text_cls)) and not isinstance(obj, span_cls):
            if self.name == "text" and isinstance(obj, option_cls) and obj.key is None:
                obj.key = value                                  # a dropdown option without a key is read back by its text: the key keeps the English original
            _remember(obj, self.name, value)
            value = tr(value)
        original(self, obj, value)
    Prop.__set__ = _set
    _installed["done"] = True


def refresh(page) -> int:
    """Write the current language into every control that holds a translated text, then redraw. Returns how many controls were updated."""
    n = 0
    alive = []
    for ref in _tracked:
        obj = ref()
        if obj is None:
            continue
        alive.append(ref)
        store = obj.__dict__.get("_i18n") or {}
        for name, english in list(store.items()):
            try:
                setattr(obj, name, english)                      # goes through the hook again: translated for the current language
                n += 1
            except Exception:
                pass
    _tracked[:] = alive
    try:
        page.update()
    except Exception:
        pass
    return n
