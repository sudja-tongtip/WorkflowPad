"""Thai sound check for a rhyme the model proposed.

PyThaiNLP's KhaveeVerifier (already installed for Thai line breaking) names the
vowel and the final-consonant class of ONE syllable from Royal Institute
spelling rules. That is a sound class, not a check that two written endings
look alike. Khavee is only asked when this module can see that the text is a
single syllable. A fragment (าว inside ดาว), a word that is not in the line,
or a word whose last syllable cannot be separated stays unverified or is
rejected as a bad span. Nothing here claims to have heard the singer.

Neural G2P (thai-g2p) was not used: it needs a separate Torch model download.
When the spelling is ambiguous (a leading vowel tangled with the previous
syllable, an uncommon loanword), the result is unverified rather than a guess.
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache

VERIFIED, UNVERIFIED, REJECTED = "verified", "unverified", "rejected"
STATUSES = (VERIFIED, UNVERIFIED, REJECTED)

_CONS = frozenset("กขฃคฅฆงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ")
_LEAD = frozenset("เแโไใ")
_TONE = frozenset("่้๊๋")
_ABOVE = frozenset("ิีึืัํ")
_BELOW = frozenset("ุู")
_DEPENDENT = _TONE | _ABOVE | _BELOW | frozenset("ะาำๅ็์")
_CLUST = frozenset({
    "กร", "กล", "กว", "ขร", "ขล", "ขว", "คร", "คล", "คว", "ตร", "ปร", "ปล", "พร", "พล",
    "บร", "บล", "ดร", "ฟร", "ฟล", "สร", "ศร", "ซร", "จร",
})
_NAM = frozenset({"หง", "หญ", "หน", "หม", "หย", "หร", "หล", "หว", "อย"})
_LENGTH = {frozenset(p) for p in (
    ("อา", "อะ"), ("อี", "อิ"), ("อือ", "อึ"), ("อู", "อุ"), ("เอ", "เอะ"), ("แอ", "แอะ"),
    ("โอ", "โอะ"), ("ออ", "เอาะ"), ("เอีย", "เอียะ"), ("เอือ", "เอือะ"), ("อัว", "อัวะ"),
)}
_NASAL = frozenset({"กน", "กม", "กง"})
_STOP = frozenset({"กก", "กด", "กบ"})
_EXACT_TYPES = frozenset({"end", "cross", "internal"})


def has_thai(text: str) -> bool:
    return any("\u0e01" <= ch <= "\u0e5b" for ch in text or "")


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _skip_tone(s: str, i: int) -> int:
    while i < len(s) and s[i] in _TONE:
        i += 1
    return i


def _take_onset(s: str, i: int, lead: bool) -> "int | None":
    if i >= len(s) or s[i] not in _CONS:
        return None
    if i + 1 < len(s) and s[i:i + 2] in _CLUST | _NAM:
        nxt = i + 2
        if nxt < len(s) and (s[nxt] in _ABOVE or s[nxt] in _BELOW or s[nxt] in "ะาำๅ็อ" or s[nxt] in _TONE):
            return nxt
        if lead and (nxt >= len(s) or s[nxt] in _TONE or s[nxt] in _CONS):
            return nxt
    return i + 1


def _coda_rest(s: str, i: int, required: bool) -> bool:
    i = _skip_tone(s, i)
    if i >= len(s):
        return not required
    if s[i] not in _CONS:
        return False
    i += 1
    i = _skip_tone(s, i)
    if i < len(s) and s[i] == "์":
        i += 1
    return i == len(s)


def _match_rr(s: str) -> bool:
    t = "".join(ch for ch in s if ch not in _TONE)
    if len(t) < 3 or t[0] not in _CONS or t[1:3] != "รร":
        return False
    rest = t[3:]
    return rest == "" or (len(rest) == 1 and rest[0] in _CONS) or (len(rest) == 2 and rest[0] in _CONS and rest[1] == "์")


def _inherent(s: str) -> bool:
    if any(ch in _LEAD or ch in _ABOVE or ch in _BELOW or ch in "ะาำๅ็" for ch in s):
        return False
    cons = [ch for ch in s if ch in _CONS]
    bare = [ch for ch in s if ch not in _TONE and ch != "์"]
    if len(cons) != 2 or len(bare) != 2:
        return False
    if cons[0] == "ห" and cons[1] in "งญนมยรลว":
        return False
    return True


def _nucleus_e(s: str, i: int) -> bool:
    n = len(s)
    if i >= n:
        return True
    ch = s[i]
    if ch == "็":
        return _coda_rest(s, i + 1, True)
    if ch == "ิ":
        return _coda_rest(s, i + 1, True)
    if ch == "ี":
        j = _skip_tone(s, i + 1)
        if j >= n or s[j] != "ย":
            return False
        j += 1
        if j < n and s[j] == "ะ":
            j += 1
        return _coda_rest(s, j, False)
    if ch == "ื":
        j = _skip_tone(s, i + 1)
        if j >= n or s[j] != "อ":
            return False
        return _coda_rest(s, j + 1, False)
    if ch == "า":
        j = i + 1
        if j < n and s[j] == "ะ":
            return _skip_tone(s, j + 1) == n
        return _skip_tone(s, j) == n
    if ch == "อ":
        return _coda_rest(s, i + 1, False) and _skip_tone(s, i + 1) <= n
    if ch == "ะ":
        return _skip_tone(s, i + 1) == n
    return _coda_rest(s, i, False)


def _nucleus_ae(s: str, i: int) -> bool:
    n = len(s)
    if i >= n:
        return True
    if s[i] == "็":
        return _coda_rest(s, i + 1, True)
    if s[i] == "ะ":
        return _skip_tone(s, i + 1) == n
    return _coda_rest(s, i, False)


def _nucleus_o(s: str, i: int) -> bool:
    n = len(s)
    if i >= n:
        return True
    if s[i] == "ะ":
        return _skip_tone(s, i + 1) == n
    return _coda_rest(s, i, False)


def _nucleus_plain(s: str, i: int) -> bool:
    n = len(s)
    if i >= n:
        return _inherent(s) or _match_rr(s)
    ch = s[i]
    if ch == "ำ":
        return _skip_tone(s, i + 1) == n
    if ch == "็":
        j = i + 1
        if j < n and s[j] == "อ":
            j += 1
        return _coda_rest(s, j, False)
    if ch == "ั":
        j = _skip_tone(s, i + 1)
        if j < n and s[j] == "ว":
            return _coda_rest(s, j + 1, False)
        return _coda_rest(s, j, True)
    if ch in "ิีึุู":
        return _coda_rest(s, _skip_tone(s, i + 1), False)
    if ch == "ื":
        j = _skip_tone(s, i + 1)
        if j < n and s[j] == "อ":
            return _coda_rest(s, j + 1, False)
        return _coda_rest(s, j, False)
    if ch in "า":
        return _coda_rest(s, _skip_tone(s, i + 1), False)
    if ch == "ะ":
        return _skip_tone(s, i + 1) == n
    if ch == "อ":
        return _coda_rest(s, _skip_tone(s, i + 1), False)
    if ch == "ว":
        # สวย / ด้วย: ว is the vowel อัว and ย is the final. ควร / รวม: ว is อัว and a consonant is the final.
        j = _skip_tone(s, i + 1)
        if j < n and s[j] == "ย":
            return _skip_tone(s, j + 1) == n
        return _coda_rest(s, j, True)
    return _inherent(s) or _match_rr(s)


def is_single_syllable(word: str) -> bool:
    """True only when `word` is one Thai syllable. Fragments and unclear strings are False."""
    s = _nfc(word).strip()
    if not s or "ๆ" in s or any(ch not in _CONS and ch not in _LEAD and ch not in _DEPENDENT and ch not in "ฤฦ" for ch in s):
        return False
    if "ฤ" in s or "ฦ" in s:
        return s in {"ฤ", "ฤๅ", "ฦ", "ฦๅ"}
    if s[0] in _DEPENDENT:
        return False
    i = 0
    lead = ""
    if s[0] in _LEAD:
        lead = s[0]
        i = 1
    onset = _take_onset(s, i, bool(lead))
    if onset is None:
        return _match_rr(s)
    i = _skip_tone(s, onset)
    if lead in {"ไ", "ใ"}:
        i = _skip_tone(s, i)
        return _coda_rest(s, i, False)
    if lead == "เ":
        return _nucleus_e(s, i)
    if lead == "แ":
        return _nucleus_ae(s, i)
    if lead == "โ":
        return _nucleus_o(s, i)
    return _nucleus_plain(s, i)


def _segmentable(text: str) -> bool:
    """True when every character sits in a syllable this parser accepts. A leading vowel left over makes it False."""
    s = _nfc(text)
    n, i = len(s), 0
    if any(ch in _LEAD for ch in s):
        # A leading vowel tangled with a later syllable (เสมอ) must not be split by greedy matching.
        return is_single_syllable(s)
    while i < n:
        if s[i] in _DEPENDENT:
            return False
        found = 0
        for j in range(i + 1, n + 1):
            if is_single_syllable(s[i:j]):
                found = j
        if not found:
            return False
        i = found
    return True


def final_syllable(word: str) -> "str | None":
    """The last syllable, or None when it cannot be separated without guessing."""
    s = _nfc(word).strip()
    if is_single_syllable(s):
        return s
    for length in range(len(s) - 1, 1, -1):
        suf = s[-length:]
        if suf[0] in _DEPENDENT:
            continue
        if not is_single_syllable(suf):
            continue
        prefix = s[:-length]
        if any(ch in _LEAD for ch in prefix):
            return None
        if _segmentable(prefix):
            return suf
    return None


def _rime(syllable: str) -> "tuple | None":
    kv = _verifier()
    if kv is None:
        return None
    try:
        sara, mart = kv.check_sara(syllable), kv.check_marttra(syllable)
    except Exception:
        return None
    if not sara or not mart:
        return None
    # The same sound-normalization KhaveeVerifier.is_sumpus uses for สระเกิน.
    if sara == "อะ" and mart == "เกย":
        sara, mart = "ไอ", "กา"
    if sara in ("อะ", "อำ") and mart == "กม":
        sara, mart = "อำ", "กา"
    return sara, mart


def _similar(a: tuple, b: tuple) -> bool:
    (sa, ma), (sb, mb) = a, b
    if sa == sb and ma != mb and ((ma in _NASAL and mb in _NASAL) or (ma in _STOP and mb in _STOP)):
        return True
    return ma == mb and frozenset((sa, sb)) in _LENGTH


def describe_rime(word: str) -> str:
    syl = final_syllable(word)
    if syl is None:
        return ""
    r = _rime(syl)
    if r is None:
        return ""
    return f"{r[0]}+{r[1]}"


def sound_relation(word_a: str, word_b: str) -> tuple:
    """(exact|near|different|unverified, detail). Exact and near are separate. Nothing here is guessed."""
    if not has_thai(word_a) or not has_thai(word_b):
        return "unverified", "Python did not check this sound (it only reads Thai spelling)."
    sa, sb = final_syllable(word_a), final_syllable(word_b)
    if sa is None or sb is None:
        return "unverified", "Python could not separate the last syllable, so this pair is unverified."
    ra, rb = _rime(sa), _rime(sb)
    if ra is None or rb is None:
        return "unverified", "Python could not read the vowel and final consonant, so this pair is unverified."
    detail = f"{sa} {ra[0]}+{ra[1]} · {sb} {rb[0]}+{rb[1]}"
    if ra == rb:
        return "exact", detail
    if _similar(ra, rb):
        return "near", detail
    return "different", detail


def verify_pair(kind: str, word_a: str, word_b: str) -> tuple:
    """(verified|unverified|rejected, note). A near report is verified only when the sounds are close but not the same."""
    rel, detail = sound_relation(word_a, word_b)
    if rel == "unverified":
        return UNVERIFIED, detail
    if kind == "near":
        if rel == "near":
            return VERIFIED, "Sound check: " + detail
        if rel == "exact":
            return UNVERIFIED, f"The sounds match exactly ({detail}), so this is not a near rhyme."
        return REJECTED, f"Not a near rhyme ({detail}). The vowel and the final consonant both differ."
    if kind in _EXACT_TYPES:
        if rel == "exact":
            return VERIFIED, "Sound check: " + detail
        if rel == "near":
            return REJECTED, f"Not an exact rhyme ({detail}). Vowel length or the final consonant differs, so it was not called a near rhyme."
        return REJECTED, f"Not a rhyme ({detail})."
    return UNVERIFIED, "Sound check: " + detail


@lru_cache(maxsize=1)
def _verifier():
    try:
        from pythainlp.khavee import KhaveeVerifier
        return KhaveeVerifier()
    except Exception:
        return None


@lru_cache(maxsize=128)
def _syllable_tokens(line: str) -> tuple:
    try:
        from pythainlp.tokenize import syllable_tokenize
        return tuple(syllable_tokenize(_nfc(line), engine="dict"))
    except Exception:
        return ()


def _span_status(line: str, start: int, end: int) -> str:
    """ok, fragment, or uncertain for line[start:end].

    A consonant after the span is not proof of a fragment. Thai words are not
    spaced, so เธอ in เธอมา is a whole syllable even though เธอม is also a legal
    one-syllable spelling. A piece is a fragment when a vowel mark hangs off it,
    a leading vowel before it belongs to it, a longer syllable ends on it, or
    the dictionary token that contains it is itself one syllable (ดา in ดาว).
    A dictionary token this parser cannot read as one syllable is not proof
    either way: มา in เสมอมา stays uncertain, because the same shape is งค์
    inside องค์ and วัส inside สวัสดี.
    """
    word = line[start:end]
    if not word or word[0] in _DEPENDENT:
        return "fragment"
    if start > 0 and line[start - 1] in _LEAD:
        return "fragment"
    if end < len(line) and line[end] in _DEPENDENT:
        return "fragment"
    for j in range(max(0, start - 6), start):
        if line[j] in _DEPENDENT and line[j] not in _TONE:
            continue
        longer = line[j:end]
        if len(longer) > len(word) and is_single_syllable(longer):
            return "fragment"
    tokens = _syllable_tokens(line)
    if tokens and "".join(tokens) == line:
        pos = 0
        for tok in tokens:
            nxt = pos + len(tok)
            if start == pos and end == nxt:
                break
            if pos <= start and end <= nxt:
                prefix = tok[:start - pos]
                if is_single_syllable(tok) or any(ch in _LEAD for ch in prefix):
                    return "fragment"
                # Not one syllable. Do not guess a boundary: PyThaiNLP glues
                # เสมอมา into เสม+อมา, but องค์ and สวัส have the same shape and
                # are not safe to split. Uncertain, not fragment.
                return "uncertain"
            pos = nxt
        else:
            return "uncertain"
    elif end < len(line) and is_single_syllable(line[start:]):
        # No aligned dictionary split. Fragment only when nothing is left after
        # this longer syllable, so the extra letters cannot be the next word.
        return "fragment"
    if is_single_syllable(word) or final_syllable(word):
        return "ok"
    return "uncertain"


def placement(line: str, word: str) -> dict:
    """Where `word` sits in a Thai line: end / other / best are ok, uncertain, fragment, or missing."""
    line, word = _nfc(line).strip(), _nfc(word).strip()
    out = {"end": "missing", "other": "missing", "best": "missing"}
    if not word:
        return out
    rank = {"ok": 3, "uncertain": 2, "fragment": 1, "missing": 0}
    start, best = 0, "missing"
    while True:
        i = line.find(word, start)
        if i < 0:
            break
        status = _span_status(line, i, i + len(word))
        slot = "end" if i + len(word) == len(line) else "other"
        if rank[status] > rank[out[slot]]:
            out[slot] = status
        if rank[status] > rank[best]:
            best = status
        start = i + 1
    out["best"] = best
    return out
