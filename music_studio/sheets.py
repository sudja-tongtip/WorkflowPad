"""Lyrics Sheets: several drafts (lyrics + generation options) inside ONE song project. Pure functions, no UI, no I/O (store.py reads / writes sheets.json).

The Song object stays the editor's working copy: song.lyrics / song.settings ARE the active sheet while it is being edited. `flush` copies them into the active Sheet and
`switch` loads another Sheet into them, so the whole editor (undo, analysis, generation, autosave) keeps working on the same two fields and there is never a second
place being edited. A sheet is never identified by its name: ids are unique, names may repeat (the UI shows them as "Pop AI (2)").
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, fields

from .model import Song, SongSettings, Sheet, SheetBook, new_id, now_str

NAME_MAX = 40


class SheetError(ValueError):
    """A sentence for the user."""


def settings_to_dict(s: SongSettings) -> dict:
    return copy.deepcopy(asdict(s))


def settings_from_dict(d) -> SongSettings:
    """Every field of SongSettings that the dict knows; missing fields keep their defaults and unknown keys are ignored (a file from another version loads, nothing is invented)."""
    names = {f.name for f in fields(SongSettings)}
    return SongSettings(**{k: copy.deepcopy(v) for k, v in (d or {}).items() if k in names})


def clean_name(name: str) -> str:
    n = " ".join((name or "").split())
    if not n:
        raise SheetError("Give the sheet a name.")
    return n[:NAME_MAX]


def unique_name(book: SheetBook, name: str, exclude_id: str = "") -> str:
    """The name as typed when it is free, else 'Name (2)', 'Name (3)'... Only for display: the id is the identity."""
    taken = {s.name.casefold() for s in book.sheets if s.id != exclude_id}
    if name.casefold() not in taken:
        return name
    k = 2
    while f"{name} ({k})".casefold() in taken:
        k += 1
    return f"{name} ({k})"


def sheet_of(book: SheetBook, sheet_id: str) -> "Sheet | None":
    return next((s for s in book.sheets if s.id == sheet_id), None)


def new_book(song: Song) -> SheetBook:
    """The first sheet, 'Original', made from the song exactly as it is."""
    s = Sheet(name="Original", lyrics=song.lyrics, settings=settings_to_dict(song.settings), created=song.created, updated=song.updated)
    return SheetBook(sheets=[s], active_id=s.id, open_ids=[s.id], primary_id=s.id)


def ensure_book(song: Song) -> SheetBook:
    if song.book is None or not song.book.sheets:
        song.book = new_book(song)
    return song.book


def active_sheet(song: Song) -> Sheet:
    book = ensure_book(song)
    return sheet_of(book, book.active_id) or book.sheets[0]


def flush(song: Song) -> bool:
    """Copy the editor's working copy into the active sheet. True when the sheet changed."""
    sh = active_sheet(song)
    known = {f.name for f in fields(SongSettings)}
    sd = {**{k: v for k, v in sh.settings.items() if k not in known}, **settings_to_dict(song.settings)}      # a key written by a newer version is carried along, not dropped
    if sh.lyrics == song.lyrics and sh.settings == sd:
        return False
    sh.lyrics, sh.settings, sh.updated = song.lyrics, sd, now_str()
    return True


def load_active(song: Song) -> None:
    """The other direction (after loading a book): the active sheet becomes the editor's working copy."""
    sh = active_sheet(song)
    song.book.active_id = sh.id
    song.lyrics, song.settings = sh.lyrics, settings_from_dict(sh.settings)


def switch(song: Song, sheet_id: str) -> Sheet:
    """Flush the active sheet, then make `sheet_id` the active one. A sheet that was closed (hidden) is shown again."""
    book = ensure_book(song)
    target = sheet_of(book, sheet_id)
    if target is None:
        raise SheetError("That sheet no longer exists.")
    if target.id == book.active_id:
        return target
    flush(song)
    book.active_id = target.id
    if target.id not in book.open_ids:
        book.open_ids.append(target.id)
    song.lyrics, song.settings = target.lyrics, settings_from_dict(target.settings)
    return target


def _add(song: Song, sh: Sheet, activate: bool = True) -> Sheet:
    book = ensure_book(song)
    flush(song)
    sh.name = unique_name(book, sh.name)
    book.sheets.append(sh)
    book.open_ids.append(sh.id)
    if activate:
        switch(song, sh.id)
    return sh


def new_blank(song: Song, name: str = "New Sheet", lyrics: str = "") -> Sheet:
    """A blank draft that keeps the generation options of the sheet it was made from (a new sheet is not a new song, but it starts from the same options)."""
    flush(song)
    return _add(song, Sheet(name=clean_name(name), lyrics=lyrics, settings=settings_to_dict(song.settings)))


def duplicate(song: Song, sheet_id: str, name: str = "") -> Sheet:
    """A copy with a NEW id (lyrics + options). The copy is not linked to the original: later edits of one never touch the other."""
    book = ensure_book(song)
    flush(song)
    src = sheet_of(book, sheet_id)
    if src is None:
        raise SheetError("That sheet no longer exists.")
    return _add(song, Sheet(name=clean_name(name or f"{src.name} Copy"), lyrics=src.lyrics, settings=copy.deepcopy(src.settings), source_take_id=src.source_take_id))


def from_snapshot(song: Song, name: str, lyrics: str, settings: dict, source_take_id: str | None, activate: bool = True) -> Sheet:
    """A new sheet made from data that was captured earlier (a take's snapshot, an AI arrangement's request snapshot)."""
    flush(song)
    return _add(song, Sheet(name=clean_name(name), lyrics=lyrics, settings=copy.deepcopy(settings), source_take_id=source_take_id), activate)


def rename(song: Song, sheet_id: str, name: str) -> Sheet:
    book = ensure_book(song)
    sh = sheet_of(book, sheet_id)
    if sh is None:
        raise SheetError("That sheet no longer exists.")
    sh.name = unique_name(book, clean_name(name), exclude_id=sh.id)
    sh.updated = now_str()
    return sh


def close(song: Song, sheet_id: str) -> str:
    """Hide the tab; the sheet and its content stay in the song (Delete is the only thing that removes a draft). The last open tab cannot be closed.
    Returns the id of the sheet that is active afterwards."""
    book = ensure_book(song)
    if sheet_id not in book.open_ids:
        return book.active_id
    if len(book.open_ids) <= 1:
        raise SheetError("This is the only open sheet, so it cannot be closed. Open another sheet first, or create a new one.")
    flush(song)
    idx = book.open_ids.index(sheet_id)
    book.open_ids.remove(sheet_id)
    if book.active_id == sheet_id:
        switch(song, book.open_ids[min(idx, len(book.open_ids) - 1)])
    return book.active_id


def delete(song: Song, sheet_id: str) -> str:
    """Remove a draft for good (the UI asks first). The only sheet of a song cannot be deleted: a song always keeps a working draft."""
    book = ensure_book(song)
    if sheet_of(book, sheet_id) is None:
        raise SheetError("That sheet no longer exists.")
    if len(book.sheets) <= 1:
        raise SheetError("A song needs at least one sheet. Create another sheet before deleting this one.")
    flush(song)
    if book.active_id == sheet_id:
        nxt = next((i for i in book.open_ids if i != sheet_id), None) or next(s.id for s in book.sheets if s.id != sheet_id)
        switch(song, nxt)
    book.sheets = [s for s in book.sheets if s.id != sheet_id]
    book.open_ids = [i for i in book.open_ids if i != sheet_id]
    if book.primary_id == sheet_id:
        book.primary_id = ""
    return book.active_id


def apply_settings(song: Song, sheet_id: str, changes: dict) -> None:
    """Set SongSettings fields on ONE sheet, found by its id. The active sheet's working copy (song.settings) is updated; any other sheet's stored options are updated in place; no other
    sheet is touched, whichever sheet happens to be open. Unknown field names are refused (nothing is applied)."""
    book = ensure_book(song)
    target = sheet_of(book, sheet_id)
    if target is None:
        raise SheetError("That sheet no longer exists. Nothing was applied.")
    known = {f.name for f in fields(SongSettings)}
    bad = [k for k in changes if k not in known]
    if bad:
        raise SheetError("Not a sheet option: " + ", ".join(bad))
    if target.id == book.active_id:
        for k, v in changes.items():
            setattr(song.settings, k, copy.deepcopy(v))
        flush(song)
    else:
        target.settings = {**target.settings, **copy.deepcopy(changes)}
        target.updated = now_str()


def hidden(song: Song) -> list:
    """Sheets that exist but have no open tab (they can be reopened from the menu)."""
    book = ensure_book(song)
    return [s for s in book.sheets if s.id not in book.open_ids]


def display_names(book: SheetBook) -> dict:
    return {s.id: s.name for s in book.sheets}


# ── (de)serialization of sheets.json ─────────────────────────────────────────────────────────────────────────
def book_to_dict(book: SheetBook) -> dict:
    d = dict(book.extra)
    d.update({"schema_version": 1, "rev": book.rev, "active_sheet_id": book.active_id, "open_sheet_ids": list(book.open_ids), "primary_sheet_id": book.primary_id,
              "sheets": [asdict(s) for s in book.sheets]})
    return d


def book_from_dict(d: dict) -> SheetBook:
    """Raises SheetError for a file that cannot be a sheet book (the caller then protects the file and opens the song from song.json)."""
    if not isinstance(d, dict) or not isinstance(d.get("sheets"), list) or not d["sheets"]:
        raise SheetError("sheets.json has no sheets")
    names = {f.name for f in fields(Sheet)}
    sheets, seen = [], set()
    for raw in d["sheets"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("lyrics", ""), str) or not isinstance(raw.get("settings", {}), dict):
            raise SheetError("a sheet in sheets.json is damaged")
        sh = Sheet(**{k: v for k, v in raw.items() if k in names})
        if not sh.id or sh.id in seen:
            raise SheetError("a sheet id is missing or repeated")
        seen.add(sh.id)
        sheets.append(sh)
    active = d.get("active_sheet_id") if d.get("active_sheet_id") in seen else sheets[0].id
    open_ids = [i for i in (d.get("open_sheet_ids") or []) if i in seen]
    if active not in open_ids:
        open_ids.append(active)
    known = {"schema_version", "rev", "active_sheet_id", "open_sheet_ids", "primary_sheet_id", "sheets"}
    return SheetBook(sheets=sheets, active_id=active, open_ids=open_ids, primary_id=d.get("primary_sheet_id") if d.get("primary_sheet_id") in seen else "",
                     rev=int(d.get("rev") or 0), extra={k: v for k, v in d.items() if k not in known})


def signature(song: Song) -> str:
    """A hash of everything the sheets hold (after a flush): used to tell whether an autosave differs from what is saved."""
    book = ensure_book(song)
    flush(song)
    return hashlib.sha1(json.dumps([[s.id, s.name, s.lyrics, s.settings] for s in book.sheets] + [book.active_id, book.open_ids], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
