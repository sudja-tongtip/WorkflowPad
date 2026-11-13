# -*- coding: utf-8 -*-
"""LLM judge, used only as a HELPER for Musical Judgment and Actionability (the final scores also use the deterministic checks and a human reading). The judge does not know which backend wrote a report.
usage: judge_audit.py  -> audit/judge.json (cached per run)"""
import json, re, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
AUD = SCR / "audit"
sys.path.insert(0, str(AUD)); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
from songs import SONGS
from music_studio import llm_chat as LC

SYS = ("You are a strict reviewer of songwriting-assistant reports for Thai/English songs. You get a song (numbered lines), its settings, a human REFERENCE note (what a good report must notice) and two reports "
       "(a lyrics report and a music-production report). Score ONLY from what is written; a report is not better for being long, using music terms or having many headings. "
       "Return ONLY JSON: {\"musical_judgment\": 0-5, \"actionability\": 0-5, \"critical_errors\": [\"short quote + why\"], \"notes\": \"two sentences\"}. "
       "Musical judgment: does it understand the song's structure, story, hook and what each part does, separate real strengths from real weaknesses, and avoid inventing problems (a good song must not be called bad; "
       "a flawed song must not be called fine)? Actionability: does each piece of advice name where (lines), why, and what to do; does it say plainly when the original is already fine; are suggestions usable? "
       "Critical error = advice that would send the writer the wrong way (wrong line, invented flaw in a good part, a suggestion that changes the meaning, claiming to have heard audio, a false sound/syllable claim).")


def render(rep: dict) -> str:
    out = [f"SUMMARY: {rep.get('summary','')}", "STRENGTHS: " + " | ".join(rep.get("strengths") or [])]
    for f in rep.get("findings") or []:
        out.append(f"FINDING [{f.get('kind')}/{f.get('priority')}] lines {f.get('first')}-{f.get('last')}: {f.get('finding','')} | why: {f.get('reason','')} | evidence: {f.get('evidence','')} | suggestion: {f.get('suggestion','')}")
    for s in (rep.get("data") or {}).get("rhyme_flow") or []:
        out.append(f"SECTION {s.get('name')} ({s.get('first')}-{s.get('last')}): {s.get('quality','')} | strength: {s.get('strength','')} | issue: {s.get('issue','')} | consider: {s.get('consideration') or s.get('consider') or ''} | keep: {s.get('keep_reason','')} | recommendation: {s.get('recommendation','')}")
    out.append("NOT SURE: " + " | ".join(rep.get("uncertain") or []))
    return "\n".join(out)


def grade(key, rec, song):
    cache = AUD / "judge_cache" / f"{key}.json"
    cache.parent.mkdir(exist_ok=True)
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    lines = "\n".join(f"L{i}| {l}" for i, l in enumerate(rec["lyrics"].split("\n"), 1))
    s = rec["settings"]
    user = (f"SONG SETTINGS: genre {s['genre']}, mood {s['mood']}, BPM {s['bpm']}, target duration {s['duration']} s, language {s['language']}\nLYRICS:\n{lines}\n\n"
            f"REFERENCE (human): {song['kind']}. {song['expect']['summary']}\n\n"
            f"LYRICS REPORT:\n{render(rec['reports'].get('songwriter') or {})}\n\nMUSIC PRODUCTION REPORT:\n{render(rec['reports'].get('music_producer') or {})}")
    g = LC.grok_creds()
    for _ in range(3):
        try:
            ans = LC.ask_grok(g["key"], g["model"], SYS, user, temperature=0.0)
            d = json.loads(re.search(r"\{.*\}", ans, re.S).group(0))
            cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            return d
        except Exception as ex:
            err = str(ex)
    return {"error": err}


def main():
    jobs = []
    for f in sorted((AUD / "out").glob("*.json")):
        mm = re.match(r"(local|grok)_(S\d)_r(\d)\.json", f.name)
        if mm:
            kind, sid, rnd = mm.groups()
            jobs.append((f"{kind}|{sid}|{rnd}", json.loads(f.read_text(encoding="utf-8")), SONGS[sid]))
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda j: (j[0], grade(j[0].replace("|", "_"), j[1], j[2])), jobs))
    out = dict(res)
    (AUD / "judge.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in out.items():
        print(k, v.get("musical_judgment"), v.get("actionability"), "| errors:", len(v.get("critical_errors") or []))


if __name__ == "__main__":
    main()
