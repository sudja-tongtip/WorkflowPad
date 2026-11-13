# -*- coding: utf-8 -*-
import json, re, sys
from pathlib import Path
SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
sys.path.insert(0, str(SCR)); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
from tune_cases import CASES
from music_studio import chat_tags as CT
import judge as J
LY = {c["id"]: c["lyrics"] for c in CASES}
split = {c["id"]: c["split"] for c in CASES}
groups = {"Baseline": ["base", "base_b"], "R1": ["R1", "R1_b"], "R2": ["R2", "R2_b"], "R3": ["R3", "R3_b"], "R4": ["R4", "R4_b"], "R5": ["R5", "R5_b"]}
OUT = SCR / "tune_out"
rows = []
for g, names in groups.items():
    tune = hold = n = 0
    tot_tune = tot_hold = 0
    tags_total = tags_ok = tags_bad = 0
    resp_tok = 0
    sys_tok = 0
    d4 = d4t = 0
    for nm in names:
        j = json.loads((OUT / f"judge_{nm}.json").read_text(encoding="utf-8"))
        for cid, crit in J.CRIT.items():
            for k, dim, _t in crit:
                v = (j.get(cid) or {}).get(k)
                if isinstance(v, dict) and "pass" in v:
                    p = bool(v["pass"])
                    if split[cid] == "tune":
                        tune += p; tot_tune += 1
                    else:
                        hold += p; tot_hold += 1
                    if dim == 4:
                        d4 += p; d4t += 1
        for ln in (OUT / f"{nm}.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            sys_tok = r["system_tokens"]; resp_tok += r["response_tokens"]; n += 1
            raw = r["raw"]
            if re.search(r"\[(EDIT|INSERT)\.\d", raw):
                tags_total += 1
                lyr = LY[r["case"]]
                nl = len(lyr.split(chr(10)))
                raw2 = re.sub(r'(after:\s*)L0*%d\b' % nl, lambda m: m.group(1) + "end", raw) if lyr.endswith(chr(10)) else raw   # the empty last line is listed with an id: scored as 'end' (parser issue recorded separately)
                o = CT.answer("songwriter", raw2, lyr)
                ok = bool(o.edits) and not o.rejected
                tags_ok += ok
                tags_bad += (not ok)
    k = len(names)
    rows.append((g, sys_tok, tune / k, tot_tune / k, hold / k, tot_hold / k, d4 / k, d4t / k, resp_tok / max(1, n), tags_total, tags_ok, tags_bad))
print(f"{'Candidate':10s} {'sys tok':>7s} {'tune /22':>9s} {'hold /12':>9s} {'judgment d4 /10':>16s} {'resp tok':>8s} {'tags ok/total':>14s}")
for g, st, t, tt, h, ht, d, dt, rt, tg, ok, bad in rows:
    print(f"{g:10s} {st:7d} {t:9.1f} {h:9.1f} {d:16.1f} {rt:8.0f} {ok:>7d}/{tg:<6d}")
json.dump(rows, open(OUT / "summary_rows.json", "w", encoding="utf-8"), ensure_ascii=False)
