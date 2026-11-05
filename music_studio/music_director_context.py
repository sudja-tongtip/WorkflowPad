"""Facts the Music Director can use while writing a caption.

Built only from the snapshot of one request (its lyrics and the settings on that snapshot). Nothing here reads another song, another sheet, or the open editor.
The text is a section summary plus the risks that stood out. It does not repeat every lyric line.
Python counts and estimates. It does not choose the genre, the groove, or the vocal color.
"""
from __future__ import annotations

import re

from . import live_insights as LI

CAPTION_CRAFT = """CAPTION CRAFT
Write the caption as one short English paragraph of natural sentences. Describe how the finished record sounds. Do not write a keyword list, lyrics, a song title, BPM, a key, or a time signature. Do not say the model must obey a section, and do not promise a result.

Cover these, briefly, and stop:
1. Sound identity: one genre home and one mood, not a pile of styles.
2. Instrument roles: a few instruments, and the job each one does.
3. Groove and rhythm: how the drums and bass move. No tempo number.
4. Vocal direction: tone, diction, and emotional control. Do not use belting, soaring, falsetto, melisma, or a high register unless the user asked.
Thai lexical tone is part of the word, not a musical pitch. When the lyrics are Thai, ask for clear native diction, conversational melodic phrasing, controlled pitch movement, and a comfortable register, and let the arrangement carry the lift. A powerful vocal is still available when the user or that direction asks for it. Do not force every Thai song to be quiet or low.
5. Arrangement arc: how the energy moves across the sections in the blueprint, including a chorus that returns when the blueprint has one.
6. Production texture: space, saturation, layering, and where the mix stays open.

Shape examples (illustrations of the writing, not songs to copy):
Ambient pop: A hushed ambient-pop ballad where a soft synth pad holds the verse, a rounded bass pulse enters under the chorus, and the vocal stays close and unforced, with a wider shimmer only on the final chorus.
Acoustic ballad: An intimate acoustic ballad led by fingerpicked guitar and a quiet upright bass, the vocal breathy and conversational, opening into a warmer chorus without raising the register.
Alternative rock: Alternative rock that keeps the verse on clean guitar and a dry kit, then brings a driven guitar and a fuller backbeat into the chorus, the vocal urgent but still in a comfortable range.
R&B: A slow R&B groove with muted electric piano, a soft kick and a syncopated bass, low stacked harmonies, and a lead vocal that leans into the words instead of belting.
Synth-pop: Bright synth-pop with a bouncing bass arpeggio, tight electronic hats, a clear lead vocal, and a chorus that stacks a wide pad under the melody the verse sang quietly.
Thai vocal shape (when the lyrics are Thai, and only as a shape, not a rule that every genre stays soft): Natural Thai lead vocals with clear native diction, conversational melodic phrasing, controlled pitch movement and a comfortable vocal register. Preserve Thai word intelligibility while the instrumental arrangement provides the emotional lift."""

_SIG = {"2": "2/4", "3": "3/4", "4": "4/4", "6": "6/8"}
_VOCAL_LOW = {"soft", "whispered", "soft vocals", "breathy vocals", "minimal", "low energy"}
_HIGH = re.compile(r"\b(?:soaring(?:\s+vocals?)?|powerful\s+belting|belting|explosive\s+high(?:\s+notes?)?|falsetto|operatic)\b", re.IGNORECASE)
_SPARSE = re.compile(r"\b(?:spacious|lots of space|drawn-out|long held notes|wide open vocal)\b", re.IGNORECASE)
_INSTRUMENT_PHRASES = (
    ("electric guitar", "guitar"), ("acoustic guitar", "guitar"), ("nylon-string guitar", "guitar"), ("bass guitar", "bass"),
    ("electric piano", "piano"), ("rhodes", "piano"), ("piano", "piano"),
    ("synth pad", "synth"), ("synthesizer", "synth"), ("synth", "synth"), ("pad", "synth"),
    ("upright bass", "bass"), ("bass", "bass"),
    ("hi-hat", "drums"), ("kick drum", "drums"), ("kick", "drums"), ("snare", "drums"), ("drums", "drums"), ("drum", "drums"), ("percussion", "drums"), ("808", "drums"),
    ("strings", "strings"), ("violin", "strings"), ("cello", "strings"),
    ("saxophone", "horns"), ("brass", "horns"), ("trumpet", "horns"), ("organ", "organ"), ("harp", "harp"), ("flute", "flute"),
)
_GENRE_PHRASES = (
    ("ambient pop", "ambient pop"), ("synth-pop", "synth-pop"), ("synthpop", "synth-pop"),
    ("alternative rock", "alternative rock"), ("hip-hop", "hip-hop"), ("hip hop", "hip-hop"),
    ("acoustic ballad", "acoustic ballad"), ("r&b", "r&b"), ("rnb", "r&b"), ("lo-fi", "lo-fi"), ("lofi", "lo-fi"),
    ("ambient", "ambient"), ("acoustic", "acoustic"), ("ballad", "ballad"), ("pop", "pop"), ("rock", "rock"),
    ("jazz", "jazz"), ("folk", "folk"), ("metal", "metal"), ("soul", "soul"), ("funk", "funk"),
    ("electronic", "electronic"), ("indie", "indie"), ("dance", "dance"), ("house", "house"),
    ("techno", "techno"), ("reggae", "reggae"), ("blues", "blues"), ("country", "country"), ("punk", "punk"), ("disco", "disco"),
)
_MAX_INSTRUMENTS = 6
_MAX_GENRES = 3


def _field(snapshot, name: str, default=""):
    """A value stored on a Basic snapshot or, for AI Recommend, inside its settings pairs."""
    setting = getattr(snapshot, "setting", None)
    if callable(setting):
        value = setting(name, default)
        if value not in (None, ""):
            return value
    if hasattr(snapshot, name):
        value = getattr(snapshot, name)
        if value not in (None, ""):
            return value
    return default


def _number(snapshot, name: str):
    try:
        return float(_field(snapshot, name, ""))
    except (TypeError, ValueError):
        return None


def _shown(name: str, index: int) -> str:
    return f"{name}{' (return)' if index else ''}"


def _confidence(lines: list) -> str:
    statuses = {getattr(li, "status", "") for li in lines}
    statuses.discard("")
    if not lines:
        return "No sung lines were counted."
    if statuses <= {"verified", "manual_corrected"}:
        return "Syllable totals are dictionary-backed or manually corrected."
    if "unverified" in statuses and not ({"verified", "estimated", "manual_corrected"} & statuses):
        return "Syllable totals could not be verified."
    return "Some syllable totals are estimates (English, a number, or a word outside the dictionary)."


def _matches(text: str, phrases: tuple) -> set:
    """Non-overlapping hits, longer phrases first, so 'electric guitar' counts once."""
    low = (text or "").lower()
    taken = [False] * len(low)
    found = set()
    ordered = sorted(phrases, key=lambda item: -len(item[0] if isinstance(item, tuple) else item))
    for item in ordered:
        phrase, family = item if isinstance(item, tuple) else (item, item)
        start = 0
        while True:
            at = low.find(phrase, start)
            if at < 0:
                break
            end = at + len(phrase)
            if (at == 0 or not low[at - 1].isalnum()) and (end == len(low) or not low[end].isalnum()) and not any(taken[at:end]):
                for i in range(at, end):
                    taken[i] = True
                found.add(family)
            start = at + 1
    return found


def _analysis(snapshot):
    lyrics = getattr(snapshot, "lyrics", "") or ""
    bpm = _number(snapshot, "bpm")
    duration = _number(snapshot, "duration")
    timesig = str(_field(snapshot, "timesignature", "4") or "4")
    ins = LI.analyze(lyrics)
    dn = LI.density(ins, LI.DensitySettings(bpm=bpm or 0, timesig=timesig, duration=duration or 0))
    return ins, dn


def build_music_director_context(snapshot, mode: str = "basic") -> str:
    """A short analysis block for one caption request. `mode` is 'basic' or 'recommend'."""
    try:
        return _context(snapshot, mode)
    except Exception:
        return "MUSIC ANALYSIS\nThe lyrics could not be counted. Decide from the lyric text. Do not invent counts."


def _context(snapshot, mode: str) -> str:
    lyrics = getattr(snapshot, "lyrics", "") or ""
    language = str(_field(snapshot, "language", "auto") or "auto")
    bpm = _number(snapshot, "bpm")
    duration = _number(snapshot, "duration")
    timesig = str(_field(snapshot, "timesignature", "4") or "4")
    ins, dn = _analysis(snapshot)
    rows = ["MUSIC ANALYSIS (this request only; Python facts and estimates; do not copy this block into the caption and do not recount it)"]
    if mode == "recommend":
        rows.append("Evidence about the lyric's shape only. Choose the genre, instruments, groove, vocal color, and production yourself. Use a style given earlier in the request only when the user asked you to start from it.")
    else:
        rows.append("Use this to shape the arrangement around the user's Basic settings. Do not change BPM, key, duration, or the lyrics.")
    dur = f"{duration:g} s" if duration else "not set"
    rows.append(f"Target duration: {dur}. Vocal language setting: {language}.")
    clock = _SIG.get(timesig, timesig)
    if bpm and duration:
        if mode == "recommend":
            rows.append(f"Density clock (a calculation reference only, not a recommendation): {bpm:g} BPM, {clock}. It only explains how full the lyric is. Propose the tempo and meter that fit your own direction, and do not write a tempo number in the caption.")
        else:
            rows.append(f"Density clock: {bpm:g} BPM, {clock}. The clock only explains the estimate. Do not write it into the caption.")
    else:
        rows.append("Density clock: BPM or duration is missing, so the density is unknown. Do not invent a tempo in the caption.")
    rows.append("Confidence: " + _confidence(ins.lines) + " Density and duration risk are estimates. The melody is not known.")
    rows.append("Section blueprint:")
    if not ins.sections:
        rows.append("- No section tags.")
    seen: dict = {}
    for sec in ins.sections:
        n = seen.get(sec.name, 0)
        seen[sec.name] = n + 1
        _rest, parts = LI._cue_parts(sec.tag)
        cue = ", ".join(parts)[:80] if parts else "none"
        if sec.instrumental and not sec.sung_lines:
            rows.append(f"- {_shown(sec.name or '(start)', n)} | instrumental | cue: {cue}")
        else:
            unit = "sung line" if sec.sung_lines == 1 else "sung lines"
            rows.append(f"- {_shown(sec.name or '(start)', n)} | {sec.sung_lines} {unit} | cue: {cue}")
    rows.append("Vocal density:")
    if not dn.known:
        rows.append(f"- Unknown ({dn.why_unknown})")
    else:
        for s in dn.sections:
            label = _shown(s.name, s.index)
            if s.instrumental:
                rows.append(f"- {label}: instrumental")
            else:
                rows.append(f"- {label}: {s.syllables} syllables, {LI.STATUS_NAME.get(s.status, s.status)}" + (f", lines to check: {', '.join(str(n) for n in s.risky_lines)}" if s.risky_lines else ""))
        tight = [f"{_shown(s.name, s.index)} (lines {', '.join(str(n) for n in s.density_lines)})" for s in dn.sections if s.density_lines]
        rows.append("Tight spots: " + (", ".join(tight) if tight else "none") + f". Whole song: {len(dn.risky_lines)} of {dn.total_lines} lines to check"
                    + (f"; duration fit: {dn.fit} (about {dn.need_low:.0f}-{dn.need_high:.0f} s of {dn.settings.duration:g} s)" if dn.fit != "unknown" else "") + ".")
    items = LI.health(ins, dn) if dn.known or ins.sections else []
    notable = [h for h in items if h.kind in ("song_density", "section_density", "long_line", "uneven", "short_word", "structure", "duplicate_section", "cue_conflict", "tag_complexity")]
    rows.append("Lyrics health:")
    if not notable:
        rows.append("- None.")
    else:
        for h in notable[:6]:
            sentence = (h.summary or "").split(". ")[0].strip().rstrip(".")
            if len(sentence) > 180:
                sentence = sentence[:177].rstrip() + "..."
            rows.append(f"- {h.title}. {sentence}.")
    if lyrics.strip() and not ins.lines and not ins.sections:
        rows.append("The lyric text has no sung lines and no section tags.")
    return "\n".join(rows)


def review_caption(caption: str, snapshot) -> list:
    """Warnings only. A caption is never rewritten here, and a warning is not a reason to reject it."""
    text = caption or ""
    out = []
    instruments = _matches(text, _INSTRUMENT_PHRASES)
    if len(instruments) > _MAX_INSTRUMENTS:
        out.append(f"The caption names {len(instruments)} instrument families. A few with clear roles is easier to follow.")
    genres = _matches(text, _GENRE_PHRASES)
    if len(genres) > _MAX_GENRES:
        out.append(f"The caption names {len(genres)} genres. One genre home keeps the direction coherent.")
    try:
        ins, dn = _analysis(snapshot)
    except Exception:
        return out
    if dn.known and any(x.density_status in ("warning", "high") for x in dn.lines) and _SPARSE.search(text):
        out.append("Some lines are tight for their time, so a very spacious or drawn-out vocal may not fit those lines.")
    for sec in ins.sections:
        _rest, parts = LI._cue_parts(sec.tag)
        low = [p for p in parts if p.lower() in _VOCAL_LOW]
        if low and _HIGH.search(text):
            out.append(f"The {sec.name or 'section'} cue asks for {low[0]}, while the caption asks for a high or belted delivery.")
            break
    return out
