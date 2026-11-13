# -*- coding: utf-8 -*-
"""Builds PATCH/song_critic_academy_task_only.patch: ONLY the Songwriting Academy changes. The base is "HEAD + the Song Critic V3 task patch" (V3 is not committed yet), built on a clean worktree; the
Academy edits are applied to it by the same scripts that edited the real tree (apply_music_studio_edits.py, th_keys_patch.py), so the patch contains no hunk of the unrelated Export Audio work.
It is then checked: `git apply --check` WITHOUT --recount on that base, apply, and the Academy and V3 tests run on the result."""
import os, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path("D:/AI/MyApps/WorkflowPad")
HERE = ROOT / "docs/song_critic_academy"
V3 = ROOT / "docs/song_critic_v3"
OUT = HERE / "PATCH"
OUT.mkdir(exist_ok=True)
os.chdir(ROOT)
PY = sys.executable
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def git(*a, cwd=None):
    r = subprocess.run(["git", *a], capture_output=True, cwd=cwd or ROOT)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


wt = ROOT.parent / "wt_check_academy"
if wt.exists():
    git("worktree", "remove", "--force", str(wt))
print("worktree", git("worktree", "add", "--detach", str(wt), "HEAD")[0])
print("V3 patch applied:", git("apply", str(V3 / "PATCH/song_critic_v3_task_only.patch"), cwd=wt)[0])
(wt / "tests").mkdir(exist_ok=True)
shutil.copy(V3 / "PATCH/test_songcraft.py", wt / "tests/test_songcraft.py")                 # the V3 test as it was before the Academy changed its tile counting
shutil.copy(ROOT / "tests/_ui_harness.py", wt / "tests/_ui_harness.py")
snap = HERE / "_base_snap"
if snap.exists():
    shutil.rmtree(snap)
snap.mkdir()
FILES = ["music_studio.py", "music_studio/locales/th.json", "tests/test_songcraft.py"]
for f in FILES:
    (snap / f).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(wt / f, snap / f)
# the Academy edits, by the same scripts
print(subprocess.run([PY, str(HERE / "apply_music_studio_edits.py"), str(wt / "music_studio.py")], capture_output=True, env=ENV).stdout.decode().strip())
print(subprocess.run([PY, str(HERE / "th_keys_patch.py"), str(wt / "music_studio/locales/th.json")], capture_output=True, env=ENV).stdout.decode().strip())
shutil.copy(ROOT / "tests/test_songcraft.py", wt / "tests/test_songcraft.py")                 # the V3 test adapted to the Academy (aspect tiles filtered, lesson buttons excluded)
parts = []
for f in FILES:
    r = subprocess.run(["git", "diff", "--no-index", "--", str(snap / f), str(wt / f)], capture_output=True, cwd=ROOT)
    d = r.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    d = re.sub(r"^diff --git .*$", f"diff --git a/{f} b/{f}", d, flags=re.M)
    d = re.sub(r"^--- .*$", f"--- a/{f}", d, flags=re.M)
    d = re.sub(r"^\+\+\+ .*$", f"+++ b/{f}", d, flags=re.M)
    parts.append(d)
patch = "".join(parts)
(OUT / "song_critic_academy_task_only.patch").write_text(patch, encoding="utf-8", newline="\n")
print("patch bytes", len(patch))
# new files of the task, listed and copied
NEW = ["music_studio/songcraft_academy.py", "tests/test_academy.py"] + [f"music_studio/songcraft_knowledge/{p.name}" for p in sorted((ROOT / "music_studio/songcraft_knowledge").glob("*.md"))]
for f in NEW:
    dst = OUT / "new_files" / f
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / f, dst)
# check on a clean base: fresh worktree, V3 patch, then the Academy patch (no --recount)
wt2 = ROOT.parent / "wt_check_academy2"
if wt2.exists():
    git("worktree", "remove", "--force", str(wt2))
git("worktree", "add", "--detach", str(wt2), "HEAD")
git("apply", str(V3 / "PATCH/song_critic_v3_task_only.patch"), cwd=wt2)
(wt2 / "tests").mkdir(exist_ok=True)
shutil.copy(V3 / "PATCH/test_songcraft.py", wt2 / "tests/test_songcraft.py")
rc, o, e = git("apply", "--check", str(OUT / "song_critic_academy_task_only.patch"), cwd=wt2)
print("git apply --check (no --recount):", rc, e[-200:])
rc2, o, e = git("apply", str(OUT / "song_critic_academy_task_only.patch"), cwd=wt2)
print("applied:", rc2)
for f in NEW:
    dst = wt2 / f
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / f, dst)
shutil.copy(ROOT / "tests/_ui_harness.py", wt2 / "tests/_ui_harness.py")
shutil.copytree(ROOT / "comfy_apis", wt2 / "comfy_apis", dirs_exist_ok=True)
(wt2 / "docs").mkdir(exist_ok=True)
for d_ in ("realtime_internal_rhyme", "specialist_quality_audit_01", "song_critic_tune_02", "song_critic_v2"):
    shutil.copytree(ROOT / "docs" / d_, wt2 / "docs" / d_, dirs_exist_ok=True)
(wt2 / "docs/song_critic_v3").mkdir(parents=True, exist_ok=True)
for n in ("CANDIDATE_PROMPT.md", "songs_s5.py"):
    shutil.copy(V3 / n, wt2 / "docs/song_critic_v3" / n)
shutil.copytree(V3 / "RAW_RESPONSES", wt2 / "docs/song_critic_v3/RAW_RESPONSES", dirs_exist_ok=True)
res = {}
for t in ("test_academy", "test_songcraft", "test_critic_refs", "test_critic_actions", "test_spec_reports"):
    r = subprocess.run([PY, "tests/" + t + ".py"], capture_output=True, cwd=wt2, env=ENV, timeout=600)
    out = r.stdout.decode("utf-8", "replace").strip().splitlines()
    res[t] = out[-1] if out else r.stderr.decode("utf-8", "replace")[-200:]
print(res)
(OUT / "clean_base_check.txt").write_text(f"base = clean HEAD + the Song Critic V3 task patch (V3 is not committed)\ngit apply --check of the Academy patch (no --recount): rc={rc}\napplied: {rc2}\n" + "\n".join(f"{k}: {v}" for k, v in res.items()) + "\n", encoding="utf-8")
git("worktree", "remove", "--force", str(wt))
git("worktree", "remove", "--force", str(wt2))
git("worktree", "prune")
shutil.rmtree(snap)
