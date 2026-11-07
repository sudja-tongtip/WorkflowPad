"""Rhyme & Flow in the Songwriter AI's report.

Two parts with two different owners:

  EVIDENCE   the Rhyme Analyzer AI decides everything about the sounds: which words rhyme, exact / near / uncertain, the rhyme letters of the line endings (A, B, C ...), the scheme, repetition.
             The program only CHECKS THE FORMAT of its answer (it parses, the required fields are there, the types are right: `rhyme_v6.llm_data`) and `build_llm_evidence` lays the answer out for
             the page. Nothing here judges a rhyme, regroups a letter, works a scheme out or adds or removes a pair because of how it sounds. The only lookup is where a quoted word sits in its line,
             so it can be highlighted; a word that is not found is simply not highlighted.
  CRITIQUE   the Songwriter AI reads the same evidence (`evidence_text`) and writes what the rhymes do for the flow, the meaning, the mood and the hook, and one recommendation where one is needed
             (`RHYME_TASK`, `parse_blocks`). It never supplies pairs or letters of its own.

The report page draws the evidence (scheme, line map, pairs, repetition) and puts the Songwriter's critique under it (`merge`). Analyze Song never rewrites a lyric.
"""
from __future__ import annotations

import re
import unicodedata

from . import compiler as K

MARK = "RHYME_SECTION"
PRIORITIES = {"optional": "optional", "worthwhile": "worthwhile", "important": "important", "low": "optional", "medium": "worthwhile", "high": "important"}
KIND_LABEL = {"vowel": "Vowel rhyme", "consonant": "Consonant rhyme", "repetition": "Repetition", "assonance": "Assonance", "consonance": "Consonance", "alliteration": "Alliteration",
              "multisyllabic": "Multisyllabic rhyme", "wordplay": "Wordplay", "other": "Sound link"}
PLACEMENT_LABEL = {"end": "end rhyme", "internal": "internal rhyme", "end_to_internal": "end to inside", "cross": "across lines", "end_to_end": "end rhyme", "cross_line": "across lines",
                   "cross_section": "across sections"}
_KIND_OF = {"vowel_rhyme": "vowel", "consonant_rhyme": "consonant", "assonance": "assonance", "consonance": "consonance", "alliteration": "alliteration", "multisyllabic": "multisyllabic",
            "wordplay": "wordplay"}
MAX_SECTIONS = 24
MAX_PAIRS = 40

RHYME_TASK = (
    "SECTION REVIEW (part of YOUR report, after STRENGTHS and before the FINDING blocks). For EACH section of the RHYME ANALYZER RESULT follow the Report Method and write one [RHYME.n] tag. Its lines are:\n"
    "section: the section name, as in the result\n"
    "lines: N-M (the first and last sung line of that section, as in the result)\n"
    "checks: the verdict on each of these, one short phrase each, naming the line when it is not fine: wording | meaning | story | imagery | hook | flow | line endings (for example `wording ok; meaning ok; story ok; imagery L12 cliche; hook -; flow ok; endings L13 filler?`)\n"
    "quality: 2-3 sentences in the REPLY LANGUAGE about the lyrics of this section on their own, not about the rhyme: meaning and story, whether the Thai is wording a person would sing, images, feeling, hook, flow. Name real lines.\n"
    "strength: what really works, with its line number and why (or `-`)\n"
    "issue: a problem you can show from the lines, with its line number and what it does to the listener or the singer (or `-`)\n"
    "consideration: something that could be better but depends on the writer's intent, with its line number (or `-`)\n"
    "rhyme: 1-2 sentences: what the rhyme does for the continuity, the hook and the singing; whether it makes a word feel forced or only repeats a sound; whether it suits the job of the section; what repetition does. When the result lists no pair for lines, write that the analysis listed none (whether they rhyme is then not confirmed); name the writer's intent only when the lyrics themselves show it, never from a missing pair alone\n"
    "keep: with no recommendation: the specific reason this section can stay as it is, naming the lines (or `-` when you do recommend something)\n"
    "recommendation: ONE suggestion with its line number and a direction to try (never a replacement lyric), or `-` when nothing needs to change\n"
    "reason: with a recommendation: what you found and why it matters for the song (or `-`)\n"
    "benefit: with a recommendation: what it should improve (or `-`)\n"
    "priority: optional | worthwhile | important, matching how much it matters to the song. It MUST be `-` when the recommendation is `-`\n"
    "limits: what the result left uncertain and you therefore did not judge, or `-`\n"
    "LANGUAGE: every free-text field above (quality, strength, issue, consideration, rhyme, keep, recommendation, reason, benefit, limits) is written in the REPLY LANGUAGE. Quotes from the lyrics stay as written; tag names, field names and `-` stay as they are.\n"
    "RHYME RULES: Do not write the groups or the pairs again. If the result says its own groups and pairs disagree (\"inconsistent\"), say so in `limits`. Text in parentheses is not sung text for the rhyme and is not a line ending. "
    "A rhyme point goes in the section block only: do not repeat it as a FINDING. Never write a replacement lyric in a block. A repeated word or a matching pair is not a strength by itself: say what it does for the song.\n"
    "SUMMARY: it must name the weakest place of the song with its line number and why, or say honestly that you found none and what you checked. A real weak point of the language also gets its own FINDING block."
)

NO_EVIDENCE = ("RHYME ANALYZER RESULT: not available this time (the Rhyme Analyzer could not be used, or its answer could not be read). Do not state which lines rhyme and do not state a scheme, and do not write as if the "
               "Rhyme Analyzer had checked anything. In each [RHYME] tag judge the flow, the meaning and the hook only, and write in `limits` that the rhyme sounds were not analyzed.")

_KV = re.compile(r"^\s*(section|lines|checks|quality|strength|issue|consideration|consider|rhyme|keep|recommendation|reason|benefit|priority|limits)\s*:\s*(.*)$", re.IGNORECASE)
_LEGACY = re.compile(r"^\s*(scheme|confidence|labels|endings|pair)\s*:", re.IGNORECASE)          # the answer's own letters and pairs are never used
_TRAIL = " \t\"'“”‘’()[]{}.,!?…:;-–—"


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def _sung_lines(lyrics: str) -> dict:
    """{file line number: text} of the sung lines (not blank, not a [tag] line)."""
    out = {}
    for n, raw in enumerate((lyrics or "").replace("\r\n", "\n").split("\n"), start=1):
        st = raw.strip()
        if st and not K.TAG_LINE.match(st):
            out[n] = raw
    return out


def find_word(text: str, word: str, end: bool, occ: "int | None" = None) -> "list | None":
    """[start, end] of `word` in the line (NFC, case-insensitive), or None when it is not there. `occ` = which occurrence (1 = the first). An end word is taken at the end of the line, outside any
    parentheses, first. A word that appears more than once, with no occurrence and not at the end, has no position ([-1, -1]): no colour is drawn on a guess. A text lookup only; it judges nothing."""
    t, w = _nfc(text), _nfc(word).strip(_TRAIL)
    if not w:
        return None
    tl, wl = t.casefold(), w.casefold()
    hits = [m.start() for m in re.finditer(re.escape(wl), tl)]
    if not hits:
        return None
    if occ and 1 <= occ <= len(hits):
        return [hits[occ - 1], hits[occ - 1] + len(wl)]
    if end:
        core = tl
        for _ in range(4):                                               # the last words outside trailing (parentheses) and inline [tags]
            nxt = re.sub(r"(?:\([^()]*\)|\[[^\[\]]*\])\s*$", "", core).rstrip(_TRAIL)
            if nxt == core:
                break
            core = nxt
        core = core.rstrip(_TRAIL)
        if core.endswith(wl):
            s = len(core) - len(wl)
            return [s, s + len(wl)]
        return [hits[-1], hits[-1] + len(wl)] if len(hits) == 1 else [-1, -1]
    if len(hits) == 1:
        return [hits[0], hits[0] + len(wl)]
    return [-1, -1]


def _pos(p: "list | None") -> "list | None":
    return p if p and p[0] >= 0 else None


_FREE_TYPES = ("free", "partial", "irregular", "uncertain", "none")


def _canon(pattern: str) -> str:
    """A letter pattern with its letters renamed by first appearance (JKLL -> ABCC); `-` and `~` stay. Only used to COMPARE two things the Analyzer wrote: the result is never shown instead of them."""
    seen: dict = {}
    out = []
    for ch in pattern:
        if ch.isalpha():
            seen.setdefault(ch.upper(), chr(65 + len(seen) % 26))
            out.append(seen[ch.upper()])
        elif ch == "-":
            out.append("-")
    return "".join(out)


def consistency(scheme: str, stype: str, line_letters: list, end_pairs: list) -> list:
    """Where the Analyzer's answer contradicts ITSELF, by reading its own fields side by side (no sound is looked at, nothing is changed or worked out for it):
      * `scheme` is not the letters of its own `line_schemes`;
      * `scheme_type` names a pattern (AABB ...) that is not the pattern of its own `scheme`;
      * `scheme_type` says free / irregular but its own pair list has an exact end rhyme in this section;
      * two lines it paired as an exact end rhyme carry different letters.
    `line_letters` = [(line, letter or None)], `end_pairs` = [(line_a, word_a, line_b, word_b)] of the exact end-to-end pairs of this section."""
    out = []
    got = "".join(l for _n, l in line_letters if l)
    if scheme and got and _canon(scheme) != _canon(got):
        out.append(f"scheme {scheme} is not the letters of its own line map ({got})")
    if stype and stype.lower() not in _FREE_TYPES and re.fullmatch(r"[A-Za-z~-]{2,}", stype):
        base = scheme or got
        if base and _canon(stype) != _canon(base):
            out.append(f"scheme_type {stype} does not match its own scheme {base}")
    if stype and stype.lower() in ("free", "irregular", "none") and end_pairs:
        a = end_pairs[0]
        out.append(f"scheme_type is {stype} but it lists an exact end rhyme (L{a[0]} {a[1]} - L{a[2]} {a[3]})")
    by_line = dict(line_letters)
    for la, wa, lb, wb in end_pairs:
        x, y = by_line.get(la), by_line.get(lb)
        if x and y and x != y:
            out.append(f"it lists L{la} {wa} - L{lb} {wb} as an exact end rhyme but gives the lines the letters {x} and {y}")
    return out


def group_consistency(group_of: dict, conflicts: list, outside: list, end_pairs: list, soft_pairs: "list | None" = None) -> list:
    """Where the Analyzer's answer contradicts ITSELF, by reading its own fields side by side (no sound is looked at, nothing is changed or worked out for it):
      * a line is in two end groups (`conflicts`) or a group names a line that is not in the section (`outside`);
      * two lines it listed as an EXACT end rhyme in `pairs` are not in one end group.
    `group_of` = {line: group index}, `end_pairs` = [(line_a, word_a, line_b, word_b)] of the exact end-to-end pairs of this section."""
    out = []
    if conflicts:
        out.append("a line is in two end groups: " + ", ".join(f"L{n}" for n in sorted(set(conflicts))))
    if outside:
        out.append("an end group names a line that is not in this section: " + ", ".join(f"L{n}" for n in sorted(set(outside))))
    for la, wa, lb, wb in end_pairs:
        if group_of.get(la) is None or group_of.get(la) != group_of.get(lb):
            out.append(f"it lists L{la} {wa} - L{lb} {wb} as an exact end rhyme but they are not in one end group")
    for la, wa, lb, wb, rel in soft_pairs or []:
        if group_of.get(la) is not None and group_of.get(la) == group_of.get(lb):
            out.append(f"it lists L{la} {wa} - L{lb} {wb} as a {rel} rhyme but puts them in one (exact) end group")
    return out


def build_llm_evidence(llm: "dict | None", lyrics: str) -> "dict | None":
    """The Rhyme Analyzer's answer (`rhyme_v6.llm_data`, format-checked) laid out per section of THESE lyrics, or None when there is no answer.

    Everything about the SOUND is the Analyzer's, exactly as it wrote it: the ending words, how it wrote each ending sound, the END GROUPS (lines with the same end sound), every pair with its relation,
    the repetition list. The program only NAMES the groups A, B, C in line order for the page; it does not look at a sound, change a group's members, make a group or move a line. What is done:
      * which Analyzer section is which section of the lyrics is decided by the line ids it used (the section ids are only a fallback: a model counts them differently);
      * the end connections shown for a group are its own members, in order (words from `endings`); two members that end in the SAME word are listed as repetition, not as a rhyme;
      * a pair is shown once, under the section of its first word; a pair that quotes the SAME word twice is listed with the repetition; an exact end-to-end pair that is already a group is not shown twice;
      * a pair that points at a line that is not a sung line is left out and counted; a pair whose quoted word is not in its line is listed apart (`misplaced`);
      * `inconsistencies` lists where the answer contradicts itself (a line in two groups, an exact pair outside one group): shown as "Analyzer output inconsistent", never repaired;
      * lines in no group are listed in `unlettered` and get no letter.
    An older saved answer (letters per line, `line_schemes`) is laid out as it was written."""
    if not llm or not (llm.get("sections") or llm.get("pairs") or llm.get("endings")):
        return None
    from . import lyric_analysis as LA
    from . import rhyme_v6 as R6
    sung = LA.sung_lines(lyrics)
    raw = _sung_lines(lyrics)
    by_sec: dict = {}
    for ln in sung:
        by_sec.setdefault(ln.section, []).append(ln)
    sec_of = {x.file_line: idx for idx, lns in by_sec.items() for x in lns}
    ai = list(llm.get("sections") or [])
    ends, sounds = llm.get("endings") or {}, llm.get("sounds") or {}
    taken: set = set()
    left_out = int(llm.get("dropped_pairs") or 0)

    def ids_of(sd: dict) -> set:
        got = {int(x) for x in (sd.get("line_schemes") or {}) if str(x).isdigit()}
        for g in sd.get("end_groups") or []:
            got |= {int(x) for x in g}
        return got

    def match(idx: int, files: list) -> dict:
        """The Analyzer section that covers these lines: most line ids in common; the section id only when nothing overlaps."""
        best, score = None, 0
        for k, sd in enumerate(ai):
            if k in taken:
                continue
            n = len(ids_of(sd) & set(files))
            if n > score:
                best, score = k, n
        if best is None:
            for k, sd in enumerate(ai):
                if k not in taken and sd.get("section_id") == R6.section_id(idx):
                    best = k
                    break
        if best is None:
            return {}
        taken.add(best)
        return ai[best]

    def ref(p: dict, ends_flag: bool) -> dict:
        pos = find_word(raw[p["file_line"]], p["text"], ends_flag, p.get("occurrence"))
        return {"line": p["file_line"], "word": p["text"], "pos": _pos(pos), "_found": pos is not None}
    out = []
    for idx, lns in by_sec.items():
        files = [x.file_line for x in lns]
        sd = match(idx, files)
        rows = [x for x in lns if not _only_parens(raw.get(x.file_line, ""))]
        row_files = {x.file_line for x in rows}
        legacy = bool(sd) and not sd.get("end_groups") and bool(sd.get("line_schemes"))
        group_of: dict = {}
        outside: list = []
        if sd and not legacy:
            members = []
            for g in sd.get("end_groups") or []:
                one = [n for n in g if n in row_files]
                outside += [n for n in g if n not in row_files]
                if one:
                    members.append(one)
            order = sorted(range(len(members)), key=lambda k: min(members[k]))                  # A, B, C ... by the first line of each group, in line order (a name only)
            for rank, k in enumerate(order):
                for n in members[k]:
                    group_of[n] = rank
        ls = sd.get("line_schemes") or {} if legacy else {}
        lines = []
        for x in rows:
            word = ends.get(str(x.file_line), "")
            if legacy:
                given = ls.get(str(x.file_line), "")
                lab, near, reported = (given[:1].upper() if given[:1].isalpha() else None), given.endswith("~"), bool(given)
            else:
                g = group_of.get(x.file_line)
                lab, near, reported = (chr(65 + g % 26) if g is not None else None), False, g is not None
            lines.append({"line": x.file_line, "sung": x.num, "label": lab, "near": near, "reported": reported, "ending": word, "sound": sounds.get(str(x.file_line), ""),
                          "how": "ai" if reported else "", "pos": _pos(find_word(raw.get(x.file_line, ""), word, True)) if word else None})
        scheme, stype = (sd.get("scheme") or "", sd.get("scheme_type") or "") if legacy else ("", "")
        pairs, reps, lost, end_exact, soft_end = [], [], [], [], []
        seen_pair: set = set()
        seen_rep: set = set()

        def add_rep(a: dict, b: dict, role: str) -> None:
            key = frozenset({a["file_line"], b["file_line"]})
            if key in seen_rep or a["file_line"] == b["file_line"]:
                return
            seen_rep.add(key)
            reps.append({"a": ref(a, a.get("role") == "end"), "b": ref(b, b.get("role") == "end"), "kind": "repetition", "placement": "end" if role == "end" else "internal", "sound": "exact",
                         "verification": "analyzer", "confidence": "high", "why": "", "sound_type": ""})

        def pair_key(a: dict, b: dict) -> frozenset:
            return frozenset({(a["file_line"], _nfc(a["text"]).casefold()), (b["file_line"], _nfc(b["text"]).casefold())})
        by_group: dict = {}
        for x in lines:
            if x["label"] and not legacy:
                by_group.setdefault(group_of[x["line"]], []).append(x)
        for grp in by_group.values():                                       # the end connections of a group: its own members in order, with the Analyzer's words
            for a, b in zip(grp, grp[1:]):
                if not (a["ending"] and b["ending"]):
                    continue
                pa, pb = {"file_line": a["line"], "text": a["ending"], "role": "end"}, {"file_line": b["line"], "text": b["ending"], "role": "end"}
                if _nfc(a["ending"]).casefold() == _nfc(b["ending"]).casefold():
                    add_rep(pa, pb, "end")                                  # the same word again: repetition
                    continue
                seen_pair.add((pair_key(pa, pb), "exact"))
                pairs.append({"a": {"line": a["line"], "word": a["ending"], "pos": a["pos"]}, "b": {"line": b["line"], "word": b["ending"], "pos": b["pos"]}, "kind": "other", "placement": "end",
                              "sound": "exact", "verification": "analyzer", "confidence": "", "why": a["sound"] or "", "sound_type": "", "group": chr(65 + group_of[a["line"]] % 26), "derived": True})
        for p in llm.get("pairs") or []:
            a, b = p["a"], p["b"]
            if a["file_line"] not in raw or b["file_line"] not in raw:
                if sec_of.get(a["file_line"], sec_of.get(b["file_line"])) == idx or (a["file_line"] not in raw and b["file_line"] not in raw and idx == next(iter(by_sec), idx)):
                    left_out += 1                                             # points at a line that is not a sung line: a format error, left out and counted
                continue
            if sec_of.get(a["file_line"], sec_of.get(b["file_line"])) != idx:
                continue                                                  # shown once, under the section of its first word
            if _nfc(a["text"]).casefold() == _nfc(b["text"]).casefold():
                add_rep(a, b, "end" if a.get("role") == "end" and b.get("role") == "end" else "internal")   # the same word again: listed as repetition
                continue
            same = a["file_line"] == b["file_line"]
            ends_a, ends_b = a.get("role") == "end", b.get("role") == "end"
            cross = sec_of.get(a["file_line"]) != sec_of.get(b["file_line"])
            exact_end = p["relation"] == "exact" and ends_a and ends_b and not same and not cross
            if exact_end and not legacy:
                end_exact.append((a["file_line"], a["text"], b["file_line"], b["text"]))
                if group_of.get(a["file_line"]) is not None and group_of.get(a["file_line"]) == group_of.get(b["file_line"]):
                    continue                                              # already one end group: its connection is shown from the group
            key = (pair_key(a, b), p["relation"])
            if key in seen_pair:
                continue
            seen_pair.add(key)
            if p["relation"] != "exact" and ends_a and ends_b and not same and not cross and not legacy:
                soft_end.append((a["file_line"], a["text"], b["file_line"], b["text"], p["relation"]))
            place = "internal" if same else ("cross_section" if cross else ("end" if ends_a and ends_b else "end_to_internal"))
            ra, rb = ref(a, ends_a), ref(b, ends_b)
            item = {"a": {k: v for k, v in ra.items() if k != "_found"}, "b": {k: v for k, v in rb.items() if k != "_found"}, "kind": _KIND_OF.get(p["sound_type"], "other"), "placement": place,
                    "sound": p["relation"], "verification": "analyzer", "confidence": p["confidence"], "why": p["reason"], "sound_type": p["sound_type"], "group": p.get("group_id", "")}
            if not (ra["_found"] and rb["_found"]):
                lost.append(item)                                         # a quoted word that is not in the line it points to: a wrong line number or text. Listed apart, never as a rhyme connection
                continue
            pairs.append(item)
            if legacy and exact_end:
                end_exact.append((a["file_line"], a["text"], b["file_line"], b["text"]))
        for r in llm.get("repeats") or []:
            a, b = r["a"], r["b"]
            if a["file_line"] in raw and b["file_line"] in raw and sec_of.get(a["file_line"]) == idx:
                add_rep(a, b, "end" if a.get("role") == "end" else "internal")
        in_rep = {x for r in reps for x in (r["a"]["line"], r["b"]["line"])}
        for x in lines:
            x["repeat"] = x["line"] in in_rep
        if legacy:
            notes = consistency(scheme, stype, [(x["line"], x["label"]) for x in lines], end_exact)
        else:
            notes = group_consistency(group_of, list(sd.get("group_conflicts") or []), outside, end_exact, soft_end) if sd else []
        out.append({"name": lns[0].section_name or "", "first": min(files), "last": max(files),
                    "pattern": scheme or ("".join((x["label"] or "-") for x in lines) if any(x["label"] for x in lines) else ""),          # the groups named in line order (legacy: the Analyzer's own scheme string)
                    "scheme_name": stype, "scheme_unverified": False, "lines": lines, "pairs": pairs[:MAX_PAIRS], "repetitions": reps[:MAX_PAIRS], "not_confirmed": [], "rejected": [], "misplaced": lost[:MAX_PAIRS],
                    "reported": bool(sd), "letters_from": "ai" if legacy else "groups", "inconsistent": bool(notes), "inconsistencies": notes,
                    "group_conflicts": len(sd.get("group_conflicts") or []) if sd else 0, "unlettered": [x["line"] for x in lines if not x["reported"]] if sd else [],
                    "uncertain": [f"L{o['line']}: {o['text']}" for o in sd.get("observations") or []][:6]})
    cross_n = sum(1 for sc in out for p in sc["pairs"] if p["placement"] == "cross_section")
    return {"sections": out, "source": "llm", "notes": list(llm.get("notes") or []), "left_out": left_out, "cross_section_pairs": cross_n,
            "group_conflicts": sum(sc["group_conflicts"] for sc in out)} if out else None


def _only_parens(text: str) -> bool:
    """True for a line that holds nothing but parenthesised text, such as `(Hook)` or `(เฮ้!)`."""
    t = (text or "").strip()
    return bool(t) and not re.sub(r"\([^()]*\)", "", t).strip(_TRAIL)


def _pair_line(p: dict) -> str:
    a, b = p["a"], p["b"]
    where = f"L{a['line']} {a['word']} ↔ L{b['line']} {b['word']}" if a["line"] != b["line"] else f"L{a['line']} {a['word']} ↔ {b['word']} (inside the line)"
    if p["kind"] == "repetition":
        return f"{where}: repetition (the same word or syllable again)"
    return (f"{where}: {KIND_LABEL.get(p['kind'], 'Sound link')}, {PLACEMENT_LABEL.get(p['placement'], p['placement'])}, {p['sound']}, confidence {p.get('confidence') or 'medium'}"
            + (f" ({p['why']})" if p["why"] else ""))


def evidence_text(ev: "dict | None") -> str:
    """The Rhyme Analyzer's result as the Songwriter AI reads it, in its request. Facts only, exactly as the Analyzer gave them; the Songwriter judges them."""
    if not ev:
        return NO_EVIDENCE
    rows = ["RHYME ANALYZER RESULT (the Rhyme Analyzer AI's own reading of the sounds; the program only checked its format and did not change a pair, a letter or a scheme. Judge it; do not contradict it, "
            "do not add pairs. Letters: the same letter = the same end sound, `-` = rhymes with no other line, `~` = only a near rhyme):"]
    for sec in ev["sections"]:
        rows.append(f"- {sec['name'] or '(start)'} (lines {sec['first']}-{sec['last']}): scheme {sec['pattern'] or 'not reported'}" + (f" ({sec['scheme_name']})" if sec.get("scheme_name") else ""))
        rows.append("    line endings: " + "; ".join(f"L{x['line']} {x['ending'] or '?'} [" + (((x['label'] or '-') + ('~' if x.get('near') else '')) if x.get('reported') else '?') + "]" for x in sec["lines"]))
        if sec["pairs"]:
            rows.append("    pairs: " + " | ".join(_pair_line(p) for p in sec["pairs"]))
        if sec["repetitions"]:
            rows.append("    repetition (not a rhyme): " + " | ".join(_pair_line(p) for p in sec["repetitions"]))
        if sec["uncertain"]:
            rows.append("    uncertain: " + " | ".join(sec["uncertain"]))
        if sec.get("inconsistent"):
            rows.append("    ANALYZER OUTPUT INCONSISTENT (its own fields disagree; nothing was changed): " + "; ".join(sec.get("inconsistencies") or []) + ". Do not treat this scheme as settled.")
    left = int(ev.get("left_out") or 0)
    if left:
        rows.append(f"NOTE: {left} pair(s) of the Analyzer's answer could not be read (missing fields or a line that is not in the song) and are not in this list.")
    lost = sum(len(sec.get("misplaced") or []) for sec in ev["sections"])
    if lost:
        rows.append(f"NOTE: {lost} pair(s) named a word that is not in the line they point to; they were left out of this list.")
    for n in ev.get("notes") or []:
        rows.append("NOTE: " + n)
    return "\n".join(rows)


def section_problems(sec: dict) -> list:
    """What Python can see is wrong WITHIN the Analyzer's answer for one section, by comparing its own fields: no sound is judged and nothing is changed. A plain sentence for each:
      * the same end sound written under two different end groups (equal sounds belong to one group);
      * lines the answer put in no end group at all;
      * an exact end rhyme between two lines that sit in different end groups;
      * the disagreements the evidence builder already found."""
    out: list = []
    lines = [x for x in (sec.get("lines") or []) if x.get("line")]
    by_sound: dict = {}
    for x in lines:
        s = str(x.get("sound") or "").strip().lower()
        if s and x.get("label"):
            by_sound.setdefault(s, set()).add(x["label"])
    for s, labs in by_sound.items():
        if len(labs) > 1:
            out.append(f"the end sound `{s}` is written under different end groups ({', '.join(sorted(labs))}); lines with the same sound belong to one group")
    if sec.get("unlettered"):
        out.append("lines " + ", ".join(str(n) for n in sec["unlettered"]) + " are in no end group")
    label = {x["line"]: x.get("label") for x in lines}
    for p in sec.get("pairs") or []:
        if p.get("placement") == "end" and p.get("sound") == "exact" and not p.get("derived"):
            la, lb = label.get((p.get("a") or {}).get("line")), label.get((p.get("b") or {}).get("line"))
            if la and lb and la != lb:
                out.append(f"lines {p['a']['line']} and {p['b']['line']} are reported as an exact end rhyme but sit in different end groups ({la} and {lb})")
    for t in sec.get("inconsistencies") or []:
        if str(t) not in out:
            out.append(str(t))
    return out


def _kv_rows(block: list) -> list:
    rows, cur = [], None
    for line in block:
        if _LEGACY.match(line):
            cur = None                                              # a scheme, letters or pairs of the answer's own are skipped
            continue
        m = _KV.match(line)
        if m:
            cur = [m.group(1).lower(), m.group(2).strip()]
            rows.append(cur)
        elif cur is not None and line.strip():
            cur[1] = (cur[1] + " " + line.strip()).strip()
    return rows


def parse_blocks(blocks: list, lyrics: str, diagnostics: "list | None" = None) -> list:
    """The Songwriter's critique per section. A block whose lines are not sung lines of the song is left out with a note. The critique never carries pairs or letters: those come from the evidence."""
    diag = diagnostics if diagnostics is not None else []
    sung = _sung_lines(lyrics)
    out = []
    for block in blocks[:MAX_SECTIONS]:
        one = {k: v for k, v in _kv_rows(block)}
        name = one.get("section", "").strip()
        m = re.search(r"L?(\d+)\s*(?:-|–|to)\s*L?(\d+)", one.get("lines", "")) or re.search(r"(\d+)", one.get("lines", ""))          # `L2-L5` as well as `2-5`
        if not name or not m:
            diag.append("A rhyme section was left out: it has no name or no line numbers.")
            continue
        first, last = int(m.group(1)), int(m.group(2)) if m.lastindex == 2 else int(m.group(1))
        if not [n for n in sung if first <= n <= last]:
            diag.append(f"The rhyme section {name} was left out: lines {first}-{last} are not sung lines of the song.")
            continue
        rec = one.get("recommendation", "").strip()
        keep = rec in ("", "-", "none", "keep") or rec.lower().startswith("keep")
        dash = lambda v: "" if (v or "").strip() in ("", "-") else v.strip()
        issue = dash(one.get("issue", ""))
        cons = dash(one.get("consideration", "") or one.get("consider", ""))
        out.append({"name": name, "first": first, "last": last, "checks": dash(one.get("checks", "")), "quality": dash(one.get("quality", "")), "strength": dash(one.get("strength", "")), "issue": issue,
                    "consideration": cons, "rhyme": dash(one.get("rhyme", "")), "keep_reason": (dash(one.get("keep", "")) or dash(one.get("reason", ""))) if keep else "", "recommendation": "" if keep else rec, "keep": keep,
                    "reason": dash(one.get("reason", "")), "benefit": "" if keep else dash(one.get("benefit", "")),
                    "priority": "" if keep else PRIORITIES.get(one.get("priority", "").strip().lower(), "optional"), "limits": dash(one.get("limits", "")),          # no priority without a recommendation
                    "raw_priority": one.get("priority", "").strip().lower() if keep else ""})
    return out


def merge(evidence: "dict | None", critique: list) -> list:
    """The sections for the report page: each evidence section with the Songwriter's critique for it (matched by the lines it covers, then by name). A critique with no evidence section is kept on its own
    (no pairs, no letters); an evidence section with no critique is shown without one."""
    base = {"name": "", "first": 0, "last": 0, "checks": "", "quality": "", "strength": "", "issue": "", "consideration": "", "rhyme": "", "keep_reason": "", "recommendation": "", "keep": True, "reason": "",
            "benefit": "", "priority": "", "limits": "", "raw_priority": ""}
    critique = [dict(base, **{k: v for k, v in c.items() if k in base}) for c in critique or [] if isinstance(c, dict)]      # a report saved by an older version has fewer keys: every one has a default
    secs = []
    used = set()
    for sec in (evidence or {}).get("sections", []):
        match = None
        for i, c in enumerate(critique):
            if i in used:
                continue
            if c["first"] <= sec["last"] and sec["first"] <= c["last"] and (c["name"].casefold() == sec["name"].casefold() or abs(c["first"] - sec["first"]) <= 1):
                match = i
                break
        if match is None:
            for i, c in enumerate(critique):
                if i not in used and c["name"].casefold() == sec["name"].casefold():
                    match = i
                    break
        if match is not None:
            used.add(match)
        secs.append(dict(sec, critique=critique[match] if match is not None else None))
    for i, c in enumerate(critique):
        if i not in used:
            secs.append({"name": c["name"], "first": c["first"], "last": c["last"], "pattern": "", "scheme_name": "", "scheme_unverified": False, "lines": [], "pairs": [], "repetitions": [],
                         "not_confirmed": [], "rejected": [], "uncertain": [], "critique": c, "no_evidence": True})
    return secs


_LINE_KINDS = ("wording", "imagery", "filler", "language", "rhythm", "syllables", "rhyme", "clarity")
_REF = r"L0*(\d{1,4})"
_PAIR_CLAIM = re.compile(_REF + r"[^\n.;]{0,40}?(?:↔|<->|\s[–-]\s|สัมผัสกับ|rhymes? with|rhyming with)\s*[^\n.;L]{0,30}?" + _REF, re.IGNORECASE)


def quality_gate(flow: list, findings: list, evidence: "dict | None", summary: str = "") -> list:
    """A structural check of the Songwriter's report, with NO sound judgement and no extra request: notes about its shape, never a change to what it says and never a finding made up for it.
      * a priority (`important`, `worthwhile`, ...) with no recommendation (the priority was dropped by the parser);
      * a section of the Analyzer's result with no review, or a report with no section review at all;
      * a finding that is about lines but names none, and the same finding twice;
      * a rhyme between two lines named in the Songwriter's text that the Analyzer's result does not list (same end group, a pair, or repetition);
      * an empty summary."""
    notes = []
    for c in flow or []:
        if c.get("keep") and c.get("raw_priority") not in ("", "-", "none", None):          # the format says the priority is `-` without a recommendation: any other value (worthwhile, important, ...) is refused, not just `important`
            notes.append(f"Check: {c.get('name')}: priority {c.get('raw_priority')} with no recommendation (the priority was ignored).")
    secs = merge(evidence, flow) if evidence else []
    if evidence and not flow:
        notes.append("Check: the report has no section review.")
    for sec in secs:
        if sec.get("critique") is None and not sec.get("no_evidence"):
            notes.append(f"Check: no review for the section {sec.get('name') or '(start)'}.")
    seen = set()
    for f in findings or []:
        if getattr(f, "kind", "") in _LINE_KINDS and not getattr(f, "first", 0):
            notes.append(f"Check: a {f.kind} finding names no line ({(f.finding or '')[:40]}).")
        key = (getattr(f, "kind", ""), getattr(f, "first", 0), getattr(f, "last", 0), (getattr(f, "evidence", "") or "").strip())
        if key in seen:
            notes.append(f"Check: the same finding appears twice (lines {f.first}-{f.last}).")
        seen.add(key)
    if evidence:
        listed = set()
        for sec in evidence.get("sections", []):
            for p in list(sec.get("pairs") or []) + list(sec.get("repetitions") or []):
                listed.add(frozenset({p["a"]["line"], p["b"]["line"]}))
            by_label: dict = {}
            for x in sec.get("lines") or []:
                if x.get("label"):
                    by_label.setdefault(x["label"], []).append(x["line"])
            for grp in by_label.values():
                for i, a in enumerate(grp):
                    for b in grp[i + 1:]:
                        listed.add(frozenset({a, b}))
        said = set()
        for c in flow or []:
            for k in ("quality", "strength", "issue", "consideration", "rhyme", "recommendation", "reason"):
                for m in _PAIR_CLAIM.finditer(c.get(k) or ""):
                    a, b = int(m.group(1)), int(m.group(2))
                    if a != b and abs(a - b) > 0 and frozenset({a, b}) not in listed and (a, b) not in said:
                        said.add((a, b))
                        notes.append(f"Check: the Songwriter links L{a} and L{b} as a rhyme in {c.get('name')}; the Analyzer's result does not list that pair.")
    if not (summary or "").strip():
        notes.append("Check: the report has no summary.")
    return notes


def group_colour_index(label: "str | None", fallback: int = 0) -> int:
    """The palette index of a rhyme letter (A -> 0, B -> 1 ...), so the same letter has the same colour everywhere on the page."""
    return (ord(label) - 65) % 8 if label else fallback
