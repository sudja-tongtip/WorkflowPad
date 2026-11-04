"""Section types and cue phrases for Music Studio.

WorkflowPad's own catalog. It is not a whitelist and not every phrase ACE-Step 1.5 can understand.
The official tutorial is not a closed list. A short custom cue the user writes is kept.
Studio rows are guidance, not official guaranteed cues.
Section types are not vocal cues and not instrument cues. Fade Out is both a special section and a cue, depending on where it is written.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SECTION_GROUPS = (
    ("basic", "Basic"),
    ("dynamic", "Dynamic"),
    ("instrumental", "Instrumental"),
    ("studio", "Additional Studio Sections"),
    ("special", "Special"),
)

CATEGORIES = (
    ("vocal", "Vocal Delivery"),
    ("energy", "Energy / Dynamics"),
    ("mood", "Mood / Atmosphere"),
    ("instruments", "Instrument / Arrangement Hints"),
    ("timbre", "Timbre / Performance"),
    ("arrangement", "Arrangement / Special"),
)

_CONNECT = re.compile(r"\s*(?:,|&|\+|\band\b|\bwith\b)\s*", re.IGNORECASE)
_NAME_CUE = re.compile(r"\s+(?:\u2014|\u2013|-|:)\s+")
_LONG_WORD = re.compile(r"\b(becomes|underneath|almost|across|throughout|supported)\b", re.IGNORECASE)
_DEBRIS = re.compile(r"\b(just|only|almost|very|underneath|becomes|throughout|across|supported|light)\b", re.IGNORECASE)
_NO_VOCAL = re.compile(r"\bno\s+(?:vocal|vocals|singing|sung)\b", re.IGNORECASE)
_VOCAL_WORD = re.compile(r"\b(whisper|spoken|belt|falsetto|raspy|harmon|intimate|singing|sung|vocals?)\b", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _section_norm(name: str) -> str:
    n = re.sub(r"\s*\d+$", "", (name or "").strip()).lower().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", n).strip()


@dataclass(frozen=True)
class SectionType:
    id: str
    name: str
    group: str
    source: str
    aliases: tuple = ()
    description: str = ""

    @property
    def guidance(self) -> str:
        if self.source == "official_table":
            return "Official tutorial section. A request, not a guarantee."
        if self.source == "official_example":
            return "Named in an official example, not in the structure table."
        return "Studio section, not an official guaranteed tag."


@dataclass(frozen=True)
class Cue:
    id: str
    label: str
    phrase: str
    category: str
    source: str
    placement: str
    aliases: tuple = ()
    description: str = ""
    roles: tuple = ()

    @property
    def guidance(self) -> str:
        if self.source == "official_table":
            return "Official tutorial cue. A request, not a guarantee."
        if self.source == "official_example":
            return "From an official example, not a closed cue list."
        return "Studio guidance, not an official guaranteed cue."


def _section(id_: str, name: str, group: str, source: str, description: str, aliases: tuple = ()) -> SectionType:
    return SectionType(id_, name, group, source, aliases, description)


def _cue(id_: str, label: str, category: str, source: str, placement: str, description: str, aliases: tuple = (), roles: tuple = ()) -> Cue:
    return Cue("cue." + id_, label, label.lower(), category, source, placement, aliases, description, roles or (placement,))


SECTION_TYPES: tuple = (
    _section("section.intro", "Intro", "basic", "official_table", "Opening. Sets the atmosphere before the singing."),
    _section("section.verse", "Verse", "basic", "official_table", "Verse, including Verse 1 and Verse 2. The story moves forward here."),
    _section("section.pre_chorus", "Pre-Chorus", "basic", "official_table", "The lift between a verse and a chorus.", ("pre chorus", "prechorus")),
    _section("section.chorus", "Chorus", "basic", "official_table", "The main part that returns."),
    _section("section.bridge", "Bridge", "basic", "official_table", "A turn in mood, viewpoint, or sound, often before the last chorus."),
    _section("section.outro", "Outro", "basic", "official_table", "The close of the song."),
    _section("section.build", "Build", "dynamic", "official_table", "Energy rises toward what comes next."),
    _section("section.drop", "Drop", "dynamic", "official_table", "The release after a build, mostly in electronic songs."),
    _section("section.breakdown", "Breakdown", "dynamic", "official_table", "The arrangement thins out and leaves space."),
    _section("section.instrumental", "Instrumental", "instrumental", "official_table", "A part with no singing."),
    _section("section.guitar_solo", "Guitar Solo", "instrumental", "official_table", "A guitar solo, with no lyric required."),
    _section("section.piano_interlude", "Piano Interlude", "instrumental", "official_table", "A piano passage between sung parts."),
    _section("section.hook", "Hook", "studio", "studio", "A short catchy phrase that stands as its own section. The studio treats it like a chorus. Not in the official structure table."),
    _section("section.post_chorus", "Post-Chorus", "studio", "studio", "A short part after the chorus. Not in the official structure table.", ("post chorus", "postchorus")),
    _section("section.final_chorus", "Final Chorus", "studio", "studio", "The last chorus, when it should feel bigger than the earlier ones. The tutorial's full example uses the name, but it is not in the structure table."),
    _section("section.drum_break", "Drum Break", "studio", "studio", "A drum-led break. The tutorial names a drum break in passing. It is not in the structure table.", ("drumbreak",)),
    _section("section.fade_out", "Fade Out", "special", "official_table", "The ending fades. Also usable as a cue on another section, usually the outro.", ("fadeout", "fade out")),
    _section("section.silence", "Silence", "special", "official_table", "A silent part. Not a vocal cue."),
)

CUES: tuple = (
    _cue("whispered", "Whispered", "vocal", "official_table", "standalone", "A whispered vocal.", ("whisper", "whispered vocal", "whispered vocals")),
    _cue("falsetto", "Falsetto", "vocal", "official_table", "standalone", "A light high head-voice.", ("falsetto vocals",)),
    _cue("powerful_belting", "Powerful Belting", "vocal", "official_table", "standalone", "A full, powerful sung vocal.", ("belting",)),
    _cue("spoken_word", "Spoken Word", "vocal", "official_table", "standalone", "Spoken or half-spoken, not a sung melody.", ("spoken",)),
    _cue("raspy_vocal", "Raspy Vocal", "vocal", "official_table", "standalone", "A rough, textured vocal. The older studio spelling raspy vocals is the same cue.", ("raspy vocals", "raspy / gritty vocals", "raspy", "gritty vocals")),
    _cue("harmonies", "Harmonies", "vocal", "official_table", "standalone", "Layered or backing harmonies.", ("harmony",)),
    _cue("call_and_response", "Call and Response", "vocal", "official_table", "standalone", "A call answered by another voice.", ("call & response",)),
    _cue("ad_lib", "Ad-lib", "vocal", "official_table", "standalone", "A short improvised vocal.", ("ad lib", "adlib")),
    _cue("soft_vocals", "Soft Vocals", "vocal", "studio", "standalone", "A soft, gentle vocal. Studio guidance, not in the official vocal table.", ("soft vocal",), ("standalone", "modifier")),
    _cue("breathy_vocals", "Breathy Vocals", "vocal", "studio", "standalone", "An airy vocal. Studio guidance, not in the official vocal table.", ("breathy", "breathy vocal"), ("standalone", "modifier")),
    _cue("choir_vocals", "Choir Vocals", "vocal", "studio", "modifier", "A choir or group of voices. Studio guidance.", ("choir",)),
    _cue("emotional_vocals", "Emotional Vocals", "vocal", "studio", "standalone", "A vocal led by feeling. Studio guidance, not in the official vocal table.", ("emotional vocal",), ("standalone", "modifier")),
    _cue("operatic_vocals", "Operatic Vocals", "vocal", "studio", "modifier", "An opera-style vocal. Studio guidance.", ("operatic",)),
    _cue("chant", "Chant", "vocal", "studio", "modifier", "A repeated rhythmic vocal. Studio guidance."),
    _cue("high_energy", "High Energy", "energy", "official_table", "standalone", "High energy."),
    _cue("low_energy", "Low Energy", "energy", "official_table", "standalone", "Low, held-back energy."),
    _cue("building_energy", "Building Energy", "energy", "official_table", "standalone", "Energy that grows."),
    _cue("explosive", "Explosive", "energy", "official_table", "standalone", "A sudden release of energy."),
    _cue("powerful", "Powerful", "energy", "official_example", "modifier", "A powerful delivery, as in the tutorial example [Chorus - powerful]."),
    _cue("anthemic", "Anthemic", "energy", "official_example", "modifier", "A big sing-along chorus, as in the tutorial example [Chorus - anthemic]."),
    _cue("soft", "Soft", "energy", "official_example", "modifier", "A soft dynamic. Named in the tutorial's caption writing, not in the lyrics cue table."),
    _cue("punchy", "Punchy", "energy", "official_example", "modifier", "A punchy accent. Named as a caption texture, not in the lyrics cue table."),
    _cue("energetic", "Energetic", "energy", "official_example", "modifier", "An energetic feel. Named as a caption emotion, not in the lyrics cue table."),
    _cue("melancholic", "Melancholic", "mood", "official_table", "standalone", "A melancholic mood."),
    _cue("euphoric", "Euphoric", "mood", "official_table", "standalone", "A euphoric mood."),
    _cue("dreamy", "Dreamy", "mood", "official_table", "standalone", "A dreamy mood."),
    _cue("aggressive", "Aggressive", "mood", "official_table", "standalone", "An aggressive mood."),
    _cue("ambient", "Ambient", "mood", "studio", "modifier", "An ambient atmosphere. Studio guidance."),
    _cue("atmospheric", "Atmospheric", "mood", "studio", "modifier", "A wide atmosphere. Studio guidance."),
    _cue("piano", "Piano", "instruments", "official_example", "modifier", "Piano, as in the tutorial example [Intro - piano]."),
    _cue("soft_piano", "Soft Piano", "instruments", "studio", "modifier", "A quiet piano. Studio guidance."),
    _cue("guitar", "Guitar", "instruments", "studio", "modifier", "Guitar. Studio guidance."),
    _cue("acoustic_guitar", "Acoustic Guitar", "instruments", "studio", "modifier", "Acoustic guitar. Studio guidance."),
    _cue("drums", "Drums", "instruments", "studio", "modifier", "Drums. Studio guidance."),
    _cue("heavy_drums", "Heavy Drums", "instruments", "studio", "modifier", "Heavy drums. Studio guidance."),
    _cue("rising_drums", "Rising Drums", "instruments", "studio", "modifier", "Drums that build. Studio guidance."),
    _cue("bass", "Bass", "instruments", "studio", "modifier", "Bass. Studio guidance."),
    _cue("heavy_bass", "Heavy Bass", "instruments", "studio", "modifier", "Heavy bass. Studio guidance."),
    _cue("strings", "Strings", "instruments", "studio", "modifier", "Strings. Studio guidance."),
    _cue("synth", "Synth", "instruments", "studio", "modifier", "Synth. Studio guidance."),
    _cue("synth_riser", "Synth Riser", "instruments", "studio", "modifier", "A rising synth. Studio guidance."),
    _cue("clean", "Clean", "timbre", "studio", "modifier", "A clean tone. Studio guidance. Not an instrument name."),
    _cue("distorted", "Distorted", "timbre", "official_example", "modifier", "Distortion. The tutorial uses the word in a lyric-tag example."),
    _cue("acoustic", "Acoustic", "timbre", "studio", "modifier", "An acoustic tone. Studio guidance. Not an instrument name."),
    _cue("melodic", "Melodic", "timbre", "studio", "modifier", "A melodic performance. Studio guidance."),
    _cue("expressive", "Expressive", "timbre", "official_example", "modifier", "An expressive performance, as in the tutorial example [Violin Solo - expressive]."),
    _cue("fade_out", "Fade Out", "arrangement", "official_table", "modifier", "The part fades away. Also a special section when it stands alone.", ("fade-out", "fadeout"), ("section", "standalone", "modifier")),
    _cue("minimal", "Minimal", "arrangement", "studio", "modifier", "A stripped arrangement. Studio guidance."),
    _cue("instrumental_hook", "Instrumental Hook", "arrangement", "studio", "modifier", "A hook played by instruments. Studio guidance."),
)

BY_PHRASE: dict = {}
for _c in CUES:
    BY_PHRASE.setdefault(_c.phrase, _c)
    for _alias in _c.aliases:
        BY_PHRASE.setdefault(_norm(_alias), _c)

_SECTION_BY_NORM = {}
for _s in SECTION_TYPES:
    _SECTION_BY_NORM.setdefault(_section_norm(_s.name), _s.name)
    for _alias in _s.aliases:
        _SECTION_BY_NORM.setdefault(_section_norm(_alias), _s.name)

WITH_CUES = {"harmonies", "ad-lib", "chant", "instrumental hook"}
STANDALONE = set(BY_PHRASE) | {"silence", "hold", "pause"}
# Line vocals stand on their own line immediately before one lyric. The first eight are the tutorial vocal table.
# The last three are studio guidance and are not in that table. Energy and mood words are not line vocals.
LINE_VOCALS = frozenset({
    "whispered", "raspy vocal", "falsetto", "powerful belting", "spoken word", "harmonies", "call and response", "ad-lib",
    "breathy vocals", "soft vocals", "emotional vocals",
})

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
    "Piano Interlude": ["Soft Piano", "Piano", "Dreamy"],
    "Drum Break": ["Punchy", "Heavy Drums", "Energetic"],
    "Build": ["Building Energy", "Rising Drums", "Synth Riser"],
    "Drop": ["Explosive", "Heavy Bass", "High Energy"],
    "Breakdown": ["Minimal", "Ambient", "Soft Piano", "Low Energy"],
    "Fade Out": ["Soft", "Piano", "Dreamy"],
    "Silence": [],
}


def section_key(name: str) -> "str | None":
    """'Verse 2' -> 'Verse', 'pre chorus' -> 'Pre-Chorus'. None when the name is not a section type."""
    key = _section_norm(name)
    return _SECTION_BY_NORM.get(key) if key else None


def section_type(name: str) -> "SectionType | None":
    key = section_key(name)
    if not key:
        return None
    return next(s for s in SECTION_TYPES if s.name == key)


def find_phrase(text: str) -> "Cue | None":
    return BY_PHRASE.get(_norm(text))


def line_vocal(text: str) -> "Cue | None":
    """A vocal cue that may stand on its own line before one lyric. None for energy, mood, instruments, and other studio vocals."""
    cue = find_phrase(text)
    if cue and cue.phrase in LINE_VOCALS:
        return cue
    return None


def recommended(name: str) -> list:
    key = section_key(name)
    if not key:
        return []
    out = []
    for label in RECOMMENDED.get(key, []):
        cue = find_phrase(label)
        if cue and cue not in out:
            out.append(cue)
    return out


def cue_parts(cue_text: str) -> list:
    return [p.strip() for p in _CONNECT.split(cue_text or "") if p.strip()]


def is_short_custom(text: str) -> bool:
    """A short phrase the library does not know. ACE-Step can still read it. A sentence is not one."""
    s = _norm(text)
    if not s or find_phrase(s) or section_key(s):
        return False
    if len(s) > 40 or len(s.split()) > 4:
        return False
    if _LONG_WORD.search(s) or re.search(r"[.;:!?/]", s):
        return False
    return True


def _split_tag(inner: str) -> tuple:
    m = _NAME_CUE.search(inner or "")
    if not m:
        return (inner or "").strip(), ""
    return inner[:m.start()].strip(), inner[m.end():].strip()


def _has_vocal(text: str) -> bool:
    low = _NO_VOCAL.sub(" ", text or "")
    if _VOCAL_WORD.search(low):
        return True
    return any(c.category == "vocal" for c in mentioned_cues(low))


def _pseudo_section(name: str) -> bool:
    if section_key(name):
        return False
    low = _norm(name)
    return "/" in (name or "") or "post-verse" in low or "post verse" in low


def stray_fragment(text: str) -> bool:
    """A scrap of a sentence, not a cue to keep: 'just bass', 'only', 'no singing'."""
    return bool(_DEBRIS.search(text or "") or _NO_VOCAL.search(text or ""))


def mentioned_cues(text: str) -> list:
    """Catalog cues named in text, earliest first. A longer name hides a shorter one inside the same words. Aliases count."""
    low = _norm(text)
    hits = []
    for cue in CUES:
        names = (cue.phrase,) + tuple(_norm(a) for a in cue.aliases)
        for name in names:
            if not name:
                continue
            for m in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", low):
                hits.append((m.start(), m.end(), cue))
    hits.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    out, end_at = [], -1
    for start, end, cue in hits:
        if start < end_at:
            continue
        end_at = end
        if cue not in out:
            out.append(cue)
    return out


def cue_is_long(text: str) -> bool:
    s = _norm(text)
    if not s or find_phrase(s):
        return False
    return len(s) > 80 or len(s.split()) > 4 or bool(_LONG_WORD.search(s))


def one_cue(text: str) -> "Cue | None":
    hits = mentioned_cues(text)
    if not hits:
        return None
    if _has_vocal(text):
        vocal = [c for c in hits if c.category == "vocal"]
        if vocal:
            return vocal[0]
    return hits[0]


def _section_for_pseudo(name: str, cue_text: str) -> str:
    blob = f"{name} {cue_text}"
    low = _norm(blob)
    if not _has_vocal(blob):
        if "piano" in low:
            return "Piano Interlude"
        if "guitar" in low and "solo" in low:
            return "Guitar Solo"
        if "drum" in low and "break" in low:
            return "Drum Break"
        return "Instrumental"
    return "Bridge"


def _caption_for(name: str, cue_text: str, section: str, cue: "Cue | None") -> str:
    blob = f"{name} {cue_text}"
    low = _norm(blob)
    if section == "Bridge" and cue and cue.phrase == "spoken word" and "bass" in low and "pad" in low and ("intimate" in low or "spoken" in low):
        return "An intimate, almost spoken vocal passage after the verse, supported by bass and a soft ambient pad."
    prose = cue_text.strip()
    if cue_is_long(prose):
        return prose[0].upper() + prose[1:] if prose and prose[0].islower() else prose
    return ""


def _join_phrases(phrases: list) -> str:
    items = [p for p in phrases if p]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    glue = " with " if items[-1].lower() in WITH_CUES else " and "
    return ", ".join(items[:-1]) + glue + items[-1]


@dataclass(frozen=True)
class TagRepair:
    before: str
    after: str
    caption: str
    reason: str
    changed: bool
    issues: tuple = ()


def _same(before: str, after: str, caption: str = "", reason: str = "", issues: tuple = ()) -> TagRepair:
    return TagRepair(before, after, caption, reason, before != after, issues)


def assess_tag(tag: str) -> TagRepair:
    """A proposal for a shorter, better-sorted tag. The lyrics are not changed here.

    A known section keeps its name, including Verse 2. A short custom cue stays.
    A sentence, a repeated cue, or a section name sitting in the cue slot is repaired.
    A non-vocal interlude is an instrumental section, not a Bridge.
    """
    raw = (tag or "").strip()
    if not (raw.startswith("[") and raw.endswith("]") and raw.count("[") == 1 and "\n" not in raw):
        return TagRepair(raw, raw, "", "", False, ())
    inner = raw[1:-1].strip()
    if not inner:
        return TagRepair(raw, raw, "", "", False, ())
    name, cue_text = _split_tag(inner)
    if not cue_text and (section_key(name) or find_phrase(inner) or _norm(inner) in STANDALONE):
        return TagRepair(raw, raw, "", "", False, ())
    section = section_key(name)
    issues = []
    parts = cue_parts(cue_text)
    known, custom, dropped = [], [], []
    whole_long = cue_is_long(cue_text)
    long = whole_long
    drop_soft = whole_long or _pseudo_section(name)
    for part in parts:
        cue = find_phrase(part)
        if cue:
            if cue not in known:
                known.append(cue)
            elif "redundant" not in issues:
                issues.append("redundant")
            continue
        if section_key(part) and not find_phrase(part):
            dropped.append(part)
            if "miscategorized" not in issues:
                issues.append("miscategorized")
            continue
        if drop_soft and stray_fragment(part):
            continue
        if is_short_custom(part):
            custom.append(part.strip())
            continue
        long = True
    if long and "long" not in issues:
        issues.append("long")
    new_name = name
    if section:
        new_name = name.strip()
    elif _pseudo_section(name):
        new_name = _section_for_pseudo(name, cue_text)
        issues.append("unknown_section")
        if new_name == "Bridge" and not _has_vocal(f"{name} {cue_text}"):
            new_name = "Instrumental"
    elif not section and not find_phrase(name):
        new_name = name.strip()
    phrases = []
    chosen = None
    if "long" in issues or "unknown_section" in issues:
        pool = f"{name} {cue_text}"
        chosen = one_cue(pool)
        if chosen and section_key(new_name) == "Piano Interlude" and chosen.phrase in ("piano", "soft piano"):
            chosen = None
        if chosen and section_key(new_name) == "Guitar Solo" and chosen.phrase == "guitar":
            chosen = None
        if chosen:
            phrases.append(chosen.phrase)
        phrases.extend(custom)
    else:
        phrases.extend(c.phrase for c in known)
        phrases.extend(custom)
    if not issues:
        return TagRepair(raw, raw, "", "", False, ())
    if "miscategorized" in issues and not phrases and section:
        new_name = name.strip()
    cue_out = _join_phrases(phrases)
    after = f"[{new_name} - {cue_out}]" if cue_out else f"[{new_name}]"
    if after == raw:
        return TagRepair(raw, raw, "", "", False, tuple(issues))
    caption = _caption_for(name, cue_text, section_key(new_name) or new_name, chosen) if ("long" in issues or "unknown_section" in issues) else ""
    reason = _reason(issues, name, new_name, phrases, dropped, custom)
    return TagRepair(raw, after, caption, reason, True, tuple(issues))


def _reason(issues: list, name: str, new_name: str, phrases: list, dropped: list, custom: list) -> str:
    bits = []
    if "unknown_section" in issues and new_name != name:
        if new_name == "Bridge":
            bits.append(f"{name} is not a section. The passage is sung, so Bridge is the section.")
        elif new_name in ("Instrumental", "Piano Interlude", "Guitar Solo", "Drum Break"):
            bits.append(f"{name} is not a section. There is no vocal, so this stays an instrumental section ({new_name}), not a Bridge.")
        else:
            bits.append(f"{name} is not a section type. The proposed section is {new_name}.")
    if "long" in issues:
        shown = phrases[0] if phrases else "no cue"
        bits.append(f"The cue is a sentence. The tag keeps one short cue ({shown}). The rest belongs in the caption.")
    if "redundant" in issues:
        bits.append("The same cue was written more than once. One of them remains.")
    if "miscategorized" in issues:
        bits.append("A section name is not a vocal or instrument cue" + (f" ({', '.join(dropped)})" if dropped else "") + ".")
    if custom:
        bits.append("The custom cue " + ", ".join(custom) + " stays.")
    if not bits:
        bits.append("The tag can be shorter.")
    bits.append("Nothing is applied until you accept it. This catalog is not every phrase ACE-Step can read.")
    return " ".join(bits)


def custom_fragments(cue_text: str) -> str:
    """Short custom phrases inside a cue slot. A sentence, and scraps such as 'just bass', are not kept as a cue."""
    return ", ".join(part.strip() for part in cue_parts(cue_text) if is_short_custom(part) and not stray_fragment(part))


@dataclass(frozen=True)
class LayoutFix:
    """A placement proposal. `after` is empty when the destination is not a single clear line. Nothing here edits the lyrics."""
    before: str
    after: str
    reason: str
    first: int
    last: int

    @property
    def advice(self) -> bool:
        return not self.after


_TAG_BITS = re.compile(r"\[[^\[\]\n]*\]")


def _whole_tag(line: str) -> str:
    s = (line or "").strip()
    if s.startswith("[") and s.endswith("]") and s.count("[") == 1 and "\n" not in s:
        return s[1:-1].strip()
    return ""


def _sung_line(line: str) -> bool:
    return bool((line or "").strip()) and not _whole_tag(line)


def _next_filled(lines: list, start: int, step: int) -> "int | None":
    i = start
    while 0 <= i < len(lines):
        if lines[i].strip():
            return i
        i += step
    return None


def _span(lines: list, a: int, b: int) -> str:
    return "\n".join(lines[a:b + 1])


def _vocal_spans(line: str) -> list:
    out = []
    for m in _TAG_BITS.finditer(line or ""):
        cue = line_vocal(m.group(0)[1:-1])
        if cue:
            out.append((m.start(), m.end(), cue))
    return out


def _excise_vocals(line: str, spans: list) -> str:
    """The line with those vocal brackets removed. A space that only separated an edge bracket is dropped. Letters stay."""
    text, pos = [], 0
    for a, b, _cue in spans:
        text.append(line[pos:a])
        pos = b
    text.append(line[pos:])
    out = "".join(text)
    if spans and spans[0][0] == 0:
        out = out.lstrip(" \t")
    if spans and spans[-1][1] == len(line):
        out = out.rstrip(" \t")
    return out


def _header_vocals(line: str) -> list:
    inner = _whole_tag(line)
    if not inner or " - " not in inner:
        return []
    _name, cue_text = _split_tag(inner)
    out = []
    for part in cue_parts(cue_text):
        cue = line_vocal(part)
        if cue and cue not in out:
            out.append(cue)
    return out


def _hold_only(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\[Hold\]\s*", line or "", re.IGNORECASE))


def _has_hold(line: str) -> bool:
    return bool(re.search(r"\[Hold\]", line or "", re.IGNORECASE))


def assess_vocal_layout(text: str) -> list:
    """Placement proposals for line vocal cues and a [Hold] that left its lyric.

    The returned list is a proposal. This function does not change `text`.
    `after` is empty when more than one destination is possible: nothing is chosen for the user.
    A cue that already stands on the line before a lyric is left alone.
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    fixes: list = []
    used: set = set()

    def add(before: str, after: str, reason: str, first: int, last: int) -> None:
        if not before.strip():
            return
        fixes.append(LayoutFix(before, after, reason, first, last))

    for i, line in enumerate(lines):
        if not _sung_line(line):
            continue
        spans = _vocal_spans(line)
        if not spans:
            continue
        phrases = []
        for _a, _b, cue in spans:
            if cue.phrase not in phrases:
                phrases.append(cue.phrase)
        if len(phrases) > 1:
            add(line, "", "These vocal cues sit on the lyric and do not agree. No cue was chosen.", i + 1, i + 1)
        else:
            lyric = _excise_vocals(line, spans)
            if not lyric.strip():
                continue
            add(line, f"[{phrases[0]}]\n{lyric}",
                f"[{phrases[0]}] belongs on its own line immediately before this lyric. The words are unchanged. Nothing is applied until you accept it.",
                i + 1, i + 1)
        used.add(i)

    for i, line in enumerate(lines):
        if i in used or not _hold_only(line):
            continue
        prev_i = _next_filled(lines, i - 1, -1)
        next_i = _next_filled(lines, i + 1, 1)
        prev_sung = prev_i is not None and _sung_line(lines[prev_i])
        next_sung = next_i is not None and _sung_line(lines[next_i])
        if prev_sung and next_sung:
            a, b = min(prev_i, next_i), max(prev_i, next_i)
            add(_span(lines, a, b), "", "[Hold] is on its own line between two lyrics. It was not moved, because either line could be the one.", prev_i + 1, next_i + 1)
        elif prev_sung or next_sung:
            target = prev_i if prev_sung else next_i
            lyric = lines[target]
            if _has_hold(lyric):
                a, b = min(i, target), max(i, target)
                add(_span(lines, a, b), "", "[Hold] is on its own line, and the lyric already has [Hold]. It was not moved.", a + 1, b + 1)
            else:
                a, b = min(i, target), max(i, target)
                add(_span(lines, a, b), lyric.rstrip() + "[Hold]",
                    "[Hold] belongs after the lyric, on that same line. The words are unchanged. Nothing is applied until you accept it.",
                    a + 1, b + 1)
        else:
            add(line, "", "[Hold] is on its own line and no lyric sits next to it. It was not moved.", i + 1, i + 1)
        used.add(i)

    i = 0
    while i < len(lines):
        inner = _whole_tag(lines[i])
        cue = line_vocal(inner) if inner and " - " not in inner else None
        if i in used or not cue:
            i += 1
            continue
        run = [i]
        phrases = [cue.phrase]
        j = i
        while True:
            nxt = _next_filled(lines, j + 1, 1)
            if nxt is None:
                break
            nxt_inner = _whole_tag(lines[nxt])
            nxt_cue = line_vocal(nxt_inner) if nxt_inner and " - " not in nxt_inner else None
            if not nxt_cue:
                break
            run.append(nxt)
            if nxt_cue.phrase not in phrases:
                phrases.append(nxt_cue.phrase)
            j = nxt
        nxt = _next_filled(lines, run[-1] + 1, 1)
        nxt_sung = nxt is not None and _sung_line(lines[nxt])
        prev = _next_filled(lines, run[0] - 1, -1)
        header_same = False
        if nxt_sung and prev is not None and len(phrases) == 1:
            head = _whole_tag(lines[prev])
            if head and " - " in head and _next_filled(lines, prev + 1, 1) == run[0]:
                header_same = any(c.phrase == phrases[0] for c in _header_vocals(lines[prev]))
        if nxt_sung and len(phrases) > 1:
            add(_span(lines, run[0], run[-1]), "",
                "These vocal cues are stacked before one lyric and do not agree. No cue was chosen.",
                run[0] + 1, run[-1] + 1)
        elif header_same:
            add(_span(lines, prev, run[-1]), lines[prev],
                f"[{phrases[0]}] is already the section cue, so the extra line is redundant. Nothing is applied until you accept it.",
                prev + 1, run[-1] + 1)
        elif nxt_sung and len(run) > 1:
            add(_span(lines, run[0], run[-1]), f"[{phrases[0]}]",
                "The same vocal cue is repeated. One of them remains. Nothing is applied until you accept it.",
                run[0] + 1, run[-1] + 1)
        elif not nxt_sung:
            if len(phrases) > 1 or (prev is None or not _sung_line(lines[prev]) if prev is not None else True):
                add(_span(lines, run[0], run[-1]), "",
                    "This vocal cue has no lyric after it, and the line it belongs to is not clear. It was not moved.",
                    run[0] + 1, run[-1] + 1)
            else:
                before_prev = _next_filled(lines, prev - 1, -1)
                bare = _whole_tag(lines[before_prev]) if before_prev is not None else ""
                blocked = prev in used or (before_prev is not None and bool(line_vocal(bare if " - " not in bare else "")))
                if blocked:
                    add(_span(lines, prev, run[-1]), "",
                        "This vocal cue has no lyric after it. A vocal cue is already before the previous line, so it was not stacked.",
                        prev + 1, run[-1] + 1)
                else:
                    add(_span(lines, prev, run[-1]), f"[{phrases[0]}]\n{lines[prev]}",
                        "This vocal cue sits after the lyric, with no lyric following it. The proposal puts it on the line before. The words are unchanged. Nothing is applied until you accept it.",
                        prev + 1, run[-1] + 1)
        for n in run:
            used.add(n)
        i = run[-1] + 1

    for i, line in enumerate(lines):
        if i in used:
            continue
        inner = _whole_tag(line)
        if not inner or " - " in inner or line_vocal(inner) or section_key(inner):
            continue
        if not cue_is_long(inner) or not _has_vocal(inner):
            continue
        vocals = []
        for cue in mentioned_cues(inner):
            if cue.phrase in LINE_VOCALS and cue not in vocals:
                vocals.append(cue)
        nxt = _next_filled(lines, i + 1, 1)
        if len(vocals) > 1:
            add(line, "", "This bracket is a sentence and names more than one vocal cue. No cue was chosen, because it may be sung as written.", i + 1, i + 1)
        elif len(vocals) == 1 and nxt is not None and _sung_line(lines[nxt]):
            add(line, f"[{vocals[0].phrase}]",
                "This bracket is a sentence and may be sung. The proposal keeps one short vocal cue. Nothing is applied until you accept it.",
                i + 1, i + 1)
        else:
            shown = f" A short cue would be [{vocals[0].phrase}]." if vocals else ""
            add(line, "", "This bracket is a sentence and may be sung. It was not rewritten, because the line it belongs to is not clear." + shown, i + 1, i + 1)
    return fixes


def apply_layout(text: str, fix: LayoutFix) -> str:
    """The lyrics with this one proposal applied. Raises ValueError when there is no single exact span. Does not guess a second copy."""
    if not fix.after:
        raise ValueError("this note has no single repair")
    src = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    needle = fix.before
    if not needle or src.count(needle) != 1:
        raise ValueError("the lines are not a single exact span")
    return src.replace(needle, fix.after, 1)


def repair_notes(text: str) -> str:
    """Lines a producer can mention. Empty when every tag is fine. This does not edit the lyrics."""
    rows = []
    for i, line in enumerate((text or "").split("\n"), 1):
        raw = line.strip()
        if not (raw.startswith("[") and raw.endswith("]") and raw.count("[") == 1):
            continue
        fix = assess_tag(raw)
        if not fix.changed:
            continue
        extra = f" Caption: {fix.caption}" if fix.caption else ""
        rows.append(f"L{i:03d} {fix.before} -> {fix.after}{extra} ({fix.reason})")
    for fix in assess_vocal_layout(text):
        shown = fix.before.replace("\n", " / ")
        if fix.after:
            rows.append(f"L{fix.first:03d} {shown} -> {fix.after.replace(chr(10), ' / ')} ({fix.reason})")
        else:
            rows.append(f"L{fix.first:03d} {shown} ({fix.reason})")
    if not rows:
        return ""
    return ("TAG NOTES (the program noticed these tags. Do not emit a tag unless the user asked for a change. "
            "If they did, propose the shorter tag, not the long one. A vocal-cue note is a placement proposal: "
            "when it has no replacement, do not move the cue. Do not rewrite a sung line or a short custom cue.):\n"
            + "\n".join(rows))


def inventory() -> dict:
    """Counts for an audit. Not a claim that the list is complete."""
    by_cat = {}
    by_source = {}
    for cue in CUES:
        by_cat[cue.category] = by_cat.get(cue.category, 0) + 1
        by_source[cue.source] = by_source.get(cue.source, 0) + 1
    return {
        "cues": len(CUES),
        "sections": len(SECTION_TYPES),
        "by_category": by_cat,
        "by_source": by_source,
        "section_names": [s.name for s in SECTION_TYPES],
    }
