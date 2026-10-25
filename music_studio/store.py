"""Music library on disk. One folder per song:

  <library>/songs/<song_id>/song.json       title, lyrics, settings        (written by Save; autosave goes to song.autosave.json)
                            takes.json      takes + failed/cancelled runs  (written by generation, never touches song.json)
                            chat.json       AI chat history of this song   (written after every message)
                            analysis.json   last Lyrics Analysis result + manual syllable corrections of this song
                            takes/<id>.mp3  the real audio files

All JSON is written atomically (temp file + replace). Nothing here ever overwrites a take file.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
from dataclasses import asdict
from pathlib import Path

from . import sheets as SH
from .model import (Song, Take, Run, ChatMessage, song_to_dict, song_from_dict, take_from_dict, run_from_dict, chat_from_dict, new_id, now_str)

_lock = threading.RLock()


def library_dir() -> Path:
    d = Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents" / "WorkflowPad" / "Music"
    (d / "songs").mkdir(parents=True, exist_ok=True)
    return d


def exports_dir() -> Path:
    d = library_dir() / "Exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def song_dir(song_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_]+", song_id or ""):
        raise ValueError("bad song id")
    return library_dir() / "songs" / song_id


def takes_dir(song_id: str) -> Path:
    return song_dir(song_id) / "takes"


def safe_name(title: str) -> str:
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title or "").strip(" .") or "song"
    return n[:80]


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ── damaged files ────────────────────────────────────────────────────────────────────────────────────────
# A file that EXISTS but cannot be read is not "empty": it is copied to <name>.corrupt-<time>.json (once per damaged state, never over an existing backup) and then PROTECTED:
# nothing is written over it, so the (possibly recoverable) original stays where it is. A file that does not exist is simply empty.
class DataError(Exception):
    def __init__(self, message: str, path: Path | None = None, backup: Path | None = None):
        super().__init__(message)
        self.path, self.backup = path, backup


_damage: dict = {}           # str(path) -> ((mtime_ns, size), backup path, message): one backup per damaged state


def _protect(path: Path, ex: Exception) -> DataError:
    key = str(path)
    try:
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = (0, 0)
    cur = _damage.get(key)
    if cur and cur[0] == sig:
        return DataError(cur[2], path, cur[1])
    stamp = __import__("time").strftime("%Y%m%d-%H%M%S")
    backup, i = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}"), 2
    while backup.exists():                                    # a backup is never overwritten
        backup = path.with_name(f"{path.stem}.corrupt-{stamp}-{i}{path.suffix}")
        i += 1
    try:
        shutil.copy2(path, backup)
        msg = f"{path.name} of this song is damaged ({ex}). A copy was saved as {backup.name}; the damaged file was not overwritten and no audio was touched."
    except OSError as cx:
        backup = None
        msg = f"{path.name} of this song is damaged ({ex}) and could not be copied ({cx}). It was left untouched."
    _damage[key] = (sig, backup, msg)
    return DataError(msg, path, backup)


def _read_checked(path: Path, kind: type = dict):
    """The JSON of an existing file, or DataError (after the backup) when it is unreadable or not a `kind`."""
    try:
        d = _read(path)
        if not isinstance(d, kind):
            raise ValueError(f"expected a JSON {'object' if kind is dict else 'list'}")
        _damage.pop(str(path), None)
        return d
    except Exception as ex:
        raise _protect(path, ex)


def discard_damaged(path: Path) -> None:
    """The user explicitly chose to start over (e.g. Clear chat): the damaged original is moved out of the way (its backup copy is kept), so a new file can be written."""
    try:
        _read_checked(path, list if path.name == "chat.json" else dict)
        return                                                # it is fine after all: nothing to discard
    except DataError as ex:
        if ex.backup is not None:
            path.unlink(missing_ok=True)
            _damage.pop(str(path), None)


def song_problems(song_id: str) -> list:
    """Messages about damaged data files of this song (empty list = all readable). Never raises."""
    out = []
    try:
        d = song_dir(song_id)
        for name, kind in (("takes.json", dict), ("chat.json", list), ("analysis.json", dict), ("sheets.json", dict)):
            p = d / name
            if p.exists():
                try:
                    _read_checked(p, kind)
                except DataError as ex:
                    out.append(str(ex))
    except Exception:
        pass
    return out


# ── songs ────────────────────────────────────────────────────────────────────────────────────────────────
def create_song(title: str = "Untitled Song", lyrics: str = "") -> Song:
    s = Song(title=title, lyrics=lyrics)
    save_song(s)
    return s


def _write_sheets(s: Song) -> None:
    """sheets.json (all drafts of the song), atomically. The editor's working copy is flushed into the active sheet first. Skipped while the file on disk is protected (damaged)."""
    book = SH.ensure_book(s)
    SH.flush(s)
    if book.protected:
        return
    book.rev += 1
    _atomic(song_dir(s.id) / "sheets.json", json.dumps(SH.book_to_dict(book), ensure_ascii=False, indent=1))


def save_song(s: Song) -> None:
    """sheets.json first, then song.json (which repeats the revision and mirrors the ACTIVE sheet, so older code that reads only song.json still sees the lyrics being worked on)."""
    with _lock:
        s.updated = now_str()
        _write_sheets(s)
        _atomic(song_dir(s.id) / "song.json", json.dumps(song_to_dict(s), ensure_ascii=False, indent=2))
        try:
            (song_dir(s.id) / "song.autosave.json").unlink(missing_ok=True)
        except OSError:
            pass


def _adopt_backup(path: Path) -> None:
    stamp = __import__("time").strftime("%Y%m%d-%H%M%S")
    dest, i = path.with_name(f"{path.stem}.replaced-{stamp}{path.suffix}"), 2
    while dest.exists():
        dest = path.with_name(f"{path.stem}.replaced-{stamp}-{i}{path.suffix}")
        i += 1
    shutil.copy2(path, dest)


def load_song(song_id: str) -> Song:
    """song.json + its sheets. A song from before sheets existed becomes a book with one sheet 'Original' (song.json is copied to song.premigration.json first and is not changed).
    A damaged sheets.json is never overwritten: the song opens from song.json and saving skips sheets.json until it is repaired."""
    with _lock:
        d = song_dir(song_id)
        raw = _read(d / "song.json")
        song = song_from_dict(raw)
        sp = d / "sheets.json"
        if not sp.exists():
            pre = d / "song.premigration.json"
            if not pre.exists():
                shutil.copy2(d / "song.json", pre)
            song.book = SH.new_book(song)
            song.book.rev = 1
            _atomic(sp, json.dumps(SH.book_to_dict(song.book), ensure_ascii=False, indent=1))
            return song
        try:
            book = SH.book_from_dict(_read_checked(sp, dict))
        except (DataError, SH.SheetError) as ex:
            if not isinstance(ex, DataError):
                ex = _protect(sp, ex)
            song.book = SH.new_book(song)
            song.book.protected = str(ex)
            return song
        song.book = book
        act = SH.active_sheet(song)
        same = act.lyrics == song.lyrics and act.settings == SH.settings_to_dict(song.settings)
        rev_in_song = raw.get("sheets_rev")
        if not same and (rev_in_song is None or int(rev_in_song or 0) > book.rev):
            _adopt_backup(sp)                           # song.json was written by something that does not know sheets (an older version): its lyrics win, sheets.json is kept aside
            act.lyrics, act.settings, act.updated = song.lyrics, SH.settings_to_dict(song.settings), now_str()
        SH.load_active(song)
        return song


def write_autosave(s: Song) -> None:
    with _lock:
        book = SH.ensure_book(s)
        SH.flush(s)
        data = song_to_dict(s)
        data["sheets"] = SH.book_to_dict(book)
        _atomic(song_dir(s.id) / "song.autosave.json", json.dumps(data, ensure_ascii=False))


def newer_autosave(song_id: str) -> Song | None:
    """The autosaved version when it is newer than song.json (a crash / close without saving), else None."""
    a, m = song_dir(song_id) / "song.autosave.json", song_dir(song_id) / "song.json"
    try:
        if a.exists() and (not m.exists() or a.stat().st_mtime > m.stat().st_mtime):
            raw = _read(a)
            song = song_from_dict(raw)
            if isinstance(raw.get("sheets"), dict):
                try:
                    song.book = SH.book_from_dict(raw["sheets"])
                    SH.load_active(song)
                except SH.SheetError:
                    song.book = None
            return song
    except Exception:
        pass
    return None


def discard_autosave(song_id: str) -> None:
    try:
        (song_dir(song_id) / "song.autosave.json").unlink(missing_ok=True)
    except OSError:
        pass


def delete_song(song_id: str) -> None:
    d = song_dir(song_id)
    if d.exists() and d.parent == library_dir() / "songs":
        shutil.rmtree(d)


def duplicate_song(s: Song, new_title: str) -> Song:
    """Save As: lyrics + settings + chat copied under a new id; takes are not copied."""
    c = Song(title=new_title, lyrics=s.lyrics, settings=song_from_dict(song_to_dict(s)).settings)
    if s.book is not None:                                    # every sheet comes along (new song id, same sheet ids); a sheet's link to a take of the old song is cut
        SH.flush(s)
        c.book = SH.book_from_dict(SH.book_to_dict(s.book))
        c.book.rev, c.book.protected = 0, ""
        for sh in c.book.sheets:
            sh.source_take_id = None
    save_song(c)
    save_chat(c.id, load_chat(s.id))
    return c


# ── takes + runs ─────────────────────────────────────────────────────────────────────────────────────────
def _load_takes_checked(song_id: str) -> tuple:
    """(takes, runs, extra): extra = raw entries this version could not read, carried along unchanged by every save. A missing file = empty; an unreadable file = DataError."""
    p = song_dir(song_id) / "takes.json"
    if not p.exists():
        return [], [], {"takes": [], "runs": []}
    d = _read_checked(p, dict)
    if not isinstance(d.get("takes", []), list) or not isinstance(d.get("runs", []), list):
        raise _protect(p, ValueError("“takes” / “runs” are not lists"))
    out, extra = {}, {"takes": [], "runs": []}
    for key, conv in (("takes", take_from_dict), ("runs", run_from_dict)):
        out[key] = []
        for raw in d.get(key, []):
            try:
                out[key].append(conv(raw))
            except Exception:
                extra[key].append(raw)
    return out["takes"], out["runs"], extra


def load_takes(song_id: str) -> tuple[list[Take], list[Run]]:
    """For DISPLAY: an unreadable takes.json shows no takes here, but the damage is recorded (song_problems) and every write refuses to touch the file."""
    try:
        t, r, _ = _load_takes_checked(song_id)
        return t, r
    except DataError:
        return [], []


def _save_takes(song_id: str, takes: list[Take], runs: list[Run], extra: dict | None = None) -> None:
    ex = extra or {}
    data = {"takes": [asdict(t) for t in takes] + list(ex.get("takes", [])), "runs": [asdict(r) for r in runs] + list(ex.get("runs", []))}
    _atomic(song_dir(song_id) / "takes.json", json.dumps(data, ensure_ascii=False, indent=1))


def new_take_file(song_id: str, take_id: str, ext: str) -> Path:
    d = takes_dir(song_id)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{take_id}.{ext.lstrip('.') or 'mp3'}"
    i = 2
    while f.exists():
        f = d / f"{take_id}_{i}.{ext.lstrip('.') or 'mp3'}"
        i += 1
    return f


def add_take(t: Take) -> Take:
    """Raises DataError when takes.json is damaged (nothing is written; the audio file stays where it is)."""
    with _lock:
        takes, runs, extra = _load_takes_checked(t.song_id)
        t.number = max([x.number for x in takes] + [0]) + 1
        takes.append(t)
        _save_takes(t.song_id, takes, runs, extra)
    return t


def add_run(r: Run) -> None:
    with _lock:
        takes, runs, extra = _load_takes_checked(r.song_id)
        runs.append(r)
        _save_takes(r.song_id, takes, runs, extra)


def update_take(song_id: str, take_id: str, **changes) -> None:
    with _lock:
        takes, runs, extra = _load_takes_checked(song_id)
        for t in takes:
            if t.id == take_id:
                for k, v in changes.items():
                    setattr(t, k, v)
        _save_takes(song_id, takes, runs, extra)


def delete_take(song_id: str, take_id: str) -> None:
    with _lock:
        takes, runs, extra = _load_takes_checked(song_id)          # a damaged takes.json is never rewritten, and then no audio is deleted either
        keep, gone = [], []
        for t in takes:
            (gone if t.id == take_id else keep).append(t)
        _save_takes(song_id, keep, runs, extra)                    # metadata first: if this fails the audio is still there
        for t in gone:
            try:
                (takes_dir(song_id) / t.file).unlink(missing_ok=True)
            except OSError:
                pass


def take_path(t: Take) -> Path | None:
    p = takes_dir(t.song_id) / t.file
    return p if p.exists() else None


# ── chat ─────────────────────────────────────────────────────────────────────────────────────────────────
def _load_chat_checked(song_id: str) -> list[ChatMessage]:
    p = song_dir(song_id) / "chat.json"
    if not p.exists():
        return []
    d = _read_checked(p, list)
    try:
        return [chat_from_dict(m) for m in d]
    except Exception as ex:
        raise _protect(p, ex)


def load_chat(song_id: str) -> list[ChatMessage]:
    """For DISPLAY: a damaged chat.json shows an empty chat (see song_problems) and save_chat then refuses to overwrite it."""
    try:
        return _load_chat_checked(song_id)
    except DataError:
        return []


def save_chat(song_id: str, msgs: list[ChatMessage]) -> bool:
    """False (nothing written) when the existing chat.json is damaged; the chat of this session stays in memory only. Missing file = fine."""
    with _lock:
        p = song_dir(song_id) / "chat.json"
        if p.exists():
            try:
                _load_chat_checked(song_id)
            except DataError:
                return False
        _atomic(p, json.dumps([asdict(m) for m in msgs[-200:]], ensure_ascii=False, indent=1))
        return True


def clear_chat_file(song_id: str) -> bool:
    """Clear chat: the user chose to start over, so a damaged chat.json is first moved aside (its backup copy is kept)."""
    with _lock:
        discard_damaged(song_dir(song_id) / "chat.json")
        return save_chat(song_id, [])


# ── lyrics analysis (the last good AI result + the user's manual corrections; never touches song.json) ───────
def _analysis_path(song_id: str, sheet_id: str = "") -> Path:
    """The primary sheet (the song as it was before sheets) keeps analysis.json; every other sheet has its own analysis.<sheet id>.json, so results and manual corrections
    of one sheet can never be read for another."""
    return song_dir(song_id) / ("analysis.json" if not sheet_id else f"analysis.{re.sub(r'[^A-Za-z0-9_-]', '_', sheet_id)}.json")


def load_analysis_meta(song_id: str, sheet_id: str = "") -> dict:
    p = _analysis_path(song_id, sheet_id)
    if not p.exists():
        return {}
    try:
        return _read_checked(p, dict)
    except DataError:
        return {}                                             # display only: save_analysis_meta refuses to overwrite a damaged file


def save_analysis_meta(song_id: str, meta: dict, sheet_id: str = "") -> None:
    """Raises DataError when analysis.json is damaged. Keys of the file that `meta` does not mention (syllables / corrections / rhyme / rhythm / thesaurus / unknown ones) are kept,
    so saving one kind of result can never delete another."""
    with _lock:
        p = _analysis_path(song_id, sheet_id)
        merged = dict(meta)
        if p.exists():
            old = _read_checked(p, dict)
            for k, v in old.items():
                merged.setdefault(k, v)
        _atomic(p, json.dumps(merged, ensure_ascii=False, indent=1))


# ── library index (reads every song folder; cheap enough for personal libraries) ──────────────────────────
def list_songs() -> list[dict]:
    """[{song, takes, runs, latest, favorite}] newest-updated first. Broken folders are skipped, not deleted."""
    out = []
    for d in (library_dir() / "songs").iterdir():
        if not (d / "song.json").exists():
            continue
        try:
            s = song_from_dict(_read(d / "song.json"))
        except Exception:
            continue
        takes, runs = load_takes(s.id)
        ts = [t for t in takes if take_path(t)]
        out.append({"song": s, "takes": ts, "runs": runs, "latest": ts[-1] if ts else None, "favorite": any(t.favorite for t in ts), "warnings": song_problems(s.id)})
    out.sort(key=lambda x: x["song"].updated, reverse=True)
    return out


def export_take(t: Take, title: str, dest_dir: Path | None = None) -> Path:
    """Copy the take's audio to the exports folder under a readable name; an existing file is never overwritten."""
    src = take_path(t)
    if not src:
        raise FileNotFoundError("The audio file of this take is missing")
    folder = dest_dir or exports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{safe_name(title)} - Take {t.number:02d}"
    dest = folder / (base + src.suffix)
    i = 2
    while dest.exists():
        dest = folder / f"{base} ({i}){src.suffix}"
        i += 1
    shutil.copy2(src, dest)
    return dest
