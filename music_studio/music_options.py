"""The values the Style / Output controls offer, in ONE place, so anything that proposes a value (the AI template arrangement) is checked against exactly what the UI accepts.
No UI here."""
from __future__ import annotations

KEYS = [f"{n} {m}" for n in ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B") for m in ("major", "minor")]
GENRES = ["Alternative Rock", "Pop", "Rock", "Pop Rock", "Indie Pop", "Indie Rock", "Acoustic", "Ballad", "Electronic", "EDM", "Hip Hop", "R&B", "Folk", "Jazz", "Metal",
          "Lo-fi", "Country", "Funk", "Soul", "Reggae", "Classical"]
MOODS = ["", "Melancholic", "Happy", "Energetic", "Dreamy", "Romantic", "Dark", "Calm", "Nostalgic", "Epic", "Angry", "Hopeful"]
VOCALS = [("", "Auto"), ("male vocal", "Male"), ("female vocal", "Female"), ("duet vocals", "Duet"), ("choir", "Choir")]
ENERGIES = [("", "Auto"), ("low energy", "Low"), ("medium energy", "Medium"), ("high energy", "High")]
TIMESIGS = [("2", "2/4"), ("3", "3/4"), ("4", "4/4"), ("6", "6/8")]      # what ACE-Step's `timesignature` accepts
BPM_RANGE = (30, 300)                                                    # the BPM field's own limits
DURATION_RANGE = (10, 600)                                               # the Duration field's own limits (seconds)
