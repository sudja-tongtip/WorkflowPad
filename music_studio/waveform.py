"""Peaks for a real audio file, and the math that draws a time range on top of them.

No UI and no network. WAV is read with the standard library. Anything else is tried, in order, with
soundfile, ffmpeg, then Windows Media Foundation — whichever is already installed. Nothing is installed from here.
Peaks are cached by file identity (path, size, mtime). A selection or a zoom never decodes the file again.
The cache is not written into a song file.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

REPAINT_MAX_S = 600.0
CACHE_BUCKETS = 2048

_MEM: dict[tuple, "Waveform"] = {}
_LOCK = threading.Lock()


class WaveformError(ValueError):
    """A sentence for the user. The file was not decoded, so nothing is drawn in its place."""


@dataclass(frozen=True)
class Waveform:
    mins: tuple
    maxs: tuple
    duration: float
    sample_rate: int
    channels: int


def default_cache_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(root) / "WorkflowPad" / "waveform_cache"


def _identity(path: Path, buckets: int) -> tuple:
    st = path.stat()
    return (str(path.resolve()), int(st.st_size), int(st.st_mtime_ns), int(buckets))


def load_peaks(path, buckets: int = CACHE_BUCKETS, cache_dir: Path | None = None) -> Waveform:
    """Min/max peaks across the whole file. Raises WaveformError when the file cannot be read."""
    path = Path(path)
    if not path.is_file():
        raise WaveformError("The source audio file is missing.")
    buckets = max(8, int(buckets))
    key = _identity(path, buckets)
    with _LOCK:
        hit = _MEM.get(key)
    if hit is not None:
        return hit
    if cache_dir is not None:
        hit = _read_disk(Path(cache_dir), key)
        if hit is not None:
            with _LOCK:
                _MEM[key] = hit
            return hit
    wf = _decode(path, buckets)
    with _LOCK:
        _MEM[key] = wf
    if cache_dir is not None:
        _write_disk(Path(cache_dir), key, wf)
    return wf


def _decode(path: Path, buckets: int) -> Waveform:
    errors = []
    if path.suffix.lower() == ".wav":
        try:
            return _decode_wav(path, buckets)
        except Exception as ex:
            errors.append(f"WAV: {ex}")
    try:
        import soundfile as sf
    except Exception:
        sf = None
    if sf is not None:
        try:
            return _decode_soundfile(path, buckets, sf)
        except Exception as ex:
            errors.append(f"soundfile: {ex}")
    if shutil.which("ffmpeg"):
        try:
            return _decode_ffmpeg(path, buckets)
        except Exception as ex:
            errors.append(f"ffmpeg: {ex}")
    if sys.platform == "win32":
        try:
            return _decode_mf(path, buckets)
        except Exception as ex:
            errors.append(f"Media Foundation: {ex}")
    detail = "; ".join(errors) if errors else "no decoder is available"
    raise WaveformError(f"Could not read the audio ({detail}).")


def _bucket(samples, sample_rate: int, channels: int, buckets: int) -> Waveform:
    import numpy as np
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    n = int(x.shape[0])
    rate = int(sample_rate) or 1
    duration = n / float(rate) if n else 0.0
    if n == 0:
        raise WaveformError("The audio file has no samples.")
    buckets = max(1, min(int(buckets), n))
    edges = np.linspace(0, n, buckets + 1).astype(np.int64)
    mins, maxs = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            b = min(n, a + 1)
        sl = x[int(a):int(b)]
        mins.append(float(sl.min()) if sl.size else 0.0)
        maxs.append(float(sl.max()) if sl.size else 0.0)
    return Waveform(tuple(mins), tuple(maxs), duration, rate, int(channels) or 1)


def _pcm16_mono(raw: bytes, channels: int, sample_rate: int, buckets: int) -> Waveform:
    import numpy as np
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    ch = max(1, int(channels))
    usable = (len(x) // ch) * ch
    x = x[:usable]
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return _bucket(x, sample_rate, ch, buckets)


def _decode_wav(path: Path, buckets: int) -> Waveform:
    import numpy as np
    with wave.open(str(path), "rb") as w:
        ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8)
        usable = (len(b) // 3) * 3
        trip = b[:usable].reshape(-1, 3).astype(np.int32)
        v = trip[:, 0] | (trip[:, 1] << 8) | (trip[:, 2] << 16)
        v = np.where(v >= 0x800000, v - 0x1000000, v)
        x = v.astype(np.float32) / 8388608.0
    else:
        raise WaveformError(f"unsupported sample width {sw}")
    ch = max(1, int(ch))
    usable = (len(x) // ch) * ch
    x = x[:usable]
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return _bucket(x, rate, ch, buckets)


def _decode_soundfile(path: Path, buckets: int, sf) -> Waveform:
    data, rate = sf.read(str(path), always_2d=True, dtype="float32")
    ch = int(data.shape[1]) if data.ndim == 2 else 1
    mono = data.mean(axis=1) if ch > 1 else data.reshape(-1)
    return _bucket(mono, int(rate), ch, buckets)


def _decode_ffmpeg(path: Path, buckets: int) -> Waveform:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30)
    rate, ch = 44100, 2
    if probe.returncode == 0 and probe.stdout.strip():
        bits = probe.stdout.strip().split(",")
        if len(bits) >= 2:
            try:
                rate, ch = int(float(bits[0])), int(bits[1])
            except ValueError:
                pass
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(max(1, ch)), "-ar", str(rate), "pipe:1"],
        capture_output=True, timeout=120)
    if proc.returncode != 0 or not proc.stdout:
        raise WaveformError((proc.stderr or b"").decode(errors="ignore")[:200] or "ffmpeg produced no audio")
    return _pcm16_mono(proc.stdout, ch, rate, buckets)


def _decode_mf(path: Path, buckets: int) -> Waveform:
    raw, rate, ch = decode_with_mf(path)
    return _pcm16_mono(raw, ch, rate, buckets)


def decode_with_mf(path) -> tuple[bytes, int, int]:
    """PCM 16-bit little-endian bytes, sample rate and channel count, via Windows Media Foundation."""
    if sys.platform != "win32":
        raise WaveformError("Media Foundation is only available on Windows.")
    import ctypes
    from ctypes import POINTER, WINFUNCTYPE, byref, c_int64, c_ubyte, c_uint32, c_void_p, c_long

    HRESULT = c_long
    ole32, mfplat, mfreadwrite = ctypes.WinDLL("ole32"), ctypes.WinDLL("mfplat"), ctypes.WinDLL("mfreadwrite")

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16), ("Data4", c_ubyte * 8)]

    def G(d1, d2, d3, b):
        return GUID(d1, d2, d3, (c_ubyte * 8)(*b))

    MF_VERSION = (0x0002 << 16) | 0x0070
    MF_MT_MAJOR = G(0x48eba18e, 0xf8c9, 0x4687, [0xbf, 0x11, 0x0a, 0x74, 0xc9, 0xf9, 0x6a, 0x8f])
    MF_MT_SUB = G(0xf7e34c9a, 0x42e8, 0x4714, [0xb7, 0x4b, 0xcb, 0x29, 0xd7, 0x2c, 0x35, 0xe5])
    MF_CH = G(0x37e48bf5, 0x645e, 0x4c5b, [0x89, 0xde, 0xad, 0xa9, 0xe2, 0x9b, 0x69, 0x6a])
    MF_SR = G(0x5faeeae7, 0x0290, 0x4c31, [0x9e, 0x8a, 0xc5, 0x34, 0xf6, 0x8d, 0x9d, 0xba])
    MF_BPS = G(0xf2deb57f, 0x40fa, 0x4764, [0xaa, 0x33, 0xed, 0x4f, 0x2d, 0x1f, 0xf6, 0x69])
    MAJOR_AUDIO = G(0x73647561, 0x0000, 0x0010, [0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71])
    PCM = G(0x00000001, 0x0000, 0x0010, [0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71])
    FIRST_AUDIO = 0xFFFFFFFD
    EOS, ERROR, TICK = 0x2, 0x1, 0x100

    def vt(obj, index, restype, *argtypes):
        table = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
        return WINFUNCTYPE(restype, c_void_p, *argtypes)(table[index])

    def release(obj):
        if obj is not None and getattr(obj, "value", None):
            try:
                vt(obj, 2, c_uint32)(obj)
            except Exception:
                pass

    def fail(code, what):
        if code < 0:
            raise WaveformError(f"{what} failed ({code & 0xFFFFFFFF:08X})")

    def get_u32(obj, key):
        val = c_uint32()
        code = vt(obj, 7, HRESULT, POINTER(GUID), POINTER(c_uint32))(obj, byref(key), byref(val))
        return int(val.value) if code >= 0 else 0

    started = inited = False
    reader, media = c_void_p(), c_void_p()
    try:
        code = ole32.CoInitializeEx(None, 0)
        inited = code >= 0
        if code < 0 and (code & 0xFFFFFFFF) != 0x80010106:
            fail(code, "CoInitializeEx")
        mfplat.MFStartup.argtypes = [c_uint32, c_uint32]
        mfplat.MFStartup.restype = HRESULT
        fail(mfplat.MFStartup(MF_VERSION, 0), "MFStartup")
        started = True
        mfreadwrite.MFCreateSourceReaderFromURL.argtypes = [ctypes.c_wchar_p, c_void_p, POINTER(c_void_p)]
        mfreadwrite.MFCreateSourceReaderFromURL.restype = HRESULT
        fail(mfreadwrite.MFCreateSourceReaderFromURL(str(path), None, byref(reader)), "open audio")
        native = c_void_p()
        vt(reader, 5, HRESULT, c_uint32, c_uint32, POINTER(c_void_p))(reader, FIRST_AUDIO, 0, byref(native))
        rate = get_u32(native, MF_SR) if native.value else 0
        ch = get_u32(native, MF_CH) if native.value else 0
        release(native)
        if rate <= 0:
            rate = 44100
        if ch <= 0:
            ch = 2
        mfplat.MFCreateMediaType.argtypes = [POINTER(c_void_p)]
        mfplat.MFCreateMediaType.restype = HRESULT
        fail(mfplat.MFCreateMediaType(byref(media)), "media type")
        fail(vt(media, 24, HRESULT, POINTER(GUID), POINTER(GUID))(media, byref(MF_MT_MAJOR), byref(MAJOR_AUDIO)), "major type")
        fail(vt(media, 24, HRESULT, POINTER(GUID), POINTER(GUID))(media, byref(MF_MT_SUB), byref(PCM)), "subtype")
        fail(vt(media, 21, HRESULT, POINTER(GUID), c_uint32)(media, byref(MF_CH), int(ch)), "channels")
        fail(vt(media, 21, HRESULT, POINTER(GUID), c_uint32)(media, byref(MF_SR), int(rate)), "sample rate")
        fail(vt(media, 21, HRESULT, POINTER(GUID), c_uint32)(media, byref(MF_BPS), 16), "bit depth")
        fail(vt(reader, 7, HRESULT, c_uint32, POINTER(c_uint32), c_void_p)(reader, FIRST_AUDIO, None, media), "set PCM output")
        current = c_void_p()
        if vt(reader, 6, HRESULT, c_uint32, POINTER(c_void_p))(reader, FIRST_AUDIO, byref(current)) >= 0 and current.value:
            rate = get_u32(current, MF_SR) or rate
            ch = get_u32(current, MF_CH) or ch
            release(current)
        parts = []
        total = 0
        for _ in range(200000):
            flags, sample, ts, actual = c_uint32(), c_void_p(), c_int64(), c_uint32()
            code = vt(reader, 9, HRESULT, c_uint32, c_uint32, POINTER(c_uint32), POINTER(c_uint32), POINTER(c_int64), POINTER(c_void_p))(
                reader, FIRST_AUDIO, 0, byref(actual), byref(flags), byref(ts), byref(sample))
            if code < 0:
                release(sample)
                fail(code, "read sample")
            chunk = b""
            if sample.value and not (flags.value & (ERROR | TICK)):
                buf = c_void_p()
                if vt(sample, 41, HRESULT, POINTER(c_void_p))(sample, byref(buf)) < 0 or not buf.value:
                    count = c_uint32()
                    vt(sample, 39, HRESULT, POINTER(c_uint32))(sample, byref(count))
                    if count.value:
                        vt(sample, 40, HRESULT, c_uint32, POINTER(c_void_p))(sample, 0, byref(buf))
                if buf.value:
                    ptr, cur = c_void_p(), c_uint32()
                    locked = vt(buf, 3, HRESULT, POINTER(c_void_p), POINTER(c_uint32), POINTER(c_uint32))(buf, byref(ptr), None, byref(cur))
                    if locked >= 0 and ptr.value and cur.value:
                        chunk = ctypes.string_at(ptr.value, int(cur.value))
                    if locked >= 0:
                        vt(buf, 4, HRESULT)(buf)
                    release(buf)
                release(sample)
            if chunk:
                parts.append(chunk)
                total += len(chunk)
                if total > 256 * 1024 * 1024:
                    raise WaveformError("The audio is too long to draw a waveform in memory.")
            if flags.value & EOS:
                break
        raw = b"".join(parts)
        if not raw:
            raise WaveformError("Media Foundation returned no audio samples.")
        return raw, int(rate), int(ch)
    finally:
        release(media)
        release(reader)
        if started:
            try:
                mfplat.MFShutdown()
            except Exception:
                pass
        if inited:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass


def _disk_path(cache_dir: Path, key: tuple) -> Path:
    name = hashlib.sha256("|".join(map(str, key)).encode()).hexdigest() + ".json"
    return cache_dir / name


def _read_disk(cache_dir: Path, key: tuple) -> Waveform | None:
    p = _disk_path(cache_dir, key)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return Waveform(tuple(d["mins"]), tuple(d["maxs"]), float(d["duration"]), int(d["sample_rate"]), int(d["channels"]))
    except Exception:
        return None


def _write_disk(cache_dir: Path, key: tuple, wf: Waveform) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        p = _disk_path(cache_dir, key)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"mins": list(wf.mins), "maxs": list(wf.maxs), "duration": wf.duration,
                                   "sample_rate": wf.sample_rate, "channels": wf.channels}), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def visible_peaks(wf: Waveform, view_start: float, view_end: float, width: int) -> tuple[list, list]:
    """Peaks for the pixels of one view. Uses the cached peaks only."""
    width = max(1, int(width))
    n = len(wf.mins)
    if n == 0 or wf.duration <= 0:
        return [0.0] * width, [0.0] * width
    view_start = max(0.0, min(float(view_start), wf.duration))
    view_end = max(view_start, min(float(view_end), wf.duration))
    if view_end <= view_start:
        view_end = min(wf.duration, view_start + 1e-6)
    span = view_end - view_start
    mins, maxs = [], []
    for i in range(width):
        a = int((view_start + span * i / width) / wf.duration * n)
        b = int((view_start + span * (i + 1) / width) / wf.duration * n)
        a = max(0, min(a, n - 1))
        b = max(a + 1, min(max(b, a + 1), n))
        mins.append(min(wf.mins[a:b]))
        maxs.append(max(wf.maxs[a:b]))
    return mins, maxs


def view_window(duration: float, zoom: float, center: float) -> tuple[float, float]:
    """The visible time range. Zoom 1 shows the whole file. The selection times are not changed here."""
    duration = float(duration)
    if duration <= 0:
        return 0.0, 0.0
    zoom = max(1.0, float(zoom))
    span = duration / zoom
    start = float(center) - span / 2.0
    if start < 0:
        start = 0.0
    end = start + span
    if end > duration:
        end = duration
        start = max(0.0, end - span)
    return start, end


def x_to_time(x: float, width: float, view_start: float, view_end: float) -> float:
    if width <= 0:
        return float(view_start)
    frac = min(1.0, max(0.0, float(x) / float(width)))
    return float(view_start) + frac * (float(view_end) - float(view_start))


def selection_rect(width: float, view_start: float, view_end: float, sel_start: float, sel_end: float) -> tuple[float, float]:
    """(left, width) in pixels of the selection overlay. Empty when the selection is outside the view."""
    span = float(view_end) - float(view_start)
    if width <= 0 or span <= 0:
        return 0.0, 0.0
    a = max(float(sel_start), float(view_start))
    b = min(float(sel_end), float(view_end))
    if b <= a:
        return 0.0, 0.0
    left = (a - float(view_start)) / span * float(width)
    right = (b - float(view_start)) / span * float(width)
    return left, max(0.0, right - left)


def render_png(mins, maxs, dest, width: int, height: int = 96) -> None:
    """One column per pixel. The selection is not drawn here, so dragging it does not write a new image."""
    from PIL import Image, ImageDraw
    width, height = max(1, int(width)), max(1, int(height))
    im = Image.new("RGBA", (width, height), (12, 22, 40, 255))
    draw = ImageDraw.Draw(im)
    mid = (height - 1) / 2.0
    draw.line([(0, mid), (width, mid)], fill=(34, 51, 82, 255))
    amp = mid - 2
    cols = max(1, min(len(mins), len(maxs)))
    for i in range(width):
        j = min(cols - 1, int(i * cols / width))
        y1 = mid - float(maxs[j]) * amp
        y2 = mid - float(mins[j]) * amp
        if y2 < y1:
            y1, y2 = y2, y1
        draw.line([(i, y1), (i, y2)], fill=(43, 115, 255, 255))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "PNG")


def validate_repaint(*, path_exists: bool, duration: float, start: float, end: float, caption: str) -> str:
    """'' when a repaint may be queued. Otherwise a sentence, and the caller must not queue."""
    if not path_exists:
        return "The source audio file is missing."
    if not (caption or "").strip():
        return "Write a caption for the repaint."
    try:
        start_f, end_f = float(start), float(end)
    except (TypeError, ValueError):
        return "Start and end must be times in seconds."
    if duration <= 0:
        return "The length of the source audio is unknown, so the selection cannot be checked."
    if start_f < 0 or start_f > REPAINT_MAX_S:
        return f"Start must be from 0 to {REPAINT_MAX_S:.0f} seconds."
    if end_f <= start_f:
        return "End must be after the start."
    if end_f > REPAINT_MAX_S:
        return f"End must be at most {REPAINT_MAX_S:.0f} seconds."
    if end_f > float(duration) + 0.05:
        return "End is past the end of the audio file."
    return ""
