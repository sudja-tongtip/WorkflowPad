# -*- coding: utf-8 -*-
"""Deterministic checks of the Analyze Song audit runs against the hand-made reference answers (songs.py). usage: score_audit.py  -> audit/metrics.json + printed tables.
Nothing here asks an LLM. Every number can be re-derived from out/*.json (the raw runs) and songs.py (the references)."""
import json, re, sys
from pathlib import Path

SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
AUD = SCR / "audit"
sys.path.insert(0, str(AUD)); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
from songs import SONGS
from music_studio import live_insights as LI

REFNO = re.compile(r"L(\d{1,3})(?:\s*[-–]\s*L?(\d{1,3}))?|บรรทัด(?:ที่)?\s*(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?")
QUOTE = re.compile(r"[“\"']([^“”\"'\n]{3,80})[”\"']")
SYL = re.compile(r"(\d{1,2})\s*พยางค์")
AUDIO = re.compile(r"ฟังแล้ว|เมื่อฟัง|ได้ยินเสียงร้อง|จากเสียงที่(ได้ยิน|บันทึก)|เสียงร้องที่ได้ยิน|(after|when) listening|I heard|ฟังเทค")
THAI_ONLY = re.compile(r"[\u0E00-\u0E7F]")


def texts_of(rep: dict) -> dict:
    """field name -> text, for every free-text field of a report."""
    out = {"summary": rep.get("summary") or ""}
    for i, s in enumerate(rep.get("strengths") or []):
        out[f"strength{i}"] = s
    for i, s in enumerate(rep.get("uncertain") or []):
        out[f"uncertain{i}"] = s
    for i, f in enumerate(rep.get("findings") or []):
        for k in ("finding", "reason", "suggestion", "tradeoff", "meaning", "rhyme_effect", "syllables"):
            if f.get(k):
                out[f"finding{i}.{k}"] = str(f[k])
    for i, sec in enumerate((rep.get("data") or {}).get("rhyme_flow") or []):
        for k in ("checks", "quality", "strength", "issue", "consideration", "consider", "rhyme", "keep_reason", "recommendation", "reason", "benefit", "limits"):
            if sec.get(k):
                out[f"flow{i}.{k}"] = str(sec[k])
    return out


def line_map(lyrics: str) -> dict:
    return {i + 1: ln for i, ln in enumerate(lyrics.split("\n"))}


def run_metrics(rec: dict, song: dict) -> dict:
    lyrics = rec["lyrics"]
    lines = line_map(lyrics)
    n = len([1 for k in lines if True])
    flat = re.sub(r"\s+", "", lyrics)
    m = {"seconds": rec["seconds"], "requests": rec["requests"], "error": rec["error"], "lyrics_unchanged": lyrics == song["text"]}
    # ---- program facts: the Python syllable table against the hand count ----
    ins = LI.analyze(lyrics, {})
    prog = {l.line_number: l.syllable_count for l in ins.lines}
    pstat = {l.line_number: l.status for l in ins.lines}
    ok = bad = 0
    bad_verified = 0
    detail = []
    for text, want in song["anchors"].items():
        ln = next((k for k, v in lines.items() if v.strip() == text), None)
        got = prog.get(ln)
        if got is None:
            continue
        if got == want:
            ok += 1
        else:
            bad += 1
            bad_verified += pstat.get(ln) == "verified"
            detail.append((text[:24], want, got, pstat.get(ln)))
    m["program_syllables"] = {"right": ok, "wrong": bad, "wrong_but_marked_verified": bad_verified, "detail": detail}
    # ---- rhyme analyzer evidence against the designed pairs ----
    sw = rec["reports"].get("songwriter") or {}
    ev = ((sw.get("data") or {}).get("rhyme_evidence") or {})
    pairs = set()
    for sec in ev.get("sections") or []:
        for p in sec.get("pairs") or []:
            pairs.add(frozenset({p["a"]["line"], p["b"]["line"]}))
    def lines_of(txt):
        return [k for k, v in lines.items() if v.strip() == txt]
    true_hit = true_all = false_hit = false_all = 0
    for a, b in song["rhyme_true"]:
        la, lb = lines_of(a), lines_of(b)
        if la and lb:
            true_all += 1
            true_hit += any(frozenset({x, y}) in pairs for x in la for y in lb)
    for a, b in song["rhyme_false"]:
        la, lb = lines_of(a), lines_of(b)
        if la and lb:
            false_all += 1
            false_hit += any(frozenset({x, y}) in pairs for x in la for y in lb)
    m["rhyme_evidence"] = {"found": bool(ev.get("sections")), "true_found": true_hit, "true_total": true_all, "false_claimed": false_hit, "false_total": false_all, "pairs_total": len(pairs)}
    # ---- every report: reference validity, quotes, audio claims, syllable claims ----
    per = {}
    for spec, rep in rec["reports"].items():
        tx = texts_of(rep)
        allt = "\n".join(tx.values())
        refs_ok = refs_bad = 0
        bad_refs = []
        for mm in REFNO.finditer(allt):
            a = mm.group(1) or mm.group(3)
            b = mm.group(2) or mm.group(4)
            a = int(a)
            b = int(b) if b else a
            if 1 <= a <= b <= len(lines):
                single_blank = a == b and not lines.get(a, "").strip()
                if single_blank:
                    refs_bad += 1
                    bad_refs.append(f"L{a} is empty")
                else:
                    refs_ok += 1
            else:
                refs_bad += 1
                bad_refs.append(f"L{a}-{b} does not exist")
        quotes = [q for q in QUOTE.findall(allt) if (THAI_ONLY.search(q) or re.search(r"[A-Za-z]{3,}", q)) and not re.search(r"[-–↔=→]", q)]
        q_ok = q_bad = 0
        bad_q = []
        grams = {flat[i:i + 3].lower() for i in range(len(flat) - 2)}
        for q in quotes:
            qq = re.sub(r"\s+", "", q).strip(".,!?…")
            if len(qq) < 6:
                continue                                           # a short scare-quoted word is a concept, not a quote of a line
            qg = [qq[i:i + 3].lower() for i in range(len(qq) - 2)]
            share = sum(1 for g in qg if g in grams) / max(1, len(qg))
            if qq.lower() in flat.lower():
                q_ok += 1
            elif share >= 0.6:                                      # it looks like a line but is not in the lyrics: a misquote
                q_bad += 1
                bad_q.append(q[:50])
        # findings: the evidence must be in the lines the finding names
        f_ok = f_bad = 0
        bad_f = []
        for f in rep.get("findings") or []:
            ev_t = (f.get("evidence") or "").strip()
            if not ev_t:
                continue
            try:
                a, b = int(f.get("first") or 0), int(f.get("last") or 0)
            except ValueError:
                a = b = 0
            seg = "\n".join(lines.get(k, "") for k in range(a, b + 1))
            if re.sub(r"\s+", "", ev_t) in re.sub(r"\s+", "", seg):
                f_ok += 1
            else:
                f_bad += 1
                bad_f.append((f.get("id"), ev_t[:40]))
        # syllable claims "L7 ... 8 พยางค์": compare with the hand count when the line is an anchor, else with the program table
        s_ok = s_bad = s_prog_ok = s_prog_bad = 0
        s_detail = []
        for mm in SYL.finditer(allt):
            around = allt[max(0, mm.start() - 70):mm.start()]
            r = list(REFNO.finditer(around))
            if not r:
                continue
            last = r[-1]
            a = int(last.group(1) or last.group(3))
            b = last.group(2) or last.group(4)
            if b:                                           # a range with ONE count is a total: not comparable
                continue
            claim = int(mm.group(1))
            want = next((v for t, v in song["anchors"].items() if lines.get(a, "").strip() == t), None)
            if want is not None:
                if claim == want:
                    s_ok += 1
                else:
                    s_bad += 1
                    s_detail.append((a, claim, want))
            elif a in prog:
                if claim == prog[a]:
                    s_prog_ok += 1
                else:
                    s_prog_bad += 1
        audio = [mm.group(0) for mm in AUDIO.finditer(allt)]
        per[spec] = {"status": rep.get("status"), "diagnostics": len(rep.get("diagnostics") or []), "findings": len(rep.get("findings") or []), "strengths": len(rep.get("strengths") or []),
                     "refs_ok": refs_ok, "refs_bad": refs_bad, "bad_refs": bad_refs[:5], "quotes_ok": q_ok, "quotes_bad": q_bad, "bad_quotes": bad_q[:5],
                     "finding_evidence_ok": f_ok, "finding_evidence_bad": f_bad, "bad_finding_evidence": bad_f[:5], "syl_claims_ok": s_ok, "syl_claims_bad": s_bad, "syl_detail": s_detail[:5],
                     "syl_vs_program_ok": s_prog_ok, "syl_vs_program_bad": s_prog_bad, "audio_claims": audio[:3], "chars": len(allt)}
    m["reports"] = per
    return m


def flags(rec: dict, song: dict) -> dict:
    """Does the report say what a good report must say about THIS song? Keyword evidence only (a human reads the quotes in the audit); each flag keeps the matching sentence."""
    sw = rec["reports"].get("songwriter") or {}
    pr = rec["reports"].get("music_producer") or {}
    st = "\n".join(texts_of(sw).values())
    pt = "\n".join(texts_of(pr).values())
    both = st + "\n" + pt
    ex = song["expect"]
    out = {}

    def find(pat, text):
        m = re.search(pat, text)
        return text[max(0, m.start() - 60): m.end() + 80].replace("\n", " ") if m else ""
    chorus_lines = set()
    cur = None
    for i, ln in enumerate(song["text"].split("\n"), 1):
        mm = re.match(r"\[(.+?)\]", ln.strip())
        if mm:
            cur = mm.group(1)
        elif cur and cur.startswith("Chorus") and ln.strip():
            chorus_lines.add(i)
    def hits_chorus(f):
        try:
            a, b = int(f.get("first") or 0), int(f.get("last") or 0)
        except ValueError:
            return False
        return any(k in chorus_lines for k in range(a, b + 1))
    chorus_findings = [f for f in (sw.get("findings") or []) if hits_chorus(f)]
    if ex.get("weak_hook"):
        ev = find(r"(ฮุก|คอรัส|Chorus|บรรทัด\s*[6-9]|L[6-9]\b)[^\n]{0,120}(ทั่วไป|ธรรมดา|จืด|ไม่มี(ภาพ|จุดเด่น|ความเฉพาะ)|ซ้ำ|อ่อน|ขาด|generic|คลิเช่|สูตร)", st) or find(r"(ทั่วไป|ธรรมดา|จืด|ซ้ำ|อ่อน|ขาด|คลิเช่)[^\n]{0,80}(ฮุก|คอรัส|Chorus)", st)
        out["weak_hook_named"] = {"ok": bool(ev), "evidence": ev}
    if ex.get("good_hook"):
        high = [f for f in chorus_findings if f.get("priority") in ("high", "medium") and (f.get("suggestion") or "").strip()]
        out["good_hook_not_attacked"] = {"ok": not high, "evidence": "; ".join(f"{f.get('priority')} {f.get('first')}-{f.get('last')}: {(f.get('finding') or '')[:60]}" for f in high)}
        ev = find(r"(ฮุก|คอรัส|Chorus)[^\n]{0,100}(ติดหู|จำง่าย|แข็งแรง|ดี|โดดเด่น|แรง|หนักแน่น)", st)
        out["good_hook_recognised"] = {"ok": bool(ev), "evidence": ev}
    if ex.get("contradiction"):
        ev = find(r"ขัดแย้ง|ย้อนแย้ง|ไม่สอดคล้อง|สลับ(กัน)?|กลับด้าน|ไม่ตรงกัน|ผู้จากไป|ใครเป็นคน|ทั้งที่|contradict|ตัดกัน|ไม่เคย.{0,20}(แต่|ทั้งที่)", both)
        out["contradiction_named"] = {"ok": bool(ev), "evidence": ev}
    if ex.get("dense"):
        ev = find(r"หนาแน่น|พยางค์ต่อบรรทัด|เร็วเกิน|ยาวเกิน|ยาวมาก|เกิน(ไป)?.{0,20}(ความยาว|duration|เวลา)|ไม่พอดี|อัด|rush|dense|(2[0-9]|3[0-9])\s*พยางค์", both)
        out["density_named"] = {"ok": bool(ev), "evidence": ev}
        ev2 = find(r"ACE-?Step[^\n]{0,80}(จะ|แน่นอน)(?!.*ความเสี่ยง)", both)
        out["no_audio_prediction"] = {"ok": not re.search(r"ACE-?Step[^\n]{0,60}(จะร้อง|จะข้าม|จะรวบ)[^\n]{0,20}แน่", both), "evidence": ev2}
    if ex.get("no_rhyme_demand"):
        bad = [f for f in (sw.get("findings") or []) if (f.get("kind") in ("rhyme",) or "สัมผัส" in (f.get("finding") or "")) and f.get("priority") in ("high", "medium") and (f.get("suggestion") or "").strip()]
        out["irregular_rhyme_not_a_fault"] = {"ok": not bad, "evidence": "; ".join((f.get("finding") or "")[:70] for f in bad)}
    if ex.get("tags"):
        bad = []
        for f in (sw.get("findings") or []):
            sug = (f.get("suggestion") or "")
            if re.search(r"^\s*\[[^\]]+\]", sug) and re.match(r"\[", (f.get("evidence") or "").strip()):
                bad.append(f.get("id"))
        out["songwriter_leaves_cue_tags"] = {"ok": not bad, "evidence": str(bad)}
        secs = []
        for f in pr.get("findings") or []:
            pass
    # sections the report names must be the real ones
    names = [re.sub(r"\s*-\s.*", "", s) for s in ex["sections"]]
    flow = (sw.get("data") or {}).get("rhyme_flow") or []
    if flow:
        got = [re.sub(r"\s*\(.*\)\s*$", "", re.sub(r"\s*-\s.*", "", (s.get("name") or ""))).strip() for s in flow]
        out["sections_match_reference"] = {"ok": got == names, "evidence": f"reported {got} vs reference {names}"}
    return out


def main():
    res = {}
    for f in sorted((AUD / "out").glob("*.json")):
        mm = re.match(r"(local|grok)_(S\d)_r(\d)\.json", f.name)
        if not mm:
            continue
        kind, sid, rnd = mm.groups()
        rec = json.loads(f.read_text(encoding="utf-8"))
        song = SONGS[sid]
        res[f"{kind}|{sid}|{rnd}"] = {"metrics": run_metrics(rec, song), "flags": flags(rec, song)}
    (AUD / "metrics.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    r = main()
    for k, v in r.items():
        m, fl = v["metrics"], v["flags"]
        pr = m["program_syllables"]
        print(f"{k:14s} {m['seconds']:5.0f}s req={m['requests']:2d} err={'-' if not m['error'] else 'ERR'} prog_syl={pr['right']}/{pr['right']+pr['wrong']} rhyme_ev true {m['rhyme_evidence']['true_found']}/{m['rhyme_evidence']['true_total']} false {m['rhyme_evidence']['false_claimed']}/{m['rhyme_evidence']['false_total']} "
              + " ".join(f"{sp[:2]}:{d['status']},f{d['findings']},refs{d['refs_bad']}bad,q{d['quotes_bad']}bad,ev{d['finding_evidence_bad']}bad,audio{len(d['audio_claims'])}" for sp, d in m["reports"].items())
              + " | flags " + " ".join(f"{a}={'OK' if b['ok'] else 'NO'}" for a, b in fl.items()))
