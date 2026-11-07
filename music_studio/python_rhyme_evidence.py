"""Python Rhyme Analysis as the rhyme evidence of Analyze Song (the Songwriter's report and the Rhyme & Flow page).

No AI is asked here. The evidence is laid out from the Shared Rhyme Map (`live_insights.rhyme_map` -> `rhyme_map.python_links`): the SAME map the rhyme colours use, so the Songwriter, the report page and the colours
read one source. Nothing in this module changes a sound rule; it only arranges what Python found and says honestly what Python cannot do.

The result has the shape `rhyme_flow.build_llm_evidence` returns (sections -> lines / pairs / repetitions / not_confirmed ...), plus `source: "python"`, so the report page and its cache keep working. A pair
is listed as a pair only when Python read both sounds; a repeated word is repetition; lines in sections that are not next to each other are listed with LOW confidence and say so; a pair that rests on a
spelling estimate (English) is "not confirmed". The text for the Songwriter (`evidence_text`) carries the limitations and never tells the Songwriter to believe the result without conditions.
"""
from __future__ import annotations

import unicodedata

from . import live_insights as LI
from . import lyric_analysis as LA
from . import rhyme_flow as RF
from . import rhyme_map as RM

SOURCE = "python"

LIMITATIONS = (
    "The rhyme results below were produced by Python with sound-analysis rules. They are information to help you decide, not conclusions that are always right.",
    "Python can miss internal rhymes, words of several syllables and words whose sound it cannot separate. It can also pair a repeated word, or words that are very far apart.",
    "English analysis is not reliable enough to confirm a rhyme by sound.",
    "Read the original lyrics together with this evidence. You may disagree with Python when you have a reason, but never invent a word or claim a rhyme pair that is not in the lyrics.",
    "If a pair is not in this evidence that does NOT mean the song has no rhyme there.",
    "Keep what can be checked (which words share a sound, where they are) apart from your opinion about musicality and artistic fit.",
)

CONFIDENCE_NOTE = {
    "high": "same section",
    "medium": "neighbouring sections",
    "low": "sections that are not next to each other: weak as a heard rhyme",
}


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "").strip()


def _lang(language: str) -> str:
    v = (language or "").strip()
    return "" if v.lower() == "auto" else v


def _conf(sec_a: int, sec_b: int) -> str:
    if sec_a == sec_b:
        return "high"
    return "medium" if abs(sec_a - sec_b) <= 1 else "low"


def build(lyrics: str, language: str = "", ins=None) -> "dict | None":
    """The rhyme evidence of `lyrics` from the Python rhyme map, or None when there are no sung lines. `ins`: an analysis of exactly these lyrics the caller already has."""
    lyrics = (lyrics or "").replace("\r\n", "\n")
    language = _lang(language)
    sung = LA.sung_lines(lyrics)
    raw = RF._sung_lines(lyrics)
    if not sung:
        return None
    ins = ins if ins is not None and ins.language == language else LI.analyze(lyrics, None, 0, language)
    m = LI.rhyme_map(lyrics, None, language, ins=ins)                # result=None: only the Python links, whatever AI result may be saved
    info = {x.line_number: x for x in ins.lines}
    sec_of = {x.file_line: x.section for x in sung}
    by_sec: dict = {}
    for ln in sung:
        by_sec.setdefault(ln.section, []).append(ln)
    raw_lines = lyrics.split("\n")
    off, pos = {}, 0
    for n, ln in enumerate(raw_lines, 1):
        off[n] = pos
        pos += len(ln) + 1

    def ref(w: "RM.Word") -> dict:
        o = off.get(w.file_line, 0)
        return {"line": w.file_line, "word": w.text, "pos": [w.start - o, w.end - o], "section": info[w.file_line].section if w.file_line in info else ""}

    pairs_of: dict = {}                    # section index -> lists
    cross = 0
    unconfirmed_en = False
    repeated_pairs: list = []
    end_pos: dict = {}                     # file line -> [start, end] of the ending word as Python coloured it
    for k in m.links:
        la, lb = k.a.file_line, k.b.file_line
        if la not in sec_of or lb not in sec_of:
            continue
        sa, sb = sec_of[la], sec_of[lb]
        bucket = pairs_of.setdefault(sa, {"pairs": [], "repetitions": [], "not_confirmed": []})
        a, b = ref(k.a), ref(k.b)
        same_word = RM._same_word(k.a.text, k.b.text)
        if same_word:
            bucket["repetitions"].append({"a": a, "b": b, "kind": "repetition", "placement": "end" if k.placement == "end" else "internal", "sound": "exact", "verification": SOURCE,
                                          "confidence": "high", "why": "the same word or syllable again: repetition, not a rhyme", "sound_type": "", "group": k.group})
            continue
        conf = _conf(sa, sb)
        placement = "internal" if la == lb else ("cross_section" if sa != sb else ("end" if k.placement == "end" else "end_to_internal"))
        if sa != sb:
            cross += 1
        why = k.reason or ""
        if conf == "low":
            why = (why + " · " if why else "") + "the lines are in sections that are not next to each other: weak as a heard rhyme"
        elif conf == "medium" and sa != sb:
            why = (why + " · " if why else "") + "across neighbouring sections"
        row = {"a": a, "b": b, "kind": "vowel", "placement": placement, "sound": k.sound, "verification": SOURCE, "confidence": conf, "why": why, "sound_type": "vowel_rhyme", "group": k.group}
        if k.colourable and k.verification == "verified":
            bucket["pairs"].append(row)
            if k.placement == "end" and la != lb:
                end_pos.setdefault(la, a["pos"])
                end_pos.setdefault(lb, b["pos"])
        else:
            if k.a.lang == RM.EN or k.b.lang == RM.EN:
                unconfirmed_en = True
            row["confidence"] = "low"
            row["why"] = "estimated from the spelling only: not confirmed" + (" · " + k.reason if k.reason else "")
            bucket["not_confirmed"].append(row)
    out = []
    for idx, lns in by_sec.items():
        files = [x.file_line for x in lns]
        rows_ = [x for x in lns if not RF._only_parens(raw.get(x.file_line, ""))]
        bucket = pairs_of.get(idx, {"pairs": [], "repetitions": [], "not_confirmed": []})
        # the rhyme letters: lines joined by an END pair of this section (exact first, a near pair adds a line to a letter that already exists)
        parent = {x.file_line: x.file_line for x in rows_}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        end_pairs = [p for p in bucket["pairs"] if p["placement"] == "end" and p["a"]["line"] in parent and p["b"]["line"] in parent]
        for p in end_pairs:
            if p["sound"] == "exact":
                parent[find(p["a"]["line"])] = find(p["b"]["line"])
        members: dict = {}
        for n in parent:
            members.setdefault(find(n), []).append(n)
        groups = sorted((sorted(g) for g in members.values() if len(g) >= 2), key=lambda g: g[0])
        letter = {}
        near_line = set()
        for i, g in enumerate(groups):
            for n in g:
                letter[n] = chr(65 + i % 26)
        for p in end_pairs:
            if p["sound"] == "near":
                a_, b_ = p["a"]["line"], p["b"]["line"]
                if a_ in letter and b_ not in letter:
                    letter[b_] = letter[a_]; near_line.add(b_)
                elif b_ in letter and a_ not in letter:
                    letter[a_] = letter[b_]; near_line.add(a_)
        rep_lines = {p["a"]["line"] for p in bucket["repetitions"]} | {p["b"]["line"] for p in bucket["repetitions"]}
        # the same ending word on two lines of the section is repetition too (python_links never links the same word)
        ends_seen: dict = {}
        for x in rows_:
            w = _nfc((info[x.file_line].end_sound or "").rpartition(" ")[0] if x.file_line in info else "")
            if w:
                ends_seen.setdefault(w.casefold(), []).append(x.file_line)
        for w, ls in ends_seen.items():
            for a_, b_ in zip(ls, ls[1:]):
                if not any({p["a"]["line"], p["b"]["line"]} == {a_, b_} for p in bucket["repetitions"]):
                    bucket["repetitions"].append({"a": {"line": a_, "word": w, "pos": None, "section": lns[0].section_name}, "b": {"line": b_, "word": w, "pos": None, "section": lns[0].section_name},
                                                  "kind": "repetition", "placement": "end", "sound": "exact", "verification": SOURCE, "confidence": "high",
                                                  "why": "the same ending word again: repetition, not a rhyme", "sound_type": "", "group": ""})
                    rep_lines |= {a_, b_}
        lines = []
        unread = []
        for x in rows_:
            li = info.get(x.file_line)
            es = (li.end_sound or "").strip() if li else ""
            word, _, rime = es.rpartition(" ")
            if not word or "+" not in rime:
                word, rime = es, ""
            if li is not None and li.rhyme_status == "unverified":
                unread.append(f"L{x.file_line}: Python could not read the sound of the line ending")
            lab = letter.get(x.file_line)
            lines.append({"line": x.file_line, "sung": x.num, "label": lab, "near": x.file_line in near_line, "reported": True, "ending": word, "sound": rime, "how": SOURCE,
                          "pos": end_pos.get(x.file_line), "repeat": x.file_line in rep_lines})
        pattern = "".join((y["label"] or "-") for y in lines) if any(y["label"] for y in lines) else ""
        out.append({"name": lns[0].section_name or "", "first": min(files), "last": max(files), "pattern": pattern, "scheme_name": "", "scheme_unverified": False, "lines": lines,
                    "pairs": bucket["pairs"][:RF.MAX_PAIRS], "repetitions": bucket["repetitions"][:RF.MAX_PAIRS], "not_confirmed": bucket["not_confirmed"][:RF.MAX_PAIRS], "rejected": [], "misplaced": [],
                    "reported": True, "letters_from": SOURCE, "inconsistent": False, "inconsistencies": [], "group_conflicts": 0, "unlettered": [], "uncertain": unread[:6]})
    limits = list(LIMITATIONS)
    n_unread = sum(len(s["uncertain"]) for s in out)
    facts = []
    if n_unread:
        facts.append(f"{n_unread} line ending(s) could not be read by Python (listed as `uncertain`).")
    if unconfirmed_en or any(RM.language_of(w) == RM.EN for w in (i.end_sound.rpartition(' ')[0] for i in ins.lines if i.end_sound)):
        facts.append("This song has English words: Python only estimates them from the spelling, so no English rhyme is confirmed.")
    return {"sections": out, "source": SOURCE, "notes": [], "left_out": 0, "cross_section_pairs": cross, "group_conflicts": 0, "limitations": limits, "facts": facts, "generated_by": "python rhyme_map.python_links"}


def _pair_line(p: dict) -> str:
    a, b = p["a"], p["b"]
    sa, sb = a.get("section") or "", b.get("section") or ""
    where = f"L{a['line']} {a['word']} ↔ L{b['line']} {b['word']}" if a["line"] != b["line"] else f"L{a['line']} {a['word']} ↔ {b['word']} (inside the line)"
    if sa and sb and sa != sb:
        where += f" ({sa} → {sb})"
    if p["kind"] == "repetition":
        return f"{where}: repetition (the same word or syllable again)"
    return (f"{where}: {RF.KIND_LABEL.get(p['kind'], 'Sound link')}, {RF.PLACEMENT_LABEL.get(p['placement'], p['placement'])}, {p['sound']}, confidence {p.get('confidence') or 'medium'}"
            + (f" ({p['why']})" if p.get("why") else ""))


def evidence_text(ev: "dict | None") -> str:
    """The Python Rhyme Analysis as the Songwriter AI reads it, in its request: the limitations first, then the facts per section. Facts, not orders: the Songwriter weighs them against the lyrics."""
    if not ev:
        return RF.NO_EVIDENCE
    rows = ["RHYME ANALYZER RESULT (PYTHON RHYME ANALYSIS: made by Python with sound-analysis rules from the spelling of the lyrics, not by an AI)",
            "LIMITATIONS OF THIS RESULT:"]
    rows += [f"- {t}" for t in (ev.get("limitations") or LIMITATIONS)]
    for t in ev.get("facts") or []:
        rows.append(f"- {t}")
    rows.append("Letters: the same letter = Python read the same end sound, `-` = Python found no partner for that line (not proof that it has none), `~` = only a near rhyme.")
    for sec in ev["sections"]:
        rows.append(f"- {sec['name'] or '(start)'} (lines {sec['first']}-{sec['last']}): scheme {sec['pattern'] or 'no end pair found by Python'}")
        rows.append("    line endings: " + "; ".join(f"L{x['line']} {x['ending'] or '?'} [" + (((x['label'] or '-') + ('~' if x.get('near') else '')) if x.get('ending') else '?') + "]" for x in sec["lines"]))
        if sec["pairs"]:
            rows.append("    pairs (sound read by Python): " + " | ".join(_pair_line(p) for p in sec["pairs"]))
        if sec["repetitions"]:
            rows.append("    repetition (not a rhyme): " + " | ".join(_pair_line(p) for p in sec["repetitions"]))
        if sec["not_confirmed"]:
            rows.append("    not confirmed (spelling estimate only, do not state as a rhyme): " + " | ".join(_pair_line(p) for p in sec["not_confirmed"]))
        if sec["uncertain"]:
            rows.append("    uncertain: " + " | ".join(sec["uncertain"]))
    return "\n".join(rows)
