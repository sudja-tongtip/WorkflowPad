"""Playback through Windows' own media stack (WPF MediaPlayer, the same decoders as Windows Media Player), driven by one long-lived PowerShell child process.

Used when MCI cannot open a file (on some Windows installs the legacy 'mpegvideo' driver is missing, so mp3 fails with MCI error 277). No extra package:
powershell.exe ships with Windows. Same interface as MciPlayer, so PlayerService can use either.

Protocol (one line each way): OPEN <path> | PLAY | PAUSE | STOP | SEEK <ms> | VOL <0-100> | POS | LEN | PLAYING | CLOSE | QUIT ; every command answers one line.
"""
from __future__ import annotations

import subprocess
import sys
import threading

_SCRIPT = r"""
Add-Type -AssemblyName presentationCore
$p = New-Object System.Windows.Media.MediaPlayer
$state = "stopped"
$out = [Console]::Out
while (($line = [Console]::In.ReadLine()) -ne $null) {
  $parts = $line.Split(" ", 2)
  $cmd = $parts[0]
  $arg = if ($parts.Length -gt 1) { $parts[1] } else { "" }
  try {
    switch ($cmd) {
      "OPEN"  { $p.Close(); $p.Open([Uri]$arg); $n = 0
                while (-not $p.NaturalDuration.HasTimeSpan -and $n -lt 80) { Start-Sleep -Milliseconds 50; $n++ }
                if ($p.NaturalDuration.HasTimeSpan) { $state = "paused"; $out.WriteLine("OK " + [int]$p.NaturalDuration.TimeSpan.TotalMilliseconds) } else { $out.WriteLine("ERR cannot open") } }
      "PLAY"  { $p.Play(); $state = "playing"; $out.WriteLine("OK") }
      "PAUSE" { $p.Pause(); $state = "paused"; $out.WriteLine("OK") }
      "STOP"  { $p.Stop(); $state = "stopped"; $out.WriteLine("OK") }
      "SEEK"  { $p.Position = [TimeSpan]::FromMilliseconds([double]$arg); $out.WriteLine("OK") }
      "VOL"   { $p.Volume = [double]$arg / 100.0; $out.WriteLine("OK") }
      "POS"   { $out.WriteLine("OK " + [int]$p.Position.TotalMilliseconds) }
      "LEN"   { if ($p.NaturalDuration.HasTimeSpan) { $out.WriteLine("OK " + [int]$p.NaturalDuration.TimeSpan.TotalMilliseconds) } else { $out.WriteLine("OK 0") } }
      "PLAYING" {
                $ended = $p.NaturalDuration.HasTimeSpan -and ($p.Position -ge ($p.NaturalDuration.TimeSpan - [TimeSpan]::FromMilliseconds(80)))
                if ($ended -and $state -eq "playing") { $state = "stopped" }
                if ($state -eq "playing") { $out.WriteLine("OK 1") } else { $out.WriteLine("OK 0") } }
      "CLOSE" { $p.Close(); $state = "stopped"; $out.WriteLine("OK") }
      "QUIT"  { $out.WriteLine("OK"); $out.Flush(); exit }
      default { $out.WriteLine("ERR unknown") }
    }
  } catch { $out.WriteLine("ERR " + $_.Exception.Message) }
  $out.Flush()
}
"""


class WmpPlayer:
    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._open = False
        self.length_ms = 0
        self._volume = 80

    @property
    def available(self) -> bool:
        return sys.platform == "win32"

    def _ensure(self):
        if self._proc and self._proc.poll() is None:
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1, creationflags=flags)

    def _cmd(self, line: str) -> str:
        with self._lock:
            try:
                self._ensure()
                self._proc.stdin.write(line + "\n")
                self._proc.stdin.flush()
                return (self._proc.stdout.readline() or "").strip()
            except Exception as ex:
                self._proc = None
                return f"ERR {ex}"

    # same surface as MciPlayer ------------------------------------------------------------------------------
    def load(self, path: str) -> None:
        self.close()
        r = self._cmd(f"OPEN {path}")
        if not r.startswith("OK"):
            raise RuntimeError("Cannot open the audio file (" + (r[4:] or "no answer") + ")")
        self._open = True
        self.length_ms = int(r.split()[1])
        self._cmd(f"VOL {self._volume}")

    def close(self) -> None:
        if self._open:
            self._cmd("CLOSE")
        self._open, self.length_ms = False, 0

    @property
    def loaded(self) -> bool:
        return self._open

    def play(self) -> None:
        if self._open:
            if self.position_ms() >= self.length_ms - 100:
                self._cmd("SEEK 0")
            self._cmd("PLAY")

    def pause(self) -> None:
        if self._open:
            self._cmd("PAUSE")

    def stop(self) -> None:
        if self._open:
            self._cmd("STOP")
            self._cmd("SEEK 0")

    def seek(self, ms: int, resume: bool | None = None) -> None:
        if self._open:
            self._cmd(f"SEEK {max(0, min(int(ms), max(0, self.length_ms - 1)))}")

    def position_ms(self) -> int:
        r = self._cmd("POS") if self._open else ""
        return int(r.split()[1]) if r.startswith("OK ") else 0

    def is_playing(self) -> bool:
        return self._open and self._cmd("PLAYING") == "OK 1"

    def set_volume(self, frac: float) -> None:
        self._volume = int(max(0.0, min(1.0, frac)) * 100)
        if self._open:
            self._cmd(f"VOL {self._volume}")

    def probe_length_ms(self, path: str) -> int:
        try:
            self.load(path)
            return self.length_ms
        except Exception:
            return 0
        finally:
            self.close()

    def quit(self) -> None:
        try:
            if self._proc and self._proc.poll() is None:
                self._cmd("QUIT")
                self._proc.terminate()
        except Exception:
            pass
