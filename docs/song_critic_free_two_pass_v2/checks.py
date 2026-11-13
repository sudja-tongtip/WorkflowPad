# -*- coding: utf-8 -*-
"""Mechanical checks shared by the two-pass prototype (copied from the sequential prototype's evaluate.py; no judgment of song quality)."""
import re


def numbered(text):
    """{line number: text} with the same numbering the model saw (file line numbers, blank lines counted)."""
    return {i: l for i, l in enumerate(text.split("\n"), 1)}


def norm(s):
    return re.sub(r"\s+", "", s or "")


def refs(spec):
    out, bad = [], []
    for part in re.split(r"[,;]", spec or ""):
        part = part.strip()
        if not part or part == "-":
            continue
        m = re.fullmatch(r"L?0*(\d+)\s*(?:[-–]\s*L?0*(\d+))?(?:\s*\(.*\))?", part)
        if not m:
            bad.append(part); continue
        a = int(m.group(1)); b = int(m.group(2) or a)
        out.append((a, b)) if a <= b else bad.append(part)
    return out, bad


def check_item(lines_spec, quote, lyr):
    """-> (status, why). status: ok | unverified | no_evidence"""
    if (lines_spec or "-").strip() == "-":
        return "no_evidence", "whole song / none cited"
    rs, bad = refs(lines_spec)
    if bad or not rs:
        return "unverified", f"unreadable line reference: {bad or lines_spec}"
    cited = []
    for a, b in rs:
        for n in range(a, b + 1):
            if n not in lyr or not lyr[n].strip():
                return "unverified", f"line id L{n:03d} does not exist or is empty"
            cited.append(lyr[n])
    q = (quote or "").strip()
    if not q or q == "-":
        return "no_evidence", "lines cited, no quote"
    blob = norm("".join(cited))
    for ql in [x for x in q.split("\n") if x.strip()]:
        if norm(ql) not in blob:
            return "unverified", f"quote line not found in the cited lines: {ql.strip()[:50]}"
    return "ok", ""


def blocks(raw):
    """Numbered Clear Tag blocks: [(TAG, n, {field: value}), ...]; `<<< ... >>>` is one value."""
    out = []
    cur = None
    quote_mode = False
    key = None
    for line in raw.split("\n"):
        m = re.match(r"^\[([A-Z]+)\.(\d+)\]\s*$", line.strip())
        if m and not quote_mode:
            cur = (m.group(1), int(m.group(2)), {}); out.append(cur); key = None; continue
        if line.strip().startswith("[DONE]"):                 # Gemma ends the answer with "[DONE]<end_turn>" (a leaked end token): still the end
            break
        if cur is None:
            continue
        if quote_mode:
            if line.strip().startswith(">>>"):
                quote_mode = False
            else:
                cur[2]["quote"] = (cur[2].get("quote", "") + ("\n" if cur[2].get("quote") else "") + line.strip())
            continue
        m2 = re.match(r"^([a-z_0-9]+):\s*(.*)$", line.strip())
        if m2:
            key = m2.group(1)
            if key == "quote" and m2.group(2).strip().startswith("<<<"):
                quote_mode = True; cur[2]["quote"] = ""; rest = m2.group(2).strip()[3:].strip()
                if rest:
                    cur[2]["quote"] = rest
                continue
            cur[2][key] = m2.group(2).strip()
        elif key and line.strip():
            cur[2][key] = (cur[2].get(key, "") + " " + line.strip()).strip()
    return out


def evidence_of(raw, tag_kinds):
    """(lines, quote) pairs of an answer: [EVIDENCE.n] blocks, and for the one-shot report the FINDING / ASPECT blocks (they carry lines + quote themselves)."""
    res = []
    for t, n, f in blocks(raw):
        if t in tag_kinds and ("lines" in f):
            res.append((t, n, f.get("lines", ""), f.get("quote", "")))
    return res


