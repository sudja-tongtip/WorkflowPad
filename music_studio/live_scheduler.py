"""Latest-wins scheduling of the Live Insights analysis.

The analysis of a song's lyrics is slow enough to be felt on the UI thread, so it runs in a worker thread after typing pauses. Rules:
  * at most ONE worker at a time;
  * a result is delivered only when the lyrics, the song, the sheet, the language and the corrections are still exactly what the analysis was made for;
  * when something changed while the worker was busy, the worker analyses the newest snapshot next (after the debounce), so the last result always matches the last text even when the user
    stops typing right after the worker started;
  * an error never leaves the scheduler stuck, and never makes it retry the same snapshot in a loop.

No UI code is imported here: the caller passes in how to take a snapshot, how to analyse it and what to do with the result, which is also what makes it testable with a fake worker.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Snapshot:
    """What an analysis is made for. Two snapshots are the same analysis when `key` and `text` are equal; `payload` (the corrections, the language) rides along and is not compared."""
    key: tuple
    text: str
    payload: Any = field(default=None, compare=False)


class LatestWins:
    def __init__(self, snapshot: Callable[[], Optional[Snapshot]], analyze: Callable[[Snapshot], Any], deliver: Callable[[Snapshot, Any], None], *,
                 debounce: float = 0.25, clock: Callable[[], float] = time.monotonic, sleep=asyncio.sleep, to_thread=asyncio.to_thread,
                 on_error: Optional[Callable[[Exception], None]] = None, current: Optional[Callable[[Snapshot], bool]] = None):
        self.snapshot, self.analyze, self.deliver = snapshot, analyze, deliver
        self.debounce, self.clock, self.sleep, self.to_thread = debounce, clock, sleep, to_thread
        self.on_error = on_error
        self.current = current                      # True when a stored result already matches this snapshot (nothing to analyse)
        self.due = 0.0
        self.running = False
        self.analyses = 0                           # statistics: analyses started
        self.dropped = 0                            # results thrown away because the snapshot changed meanwhile

    def request(self, delay: Optional[float] = None) -> bool:
        """Ask for an analysis `delay` seconds from now (the debounce by default). True when the caller must start `run()` (no worker is running or about to run). The worker is claimed HERE, not when
        run() gets its first turn, so two requests in a row can never start two workers. If the caller cannot start run(), it must call release()."""
        self.due = self.clock() + (self.debounce if delay is None else delay)
        if self.running:
            return False
        self.running = True
        return True

    def release(self) -> None:
        self.running = False

    async def run(self) -> None:
        self.running = True                         # already claimed by request(); kept so a direct run() is also safe
        try:
            while True:
                while self.clock() < self.due:
                    await self.sleep(0.05)
                snap = self.snapshot()
                if snap is None:
                    return
                if self.current is not None and self.current(snap):
                    return
                self.analyses += 1
                try:
                    result = await self.to_thread(self.analyze, snap)
                except asyncio.CancelledError:
                    raise
                except Exception as ex:             # a failed analysis is reported once; the same snapshot is not retried (that would loop), a changed one is
                    if self.on_error is not None:
                        try:
                            self.on_error(ex)
                        except Exception:
                            pass
                    if self.snapshot() == snap:
                        return
                    continue
                now = self.snapshot()
                if now == snap:
                    self.deliver(snap, result)
                    return
                self.dropped += 1                   # the text moved on while the worker was busy: the next loop analyses the newest snapshot
                if now is None:
                    return
        finally:
            self.running = False
