# -*- coding: utf-8 -*-
"""Mechanical checks and cost numbers for the Sequential prototype (no judgment of song quality; that is read by a person from the raw answers).
For both arms: every evidence item (`lines:` + `quote: <<< >>>`) is checked the same way: the line ids exist, the quote is found (whitespace-normalised) in the cited lines. Nothing is corrected.
Writes PARSED_STEPS/*.json (steps, arm B), FINAL_REPORTS/*.md (the Step 9 report as parsed + the one-shot report as sent), METRICS.json and METRICS.md."""
import json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "docs" / "specialist_quality_audit_01")); sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from songs import SONGS

SONGS_USED = {"Test Song 01": SONGS["S1"], "Test Song 03": SONGS["S3"]}


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


def load(tag):
    p = HERE / "RAW_RESPONSES" / f"{tag}_response.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def req(tag):
    p = HERE / "RAW_REQUESTS" / f"{tag}_request.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def toks(r):
    u = r.get("usage_reported") or {}
    return {"in_est": r.get("system_tokens_est", 0) + r.get("user_tokens_est", 0), "out_est": r.get("output_tokens_est", 0), "in_reported": u.get("prompt_tokens"), "out_reported": u.get("completion_tokens"), "reasoning_reported": (u.get("completion_tokens_details") or {}).get("reasoning_tokens")}


M = {}
lines_md = []
for name, song in SONGS_USED.items():
    lyr = numbered(song["text"]); base = name.replace(" ", "")
    for be in ("gemma", "grok"):
        key = f"{base}_{be}"
        # --- arm A
        ra = load(f"{key}_oneshot"); qa = req(f"{key}_oneshot")
        ev_a = evidence_of(ra, ("FINDING", "ASPECT"))
        chk_a = [(t, n, *check_item(l, q, lyr)) for t, n, l, q in ev_a]
        # --- arm B
        steps, chk_b, secs, tin, tout, tin_r, tout_r, errors = [], [], 0.0, 0, 0, 0, 0, []
        for n in range(1, 10):
            r = req(f"{key}_seq_step{n}"); raw = load(f"{key}_seq_step{n}")
            if not r:
                continue
            bl = blocks(raw)
            ev = evidence_of(raw, ("EVIDENCE",))
            ck = [(f"S{n}", i, *check_item(l, q, lyr)) for _, i, l, q in ev]
            chk_b += ck
            t = toks(r); secs += r.get("seconds", 0); tin += t["in_est"]; tout += t["out_est"]
            if t["in_reported"]:
                tin_r += t["in_reported"]; tout_r += t["out_reported"] or 0
            if r.get("error"):
                errors.append(f"step {n}: {r['error'][:160]}")
            head = next((f for tg, _, f in bl if tg in ("STEP", "FINAL")), {})
            steps.append({"step": n, "name": r.get("step_name"), "seconds": r.get("seconds"), "tokens": t, "tags": [f"{tg}.{i}" for tg, i, _ in bl], "has_done": "[DONE]" in raw, "status": head.get("status"), "conclusion": head.get("conclusion") or head.get("headline"),
                          "evidence_checks": [{"tag": a, "n": b, "result": c, "why": d} for a, b, c, d in ck], "session_id": r.get("session_id"), "history": r.get("history"), "error": r.get("error")})
        (HERE / "PARSED_STEPS" / f"{key}_steps.json").write_text(json.dumps(steps, ensure_ascii=False, indent=1), encoding="utf-8")
        ta = toks(qa) if qa else {}
        M[key] = {"A": {"seconds": qa.get("seconds"), "tokens": ta, "evidence_items": len(chk_a), "unverified": sum(1 for c in chk_a if c[2] == "unverified"), "no_evidence": sum(1 for c in chk_a if c[2] == "no_evidence"),
                        "unverified_detail": [f"{c[0]}.{c[1]}: {c[3]}" for c in chk_a if c[2] == "unverified"], "error": qa.get("error")},
                  "B": {"requests": len(steps), "seconds": round(secs, 1), "in_est": tin, "out_est": tout, "in_reported": tin_r or None, "out_reported": tout_r or None, "evidence_items": len(chk_b), "unverified": sum(1 for c in chk_b if c[2] == "unverified"),
                        "no_evidence": sum(1 for c in chk_b if c[2] == "no_evidence"), "unverified_detail": [f"{c[0]}.{c[1]}: {c[3]}" for c in chk_b if c[2] == "unverified"], "errors": errors, "completed_9": len(steps) == 9 and not errors and all(s["has_done"] for s in steps)}}
        # final report file
        rb = load(f"{key}_seq_step9")
        (HERE / "FINAL_REPORTS" / f"{key}_B_final_step9.md").write_text(f"# {name} / {be}: Sequential Step 9 (as answered, unedited)\n\n```\n{rb}\n```\n", encoding="utf-8")
        (HERE / "FINAL_REPORTS" / f"{key}_A_oneshot.md").write_text(f"# {name} / {be}: One-shot report (as answered, unedited)\n\n```\n{ra}\n```\n", encoding="utf-8")
(HERE / "METRICS.json").write_text(json.dumps(M, ensure_ascii=False, indent=1), encoding="utf-8")
md = ["# METRICS (script output; no quality judgment)", "", "| cell | arm | requests | latency s | in tok (est) | out tok (est) | in/out tok (reported) | evidence items | unverified | rate |", "|---|---|---|---|---|---|---|---|---|---|"]
for k, v in M.items():
    a, b = v["A"], v["B"]
    ta = a["tokens"] or {}
    md.append(f"| {k} | A one-shot | 1 | {a['seconds']} | {ta.get('in_est')} | {ta.get('out_est')} | {ta.get('in_reported')}/{ta.get('out_reported')} | {a['evidence_items']} | {a['unverified']} | {a['unverified'] / a['evidence_items']:.0%} |" if a["evidence_items"] else f"| {k} | A one-shot | 1 | {a['seconds']} | {ta.get('in_est')} | {ta.get('out_est')} | {ta.get('in_reported')}/{ta.get('out_reported')} | 0 | 0 | - |")
    md.append(f"| {k} | B sequential | {b['requests']} | {b['seconds']} | {b['in_est']} | {b['out_est']} | {b['in_reported']}/{b['out_reported']} | {b['evidence_items']} | {b['unverified']} | {(b['unverified'] / b['evidence_items']):.0%} |" if b["evidence_items"] else f"| {k} | B sequential | {b['requests']} | {b['seconds']} | {b['in_est']} | {b['out_est']} | {b['in_reported']}/{b['out_reported']} | 0 | 0 | - |")
md.append("")
for k, v in M.items():
    for arm in ("A", "B"):
        for d in v[arm]["unverified_detail"]:
            md.append(f"- {k} {arm} unverified: {d}")
    if v["B"]["errors"]:
        md.append(f"- {k} B errors: {v['B']['errors']}")
(HERE / "METRICS.md").write_text("\n".join(md) + "\n", encoding="utf-8")
print("\n".join(md))
