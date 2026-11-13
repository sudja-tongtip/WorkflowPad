"""Export a part of a take's audio file (the audio twin of the video export's "Part of the take").

The part is cut with ffmpeg by STREAM COPY (`-c copy`): nothing is re-encoded, so the exported part has exactly the quality of the take (flac / mp3 / opus) and is made in a moment. The cut falls on the
format's own frame boundary (about 20-30 ms for mp3 / opus). The whole take is exported by the plain copy of store.export_take (no ffmpeg needed). Nothing here changes the take or the lyrics.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import video_export as VX


class AudioExportError(RuntimeError):
    pass


def range_tag(start: float, end: float) -> str:
    """'0m25-4m00' for a file name: whole seconds, never a character Windows refuses."""
    def one(x: float) -> str:
        s = int(max(0.0, x))
        return f"{s // 60}m{s % 60:02d}"
    return f"{one(start)}-{one(end)}"


def unique_dest(folder: Path, base: str, suffix: str) -> Path:
    """`base + suffix` in `folder`; an existing file is never overwritten (a number is added)."""
    folder.mkdir(parents=True, exist_ok=True)
    dest, i = folder / (base + suffix), 2
    while dest.exists():
        dest = folder / f"{base} ({i}){suffix}"
        i += 1
    return dest


def build_command(ffmpeg: str, src, dest, start: float, end: float) -> list:
    """The ffmpeg command: audio only, copied without re-encoding. The times come AFTER `-i` (output seeking): with stream copy the cut is then exact to the packet; before `-i` an opus file started
    up to half a second early (it jumps to the previous page)."""
    return [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-vn", "-map_metadata", "0", "-c", "copy", str(dest)]


def trim_audio(src, dest, start: float, end: float, ffmpeg_path: str = "") -> Path:
    """Cut [start, end] seconds of `src` into `dest` (same container). Raises AudioExportError with ffmpeg's own words when it fails; a half-written file is removed."""
    if end - start < 0.2:
        raise AudioExportError("The part is too short.")
    ff = VX.find_ffmpeg(ffmpeg_path)
    if not ff:
        raise AudioExportError("ffmpeg was not found. Choose ffmpeg.exe in Export to Video (or put ffmpeg on the PATH).")
    dest = Path(dest)
    cmd = build_command(ff, src, dest, start, end)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=VX._no_window(), timeout=300)
    except (OSError, subprocess.SubprocessError) as ex:
        raise AudioExportError(f"ffmpeg could not be run: {ex}") from ex
    if r.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise AudioExportError("ffmpeg failed: " + ((r.stderr or "").strip().splitlines() or ["no message"])[-1][:200])
    return dest
