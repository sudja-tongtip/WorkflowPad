"""Manual Audio Timeline (Workspace -> Timeline): markers on the time line of ONE audio take.

Pure logic, no UI and no player: time parsing / formatting, validation, the section references of a take's own lyrics snapshot, marker storage.
A marker belongs to a (song, take) pair by the take's stable id, never by its place in a list. Times are integer milliseconds.
Nothing here measures or invents a playback position: the position always comes from the real player, and a marker is only ever created from a time the caller passes in.

Storage: songs/<song_id>/timeline.json  {"schema_version": 1, "takes": {take_id: [marker, ...]}} (written atomically; a damaged file is copied to *.corrupt-<time>.json first;
an entry that cannot be read is skipped with a message and kept in the file). Audio files and the take metadata are never touched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .compiler import TAG_LINE, tag_class_of

SCHEMA_VERSION = 1
LABEL_MAX = 60
_TIME = re.compile(r"^(?:(\d{1,2}):)?(\d{1,3}):([0-5]\d)(?:\.(\d{1,3}))?$")


class TimelineError(ValueError):
    """A sentence for the user."""


# ── time ─────────────────────────────────────────────────────────────────────────────────────────────────────
def parse_time(s: str) -> int:
    """'01:24' -> 84000, '01:24.500' -> 84500, '00:05.250' -> 5250, '1:01:24' -> 3684000. Minutes may exceed 59 only without hours; seconds are 00-59; the fraction has up to
    3 digits ('.5' means 500 ms). Anything else is a TimelineError (no negative values, no guessing)."""
    t = (s or "").strip()
    m = _TIME.match(t)
    if not m:
        raise TimelineError("Use the time format mm:ss or mm:ss.mmm, for example 01:24 or 01:24.500.")
    h, mi, sec, frac = m.groups()
    if h is not None and int(mi) > 59:
        raise TimelineError("With hours, minutes must be 00-59.")
    ms = int(((int(h) if h else 0) * 60 + int(mi)) * 60 + int(sec)) * 1000
    if frac:
        ms += int(frac.ljust(3, "0"))
    return ms


def format_time(ms: int, millis: bool = False) -> str:
    ms = max(0, int(ms))
    s, f = divmod(ms, 1000)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    base = f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
    return base + (f".{f:03d}" if millis else "")


def format_auto(ms: int) -> str:
    """Whole seconds as mm:ss, with .mmm only when the time has milliseconds."""
    return format_time(ms, millis=(int(ms) % 1000 != 0))


def validate_time(ms: int, duration_ms: "int | None") -> int:
    """The time itself, or TimelineError. A duration that is None/0 is UNKNOWN: then only 'not negative' can be checked (and the caller says so)."""
    if not isinstance(ms, int) or isinstance(ms, bool) or ms < 0:
        raise TimelineError("The time cannot be negative.")
    if duration_ms and ms > duration_ms:
        raise TimelineError(f"The time {format_auto(ms)} is after the end of this take ({format_auto(duration_ms)}).")
    return ms


def fraction(ms: int, duration_ms: int) -> float:
    return 0.0 if not duration_ms else max(0.0, min(1.0, ms / duration_ms))


CLUSTER_FRAC = 0.015        # markers closer than this share one tick (and one label) on the bar; the list below the bar always shows each one
MIN_WEIGHT_FRAC = 0.003     # no segment is drawn narrower than this share of the bar (never a zero-width widget)


def bar_layout(markers: list, duration_ms: int, cursor: "int | None" = None) -> dict:
    """Pure layout of the timeline bar: {"segments": [{start, end, weight, markers, played, cursor_edge}], "end_markers": [...]}.
    Segments cover 0..duration in order; a marker belongs to the segment that STARTS at its (clustered) time; markers at the very end (or after it) go to "end_markers", which the
    bar draws as an End tick. Several markers at the same time (or very close) sit in one segment, in list order. Every weight is > 0."""
    if not duration_ms or duration_ms <= 0:
        return {"segments": [], "end_markers": []}
    gap = max(1, int(duration_ms * CLUSTER_FRAC))
    floor = max(1, int(duration_ms * MIN_WEIGHT_FRAC))
    end_markers = [m for m in markers if m.time_ms >= duration_ms]
    inner = sorted((m for m in markers if m.time_ms < duration_ms), key=lambda m: m.time_ms)
    at: dict = {}
    start = None
    for m in inner:
        if start is None or m.time_ms - start >= gap:
            start = m.time_ms
        at.setdefault(start, []).append(m)
    pts = {0, duration_ms, *at.keys()}
    c = None if cursor is None else max(0, min(int(cursor), duration_ms))
    if c is not None:
        pts.add(c)
    pts = sorted(pts)
    segs = []
    for a, b in zip(pts, pts[1:]):
        segs.append({"start": a, "end": b, "weight": max(b - a, floor), "markers": at.get(a, []), "played": c is not None and b <= c, "cursor_edge": c is not None and b == c and b < duration_ms})
    return {"segments": segs, "end_markers": end_markers}


# ── section references of a take's OWN lyrics snapshot ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class SectionRef:
    ref: str            # "chorus:2": the normalized name + which occurrence of that name (exact, never the name alone)
    name: str           # as written in the lyrics, without the " - style" part
    occurrence: int     # 1-based
    total: int          # how many times the name occurs in this take's lyrics

    @property
    def label(self) -> str:
        return f"{self.name} ({self.occurrence})" if self.total > 1 else self.name


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def section_refs(take_lyrics: str) -> list:
    """The sections of a take's lyrics snapshot, in order: every whole-line [tag] that is a section (not a vocal cue or [Hold]). A name that repeats gets occurrence 1, 2, ...
    Vocal cue tags such as [whispered] are not sections. Empty / no snapshot -> []."""
    found = []
    for raw in (take_lyrics or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not TAG_LINE.match(raw) or tag_class_of(raw) in ("cue", "hold"):
            continue
        name = raw.strip()[1:-1].split(" - ", 1)[0].strip()
        if name:
            found.append(name)
    totals = {}
    for n in found:
        totals[_norm_name(n)] = totals.get(_norm_name(n), 0) + 1
    seen, out = {}, []
    for n in found:
        k = _norm_name(n)
        seen[k] = seen.get(k, 0) + 1
        out.append(SectionRef(f"{k}:{seen[k]}", n, seen[k], totals[k]))
    return out


def find_ref(refs: list, ref: "str | None") -> "SectionRef | None":
    return next((r for r in refs if r.ref == ref), None) if ref else None


# ── seek safety ──────────────────────────────────────────────────────────────────────────────────────────────
def seek_allowed(selected_song: str, selected_take: str, playing_song: str, playing_take: str, loaded: bool) -> "str | None":
    """None when a click on a marker may seek; otherwise the sentence that explains why not. Identity is song id + take id, never a list position."""
    if not (playing_song and playing_take):
        return "Nothing is playing. Press “Play Selected Take” first, then click the marker."
    if (selected_song, selected_take) != (playing_song, playing_take):
        return "The player is playing a different take. Press “Play Selected Take” first; nothing was changed."
    if not loaded:
        return "The audio of this take is not loaded in the player. Press “Play Selected Take” first."
    return None


# ── markers ──────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Marker:
    id: str
    time_ms: int
    label: str
    section_ref: "str | None" = None

    def to_dict(self) -> dict:
        return {"id": self.id, "time_ms": self.time_ms, "label": self.label, "section_ref": self.section_ref}


def marker_from_dict(d) -> Marker:
    i, t, lab, ref = d.get("id"), d.get("time_ms"), d.get("label"), d.get("section_ref")
    if not isinstance(i, str) or not re.fullmatch(r"[A-Za-z0-9_\-]{3,40}", i):
        raise TimelineError("missing or invalid id")
    if isinstance(t, bool) or not isinstance(t, int) or t < 0:
        raise TimelineError("invalid time")
    if not isinstance(lab, str) or not lab.strip():
        raise TimelineError("missing label")
    if ref is not None and not (isinstance(ref, str) and re.fullmatch(r".+:\d+", ref)):
        raise TimelineError("invalid section reference")
    return Marker(i, t, lab.strip()[:LABEL_MAX], ref)


def check_label(label: str) -> str:
    n = re.sub(r"\s+", " ", (label or "").strip())
    if not n:
        raise TimelineError("Give the marker a name.")
    if len(n) > LABEL_MAX:
        raise TimelineError(f"The name is too long (up to {LABEL_MAX} characters).")
    return n


def sort_markers(ms: list) -> list:
    return sorted(ms, key=lambda m: m.time_ms)          # stable: markers at the same time keep the order they were added in


class TimelineData:
    """All markers of ONE song, per take id. `rev(take_id)` changes on every change of that take's list: a dialog compares it before it saves."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._takes: dict = {}
        self._skipped: dict = {}
        self._rev: dict = {}
        self.warnings: list = []
        self._readonly = False

    # -- load / save ------------------------------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "TimelineData":
        d = cls(path)
        if not d.path.exists():
            return d
        try:
            data = json.loads(d.path.read_text(encoding="utf-8"))
            takes = data["takes"]
            if not isinstance(takes, dict):
                raise ValueError("“takes” is not an object")
        except Exception as ex:
            backup = d.path.with_name(d.path.stem + time.strftime(".corrupt-%Y%m%d-%H%M%S") + d.path.suffix)
            try:
                shutil.copy2(d.path, backup)
                d.warnings.append(f"The timeline file could not be read ({ex}). It was copied to {backup.name}; markers start empty and that copy is never overwritten.")
            except Exception:
                d.warnings.append(f"The timeline file could not be read ({ex}) and could not be backed up, so nothing will be saved until it is fixed.")
                d._readonly = True
            return d
        for tid, lst in takes.items():
            if not isinstance(lst, list):
                d.warnings.append(f"Take {tid}: its marker list was not a list and was ignored.")
                d._skipped[tid] = lst
                continue
            ok, seen = [], set()
            for i, raw in enumerate(lst, 1):
                try:
                    m = marker_from_dict(raw)
                    if m.id in seen:
                        raise TimelineError("duplicate id")
                    seen.add(m.id)
                    ok.append(m)
                except Exception as ex:
                    d.warnings.append(f"Take {tid}: marker {i} was skipped ({ex}).")
                    d._skipped.setdefault(tid, []).append(raw)
            d._takes[tid] = ok
        return d

    def save(self) -> None:
        if self._readonly:
            raise TimelineError("The timeline file is damaged and could not be backed up, so nothing was saved.")
        out = {}
        for tid in set(self._takes) | set(self._skipped):
            cur = [m.to_dict() for m in sort_markers(self._takes.get(tid, []))]
            extra = self._skipped.get(tid, [])
            if isinstance(extra, list):
                cur += extra                                    # an entry this version could not read is carried along unchanged
            if cur:
                out[tid] = cur
            elif not isinstance(extra, list):
                out[tid] = extra
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "takes": out}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)                              # atomic

    # -- queries / edits ----------------------------------------------------------------------------------------
    def rev(self, take_id: str) -> int:
        return self._rev.get(take_id, 0)

    def markers(self, take_id: str) -> list:
        return sort_markers(list(self._takes.get(take_id, [])))

    def get(self, take_id: str, marker_id: str) -> "Marker | None":
        return next((m for m in self._takes.get(take_id, []) if m.id == marker_id), None)

    def _bump(self, take_id: str) -> None:
        self._rev[take_id] = self.rev(take_id) + 1

    def add(self, take_id: str, time_ms: int, label: str, section_ref: "str | None", duration_ms: "int | None", expect_rev: "int | None" = None) -> Marker:
        self._check_rev(take_id, expect_rev)
        m = Marker("m_" + uuid.uuid4().hex[:12], validate_time(time_ms, duration_ms), check_label(label), section_ref)
        self._takes.setdefault(take_id, []).append(m)
        self._bump(take_id)
        self._commit(take_id, lambda: self._takes[take_id].remove(m))
        return m

    def update(self, take_id: str, marker_id: str, time_ms: int, label: str, section_ref: "str | None", duration_ms: "int | None", expect_rev: "int | None" = None) -> Marker:
        self._check_rev(take_id, expect_rev)
        m = self.get(take_id, marker_id)
        if m is None:
            raise TimelineError("That marker no longer exists.")
        new = Marker(m.id, validate_time(time_ms, duration_ms), check_label(label), section_ref)       # the id never changes
        old = (m.time_ms, m.label, m.section_ref)
        m.time_ms, m.label, m.section_ref = new.time_ms, new.label, new.section_ref
        self._bump(take_id)
        self._commit(take_id, lambda: (setattr(m, "time_ms", old[0]), setattr(m, "label", old[1]), setattr(m, "section_ref", old[2])))
        return m

    def delete(self, take_id: str, marker_id: str, expect_rev: "int | None" = None) -> None:
        self._check_rev(take_id, expect_rev)
        lst = self._takes.get(take_id, [])
        m = self.get(take_id, marker_id)
        if m is None:
            raise TimelineError("That marker no longer exists.")
        idx = lst.index(m)
        lst.remove(m)                                           # only this marker of this take
        self._bump(take_id)
        self._commit(take_id, lambda: lst.insert(idx, m))

    def _check_rev(self, take_id: str, expect: "int | None") -> None:
        if expect is not None and expect != self.rev(take_id):
            raise TimelineError("The markers of this take changed while the dialog was open. Nothing was saved: open it again.")

    def _commit(self, take_id: str, undo) -> None:
        try:
            self.save()
        except Exception:
            undo()                                              # nothing is shown as done when it could not be written
            self._bump(take_id)
            raise
