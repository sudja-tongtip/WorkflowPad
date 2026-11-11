# -*- coding: utf-8 -*-
"""Builds PATCH/song_critic_v2_task_only.patch: ONLY the Song Critic V2 changes to tracked files, as a patch against HEAD (LF, hunk headers recomputed), and checks it on a clean HEAD worktree
(git apply --check WITHOUT --recount, apply, new files, tests)."""
import json, re, subprocess, pathlib, shutil, sys, os

ROOT = pathlib.Path("D:/AI/MyApps/WorkflowPad")
OUT = ROOT / "docs/song_critic_v2/PATCH"
OUT.mkdir(exist_ok=True)
os.chdir(ROOT)
SC = pathlib.Path("C:/Users/sudja/AppData/Local/Temp/claude/a8ec2d1fb949c4ce/f45a390f-0139-4fee-892a-2bab035e394a/scratchpad")


def git(*a, cwd=None):
    r = subprocess.run(["git", *a], capture_output=True, cwd=cwd or ROOT)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def fix_hunk(h):
    hl = h.split("\n")
    m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", hl[0])
    old_n = sum(1 for l in hl[1:] if l[:1] in (" ", "-"))
    new_n = sum(1 for l in hl[1:] if l[:1] in (" ", "+"))
    hl[0] = f"@@ -{m.group(1)},{old_n} +{m.group(2)},{new_n} @@{m.group(3)}"
    return "\n".join(hl)


# music_studio.py: the hunks of this task (the Export Audio / table-style hunks of other work are left out)
_, full, _ = git("diff", "HEAD", "-U3", "--", "music_studio.py")
full = full.replace("\r\n", "\n")
head, *hunks = re.split(r"(?m)^(?=@@ )", full)
other_re = re.compile(r"audio_export|open_audio_export|\bAX\b|table_body_text_style|Export")
kept, other = [], []
for h in hunks:
    body = "\n".join(l for l in h.split("\n") if l[:1] in "+-")
    (other if other_re.search(body) else kept).append(h)
parts = [head + "".join(fix_hunk(h) for h in kept)]
_, d, _ = git("diff", "HEAD", "--", "music_studio/spec_reports.py")
parts.append(d.replace("\r\n", "\n"))

# th.json: only the keys this task added
_, headtxt, _ = git("show", "HEAD:music_studio/locales/th.json")
headtxt = headtxt.replace("\r\n", "\n")
headd = json.loads(headtxt)["strings"]
src = (SC / "patch_th6.py").read_text(encoding="utf-8")
ns = {}
exec(re.search(r"^new = \{.*?^\}\n", src, re.S | re.M).group(0), ns)
add = "".join(f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)},\n" for k, v in ns["new"].items() if k not in headd)
newtxt = headtxt.replace('  "strings": {\n', '  "strings": {\n' + add, 1)
json.loads(newtxt)
a_p, b_p = ROOT / "docs/song_critic_v2/_th_old.json", ROOT / "docs/song_critic_v2/_th_new.json"
a_p.write_text(headtxt, encoding="utf-8", newline="\n"); b_p.write_text(newtxt, encoding="utf-8", newline="\n")
r = subprocess.run(["git", "diff", "--no-index", "--", str(a_p), str(b_p)], capture_output=True, cwd=ROOT)
d = r.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
d = re.sub(r"^diff --git .*$", "diff --git a/music_studio/locales/th.json b/music_studio/locales/th.json", d, flags=re.M)
d = re.sub(r"^--- .*$", "--- a/music_studio/locales/th.json", d, flags=re.M)
d = re.sub(r"^\+\+\+ .*$", "+++ b/music_studio/locales/th.json", d, flags=re.M)
parts.append(d)
a_p.unlink(); b_p.unlink()
patch = "".join(parts)
(OUT / "song_critic_v2_task_only.patch").write_text(patch, encoding="utf-8", newline="\n")
(OUT / "OTHER_WORK_music_studio_py_not_part_of_this_task.diff").write_text(head + "".join(other), encoding="utf-8", newline="\n")
print("hunks task", len(kept), "other", len(other), "bytes", len(patch))

# new files of the task
NEW = ["tests/test_critic_refs.py", "tests/test_critic_actions.py", "docs/specialist_reports/REPORT_TAG_CONTRACT.md"]
for f in NEW:
    shutil.copy(ROOT / f, OUT / pathlib.Path(f).name)
# modified test file
_, d, _ = git("diff", "HEAD", "--", "tests/test_spec_reports.py")
(OUT / "tests_test_spec_reports.diff").write_text(d.replace("\r\n", "\n"), encoding="utf-8", newline="\n")

# check on a clean HEAD worktree
wt = ROOT.parent / "wt_check_v2"
if wt.exists():
    git("worktree", "remove", "--force", str(wt))
rc, o, e = git("worktree", "add", "--detach", str(wt), "HEAD")
print("worktree", rc)
rc, o, e = git("apply", "--check", str(OUT / "song_critic_v2_task_only.patch"), cwd=wt)
print("git apply --check (no --recount):", rc, e[-200:])
rc2, o, e = git("apply", str(OUT / "song_critic_v2_task_only.patch"), cwd=wt)
print("applied:", rc2)
rc3, o, e = git("apply", str(OUT / "tests_test_spec_reports.diff"), cwd=wt)
print("tests diff applied:", rc3, e[-120:])
for f in NEW:
    dst = wt / f
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / f, dst)
for f in ("tests/_ui_harness.py",):
    (wt / "tests").mkdir(exist_ok=True)
    shutil.copy(ROOT / f, wt / f)
shutil.copytree(ROOT / "comfy_apis", wt / "comfy_apis", dirs_exist_ok=True)      # the LLM workflows are not tracked by git (needed by the Songwriter tests for the context size)
(wt / "docs").mkdir(exist_ok=True)
for d_ in ("realtime_internal_rhyme", "specialist_quality_audit_01", "song_critic_tune_02"):
    shutil.copytree(ROOT / "docs" / d_, wt / "docs" / d_, dirs_exist_ok=True)
res = {}
for t in ("test_critic_refs", "test_critic_actions", "test_spec_reports"):
    r = subprocess.run([sys.executable, "tests/" + t + ".py"], capture_output=True, cwd=wt, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=400)
    out = r.stdout.decode("utf-8", "replace").strip().splitlines()
    res[t] = out[-1] if out else r.stderr.decode("utf-8", "replace")[-200:]
print(res)
(OUT / "clean_head_check.txt").write_text(f"git apply --check on a clean HEAD worktree (no --recount): rc={rc}\napplied: {rc2}\n" + "\n".join(f"{k}: {v}" for k, v in res.items()) + "\n", encoding="utf-8")
git("worktree", "remove", "--force", str(wt))
git("worktree", "prune")
