# -*- coding: utf-8 -*-
"""usage: judge.py <cand> [<cand> ...]   Blind, criteria-based grading with Grok (the grader never sees which candidate wrote the answer). Every criterion is binary and must cite a quote from the answer.
Results are cached in tune_out/judge_<cand>.json. Then prints pass rates per candidate (tune / holdout) and per dimension."""
import json, re, sys, time, hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCR = Path(r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad")
sys.path.insert(0, str(SCR)); sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
from tune_cases import CASES
from music_studio import llm_chat as LC

# criterion id -> (dimension, text)
CRIT = {
 "A": [("a1", 1, "At least TWO of the offered alternatives are phrases a native Thai speaker would genuinely say in this situation (everyday idiom / natural collocation). Literal coinages or stiff constructions such as 'นัดแบบสุ่ม', 'นัดมั่วๆ', 'นัดแบบไม่รู้', 'นัดแบบไม่รู้จัก', 'เดทบอด' do NOT count."),
       ("a2", 2, "Every option it recommends keeps the meaning: a meeting/date arranged by the mother with a man the narrator has not chosen or met."),
       ("a3", 4, "It states which option it prefers AND gives a reason that is about how the phrase sounds or is said (not just 'it is short').")],
 "B": [("b1", 4, "TURN 1: gives a specific honest opinion of the line that names at least one concrete weakness or risk (or plainly says there is none after naming what works). Generic praise ('ดีมาก', 'สื่อความรู้สึกได้ชัดเจน') alone FAILS."),
       ("b2", 2, "TURN 2: names a real change in meaning or stance caused by the user's replacement (the original pushes back / refuses to be pressured; the replacement says the narrator does not dislike him = reassurance) and says what is lost or gained."),
       ("b3", 4, "TURN 2: does not simply endorse the user's version. It must state a real cost or reservation AND not open with agreement such as 'ดีมาก', 'เห็นด้วย', 'ผมชอบเวอร์ชันใหม่'. (If it names a cost but then agrees fully with no reservation, FAIL.)"),
       ("b4", 3, "Its claims about syllables/sound/rhyme in the two turns are not plainly wrong (a count within 1-2 of correct is fine).")],
 "C": [("c1", 3, "It reads the line endings correctly: the end words it names are really the last words of real lines of the lyrics (no invented lines, no wrong words)."),
       ("c2", 3, "It identifies หนอ, นา and เอย as probably added/filler to land the rhyme (hedging such as 'may be' is fine)."),
       ("c3", 7, "It proposes no lyric change and writes no tag (the user said not to edit yet).")],
 "D1": [("d1", 4, "It says candidly that the verses/chorus keep repeating one sadness in similar images without development or a turn, quoting or pointing to specific lines."),
        ("d2", 4, "It does NOT say the song has a strong or well-developed spine/story, and does not praise the development."),
        ("d3", 3, "Every line it quotes or cites is really in the lyrics.")],
 "D2": [("e1", 4, "It treats the song as an intentional mood piece: it does NOT demand a plot, an event, 'telling' instead of 'showing', or call the lack of story a weakness."),
        ("e2", 4, "It makes at least one specific observation (with a quoted line) about whether the images build one experience."),
        ("e3", 4, "It is not generic praise only: there is at least one real reservation or specific craft note (or it says plainly the spine is sound with a named reason).")],
 "E": [("f1", 1, "The new lines read as natural spoken Thai that a person could sing (no awkward word-by-word construction, no stiff 'dictionary' phrases)."),
       ("f2", 2, "The verse fits the story: the daughter agrees to meet him but wants to get to know him first; it continues from the existing song instead of restarting."),
       ("f3", 3, "Any rhyme is carried by meaningful words; end words such as เธอ/หนา/เอย/หนอ are not added just to land a rhyme, and no line is bent to rhyme."),
       ("f4", 5, "There is at least one fresh, concrete image or turn of phrase (not generic 'หัวใจ/น้ำตา/ใจ' filler).")],
 "F": [("g1", 2, "TURN 1: the shortened line is clearly shorter than the original, keeps the mother opening the laptop and looking for a good man, and still sounds funny/playful in natural Thai."),
       ("g2", 6, "TURN 2: it accepts the cancel in plain words without writing a new proposal or claiming the line was restored/changed.")],
 "H1": [("h1", 1, "At least TWO of the offered Thai alternatives for 'โอเค' are phrases a native speaker would genuinely say in this situation (everyday, natural; stiff or coined constructions do not count)."),
        ("h2", 2, "Every alternative it recommends keeps the meaning of the line (agreeing / being fine with it) and the same stance."),
        ("h3", 4, "It states a preference with a reason about how the phrase is said or sung.")],
 "H2": [("i1", 2, "It recognises that the user's replacement changes the meaning: 'ฉันไม่ได้เกลียดคนนั้น' (my feeling) becomes 'คนนั้นไม่ได้น่าเกลียด' (a judgment of his looks), and says so."),
        ("i2", 4, "It does not simply declare the replacement better or agree: it states the cost clearly and offers another way to get a rhyme/closeness without changing the meaning."),
        ("i3", 3, "Its claims about the sounds/rhyme are not plainly wrong.")],
 "H3": [("j1", 3, "The end words it names are really the last words of real lines (no invented lines)."),
        ("j2", 3, "It separates a deliberate folk-style particle (e.g. an 'เอย'/'นา' that fits an old-fashioned voice) from words that look added only to rhyme, with reasoning per word, not a blanket claim for all of them."),
        ("j3", 7, "It proposes no lyric change and writes no tag.")],
 "H4": [("k1", 1, "The four lines read as natural spoken Thai (no awkward constructions)."),
        ("k2", 5, "It avoids เหงา / น้ำตา / ดาว and does not repeat the same word/phrase to fill space."),
        ("k3", 5, "The lines build a feeling of 'about to break' with concrete detail rather than naming the emotion.")],
}
SYS = ("You are a strict, careful grader of Thai song-writing assistant answers. You are given the song lyrics (line ids L001..), the user's turns, the assistant's raw answers and a list of binary criteria. "
       "Grade each criterion pass or fail. Be literal and demanding: a criterion passes only when the answer clearly satisfies it. For every criterion give a short evidence quote copied from the answer (or 'none'). "
       "Return ONLY JSON: {\"c1\": {\"pass\": true, \"evidence\": \"...\"}, ...} using the criterion ids given. Ignore answer length and formatting unless a criterion mentions them.")


def grade(cid, recs, lyrics):
    crit = CRIT[cid]
    lines = "\n".join(f"L{i:03d}| {l}" for i, l in enumerate(lyrics.split("\n"), 1))
    body = [f"LYRICS:\n{lines}\n"]
    for t in sorted(recs):
        body.append(f"USER TURN {t}: {recs[t]['ask']}\nASSISTANT ANSWER {t}:\n{re.sub(r'\\s*\\[DONE\\]\\s*$', '', recs[t]['raw']).strip()[:3500]}\n")
    body.append("CRITERIA:\n" + "\n".join(f"{k}: {txt}" for k, _d, txt in crit))
    g = LC.grok_creds()
    for attempt in range(3):
        try:
            ans = LC.ask_grok(g["key"], g["model"], SYS, "\n".join(body), temperature=0.0)
            m = re.search(r"\{.*\}", ans, re.S)
            return json.loads(m.group(0))
        except Exception as ex:
            err = str(ex)
            time.sleep(2)
    return {"error": err}


def run(name):
    cache = SCR / "tune_out" / f"judge_{name}.json"
    done = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    recs = {}
    for ln in (SCR / "tune_out" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        recs.setdefault(r["case"], {})[r["turn"]] = r
    todo = [c for c in CASES if c["id"] in recs and c["id"] in CRIT and c["id"] not in done]
    with ThreadPoolExecutor(5) as ex:
        res = list(ex.map(lambda c: (c["id"], grade(c["id"], recs[c["id"]], c["lyrics"])), todo))
    for cid, r in res:
        done[cid] = r
    cache.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    return done


if __name__ == "__main__":
    names = sys.argv[1:]
    split = {c["id"]: c["split"] for c in CASES}
    dimn = {1: "Natural Thai", 2: "Meaning kept", 3: "Rhyme & craft", 4: "Independent judgment", 5: "Creativity", 6: "Conversational", 7: "Tags / contract"}
    table = {}
    for n in names:
        d = run(n)
        rows = []
        for cid, crit in CRIT.items():
            got = d.get(cid, {})
            for k, dim, txt in crit:
                v = got.get(k)
                if isinstance(v, dict) and "pass" in v:
                    rows.append((cid, k, dim, bool(v["pass"])))
        table[n] = rows
    print("CRITERIA (✔ pass)")
    for cid, crit in CRIT.items():
        for k, dim, txt in crit:
            print(f"  {split[cid][0].upper()} {cid:2s} {k}  d{dim} " + "  ".join("✔" if any(r[1] == k and r[3] for r in table[n]) else ("✘" if any(r[1] == k for r in table[n]) else "·") for n in names) + f"   {txt[:70]}")
    for sp in ("tune", "holdout"):
        print(f"\n{sp:8s}", "  ".join(f"{n}: {sum(1 for r in table[n] if split[r[0]]==sp and r[3])}/{sum(1 for r in table[n] if split[r[0]]==sp)}" for n in names))
    print()
    for dm, lab in dimn.items():
        print(f"  d{dm} {lab:22s}", "  ".join(f"{n}: {sum(1 for r in table[n] if r[2]==dm and r[3])}/{sum(1 for r in table[n] if r[2]==dm)}" for n in names))
