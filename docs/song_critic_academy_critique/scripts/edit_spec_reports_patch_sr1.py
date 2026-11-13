# -*- coding: utf-8 -*-
import sys
p = sys.argv[1]
raw = open(p, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")


def sub(old, new):
    global t
    assert t.count(old) == 1, (t.count(old), old[:90])
    t = t.replace(old, new)


sub('ASPECT_STATUS = ("strength", "concern", "mixed", "no_issue", "insufficient")\nSTATUS_KIND = {"strength": "strength", "concern": "concern", "mixed": "concern", "no_issue": "keep", "insufficient": "keep", "unrated": "keep"}',
    '''ASPECT_STATUS = ("strength", "concern", "mixed", "no_issue", "not_applicable", "insufficient")
STATUS_KIND = {"strength": "strength", "concern": "concern", "mixed": "concern", "no_issue": "keep", "not_applicable": "keep", "insufficient": "keep", "unrated": "keep"}
STATUS_GROUPS = ("good", "improve", "major", "na", "unknown")


def status_group(status: str, priority: str = "") -> str:
    """The five states the Academy cards show, from what the AI wrote (a plain mapping, no judgment of the song): good = strength / no significant issue; improve = a concern or a mixed reading
    of low or medium priority; major = a concern or a mixed reading of HIGH priority; na = not relevant to this song; unknown = insufficient evidence, not rated, or not in the report."""
    s = (status or "").strip().lower()
    if s in ("strength", "no_issue"):
        return "good"
    if s in ("concern", "mixed"):
        return "major" if (priority or "").strip().lower() == "high" else "improve"
    if s == "not_applicable":
        return "na"
    return "unknown"
''')
sub('''_STATUS_ALIAS = {"no_significant_issue": "no_issue", "none": "no_issue", "ok": "no_issue", "not_applicable": "insufficient", "na": "insufficient", "n_a": "insufficient", "insufficient_evidence": "insufficient",
                 "mixed_conditional": "mixed", "conditional": "mixed"}''',
    '''_STATUS_ALIAS = {"no_significant_issue": "no_issue", "none": "no_issue", "ok": "no_issue", "na": "not_applicable", "n_a": "not_applicable", "not_relevant": "not_applicable", "irrelevant": "not_applicable",
                 "not_relevant_to_this_song": "not_applicable", "insufficient_evidence": "insufficient", "not_analyzed": "insufficient", "unknown": "insufficient", "mixed_conditional": "mixed", "conditional": "mixed",
                 "good": "strength", "needs_improvement": "concern", "major_issue": "concern"}''')
sub('"observation", "interpretation", "recommendation", "keep", "alternative", "proposal",', '"summary", "observation", "interpretation", "recommendation", "keep", "alternative", "proposal",')
sub('''        got[key] = dict(ev, id="as_" + key, aspect=key, status=st, kind=STATUS_KIND[st], priority=pr, patterns=pats, observation=obs, interpretation=why,
                        recommendation="" if _none(f.get("recommendation")) else f["recommendation"].strip(), keep="" if _none(f.get("keep")) else f["keep"].strip(),
                        proposal="")''', '''        prop = ""
        if not _none(f.get("proposal")):                                          # a real wording for ONE place: it must be a different text for exactly those lines (the same rules as a finding of the Song Critic)
            if len(ev["refs"]) != 1:
                diag.append(f"[ASPECT.{n}]: a proposed wording needs exactly one line reference; the proposal was not used.")
            else:
                prop, why_not = alternative_of(f.get("proposal", ""), ev["quote"], ev["first"], ev["last"])
                if why_not:
                    diag.append(f"[ASPECT.{n}]: proposal: {why_not}")
        got[key] = dict(ev, id="as_" + key, aspect=key, status=st, kind=STATUS_KIND[st], priority=pr, patterns=pats, observation=obs, interpretation=why,
                        summary="" if _none(f.get("summary")) else f["summary"].strip(),
                        recommendation="" if _none(f.get("recommendation")) else f["recommendation"].strip(), keep="" if _none(f.get("keep")) else f["keep"].strip(),
                        proposal=prop)''')
open(p, "wb").write((t.replace("\n", "\r\n") if crlf else t).encode("utf-8"))
print("patched")
