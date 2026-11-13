# -*- coding: utf-8 -*-
"""Scores the Python-only links against the existing references (TP/FP/FN) and probes capability limits. NO LLM."""
import json, sys, importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
from music_studio import live_insights as LI, thai_rhyme as TR, rhyme_map as RM

spec = importlib.util.spec_from_file_location("songs", ROOT / "docs/analyze_song_audit/tools/songs.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
OUT = Path(__file__).resolve().parent
res = {}

# ---- 1. score vs reference ------------------------------------------------------------------------
def line_no(text_lines, t):
    return [i + 1 for i, l in enumerate(text_lines) if l.strip() == t.strip() or t.strip() in l]

for sid in ("S1", "S2", "S3"):
    s = m.SONGS[sid]
    lines = s["text"].split("\n")
    mp = LI.rhyme_map(s["text"], None, "th")
    end_links = [k for k in mp.links if k.colourable and k.placement == "end"]
    linked_line_pairs = {frozenset((k.a.file_line, k.b.file_line)) for k in end_links}
    r = {"true_pairs": [], "false_pairs": [], "other_python_links": []}
    for a, b in s["rhyme_true"]:
        la, lb = line_no(lines, a), line_no(lines, b)
        hit = any(frozenset((x, y)) in linked_line_pairs for x in la for y in lb)
        r["true_pairs"].append({"a": a, "b": b, "lines": [la, lb], "found_by_python": hit})
    for a, b in s["rhyme_false"]:
        la, lb = line_no(lines, a), line_no(lines, b)
        hit = any(frozenset((x, y)) in linked_line_pairs for x in la for y in lb)
        r["false_pairs"].append({"a": a, "b": b, "lines": [la, lb], "python_claims_rhyme": hit})
    tl = {frozenset((x, y)) for a, b in s["rhyme_true"] for x in line_no(lines, a) for y in line_no(lines, b)}
    fl = {frozenset((x, y)) for a, b in s["rhyme_false"] for x in line_no(lines, a) for y in line_no(lines, b)}
    for k in mp.links:
        key = frozenset((k.a.file_line, k.b.file_line))
        if key in tl or key in fl:
            continue
        r["other_python_links"].append(f"L{k.a.file_line}:{k.a.text} <-> L{k.b.file_line}:{k.b.text} ({k.sound} {k.placement})")
    r["TP"] = sum(x["found_by_python"] for x in r["true_pairs"])
    r["FN"] = len(r["true_pairs"]) - r["TP"]
    r["FP_vs_reference_false_pairs"] = sum(x["python_claims_rhyme"] for x in r["false_pairs"])
    r["TN"] = len(r["false_pairs"]) - r["FP_vs_reference_false_pairs"]
    res[sid] = r
    print(sid, {k: r[k] for k in ("TP", "FN", "FP_vs_reference_false_pairs", "TN")}, "| unreferenced links:", len(r["other_python_links"]))
    for x in r["other_python_links"]:
        print("    not in reference:", x)

# ---- 2. capability probes -------------------------------------------------------------------------
probe = {}
words = ["สบายใจ", "โอเค", "ไฟแดง", "เสมอ", "พยาน", "ปกติ", "อธิบาย", "กาแฟ", "คอมพิวเตอร์", "ความรัก", "สวัสดี", "ทรัพย์", "ยิ้ม", "เพลา", "กรรม", "บ้านเมือง", "เศร้า", "ดีไซน์", "เซลฟี่", "ฟีลลิ่ง"]
probe["final_syllable"] = {w: (TR.final_syllable(w), TR.describe_rime(w)) for w in words}
pairs = [("ใจ", "ไป"), ("ฝัน", "วัน"), ("รัก", "จัก"), ("รัก", "หลัก"), ("ตา", "มา"), ("ตาม", "ตาน"), ("กลม", "ลม"), ("เธอ", "เสมอ"), ("ดี", "ปกติ"), ("สาย", "ใจ"), ("ขาว", "เจ้า"), ("เศร้า", "เก่า"), ("พอ", "ก่อ"), ("ได้", "ไป"), ("เคย", "เอย"), ("โอเค", "เธอ"), ("คอมพิวเตอร์", "เธอ"), ("ฟีลลิ่ง", "ใจ")]
probe["thai_pairs"] = {f"{a}/{b}": TR.sound_relation(a, b)[0] for a, b in pairs}
en = [("night", "light"), ("love", "above"), ("love", "move"), ("though", "through"), ("read", "need"), ("heart", "apart"), ("day", "way"), ("fire", "desire"), ("cat", "bat"), ("tear", "fear"), ("bright", "write"), ("away", "stay"), ("blue", "you"), ("you", "through")]
probe["english_pairs(estimate_only)"] = {f"{a}/{b}": RM.en_relation(a, b)[0] for a, b in en}
# multi-syllable internal rhyme / mixed Thai-English line
tests = ["[Verse 1]\nฉันเดินเรื่อยเปื่อยเลื่อนลอยตามลม\nเธอยิ้มพริ้มพรายในใจฉันจม\n", "[Verse 1]\nI see the night, you hold me tight\nThe stars are bright tonight\n", "[Verse 1]\nเมื่อคืนฝันถึงเธอ night after night\nแสงไฟ light up my sight\n"]
probe["extra_lyrics"] = {}
for t in tests:
    mp = LI.rhyme_map(t, None, "th")
    probe["extra_lyrics"][t.split("\n", 1)[1][:40]] = [f"L{k.a.file_line}:{k.a.text}<->L{k.b.file_line}:{k.b.text} {k.sound} {k.placement} {k.verification} colourable={k.colourable}" for k in mp.links]
res["probe"] = probe
json.dump(res, open(OUT / "score_and_probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(probe, ensure_ascii=False, indent=1))
