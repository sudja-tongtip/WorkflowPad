# -*- coding: utf-8 -*-
"""Rhyme Analyzer Hybrid V2 - PROTOTYPE (separate from production; imports production modules read-only, changes none of them).

   Python engine  ->  ONE whole-song LLM review request  ->  Python merge & validation  ->  evidence (Songwriter / UI shape)

Nothing here writes lyrics, creates edit actions, touches AppData or calls any LLM by itself (the runner passes the `ask` function).
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from music_studio import live_insights as LI          # noqa: E402
from music_studio import rhyme_map as RM               # noqa: E402
from music_studio import thai_rhyme as TR              # noqa: E402

VERIFIED, CANDIDATE, REJECTED, DISPUTED = "verified", "candidate", "rejected", "disputed"


# ───────────────────────────── 1. Python engine ─────────────────────────────
def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "").strip()


def python_engine(lyrics: str, language: str = "th") -> dict:
    """The Python rhyme analysis of the whole song: lines, sections, line endings, pairs (from rhyme_map.python_links) with position, sound, scope and confidence.
    Repetition (the same word / syllable again) is separated from rhymes. Nothing is hard-coded to a song."""
    t0 = time.perf_counter()
    ins = LI.analyze(lyrics, None, 0, language)
    raw_lines = lyrics.split("\n")
    off, pos = {}, 0
    for n, ln in enumerate(raw_lines, 1):
        off[n] = pos
        pos += len(ln) + 1
    lines = {}
    sections = []                                   # [(index, name, [file_lines])]
    for x in ins.lines:
        lines[x.line_number] = {"line": x.line_number, "sung": x.sung_number, "sec": x.sec_index, "section": x.section, "text": x.text, "ending": x.end_sound_word if hasattr(x, "end_sound_word") else "",
                                "rime": x.rhyme_group or "", "status": x.rhyme_status}
    for n, ln in lines.items():
        ln["ending"] = ""
    for x in ins.lines:
        es = (x.end_sound or "").strip()
        word, _, rime = es.rpartition(" ")
        if not word or "+" not in rime:
            word, rime = es, ""
        lines[x.line_number]["ending"] = word
        lines[x.line_number]["end_rime"] = rime
    by_sec = {}
    for n, ln in lines.items():
        by_sec.setdefault(ln["sec"], {"name": ln["section"], "lines": []})["lines"].append(n)
    sections = [{"index": k, "name": v["name"], "lines": v["lines"]} for k, v in sorted(by_sec.items())]

    def info(w):
        ln = lines.get(w.file_line, {})
        return {"line": w.file_line, "word": w.text, "start": w.start, "end": w.end, "sec": ln.get("sec"), "sung": ln.get("sung")}

    def scope(a, b):
        if a["sec"] == b["sec"]:
            return "same_section", "high"
        return ("adjacent_section", "medium") if abs((a["sec"] or 0) - (b["sec"] or 0)) <= 1 else ("distant", "low")

    pairs, reps = [], []
    for k in RM.merge(ins.rhyme_links, []):          # the same de-duplication the production map applies to the Python links
        a, b = info(k.a), info(k.b)
        sc, conf = scope(a, b)
        same_line = a["line"] == b["line"]
        end_a = a["end"] - off[a["line"]] >= len(raw_lines[a["line"] - 1].rstrip())
        end_b = b["end"] - off[b["line"]] >= len(raw_lines[b["line"] - 1].rstrip())
        placement = "internal" if same_line else ("end_to_end" if end_a and end_b else "end_to_internal" if (end_a or end_b) else "cross_line")
        rec = {"a": a, "b": b, "sound": k.sound, "placement": placement, "scope": sc, "confidence": conf, "language": k.a.lang, "sound_verified": k.verification == "verified",
               "rime": k.group, "detail": k.reason, "kind": "rhyme"}
        if _nfc(k.a.text) == _nfc(k.b.text):
            rec["kind"] = "repetition"
            reps.append(rec)
        else:
            pairs.append(rec)
    pairs.sort(key=lambda r: (min(r["a"]["start"], r["b"]["start"]), max(r["a"]["start"], r["b"]["start"])))
    for i, r in enumerate(pairs, 1):
        r["id"] = f"P{i}"
    for i, r in enumerate(reps, 1):
        r["id"] = f"R{i}"
    unread = [{"line": n, "ending": ln["ending"], "id": f"U{j}"} for j, (n, ln) in enumerate(((n, l) for n, l in lines.items() if l["status"] == "unverified"), 1)]
    return {"ins": ins, "lines": lines, "sections": sections, "pairs": pairs, "repetitions": reps, "unread": unread, "offsets": off, "raw_lines": raw_lines, "lyrics": lyrics,
            "seconds": round(time.perf_counter() - t0, 3),
            "limits": ["only the LAST syllable of a word is read; words it cannot cut (U list) stay unread, not wrong",
                       "internal rhyme: only single-syllable words (a dictionary compound such as เรื่อยเปื่อย is one token and is not split)",
                       "English is a spelling estimate and is never verified",
                       "a pair joined only by a distant section is marked low confidence, not removed"]}


# ───────────────────────────── 2. Reviewer prompt ─────────────────────────────
REVIEW_SYSTEM = """You are the Rhyme Reviewer of a lyric tool. A Python program has already analysed the rhymes of the WHOLE song from the spelling of the words. You do not start from zero: you CHECK its work and complete it.
Your job is limited to rhyme sounds. Do not judge the quality of the song, do not suggest changes to the lyrics, do not rewrite anything.

WHAT YOU RECEIVE
- The original lyrics, every sung line with its L number (the file line). Section tags are headers.
- Python's line endings (the last word of each line, the sound Python read as vowel+final, and whether Python found a partner).
- Python's pairs P1, P2 ... (exact or near, with the two words and lines), Python's repetitions R1 ... (the same word again, NOT a rhyme) and the endings Python could not read U1 ...

WHAT YOU DO (the whole song, in this one answer)
1. REVIEW every Python pair P: agree, wrong (not a rhyme when SUNG: say why), or unsure. Think of how the words are pronounced in the context of the line (Thai words with more than one reading, words of several syllables, final consonants, vowel length).
2. ADD rhymes Python missed: line endings that rhyme, rhymes INSIDE a line, an ending that rhymes with a word inside another line, words of several syllables, and rhymes Python could not read (U). Each added pair needs two words that are written in the lyrics EXACTLY as you copy them, with the line numbers.
3. DISPUTE where you disagree with how Python read or classified something: the sound of a word, exact versus near, or Python counted a repeated word as a rhyme (or a rhyme as a repeat). Say what Python says and what you say.
4. Confirm each section with a SECTION tag so the program knows you looked at all of them.
Never invent a word: every quoted word must be in the lyrics. A repeated word (the same word or the same syllable again) is repetition, not a rhyme: do not ADD it. Do not add a pair only because the first consonants are the same; this tool counts the vowel and the final sound.
Keep every reason to one short sentence.

OUTPUT FORMAT: numbered clear tags only. No JSON, no XML, no code fence, no text outside the tags. Each tag type has ONE counter for the whole answer: it starts at 1 and only goes up. The answer ends with [DONE].

[REVIEW.1]
pair: P1
verdict: agree | wrong | unsure
reason: one short sentence

[ADD.1]
relation: exact | near
placement: end_to_end | end_to_internal | internal | cross_line | cross_section
confidence: high | medium | low
reason: one short sentence (name the shared vowel and final sound)
L12|ลม|end
L14|ชม|end

[DISPUTE.1]
target: P2 | R1 | U1
python_says: one short phrase
you_say: one short phrase
reason: one short sentence

[SECTION.1]
name: the section name exactly as in the lyrics
checked: yes
note: one short sentence, or -

Endpoint lines are `L<number>|<word as written in that line>|<end or inside>`. Use `end` when the word is the last word of its line, `inside` otherwise. The pair of an ADD may be in one line (internal) or in two lines.
Write one [REVIEW] for every P. Write a [SECTION] for every section. Use [ADD] and [DISPUTE] only when needed (no tag is fine). Finish with [DONE]."""


def build_review_request(eng: dict) -> str:
    L = eng["lines"]
    rows = ["SONG LANGUAGE: Thai", "", "LYRICS (original, complete):"]
    for n, raw in enumerate(eng["raw_lines"], 1):
        if n in L:
            rows.append(f"L{n} | {raw}")
        elif raw.strip():
            rows.append(raw.strip())
    rows += ["", "PYTHON ANALYSIS (spelling rules; it can miss rhymes and cannot read some words)", "", "Line endings (last word, sound Python read as vowel+final, partner):"]
    for n, ln in L.items():
        sound = ln.get("end_rime") or "unread"
        partner = "rhymes with another line" if ln["status"] == "rhymed" else ("could not read" if ln["status"] == "unverified" else "no partner found")
        rows.append(f"L{n} {ln['ending'] or '?'} [{sound}] {partner}")
    rows += ["", "Python pairs:"]
    if eng["pairs"]:
        for p in eng["pairs"]:
            a, b = p["a"], p["b"]
            where = f"L{a['line']} {a['word']} <-> L{b['line']} {b['word']}" if a["line"] != b["line"] else f"L{a['line']} {a['word']} <-> {b['word']} (inside the line)"
            rows.append(f"{p['id']} | {p['sound']} | {p['placement']} | {where} | {p['rime']} | {p['scope']}")
    else:
        rows.append("(none)")
    rows += ["", "Python repetitions (the same word again, not rhymes):"]
    rows += [f"{r['id']} | L{r['a']['line']} {r['a']['word']} / L{r['b']['line']} {r['b']['word']}" for r in eng["repetitions"]] or ["(none)"]
    rows += ["", "Endings Python could not read:"]
    rows += [f"{u['id']} | L{u['line']} {u['ending']}" for u in eng["unread"]] or ["(none)"]
    secs = ", ".join(f"{s['name']} (L{min(s['lines'])}-L{max(s['lines'])})" for s in eng["sections"])
    rows += ["", f"SECTIONS to confirm ({len(eng['sections'])}): {secs}", "", "Review the whole song now."]
    return "\n".join(rows)


# ───────────────────────────── 3. Reviewer answer parser (clear-tag grammar) ─────────────────────────────
_TAG = re.compile(r"^\[([A-Z][A-Z0-9]*)(?:\.(\d+))?\]\s*$")
_KV = re.compile(r"^([A-Za-z_]+):\s*(.*)$")
_EP = re.compile(r"^[Ll]?\s*(\d{1,5})\s*\|\s*(.+?)\s*(?:\|\s*([A-Za-z_]+))?\s*$")
_TURN = re.compile(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$")
_KINDS = ("REVIEW", "ADD", "DISPUTE", "SECTION")


def parse_review(raw: str) -> dict:
    """Syntax only. A bad block is kept as an issue and does not erase the others. Same grammar as the program's clear tags: whole-line [TYPE.n], `key: value` fields, one `[DONE]`."""
    out = {"review": [], "add": [], "dispute": [], "section": [], "issues": [], "done": False, "preamble": ""}
    text = _TURN.sub("", raw or "").strip()
    lines = text.splitlines()
    if lines and re.fullmatch(r"\s*```[A-Za-z]*\s*", lines[0]) and re.fullmatch(r"\s*```\s*", lines[-1]):
        lines = lines[1:-1]
    cur, buf, seen = None, [], set()

    def close():
        nonlocal cur, buf
        if cur is None:
            return
        kind, num = cur
        fields, eps = {}, []
        for ln in buf:
            s = ln.strip()
            if not s:
                continue
            m = _EP.match(s) if "|" in s and not _KV.match(s) else None
            if m:
                eps.append({"line": int(m.group(1)), "word": m.group(2), "role": (m.group(3) or "").lower()})
                continue
            m = _KV.match(s)
            if m:
                fields[m.group(1).lower()] = m.group(2).strip()
        out[kind.lower()].append({"n": num, "fields": fields, "endpoints": eps})
        cur, buf = None, []
    pre = []
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        m = _TAG.match(s)
        if m and (m.group(1) == "DONE" or m.group(2)):
            close()
            if m.group(1) == "DONE":
                out["done"] = True
                break
            kind, num = m.group(1), int(m.group(2))
            if kind not in _KINDS:
                out["issues"].append(f"[{kind}.{num}] is not a tag the reviewer may use (ignored)")
                cur = None
                continue
            if (kind, num) in seen:
                out["issues"].append(f"[{kind}.{num}] repeated; the first is kept")
                cur = None
                continue
            seen.add((kind, num))
            cur, buf = (kind, num), []
        elif cur is not None:
            buf.append(ln)
        else:
            pre.append(ln)
    close()
    out["preamble"] = "\n".join(pre).strip()
    if not out["done"]:
        out["issues"].append("no [DONE] line: complete tags above were still read")
    if out["preamble"]:
        out["issues"].append("text before the first tag was ignored")
    return out


# ───────────────────────────── 4. Merge & validation ─────────────────────────────
def _scope(eng, la, lb):
    sa, sb = eng["lines"][la]["sec"], eng["lines"][lb]["sec"]
    if sa == sb:
        return "same_section", "high"
    return ("adjacent_section", "medium") if abs(sa - sb) <= 1 else ("distant", "low")


def locate(eng: dict, line: int, word: str, role: str) -> tuple:
    """(start, end, problem) of `word` in file line `line` (absolute offsets). problem is '' when the word is really there, whole, and not in a tag."""
    if line not in eng["lines"]:
        return None, None, "line is not a sung line"
    raw = eng["raw_lines"][line - 1]
    masked = RM.mask(raw)
    w = _nfc(word)
    if not w:
        return None, None, "empty word"
    hits, i = [], masked.find(w)
    while i >= 0:
        hits.append(i)
        i = masked.find(w, i + 1)
    if not hits:
        return None, None, "word is not in this line"
    if TR.has_thai(w):
        st = TR.placement(masked.strip(), w)
        # fragment = the quoted piece is only part of a syllable/word (placement() looks at the stripped line)
        if st["best"] == "fragment":
            return None, None, "quoted text is a fragment of a syllable"
    else:
        hits = [h for h in hits if (h == 0 or not masked[h - 1].isalpha()) and (h + len(w) >= len(masked) or not masked[h + len(w)].isalpha())]
        if not hits:
            return None, None, "quoted text is only part of a word"
    stripped_end = len(masked.rstrip())
    ends = [h for h in hits if h + len(w) == stripped_end or masked[h + len(w):].strip(" .,!?…\"'”’") == ""]
    if role == "end":
        if not ends:
            return None, None, "word is not at the end of the line"
        h = ends[-1]
    elif role == "inside":
        ins_ = [h for h in hits if h not in ends] or hits
        h = ins_[0]
        if len(ins_) > 1:
            pass                                   # first occurrence; the position is the reviewer's, noted below
    else:
        h = ends[-1] if ends else hits[0]
    if len(hits) > 1 and role not in ("end",):
        return eng["offsets"][line] + h, eng["offsets"][line] + h + len(w), "ambiguous"
    return eng["offsets"][line] + h, eng["offsets"][line] + h + len(w), ""


def _pkey(la, ea, lb, eb):
    return frozenset({(la, ea), (lb, eb)})


def merge(eng: dict, review: "dict | None", reviewer_status: str = "ok") -> dict:
    """Final evidence. Python sound checks decide verified; the reviewer can only move a pair to disputed, propose candidates, or add pairs that Python itself then checks."""
    t0 = time.perf_counter()
    items, disagreements, blocked = [], [], []
    idx = {}
    for p in eng["pairs"]:
        it = {"id": p["id"], "status": VERIFIED if p["sound_verified"] else CANDIDATE, "sound": p["sound"], "placement": p["placement"], "scope": p["scope"], "confidence": p["confidence"],
              "a": p["a"], "b": p["b"], "source": "python", "rime": p["rime"], "python_detail": p["detail"], "llm": None, "notes": []}
        if not p["sound_verified"]:
            it["notes"].append("Python estimated this from spelling only (not verified)")
        items.append(it)
        idx[p["id"]] = it
    for r in eng["repetitions"]:
        items.append({"id": r["id"], "status": REJECTED, "reason_code": "repetition", "sound": r["sound"], "placement": r["placement"], "scope": r["scope"], "confidence": "high", "a": r["a"], "b": r["b"],
                      "source": "python", "rime": r["rime"], "python_detail": r["detail"], "llm": None, "notes": ["the same word again: repetition, not a rhyme"]})
        idx[r["id"]] = items[-1]
    seen = {_pkey(i["a"]["line"], i["a"]["end"], i["b"]["line"], i["b"]["end"]): i for i in items}
    rev_cov = {"reviewed": 0, "python_pairs": len(eng["pairs"]), "sections_checked": 0, "sections": len(eng["sections"]), "unknown_refs": 0}
    if review:
        reviewed = set()
        for r in review["review"]:
            f = r["fields"]
            pid = _nfc(f.get("pair", "")).upper()
            it = idx.get(pid)
            if it is None:
                rev_cov["unknown_refs"] += 1
                blocked.append({"kind": "review_unknown_pair", "ref": pid})
                continue
            v = f.get("verdict", "").strip().lower()
            reviewed.add(pid)
            it["llm"] = {"verdict": v, "reason": f.get("reason", "")}
            if it["id"].startswith("R"):
                if v == "agree":
                    disagreements.append({"target": pid, "type": "repetition_called_rhyme", "python": "repetition", "llm": "rhyme", "reason": f.get("reason", ""), "resolved": "kept as repetition (Python rule)"})
                continue
            if v == "wrong" and it["status"] == VERIFIED:
                it["status"] = DISPUTED
                it["notes"].append("Python sound check passed, the reviewer says it is not a rhyme when sung")
                disagreements.append({"target": pid, "type": "reviewer_rejects_python_pair", "python": f"{it['sound']} {it['rime']}", "llm": "not a rhyme", "reason": f.get("reason", ""), "resolved": "disputed (not decided)"})
            elif v == "wrong":
                it["notes"].append("reviewer also doubts this estimate")
        rev_cov["reviewed"] = len(reviewed & {p["id"] for p in eng["pairs"]})
        for d in review["dispute"]:
            f = d["fields"]
            tgt = _nfc(f.get("target", "")).upper()
            it = idx.get(tgt)
            rec = {"target": tgt, "type": "dispute", "python": f.get("python_says", ""), "llm": f.get("you_say", ""), "reason": f.get("reason", "")}
            if it is not None and it["id"].startswith("P") and it["status"] == VERIFIED:
                it["status"] = DISPUTED
                it["notes"].append("reviewer disputes how Python read or classified this pair")
                rec["resolved"] = "disputed (not decided)"
            elif it is None and not re.fullmatch(r"U\d+", tgt):
                rec["resolved"] = "unknown target, kept for debug only"
                blocked.append({"kind": "dispute_unknown_target", "ref": tgt})
            else:
                rec["resolved"] = "kept for debug only"
            disagreements.append(rec)
        for a in review["add"]:
            f, eps = a["fields"], a["endpoints"]
            tag = f"ADD.{a['n']}"
            if len(eps) != 2:
                blocked.append({"kind": "add_bad_endpoints", "ref": tag, "why": f"{len(eps)} endpoints"})
                continue
            pos = []
            bad = ""
            for ep in eps:
                s, e, prob = locate(eng, ep["line"], ep["word"], ep["role"])
                if s is None:
                    bad = f"L{ep['line']} '{ep['word']}': {prob}"
                    break
                pos.append((ep, s, e, prob))
            if bad:
                blocked.append({"kind": "hallucination_or_bad_position", "ref": tag, "why": bad, "claimed": [(e["line"], e["word"]) for e in eps]})
                items.append({"id": tag, "status": REJECTED, "reason_code": "position", "sound": f.get("relation", ""), "placement": f.get("placement", ""), "scope": "", "confidence": "", "source": "llm",
                              "a": {"line": eps[0]["line"], "word": eps[0]["word"]}, "b": {"line": eps[1]["line"], "word": eps[1]["word"]}, "llm": {"claimed": f.get("relation", ""), "reason": f.get("reason", ""),
                              "confidence": f.get("confidence", "")}, "notes": [bad]})
                continue
            (e1, s1, en1, p1), (e2, s2, en2, p2) = pos
            if s1 == s2 and e1["line"] == e2["line"]:
                blocked.append({"kind": "add_same_word", "ref": tag})
                continue
            la, lb = e1["line"], e2["line"]
            mk = lambda ep, s, en: {"line": ep["line"], "word": ep["word"], "start": s, "end": en, "sec": eng["lines"][ep["line"]]["sec"], "sung": eng["lines"][ep["line"]]["sung"]}
            A, B = mk(e1, s1, en1), mk(e2, s2, en2)
            if (B["start"], B["line"]) < (A["start"], A["line"]):
                A, B = B, A
            key = _pkey(A["line"], A["end"], B["line"], B["end"])
            if key in seen:
                ex = seen[key]
                ex["notes"].append(f"{tag} proposed the same pair (duplicate merged)")
                ex["llm"] = ex["llm"] or {"verdict": "agree", "reason": f.get("reason", "")}
                continue
            sc, conf = _scope(eng, A["line"], B["line"])
            rel = RM.relation(A["word"], B["word"], "th")
            same_line = A["line"] == B["line"]
            if rel.kind == "repetition":
                st, rc, note = REJECTED, "repetition", "same word or same last syllable again: repetition, not a rhyme"
            elif rel.verification == "verified" and rel.sound in ("exact", "near") and rel.kind == "vowel":
                st, rc, note = VERIFIED, "", "Python sound check: " + rel.detail
            elif rel.verification == "verified":
                st, rc, note = REJECTED, "sound", ("Python sound check says not a vowel rhyme: " + rel.detail)
            else:
                st, rc, note = CANDIDATE, "", "Python could not read this pair: " + rel.detail
            ends = [ep["role"] == "end" for ep in (e1, e2)]
            placement = "internal" if same_line else ("end_to_end" if all(ends) else "end_to_internal" if any(ends) else "cross_line")
            it = {"id": tag, "status": st, "sound": rel.sound if rel.verification == "verified" else f.get("relation", ""), "placement": placement, "scope": sc, "confidence": conf if st == VERIFIED else (f.get("confidence", "") or "low"),
                  "a": A, "b": B, "source": "llm", "rime": rel.group, "python_detail": rel.detail, "llm": {"claimed": f.get("relation", ""), "reason": f.get("reason", ""), "confidence": f.get("confidence", "")}, "notes": [note]}
            if rc:
                it["reason_code"] = rc
            if f.get("relation") in ("exact", "near") and rel.verification == "verified" and rel.sound in ("exact", "near") and rel.sound != f.get("relation"):
                it["notes"].append(f"the reviewer said {f.get('relation')}, Python says {rel.sound}")
                disagreements.append({"target": tag, "type": "relation_differs", "python": rel.sound, "llm": f.get("relation"), "reason": f.get("reason", ""), "resolved": "Python's reading used"})
            if st == REJECTED:
                disagreements.append({"target": tag, "type": "added_pair_rejected", "python": note, "llm": f.get("relation", ""), "reason": f.get("reason", ""), "resolved": "rejected"})
            if p1 == "ambiguous" or p2 == "ambiguous":
                it["notes"].append("the word occurs more than once in a line: first occurrence used")
                if it["status"] == VERIFIED:
                    it["status"] = CANDIDATE
            items.append(it)
            seen[key] = it
        names = {_nfc(s["name"]).lower() for s in eng["sections"]}
        got = set()
        for s in review["section"]:
            nm = _nfc(s["fields"].get("name", "")).lower()
            if nm in names and s["fields"].get("checked", "").strip().lower().startswith("y"):
                got.add(nm)
        rev_cov["sections_checked"] = len(got)
        rev_cov["sections"] = len(names)                      # section NAMES (a repeated Chorus has one name, so it counts once)
    # a pair far from the other (distant sections) stays verified by sound but is flagged in evidence
    for it in items:
        if it["status"] == VERIFIED and it.get("scope") == "distant":
            it["notes"].append("sound matches but the lines are in sections that are not next to each other: low confidence, not offered as a rhyme scheme")
    items.sort(key=lambda i: (i["a"].get("start", 10**9), i["id"]))
    return {"items": items, "disagreements": disagreements, "blocked": blocked, "coverage": rev_cov, "reviewer_status": reviewer_status, "seconds": round(time.perf_counter() - t0, 3)}


def python_only(eng: dict) -> dict:
    return merge(eng, None, "python_only")


# ───────────────────────────── 5. Evidence in the Songwriter / UI shape ─────────────────────────────
def to_evidence(eng: dict, merged: dict) -> dict:
    """The merged result in the shape `rhyme_flow.build_llm_evidence` returns (sections -> lines/pairs/repetitions/not_confirmed/rejected), so `rhyme_flow.evidence_text` and the page code can read it.
    Verified (non-distant) pairs -> `pairs`; candidate / disputed / distant -> `not_confirmed` (with their status); rejected repetition -> `repetitions`; other rejected -> `rejected`, which the Songwriter
    text never prints as a rhyme."""
    def ref(w):
        return {"line": w["line"], "word": w["word"], "pos": None}
    out = []
    labels = {}
    for sec in eng["sections"]:
        lines = []
        for n in sec["lines"]:
            ln = eng["lines"][n]
            lines.append({"line": n, "sung": ln["sung"], "label": None, "near": False, "reported": ln["status"] == "rhymed", "ending": ln["ending"], "sound": ln.get("end_rime", ""), "how": "python", "pos": None, "repeat": False})
        out.append({"name": sec["name"], "first": min(sec["lines"]), "last": max(sec["lines"]), "pattern": "", "scheme_name": "", "scheme_unverified": False, "lines": lines, "pairs": [], "repetitions": [],
                    "not_confirmed": [], "rejected": [], "misplaced": [], "reported": True, "letters_from": "python", "inconsistent": False, "inconsistencies": [], "group_conflicts": 0, "unlettered": [], "uncertain": []})
    sec_of = {n: s for s in out for n in range(s["first"], s["last"] + 1)}
    for it in merged["items"]:
        sec = sec_of.get(it["a"]["line"])
        if sec is None:
            continue
        row = {"a": ref(it["a"]), "b": ref(it["b"]), "kind": "repetition" if it.get("reason_code") == "repetition" else "vowel", "placement": it["placement"], "sound": it["sound"], "verification": it["status"],
               "confidence": it["confidence"], "why": "; ".join(it["notes"]), "sound_type": "vowel_rhyme", "group": it.get("rime", ""), "status": it["status"], "source": it["source"], "id": it["id"]}
        if it["status"] == VERIFIED and it.get("scope") != "distant":
            sec["pairs"].append(row)
        elif it["status"] in (CANDIDATE, DISPUTED) or (it["status"] == VERIFIED):
            sec["not_confirmed"].append(row)
        elif it.get("reason_code") == "repetition":
            sec["repetitions"].append(row)
        else:
            sec["rejected"].append(row)
    return {"sections": out, "source": "hybrid-v2", "notes": [f"reviewer: {merged['reviewer_status']}"], "left_out": 0, "cross_section_pairs": 0, "group_conflicts": 0}


def evidence_text_v2(ev: dict) -> str:
    """What the Songwriter would read: verified pairs first, then candidate / disputed apart. Rejected pairs are never listed as rhymes (only counted). A NEW wording is needed because the production
    `evidence_text` calls everything 'the Analyzer's own reading'; this prototype does not touch it."""
    rows = ["RHYME EVIDENCE (Python sound check first, then a whole-song AI review checked by Python. VERIFIED = Python confirmed the sound. CANDIDATE = proposed but Python could not confirm. "
            "DISPUTED = Python confirmed the sound but the reviewer says it is not a rhyme when sung. Do not present candidate or disputed pairs as settled rhymes.)"]
    for sec in ev["sections"]:
        v = [p for p in sec["pairs"]]
        rows.append(f"- {sec['name']} (lines {sec['first']}-{sec['last']})")
        if v:
            rows.append("    VERIFIED: " + " | ".join(_row(p) for p in v))
        nc = [p for p in sec["not_confirmed"]]
        for tag in ("candidate", "disputed", "verified"):
            sel = [p for p in nc if p["status"] == tag]
            if sel:
                label = {"verified": "VERIFIED BUT DISTANT (low confidence)"}.get(tag, tag.upper())
                rows.append(f"    {label}: " + " | ".join(_row(p) for p in sel))
        if sec["repetitions"]:
            rows.append("    REPETITION (not a rhyme): " + " | ".join(_row(p) for p in sec["repetitions"]))
        if not (v or nc or sec["repetitions"]):
            rows.append("    no rhyme pair confirmed in this section")
    n_rej = sum(len(s["rejected"]) for s in ev["sections"])
    if n_rej:
        rows.append(f"NOTE: {n_rej} proposed pair(s) were rejected by the checks and are not evidence.")
    return "\n".join(rows)


def _row(p: dict) -> str:
    a, b = p["a"], p["b"]
    where = f"L{a['line']} {a['word']} <-> L{b['line']} {b['word']}" if a["line"] != b["line"] else f"L{a['line']} {a['word']} <-> {b['word']} (inside the line)"
    return f"{where} [{p['sound']}, {p['placement']}, {p['confidence'] or '-'}]"
