# -*- coding: utf-8 -*-
"""Builds PATCH/song_critic_academy_critique_task_only.patch: the changes since the Academy V1 delivery, on a clean base = HEAD + the Song Critic V3 task patch + the Academy V1 task patch and new files
(neither is committed). The edits of this round are replayed on that base by the same scripts that edited the real tree (edit_*.py), the other files are copied, so the patch contains no hunk of the unrelated
Export Audio work. It is then checked: `git apply --check` WITHOUT --recount on a fresh base, apply, and the suites run on the result."""
import filecmp, os, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path("D:/AI/MyApps/WorkflowPad")
HERE = ROOT / "docs/song_critic_academy_critique"
SC = HERE / "scripts"
V3 = ROOT / "docs/song_critic_v3/PATCH"
V1 = ROOT / "docs/song_critic_academy/PATCH"
OUT = HERE / "PATCH"
OUT.mkdir(exist_ok=True)
os.chdir(ROOT)
PY = sys.executable
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def git(*a, cwd=None):
    r = subprocess.run(["git", *a], capture_output=True, cwd=cwd or ROOT)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def make_base(name):
    wt = ROOT.parent / name
    if wt.exists():
        git("worktree", "remove", "--force", str(wt))
    assert git("worktree", "add", "--detach", str(wt), "HEAD")[0] == 0
    assert git("apply", str(V3 / "song_critic_v3_task_only.patch"), cwd=wt)[0] == 0
    (wt / "tests").mkdir(exist_ok=True)
    shutil.copy(V3 / "test_songcraft.py", wt / "tests/test_songcraft.py")
    shutil.copy(ROOT / "tests/_ui_harness.py", wt / "tests/_ui_harness.py")
    assert git("apply", str(V1 / "song_critic_academy_task_only.patch"), cwd=wt)[0] == 0
    for f in sorted((V1 / "new_files").rglob("*")):
        if f.is_file():
            dst = wt / f.relative_to(V1 / "new_files")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(f, dst)
    return wt


wt = make_base("wt_crit_a")
KNOW = sorted(p.name for p in (ROOT / "music_studio/songcraft_knowledge").glob("*.md"))
FILES = ["music_studio.py", "music_studio/locales/th.json", "music_studio/spec_reports.py", "music_studio/songcraft_academy.py", "music_studio/prompts/specialists/song_critic.md",
         "tests/test_academy.py", "tests/test_songcraft.py", "tests/test_spec_reports.py"] + [f"music_studio/songcraft_knowledge/{n}" for n in KNOW]
snap = HERE / "_base_snap"
if snap.exists():
    shutil.rmtree(snap)
for f in FILES:
    if (wt / f).exists():
        (snap / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(wt / f, snap / f)
# the base must be the Academy V1 delivery: its new files equal the snapshot taken before this round
v1s = pathlib.Path("C:/Users/sudja/AppData/Local/Temp/claude/a8ec2d1fb949c4ce/f45a390f-0139-4fee-892a-2bab035e394a/scratchpad/v1_snapshot")
same = all(filecmp.cmp(v1s / f, wt / f, shallow=False) for f in ["music_studio/songcraft_academy.py", "tests/test_academy.py"] + [f"music_studio/songcraft_knowledge/{n}" for n in KNOW if (v1s / "music_studio/songcraft_knowledge" / n).exists()])
print("base equals the Academy V1 delivery:", same)
# the edits of this round, by the same scripts
for script, target in (("edit_music_studio_hooks.py", "music_studio.py"), ("edit_music_studio_cards.py", "music_studio.py"), ("edit_th_polish.py", "music_studio/locales/th.json"), ("edit_th_cards.py", "music_studio/locales/th.json"),
                       ("edit_spec_reports_patch_sr1.py", "music_studio/spec_reports.py"), ("edit_spec_reports_patch_sr2.py", "music_studio/spec_reports.py"), ("edit_spec_reports_patch_sr3.py", "music_studio/spec_reports.py")):
    r = subprocess.run([PY, str(SC / script), str(wt / target)], capture_output=True, env=ENV)
    print(script, r.returncode, r.stdout.decode().strip(), r.stderr.decode()[-200:])
for f in ["music_studio/songcraft_academy.py", "music_studio/prompts/specialists/song_critic.md", "tests/test_academy.py", "tests/test_songcraft.py", "tests/test_spec_reports.py"] + [f"music_studio/songcraft_knowledge/{n}" for n in KNOW]:
    (wt / f).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / f, wt / f)
(wt / "tests/fixtures").mkdir(exist_ok=True)
shutil.copy(ROOT / "tests/fixtures/critique_mixed_song.txt", wt / "tests/fixtures/critique_mixed_song.txt")
# sanity: the replayed files equal the real working files except for the unrelated Export Audio hunks
for f in ("music_studio.py", "music_studio/spec_reports.py", "music_studio/locales/th.json"):
    r = subprocess.run(["git", "diff", "--no-index", "--stat", "--", str(wt / f), str(ROOT / f)], capture_output=True, cwd=ROOT)
    print("replayed vs working copy", f, r.stdout.decode("utf-8", "replace").strip().splitlines()[-1:] or "identical")
parts = []
for f in FILES:
    a, b = snap / f, wt / f
    if not b.exists() or (a.exists() and filecmp.cmp(a, b, shallow=False)):
        continue
    r = subprocess.run(["git", "diff", "--no-index", "--", str(a if a.exists() else "NUL"), str(b)], capture_output=True, cwd=ROOT)
    d = r.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    d = re.sub(r"^diff --git .*$", f"diff --git a/{f} b/{f}", d, flags=re.M)
    d = re.sub(r"^--- .*$", f"--- a/{f}", d, flags=re.M)
    d = re.sub(r"^\+\+\+ .*$", f"+++ b/{f}", d, flags=re.M)
    parts.append(d)
patch = "".join(parts)
(OUT / "song_critic_academy_critique_task_only.patch").write_text(patch, encoding="utf-8", newline="\n")
NEW = ["tests/fixtures/critique_mixed_song.txt"]
for f in NEW:
    dst = OUT / "new_files" / f
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / f, dst)
print("patch bytes", len(patch), "files", len(parts))
# check on a fresh base
wt2 = make_base("wt_crit_b")
rc, o, e = git("apply", "--check", str(OUT / "song_critic_academy_critique_task_only.patch"), cwd=wt2)
print("git apply --check (no --recount):", rc, e[-300:])
rc2, o, e = git("apply", str(OUT / "song_critic_academy_critique_task_only.patch"), cwd=wt2)
print("applied:", rc2, e[-200:])
for f in NEW:
    dst = wt2 / f
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / f, dst)
shutil.copytree(ROOT / "comfy_apis", wt2 / "comfy_apis", dirs_exist_ok=True)
(wt2 / "docs").mkdir(exist_ok=True)
for d_ in ("realtime_internal_rhyme", "specialist_quality_audit_01", "song_critic_tune_02", "song_critic_v2"):
    shutil.copytree(ROOT / "docs" / d_, wt2 / "docs" / d_, dirs_exist_ok=True)
(wt2 / "docs/song_critic_v3").mkdir(parents=True, exist_ok=True)
for n in ("CANDIDATE_PROMPT.md", "songs_s5.py"):
    shutil.copy(ROOT / "docs/song_critic_v3" / n, wt2 / "docs/song_critic_v3" / n)
shutil.copytree(ROOT / "docs/song_critic_v3/RAW_RESPONSES", wt2 / "docs/song_critic_v3/RAW_RESPONSES", dirs_exist_ok=True)
shutil.copytree(HERE / "REAL_RUNS", wt2 / "docs/song_critic_academy_critique/REAL_RUNS", dirs_exist_ok=True)
res = {}
for t in ("test_academy", "test_songcraft", "test_critic_refs", "test_critic_actions", "test_spec_reports"):
    r = subprocess.run([PY, "tests/" + t + ".py"], capture_output=True, cwd=wt2, env=ENV, timeout=900)
    out = r.stdout.decode("utf-8", "replace").strip().splitlines()
    res[t] = out[-1] if out else r.stderr.decode("utf-8", "replace")[-200:]
print(res)
(OUT / "clean_base_check.txt").write_text(f"base = clean HEAD + the Song Critic V3 task patch + the Academy V1 task patch and new files (none of them committed)\nthe base equals the Academy V1 delivery: {same}\ngit apply --check of this patch (no --recount): rc={rc}\napplied: {rc2}\n"
                                          + "\n".join(f"{k}: {v}" for k, v in res.items()) + "\n", encoding="utf-8")
for w in (wt, wt2):
    git("worktree", "remove", "--force", str(w))
git("worktree", "prune")
shutil.rmtree(snap)
