"""Data for the Section Options dialog: musical cues, their categories and what is recommended for which section.

These are WorkflowPad's suggestions, not a whitelist and not commands that ACE-Step 1.5 is guaranteed to follow. The user can always type any other cue.
Adding an option later means adding one line to CUES (and, if wanted, to RECOMMENDED); the dialog is built from this data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CATEGORIES = [("instruments", "Instruments"), ("vocal", "Vocal Delivery"), ("energy", "Energy"), ("mood", "Mood / Atmosphere"), ("arrangement", "Arrangement / Dynamics")]


@dataclass(frozen=True)
class Cue:
    label: str
    category: str

    @property
    def phrase(self) -> str:
        """The wording written into the tag."""
        return self.label.lower()


def _c(category: str, *labels: str) -> list:
    return [Cue(lb, category) for lb in labels]


CUES: list = (
    _c("instruments", "Piano", "Soft Piano", "Guitar", "Acoustic Guitar", "Drums", "Heavy Drums", "Rising Drums", "Bass", "Heavy Bass", "Strings", "Synth", "Synth Riser",
       "Clean", "Distorted", "Acoustic", "Melodic", "Expressive")
    + _c("vocal", "Whispered", "Falsetto", "Powerful Belting", "Spoken Word", "Soft Vocals", "Raspy Vocals", "Breathy Vocals", "Choir Vocals", "Emotional Vocals", "Operatic Vocals",
         "Harmonies", "Call and Response", "Ad-lib", "Chant")      # the same wording the Vocal Delivery tools write (Hold Note is a [Hold] line, not a section cue)
    + _c("energy", "Building Energy", "Powerful", "Explosive", "Soft", "Low Energy", "High Energy", "Anthemic", "Punchy", "Energetic")
    + _c("mood", "Dreamy", "Ambient", "Atmospheric", "Melancholic", "Euphoric", "Aggressive")      # (the tutorial's energy / emotion tags)
    + _c("arrangement", "Fade Out", "Minimal", "Instrumental Hook")
)
BY_PHRASE = {c.phrase: c for c in CUES}

# Cues that read naturally as "<something> with <cue>" when they are added to others.
WITH_CUES = {"harmonies", "ad-lib", "chant", "instrumental hook"}

# Whole tags that are a cue on their own (not a section): [whispered], [Fade Out], [Silence] ...
STANDALONE = set(BY_PHRASE) | {"silence", "hold", "pause", "powerful belting", "whisper", "ad lib"}

RECOMMENDED = {
    "Intro": ["Piano", "Guitar", "Drums", "Strings", "Ambient", "Dreamy", "Building Energy"],
    "Verse": ["Whispered", "Falsetto", "Spoken Word", "Soft Vocals", "Breathy Vocals", "Melancholic", "Low Energy"],
    "Pre-Chorus": ["Building Energy", "Rising Drums", "Harmonies", "Emotional Vocals", "Powerful"],
    "Chorus": ["Anthemic", "Powerful", "Explosive", "Powerful Belting", "Emotional Vocals", "Harmonies", "Chant", "Choir Vocals"],
    "Hook": ["Chant", "Ad-lib", "Harmonies", "Powerful", "Anthemic"],
    "Post-Chorus": ["Chant", "Ad-lib", "Harmonies", "Choir Vocals", "Instrumental Hook"],
    "Bridge": ["Dreamy", "Whispered", "Soft Vocals", "Emotional Vocals", "Piano", "Melancholic", "Powerful"],
    "Final Chorus": ["Anthemic", "Powerful", "Explosive", "Powerful Belting", "Emotional Vocals", "Harmonies", "Choir Vocals"],
    "Outro": ["Fade Out", "Piano", "Acoustic Guitar", "Soft", "Soft Vocals", "Breathy Vocals", "Dreamy"],
    "Instrumental": ["Piano", "Guitar", "Drums", "Bass", "Strings", "Synth"],
    "Guitar Solo": ["Clean", "Distorted", "Acoustic", "Melodic", "Expressive"],
    "Drum Break": ["Punchy", "Heavy Drums", "Energetic"],
    "Build": ["Building Energy", "Rising Drums", "Synth Riser"],
    "Drop": ["Explosive", "Heavy Bass", "High Energy"],
    "Breakdown": ["Minimal", "Ambient", "Soft Piano", "Low Energy"],
}

_SECTION_ALIASES = {k.lower(): k for k in RECOMMENDED}
_SECTION_ALIASES.update({"pre chorus": "Pre-Chorus", "post chorus": "Post-Chorus", "prechorus": "Pre-Chorus", "postchorus": "Post-Chorus"})


def section_key(name: str) -> str | None:
    """'Verse 2' -> 'Verse', 'pre chorus' -> 'Pre-Chorus'; None when the name is not a known section."""
    n = re.sub(r"\s*\d+$", "", (name or "").strip()).lower().replace("_", " ")
    return _SECTION_ALIASES.get(re.sub(r"\s+", " ", n))


def recommended(name: str) -> list:
    key = section_key(name)
    return [BY_PHRASE[lb.lower()] for lb in RECOMMENDED.get(key, [])] if key else []


def find_phrase(text: str):
    return BY_PHRASE.get(re.sub(r"\s+", " ", (text or "").strip().lower()))
