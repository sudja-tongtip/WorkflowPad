# -*- coding: utf-8 -*-
import sys
p = sys.argv[1] if len(sys.argv) > 1 else "D:/AI/MyApps/WorkflowPad/music_studio.py"
raw = open(p, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")


def sub(old, new, count=1):
    global t
    assert t.count(old) == count, (t.count(old), old[:90])
    t = t.replace(old, new)


# the hand-off belongs to ONE song and sheet: it is checked again when the text is built
sub('''        ho_try = {"label": label, "finding_id": finding_id, "action_id": action_id, "rep": rep, "t": time.time()}''',
    '''        ho_try = {"label": label, "finding_id": finding_id, "action_id": action_id, "rep": rep, "t": time.time(), "song_id": S.song.id, "sheet_id": sheet0}''')
sub('''        ho_try = {"kind": "lesson", "label": label, "topic": topic, "technique": tid, "finding_id": "", "action_id": "", "rep": sr_get("song_critic"), "t": time.time()}''',
    '''        ho_try = {"kind": "lesson", "label": label, "topic": topic, "technique": tid, "finding_id": "", "action_id": "", "rep": sr_get("song_critic"), "t": time.time(), "song_id": S.song.id, "sheet_id": sheet0}''')
sub('''        st = S.song.settings
        if ho.get("kind") == "lesson":''', '''        if ho.get("song_id") and (ho.get("song_id") != S.song.id or ho.get("sheet_id") != S.song.book.active_id):            # another song or sheet is open now: nothing of this one may be attached
            return None, {"refused": True, "reason": "song", "tokens": 0, "budget": budget, "shortened": [], "left_out": []}
        st = S.song.settings
        if ho.get("kind") == "lesson":''')
sub('''                    append_chat(song.id, M.ChatMessage("note", I18N.tr("The songwriting lesson was NOT sent, and nothing was sent to the model: this conversation has no room for it. Start a new session (New session) and press Discuss again.") if is_lesson else
                                                       I18N.tr("The Song Critic report was NOT sent, and nothing was sent to the model: this conversation has no room for it. Start a new session (New session) and press Discuss again."),
                                                       specialist="songwriter", sheet_id=sheet0))''',
    '''                    if ho_info.get("reason") == "song":
                        refuse_msg = I18N.tr("Nothing was sent to the model: the song or sheet changed before the message went out, so the lesson or report was not attached. Press Discuss again.")
                    elif is_lesson:
                        refuse_msg = I18N.tr("The songwriting lesson was NOT sent, and nothing was sent to the model: this conversation has no room for it. Start a new session (New session) and press Discuss again.")
                    else:
                        refuse_msg = I18N.tr("The Song Critic report was NOT sent, and nothing was sent to the model: this conversation has no room for it. Start a new session (New session) and press Discuss again.")
                    append_chat(song.id, M.ChatMessage("note", refuse_msg, specialist="songwriter", sheet_id=sheet0))''')
sub('''what_sent = ("the songwriting lesson (" + ("one technique" if ho.get("technique") else "the whole lesson") + ")") if is_lesson else''',
    '''what_sent = ("the songwriting lesson (" + ("one technique" if ho.get("technique") else f"the whole lesson, {len(ho_info.get('techniques') or [])} techniques") + ")") if is_lesson else''')
# the references: an honest label and a link that opens an http(s) page in the browser
sub('''            refs.append(ft.Text(("\\u2713 " + I18N.tr("Source checked") if r.status == "verified" else I18N.tr("General knowledge, not a citation")) + ": " + r.supports + ((" \\u00b7 " + r.url) if r.url else ""), size=10, color=C["text3"], selectable=True))''',
    '''            refs.append(ft.Text((I18N.tr("Reference page checked (confirms only what is stated)") if r.status == "verified" else I18N.tr("General knowledge, not a citation")) + ": " + r.supports, size=10, color=C["text3"], selectable=True))
            link = AKB.safe_url(r.url)
            if link:                                                              # only a plain http(s) address is opened (the browser does the rest)
                refs.append(ft.Row([ft.TextButton(I18N.tr("Open source page"), icon=ft.Icons.OPEN_IN_NEW, url=link, tooltip=link, style=ft.ButtonStyle(padding=po(4, 0, 4, 0)))], spacing=0, wrap=True))''')
sub('''I18N.tr("Lessons are reference knowledge, not the AI's opinion about your song. [R1] marks a principle that a cited reference supports; text with no tag is this program's own synthesis; the examples were written for teaching.")''',
    '''I18N.tr("Lessons are reference knowledge, not the AI's opinion about your song. The techniques are options for the writer, not rules: a song that does not use them is not wrong. [R1] marks a statement that a cited reference page makes; text with no tag is this program's own synthesis, not an endorsed theory; the examples were written for teaching.")''')
open(p, "wb").write((t.replace("\n", "\r\n") if crlf else t).encode("utf-8"))
print("patched")
