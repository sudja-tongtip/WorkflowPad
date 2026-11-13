# -*- coding: utf-8 -*-
"""Mechanical checks and cost numbers for the Free-Thinking Two-Pass prototype (no judgment of song quality; that is read by a person from the raw answers).
Both arms, the same method on the whole answer text:
  * line ids: every mention like L12 -> the line must exist and have text; for a range L12-L13 the two ends must exist (a range may end on the blank separator line, as production's own structure does);
  * quoted lyric fragments: text between quotation marks (" ", ' ', “ ”, ‘ ’, « », 「 」) or inside <<< >>> that contains Thai and has at least 5 non-space characters (split at "..." / "…") -> must be found in the lyrics
    (whitespace-normalised, anywhere in the song). Fragments in the model's own words, English terms and paraphrases are not checkable and are not counted. Nothing is corrected.
Writes METRICS.json / METRICS.md and FINAL_CRITIQUES/*.md (the Pass 1 and final texts as answered, unedited)."""
import json, os, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = Path(os.environ.get("OUT", str(HERE)))
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "docs" / "specialist_quality_audit_01")); sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from songs import SONGS
from checks import numbered, norm

SONGS_USED = {"Test Song 01": SONGS["S1"], "Test Song 03": SONGS["S3"]}
THAI = re.compile("[฀-๿]")
QUOTE = re.compile(r'"([^"\n]{3,300})"|“([^”\n]{3,300})”|‘([^’\n]{3,300})’|«([^»\n]{3,300})»|「([^」\n]{3,300})」|<<<\s*(.+?)\s*>>>', re.S)
IDS = re.compile(r"(?<![A-Za-z0-9])L\s?0*(\d{1,3})(?:\s*[-–]\s*L?\s?0*(\d{1,3}))?(?![0-9])")


def check_text(raw, lyr, lyr_norm):
    ids, bad_ids, frags, bad_frags = 0, [], 0, []
    for m in IDS.finditer(raw):
        a = int(m.group(1)); b = int(m.group(2) or a)
        if b < a:
            a, b = b, a
        if m.group(2) is None:                              # a single id must be a line with text
            ids += 1
            if a not in lyr or not lyr[a].strip():
                bad_ids.append(f"L{a}")
        else:                                               # a range: its two ends must exist (production itself writes "Verse 1 L2-L6", the end being the blank separator line)
            ids += 2
            for n in (a, b):
                if n not in lyr:
                    bad_ids.append(f"L{n}")
    for m in QUOTE.finditer(raw):
        text = next(g for g in m.groups() if g is not None)
        for part in re.split(r"\.\.\.|…", text):
            if not THAI.search(part) or len(norm(part)) < 5:
                continue
            frags += 1
            # a fragment may carry several lines separated by newlines or slashes
            pieces = [p for p in re.split(r"[\n/|]", part) if len(norm(p)) >= 5] or [part]
            for p in pieces:
                if norm(p) not in lyr_norm:
                    bad_frags.append(p.strip()[:60])
    return {"line_ids": ids, "bad_line_ids": bad_ids, "quoted_fragments": frags, "bad_fragments": bad_frags}


def load(sub, tag):
    p = SRC / sub / tag
    return p.read_text(encoding="utf-8") if p.exists() else ""


M = {}
for name, song in SONGS_USED.items():
    lyr = numbered(song["text"]); lyr_norm = norm("".join(lyr.values())); base = name.replace(" ", "")
    for be in ("gemma", "grok"):
        key = f"{base}_{be}"
        cell = {}
        for arm, tags in (("A", [f"{key}_oneshot"]), ("B", [f"{key}_twopass_pass1", f"{key}_twopass_pass2"])):
            tot = {"requests": 0, "seconds": 0.0, "in_est": 0, "out_est": 0, "in_rep": 0, "out_rep": 0, "line_ids": 0, "bad_line_ids": [], "quoted_fragments": 0, "bad_fragments": [], "errors": [], "finish": [], "chars": 0, "per_request": []}
            for t in tags:
                rq = SRC / "RAW_REQUESTS" / f"{t}_request.json"
                if not rq.exists():
                    continue
                r = json.loads(rq.read_text(encoding="utf-8")); raw = load("RAW_RESPONSES", f"{t}_response.txt")
                c = check_text(raw, lyr, lyr_norm)
                u = r.get("usage_reported") or {}
                tot["requests"] += 1; tot["seconds"] += r.get("seconds", 0); tot["in_est"] += r.get("system_tokens_est", 0) + r.get("user_tokens_est", 0); tot["out_est"] += r.get("output_tokens_est", 0)
                tot["in_rep"] += u.get("prompt_tokens", 0); tot["out_rep"] += u.get("completion_tokens", 0); tot["chars"] += len(raw)
                tot["line_ids"] += c["line_ids"]; tot["bad_line_ids"] += c["bad_line_ids"]; tot["quoted_fragments"] += c["quoted_fragments"]; tot["bad_fragments"] += c["bad_fragments"]
                if r.get("error"):
                    tot["errors"].append(r["error"][:160])
                tot["finish"].append(r.get("finish_reason"))
                tot["per_request"].append({"tag": t, "seconds": r.get("seconds"), "reported_in": u.get("prompt_tokens"), "reported_out": u.get("completion_tokens"), "out_est": r.get("output_tokens_est"), "chars": len(raw), "session_id": r.get("session_id"), "session_used_est_after": r.get("session_used_est_after"), "finish": r.get("finish_reason")})
            tot["seconds"] = round(tot["seconds"], 1)
            cell[arm] = tot
        M[key] = cell
        # final critiques (unedited)
        (HERE / "FINAL_CRITIQUES").mkdir(exist_ok=True)
        a = load("RAW_RESPONSES", f"{key}_oneshot_response.txt"); p1 = load("RAW_RESPONSES", f"{key}_twopass_pass1_response.txt"); p2 = load("RAW_RESPONSES", f"{key}_twopass_pass2_response.txt")
        (HERE / "FINAL_CRITIQUES" / f"{key}.md").write_text(f"# {name} / {be}\n\n## A. One-shot (production) report, as answered\n\n{a}\n\n## B. Two-Pass, Pass 1 (free critique), as answered\n\n{p1}\n\n## B. Two-Pass, Pass 2 (final assessment), as answered\n\n{p2}\n", encoding="utf-8")
(HERE / "METRICS.json").write_text(json.dumps(M, ensure_ascii=False, indent=1), encoding="utf-8")
md = ["# METRICS (script output; no quality judgment)", "", "| cell | arm | requests | latency s | in tok (est) | out tok (est) | in/out tok (reported) | answer chars | line ids (bad) | quoted Thai fragments (not found) |", "|---|---|---|---|---|---|---|---|---|---|"]
for k, v in M.items():
    for arm, lab in (("A", "A one-shot"), ("B", "B two-pass")):
        t = v[arm]
        md.append(f"| {k} | {lab} | {t['requests']} | {t['seconds']} | {t['in_est']} | {t['out_est']} | {t['in_rep'] or '-'}/{t['out_rep'] or '-'} | {t['chars']} | {t['line_ids']} ({len(t['bad_line_ids'])}) | {t['quoted_fragments']} ({len(t['bad_fragments'])}) |")
md.append("")
for k, v in M.items():
    for arm in ("A", "B"):
        t = v[arm]
        if t["bad_line_ids"]:
            md.append(f"- {k} {arm} line ids that do not exist: {t['bad_line_ids']}")
        for f in t["bad_fragments"]:
            md.append(f"- {k} {arm} quoted fragment not found in the lyrics: {f}")
        if t["errors"]:
            md.append(f"- {k} {arm} errors: {t['errors']}")
        if any(x not in (None, "stop") for x in t["finish"]):
            md.append(f"- {k} {arm} finish reasons: {t['finish']}")
(HERE / "METRICS.md").write_text("\n".join(md) + "\n", encoding="utf-8")
print("\n".join(md))
