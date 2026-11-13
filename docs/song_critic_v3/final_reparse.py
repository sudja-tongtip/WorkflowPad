# -*- coding: utf-8 -*-
"""Re-parses the 10 raw candidate answers with the CURRENT parser (alias parser) and writes FINAL_REPARSE_RESULTS.md: the numbers the adoption rule needs (a)(d)(e) and the runtimes.
run.log / run_parser_v1.log were written by the FIRST parser (before the aliases) and are kept as history only."""
import hashlib, re, sys
from pathlib import Path
ROOT = Path("D:/AI/MyApps/WorkflowPad"); HERE = ROOT / "docs/song_critic_v3"
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "docs/specialist_quality_audit_01")); sys.path.insert(0, str(ROOT / "docs/song_critic_v2")); sys.path.insert(0, str(HERE))
from music_studio import spec_reports as SR
from songs import SONGS
from songs_s4 import S4
from songs_s5 import S5

songs = {"01": SONGS["S1"], "02": SONGS["S2"], "03": SONGS["S3"], "04": S4, "05": S5}
CAND = (HERE / "CANDIDATE_PROMPT.md").read_text(encoding="utf-8").replace("\r\n", "\n").rstrip()
parser_sha = hashlib.sha256((ROOT / "music_studio/spec_reports.py").read_bytes()).hexdigest()[:12]
log = {}
for l in (HERE / "run.log").read_text(encoding="utf-8").splitlines():
    m = re.match(r"(\S+) sec ([\d.]+)", l)
    if m:
        log[m.group(1)] = float(m.group(2))
rows, out = [], []
for n, s in songs.items():
    for be in ("gemma", "grok"):
        tag = f"TestSong{n}_{be}_candidate"
        raw = (HERE / "RAW_RESPONSES" / f"{tag}_response.txt").read_text(encoding="utf-8")
        parsed = SR.parse_answer(raw, "song_critic", s["text"], CAND)
        sr = parsed["sr"]; asp = sr["aspects"]
        keys = [a["aspect"] for a in asp]
        full = keys == list(SR.ASPECTS)
        heading_only = sum(1 for a in asp if not a["refs"] and not (a.get("observation") or a.get("interpretation")) and a["status"] in ("strength", "concern", "mixed"))
        qbad = sum(1 for a in asp if a.get("quote_ok") is False)
        lbad = sum(1 for a in asp if a.get("lines_invalid"))
        high = [a["aspect"] for a in asp if a.get("priority") == "high" and a["status"] in ("concern", "mixed")]
        rows.append((n, be, len(asp), full, heading_only, qbad, lbad, len(sr["actions"]), high, " ".join(f"{a['aspect'][:3]}:{a['status'][:4]}" for a in asp), log.get(tag), log.get(tag.replace("candidate", "baseline"))))
cells_full = sum(1 for r in rows if r[3])
mx = lambda be, i: sum(r[i] for r in rows if r[1] == be) / 5
md = ["# FINAL_REPARSE_RESULTS (Song Critic V3)", "",
      f"Final parser: `music_studio/spec_reports.py` sha256[:12] `{parser_sha}` (with `_ASPECT_ALIAS`). The 10 raw candidate answers were parsed again by `final_reparse.py`.",
      "`run.log` / `run_parser_v1.log` and the first version of QUALITY_COMPARISON.md used the FIRST parser (before the aliases): Gemma 6/5/6/6/7 aspects. **Those counts are superseded**; they stay in the package only as history.", "",
      "| song | backend | aspects | all 8 in order | heading-only | quote_ok=false | invalid lines | actions | high concern/mixed | statuses | s base | s cand |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
for r in rows:
    md.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} | {','.join(r[8]) or '-'} | {r[9]} | {r[11]} | {r[10]} |")
md += ["", f"**Condition (a), format and coverage:** all eight aspects, in order, parsed in **{cells_full} of 10** cells; heading-only aspects: max {max(r[4] for r in rows)} in any cell. -> **{'PASS' if cells_full >= 8 and max(r[4] for r in rows) <= 2 else 'FAIL'}**.",
       f"Condition (d) evidence: `quote_ok=false` in {sum(r[5] for r in rows)} Gemma/Grok aspects (Gemma {sum(r[5] for r in rows if r[1]=='gemma')}, Grok {sum(r[5] for r in rows if r[1]=='grok')}); invalid line references {sum(r[6] for r in rows)}. A flagged quote means the model's quote did not match the lines it cited (ill-formed citation, placeholder such as `(Full Song)`, or a paraphrase); it is NOT proof of invented lyrics.",
       f"Runtime (seconds, mean of 5): Gemma baseline {mx('gemma', 11):.2f} -> candidate {mx('gemma', 10):.2f} ({(mx('gemma', 10) / mx('gemma', 11) - 1) * 100:+.0f} %); Grok baseline {mx('grok', 11):.2f} -> candidate {mx('grok', 10):.2f} ({(mx('grok', 10) / mx('grok', 11) - 1) * 100:+.0f} %). Prompt size 5,847 -> 8,905 bytes (+52 %; bytes, not tokens)."]
(HERE / "FINAL_REPARSE_RESULTS.md").write_text("\n".join(md) + "\n", encoding="utf-8")
print("\n".join(md))
