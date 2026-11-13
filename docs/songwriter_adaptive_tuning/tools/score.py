# -*- coding: utf-8 -*-
"""usage: score.py <cand> [<cand> ...]  -> per-check results (objective checks only) and a dimension table. Checks read the raw answers; they are evidence, not a verdict: the reading notes in the report are the judgement.
Dimensions: 1 natural Thai  2 meaning kept  3 rhyme/craft  4 independent judgment  5 creativity  6 conversational  7 tags/contract."""
import json, re, sys
from pathlib import Path

SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
sys.path.insert(0, str(SCR)); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
from tune_cases import CASES
from music_studio import chat_tags as CT

LY = {c["id"]: c["lyrics"] for c in CASES}
R = lambda p, s: bool(re.search(p, s))
CLICHE = r"เหงา|น้ำตา|ดาว|หัวใจ"


def tagged(cid, raw, lyrics=None):
    """The program's own checker (chat_tags.answer) on the answer: edits offered / rejected."""
    lyr = lyrics or LY[cid]
    n_lines = len(lyr.split(chr(10)))                       # the listing the model reads shows the empty last line as its own id (L021): an insert after it is read as 'after the last real line' for scoring; the parser itself refuses it (recorded as a separate issue)
    raw = re.sub(r'(after:\s*|after=")L0*%d\b' % n_lines, lambda m: m.group(1) + 'end', raw) if lyr.endswith(chr(10)) else raw
    o = CT.answer("songwriter", raw, lyr)
    return o


def quoted_ok(raw, lyrics):
    """share of quoted Thai phrases (3+ chars) in the answer that really occur in the lyrics: hallucinated 'quotes' lower it."""
    qs = [q for q in re.findall(r"[\"“”'‘’]([^\"“”'‘’\n]{3,60})[\"“”'‘’]", raw) if re.search(r"[\u0E00-\u0E7F]", q)]
    if not qs:
        return None
    flat = re.sub(r"\s+", "", lyrics)
    ok = sum(1 for q in qs if re.sub(r"\s+", "", q) in flat)
    return ok / len(qs)


def checks(rec_by_turn, cid):
    t1 = rec_by_turn.get(1, {}).get("raw", "")
    t2 = rec_by_turn.get(2, {}).get("raw", "")
    out = []  # (dimension, name, bool)
    add = lambda d, n, v: out.append((d, n, bool(v)))
    L = LY[cid]
    if cid == "A":
        add(2, "A no coined phrase (นัดสุ่ม/นัดคู่/แบบไม่รู้…)", not R(r"นัดแบบสุ่ม|นัดสุ่ม|นัดคู่|นัดแบบไม่รู้|นัดมืด|นัดแบบไม่", t1))
        add(1, "A offers a real everyday Thai idiom (ดูตัว/จับคู่/แนะนำ/ให้ได้เจอ…)", R(r"ดูตัว|จับคู่|แนะนำให้|พาไปเจอ|ให้ได้เจอ|ให้ได้รู้จัก|พาไปพบ|นัดเจอ|ลองคุยกัน", t1))
        add(4, "A states a pick", R(r"ผมเลือก|ผมชอบ|ผมว่า.{0,12}(ดีที่สุด|เหมาะ)|แนะนำ.{0,6}(อัน|แบบ|ข้อ)", t1))
        add(3, "A looks at syllables or rhyme", R(r"พยางค์|สัมผัส|ร้อง", t1))
    if cid == "B":
        add(4, "B1 no opening praise", not R(r"^\s*(\[REPLY\.1\]\s*)?[^\n]{0,40}(ดีมาก|ยอดเยี่ยม|เยี่ยม|สุดยอด)", t1))
        add(4, "B1 names a real weakness or risk", R(r"แต่|อาจ|เสี่ยง|ข้อเสีย|ระวัง|ติด|ยัง", t1) and R(r"อาจ|เสี่ยง|ข้อเสีย|ระวัง|น่าจะ.{0,10}(ปรับ|เปลี่ยน)|ลอง", t1))
        add(2, "B2 says the meaning/stance changes", R(r"ท่าที|ความหมาย.{0,20}(เปลี่ยน|ต่าง|ไป)|เปลี่ยน.{0,20}(ความหมาย|ท่าที|ความรู้สึก)|จาก.{0,30}เป็น.{0,10}(ยอมรับ|ไม่ปฏิเสธ|ปฏิเสธ)", t2))
        add(4, "B2 does not just agree (no 'ใช้ตามที่…เลย' / opening praise)", not R(r"ตามที่คุณต้องการเลย|ใช้ตามที่คุณ|ดีกว่า(เดิม)?มาก|ผมเห็นด้วย", t2))
        o2 = tagged(cid, t2) if t2 else None
        add(7, "B2 tag (if any) targets the discussed line L012", (o2 is None) or (not o2.edits) or (o2.edits[0].get("first") == 12))
    if cid in ("C", "H3"):
        qo = quoted_ok(t1, L)
        add(3, f"{cid} quotes only real lines (no invented lyrics)", qo is None or qo >= 0.8)
        add(7, f"{cid} read-only: no edit tag", "[EDIT" not in t1 and "change_lyric" not in t1)
        add(3, f"{cid} says which end words may be inserted for rhyme (hedged)", R(r"อาจ|น่าจะ|ดูเหมือน|อาจจะ|ไม่แน่", t1) and R(r"เติม|ฝืน|สร้อย|เพื่อสัมผัส|ใส่เข้ามา|ใส่เพื่อ", t1))
        add(6, f"{cid} no long structured dump (<2000 chars)", len(t1) < 2000)
    if cid == "C":
        add(3, "C names หนอ/นา/เอย as the likely inserted ones", R(r"หนอ", t1) and R(r"นา|เอย", t1))
    if cid == "D1":
        add(4, "D1 says the verses repeat / do not progress", R(r"วนซ้ำ|ซ้ำ|ไม่มีพัฒนา|ไม่เคลื่อน|นิ่ง|แบบเดิม|ไม่ได้ก้าว|ขาดการพัฒนา|ไม่พัฒนา|วนอยู่", t1))
        add(4, "D1 does not praise a strong spine", not R(r"แก่น(หลัก)?(เรื่อง)?(ของเพลง)?.{0,12}(แข็งแรง|ชัดเจนมาก)|แกนหลัก.{0,10}(แข็งแรง|แข็งแกร่ง)|แก่นเรื่องที่ชัดเจนมาก", t1))
        add(3, "D1 quotes real lines", (quoted_ok(t1, L) or 1) >= 0.7)
    if cid in ("D2",):
        add(4, "D2 does not demand a plot/telling", not R(r"ขาดการเล่าเรื่อง|ควรเล่าเรื่อง|ขาดเรื่องราว|ควรมีเหตุการณ์|บอกเล่า.{0,8}มากกว่า|ขยับจาก.{0,10}บรรยาย|ขาดการเคลื่อนไหว|ขาดการพัฒนา|ยังคงวนเวียน", t1))
        add(4, "D2 judges whether the images build one experience", R(r"ภาพ.{0,25}(ประกอบ|สร้าง|รวม|ส่งเสริม|โทน|บรรยากาศ|ประสบการณ์)", t1))
    if cid in ("E", "H4"):
        o = tagged(cid, t1)
        e = o.edits[0] if o.edits else None
        rep = (e or {}).get("replacement", "")
        body = [ln for ln in rep.split("\n") if ln.strip() and not ln.strip().startswith("[")]
        add(7, f"{cid} one clean insert tag the program accepts", bool(e) and not o.rejected)
        add(7, f"{cid} no stray >>> / บรรทัด n: / L0nn| in the lyric text", bool(rep) and not R(r">>>|บรรทัด\s*\d+\s*:|L\d{3}\|", rep))
        add(5, f"{cid} 4-6 lyric lines", 4 <= len(body) <= 6)
        add(5, f"{cid} avoids the clichés เหงา/น้ำตา/ดาว/หัวใจ", not R(CLICHE, rep))
        add(1, f"{cid} no filler tail just to rhyme (เธอ/เอย/หนา at 2+ line ends)", sum(1 for ln in body if R(r"(เธอ|เอย|หนา|หนอ)\s*$", ln)) < 2)
    if cid == "F":
        o = tagged(cid, t1)
        add(7, "F1 a valid change_lyric on L005 with the exact current line", bool(o.edits) and o.edits[0].get("first") == 5 and not o.rejected)
        add(6, "F1 short answer (<900 chars)", len(t1) < 900)
        add(7, "F2 cancel: no tag", "[EDIT" not in t2 and "change_lyric" not in t2)
        add(7, "F2 cancel: no claim of a restore", not R(r"คืนค่า|กู้คืน|ย้อนกลับให้|restored|แก้กลับ(ให้)?แล้ว", t2))
    if cid == "H1":
        add(1, "H1 offers 2+ everyday alternatives", len(re.findall(r"[\"“]([^\"”]{4,40})[\"”]", t1)) >= 2)
        add(4, "H1 states a pick with a reason", R(r"ผมเลือก|ผมชอบ|ผมว่า|แนะนำ", t1) and R(r"เพราะ|เนื่องจาก", t1))
        add(2, "H1 keeps meaning (not a stance change like ไม่ว่า/ไม่สน)", not R(r"ไม่สน|ไม่แคร์", t1))
        add(7, "H1 no edit tag before the user chooses", "[EDIT" not in t1)
    if cid == "H2":
        add(2, "H2 recognises the meaning flip (ฉันไม่เกลียด -> เขาไม่น่าเกลียด)", R(r"ความหมาย.{0,25}(เปลี่ยน|ต่าง|ไป)|เปลี่ยน.{0,20}(ความหมาย|ความรู้สึก|เรื่อง)|พลิก|ตรงข้าม|ตัดสิน.{0,10}(ตัว|รูป)|ว่าด้วยหน้าตา|รูปลักษณ์|หน้าตา|น่าเกลียด.{0,15}(หน้าตา|รูป)", t1))
        add(4, "H2 does not simply say the new line is better", not R(r"ดีกว่า.{0,6}ครับ|ฟังดูดีกว่า|น่าจะดีขึ้น", t1[:200]))
        add(7, "H2 no edit tag that applies the flip without warning", ("[EDIT" not in t1) or R(r"แต่|ข้อเสีย|เสีย", t1))
    return out


print_rows = []


def main():
    cands = sys.argv[1:]
    dims = {1: "Natural Thai", 2: "Meaning kept", 3: "Rhyme & craft", 4: "Independent judgment", 5: "Creativity", 6: "Conversational", 7: "Tags / contract"}
    summary = {}
    for name in cands:
        recs = {}
        for ln in (SCR / "tune_out" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            recs.setdefault(r["case"], {})[r["turn"]] = r
        res = []
        for c in CASES:
            if c["id"] in recs:
                for d, n, v in checks(recs[c["id"]], c["id"]):
                    res.append((c["id"], c["split"], d, n, v))
        summary[name] = res
    # per check table
    allnames = []
    for name in cands:
        for cid, sp, d, n, v in summary[name]:
            if (cid, n) not in [(a, b) for a, b, _ in allnames]:
                allnames.append((cid, n, sp))
    print("CHECKS (✔ passed)")
    for cid, n, sp in allnames:
        row = []
        for name in cands:
            m = [v for c2, s2, d2, n2, v in summary[name] if c2 == cid and n2 == n]
            row.append("✔" if m and m[0] else ("✘" if m else "·"))
        print(f"  {sp[0].upper()} {n[:84]:84s} " + "  ".join(f"{x}" for x in row))
    print("\nSCORE by split (checks passed / total)")
    for sp in ("tune", "holdout"):
        print(" ", sp, "  ".join(f"{name}: {sum(1 for r in summary[name] if r[1]==sp and r[4])}/{sum(1 for r in summary[name] if r[1]==sp)}" for name in cands))
    print("\nSCORE by dimension (all cases, checks passed / total)")
    for d, label in dims.items():
        print(f"  {d} {label:22s}", "  ".join(f"{name}: {sum(1 for r in summary[name] if r[2]==d and r[4])}/{sum(1 for r in summary[name] if r[2]==d)}" for name in cands))
    json.dump({n: [list(x) for x in summary[n]] for n in cands}, open(SCR / "tune_out" / ("scores_" + "_".join(cands) + ".json"), "w", encoding="utf-8"), ensure_ascii=False)


main()
