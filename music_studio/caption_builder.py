"""Basic mode: the multi-select Music Caption Builder (pure Python: no UI, no LLM, no network).

    User selects chips  ->  compose()  ->  Caption Preview  ->  ACE-Step

The caption is built ONLY from the English phrases of the catalog (catalogs/basic_caption_catalog_v1.json: an editable data file, not code), from the user's own custom phrases and from the
user's Additional Notes. Nothing is translated, paraphrased, completed or reordered by an AI. The same selection always gives the same caption.

A selection is stored per song as  {section_id: [entry, ...]}  where an entry is a stable item id ("genre_14") or a custom phrase written "~My phrase". The order inside a section is the order
the user chose (the first genre is the main genre). The order of the sections is COMPOSE_ORDER. Exact duplicates (same id, or the same phrase ignoring case) are written once.

Builtin presets come from the catalog and are never written to. The user's own presets live in their own file (never inside the catalog), so updating the program cannot overwrite them.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

CATALOG_FILE = Path(__file__).with_name("catalogs") / "basic_caption_catalog_v1.json"
CUSTOM = "~"                                          # an entry that starts with this is a custom phrase
COMPOSE_ORDER = ("genre", "mood", "energy", "groove", "vocal_setup", "vocal_character", "backing", "drums", "bass", "instruments", "production", "scene")
UI_CARDS = (("style", "Style", ("genre", "mood", "energy", "groove")),
            ("vocal", "Vocal Setup", ("vocal_setup", "vocal_character", "backing")),
            ("sound", "Sound & Production", ("drums", "bass", "instruments", "production", "scene")))
SINGLE = {"energy"}                                   # one value at a time. Everything else is multi-select, including Lead Vocal Setup (roles are mixed: Male Rap + Female Sung Hook ...)
CAPTION_GUIDE_MAX = 512                               # the reference inference guide's limit of the caption field (the ComfyUI node itself declares none): warn above it, never cut
CAPTION_HARD_MAX = 1200                               # the same limit as the Music Style Prompt of the Advanced tab: Generate is refused above it, the caption is still never cut
NOTES_MAX = 400


class CatalogError(Exception):
    pass


@dataclass(frozen=True)
class Item:
    id: str
    section: str
    en: str
    th: str
    provenance: str = ""


@dataclass
class Section:
    id: str
    name_en: str
    name_th: str
    suggested_max: int
    items: list
    single: bool = False
    featured: list = None                         # ids shown before "more" (None: the first items)


class Catalog:
    def __init__(self, raw: dict):
        self.version = str(raw.get("catalog_version", ""))
        self.sections: dict = {}
        self.items: dict = {}
        self._by_phrase: dict = {}
        for s in raw.get("sections") or []:
            sec = Section(s["id"], s.get("name_en", s["id"]), s.get("name_th", ""), int(s.get("suggested_max") or 0), [], s["id"] in SINGLE)
            for it in s.get("items") or []:
                item = Item(it["id"], sec.id, it["caption_en"], it.get("label_th") or it["caption_en"], it.get("provenance", ""))
                if item.id in self.items:
                    raise CatalogError(f"duplicate item id {item.id}")
                self.items[item.id] = item
                sec.items.append(item)
                self._by_phrase.setdefault(item.en.casefold(), item)
            self.sections[sec.id] = sec
        missing = [k for k in COMPOSE_ORDER if k not in self.sections]
        if missing:
            raise CatalogError("catalog has no section " + ", ".join(missing))
        for sid, ids in (raw.get("featured") or {}).items():
            if sid in self.sections and isinstance(ids, list):
                self.sections[sid].featured = [i for i in ids if i in self.items]
        self.order = [k for k in COMPOSE_ORDER if k in self.sections] + [k for k in self.sections if k not in COMPOSE_ORDER]
        self.builtin = []
        for p in raw.get("presets") or []:
            sel, notes, custom = self.selection_from_phrases(p.get("phrases") or [])
            self.builtin.append({"id": "builtin:" + re.sub(r"[^a-z0-9]+", "-", p["name"].casefold()).strip("-"), "name": p["name"], "builtin": True, "sel": sel, "notes": "",
                                 "phrases": list(p.get("phrases") or []), "custom_phrases": custom})

    def find_phrase(self, phrase: str):
        return self._by_phrase.get((phrase or "").strip().casefold())

    def selection_from_phrases(self, phrases) -> tuple:
        """(selection, notes, custom phrases). A phrase that is in the catalog becomes its id. One that is not (a combined or new phrase) is KEPT as a custom phrase in the section it
        is most likely for (never dropped): the caller can show the list of them."""
        sel: dict = {}
        custom = []
        for ph in phrases:
            it = self.find_phrase(ph)
            if it:
                sel.setdefault(it.section, []).append(it.id)
            elif (ph or "").strip():
                sel.setdefault("instruments", []).append(CUSTOM + ph.strip())
                custom.append(ph.strip())
        return sel, "", custom

    def label(self, entry: str, lang: str = "en") -> str:
        if entry.startswith(CUSTOM):
            return entry[len(CUSTOM):]
        it = self.items.get(entry)
        if not it:
            return entry
        return it.th if lang == "th" else it.en

    def phrase(self, entry: str) -> str:
        """The English caption phrase of an entry ('' for an id the catalog no longer has)."""
        if entry.startswith(CUSTOM):
            return entry[len(CUSTOM):].strip()
        it = self.items.get(entry)
        return it.en if it else ""

    def section_label(self, sid: str, lang: str = "en") -> str:
        s = self.sections[sid]
        return s.name_th if lang == "th" and s.name_th else s.name_en


_cache: dict = {}


def load_catalog(path: Path | None = None) -> Catalog:
    p = Path(path or CATALOG_FILE)
    key = str(p)
    if key not in _cache:
        try:
            _cache[key] = Catalog(json.loads(p.read_text(encoding="utf-8")))
        except CatalogError:
            raise
        except Exception as ex:
            raise CatalogError(f"The caption catalog {p.name} cannot be read: {ex}") from None
    return _cache[key]


# ── selection ────────────────────────────────────────────────────────────────────────────────────────────
def copy_sel(sel) -> dict:
    return {k: list(v) for k, v in (sel or {}).items() if isinstance(v, (list, tuple)) and v}


def _norm(entry: str, cat: Catalog) -> str:
    """The id for a typed phrase that IS a catalog phrase, else the entry itself."""
    if entry.startswith(CUSTOM):
        it = cat.find_phrase(entry[len(CUSTOM):])
        return it.id if it else CUSTOM + entry[len(CUSTOM):].strip()
    return entry


def toggle(sel: dict, section: str, entry: str, cat: Catalog) -> dict:
    """A new selection with `entry` switched on or off in `section`. A single-select section (Energy) holds one entry: choosing another replaces it. Nothing else changes."""
    out = copy_sel(sel)
    entry = _norm(entry, cat)
    cur = out.get(section, [])
    if entry in cur:
        cur = [e for e in cur if e != entry]
    elif cat.sections[section].single:
        cur = [entry]
    else:
        cur = cur + [entry]
    if cur:
        out[section] = cur
    else:
        out.pop(section, None)
    return out


def clear_section(sel: dict, section: str) -> dict:
    out = copy_sel(sel)
    out.pop(section, None)
    return out


def count(sel: dict, section: str) -> int:
    return len((sel or {}).get(section, []))


def over_soft_limit(sel: dict, section: str, cat: Catalog) -> bool:
    m = cat.sections[section].suggested_max
    return bool(m) and count(sel, section) > m          # advice only: nothing is ever removed


# ── the composer ─────────────────────────────────────────────────────────────────────────────────────────
def compose(sel: dict, notes: str, cat: Catalog) -> str:
    """The caption: the English phrases of the selected entries in COMPOSE_ORDER (then the Additional Notes), joined by ', '. Deterministic; no AI; adds nothing that was not chosen."""
    parts: list = []
    seen: set = set()
    for sid in cat.order:
        for e in (sel or {}).get(sid, []):
            ph = cat.phrase(e)
            k = ph.casefold()
            if ph and k not in seen:
                seen.add(k)
                parts.append(ph)
    n = " ".join((notes or "").split())
    if n:
        k = n.casefold()
        if k not in seen:
            parts.append(n)
    return ", ".join(parts)


def unresolved(sel: dict, cat: Catalog) -> list:
    """Ids in a stored selection that the catalog does not have (an edited catalog). They are reported, never silently dropped from the stored selection."""
    return [e for ents in (sel or {}).values() for e in ents if not e.startswith(CUSTOM) and e not in cat.items]


def length_status(caption: str) -> tuple:
    """(characters, 'ok' | 'long' | 'too_long')."""
    n = len(caption or "")
    return n, ("too_long" if n > CAPTION_HARD_MAX else "long" if n > CAPTION_GUIDE_MAX else "ok")


# ── conflicts: a few clear rules, shown as a warning, never fixed for the user ───────────────────────────────
_DENSE = ("Orchestral Strings", "Brass Section", "Lush Strings", "Soft Choir")


def conflicts(sel: dict, cat: Catalog) -> list:
    """Short English warnings for clearly contradicting choices. Normal mixes (Trap + R&B, Male Rap + Female Hook) never warn. Nothing is changed."""
    ph = {s: {cat.phrase(e).casefold() for e in (sel or {}).get(s, [])} for s in cat.sections}
    allp = set().union(*ph.values()) if ph else set()
    out = []
    if "male lead vocals" in ph.get("vocal_setup", ()) and "female lead vocals" in ph.get("vocal_setup", ()):
        out.append("Male Lead and Female Lead are both selected. Choose Duet if you want both.")
    inst = "instrumental, no vocals"
    if inst in ph.get("vocal_setup", ()) and (len(ph["vocal_setup"]) > 1 or ph.get("vocal_character") or ph.get("backing")):
        out.append("Instrumental (no vocals) is selected together with vocal choices.")
    if "minimal stripped-back arrangement" in allp and any(d.casefold() in allp for d in _DENSE):
        out.append("Minimal Stripped-Back Arrangement is selected together with a dense, layered sound.")
    return out


# ── the song's selection (with the old Basic fields carried over) ────────────────────────────────────────────
_LEGACY_VOCAL = {"male vocal": "Male Lead Vocals", "female vocal": "Female Lead Vocals", "duet vocals": "Male and Female Duet"}
_REVERSE_VOCAL = {"Male Lead Vocals": "male vocal", "Female Lead Vocals": "female vocal", "Male and Female Duet": "duet vocals"}


def _legacy_entries(text: str, cat: Catalog, section: str) -> list:
    out = []
    for part in re.split(r"\s*,\s*", (text or "").strip()):
        if not part:
            continue
        it = cat.find_phrase(part)
        out.append(it.id if it else CUSTOM + part)
    return out


def migrate_legacy(st, cat: Catalog) -> tuple:
    """(selection, notes) made from the old Basic fields (genre / mood / vocal / energy / extra tags) of a song saved before the builder existed. Pure: the song is not changed."""
    sel: dict = {}
    for sec, text in (("genre", getattr(st, "genre", "")), ("mood", getattr(st, "mood", "")), ("energy", getattr(st, "energy", ""))):
        ent = _legacy_entries(text, cat, sec)
        if ent:
            sel[sec] = ent[:1] if sec == "energy" else ent
    v = (getattr(st, "vocal", "") or "").strip()
    if v:
        mapped = _LEGACY_VOCAL.get(v.casefold())
        it = cat.find_phrase(mapped or v)
        sel["vocal_setup"] = [it.id if it else CUSTOM + v]
    return sel, (getattr(st, "extra_tags", "") or "").strip()


def effective(st, cat: Catalog | None = None) -> tuple:
    """(selection, notes, preset id) the song really has: its own builder selection, or — for a song that never used the builder — the old Basic fields carried over."""
    cat = cat or load_catalog()
    if int(getattr(st, "basic_ver", 0) or 0) >= 1:
        return copy_sel(getattr(st, "basic_sel", {})), str(getattr(st, "basic_notes", "") or ""), str(getattr(st, "basic_preset", "") or "")
    sel, notes = migrate_legacy(st, cat)
    return sel, notes, ""


def caption_for(st, cat: Catalog | None = None) -> str:
    """THE Basic caption of a song: the Preview shows this and Generate sends this."""
    cat = cat or load_catalog()
    sel, notes, _p = effective(st, cat)
    return compose(sel, notes, cat)


def store(st, sel: dict, notes: str, preset: str, cat: Catalog | None = None) -> None:
    """Write the selection into the song's settings and keep the old Basic fields in step (the library list, the AI reference and the chat read them)."""
    cat = cat or load_catalog()
    st.basic_ver, st.basic_sel, st.basic_notes, st.basic_preset = 1, copy_sel(sel), str(notes or ""), preset or ""
    joined = lambda sec: ", ".join(p for p in (cat.phrase(e) for e in sel.get(sec, [])) if p)
    st.genre, st.mood, st.energy, st.extra_tags = joined("genre"), joined("mood"), joined("energy"), (notes or "").strip()
    lead = [cat.phrase(e) for e in sel.get("vocal_setup", [])]
    st.vocal = _REVERSE_VOCAL.get(lead[0], "") if len(lead) == 1 else ""


# ── presets ──────────────────────────────────────────────────────────────────────────────────────────────
def find_preset(pid: str, user: list, cat: Catalog):
    for p in list(cat.builtin) + list(user or []):
        if p.get("id") == pid:
            return p
    return None


def same_selection(a: dict, notes_a: str, b: dict, notes_b: str, cat: Catalog) -> bool:
    """True when two selections give the same caption inputs (the order inside a section does not make a preset 'modified')."""
    norm = lambda s: {k: sorted(_norm(e, cat) for e in v) for k, v in (s or {}).items() if v}
    return norm(a) == norm(b) and " ".join((notes_a or "").split()) == " ".join((notes_b or "").split())


def is_modified(sel: dict, notes: str, preset_id: str, user: list, cat: Catalog) -> bool:
    p = find_preset(preset_id, user, cat) if preset_id else None
    if not p:
        return False
    pn = p.get("notes") or ""
    return not same_selection(sel, notes if not pn else notes, p.get("sel") or {}, pn if pn else notes, cat)   # a preset without notes does not own the Notes box: only its chips decide


USER_PRESETS_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "WorkflowPad" / "music_studio_basic_presets.json"
NAME_MAX = 60


class PresetError(Exception):
    pass


def load_user_presets(path: Path | None = None) -> tuple:
    """(presets, warning). A damaged file is copied to *.corrupt-<time>.json before anything could overwrite it; a damaged entry is skipped and reported."""
    p = Path(path or USER_PRESETS_FILE)
    if not p.exists():
        return [], ""
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        rows = raw["presets"] if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            raise ValueError("presets is not a list")
    except Exception as ex:
        try:
            p.replace(p.with_name(p.stem + time.strftime(".corrupt-%Y%m%d-%H%M%S") + p.suffix))
        except OSError:
            pass
        return [], f"The saved presets file could not be read and was kept as a backup ({ex})."
    out, bad = [], 0
    for r in rows:
        try:
            sel = {k: [str(e) for e in v] for k, v in (r.get("sel") or {}).items() if isinstance(v, list) and v}
            out.append({"id": str(r["id"]), "name": str(r["name"])[:NAME_MAX], "builtin": False, "sel": sel, "notes": str(r.get("notes") or ""),
                        "created": str(r.get("created") or ""), "updated": str(r.get("updated") or "")})
        except Exception:
            bad += 1
    return out, (f"{bad} saved preset(s) could not be read and were skipped." if bad else "")


def save_user_presets(presets: list, path: Path | None = None) -> None:
    p = Path(path or USER_PRESETS_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [{k: v for k, v in x.items() if k in ("id", "name", "sel", "notes", "created", "updated")} for x in presets if not x.get("builtin")]
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"format": "workflowpad-basic-caption-presets", "version": 1, "presets": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def clean_name(name: str) -> str:
    n = " ".join((name or "").split())
    if not n:
        raise PresetError("Give the preset a name.")
    return n[:NAME_MAX]


def unique_name(name: str, user: list, cat: Catalog, exclude_id: str = "") -> str:
    taken = {p["name"].casefold() for p in list(cat.builtin) + list(user) if p.get("id") != exclude_id}
    if name.casefold() not in taken:
        return name
    i = 2
    while f"{name} ({i})".casefold() in taken:
        i += 1
    return f"{name} ({i})"


def new_preset(name: str, sel: dict, notes: str, user: list, cat: Catalog) -> dict:
    """Save As: a NEW user preset (a builtin or another preset is never touched)."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return {"id": "user_" + uuid.uuid4().hex[:10], "name": unique_name(clean_name(name), user, cat), "builtin": False, "sel": copy_sel(sel), "notes": str(notes or ""), "created": now, "updated": now}


def update_preset(preset: dict, sel: dict, notes: str) -> None:
    if preset.get("builtin"):
        raise PresetError("A builtin preset cannot be changed. Use Save As to make your own copy.")
    preset["sel"], preset["notes"], preset["updated"] = copy_sel(sel), str(notes or ""), time.strftime("%Y-%m-%d %H:%M:%S")


def rename_preset(preset: dict, name: str, user: list, cat: Catalog) -> None:
    if preset.get("builtin"):
        raise PresetError("A builtin preset cannot be renamed.")
    preset["name"] = unique_name(clean_name(name), user, cat, exclude_id=preset["id"])
    preset["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")


def delete_preset(preset: dict, user: list) -> list:
    if preset.get("builtin"):
        raise PresetError("A builtin preset cannot be deleted.")
    return [p for p in user if p["id"] != preset["id"]]


# ── an AI Recommend / template Apply that sets the old Basic fields ──────────────────────────────────────────────
LEGACY_FIELDS = ("genre", "mood", "energy", "vocal", "extra_tags")


def follow_legacy(st, changes: dict, cat: Catalog | None = None) -> None:
    """Something outside the builder (AI Recommend Apply / Restore, a template) set the old Basic fields of a song that uses the builder: carry those values into the matching
    categories, so Basic shows (and sends) them. Only the categories that were changed are touched."""
    if int(getattr(st, "basic_ver", 0) or 0) < 1 or not any(k in changes for k in LEGACY_FIELDS):
        return
    cat = cat or load_catalog()
    sel = copy_sel(getattr(st, "basic_sel", {}))
    notes = str(getattr(st, "basic_notes", "") or "")
    for key in ("genre", "mood", "energy"):
        if key in changes:
            ent = _legacy_entries(changes[key], cat, key)
            ent = ent[:1] if key == "energy" else ent
            if ent:
                sel[key] = ent
            else:
                sel.pop(key, None)
    if "vocal" in changes:
        v = (changes["vocal"] or "").strip()
        if v:
            it = cat.find_phrase(_LEGACY_VOCAL.get(v.casefold(), v))
            sel["vocal_setup"] = [it.id if it else CUSTOM + v]
        else:
            sel.pop("vocal_setup", None)
    if "extra_tags" in changes:
        notes = str(changes["extra_tags"] or "").strip()
    store(st, sel, notes, getattr(st, "basic_preset", ""), cat)
