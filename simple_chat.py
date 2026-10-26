"""Simple Chat Studio — Flet UI for ComfyUI LLMSessionChatNode.

Drop-in replacement for simple_chat.py. Keeps the same DATA_FILE, workflow directory,
and ComfyUI API. Each chat has a distinct persistent backend session ID.

This file uses the modern Flet API already used by simple_chat.py (show_dialog,
Padding/Border, Icons, shift_enter, and async event handlers).
"""

import asyncio
import json
import os
import re
import sys
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import flet as ft
import app_error as _app_error

APP_NAME = "Simple Chat Studio"
APP_VERSION = "0.2.0"
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
LLM_DIR = APP_ROOT / "comfy_apis" / "llm"
DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "WorkflowPad"
DATA_FILE = DATA_DIR / "simple_chat.json"  # Existing history; backward compatible.
SHARED_LLM_CFG = DATA_DIR / "llm_chat_config.json"
EXPORT_DIR = DATA_DIR / "chat_exports"
DEFAULT_SYSTEM = "You are a friendly, helpful assistant. Reply in the language the user writes in."
DEFAULT_SETTINGS = {
    "host": "127.0.0.1:8188",
    "workflow": "",
    "max_tokens": 2048,
    "temperature": 0.7,
    "render_markdown": True,
    "auto_scroll": True,
    "sidebar_open": True,
}
TIMEOUT_S = 300

C = {
    "bg": "#0C1220", "sidebar": "#101827", "surface": "#151F30", "surface2": "#1B293D",
    "hover": "#243652", "selected": "#1B3556", "divider": "#29384E",
    "accent": "#65A9FF", "accent_dim": "#153B68", "green": "#35D6A1", "warning": "#F3B55E",
    "red": "#FF7182", "error_bg": "#3E202B", "text": "#ECF3FF", "sub": "#A7B6CB",
    "muted": "#74869F", "white": "#FFFFFF", "input": "#172438",
}


def new_session(title="New conversation", system=DEFAULT_SYSTEM, workflow="", temperature=0.7, max_tokens=2048, host="127.0.0.1:8188"):
    now = time.time()
    return {
        "id": uuid.uuid4().hex[:12], "title": title, "system": system,
        "workflow": workflow, "host": host, "temperature": float(temperature), "max_tokens": int(max_tokens),
        "messages": [], "created_at": now, "updated_at": now, "pinned": False,
        "draft": "",
    }


def load_data():
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        settings = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}
        sessions = []
        for s in data.get("sessions", []):
            if not isinstance(s, dict) or not s.get("id"):
                continue
            s.setdefault("title", "Chat")
            s.setdefault("system", DEFAULT_SYSTEM)
            s.setdefault("workflow", settings.get("workflow", ""))
            s.setdefault("host", settings.get("host", DEFAULT_SETTINGS["host"]))
            s.setdefault("temperature", settings.get("temperature", 0.7))
            s.setdefault("max_tokens", settings.get("max_tokens", 2048))
            s.setdefault("created_at", 0)
            s.setdefault("updated_at", s["created_at"])
            s.setdefault("pinned", False)
            s.setdefault("draft", "")
            s["messages"] = [m for m in s.get("messages", []) if isinstance(m, dict)
                             and m.get("role") in ("user", "assistant", "error") and isinstance(m.get("text"), str)]
            sessions.append(s)
        if not sessions:
            sessions = [new_session(workflow=settings["workflow"], temperature=settings["temperature"],
                                    max_tokens=settings["max_tokens"], host=settings["host"])]
        return {"settings": settings, "sessions": sessions, "active": data.get("active")}
    except FileNotFoundError:
        return {"settings": dict(DEFAULT_SETTINGS), "sessions": [new_session()], "active": None}
    except Exception:
        # Preserve malformed history, rather than silently overwriting it.
        try:
            DATA_FILE.replace(DATA_FILE.with_name(f"simple_chat.damaged-{int(time.time())}.json"))
        except Exception:
            pass
        return {"settings": dict(DEFAULT_SETTINGS), "sessions": [new_session()], "active": None}


def save_data(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    keys = ("id", "title", "system", "workflow", "host", "temperature", "max_tokens", "messages",
            "created_at", "updated_at", "pinned", "draft")
    payload = {
        "settings": data["settings"], "active": data["active"],
        "sessions": [{k: s[k] for k in keys} for s in data["sessions"]],
    }
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


def list_workflows():
    results = []
    if LLM_DIR.is_dir():
        for f in sorted(LLM_DIR.glob("*.json")):
            try:
                wf = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(wf, dict) and any(isinstance(n, dict) and n.get("class_type") == "LLMSessionChatNode"
                                                for n in wf.values()):
                    results.append(f.name)
            except Exception:
                continue
    return results


def _url(host, path):
    host = host.strip() or DEFAULT_SETTINGS["host"]
    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/") + path
    return "http://" + host.rstrip("/") + path


def build_workflow(wf_name, settings, sess, user_text):
    # Accept only an actual workflow from the approved directory; no path traversal.
    if wf_name not in list_workflows():
        raise RuntimeError("Selected workflow is missing or lacks LLMSessionChatNode.")
    wf = json.loads((LLM_DIR / wf_name).read_text(encoding="utf-8"))
    try:
        history_dir = json.loads(SHARED_LLM_CFG.read_text(encoding="utf-8")).get("history_dir", "")
    except Exception:
        history_dir = ""
    found = False
    for node in wf.values():
        if isinstance(node, dict) and node.get("class_type") == "LLMSessionChatNode":
            found = True
            inputs = node["inputs"]
            inputs.update({"user_text": user_text, "system_prompt": sess["system"],
                           "session_id": "chat_" + sess["id"], "reset_session": False,
                           "max_tokens": int(settings["max_tokens"]),
                           "temperature": float(settings["temperature"])})
            if history_dir:
                inputs["history_dir"] = history_dir
    if not found:
        raise RuntimeError("The workflow must contain LLMSessionChatNode.")
    return {k: v for k, v in wf.items() if isinstance(v, dict) and "class_type" in v}


def comfy_queue(host, wf):
    req = urllib.request.Request(_url(host, "/prompt"),
                                 data=json.dumps({"prompt": wf, "client_id": uuid.uuid4().hex}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ComfyUI rejected workflow (HTTP {e.code}): "
                           f"{e.read().decode(errors='replace')[:350]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach ComfyUI at {host}: {e.reason}")


def comfy_hist(host, pid):
    with urllib.request.urlopen(_url(host, f"/history/{pid}"), timeout=10) as response:
        return json.loads(response.read())


def comfy_ping(host):
    with urllib.request.urlopen(_url(host, "/system_stats"), timeout=5) as response:
        response.read(128)
        return response.status == 200


def comfy_cancel(host, pid):
    """Cancel only our own job; do not interrupt another user's running prompt."""
    def post(path, body):
        urllib.request.urlopen(urllib.request.Request(
            _url(host, path), data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"}), timeout=5).close()
    try:
        post("/queue", {"delete": [pid]})
        with urllib.request.urlopen(_url(host, "/queue"), timeout=5) as response:
            running = json.loads(response.read()).get("queue_running", [])
        if any(len(item) > 1 and item[1] == pid for item in running):
            post("/interrupt", {})
    except Exception:
        pass


def export_conversation(sess, kind="md"):
    """Export a local backup without requiring FilePicker or modifying session state."""
    if kind not in ("md", "json"):
        raise ValueError("Invalid export format")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    basename = re.sub(r"[^\w\-]+", "_", sess["title"], flags=re.UNICODE).strip("_")[:45] or "chat"
    target = EXPORT_DIR / f"{basename}_{sess['id']}_{datetime.now():%Y%m%d_%H%M%S}.{kind}"
    if kind == "json":
        target.write_text(json.dumps({"title": sess["title"], "system": sess["system"],
                                      "messages": sess["messages"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        output = [f"# {sess['title']}", ""]
        for m in sess["messages"]:
            role = {"user": "You", "assistant": "Assistant", "error": "Error"}.get(m["role"], "Message")
            output += [f"## {role}", "", m["text"], "", "---", ""]
        target.write_text("\n".join(output), encoding="utf-8")
    return target


def time_label(stamp):
    if not stamp:
        return ""
    try:
        return datetime.fromtimestamp(float(stamp)).strftime("%d %b · %H:%M")
    except (TypeError, ValueError, OverflowError):
        return ""


def main(page: ft.Page):
    page.title = f"{APP_NAME} · {APP_VERSION}"
    page.padding = 0
    page.bgcolor = C["bg"]
    page.theme_mode = ft.ThemeMode.DARK
    try:
        page.window.width, page.window.height = 1160, 800
        page.window.min_width, page.window.min_height = 640, 480
        _ico = APP_ROOT / "icons" / "simple_chat.ico"
        if _ico.exists():
            page.window.icon = str(_ico)
    except Exception:
        pass

    data = load_data()
    sessions = data["sessions"]
    settings = data["settings"]
    workflows = list_workflows()
    if settings.get("workflow") not in workflows:
        settings["workflow"] = workflows[0] if workflows else ""
    for s in sessions:
        if not s.get("workflow") or s["workflow"] not in workflows:
            s["workflow"] = settings["workflow"]  # Migration or missing workflow.
    active = [next((s for s in sessions if s["id"] == data["active"]), sessions[0])]
    busy = {}  # session_id -> {"pid": str|None, "host": str, "cancel": bool}
    sidebar_visible = [bool(settings.get("sidebar_open", True))]
    connection = [("unknown", None)]  # (status, host) to avoid stale status across servers

    def icon_btn(icon, tooltip, callback, color=None, size=18):
        return ft.IconButton(icon, tooltip=tooltip, on_click=callback, icon_color=color or C["sub"],
                             icon_size=size, width=34, height=34, padding=5)

    def persist():
        data["active"] = active[0]["id"]
        try:
            save_data(data)
        except Exception as exc:
            notice.value = f"Save failed: {exc}"
            notice.color = C["red"]

    def banner(message, tone="sub"):
        notice.value = message
        notice.color = C.get(tone, C["sub"])
        page.update()

    async def clipboard_copy(content):
        try:
            await ft.Clipboard().set(content)
            banner("Copied to clipboard.", "green")
        except Exception as exc:
            banner(f"Unable to copy: {exc}", "red")

    def message_control(msg):
        role, content = msg["role"], msg["text"]
        mine, error = role == "user", role == "error"
        icon = ft.Icons.PERSON_OUTLINE if mine else (ft.Icons.ERROR_OUTLINE if error else ft.Icons.AUTO_AWESOME)
        body = (ft.Markdown(value=content, selectable=True) if role == "assistant" and settings.get("render_markdown", True)
                else ft.Text(content, selectable=True, size=13, color=C["text"]))
        async def copy_this_message(_e=None):
            await clipboard_copy(content)
        card = ft.Container(
            bgcolor=C["accent_dim"] if mine else (C["error_bg"] if error else C["surface"]),
            border_radius=14,
            border=ft.Border.all(1, C["red"] if error else C["divider"]),
            padding=ft.Padding(left=15, top=12, right=15, bottom=9),
            content=ft.Column([
                body,
                ft.Row([
                    ft.Text(time_label(msg.get("ts")), size=10, color=C["muted"]),
                    icon_btn(ft.Icons.CONTENT_COPY, "Copy message", copy_this_message, size=14),
                ], spacing=4, alignment=ft.MainAxisAlignment.END),
            ], spacing=4, tight=True), expand=True, expand_loose=True)
        return ft.Row([
            ft.Container(
                content=ft.Icon(icon, size=17, color=C["accent"] if not error else C["red"]),
                bgcolor=C["surface2"], border_radius=10, padding=9,
            ),
            card,
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START,
           alignment=ft.MainAxisAlignment.END if mine else ft.MainAxisAlignment.START)

    notice = ft.Text("", size=11, color=C["muted"], max_lines=2)
    chat_list = ft.ListView(expand=True, spacing=18, padding=ft.Padding(left=26, top=22, right=26, bottom=20),
                            auto_scroll=bool(settings.get("auto_scroll", True)))
    query = ft.TextField(hint_text="Search conversations", text_size=12, height=42,
                         prefix_icon=ft.Icons.SEARCH, bgcolor=C["surface"], border_color=C["divider"],
                         focused_border_color=C["accent"], color=C["text"],
                         hint_style=ft.TextStyle(color=C["muted"]), content_padding=9)
    chat_input = ft.TextField(
        hint_text="Ask anything…  (Enter to send · Shift+Enter for new line)",
        multiline=True, min_lines=1, max_lines=6, shift_enter=True, expand=True,
        text_size=13, bgcolor=C["input"], color=C["text"],
        border_color=C["divider"], focused_border_color=C["accent"], border_radius=12,
        hint_style=ft.TextStyle(color=C["muted"]),
    )
    send_button = icon_btn(ft.Icons.ARROW_UPWARD_ROUNDED, "Send message", lambda e: send(), C["accent"], 23)
    stop_button = icon_btn(ft.Icons.STOP_CIRCLE_ROUNDED, "Stop generation", lambda e: stop(), C["red"], 23)
    tabs_row = ft.Row(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)
    sidebar_list = ft.Column(spacing=3, scroll=ft.ScrollMode.AUTO, expand=True)
    sidebar = ft.Container(width=252, bgcolor=C["sidebar"], visible=sidebar_visible[0],
                           padding=ft.Padding(left=12, top=16, right=12, bottom=12),
                           border=ft.Border(right=ft.BorderSide(1, C["divider"])))
    current_title = ft.Text("", size=17, weight=ft.FontWeight.W_600, color=C["text"],
                            overflow=ft.TextOverflow.ELLIPSIS, max_lines=1)
    current_detail = ft.Text("", size=11, color=C["muted"], max_lines=1,
                             overflow=ft.TextOverflow.ELLIPSIS)
    connection_label = ft.Text("Not checked", color=C["muted"], size=11)
    connection_icon = ft.Icon(ft.Icons.CIRCLE_OUTLINED, size=11, color=C["muted"])

    def update_connection_indicator():
        state = connection[0][0] if connection[0][1] == active[0]["host"] else "unknown"
        color = {"online": C["green"], "offline": C["red"]}.get(state, C["muted"])
        connection_icon.color = color
        connection_label.color = color
        connection_label.value = {"online": "ComfyUI connected", "offline": "ComfyUI offline"}.get(state, "Connection unchecked")

    def on_search(_e=None):
        render_sidebar()
        page.update()

    query.on_change = on_search

    def render_sidebar():
        needle = (query.value or "").strip().casefold()
        found = [s for s in sessions if not needle or needle in s["title"].casefold()
                 or any(needle in m["text"].casefold() for m in s["messages"])]
        found.sort(key=lambda s: (not s["pinned"], -float(s.get("updated_at") or 0)))
        controls = []
        if not found:
            controls.append(ft.Container(content=ft.Text("No matching conversations", size=12, color=C["muted"]), padding=14))
        last_group = None
        for s in found:
            group = "PINNED" if s["pinned"] else "CONVERSATIONS"
            if group != last_group:
                controls.append(ft.Container(content=ft.Text(group, size=10, color=C["muted"], weight=ft.FontWeight.W_600),
                                             padding=ft.Padding(left=9, top=16, right=0, bottom=5)))
                last_group = group
            is_active = active[0] is s
            controls.append(ft.Container(
                border_radius=10, bgcolor=C["selected"] if is_active else C["sidebar"],
                padding=ft.Padding(left=7, top=4, right=0, bottom=4),
                content=ft.Row([
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.CHAT_BUBBLE_OUTLINE, size=15, color=C["accent"] if is_active else C["muted"]),
                            ft.Column([
                                ft.Text(s["title"], size=12, color=C["text"] if is_active else C["sub"],
                                        weight=ft.FontWeight.W_600 if is_active else ft.FontWeight.NORMAL,
                                        overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                                ft.Text(f"{len(s['messages'])} messages" + ("  ·  thinking" if s["id"] in busy else ""),
                                        size=10, color=C["muted"], max_lines=1),
                            ], spacing=2, tight=True, expand=True),
                        ], spacing=9), expand=True, ink=True, border_radius=8,
                        on_click=lambda e, s=s: select_session(s)),
                    ft.PopupMenuButton(icon=ft.Icons.MORE_VERT, icon_color=C["muted"], icon_size=16,
                                       items=[
                                           ft.PopupMenuItem(content="Rename", on_click=lambda e, s=s: rename(s)),
                                           ft.PopupMenuItem(content="Unpin" if s["pinned"] else "Pin", on_click=lambda e, s=s: toggle_pin(s)),
                                           ft.PopupMenuItem(content="Export Markdown", on_click=lambda e, s=s: export_chat(s, "md")),
                                           ft.PopupMenuItem(content="Delete…", on_click=lambda e, s=s: delete_session(s)),
                                       ]),
                ], spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ))
        sidebar_list.controls = controls

    def empty_state():
        examples = [
            ("Review code", "ช่วยรีวิวโค้ดนี้และหาจุดที่ควรแก้ไข"),
            ("Brainstorm", "ช่วยระดมไอเดียและเสนอทางเลือกใหม่ ๆ"),
            ("Explain concept", "ช่วยอธิบายเรื่องนี้เป็นขั้นตอนแบบเข้าใจง่าย"),
        ]
        return ft.Container(
            padding=ft.Padding(left=14, top=60, right=14, bottom=0),
            content=ft.Column([
                ft.Icon(ft.Icons.AUTO_AWESOME, size=38, color=C["accent"]),
                ft.Text("What would you like to explore?", size=22, weight=ft.FontWeight.W_600, color=C["text"],
                        text_align=ft.TextAlign.CENTER),
                ft.Text("Local AI via ComfyUI · Each conversation has its own memory.", size=12, color=C["muted"],
                        text_align=ft.TextAlign.CENTER),
                ft.Container(height=14),
                *[ft.OutlinedButton(label, on_click=lambda e, t=text: prefill(t)) for label, text in examples],
            ], spacing=10, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        )

    def render_chat():
        s = active[0]
        chat_list.auto_scroll = bool(settings.get("auto_scroll", True))
        chat_list.controls = [message_control(m) for m in s["messages"]]
        if s["id"] in busy:
            job = busy[s["id"]]
            chat_list.controls.append(ft.Row([
                ft.ProgressRing(width=17, height=17, stroke_width=2, color=C["accent"]),
                ft.Text("Stopping…" if job["cancel"] else "Generating response…", size=12, color=C["sub"]),
            ], spacing=10))
        elif not s["messages"]:
            chat_list.controls = [empty_state()]
        running = s["id"] in busy
        send_button.visible = not running
        stop_button.visible = running
        stop_button.disabled = running and busy[s["id"]]["cancel"]
        current_title.value = s["title"]
        workflow = s.get("workflow") or "No workflow selected"
        current_detail.value = f"{workflow}  ·  Temp {s['temperature']:g}  ·  Max {s['max_tokens']} tokens"

    def render_tabs():
        tab_controls = []
        for s in sessions:
            selected = s is active[0]
            tab_controls.append(ft.Container(
                bgcolor=C["surface2"] if selected else C["bg"],
                border_radius=8,
                border=ft.Border(bottom=ft.BorderSide(2, C["accent"] if selected else C["bg"])),
                padding=ft.Padding(left=8, top=3, right=2, bottom=3),
                content=ft.Row([
                    ft.Container(content=ft.Row([
                        ft.ProgressRing(width=11, height=11, stroke_width=2, visible=s["id"] in busy),
                        ft.Text(s["title"], size=11, color=C["text"] if selected else C["muted"],
                                width=105, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                    ], spacing=5), ink=True, on_click=lambda e, s=s: select_session(s)),
                    icon_btn(ft.Icons.CLOSE, "Close chat", lambda e, s=s: delete_session(s), size=13),
                ], spacing=2, tight=True),
            ))
        tabs_row.controls = tab_controls

    def refresh():
        render_sidebar()
        render_tabs()
        render_chat()
        update_connection_indicator()
        sidebar.visible = sidebar_visible[0]
        page.update()

    def select_session(s):
        if active[0] is s:
            return
        active[0]["draft"] = chat_input.value or ""
        active[0] = s
        chat_input.value = s.get("draft", "")
        notice.value = ""
        persist()
        refresh()

    def toggle_sidebar(_e=None):
        sidebar_visible[0] = not sidebar_visible[0]
        settings["sidebar_open"] = sidebar_visible[0]
        persist()
        refresh()

    def toggle_pin(s):
        s["pinned"] = not s["pinned"]
        persist()
        refresh()

    def add_session(_e=None):
        current = active[0]
        active[0]["draft"] = chat_input.value or ""
        s = new_session(system=current["system"], workflow=settings.get("workflow", ""),
                        temperature=settings["temperature"], max_tokens=settings["max_tokens"], host=settings["host"])
        sessions.append(s)
        active[0] = s
        chat_input.value = ""
        notice.value = ""
        persist()
        refresh()

    def prefill(text):
        chat_input.value = text
        active[0]["draft"] = text
        page.update()

    def on_input_change(_e=None):
        active[0]["draft"] = chat_input.value or ""

    chat_input.on_change = on_input_change

    def confirm(title, explanation, action, destructive=False):
        def accept(_e=None):
            page.pop_dialog()
            action()
        page.show_dialog(ft.AlertDialog(
            modal=True, bgcolor=C["surface"], title=ft.Text(title, size=16),
            content=ft.Text(explanation, size=13, color=C["sub"]),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                     ft.FilledButton("Delete" if destructive else "Continue", on_click=accept)],
        ))

    def delete_session(s):
        if s["id"] in busy:
            banner("Stop this chat before deleting it.", "warning")
            return
        def perform():
            if s not in sessions:
                return
            if len(sessions) == 1:
                replacement = new_session(workflow=settings["workflow"],
                                          temperature=settings["temperature"], max_tokens=settings["max_tokens"], host=settings["host"])
                sessions[0] = replacement
                active[0] = replacement
            else:
                idx = sessions.index(s)
                sessions.remove(s)
                if active[0] is s:
                    active[0] = sessions[min(idx, len(sessions) - 1)]
            chat_input.value = active[0].get("draft", "")
            persist()
            refresh()
        confirm("Delete conversation?", f"Delete '{s['title']}' and its locally saved messages? This can't be undone.",
                perform, destructive=True)

    def clear_chat(_e=None):
        s = active[0]
        if s["id"] in busy:
            banner("Stop the answer first.", "warning")
            return
        def perform():
            s["messages"] = []
            s["id"] = uuid.uuid4().hex[:12]  # New backend conversation, not merely a visual clear.
            s["draft"] = ""
            s["updated_at"] = time.time()
            chat_input.value = ""
            notice.value = ""
            persist()
            refresh()
        confirm("Clear current chat?", "This removes its saved messages and starts a fresh ComfyUI session.",
                perform, destructive=True)

    def rename(s):
        field = ft.TextField(value=s["title"], autofocus=True, text_size=13,
                             border_color=C["divider"], focused_border_color=C["accent"])
        def accept(_e=None):
            s["title"] = (field.value or "").strip()[:100] or s["title"]
            s["updated_at"] = time.time()
            page.pop_dialog()
            persist()
            refresh()
        field.on_submit = accept
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["surface"], title=ft.Text("Rename conversation"),
                                        content=field,
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                                                 ft.FilledButton("Save", on_click=accept)]))

    def export_chat(s, fmt):
        try:
            saved = export_conversation(s, fmt)
            banner(f"Exported: {saved}", "green")
        except Exception as exc:
            banner(f"Export failed: {exc}", "red")

    def chat_settings(_e=None):
        s = active[0]
        if s["id"] in busy:
            banner("Finish or stop this response before changing its settings.", "warning")
            return
        has_history = bool(s["messages"])
        available = list_workflows()
        wf = ft.Dropdown(label="Workflow (ComfyUI LLMSessionChatNode)",
                         value=s["workflow"] if s["workflow"] in available else None,
                         options=[ft.dropdown.Option(w) for w in available],
                         text_size=13, disabled=has_history)
        host_field = ft.TextField(label="ComfyUI host for this chat", value=s["host"], text_size=13, disabled=has_history)
        temp = ft.TextField(label="Temperature  ·  0.0 – 2.0", value=str(s["temperature"]), text_size=13)
        tokens = ft.TextField(label="Max response tokens", value=str(s["max_tokens"]), text_size=13,
                              keyboard_type=ft.KeyboardType.NUMBER)
        system = ft.TextField(label="System prompt / persona", value=s["system"], text_size=13,
                              multiline=True, min_lines=4, max_lines=8, disabled=has_history)
        error = ft.Text("", size=11, color=C["red"])
        controls = [ft.Text("Model settings for this chat only", size=12, color=C["sub"]), host_field, wf,
                    ft.Row([temp, tokens], spacing=10), system]
        if has_history:
            controls.append(ft.Text("Workflow and system prompt are locked after the first message to avoid "
                                    "desynchronizing ComfyUI's saved conversation. Start a new chat to change them.",
                                    size=11, color=C["warning"]))
        controls.append(error)

        def accept(_e=None):
            try:
                value_t = float(temp.value)
                value_n = int(tokens.value)
                if not 0 <= value_t <= 2 or not 16 <= value_n <= 65536:
                    raise ValueError()
            except (TypeError, ValueError):
                error.value = "Temperature must be 0–2; tokens must be 16–65536."
                page.update()
                return
            if not has_history and (not (host_field.value or "").strip() or any(ch.isspace() for ch in host_field.value)):
                error.value = "Enter a valid ComfyUI host."
                page.update()
                return
            if not has_history and wf.value not in list_workflows():
                error.value = "Choose a valid workflow first."
                page.update()
                return
            s["temperature"] = value_t
            s["max_tokens"] = value_n
            if not has_history:
                s["system"] = system.value or DEFAULT_SYSTEM
                s["workflow"] = wf.value
                s["host"] = host_field.value.strip()
            page.pop_dialog()
            persist()
            refresh()

        page.show_dialog(ft.AlertDialog(
            modal=True, bgcolor=C["surface"], title=ft.Text("Conversation settings", size=16),
            content=ft.Column(controls, tight=True, width=480, spacing=12, scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                     ft.FilledButton("Save", on_click=accept)],
        ))

    def app_settings(_e=None):
        host = ft.TextField(label="ComfyUI host", value=settings["host"], text_size=13,
                            border_color=C["divider"], focused_border_color=C["accent"])
        available = list_workflows()
        wf = ft.Dropdown(label="Default workflow for new chats", value=settings["workflow"] if settings["workflow"] in available else None,
                         options=[ft.dropdown.Option(x) for x in available], text_size=13)
        md = ft.Switch(label="Render assistant replies as Markdown", value=bool(settings["render_markdown"]))
        scroll = ft.Switch(label="Scroll to newest messages automatically", value=bool(settings["auto_scroll"]))
        err = ft.Text("", size=11, color=C["red"])

        def accept(_e=None):
            newhost = (host.value or "").strip()
            if not newhost or any(ch.isspace() for ch in newhost):
                err.value = "Enter a valid ComfyUI host."
                page.update()
                return
            if wf.value not in list_workflows() and list_workflows():
                err.value = "Choose a workflow from the available files."
                page.update()
                return
            settings.update({"host": newhost, "workflow": wf.value or "",
                             "render_markdown": bool(md.value), "auto_scroll": bool(scroll.value)})
            page.pop_dialog()
            connection[0] = ("unknown", None)
            persist()
            refresh()

        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["surface"], title=ft.Text("Application settings", size=16),
                                        content=ft.Column([host, wf, md, scroll,
                                                           ft.Text("Defaults apply to new chats. Existing chats retain their server and model. "
                                                                   "Temperature, tokens and persona are in Chat settings.",
                                                                   size=11, color=C["sub"]), err],
                                                          spacing=12, tight=True, width=460, scroll=ft.ScrollMode.AUTO),
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                                                 ft.FilledButton("Save", on_click=accept)]))

    async def check_connection(_e=None):
        host = active[0]["host"]
        connection[0] = ("unknown", host)
        banner("Checking ComfyUI connection…")
        try:
            connected = await asyncio.get_running_loop().run_in_executor(None, comfy_ping, host)
            connection[0] = ("online" if connected else "offline", host)
            banner(f"ComfyUI is {'connected' if connected else 'unavailable'} at {host}.",
                   "green" if connected else "red")
        except Exception as exc:
            connection[0] = ("offline", host)
            banner(f"Cannot connect to ComfyUI: {exc}", "red")
        refresh()

    async def send(_e=None):
        s = active[0]
        text = (chat_input.value or "").strip()
        if not text or s["id"] in busy:
            return
        wf_name = s.get("workflow") or ""
        if not wf_name or wf_name not in list_workflows():
            banner("No valid LLMSessionChatNode workflow was found. Check Chat settings.", "red")
            return
        # Read once: subsequent UI edits or tab switches won't mutate this request.
        sid = s["id"]
        host = s["host"]
        request_settings = {"temperature": s["temperature"], "max_tokens": s["max_tokens"]}
        request_session = {"id": sid, "system": s["system"]}
        chat_input.value = ""
        s["draft"] = ""
        s["messages"].append({"role": "user", "text": text, "ts": time.time()})
        if not any(m["role"] == "user" for m in s["messages"][:-1]) and s["title"] in ("New conversation", "Chat"):
            s["title"] = text.replace("\n", " ")[:40]
        s["updated_at"] = time.time()
        job = {"pid": None, "host": host, "cancel": False}
        busy[sid] = job
        notice.value = ""
        persist()
        refresh()
        try:
            await chat_input.focus()
        except Exception:
            pass

        reply, err = None, None
        loop = asyncio.get_running_loop()
        try:
            wf = build_workflow(wf_name, request_settings, request_session, text)
            pid = await loop.run_in_executor(None, comfy_queue, host, wf)
            job["pid"] = pid
            if job["cancel"]:
                await loop.run_in_executor(None, comfy_cancel, host, pid)
            else:
                deadline = time.monotonic() + TIMEOUT_S
                while not job["cancel"]:
                    if time.monotonic() > deadline:
                        await loop.run_in_executor(None, comfy_cancel, host, pid)
                        err = "Response timeout (5 minutes)."
                        break
                    await asyncio.sleep(1.5)
                    if job["cancel"]:
                        break
                    try:
                        hist = await loop.run_in_executor(None, comfy_hist, host, pid)
                    except Exception:
                        continue
                    entry = hist.get(pid)
                    if not entry:
                        continue
                    for nout in (entry.get("outputs") or {}).values():
                        texts = nout.get("text") or ([nout["string"]] if "string" in nout else [])
                        if texts and str(texts[0]).strip():
                            reply = str(texts[0]).strip()
                            break
                    status_str = (entry.get("status") or {}).get("status_str", "")
                    if reply is not None:
                        break
                    if status_str == "error":
                        errors = (entry.get("status") or {}).get("messages") or []
                        err = f"ComfyUI error: {errors[-1] if errors else 'unknown'}"
                        break
                    if status_str == "success":
                        err = "The workflow finished but returned no text."
                        break
        except Exception as exc:
            err = str(exc)
        finally:
            busy.pop(sid, None)

        # A stop does not show a late answer, even if history arrived at the same time.
        if job["cancel"]:
            if active[0] is s:
                notice.value = "Stopped. Start a new chat if you want to guarantee a clean backend context."
                notice.color = C["warning"]
        elif reply is not None:
            s["messages"].append({"role": "assistant", "text": reply, "ts": time.time()})
        elif err:
            s["messages"].append({"role": "error", "text": err, "ts": time.time()})
        s["updated_at"] = time.time()
        if s in sessions:
            persist()
        refresh()

    async def stop(_e=None):
        job = busy.get(active[0]["id"])
        if not job or job["cancel"]:
            return
        job["cancel"] = True
        refresh()
        if job["pid"]:
            await asyncio.get_running_loop().run_in_executor(None, comfy_cancel, job["host"], job["pid"])

    send_button.on_click = send
    stop_button.on_click = stop
    chat_input.on_submit = send
    chat_input.value = active[0].get("draft", "")

    sidebar.content = ft.Column([
        ft.Row([
            ft.Container(content=ft.Icon(ft.Icons.AUTO_AWESOME, size=18, color=C["accent"]),
                         bgcolor=C["accent_dim"], padding=9, border_radius=11),
            ft.Column([ft.Text("SIMPLE CHAT", weight=ft.FontWeight.BOLD, size=14, color=C["text"]),
                       ft.Text("STUDIO  /  LOCAL AI", size=10, color=C["muted"])], tight=True, spacing=1),
        ], spacing=10),
        ft.Container(height=7),
        ft.FilledButton("New conversation", icon=ft.Icons.ADD, on_click=add_session, width=225),
        query,
        sidebar_list,
        ft.Divider(color=C["divider"], height=10),
        ft.Row([connection_icon, connection_label,
                icon_btn(ft.Icons.REFRESH, "Check connection", check_connection, size=15)], spacing=7),
        ft.Row([icon_btn(ft.Icons.SETTINGS_OUTLINED, "Application settings", app_settings),
                ft.Text("Settings", size=12, color=C["sub"])], spacing=5),
    ], spacing=10, expand=True)

    topbar = ft.Container(
        bgcolor=C["bg"], padding=ft.Padding(left=18, top=13, right=18, bottom=12),
        border=ft.Border(bottom=ft.BorderSide(1, C["divider"])),
        content=ft.Row([
            icon_btn(ft.Icons.MENU_OPEN, "Show / hide sidebar", toggle_sidebar, C["accent"], 22),
            ft.Column([current_title, current_detail], expand=True, spacing=4, tight=True),
            icon_btn(ft.Icons.DRIVE_FILE_RENAME_OUTLINE, "Rename chat", lambda e: rename(active[0])),
            icon_btn(ft.Icons.TUNE, "Conversation settings", chat_settings),
            ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ, icon_color=C["sub"], items=[
                ft.PopupMenuItem(content="Pin / unpin", on_click=lambda e: toggle_pin(active[0])),
                ft.PopupMenuItem(content="Export Markdown", on_click=lambda e: export_chat(active[0], "md")),
                ft.PopupMenuItem(content="Export JSON", on_click=lambda e: export_chat(active[0], "json")),
                ft.PopupMenuItem(content="Clear chat…", on_click=clear_chat),
                ft.PopupMenuItem(content="Delete chat…", on_click=lambda e: delete_session(active[0])),
            ]),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
    )

    main_column = ft.Column([
        topbar,
        ft.Container(content=ft.Row([tabs_row, icon_btn(ft.Icons.ADD, "New chat tab", add_session, C["accent"])],
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER),
                     padding=ft.Padding(left=15, top=6, right=12, bottom=5),
                     border=ft.Border(bottom=ft.BorderSide(1, C["divider"]))),
        chat_list,
        ft.Container(content=ft.Column([
            ft.Row([chat_input, send_button, stop_button], spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.END),
            ft.Row([ft.Text("Local model · Chats saved on this device", size=10, color=C["muted"]),
                    ft.Container(expand=True),
                    ft.Text("Enter ↵ send  ·  Shift+Enter ↵ newline", size=10, color=C["muted"])],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            notice,
        ], tight=True, spacing=8), bgcolor=C["bg"],
           padding=ft.Padding(left=25, top=12, right=25, bottom=16),
           border=ft.Border(top=ft.BorderSide(1, C["divider"]))),
    ], expand=True, spacing=0)

    page.add(ft.Row([sidebar, ft.Container(main_column, expand=True)], expand=True, spacing=0))
    refresh()


if __name__ == "__main__":
    ft.app(target=_app_error.guard(ft, main, APP_NAME, APP_VERSION))
