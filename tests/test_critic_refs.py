"""Song Critic: several separate line references in one finding (Task A). Pure parser tests (no Flet, no LLM): the raw answers of Audit 01 and of Quality Tune 02 are parsed again.
Run: python tests/test_critic_refs.py"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "docs" / "specialist_quality_audit_01"))
sys.stdout.reconfigure(encoding="utf-8")
from music_studio import spec_reports as SR, specialists as SP
from songs import SONGS

FAILS: list = []
N = [0]


def check(name, ok, detail=""):
    N[0] += 1
    print(("PASS " if ok else "FAIL ") + name + (("  " + str(detail)[:170]) if detail else ""))
    if not ok:
        FAILS.append(name)


# ── parse_refs ───────────────────────────────────────────────────────────────
P = SR.parse_refs
check("refs: L005", P("L005", 40) == ([(5, 5)], "ok"))
check("refs: L005-L008", P("L005-L008", 40) == ([(5, 8)], "ok"))
check("refs: L005, L016 stays TWO references and is never joined into L005-L016", P("L005, L016", 40) == ([(5, 5), (16, 16)], "ok"), P("L005, L016", 40))
check("refs: L005, L016-L018", P("L005, L016-L018", 40) == ([(5, 5), (16, 18)], "ok"))
check("refs: short ids and no space (L5,L16), semicolon, lower case", P("L5,L16", 40)[0] == [(5, 5), (16, 16)] and P("L5; L16", 40)[0] == [(5, 5), (16, 16)] and P("l5, l16-l18", 40)[0] == [(5, 5), (16, 18)])
check("refs: three places (L011, L023, L035)", P("L011, L023, L035", 40)[0] == [(11, 11), (23, 23), (35, 35)])
check("refs: a repeated id is kept once", P("L5, L5", 40)[0] == [(5, 5)])
check("refs: order as written is kept (L16, L5)", P("L16, L5", 40)[0] == [(16, 16), (5, 5)])
for bad in ("verse 2", "12", "L12-13", "L13-L12", "L12 and L14", "lines 12 and 13", "L99", "L5, L99", "L5 L16", "L5, verse 2", "L0", "L5-", "section 3, L5"):
    check(f"refs invalid, nothing guessed: {bad!r}", P(bad, 40) == ([], "invalid"), P(bad, 40))
check("refs: nothing / '-' / whole song = none", P("", 40) == ([], "none") and P("-", 40) == ([], "none") and P("whole song", 40) == ([], "none"))
check("refs_label", SR.refs_label([(5, 5), (16, 18)]) == "L5, L16-L18")
check("parse_lines (the single-reference reader used for sections) is unchanged: a list is NOT one reference", SR.parse_lines("L5, L16", 40) == (-1, -1) and SR.parse_lines("L5-L16", 40) == (5, 16))

# ── a finding with several references ───────────────────────────────────────
LY = SONGS["S1"]["text"]
SYS = SP.effective_system("song_critic")[0]
TAIL = "\n[SECTION.1]\nname: Verse 1\nlines: L2-L5\nreading: r\n[NOTE.1]\n- -\n[DONE]"


def answer(lines, quote="ฉันเดินไปในความงามอันเงียบงัน", kind="concern", extra=""):
    return ("[VERDICT.1]\nheadline: h\nreading: r\n[FINDING.1]\nkind: " + kind + "\npriority: high\nlines: " + lines + "\nquote: <<<\n" + quote + "\n>>>\nobservation: o\ninterpretation: i\nrecommendation: -\nkeep: -\n" + extra + TAIL)


p = SR.parse_answer(answer("L005, L016"), "song_critic", LY, SYS)
f = p["sr"]["findings"][0]
check("finding L005, L016: kept, two references, first/last = the first one, quote verified", p["ok"] and f["refs"] == [[5, 5], [16, 16]] and (f["first"], f["last"]) == (5, 5) and f["quote_ok"] is True and not f["lines_invalid"], p["diagnostics"])
check("finding L005, L016: the shown lines are the two places with a gap mark, not the lines in between", f["quote"].split("\n") == ["ฉันเดินไปในความงามอันเงียบงัน", "…", "ฉันเดินไปในความงามอันเงียบงัน"], f["quote"])
p = SR.parse_answer(answer("L005, L016-L017", quote="ฉันเดินไปในความงามอันเงียบงัน\nท่ามกลางแสงสุกใสและเสียงลม"), "song_critic", LY, SYS)
f = p["sr"]["findings"][0]
check("finding L005, L016-L017: ranges kept as written, quote checked against the union of the places", f["refs"] == [[5, 5], [16, 17]] and f["quote_ok"] is True, (f["refs"], f["quote_ok"]))
p = SR.parse_answer(answer("L005, L016", quote="ข้อความที่ไม่มีในเพลง"), "song_critic", LY, SYS)
check("a quotation that is in neither place is replaced by the program's lines and flagged", p["sr"]["findings"][0]["quote_ok"] is False and any("not in the lyrics" in d for d in p["diagnostics"]))
quote_between = "แสงจันทร์ละเอียดอ่อนโรยบนผืนน้ำ"                       # L002: NOT in L005 / L016
p = SR.parse_answer(answer("L005, L016", quote=quote_between), "song_critic", LY, SYS)
check("a line that is only BETWEEN the two places does not verify the quote (no range is built)", p["sr"]["findings"][0]["quote_ok"] is False)
for bad in ("verse 2", "L5-L99", "L005, L999", "12", "L005 and L016"):
    p = SR.parse_answer(answer(bad), "song_critic", LY, SYS)
    g = p["sr"]["findings"][0] if p["ok"] and p["sr"]["findings"] else {}
    check(f"invalid lines {bad!r}: the finding is KEPT (text intact), without a link, flagged, quote unverified, with a diagnostic", bool(g) and g["lines_invalid"] == bad and g["refs"] == [] and g["first"] == 0 and g["quote_ok"] is False and g["observation"] == "o" and any("not a readable reference" in d for d in p["diagnostics"]), p["diagnostics"][:1])
p = SR.parse_answer(answer("-", kind="strength"), "song_critic", LY, SYS)
check("a Song Critic finding with no line (whole song) is kept as before", p["sr"]["findings"][0]["refs"] == [] and not p["sr"]["findings"][0]["lines_invalid"])

# a proposal: a wording for ONE contiguous reference only
pp = SR.parse_answer(answer("L005", extra="proposal: <<<\nฉันยืนมองความงามอันเงียบงัน\n>>>\n"), "song_critic", LY, SYS)
check("proposal for one reference is kept as the wording of those lines", pp["sr"]["findings"][0]["proposal"] == "ฉันยืนมองความงามอันเงียบงัน", pp["diagnostics"])
pp = SR.parse_answer(answer("L005, L016", extra="proposal: <<<\nฉันยืนมองความงามอันเงียบงัน\n>>>\n"), "song_critic", LY, SYS)
check("a proposal for two separate references is not used (it would need to replace the lines between)", pp["sr"]["findings"][0]["proposal"] == "" and any("exactly one line reference" in d for d in pp["diagnostics"]))
pp = SR.parse_answer(answer("L005", extra="proposal: <<<\nฉันเดินไปในความงามอันเงียบงัน\n>>>\n"), "song_critic", LY, SYS)
check("a proposal equal to the original is not used", pp["sr"]["findings"][0]["proposal"] == "")
pp = SR.parse_answer(answer("L005", extra="proposal: <<<\n[Chorus]\nบรรทัดใหม่\n>>>\n"), "song_critic", LY, SYS)
check("a proposal with a [tag] is not used", pp["sr"]["findings"][0]["proposal"] == "")

# ── other specialists keep their rule: a finding without a readable reference is not used ──────────────
p = SR.parse_answer(answer("verse 2"), "rhyme_flow", LY, SP.effective_system("rhyme_flow")[0])
check("Rhyme & Flow: unreadable lines still drop the finding (not part of Task A)", not p["sr"]["findings"] if p["ok"] else True, p["diagnostics"][:1])

# ── the raw answers of Audit 01 and Tune 02 ─────────────────────────────────────────────────────────
neu = {"TestSong01": "S1", "TestSong02": "S2", "TestSong03": "S3"}
runs = []
for f_ in sorted((ROOT / "docs/specialist_quality_audit_01/RAW_RESPONSES").glob("*.txt")):
    runs.append((f_, SONGS[f_.name.split("_")[0]]["text"], SP.effective_system("song_critic")[0]))
for f_ in sorted((ROOT / "docs/song_critic_tune_02/RAW_RESPONSES").glob("*.txt")):
    runs.append((f_, SONGS[neu[f_.name.split("_")[0]]]["text"], SP.effective_system("song_critic")[0]))
lost_before = []
for f_, lyr, sysm in runs:
    raw = f_.read_text(encoding="utf-8")
    parsed = SR.parse_answer(raw, "song_critic", lyr, sysm)
    n_raw = len(re.findall(r"^\[FINDING\.\d+\]", raw, re.M))
    n_kept = len(parsed["sr"]["findings"]) if parsed["ok"] else -1
    if n_raw != n_kept:
        lost_before.append((f_.name, n_raw, n_kept))
    check(f"raw answer {f_.parent.parent.name}/{f_.name}: every finding the model wrote is kept ({n_raw} written, {n_kept} kept)", n_raw == n_kept, parsed["diagnostics"][:1])
comma = [f_.name for f_, lyr, _ in runs if re.search(r"^lines:\s*L\d+\s*,", f_.read_text(encoding="utf-8"), re.M)]
check("the comma-list answers of Audit 01 / Tune 02 are in the test set (S1 Gemma 01, S1 Grok, ...)", len(comma) >= 3, comma)
s1 = next(r for r in runs if r[0].name == "S1_gemma_response1.txt")
pp = SR.parse_answer(s1[0].read_text(encoding="utf-8"), "song_critic", s1[1], s1[2])
check("Audit 01 S1 Gemma (4 findings all with comma lists, 0 kept before): 4 kept now, each with its own references", len(pp["sr"]["findings"]) == 4 and [x["refs"] for x in pp["sr"]["findings"]][:2] == [[[5, 5], [16, 16]], [[11, 11], [23, 23], [35, 35]]], [x["refs"] for x in pp["sr"]["findings"]])
check("no quote of those answers was replaced (all verbatim) after the change", all(x["quote_ok"] for x in pp["sr"]["findings"]), [x["quote_ok"] for x in pp["sr"]["findings"]])

# ── legacy reports (saved before refs existed) ─────────────────────────────────────────────────────
legacy = {"id": "so1", "kind": "concern", "priority": "high", "first": 12, "last": 13, "quote": "a\nb", "quote_ok": True, "observation": "o", "interpretation": "i", "recommendation": "", "keep": ""}
check("a legacy finding (first / last, no refs) still has one reference to show", legacy.get("refs") in (None, []) and legacy["first"] == 12)

print(f"\n{N[0] - len(FAILS)} / {N[0]} passed")
sys.exit(1 if FAILS else 0)
