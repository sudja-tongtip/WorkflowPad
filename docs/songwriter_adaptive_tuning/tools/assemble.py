"""Assemble the Songwriter chat system exactly as the app does (md COMMON+CHAT + spec_extra), optionally with a candidate songwriter.md. Used by the tuning runner and the audit."""
import os, sys
from pathlib import Path

ROOT = Path("D:/AI/MyApps/WorkflowPad")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(encoding="utf-8")

_APP = None


def app():
    global _APP
    if _APP is None:
        from _ui_harness import App
        _APP = App()
    return _APP


def install_candidate(md_path):
    """Put a candidate songwriter.md into the (temporary) profile of the harness; never into the real AppData."""
    from music_studio import specialists as SP
    appdata = Path(os.environ["APPDATA"])
    assert str(appdata).lower().startswith(os.environ["TEMP"].lower().replace("\\", "/").split("/")[0].lower()) or "Temp" in str(appdata), appdata
    dst = appdata / "WorkflowPad" / "specialists" / "prompts" / "songwriter.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(Path(md_path).read_bytes())
    SP.reload_all()
    return dst


def chat_system():
    from music_studio import specialists as SP
    a = app()
    extra = a.found["spec_extra"]("songwriter")
    return SP.effective_system("songwriter", extra, mode="chat")[0], extra


if __name__ == "__main__":
    from music_studio import llm_chat as LC
    import re
    full, extra = chat_system()
    est = LC.estimate_tokens
    print("FULL chat system tokens:", est(full), "| md part:", est(full) - est(extra), "| spec_extra part:", est(extra))
    Path(sys.argv[1] if len(sys.argv) > 1 else "full_chat_system.txt").write_text(full, encoding="utf-8")
    # break the extra apart on its blank-line blocks
    for i, blk in enumerate([b for b in re.split(r"\n\n+", extra) if b.strip()], 1):
        print(f"  extra block {i}: {est(blk):5d} tokens | {blk.strip()[:90]!r}")
