"""One playback service for the whole program (My Music, the Editor mini player and the Output list all use it).

It owns the only MciPlayer, so two songs can never play at once: playing something new always replaces what was playing.
State here is independent of which page or song is open in the editor.
"""
from __future__ import annotations

from dataclasses import dataclass

from .audio_player import MciPlayer
from .wmp_player import WmpPlayer


@dataclass
class QItem:
    song_id: str
    take_id: str
    title: str
    label: str          # "Take 04"
    path: str


class PlayerService:
    def __init__(self):
        self._mci = MciPlayer("wpmusic")
        self._wmp = WmpPlayer()
        self.mci = self._mci            # the backend of the current file: MCI first, the Windows media stack when MCI cannot open it
        self.queue: list[QItem] = []
        self.index = -1
        self.state = "stopped"          # playing | paused | stopped
        self.volume = 0.8
        self.error = ""
        self._listeners = []

    @staticmethod
    def probe_length_ms(path: str) -> int:
        """Duration of a real audio file in ms (0 when unknown), using its own short-lived backend so playback is not disturbed."""
        m = MciPlayer("wpprobe")
        n = m.probe_length_ms(path) if m.available else 0
        if n:
            return n
        w = WmpPlayer()
        try:
            return w.probe_length_ms(path)
        finally:
            w.quit()

    # ── observers ────────────────────────────────────────────────────────────────────────────────────────
    def subscribe(self, fn):
        self._listeners.append(fn)

    def _notify(self):
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return self._mci.available or self._wmp.available

    def shutdown(self) -> None:
        self.release()
        self._wmp.quit()

    @property
    def current(self) -> QItem | None:
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def is_current(self, take_id: str) -> bool:
        c = self.current
        return bool(c and c.take_id == take_id)

    def is_playing_take(self, take_id: str) -> bool:
        return self.is_current(take_id) and self.state == "playing"

    # ── control ──────────────────────────────────────────────────────────────────────────────────────────
    def play_queue(self, items: list[QItem], index: int = 0) -> None:
        self.queue = list(items)
        self.index = -1
        self._start(index)

    def play_take(self, item: QItem, queue: list[QItem] | None = None) -> None:
        """Click on a take: toggle if it is the current one, else play it (its queue = the given list, or just itself)."""
        if self.is_current(item.take_id):
            self.toggle()
            return
        q = queue if queue else [item]
        idx = next((i for i, x in enumerate(q) if x.take_id == item.take_id), 0)
        self.play_queue(q, idx)

    def _start(self, index: int) -> None:
        if not (0 <= index < len(self.queue)):
            self.stop()
            return
        self.mci.close()                                  # the previous song stops first
        self.index = index
        it = self.queue[index]
        try:
            self.mci = self._open_with_fallback(it.path)
            self.mci.set_volume(self.volume)
            self.mci.play()
            self.state, self.error = "playing", ""
        except Exception as ex:
            self.state, self.error = "stopped", str(ex)
        self._notify()

    def _open_with_fallback(self, path: str):
        self._mci.close(); self._wmp.close()
        first_err = ""
        if self._mci.available:
            try:
                self._mci.load(path)
                return self._mci
            except Exception as ex:
                first_err = str(ex)
        try:
            self._wmp.load(path)
            return self._wmp
        except Exception as ex:
            raise RuntimeError("; ".join(x for x in (first_err, str(ex)) if x))

    def toggle(self) -> None:
        if not self.mci.loaded:
            if self.current:
                self._start(self.index)
            return
        if self.state == "playing":
            self.mci.pause(); self.state = "paused"
        else:
            self.mci.play(); self.state = "playing"
        self._notify()

    def stop(self) -> None:
        self.mci.stop()
        self.state = "stopped"
        self._notify()

    def next(self) -> None:
        if self.index + 1 < len(self.queue):
            self._start(self.index + 1)
        else:
            self.stop()

    def prev(self) -> None:
        if self.mci.loaded and self.position_ms() > 3000:
            self.mci.seek(0)
        elif self.index > 0:
            self._start(self.index - 1)
        else:
            self.mci.seek(0)

    def seek_frac(self, frac: float) -> None:
        if self.mci.loaded and self.mci.length_ms:
            self.mci.seek(int(max(0.0, min(1.0, frac)) * self.mci.length_ms), resume=(self.state == "playing"))

    def seek_ms(self, ms: int, song_id: str, take_id: str) -> str:
        """Seek the CURRENT item to `ms`, only when it is exactly (song_id, take_id). Returns '' when the player really is there afterwards, else a sentence that says why not.
        It never changes the queue, never starts another item and never reports a position it did not reach (the position is read back from the backend)."""
        c = self.current
        if not (c and c.song_id == song_id and c.take_id == take_id):
            return "The player is not playing this take. Nothing was changed."
        if not self.mci.loaded:
            return "The audio of this take is not loaded in the player."
        ms = int(ms)
        length = self.mci.length_ms
        if ms < 0 or (length and ms > length):
            return "That time is outside the audio file."
        try:
            self.mci.seek(ms, resume=(self.state == "playing"))
        except Exception as ex:
            return f"The player could not seek: {ex}"
        got = self.mci.position_ms()
        if abs(got - min(ms, max(0, length - 1) if length else ms)) > 1500:
            return f"The player did not move to {ms / 1000:.1f}s (it reports {got / 1000:.1f}s)."
        self._notify()
        return ""

    def set_volume(self, v: float) -> None:
        self.volume = max(0.0, min(1.0, v))
        self.mci.set_volume(self.volume)

    def release(self) -> None:
        """Stop and let go of a file (before deleting it)."""
        self._mci.close(); self._wmp.close()
        self.state = "stopped"
        self._notify()

    # ── clock ────────────────────────────────────────────────────────────────────────────────────────────
    def position_ms(self) -> int:
        return self.mci.position_ms() if self.mci.loaded else 0

    def length_ms(self) -> int:
        return self.mci.length_ms if self.mci.loaded else 0

    def tick(self) -> None:
        """Called a few times a second by the UI: moves on when a track ends."""
        if self.state == "playing" and self.mci.loaded and not self.mci.is_playing():
            if self.index + 1 < len(self.queue):
                self._start(self.index + 1)
            else:
                self.mci.stop()                           # really stop (stop + back to the start): a bare seek(0) left the Windows-media backend playing from the beginning while the program showed "stopped"
                self.state = "stopped"
                self._notify()
