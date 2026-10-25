"""Audio playback for Windows through MCI (winmm): mp3 / wav / flac-less, no extra dependency. Play, pause, stop, seek, volume, length.

Every call is a short blocking MCI command, so the UI can call them directly. On a system without winmm `available` is False.
"""
from __future__ import annotations

import ctypes
import sys


class MciPlayer:
    def __init__(self, alias: str = "wpmusic"):
        self.ALIAS = alias
        self._mci = None
        if sys.platform == "win32":
            try:
                self._mci = ctypes.windll.winmm.mciSendStringW
            except Exception:
                self._mci = None
        self._open = False
        self._path = ""
        self.length_ms = 0

    @property
    def available(self) -> bool:
        return self._mci is not None

    def _cmd(self, text: str) -> tuple[int, str]:
        buf = ctypes.create_unicode_buffer(256)
        err = self._mci(text, buf, 255, 0)
        return err, buf.value

    def load(self, path: str) -> None:
        self.close()
        if not self.available:
            raise RuntimeError("Audio playback needs Windows (winmm)")
        err, _ = self._cmd(f'open "{path}" type mpegvideo alias {self.ALIAS}')
        if err:                                           # wav and friends are not mpegvideo
            err, _ = self._cmd(f'open "{path}" alias {self.ALIAS}')
        if err:
            raise RuntimeError(f"Cannot open the audio file (MCI error {err})")
        self._open, self._path = True, path
        self._cmd(f"set {self.ALIAS} time format milliseconds")
        _, ln = self._cmd(f"status {self.ALIAS} length")
        self.length_ms = int(ln or 0)

    def close(self) -> None:
        if self._open:
            self._cmd(f"close {self.ALIAS}")
        self._open, self._path, self.length_ms = False, "", 0

    @property
    def loaded(self) -> bool:
        return self._open

    def play(self) -> None:
        if self._open:
            if self.position_ms() >= self.length_ms - 50:
                self._cmd(f"seek {self.ALIAS} to start")
            self._cmd(f"play {self.ALIAS}")

    def pause(self) -> None:
        if self._open:
            self._cmd(f"pause {self.ALIAS}")

    def stop(self) -> None:
        if self._open:
            self._cmd(f"stop {self.ALIAS}")
            self._cmd(f"seek {self.ALIAS} to start")

    def seek(self, ms: int, resume: bool | None = None) -> None:
        if not self._open:
            return
        was = self.is_playing() if resume is None else resume
        ms = max(0, min(int(ms), max(0, self.length_ms - 1)))
        self._cmd(f"seek {self.ALIAS} to {ms}")
        if was:
            self._cmd(f"play {self.ALIAS}")

    def position_ms(self) -> int:
        if not self._open:
            return 0
        _, v = self._cmd(f"status {self.ALIAS} position")
        return int(v or 0)

    def is_playing(self) -> bool:
        if not self._open:
            return False
        _, v = self._cmd(f"status {self.ALIAS} mode")
        return v == "playing"

    def probe_length_ms(self, path: str) -> int:
        """Length of a file without disturbing playback (use a player made with its own alias)."""
        try:
            self.load(path)
            return self.length_ms
        except Exception:
            return 0
        finally:
            self.close()

    def set_volume(self, frac: float) -> None:
        if self._open:
            self._cmd(f"setaudio {self.ALIAS} volume to {int(max(0.0, min(1.0, frac)) * 1000)}")
