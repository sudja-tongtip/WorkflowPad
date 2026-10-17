"""Tests for Groom Chat (EDIT CURRENT DRAFT): output-tag parser, prompt routing, malformed-output safety.

Run with:  python -m story_generator.tests.test_groom_chat

No real LLM is called — the transport function is replaced. The UI routing (latest editor text, New from
the original source, Outline handoff) is covered by driving the real dialog handlers separately.
"""
import asyncio
import json
import tempfile
from pathlib import Path

from .. import llm_engine_groom as G

_fails: list = []


def check(name: str, cond: bool, extra: str = ""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        _fails.append(name)


def test_parser():
    story, reply = G.parse_chat_edit_output(
        "<revised_story>\nFull revised story here.\n</revised_story>\n<chat_reply>\nShortened the ending.\n</chat_reply>")
    check("valid response: story + reply split", story == "Full revised story here." and reply == "Shortened the ending.")
    story, reply = G.parse_chat_edit_output("<revised_story>Only the story.</revised_story>")
    check("missing chat_reply -> story kept, reply falls back to 'Updated.'", story == "Only the story." and reply == "Updated.")
    story, _ = G.parse_chat_edit_output("Here is the story: she walked home.")
    check("missing revised_story -> None (no guessing from prose)", story is None)
    story, _ = G.parse_chat_edit_output("<revised_story>cut off before the closing tag")
    check("unclosed revised_story -> None", story is None)
    story, _ = G.parse_chat_edit_output("<revised_story>   </revised_story><chat_reply>x</chat_reply>")
    check("empty revised_story -> None", story is None)
    s, style, _ = G._parse_create_output("<revised_story>S</revised_story><source_style>ink wash</source_style>")
    check("CREATE: tagged story + source_style", s == "S" and style == "ink wash")
    s, style, _ = G._parse_create_output("plain story <source_style>ink wash</source_style>")
    check("CREATE: untagged response still yields the story (pre-chat behavior)", s == "plain story" and style == "ink wash")


def test_prompts():
    create, edit = G._build_groom_system_prompt("create"), G._build_groom_system_prompt("edit")
    check("both operations share the common core", create.startswith(G._GROOM_CORE) and edit.startswith(G._GROOM_CORE))
    check("CREATE contract has no chat_reply, EDIT has both tags", "<chat_reply>" not in create and "<chat_reply>" in edit and "<revised_story>" in edit)
    check("EDIT states the authority order and manual-edit rule", "USER REQUEST > CURRENT GROOMED STORY" in edit and "manual user edits" in edit)
    check("compat name is the CREATE assembly", G._GROOM_SYSTEM_PROMPT == create)
    check("edit is one core + one small block, not a second full prompt", len(edit) - len(G._GROOM_CORE) < 3500, str(len(edit)))
    u = G._build_groom_edit_user_prompt("ORIG TEXT", "DRAFT TEXT", "do X", lang="en", genre="horror", genre2="none", creativity="balanced")
    check("edit user prompt carries original, draft and request", all(x in u for x in ("ORIG TEXT", "DRAFT TEXT", "do X", "EDIT CURRENT DRAFT")))


def test_chat_edit_routing_and_safety():
    sent = {}
    replies = {"text": ""}

    async def fake_local(wf, system_prompt, user_prompt, **k):
        sent["system"], sent["user"] = system_prompt, user_prompt
        return replies["text"]

    orig = G._call_local_text
    G._call_local_text = fake_local
    tmp = Path(tempfile.mkdtemp())
    wf_dir_before = G.LLM_DIR
    G.LLM_DIR = tmp
    (tmp / "fake.json").write_text(json.dumps({"1": {"class_type": "LLMSessionChatNode", "inputs": {}}}), encoding="utf-8")
    try:
        replies["text"] = "<revised_story>NEW STORY</revised_story><chat_reply>Done.</chat_reply>"
        r = asyncio.run(G.run_story_chat_edit("ORIGINAL", "LATEST EDITOR TEXT", "shorten", "fake"))
        check("valid edit: ok + revised story + reply", r["ok"] and r["revised_story"] == "NEW STORY" and r["chat_reply"] == "Done.")
        check("the LATEST editor text and the original source are what the model receives",
              "LATEST EDITOR TEXT" in sent["user"] and "ORIGINAL" in sent["user"] and sent["system"] == G._build_groom_system_prompt("edit"))
        replies["text"] = "<revised_story>LATEST   EDITOR\nTEXT</revised_story>"
        r = asyncio.run(G.run_story_chat_edit("ORIGINAL", "LATEST EDITOR TEXT", "shorten", "fake"))
        check("same story back (only whitespace differs) -> changed False, no false 'Updated.'", r["ok"] and r["changed"] is False and r["chat_reply"] != "Updated.", r["chat_reply"])
        replies["text"] = "<revised_story>NEW STORY</revised_story>"
        r = asyncio.run(G.run_story_chat_edit("ORIGINAL", "LATEST EDITOR TEXT", "shorten", "fake"))
        check("different story, no chat_reply -> changed True, reply 'Updated.'", r["changed"] is True and r["chat_reply"] == "Updated.")
        replies["text"] = "Sure! Here you go: " + "the story is now shorter. " * 40
        r = asyncio.run(G.run_story_chat_edit("ORIGINAL", "LATEST EDITOR TEXT", "shorten", "fake"))
        check("malformed output: not ok, revised_story None (caller keeps the draft)", (not r["ok"]) and r["revised_story"] is None and r["error"])
        replies["text"] = "<chat_reply>Sorry about that. Tell me what to change, e.g. shorten the ending.</chat_reply>"
        r = asyncio.run(G.run_story_chat_edit("ORIGINAL", "LATEST EDITOR TEXT", "งง", "fake"))
        check("chat-only reply (tag, no story): ok, story untouched, reply shown", r["ok"] and r["chat_only"] and r["revised_story"] == "LATEST EDITOR TEXT" and r["chat_reply"].startswith("Sorry"))
        replies["text"] = "Happy to help! What would you like to change?"
        r = asyncio.run(G.run_story_chat_edit("ORIGINAL", "LATEST EDITOR TEXT", "hi", "fake"))
        check("short plain reply (no tags): shown as chat, story untouched", r["ok"] and r["chat_only"] and r["changed"] is False)
        replies["text"] = "<revised_story>cut off with no close " + "x" * 700
        r = asyncio.run(G.run_story_chat_edit("ORIGINAL", "LATEST EDITOR TEXT", "shorten", "fake"))
        check("truncated story tag is never shown as chat nor applied", (not r["ok"]) and r["revised_story"] is None)
        r = asyncio.run(G.run_story_chat_edit("ORIGINAL", "LATEST EDITOR TEXT", "   ", "fake"))
        check("empty request is rejected without a call", (not r["ok"]) and r["error"] == "empty_request")
    finally:
        G._call_local_text = orig
        G.LLM_DIR = wf_dir_before


def main():
    test_parser()
    test_prompts()
    test_chat_edit_routing_and_safety()
    print("\n%d failure(s)" % len(_fails))
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
