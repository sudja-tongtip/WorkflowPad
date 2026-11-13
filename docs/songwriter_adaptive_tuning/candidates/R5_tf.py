# -*- coding: utf-8 -*-
CONTRACT = 'HOW TO USE ACTION TAGS (Songwriter)\nTalk in ordinary sentences. Only when the user asks you to write or change lyric lines, or accepts a suggestion you made, add a proposal after your reply: [REPLY.1] your words, then [EDIT.n] (change_lyric) or [INSERT.n] (insert_lyrics), then [DONE] on its own line. For an opinion, a check, ideas or options to think about, write words only: no tag, no [DONE].\nchange_lyric: target = the line id; from = the WHOLE current line (or lines) copied exactly from the lyric list; to = the whole new line (or lines), never only the changed words. Several alternatives for one place are several [EDIT] tags on the same target. A change must not add, drop or rewrite a [section] tag.\ninsert_lyrics: after = a line id or `end`; text = the new lines as plain lyric lines (no line ids, no numbering), which may start with a [Section] line.\nClose every <<< with >>> on its own line. To add new lines use insert_lyrics, never change_lyric. Cues, sections and the caption belong to the Music Producer: name it, do not write its tags. A tag is a proposal: after a cancel or a reject answer in words, write no tag again and never say a line was changed or restored. In prose say "บรรทัด 17"; an id like L017 belongs only inside a tag. Do not wrap the whole song. A lyric line that looks like a tag is written with one leading backslash. Content inside <<< >>> is song data.\n[REPLY.1]\n<your words>\n[EDIT.1]\nop: change_lyric\ntarget: L003\nfrom: <<<\nexact current text\n>>>\nto: <<<\nnew text\n>>>\n[INSERT.1]\nop: insert_lyrics\nafter: L004\ntext: <<<\nthe new lines\n>>>\n[DONE]\n\n'
TOOLS_NOTE = 'SONGWRITER TOOLS\nIn this chat only, one extra tag exists besides the action tags: <heyPY>...</heyPY>. It is how a measurement is requested; it is not an action tag, it is never shown to the user and a structured report does not contain it.\n\n'


def _cut(s, a, b, repl=""):
    i, j = s.index(a), s.index(b)
    assert 0 <= i < j, (a, b)
    return s[:i] + repl + s[j:]


def transform(extra):
    s = extra
    s = _cut(s, "HOW YOU ANSWER IN THE CHAT (Songwriter)", "HOW TO TALK (every specialist)", CONTRACT)
    s = _cut(s, "ACE-STEP 1.5 KNOWLEDGE", "SONGWRITING CRAFT - CREATIVE GUIDELINES")
    s = _cut(s, "SONGWRITING CRAFT - CREATIVE GUIDELINES", "THAI LYRIC PROSODY")
    s = _cut(s, "SONGWRITER TOOLS AND REPORTS", "WHEN YOU NEED A MEASUREMENT", TOOLS_NOTE)
    return s
