"""The Shared Rhyme Map: ONE vocabulary and ONE set of rules for rhymes, used by Analyze Rhymes (lyric_rhyme), the colours (rhyme_color), Live Insights and the Songwriter tools.

A rhyme link says three different things and never mixes them:
  sound         exact | near | different | uncertain    how the two words SOUND
  placement     internal | end | cross                  WHERE they sit (inside one line, at the end of two lines, last word -> inside the next line)
  verification  verified | ai_assessed | unverified | rejected    HOW SURE the program is
plus `source` (python | llm) and, for a Thai or English pair that Python read, a `reason`. A pair can be an End Rhyme that the AI assessed as Exact and that no rule has checked.

Languages. Every word has its own language, found from its script: Thai (th), Latin letters (en) or anything else (other). A Latin word in a song whose lyrics language is French, Spanish,
German, Italian or Portuguese is "other", not English. Thai is checked with thai_rhyme (vowel with its length + the final consonant, tone ignored). English has no pronunciation source in this
program, so Python only makes a SPELLING-BASED ESTIMATE of the rhyme part: it is never "verified", and a pair that only has this estimate is never coloured. Other languages are "unsupported":
the pair keeps whatever the AI said and is shown as unverified; "no rhyme" is never concluded for a language the program cannot read.

A rhyme link is not made across unrelated sections only because the sounds match: two lines are related when they sit in the same section, in sections of the same kind (Verse 1 / Verse 2,
Chorus / Chorus), or are neighbouring lines.

Nothing here asks an AI, touches the editor, or changes the lyrics. Offsets are Python character offsets into the lyrics text (for Thai they are also Flet's UTF-16 offsets; `utf16` converts).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from . import thai_rhyme as TR
from .compiler import HOLD_PREFIX, TAG_LINE  # noqa: F401  (re-exported for the colour module)
from .lyric_analysis import _INLINE_TAG

ANALYZER_VERSION = "rmap-1"                      # part of any cache key that holds a map; bump it when a rule here changes what a map says

SOUNDS = ("exact", "near", "different", "uncertain")
PLACEMENTS = ("internal", "end", "cross", "end_to_end", "end_to_internal", "cross_line", "cross_section", "alliteration")   # the first three are the compact view; the others are the v6 names
VERIFICATIONS = ("verified", "ai_assessed", "unverified", "rejected")
SOURCES = ("python", "llm")
KINDS = ("vowel", "consonant")                   # a vowel rhyme (สัมผัสสระ / a rhyming sound) or only the same first consonant (สัมผัสอักษร / alliteration)

TH, EN, OTHER = "th", "en", "other"
_LATIN_ONLY_NON_EN = frozenset({"es", "fr", "de", "it", "pt", "nl", "sv", "no", "da", "fi", "pl", "tr", "id", "ms", "vi"})
PALETTE = ("#60A5FA", "#F472B6", "#34D399", "#FBBF24", "#A78BFA", "#FB923C", "#22D3EE", "#F87171")
_TRAIL = re.compile(r"[\s.,!?:;…\"'”’]+$")


# ── data ──────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Word:
    """A word located in the lyrics text."""
    file_line: int                  # 1.. in the lyrics text
    sung: int                       # 1.. among the sung lines
    section: int                    # index of the section the line belongs to (-1 before the first tag)
    section_name: str
    text: str
    start: int                      # offsets in the whole lyrics text
    end: int
    lang: str = TH                  # th | en | other

    @property
    def span(self) -> tuple:
        return (self.start, self.end)


@dataclass
class Link:
    a: Word
    b: Word
    sound: str = "uncertain"
    placement: str = "end"
    verification: str = "unverified"
    source: str = "python"
    kind: str = "vowel"
    group: str = ""                 # the sound the two words share, e.g. "ไอ+กา" (a Python-read pair): one colour per group
    reason: str = ""
    confidence: str = ""
    claimed: str = ""               # what the AI said (end / cross / internal / near), kept for the cards
    pair_id: str = ""               # the AI's pair id (rhyme-map-v6), so a click on a coloured word can show every pair it belongs to
    sound_type: str = ""            # vowel_rhyme | consonant_rhyme | assonance | ... (rhyme-map-v6)
    traditional_role: str = ""      # สัมผัสใน | สัมผัสนอก | ""
    position_validated: bool = False
    position_fixed: bool = False

    @property
    def key(self) -> frozenset:
        return frozenset({self.a.span, self.b.span})

    @property
    def colourable(self) -> bool:
        return colourable(self)


@dataclass
class RhymeMap:
    lyrics_hash: str = ""
    language: str = ""
    analyzer_version: str = ANALYZER_VERSION
    links: list = field(default_factory=list)         # every link that may be shown (verified / ai_assessed / unverified)
    rejected: list = field(default_factory=list)      # links a rule read and refused (kept apart, never coloured)
    skipped: list = field(default_factory=list)       # one message per pair that could not be placed
    stale: bool = False                               # an AI result was given but it is not for these lyrics: it was not used
    unsupported: list = field(default_factory=list)   # languages (or scripts) the program cannot read: shown as unverified, never as "no rhyme"

    def of(self, **kw) -> list:
        return [k for k in self.links if all(getattr(k, a) == v for a, v in kw.items())]


def colourable(link: Link) -> bool:
    """A word is coloured only on evidence: a Python-read sound, or the AI's assessment of this very lyrics text. Never an unverified pair, a rejected one, or a mere first-letter match."""
    return link.verification in ("verified", "ai_assessed") and link.sound in ("exact", "near") and link.kind == "vowel"


# ── languages ─────────────────────────────────────────────────────────────────────────────────────────
def language_of(word: str, declared: str = "") -> str:
    """th / en / other for ONE word, from its script. `declared` is the lyrics language of the song (Song Settings), never the language of the window."""
    if TR.has_thai(word):
        return TH
    letters = [c for c in word if c.isalpha()]
    if not letters:
        return OTHER
    if all(c.isascii() or unicodedata.name(c, "").startswith("LATIN") for c in letters):
        primary = (declared or "").lower().replace("_", "-").split("-", 1)[0].split("+", 1)[0]
        if primary in _LATIN_ONLY_NON_EN and "en" not in re.split(r"[+,/ ]", (declared or "").lower()):
            return OTHER                                             # a French / Spanish ... song: English rules do not apply to its words
        return EN
    return OTHER


# ── English: a spelling-based ESTIMATE (never verified) ───────────────────────────────────────────────
_VOW = "aeiouy"
_NASAL, _STOP, _FRIC, _LIQ = "mn", "pbtdkgc", "fvszxjh", "lrw"


def _en_letters(word: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", word or "").casefold().encode("ascii", "ignore").decode())


def en_rime(word: str) -> "tuple | None":
    """(vowel group, final consonants) of the LAST syllable by spelling, with a silent final e handled. An estimate: the stress and the real pronunciation are not known. None for a word that has no
    vowel letter or is too short to say anything about."""
    w = _en_letters(word)
    if len(w) < 2 or not any(c in _VOW for c in w):
        return None
    silent = False
    if w.endswith("e") and len(w) > 2 and w[-2] not in _VOW and any(c in _VOW for c in w[:-1]):
        w, silent = w[:-1], True
    m = re.search(r"([aeiouy]+)([^aeiouy]*)$", w)
    if not m:
        return None
    vowel, coda = m.group(1), m.group(2)
    return (vowel + ("+e" if silent else ""), coda)


def _coda_class(coda: str) -> str:
    if not coda:
        return "open"
    c = coda[-1]
    return "nasal" if c in _NASAL else "stop" if c in _STOP else "fric" if c in _FRIC else "liq" if c in _LIQ else "other"


def en_relation(a: str, b: str) -> tuple:
    """(sound, detail) of two English words by spelling: exact (the same last vowel group and ending: a perfect rhyme as far as the spelling shows), near (the same vowel group with a similar
    ending: assonance / slant), different. 'uncertain' when a word cannot be read."""
    ra, rb = en_rime(a), en_rime(b)
    if ra is None or rb is None:
        return "uncertain", "a word is too short or has no vowel letter, so its sound was not estimated"
    detail = f"{a} -{ra[0]}{ra[1]} · {b} -{rb[0]}{rb[1]} (estimated from the spelling)"
    if ra == rb:
        return "exact", detail
    if ra[0].replace("+e", "") == rb[0].replace("+e", "") and _coda_class(ra[1]) == _coda_class(rb[1]):
        return "near", detail
    return "different", detail


# ── Thai: the first consonant sound (สัมผัสอักษร) ──────────────────────────────────────────────────────
_ONSET_CLASSES = ("ก", "ขฃคฅฆ", "ง", "จ", "ฉชฌ", "ซศษส", "ญย", "ฎด", "ฏต", "ฐฑฒถทธ", "ณน", "บ", "ป", "ผพภ", "ฝฟ", "ม", "ร", "ลฬ", "ว", "หฮ", "อ")
_ONSET_OF = {ch: i for i, grp in enumerate(_ONSET_CLASSES) for ch in grp}
_LEAD = "เแโไใ"
_LOW_SONORANT = "งญนมยรลว"


def thai_initial(word: str) -> "int | None":
    """The class of the first consonant SOUND of a Thai word (ฝ and ฟ are one sound; ห before ง ญ น ม ย ร ล ว only leads and is skipped). None when it cannot be read."""
    s = unicodedata.normalize("NFC", word or "").strip()
    i = 0
    while i < len(s) and s[i] in _LEAD:
        i += 1
    if i >= len(s) or s[i] not in _ONSET_OF:
        return None
    if s[i] == "ห" and i + 1 < len(s) and s[i + 1] in _LOW_SONORANT:
        i += 1
    return _ONSET_OF.get(s[i])


# ── the one classifier ────────────────────────────────────────────────────────────────────────────────
@dataclass
class Relation:
    sound: str
    verification: str               # of what PYTHON could read: verified (Thai read), unverified (an estimate or nothing)
    kind: str = "vowel"
    group: str = ""
    detail: str = ""
    lang: str = TH


def _same_word(a: str, b: str) -> bool:
    x, y = unicodedata.normalize("NFC", a or "").strip().casefold(), unicodedata.normalize("NFC", b or "").strip().casefold()
    return bool(x) and x == y


def relation(word_a: str, word_b: str, declared: str = "") -> Relation:
    """How two words sound, by the rules of THEIR language. Thai: thai_rhyme (verified when both syllables could be read). English: a spelling estimate (unverified). Another language, or two
    different languages: uncertain (unsupported). A pair whose vowel rhyme is absent but whose first consonant sound is the same is a consonant rhyme (kind 'consonant'), not a vowel rhyme."""
    la, lb = language_of(word_a, declared), language_of(word_b, declared)
    if _same_word(word_a, word_b):                    # the same word again: repetition (a hook, an echo). It is not a rhyme and never part of a scheme.
        return Relation("exact", "verified", "repetition", "", "the same word again: repetition, not a rhyme", la if la == lb else OTHER)
    if la == TH and lb == TH:
        sa, sb = TR.final_syllable(word_a), TR.final_syllable(word_b)
        if sa and sb and _same_word(sa, sb):          # the last syllable is the very same one (ใจ / สบายใจ): the same word's sound again, not a rhyme
            return Relation("exact", "verified", "repetition", "", f"the same last syllable again ({sa}): repetition, not a rhyme", TH)
        rel, detail = TR.sound_relation(word_a, word_b)
        if rel in ("exact", "near", "different"):
            group = ""
            if rel == "exact":
                group = TR.describe_rime(word_a)
            if rel == "different":
                ia, ib = thai_initial(word_a), thai_initial(word_b)
                if ia is not None and ia == ib:
                    return Relation("different", "verified", "consonant", "", detail + " · same first consonant sound", TH)
            return Relation(rel, "verified", "vowel", group, detail, TH)
        return Relation("uncertain", "unverified", "vowel", "", detail, TH)
    if la == EN and lb == EN:
        rel, detail = en_relation(word_a, word_b)
        return Relation(rel, "unverified", "vowel", "", detail, EN)
    if la != lb:
        return Relation("uncertain", "unverified", "vowel", "", "the two words are in different languages: no common sound rule", OTHER)
    return Relation("uncertain", "unverified", "vowel", "", "this language has no sound check in the program", OTHER)


def judge(claimed: str, rel: Relation) -> tuple:
    """(sound, verification, reason) for a pair the AI proposed. `claimed` is what the AI said: exact or near (anything else = uncertain). Python decides only when it could READ both words:
    a Thai pair whose sounds are exact stays/becomes exact, close ones become near, different ones are rejected with the reason. Everything else keeps the AI's word as ai_assessed."""
    claim = claimed if claimed in ("exact", "near") else "uncertain"
    if rel.kind == "repetition":
        return "exact", "verified", "Repetition: the same word again. It is shown as repetition and is not counted as a rhyme."
    if rel.verification == "verified":
        if rel.sound == "exact":
            return "exact", "verified", ("Sound check: " + rel.detail) if claim == "exact" else f"The AI called this a near rhyme, but the sounds match exactly. Sound check: {rel.detail}"
        if rel.sound == "near":
            return "near", "verified", ("Sound check: " + rel.detail) if claim == "near" else f"The AI called this an exact rhyme, but the sounds are close, not the same. Sound check: {rel.detail}"
        return "different", "rejected", f"Not a rhyme ({rel.detail})."
    if claim == "uncertain":
        return "uncertain", "unverified", rel.detail
    note = rel.detail
    if rel.lang == EN and rel.sound == "different":
        note = "The AI assessed this as a rhyme; the spelling suggests different sounds, which is only an estimate. " + rel.detail
    return claim, ("ai_assessed" if rel.lang != OTHER else "unverified"), note


# ── sections ──────────────────────────────────────────────────────────────────────────────────────────
def section_kind(name: str) -> str:
    """'verse' for Verse / Verse 1 / Verse 2 - soft: what kind of section it is. Pre-Chorus stays its own kind."""
    n = re.split(r"\s+[-–—]\s+", (name or "").strip().lower(), maxsplit=1)[0]
    return re.sub(r"[\s\d.:]+$", "", n).strip()


def related(sec_a: int, name_a: str, sung_a: int, sec_b: int, name_b: str, sung_b: int) -> bool:
    """May two lines be linked by a rhyme? The same section; sections of the same kind; or neighbouring sung lines. Not two unrelated sections just because the sounds match."""
    if sec_a == sec_b or abs(sung_a - sung_b) <= 1:
        return True
    ka, kb = section_kind(name_a), section_kind(name_b)
    return bool(ka) and ka == kb


# ── where a word is in a line (the colour layer and the AI pairs use these) ───────────────────────────
def utf16(text: str, pos: int) -> int:
    """The UTF-16 offset of character offset `pos` (what Flet's selection uses)."""
    return len(text[:pos].encode("utf-16-le")) // 2


def mask(raw: str) -> str:
    """The editor line with a leading [Hold] and every inline [tag] replaced by spaces of the same length, so offsets in it are offsets in the editor line."""
    lead = len(raw) - len(raw.lstrip())
    m = HOLD_PREFIX.match(raw[lead:])
    s = raw
    if m:
        s = s[:lead] + " " * m.end() + s[lead + m.end():]
    return _INLINE_TAG.sub(lambda t: " " * len(t.group(0)), s)


def clean(masked: str) -> str:
    return re.sub(r"\s{2,}", " ", masked).strip()


def occurrences(masked: str, word: str) -> list:
    """[(start, end)] of `word` in the masked line. Thai has no spaces, so any occurrence counts; another script must stand as a whole word."""
    out = []
    if not word:
        return out
    thai = bool(re.search(r"[฀-๿]", word))
    hay, needle = (masked, word) if thai else (masked.lower(), word.lower())
    if len(hay) != len(masked):
        return out
    i = hay.find(needle)
    while i >= 0:
        j = i + len(needle)
        if thai or ((hay[i - 1] if i > 0 else " ").isalpha() is False and (hay[j] if j < len(hay) else " ").isalpha() is False):
            out.append((i, j))
        i = hay.find(needle, i + 1)
    return out


def sung_map(masked: str) -> tuple:
    """(sung text, index map): the sung text of a masked editor line (tags blanked) exactly as the AI is shown it (runs of spaces collapsed, trimmed), and for every character of it its index
    in the masked line. A position in the sung text is turned into a position in the editor line through this map."""
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(masked):
        if ch.isspace():
            if prev_space:
                continue
            prev_space = True
        else:
            prev_space = False
        out.append(ch)
        idx.append(i)
    text = "".join(out)
    lead = len(text) - len(text.lstrip())
    trail = len(text.rstrip())
    return text[lead:trail], idx[lead:trail]


def narrow_to_syllable(text: str) -> tuple:
    """(offset in `text`, the text) of the rhyming SYLLABLE of a Thai word of several syllables, when the dictionary's syllables add up to the word and the last one is one real syllable with
    the word's own ending sound. Otherwise (0, text): a word is never cut at a guess."""
    if not TR.has_thai(text) or unicodedata.normalize("NFC", text) != text:
        return 0, text
    parts = TR._syllable_tokens(text)
    syl = parts[-1] if parts and len(parts) > 1 and "".join(parts) == text and TR.is_single_syllable(parts[-1]) else ""
    if syl and TR._rime(syl) == _thai_rime_of(text):
        return len(text) - len(syl), syl
    return 0, text


class Line:
    """One sung line of the lyrics: where it starts in the text, its masked text, and which section it is in."""

    def __init__(self, file_line: int, start: int, raw: str, sung: int = 0, section: int = -1, section_name: str = ""):
        self.file_line, self.start, self.raw = file_line, start, raw
        self.sung, self.section, self.section_name = sung, section, section_name
        self.masked = mask(raw)
        self.core_end = len(_TRAIL.sub("", self.masked))           # the end of the line without trailing space / punctuation


def pick(line: Line, word: str, role: str, taken: "tuple | None" = None):
    """((start, end) in the editor line, '') or (None, reason). `role`: end | inner | any. When the role does not decide which of several occurrences is meant, nothing is picked."""
    found = occurrences(line.masked, word)
    if role == "end":
        found = [f for f in found if f[1] == line.core_end]
    elif role == "inner":
        found = [f for f in found if f[1] != line.core_end]
    if taken:
        found = [f for f in found if f[1] <= taken[0] or f[0] >= taken[1]]
    if not found:
        return None, f"“{word}” was not found where the pair needs it in line {line.file_line}."
    if len(found) > 1:
        return None, f"“{word}” appears {len(found)} times in line {line.file_line} and the rhyme type does not say which one, so it was not coloured."
    return found[0], ""


def word_at(line: Line, span: tuple, text: str, declared: str = "") -> Word:
    a, b = span
    w = text[line.start + a:line.start + b]
    return Word(line.file_line, line.sung, line.section, line.section_name, w, line.start + a, line.start + b, language_of(w, declared))


# ── the Python rhymes (the same rules for Live Insights and for the colours) ─────────────────────────
@dataclass
class PyLine:
    """What the Python rhyme step needs of one sung line. `tokens` are Live Insights' tokens [(start, end, word, kind, syllables)], with offsets in the masked editor line."""
    file_line: int
    sung: int
    section: int
    section_name: str
    base: int                       # offset of the line in the lyrics text
    tokens: list


FUNCTION_WORDS = frozenset("ที่ มี ใน และ ก็ จะ แล้ว ไม่ ยัง เป็น ได้ ให้ ของ นะ ค่ะ ครับ อยู่ ไป มา แต่ ว่า ซึ่ง หรือ จน ถึง กับ จาก แค่ เธอ ฉัน ผม เรา มัน".split())      # not coloured as INTERNAL rhymes (they would colour every line)
_EN_FUNCTION = frozenset("a an the and or but of to in on at is are was were be i you he she it we they me my your his her our its this that for with as so do not no".split())


def _word(pl: PyLine, tok: tuple, declared: str) -> Word:
    """The word of a token. A Thai word of several syllables is represented by its LAST syllable (the rhyming one) when that syllable can be separated without guessing (final_syllable);
    only that syllable is coloured, never a half syllable and never the whole word on a syllable's account."""
    start, end, text = pl.base + tok[0], pl.base + tok[1], tok[2]
    if tok[3] == "th" and unicodedata.normalize("NFC", text) == text:
        parts = TR._syllable_tokens(text)                    # the dictionary's syllables of the word; used only when they add up to the word and the last one is one real syllable
        syl = parts[-1] if parts and len(parts) > 1 and "".join(parts) == text and TR.is_single_syllable(parts[-1]) else ""
        if syl and text.endswith(syl) and TR._rime(syl) == _thai_rime_of(text):          # the syllable has the same sound as the word's ending, or the whole word stays
            start, text = end - len(syl), syl
    return Word(pl.file_line, pl.sung, pl.section, pl.section_name, text, start, end, language_of(text, declared))


def _thai_rime_of(word: str):
    if not TR.has_thai(word):
        return None
    syl = TR.final_syllable(word)
    return TR._rime(syl) if syl else None


def _rime_key(r: tuple) -> str:
    return f"{r[0]}+{r[1]}"


def python_links(lines: list, declared: str = "") -> tuple:
    """(links, end_info) from the Python rules alone, for the current lyrics. Thai line endings that end in the same sound (exact), or close ones (near), are linked when the lines are related;
    one-syllable Thai words of one line that share a sound are internal rhymes; English words get a spelling-estimate link (unverified, never coloured). The same word again is repetition,
    not a rhyme. `end_info` is {file_line: (word, sound key or '', status)} with status rhymed | none | unverified, for Live Insights."""
    links: list = []
    ends = []                                       # (pyline, token, rime)
    info: dict = {}
    for pl in lines:
        last = pl.tokens[-1] if pl.tokens else None
        thai_toks = [t for t in pl.tokens if t[3] == "th"]
        if last is None or last[3] != "th":
            info[pl.file_line] = ("", "", "unverified" if pl.tokens else "")
            ends.append((pl, last if last is not None and last[3] == "en" else None, None))
            continue
        r = _thai_rime_of(last[2])
        info[pl.file_line] = (last[2], _rime_key(r) if r else "", "unverified" if r is None else "none")
        ends.append((pl, last, r))
    by_key: dict = {}
    for pl, t, r in ends:
        if r is not None:
            by_key.setdefault(_rime_key(r), []).append((pl, t, r))
    rimes = {k: v[0][2] for k, v in by_key.items()}
    near_of: dict = {}
    keys = sorted(rimes)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if TR._similar(rimes[a], rimes[b]):
                near_of.setdefault(a, set()).add(b)
                near_of.setdefault(b, set()).add(a)

    def components(members: list) -> list:
        """Groups of members that are related to one another (union-find over `related`), each with at least two DIFFERENT words."""
        parent = list(range(len(members)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pi, pj = members[i][0], members[j][0]
                if members[i][1][2] != members[j][1][2] and related(pi.section, pi.section_name, pi.sung, pj.section, pj.section_name, pj.sung):
                    parent[find(i)] = find(j)
        out: dict = {}
        for i, m in enumerate(members):
            out.setdefault(find(i), []).append(m)
        return [g for g in out.values() if len({m[1][2] for m in g}) >= 2]

    def add_chain(group: list, sound: str, shown: str):
        group = sorted(group, key=lambda m: m[0].sung)
        for (pa, ta, _ra), (pb, tb, _rb) in zip(group, group[1:]):
            if ta[2] == tb[2]:
                continue
            links.append(Link(_word(pa, ta, declared), _word(pb, tb, declared), sound, "end", "verified", "python", "vowel", shown,
                              "Sound check: " + f"{ta[2]} {_rime_key(_ra)} · {tb[2]} {_rime_key(_rb)}"))
        for pl, t, r in group:
            info[pl.file_line] = (t[2], _rime_key(r), "rhymed")

    for k, members in by_key.items():
        exact = components(members)
        grouped = {id(m) for g in exact for m in g}
        for g in exact:
            add_chain(g, "exact", k)
        left = [m for m in members if id(m) not in grouped]
        if left and k in near_of:
            others = [m for kk in near_of[k] for m in by_key.get(kk, [])]
            near_groups = components(left + others)
            shown = min([k] + sorted(near_of[k]))
            for g in near_groups:
                if any(id(m) in {id(x) for x in left} for m in g):
                    add_chain(g, "near", shown)
    # internal rhymes of one line (Thai: single-syllable words with the same sound, the line's end word included; English: a spelling estimate)
    for pl in lines:
        words = [t for t in pl.tokens if t[3] == "th" and len(t[4]) == 1 and t[2] not in FUNCTION_WORDS]
        got = [(t, _rime_key(r)) for t in words if (r := _thai_rime_of(t[2])) is not None]
        end_tok = pl.tokens[-1] if pl.tokens and pl.tokens[-1][3] == "th" else None
        st = info.get(pl.file_line)
        if end_tok is not None and st and st[2] == "rhymed" and st[1] and not any(t[0] == end_tok[0] for t, _k in got):
            got.append((end_tok, st[1]))                 # the end word joins an inner rhyme only when the line already rhymes at its end
        keyed: dict = {}
        for t, k in got:
            keyed.setdefault(k, []).append(t)
        for k, ts in keyed.items():
            ts = sorted(ts, key=lambda t: t[0])
            if len({t[2] for t in ts}) < 2:
                continue
            for ta, tb in zip(ts, ts[1:]):
                if ta[2] == tb[2]:
                    continue
                links.append(Link(_word(pl, ta, declared), _word(pl, tb, declared), "exact", "internal", "verified", "python", "vowel", k, f"Sound check: {ta[2]} · {tb[2]} {k}"))
        en = [t for t in pl.tokens if t[3] == "en" and _en_letters(t[2]) not in _EN_FUNCTION and len(_en_letters(t[2])) >= 2]
        for i in range(len(en)):
            for j in range(i + 1, len(en)):
                if _en_letters(en[i][2]) == _en_letters(en[j][2]) or language_of(en[i][2], declared) != EN:
                    continue
                snd, detail = en_relation(en[i][2], en[j][2])
                if snd == "exact":
                    links.append(Link(_word(pl, en[i], declared), _word(pl, en[j], declared), "exact", "internal", "unverified", "python", "vowel", "", detail))
    # English line endings of related lines: a spelling estimate, never coloured
    en_ends = [(pl, t) for pl, t, r in ends if t is not None and t[3] == "en" and language_of(t[2], declared) == EN]
    for i in range(len(en_ends)):
        for j in range(i + 1, len(en_ends)):
            (pa, ta), (pb, tb) = en_ends[i], en_ends[j]
            if _en_letters(ta[2]) == _en_letters(tb[2]) or not related(pa.section, pa.section_name, pa.sung, pb.section, pb.section_name, pb.sung):
                continue
            snd, detail = en_relation(ta[2], tb[2])
            if snd == "exact":
                links.append(Link(_word(pa, ta, declared), _word(pb, tb, declared), "exact", "end", "unverified", "python", "vowel", "", detail))
    return links, info


# ── merging the two sources ───────────────────────────────────────────────────────────────────────────
_STRENGTH = {"verified": 3, "ai_assessed": 2, "unverified": 1, "rejected": 0}


def merge(python: list, llm: list) -> list:
    """One link per pair of words. When the two sources describe the SAME pair, the one with the stronger verification is kept (a Python-verified pair outranks an AI assessment of it); the two
    statuses are never averaged or combined into a bigger claim."""
    best: dict = {}
    for k in list(python) + list(llm):
        cur = best.get(k.key)
        if cur is None or _STRENGTH.get(k.verification, 0) > _STRENGTH.get(cur.verification, 0):
            best[k.key] = k
    return sorted(best.values(), key=lambda k: (min(k.a.start, k.b.start), max(k.a.start, k.b.start)))


def groups(links: list) -> list:
    """[[Word, ...]] colour groups: the words joined by a colourable link, sorted by position (so a colour order follows the song)."""
    parent: dict = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    words: dict = {}
    for k in links:
        if not colourable(k):
            continue
        for w in (k.a, k.b):
            words.setdefault(w.span, w)
            parent.setdefault(w.span, w.span)
        parent[find(k.a.span)] = find(k.b.span)
    out: dict = {}
    for sp, w in words.items():
        out.setdefault(find(sp), []).append(w)
    return sorted((sorted(g, key=lambda w: w.start) for g in out.values()), key=lambda g: g[0].start)


# ── the AI's pairs, placed in the lyrics ──────────────────────────────────────────────────────────────
_OLD_VIEW = {"verified": ("exact", "verified"), "llm_exact": ("exact", "ai_assessed"), "llm_near": ("near", "ai_assessed"), "uncertain": ("uncertain", "unverified"),
             "unverified": ("uncertain", "unverified"), "rejected": ("different", "rejected")}


def _pair_facts(p) -> tuple:
    """(sound, verification) of a pair of lyric_rhyme. A result saved before the three facts existed only has `verify`; the facts are read from it."""
    sound, verification = getattr(p, "sound", ""), getattr(p, "verification", "")
    if sound in SOUNDS and verification in VERIFICATIONS:
        return sound, verification
    s, v = _OLD_VIEW.get(getattr(p, "verify", ""), ("uncertain", "unverified"))
    return ("near" if getattr(p, "type", "") == "near" and s == "exact" else s), v


def _local_span(line: Line, word: str, start: int, end: int):
    """The editor-line span of a word whose position in the SUNG text was validated: ((a, b), '') or (None, reason). The text must still be exactly there."""
    sung, imap = sung_map(line.masked)
    if not (0 <= start < end <= len(sung)) or sung[start:end] != word:
        return None, f"“{word}” is no longer where the analysis put it in line {line.file_line}."
    a, b = imap[start], imap[end - 1] + 1
    if b - a != end - start:
        return None, f"“{word}” in line {line.file_line} is split by spaces in the editor line, so it was not placed."
    off, syl = narrow_to_syllable(word)                          # colour the rhyming syllable of a long Thai word, never half a syllable
    return (a + off, b), ""


def llm_links(lyrics: str, result, declared: str = "") -> tuple:
    """([Link], [rejected Link], [message]) from the pairs of a lyric_rhyme.RhymeResult that is FOR these lyrics. Each word is located by the role its rhyme type gives it (end = the last word
    of the line, cross = the earlier line's last word and a word inside the next line, internal = a word of that line). A word that sits in its line more than once and whose role does not decide
    which is meant is left out with the reason: colouring the wrong word is worse than none. Pairs of unrelated sections are not linked."""
    from .lyric_analysis import original_lines, sung_lines
    sung = sung_lines(lyrics)
    by_file = {n: (start, content) for n, start, _end, content in original_lines(lyrics or "")}
    lines: dict = {}
    skipped: list = []
    for ln in sung:
        found = by_file.get(ln.file_line)
        if found is None:
            skipped.append(f"Line {ln.file_line} could not be matched to the analysed text, so it was not coloured.")
            continue
        L = Line(ln.file_line, found[0], found[1], ln.num, ln.section, ln.section_name)
        if clean(L.masked) != ln.text:
            skipped.append(f"Line {ln.file_line} could not be matched to the analysed text, so it was not coloured.")
            continue
        lines[ln.num] = L
    ok, bad = [], []
    for p in getattr(result, "pairs", []) + getattr(result, "rejected", []):
        la, lb = lines.get(p.a_line), lines.get(p.b_line)
        if la is None or lb is None:
            skipped.append(f"{p.a_word} ↔ {p.b_word}: a line of this pair could not be located.")
            continue
        sound, verification = _pair_facts(p)
        placement = getattr(p, "placement", "") or (p.type if p.type in PLACEMENTS else ("internal" if p.a_line == p.b_line else "end"))
        if p.type == "end":
            ra, rb = "end", "end"
        elif p.type == "cross":
            ra, rb = ("end", "inner") if p.a_line < p.b_line else ("inner", "end")
        else:
            ra = rb = "any"
        if getattr(p, "position_validated", False):
            ga, why = _local_span(la, p.a_word, p.a_start, p.a_end)
            gb, why2 = _local_span(lb, p.b_word, p.b_start, p.b_end)
            if ga is None or gb is None:
                skipped.append(f"{p.a_word} ↔ {p.b_word}: {why or why2}")
                continue
        else:
            ga, why = pick(la, p.a_word, ra)
            if ga is None:
                skipped.append(f"{p.a_word} ↔ {p.b_word}: {why}")
                continue
            gb, why = pick(lb, p.b_word, rb, taken=ga if la is lb else None)
            if gb is None:
                skipped.append(f"{p.a_word} ↔ {p.b_word}: {why}")
                continue
        link = Link(word_at(la, ga, lyrics, declared), word_at(lb, gb, lyrics, declared), sound, getattr(p, "placement_detail", "") or placement, verification, getattr(p, "source", "") or "llm",
                    getattr(p, "kind", "") or "vowel", getattr(p, "group_id", ""), getattr(p, "verify_note", "") or getattr(p, "reason", ""), getattr(p, "confidence", ""), getattr(p, "type", ""))
        link.pair_id, link.sound_type, link.traditional_role = getattr(p, "pair_id", ""), getattr(p, "sound_type", ""), getattr(p, "traditional_role", "")
        link.position_validated, link.position_fixed = bool(getattr(p, "position_validated", False)), bool(getattr(p, "position_fixed", False))
        if link.a.span == link.b.span:
            continue
        if not related(la.section, la.section_name, la.sung, lb.section, lb.section_name, lb.sung):
            link.verification = "rejected"
            link.reason = "These lines are in sections that are not related, so the sounds alone do not link them."
        (bad if link.verification == "rejected" else ok).append(link)
    return ok, bad, skipped


def build_map(lyrics: str, python: list, result=None, language: str = "") -> RhymeMap:
    """The map of THESE lyrics: the Python links (always) and the AI's pairs (only when `result` was made for exactly these sung lines; otherwise `stale` is set and none of it is used)."""
    from .lyric_analysis import sung_lines, text_hash
    m = RhymeMap(text_hash(lyrics or ""), language)
    llm: list = []
    if result is not None:
        if [ln.text for ln in sung_lines(lyrics)] != list(result.lines):
            m.stale = True
        else:
            llm, m.rejected, m.skipped = llm_links(lyrics, result, language)
    m.links = merge([] if (result is not None and not m.stale) else python, llm)      # with an AI result for these lyrics the colours come from IT (positions validated, sounds re-checked); the Python rules colour only while there is none
    langs = {w.lang for k in m.links for w in (k.a, k.b)}
    m.unsupported = [x for x in (OTHER,) if x in langs]
    return m
