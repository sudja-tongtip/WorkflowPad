"""Export a take as a video: one picture, the take's audio and an animated waveform, made by ffmpeg.

This module has no UI. It builds the ffmpeg command (always as a list, never through a shell, so spaces and Thai letters in a path are safe), finds and tests the ffmpeg program,
runs it in the caller's thread with progress and cancel, and keeps the settings and the picture of each song.

  full video   -loop 1 -i image  -i audio  -filter_complex "[0:v]scale..crop[bg];[1:a]showwaves=..[wave];[bg][wave]overlay=0:H-h-margin[outv]"  -map [outv] -map 1:a  libx264 / aac  -shortest
  slideshow    one input per slide (the pictures in turn, repeated until the audio is covered), each slide zooming or panning slowly for its whole time (zoompan), joined by xfade transitions;
               with ONE picture the same picture repeats and the zoom alternates in and out, so the video keeps moving. Long filter graphs go in a script file (Windows limits the command line).
  a part       the same with  -ss start -t length  on the audio input
  preview      the same, small and fast (about 8 s), for looking at it before the real export
  still        one picture of the result

Nothing here changes a take, a song or a setting of the song. The output is written to a temporary name and renamed when ffmpeg has finished, so an unfinished file never has the final name,
and an existing file is never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SIZES = (
    ("landscape_1080", "Landscape 16:9 · 1920 × 1080", 1920, 1080),
    ("landscape_720", "Landscape 16:9 · 1280 × 720", 1280, 720),
    ("portrait_1080", "Portrait 9:16 · 1080 × 1920", 1080, 1920),
    ("portrait_720", "Portrait 9:16 · 720 × 1280", 720, 1280),
    ("square_1080", "Square 1:1 · 1080 × 1080", 1080, 1080),
    ("custom", "Custom size", 0, 0),
)
SIZE_BY_KEY = {k: (label, w, h) for k, label, w, h in SIZES}
WAVE_MODES = (("cline", "Centered line"), ("line", "Line"), ("p2p", "Peak to peak"), ("point", "Points"))
COLORS = (("cyan", "Cyan"), ("white", "White"), ("yellow", "Yellow"), ("lime", "Green"), ("magenta", "Magenta"), ("orange", "Orange"), ("red", "Red"), ("deepskyblue", "Sky blue"))
FITS = (("fill", "Fill the frame (the edges may be cropped)"), ("fit", "Show the whole picture (black bars)"))
MOTIONS = (("zoom_in", "Slow zoom in"), ("zoom_out", "Slow zoom out"), ("alternate", "Zoom in, then out"), ("pan_left", "Slow pan to the left"), ("pan_right", "Slow pan to the right"), ("none", "No movement"))
TRANSITIONS = (("fade", "Fade"), ("dissolve", "Dissolve"), ("slideleft", "Slide left"), ("slideright", "Slide right"), ("wipeleft", "Wipe"), ("zoomin", "Zoom in"),
               ("circleopen", "Circle open"), ("mixed", "Mixed (a different one each time)"))
MIXED_ORDER = ("fade", "slideleft", "dissolve", "zoomin", "slideright", "wipeleft", "circleopen")      # 'mixed' takes these in turn: the same song always gets the same video
ZOOM_AMOUNT = 0.12                    # the picture grows by 12 % over one slide
MAX_SLIDES = 150
FPS = 25
MIN_SIDE, MAX_SIDE = 64, 4096
IMAGE_TYPES = ("jpg", "jpeg", "png", "webp", "bmp")
PREVIEW_SECONDS = 8.0
PREVIEW_LONG_SIDE = 640
COVER_SIZE = 512


class VideoError(RuntimeError):
    """ffmpeg could not make the video (the message is for the user)."""


class VideoCancelled(VideoError):
    """The user stopped the export. Nothing was left behind."""


# ── settings ─────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class VideoSettings:
    ffmpeg: str = ""                 # the path of ffmpeg.exe; '' = look for one on the PATH
    size: str = "landscape_1080"     # a key of SIZES
    width: int = 1920                # used when size == 'custom'
    height: int = 1080
    wave_mode: str = "cline"         # a key of WAVE_MODES
    color: str = "cyan"              # a key of COLORS or #RRGGBB
    wave_height: int = 0             # 0 = the default of this size
    bottom_margin: int = -1          # -1 = the default of this size
    fit: str = "fill"                # a key of FITS
    motion: str = "zoom_in"          # a key of MOTIONS: how each picture moves while it is shown
    slide_seconds: float = 6.0       # how long each picture is shown (the transition included)
    transition: str = "fade"         # a key of TRANSITIONS
    transition_seconds: float = 1.0
    shuffle: bool = False            # the pictures in a fixed mixed order instead of the order they were added
    image_dir: str = ""              # the folder the picture chooser opened last

    def dims(self) -> tuple:
        if self.size in SIZE_BY_KEY and self.size != "custom":
            _l, w, h = SIZE_BY_KEY[self.size]
            return w, h
        return int(self.width), int(self.height)

    def wave_h(self) -> int:
        return int(self.wave_height) if self.wave_height > 0 else default_wave_height(*self.dims())

    def margin(self) -> int:
        return int(self.bottom_margin) if self.bottom_margin >= 0 else default_margin(*self.dims())


def default_wave_height(w: int, h: int) -> int:
    """300 px on a 1080-pixel short side, in proportion for other sizes (always even)."""
    return max(20, _even(300 * min(w, h) / 1080))


def default_margin(w: int, h: int) -> int:
    return max(0, _even(100 * min(w, h) / 1080))


def settings_path() -> Path:
    from . import store as ST
    return ST.library_dir() / "video_export.json"


def load_settings() -> VideoSettings:
    base = VideoSettings()
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    if not isinstance(data, dict):
        return base
    for k, v in data.items():
        if k in asdict(base) and (type(v) is type(getattr(base, k)) or (isinstance(getattr(base, k), float) and type(v) is int)):
            setattr(base, k, v)
    return base


def save_settings(s: VideoSettings) -> None:
    try:
        from . import store as ST
        ST._atomic(settings_path(), json.dumps(asdict(s), ensure_ascii=False, indent=1))
    except OSError:
        pass


def _even(n) -> int:
    return max(2, int(round(float(n))) // 2 * 2)


def color_arg(color: str) -> str:
    """The value for showwaves `colors=`: a known name, or #RRGGBB written as 0xRRGGBB. Anything else is an error, never guessed."""
    c = (color or "").strip()
    if c in {k for k, _l in COLORS}:
        return c
    if re.fullmatch(r"#[0-9a-fA-F]{6}", c):
        return "0x" + c[1:].upper()
    raise VideoError(f"The wave colour “{color}” is not a colour name from the list or #RRGGBB.")


def validate(s: VideoSettings) -> list:
    """What is wrong with the settings (an empty list = usable). Nothing is repaired."""
    out = []
    w, h = s.dims()
    for name, v in (("Width", w), ("Height", h)):
        if not MIN_SIDE <= v <= MAX_SIDE:
            out.append(f"{name} must be between {MIN_SIDE} and {MAX_SIDE} pixels.")
        elif v % 2:
            out.append(f"{name} must be an even number (the video format needs it).")
    if not out:
        wh, mg = s.wave_h(), s.margin()
        if wh % 2 or not 20 <= wh <= h:
            out.append(f"The wave height must be an even number between 20 and {h}.")
        if mg < 0 or mg + wh > h:
            out.append("The bottom margin and the wave height do not fit in the video height.")
    if s.wave_mode not in {k for k, _l in WAVE_MODES}:
        out.append("The wave style is not one of the choices.")
    if s.fit not in {k for k, _l in FITS}:
        out.append("The picture fit is not one of the choices.")
    if s.motion not in {k for k, _l in MOTIONS}:
        out.append("The picture movement is not one of the choices.")
    if s.transition not in {k for k, _l in TRANSITIONS}:
        out.append("The transition is not one of the choices.")
    if not 2.0 <= float(s.slide_seconds) <= 60.0:
        out.append("Each picture must be shown for 2 to 60 seconds.")
    elif not 0.2 <= float(s.transition_seconds) <= min(3.0, float(s.slide_seconds) / 2):
        out.append(f"The transition must be between 0.2 and {min(3.0, float(s.slide_seconds) / 2):g} seconds (at most half of the time a picture is shown).")
    try:
        color_arg(s.color)
    except VideoError as ex:
        out.append(str(ex))
    return out


# ── ffmpeg ───────────────────────────────────────────────────────────────────────────────────────────────
def find_ffmpeg(configured: str = "") -> str:
    """The ffmpeg to use: the configured path when it is a file, else one on the PATH, else ''."""
    c = (configured or "").strip().strip('"')
    if c:
        p = Path(c)
        if p.is_dir():
            p = p / "ffmpeg.exe"
        return str(p) if p.is_file() else ""
    return shutil.which("ffmpeg") or ""


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def test_ffmpeg(path: str) -> tuple:
    """(ok, message). ok only when the program runs and has the filters this export needs."""
    exe = find_ffmpeg(path)
    if not exe:
        return False, "ffmpeg was not found. Choose ffmpeg.exe, or put it on the PATH."
    try:
        v = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=_no_window())
        if v.returncode != 0:
            return False, "This file did not run as ffmpeg: " + (v.stderr or v.stdout).strip()[:160]
        first = (v.stdout.splitlines() or [""])[0].strip()
        f = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=_no_window())
        have = f.stdout or ""
        missing = [n for n in ("showwaves", "overlay", "scale", "pad", "crop") if not re.search(r"\b" + n + r"\b", have)]
        encoders = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=_no_window()).stdout or ""
        for n in ("libx264", "aac"):
            if not re.search(r"\b" + n + r"\b", encoders):
                missing.append(n)
        if missing:
            return False, f"{first}: this build lacks {', '.join(missing)}."
        return True, first
    except (OSError, subprocess.SubprocessError) as ex:
        return False, f"ffmpeg could not be started: {ex}"


# ── the command ──────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Job:
    image: object                    # one picture (a path) or a list of pictures
    audio: str
    output: str
    settings: VideoSettings = field(default_factory=VideoSettings)
    start: float = 0.0               # seconds into the take
    length: float = 0.0              # seconds of audio to use; 0 = to the end of the take
    kind: str = "video"              # 'video' | 'preview' | 'still'
    total: float = 0.0               # seconds of the finished video (the audio part): the slide count needs it; `run` fills it in from the audio when it is 0

    def images(self) -> list:
        return [str(x) for x in (self.image if isinstance(self.image, (list, tuple)) else [self.image]) if x]

    def moving(self) -> bool:
        """A slideshow or a moving picture (not a still frame, and not the plain one-picture video)."""
        return self.kind != "still" and (len(self.images()) > 1 or self.settings.motion != "none")


def _scaled(job: Job) -> tuple:
    """(width, height, wave height, margin) of this job: the settings, or the small size of a preview / still."""
    s = job.settings
    w, h = s.dims()
    wh, mg = s.wave_h(), s.margin()
    if job.kind in ("preview", "still") and max(w, h) > PREVIEW_LONG_SIDE:
        k = PREVIEW_LONG_SIDE / max(w, h)
        return _even(w * k), _even(h * k), max(8, _even(wh * k)), _even(mg * k)
    return w, h, wh, mg


def _wave_chain(job: Job, w: int, wh: int, audio_index: int) -> str:
    s = job.settings
    if s.wave_mode == "point":
        # One dot per sample would be about 1800 dots of one pixel across the width: it reads as a thin line. So the dots are drawn on a small canvas from a thinned-out copy of the audio
        # (about one dot for every other column) and enlarged without smoothing, which gives clear square dots with space between them. The audio of the video is not touched.
        step = max(2, round(w / 320))
        cols = max(8, w // step)
        return (f"[{audio_index}:a]aresample={FPS * max(4, cols // 2)},showwaves=s={cols}x{max(4, wh // step)}:colors={color_arg(s.color)}:mode=point:rate={FPS},"
                f"scale={w}:{wh}:flags=neighbor[wave]")
    return f"[{audio_index}:a]showwaves=s={w}x{wh}:colors={color_arg(s.color)}:mode={s.wave_mode}:rate={FPS}[wave]"


def slide_count(total: float, seconds: float, transition: float) -> int:
    """How many slides cover `total` seconds when each is shown for `seconds` and the next one starts `transition` seconds before it ends: n*seconds - (n-1)*transition >= total."""
    if total <= seconds:
        return 1
    return max(1, math.ceil((total - transition) / (seconds - transition)))


def slide_plan(job: Job, total: float) -> list:
    """[(picture, motion, transition to the next slide)] for the whole video. The pictures in turn (a fixed mixed order when `shuffle` is on), repeated until the audio is covered. With one picture
    it repeats and a zoom alternates in and out, so the video keeps moving instead of jumping back to the start of the zoom."""
    s = job.settings
    pics = job.images()
    order = list(range(len(pics)))
    if s.shuffle and len(pics) > 2:
        random.Random(len(pics) * 7919).shuffle(order)
    n = slide_count(total, float(s.slide_seconds), float(s.transition_seconds))
    if n > MAX_SLIDES:
        raise VideoError(f"That would be {n} slides. Show each picture longer, or use fewer, to stay under {MAX_SLIDES}.")
    out = []
    for i in range(n):
        motion = s.motion
        if motion == "alternate" or (len(pics) == 1 and motion in ("zoom_in", "zoom_out")):
            motion = "zoom_in" if i % 2 == 0 else "zoom_out"
        trans = MIXED_ORDER[i % len(MIXED_ORDER)] if s.transition == "mixed" else s.transition
        out.append((pics[order[i % len(order)]], motion, trans))
    return out


def _zoompan(motion: str, frames: int, w: int, h: int) -> str:
    last = max(1, frames - 1)
    za = ZOOM_AMOUNT
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom_in":
        z, x, y = f"1+{za}*on/{last}", cx, cy
    elif motion == "zoom_out":
        z, x, y = f"{1 + za}-{za}*on/{last}", cx, cy
    elif motion == "pan_left":
        z, x, y = f"{1 + za}", f"(iw-iw/zoom)*(1-on/{last})", cy
    else:
        z, x, y = f"{1 + za}", f"(iw-iw/zoom)*on/{last}", cy
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS}"


def _slide_chain(index: int, w: int, h: int, fit: str, motion: str, frames: int) -> str:
    """One slide of the video: the picture made to fill the frame (at twice the size, so the slow zoom does not stutter), moved by zoompan, at the size and rate of the video."""
    bw, bh = w * 2, h * 2
    if fit == "fit":
        base = f"scale={bw}:{bh}:force_original_aspect_ratio=decrease,pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    else:
        base = f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},setsar=1"
    if motion == "none":
        return f"[{index}:v]{base},scale={w}:{h},fps={FPS},format=yuv420p[s{index}]"
    return f"[{index}:v]{base},{_zoompan(motion, frames, w, h)},format=yuv420p[s{index}]"


def filter_graph(job: Job, total: float = 0.0) -> str:
    w, h, wh, mg = _scaled(job)
    s = job.settings
    if not job.moving():                                                    # one picture that stays still (also the still frame of a preview)
        if s.fit == "fit":
            bg = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[bg]"
        else:
            bg = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[bg]"
        return f"{bg};{_wave_chain(job, w, wh, 1)};[bg][wave]overlay=0:H-h-{mg}:format=auto,format=yuv420p[outv]"
    plan = slide_plan(job, total or job.total)
    sec, tr = float(s.slide_seconds), float(s.transition_seconds)
    frames = int(round(sec * FPS))
    parts = [_slide_chain(i, w, h, s.fit, motion, frames) for i, (_p, motion, _t) in enumerate(plan)]
    last = "s0"
    for k in range(1, len(plan)):
        parts.append(f"[{last}][s{k}]xfade=transition={plan[k - 1][2]}:duration={tr:g}:offset={k * (sec - tr):.3f}[x{k}]")
        last = f"x{k}"
    parts.append(_wave_chain(job, w, wh, len(plan)))
    parts.append(f"[{last}][wave]overlay=0:H-h-{mg}:format=auto,format=yuv420p[outv]")
    return ";".join(parts)


SCRIPT_OVER = 8000                  # a filter graph longer than this goes into a script file (the Windows command line has a limit)


SCRIPT_FLAGS = ("-/filter_complex", "-filter_complex_script")       # ffmpeg 7 and later read a filter graph from a file with -/filter_complex; the older spelling was removed in recent versions


def build_command(job: Job, ffmpeg: str = "", script: "Path | None" = None, script_flag: str = SCRIPT_FLAGS[0]) -> list:
    """The ffmpeg argument list. Raises VideoError for settings that cannot be used. A long filter graph is written to `script` (a path the caller removes afterwards)."""
    bad = validate(job.settings)
    if bad:
        raise VideoError(" ".join(bad))
    exe = ffmpeg or find_ffmpeg(job.settings.ffmpeg) or "ffmpeg"
    length = float(job.length or 0)
    s = job.settings
    if job.kind == "preview":
        wanted = max(PREVIEW_SECONDS, float(s.slide_seconds) + float(s.transition_seconds) + 1.5) if job.moving() and len(job.images()) > 1 else PREVIEW_SECONDS
        length = min(length, wanted) if length > 0 else wanted
    elif job.kind == "still":
        length = 3.0
    total = float(job.total or 0) or length
    if job.moving() and total <= 0:
        raise VideoError("The length of the video is needed to place the slides.")
    graph = filter_graph(job, total)
    pics = [] if not job.moving() else [p for p, _m, _t in slide_plan(job, total)]
    cmd = [exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if job.moving():
        for p, motion, _t in slide_plan(job, total):
            if motion == "none":
                cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{float(s.slide_seconds):.3f}", "-i", p]
            else:
                cmd += ["-i", p]                                                    # one frame: zoompan makes the frames of the slide from it
        audio_index = len(pics)
    else:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-i", job.images()[0]]
        audio_index = 1
    if job.start > 0:
        cmd += ["-ss", f"{job.start:.3f}"]
    if length > 0:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-i", str(job.audio)]
    if script is not None and len(graph) > SCRIPT_OVER:
        Path(script).write_text(graph, encoding="utf-8")
        cmd += [script_flag, str(script)]
    else:
        cmd += ["-filter_complex", graph]
    cmd += ["-map", "[outv]"]
    if job.kind == "still":
        return cmd + ["-ss", "2", "-frames:v", "1", str(job.output)]
    small = job.kind == "preview"
    cmd += ["-map", f"{audio_index}:a", "-c:v", "libx264", "-preset", "ultrafast" if small else "medium", "-crf", "30" if small else "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "128k" if small else "320k", "-shortest", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(job.output)]
    return cmd


def probe_duration(ffmpeg: str, audio) -> float:
    """The length of an audio file in seconds, read from ffmpeg's own description of it (0.0 when it cannot be read)."""
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(audio)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=_no_window())
    except (OSError, subprocess.SubprocessError):
        return 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def command_text(cmd: list) -> str:
    """The command as one line to read or copy (the program itself never runs this text)."""
    return " ".join(f'"{a}"' if re.search(r"[\s\[\];]", a) else a for a in cmd)


def unique_output(folder: Path, title: str, number: int, suffix: str = ".mp4", tag: str = "") -> Path:
    """`<title> - Take 07.mp4` in the folder; an existing file is never overwritten (a ' (2)' is added)."""
    from . import store as ST
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{ST.safe_name(title)} - Take {number:02d}{tag}"
    dest = folder / (base + suffix)
    i = 2
    while dest.exists():
        dest = folder / f"{base} ({i}){suffix}"
        i += 1
    return dest


# ── running it ───────────────────────────────────────────────────────────────────────────────────────────
def check_picture(path) -> None:
    """Raises VideoError when the file is not a picture that can be read. (ffmpeg given a broken picture to loop can wait for ever instead of failing, so it is checked first.)"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
    except (OSError, ValueError, SyntaxError) as ex:
        raise VideoError(f"The picture cannot be read: {ex}")


STALL_SECONDS = 60.0               # no sign of life from ffmpeg for this long: it is stopped and the user is told


def run(job: Job, on_progress=None, cancel: "threading.Event | None" = None, ffmpeg: str = "") -> Path:
    """Run ffmpeg and return the finished file. `on_progress(fraction 0..1)` is called while it works. Raises VideoCancelled (nothing is left behind) or VideoError.
    A filter graph kept in a script file is passed the way this ffmpeg reads it: the newer spelling first, the older one when ffmpeg does not know the first."""
    try:
        return _run(job, on_progress, cancel, ffmpeg, SCRIPT_FLAGS[0])
    except VideoError as ex:
        if isinstance(ex, VideoCancelled) or "Unrecognized option" not in str(ex):
            raise
        return _run(job, on_progress, cancel, ffmpeg, SCRIPT_FLAGS[1])


def _run(job: Job, on_progress, cancel, ffmpeg: str, script_flag: str) -> Path:
    pics = job.images()
    if not pics:
        raise VideoError("Choose a picture first.")
    for p in dict.fromkeys(pics):
        if not Path(p).is_file():
            raise VideoError(f"The picture file is missing: {Path(p).name}")
        check_picture(p)
    if not Path(job.audio).is_file():
        raise VideoError("The audio file of the take is missing.")
    exe = ffmpeg or find_ffmpeg(job.settings.ffmpeg)
    if not exe:
        raise VideoError("ffmpeg was not found. Choose ffmpeg.exe in the export window.")
    final = Path(job.output)
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_name(final.stem + ".part" + final.suffix)
    script = final.with_name(final.stem + ".filter.txt")
    length = float(job.length or 0)
    if job.kind == "preview":
        wanted = max(PREVIEW_SECONDS, float(job.settings.slide_seconds) + float(job.settings.transition_seconds) + 1.5) if job.moving() and len(pics) > 1 else PREVIEW_SECONDS
        length = min(length, wanted) if length > 0 else wanted
    elif job.kind == "still":
        length = 3.0
    if length <= 0:                                                               # the whole take: the progress and the slide count need its length
        length = max(0.0, probe_duration(exe, job.audio) - max(0.0, job.start))
        if length <= 0 and job.moving():
            raise VideoError("The length of the audio could not be read, so the slides cannot be placed. Choose a part of the take instead.")
    total = length
    try:
        cmd = build_command(Job(job.image, job.audio, str(part), job.settings, job.start, job.length, job.kind, total), exe, script, script_flag)
    except VideoError:
        script.unlink(missing_ok=True)
        raise
    tail: list = []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=_no_window())
    except OSError as ex:
        raise VideoError(f"ffmpeg could not be started: {ex}")

    def drain():
        for line in proc.stderr:
            tail.append(line)
            del tail[:-30]
    t = threading.Thread(target=drain, daemon=True)
    t.start()
    alive = {"at": time.time(), "stalled": False}

    def watch():
        while proc.poll() is None:
            time.sleep(1.0)
            if proc.poll() is None and time.time() - alive["at"] > STALL_SECONDS:
                alive["stalled"] = True
                proc.kill()
    threading.Thread(target=watch, daemon=True).start()
    try:
        for line in proc.stdout:
            alive["at"] = time.time()
            if cancel is not None and cancel.is_set():
                proc.terminate()
                break
            if on_progress is not None and total > 0 and line.startswith(("out_time_us=", "out_time_ms=")):
                try:
                    us = float(line.split("=", 1)[1])
                except ValueError:
                    continue
                on_progress(max(0.0, min(1.0, us / 1_000_000.0 / total)))
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
        t.join(timeout=2)
    script.unlink(missing_ok=True)
    if cancel is not None and cancel.is_set():
        part.unlink(missing_ok=True)
        raise VideoCancelled("Stopped. No file was made.")
    if alive["stalled"]:
        part.unlink(missing_ok=True)
        raise VideoError(f"ffmpeg stopped responding for {int(STALL_SECONDS)} seconds and was stopped. Check the picture and the audio file, then try again.")
    if proc.returncode != 0 or not part.exists():
        part.unlink(missing_ok=True)
        raise VideoError(("".join(tail).strip() or f"ffmpeg stopped with code {proc.returncode}")[-600:])
    os.replace(part, final)
    if on_progress is not None:
        on_progress(1.0)
    return final


# ── the pictures of a song ───────────────────────────────────────────────────────────────────────────────
# Each song keeps copies of its pictures in <song>/video_pictures/ and their order in <song>/video_pictures.json. The first one is the song's icon (its centre as a square, cover_<hash>.jpg).
# The originals are never touched. A song from before the list existed had one picture, video_image.<ext>: it is moved into the list the first time it is read.
MAX_PICTURES = 30


def _song_dir(song_id: str) -> Path:
    from . import store as ST
    return ST.song_dir(song_id)


def _is_picture_name(p: Path) -> bool:
    return p.suffix.lower().lstrip(".") in IMAGE_TYPES


def _read_index(d: Path) -> list:
    try:
        data = json.loads((d / "video_pictures.json").read_text(encoding="utf-8"))
        names = [n for n in data.get("pictures", []) if isinstance(n, str)]
    except (OSError, ValueError, AttributeError):
        names = []
    return [n for n in names if (d / "video_pictures" / n).is_file()]


def _write_index(d: Path, names: list) -> None:
    from . import store as ST
    ST._atomic(d / "video_pictures.json", json.dumps({"pictures": names}, ensure_ascii=False, indent=1))


def _migrate_single(d: Path) -> None:
    """A song from before the list: its one picture (video_image.<ext>) becomes the first of the list."""
    old = next((p for p in sorted(d.glob("video_image.*")) if _is_picture_name(p) and p.is_file()), None)
    if old is None or (d / "video_pictures.json").exists():
        return
    folder = d / "video_pictures"
    folder.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(old.read_bytes()).hexdigest()[:12] + old.suffix.lower()
    shutil.move(str(old), str(folder / name))
    _write_index(d, [name])


def song_images(song_id: str) -> list:
    """The paths of this song's pictures, in order ([] when it has none)."""
    try:
        d = _song_dir(song_id)
    except ValueError:
        return []
    if not d.is_dir():
        return []
    _migrate_single(d)
    return [str(d / "video_pictures" / n) for n in _read_index(d)]


def song_image(song_id: str) -> str:
    """The first picture of the song (its icon picture), or ''."""
    pics = song_images(song_id)
    return pics[0] if pics else ""


def song_cover(song_id: str) -> str:
    """The square icon made from the first picture, or ''."""
    try:
        d = _song_dir(song_id)
    except ValueError:
        return ""
    return next((str(p) for p in sorted(d.glob("cover_*.jpg")) if p.is_file()), "")


def make_square_cover(src, dest, size: int = COVER_SIZE) -> None:
    """The centre of the picture as a size × size JPEG (EXIF rotation applied). Raises VideoError for a file that is not a picture."""
    try:
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            ImageOps.fit(im, (size, size), Image.LANCZOS, centering=(0.5, 0.5)).save(dest, "JPEG", quality=90)
    except (OSError, ValueError) as ex:
        raise VideoError(f"This file is not a picture that can be read: {ex}")


def _refresh_cover(d: Path, names: list) -> None:
    """The icon follows the first picture: a new file name for a new picture, so the window never shows an old cached one."""
    for old in d.glob("cover_*.jpg"):
        old.unlink(missing_ok=True)
    if not names:
        return
    tmp = d / "cover.tmp.jpg"
    make_square_cover(d / "video_pictures" / names[0], tmp)
    os.replace(tmp, d / ("cover_" + hashlib.sha1((d / "video_pictures" / names[0]).read_bytes()).hexdigest()[:10] + ".jpg"))


def add_song_images(song_id: str, sources: list) -> list:
    """Add pictures to the song (copies). All or nothing: when one file is not a picture, nothing is added. Returns the paths of all the song's pictures."""
    srcs = [str(x) for x in sources if x]
    for src in srcs:
        if Path(src).suffix.lower().lstrip(".") not in IMAGE_TYPES:
            raise VideoError(f"Choose pictures ({', '.join(IMAGE_TYPES)}): {Path(src).name}")
        check_picture(src)
    d = _song_dir(song_id)
    d.mkdir(parents=True, exist_ok=True)
    _migrate_single(d)
    names = _read_index(d)
    if len(names) + len(srcs) > MAX_PICTURES:
        raise VideoError(f"A song can have {MAX_PICTURES} pictures at most.")
    folder = d / "video_pictures"
    folder.mkdir(parents=True, exist_ok=True)
    for src in srcs:
        name = hashlib.sha1(Path(src).read_bytes()).hexdigest()[:12] + Path(src).suffix.lower()
        if not (folder / name).exists():
            shutil.copy2(src, folder / name)
        names.append(name)                                                         # the same picture twice is allowed: it is shown twice
    _write_index(d, names)
    _refresh_cover(d, names)
    return song_images(song_id)


def set_song_image(song_id: str, src: str) -> str:
    """The song gets exactly this one picture (the others are removed). Returns the kept path."""
    clear_song_image(song_id, _keep_if_bad=src)
    return add_song_images(song_id, [src])[0]


def remove_song_image(song_id: str, index: int) -> list:
    d = _song_dir(song_id)
    _migrate_single(d)
    names = _read_index(d)
    if not 0 <= index < len(names):
        return song_images(song_id)
    gone = names.pop(index)
    _write_index(d, names)
    if gone not in names:
        (d / "video_pictures" / gone).unlink(missing_ok=True)
    _refresh_cover(d, names)
    return song_images(song_id)


def move_song_image(song_id: str, index: int, delta: int) -> list:
    """Move a picture one place earlier (delta -1) or later (+1). The first picture is the icon, so the icon changes with it."""
    d = _song_dir(song_id)
    names = _read_index(d)
    j = index + delta
    if 0 <= index < len(names) and 0 <= j < len(names):
        names[index], names[j] = names[j], names[index]
        _write_index(d, names)
        _refresh_cover(d, names)
    return song_images(song_id)


def clear_song_image(song_id: str, _keep_if_bad: str = "") -> None:
    """Remove every picture of the song and its icon. (With `_keep_if_bad` the check of that file comes first, so a file that is not a picture changes nothing.)"""
    if _keep_if_bad:
        check_picture(_keep_if_bad)
    d = _song_dir(song_id)
    if not d.is_dir():
        return
    shutil.rmtree(d / "video_pictures", ignore_errors=True)
    for p in list(d.glob("video_image.*")) + list(d.glob("cover_*.jpg")) + [d / "video_pictures.json"]:
        p.unlink(missing_ok=True)


# ── time text ────────────────────────────────────────────────────────────────────────────────────────────
def fmt_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:05.2f}"


def parse_time(text: str) -> float:
    """'75', '75.5', '1:15' or '1:15.5' -> seconds. Raises ValueError for anything else."""
    t = (text or "").strip().replace(",", ".")
    m = re.fullmatch(r"(?:(\d+):)?(\d+(?:\.\d+)?)", t)
    if not m:
        raise ValueError(f"not a time: {text!r}")
    return int(m.group(1) or 0) * 60 + float(m.group(2))
