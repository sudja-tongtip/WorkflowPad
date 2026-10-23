"""export_util.py - copy / save files without freezing the window (shared by the WorkflowPad programs).

Copying an image inside an async button handler with shutil.copy2() blocks Flet's event loop for as long as the copy takes: the whole window stops responding (on a slow disk, a busy antivirus or a cloud-synced folder
that can be a long time). Here every copy runs in a worker thread (`asyncio.to_thread`), progress is reported between files, and the time the copy really took is returned, so a slow export can be told apart from a
slow folder dialog.
"""

from __future__ import annotations

import asyncio
import errno
import os
import shutil
import time
import uuid
from pathlib import Path


def _tmp_name(directory: Path, final_name: str) -> Path:
    """A temporary name in the SAME folder as the final file (so the last step never crosses a drive). It starts with a dot and ends in .part."""
    return directory / f".{final_name}.{uuid.uuid4().hex[:8]}.part"


def _remove_quietly(path) -> None:
    try:
        os.unlink(str(path))
    except OSError:
        pass


def _finalize_exclusive(tmp: Path, dst: Path) -> None:
    """Put the finished temporary file at `dst` WITHOUT ever replacing an existing file: raises FileExistsError when `dst` exists (even if it was created a moment ago by someone else).
    A hard link is atomic and refuses an existing name; where hard links do not exist (exFAT, some network drives) Windows' rename refuses to replace too."""
    try:
        os.link(str(tmp), str(dst))
    except FileExistsError:
        raise
    except (OSError, AttributeError, NotImplementedError):
        if os.name == "nt":
            os.rename(str(tmp), str(dst))                       # Windows: FileExistsError when dst exists, never a silent replace
            return
        with open(str(tmp), "rb") as s_f, open(str(dst), "xb") as d_f:      # exclusive create: raises FileExistsError, never replaces
            try:
                shutil.copyfileobj(s_f, d_f)
            except BaseException:
                d_f.close()
                _remove_quietly(dst)                            # we created dst a moment ago: only then is it ours to remove
                raise
        _remove_quietly(tmp)
        return
    _remove_quietly(tmp)                                         # the data is now under the final name; the temporary name is not needed


def _copy_atomic(src, dst, overwrite: bool) -> Path:
    """Copy src to dst through a temporary file. The final name only ever holds a COMPLETE copy. overwrite=False: an existing file is never replaced (the next free name, name_1, name_2 ... is used);
    overwrite=True: the existing file is replaced in one step. On any failure the temporary file is removed and nothing else is touched. Returns the path that was written."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_name(dst.parent, dst.name)
    try:
        shutil.copy2(str(src), str(tmp))
        if overwrite:
            os.replace(str(tmp), str(dst))
            return dst
        stem, suffix = dst.stem, dst.suffix
        n = 0
        while True:
            cand = dst if n == 0 else dst.with_name(f"{stem}_{n}{suffix}")
            try:
                _finalize_exclusive(tmp, cand)
                return cand
            except FileExistsError:
                n += 1
    except BaseException:
        _remove_quietly(tmp)
        raise


async def copy_files(pairs, on_progress=None, overwrite: bool = False) -> dict:
    """Copy [(src, dst), ...] one file at a time, each in a worker thread, each through a temporary file (a copy that fails leaves nothing under the final name). on_progress(done, total) is called after
    every file. By default an existing destination is NEVER replaced (a file another program created after the name was chosen, or an earlier export): the copy goes to name_1, name_2 ...
    overwrite=True replaces it (for an export whose names the person expects to be refreshed).
    Returns {"copied", "failed", "seconds", "errors", "paths"}; `paths` are the files really written (their number is `copied`). A full disk (ENOSPC) is raised at once so the friendly error dialog can explain it;
    any other OSError counts the file as failed and goes on."""
    pairs = list(pairs)
    t0 = time.perf_counter()
    copied = failed = 0
    errors: list[str] = []
    paths: list[str] = []
    for i, (src, dst) in enumerate(pairs, 1):
        try:
            written = await asyncio.to_thread(_copy_atomic, src, dst, overwrite)
            paths.append(str(written))
            copied += 1
        except OSError as ex:
            if ex.errno == errno.ENOSPC:
                raise
            failed += 1
            errors.append(f"{Path(str(src)).name}: {ex}")
        if on_progress:
            try:
                on_progress(i, len(pairs))
            except Exception:
                pass
    return {"copied": copied, "failed": failed, "seconds": time.perf_counter() - t0, "errors": errors, "paths": paths}


async def copy_one(src, dst) -> float:
    """'Save As': the person chose this file name in the dialog (and Windows asked about replacing it), so an existing file IS replaced - in one step, through a temporary file, so a failed copy leaves the old
    file as it was. Returns the seconds it took (OSError is raised to the caller)."""
    t0 = time.perf_counter()
    await asyncio.to_thread(_copy_atomic, src, dst, True)
    return time.perf_counter() - t0


async def copy_unique(src, dst) -> Path:
    """Copy to dst, or to the next free name when it is taken, never replacing a file. Returns the path written."""
    return await asyncio.to_thread(_copy_atomic, src, dst, False)


def _write_bytes_replace(path, data: bytes) -> None:
    """The data goes to a temporary file next to `path` and replaces it in one step: a failure (full disk, access denied) leaves the file that was there as it was and removes the temporary file."""
    path = Path(path)
    tmp = _tmp_name(path.parent, path.name)
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        _remove_quietly(tmp)
        raise


async def write_bytes(path, data: bytes) -> float:
    """'Save As' for bytes: the person chose this file name (and was asked about replacing it), so an existing file IS replaced - in one step, through a temporary file. Returns the seconds it took."""
    t0 = time.perf_counter()
    await asyncio.to_thread(_write_bytes_replace, path, data)
    return time.perf_counter() - t0


async def write_bytes_unique(path, data: bytes) -> Path:
    """Write bytes to `path`, or to the next free name (name_1, name_2 ...) when it is taken, never replacing a file. Returns the path written."""
    path = Path(path)
    return await asyncio.to_thread(write_unique, path.parent, path.name, data)


# ── names that never collide ─────────────────────────────────────────────────────────────────────────────────
def unique_dest(directory, name: str, reserved: set | None = None) -> Path:
    """A destination path in `directory` that exists neither on the disk NOR in `reserved` (the destinations already handed out for the files of the same batch). The chosen path is added to `reserved`.
    Checking the disk alone is not enough when the destinations of a whole batch are prepared before the first file is copied: two sources with the same name would both get the same free name."""
    directory = Path(directory)
    stem, suffix = Path(name).stem, Path(name).suffix
    if reserved is None:
        reserved = set()
    n = 0
    while True:
        dst = directory / (name if n == 0 else f"{stem}_{n}{suffix}")
        key = os.path.normcase(str(dst))
        if key not in reserved and not dst.exists():
            reserved.add(key)
            return dst
        n += 1


def write_unique(directory, name: str, data: bytes) -> Path:
    """Write `data` to directory/name, or to name_1, name_2 ... when that name is taken, and return the path. The data goes to a temporary file first and is put under its final name in one step that refuses to
    replace an existing file: an existing file is never overwritten (not even by a job running at the same time, nor by a file another program creates at the last moment), and the final name never holds a
    half-written file. When anything fails (full disk, access denied) the temporary file - which this call created - is removed and the error is raised; nothing else is touched."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_name(directory, name)
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        stem, suffix = Path(name).stem, Path(name).suffix
        n = 0
        while True:
            dst = directory / (name if n == 0 else f"{stem}_{n}{suffix}")
            try:
                _finalize_exclusive(tmp, dst)
                return dst
            except FileExistsError:
                n += 1
    except BaseException:
        _remove_quietly(tmp)
        raise


# ── what the bytes of an image really are ─────────────────────────────────────────────────────────────────
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
    (b"BM", ".bmp", "image/bmp"),
)


def sniff_image(data: bytes):
    """(extension, MIME type) from the first bytes of an image, or None when they are not a format the programs handle (PNG, JPEG, WebP, GIF, BMP). Nothing is decoded."""
    head = bytes(data[:16])
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp", "image/webp"
    for sig, ext, mime in _SIGNATURES:
        if head.startswith(sig):
            return ext, mime
    return None


def image_ext(data: bytes, fallback: str = ".jpg") -> str:
    """The extension that matches the bytes (so a PNG is never saved as .jpg); `fallback` when the format is not recognised."""
    s = sniff_image(data)
    return s[0] if s else fallback


def image_mime(data: bytes, ext_fallback: str = "") -> str:
    """The MIME type that matches the bytes; when they are not recognised, the one of the file extension (jpeg when unknown)."""
    s = sniff_image(data)
    if s:
        return s[1]
    return {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}.get((ext_fallback or "").lower(), "image/jpeg")


# ── JSON files that are never left half-written ───────────────────────────────────────────────────────────────
def write_json_atomic(path, obj, *, keep_backup: bool = False, **dump_kwargs) -> None:
    """Write `obj` as JSON to `path` so that the file is always either the old content or the new one, never a cut-off mix: the text goes to a temporary file in the same folder, is flushed to the disk,
    and `os.replace` swaps it in. When anything fails (disk full, serialisation error, access denied) the temporary file is removed, the OLD file is untouched and the error is raised.
    keep_backup: before the swap, a copy of the current file is kept as '<name>.bak' - but only when that current file is itself valid JSON, so a good backup is never replaced by a damaged one."""
    import json
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, **dump_kwargs)                       # serialise first: a failure here has touched nothing
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if keep_backup and path.exists():
            try:
                json.loads(path.read_text(encoding="utf-8"))
                shutil.copy2(str(path), str(path) + ".bak")
            except (ValueError, OSError):
                pass                                            # the current file is damaged (or unreadable): keep the earlier backup as it is
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json_safe(path):
    """(data, problem) for a JSON file. problem is '' when the file is fine, 'missing' when it does not exist, or the reason when it cannot be read / parsed. A damaged file is never reported as 'empty'."""
    import json
    path = Path(path)
    if not path.exists():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except (ValueError, OSError) as ex:
        return None, f"{type(ex).__name__}: {ex}"


# ── show files in Explorer ──────────────────────────────────────────────────────────────────────────────────
def _explorer_open(target: Path, select: bool) -> None:
    """Windows Explorer: the folder of a file with the file selected, or just a folder. Never raises."""
    import subprocess
    try:
        if select:
            subprocess.Popen(f'explorer /select,"{target}"')                 # one command-line string: Explorer parses the quotes itself (commas and spaces in the name are fine)
        else:
            os.startfile(str(target))                                         # type: ignore[attr-defined]  (Windows)
    except Exception:
        pass


def reveal_in_explorer(paths, max_folders: int = 5, opener=None) -> dict:
    """Open Explorer for these files. One existing file: its folder with the file selected. Several: every distinct folder once (at most `max_folders`, so a big selection does not open a wall of windows).
    A file that no longer exists falls back to its folder when that still exists. Returns {"opened", "skipped" (folders not opened because of the limit), "missing" (nothing to open)}.
    `opener(target, select)` can be replaced (tests); by default Explorer is started."""
    opener = opener or _explorer_open
    paths = [Path(str(p)) for p in paths if p]
    result = {"opened": 0, "skipped": 0, "missing": 0}
    if len(paths) == 1 and paths[0].exists():
        opener(paths[0], True)
        result["opened"] = 1
        return result
    folders: list[Path] = []
    for p in paths:
        folder = p.parent
        if not folder.exists():
            result["missing"] += 1
            continue
        key = os.path.normcase(str(folder))
        if all(os.path.normcase(str(f)) != key for f in folders):
            folders.append(folder)
    for folder in folders[:max_folders]:
        opener(folder, False)
        result["opened"] += 1
    result["skipped"] = max(0, len(folders) - max_folders)
    return result
