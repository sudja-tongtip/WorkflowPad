# -*- coding: utf-8 -*-
"""Rebuilds the files as they were BEFORE this task (for git_diff.patch and the before/after comparison). It reverses this task's edits on the current files; it never writes to the project."""
import shutil, sys
from pathlib import Path
ROOT = Path("D:/AI/MyApps/WorkflowPad")
OUT = Path(sys.argv[1])
SCR = Path("C:/Users/sudja/AppData/Local/Temp/claude/a8ec2d1fb949c4ce/f45a390f-0139-4fee-892a-2bab035e394a/scratchpad")
if OUT.exists():
    shutil.rmtree(OUT)
shutil.copytree(ROOT / "music_studio", OUT / "music_studio", ignore=shutil.ignore_patterns("__pycache__"))
shutil.copy(ROOT / "music_studio.py", OUT / "music_studio.py")


def capture(script):
    src = (SCR / script).read_text(encoding="utf-8")
    i = src.index("def patch(rel, pairs):"); j = src.index("\n\n\n", i)
    src = src[:i] + src[j:]
    calls = []
    ns = {"sys": sys, "ROOT": "", "patch": lambda rel, pairs: calls.append((rel, pairs))}
    exec(compile(src, script, "exec"), ns)
    return calls


def rd(p):
    return p.read_bytes().decode("utf-8")


def wr(p, s):
    p.write_bytes(s.encode("utf-8"))


def reverse(rel, pairs, pre=(), post=()):
    p = OUT / rel
    raw = rd(p); crlf = "\r\n" in raw; s = raw.replace("\r\n", "\n")
    for a, b in pre:
        assert s.count(a) == 1, (rel, a[:60], s.count(a)); s = s.replace(a, b)
    for old, new in reversed(pairs):
        if new == "":                                   # a removed line: put it back after its anchor
            anchor = post[0]
            assert s.count(anchor) == 1, (rel, "anchor", s.count(anchor)); s = s.replace(anchor, anchor + old); continue
        assert s.count(new) == 1, (rel, new[:60], s.count(new)); s = s.replace(new, old)
    wr(p, s.replace("\n", "\r\n") if crlf else s)


fix = {rel: pairs for rel, pairs in capture("patch_fix.py")}
steps = {rel: pairs for rel, pairs in capture("patch_steps.py")}
reverse("music_studio/specialist_engine.py", fix["music_studio/specialist_engine.py"],
        post=['        task = task.replace(" You have not heard any melody or audio: never claim that something fits a tune or will be catchy.", "")     # the Songwriter prompt (Limitations) says it once\n'])
reverse("music_studio/prompts/specialists/songwriter.md", fix["music_studio/prompts/specialists/songwriter.md"])
reverse("music_studio.py", steps["music_studio.py"])
reverse("music_studio/report_steps.py", steps["music_studio/report_steps.py"],
        pre=[(' and not n.endswith("<<<"):      # a delimiter line (`suggestion: <<<`) is format, never a copied sentence', ':')])
# rhyme_flow: the net edits (fields, rules, and the generalized gate)
p = OUT / "music_studio/rhyme_flow.py"
raw = rd(p); crlf = "\r\n" in raw; s = raw.replace("\r\n", "\n")
fix_rf = [(o, n) for rel, ps in capture("patch_fix.py") if rel.endswith("rhyme_flow.py") for o, n in ps]
rev = [
    (fix_rf[0][1], fix_rf[0][0]),
    (fix_rf[2][1], fix_rf[2][0]),
    ("    notes = []\n    for c in flow or []:\n        if c.get(\"keep\") and c.get(\"raw_priority\") not in (\"\", \"-\", \"none\", None):          # the format says the priority is `-` without a recommendation: any other value (worthwhile, important, ...) is refused, not just `important`\n            notes.append(f\"Check: {c.get('name')}: priority {c.get('raw_priority')} with no recommendation (the priority was ignored).\")\n    secs = merge(evidence, flow) if evidence else []",
     "    notes = []\n    for c in flow or []:\n        if c.get(\"keep\") and c.get(\"raw_priority\") in (\"important\", \"high\"):\n            notes.append(f\"Check: {c.get('name')}: priority important with no recommendation (the priority was ignored).\")\n    secs = merge(evidence, flow) if evidence else []"),
    ("      * a priority (`important`, `worthwhile`, ...) with no recommendation (the priority was dropped by the parser);\n", "      * a priority `important` with no recommendation (the priority was dropped by the parser);\n"),
]
for a, b in rev:
    assert s.count(a) == 1, (a[:70], s.count(a)); s = s.replace(a, b)
wr(p, s.replace("\n", "\r\n") if crlf else s)
print("before-tree built in", OUT)
