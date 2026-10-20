"""update_check.py - tell the user that a newer WorkflowPad release exists on GitHub. A NOTICE only: nothing is downloaded or installed.

One module for all five programs, one shared setting: the switch, the "skip this version" choice and the last answer live in one file
(%APPDATA%\\WorkflowPad\\update_check.json), so turning the check off in one program turns it off in all of them.

What it does (and nothing else):
  - at most once a day, one plain GET to the GitHub releases list of this project (no account, no token, nothing about the user is sent;
    GitHub sees the IP address like for any web request);
  - the newest release tag is compared with the program's own APP_VERSION as numbers (app_version.is_newer_version);
  - a newer one shows a small notice with a button that opens the release page in the browser.
A network problem, a timeout or an unreadable answer is silent: the program starts and works exactly as without this module.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
import webbrowser
from pathlib import Path

from app_version import is_newer_version, parse_version

RELEASES_API = "https://api.github.com/repos/sudja-tongtip/WorkflowPad/releases?per_page=20"
RELEASES_PAGE = "https://github.com/sudja-tongtip/WorkflowPad/releases"
CHECK_EVERY_SECONDS = 24 * 3600
TIMEOUT_SECONDS = 5
CONFIG_PATH = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "WorkflowPad" / "update_check.json"


# ── the shared setting file ─────────────────────────────────────────────────────────────────────────────
def load() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(data: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def is_enabled() -> bool:
    return bool(load().get("enabled", True))


def set_enabled(on: bool) -> None:
    data = load()
    data["enabled"] = bool(on)
    save(data)


def skip_version(version: str) -> None:
    data = load()
    data["skipped"] = str(version or "")
    save(data)


# ── the check ──────────────────────────────────────────────────────────────────────────────────────────
def _newest_release(releases) -> dict | None:
    """The release with the highest MAJOR.MINOR.PATCH tag. Tags that are not a version are ignored; drafts never come back from the public API."""
    best, best_key = None, None
    for rel in releases if isinstance(releases, list) else []:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        try:
            key = parse_version(str(rel.get("tag_name") or ""))
        except ValueError:
            continue
        if best_key is None or key > best_key:
            best, best_key = rel, key
    if best is None:
        return None
    return {"version": ".".join(str(x) for x in best_key), "url": str(best.get("html_url") or RELEASES_PAGE), "name": str(best.get("name") or "")}


def fetch_latest() -> dict | None:
    """One request to GitHub. None on any problem (offline, timeout, rate limit, unreadable answer)."""
    try:
        req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json", "User-Agent": "WorkflowPad-update-check"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return _newest_release(json.loads(resp.read().decode("utf-8")))
    except Exception:
        return None


def available(current_version: str, force: bool = False) -> dict | None:
    """{"version", "url", "current"} when a release newer than `current_version` exists, else None. Blocking (network): call it off the UI thread.
    Without `force` it respects the on/off switch, asks GitHub at most once a day (the last answer is kept in the setting file) and honours "skip this version"."""
    try:
        data = load()
        if not force and not data.get("enabled", True):
            return None
        latest = data.get("latest") if isinstance(data.get("latest"), dict) else None
        stale = force or not latest or (time.time() - float(data.get("last_check") or 0)) >= CHECK_EVERY_SECONDS
        if stale:
            fresh = fetch_latest()
            data["last_check"] = time.time()          # a failed try also waits a day: no hammering when GitHub is unreachable
            if fresh:
                data["latest"] = latest = fresh
            save(data)
        if not latest or not is_newer_version(current_version, str(latest.get("version") or "")):
            return None
        if not force and str(data.get("skipped") or "") == latest["version"]:
            return None
        return {"version": latest["version"], "url": latest.get("url") or RELEASES_PAGE, "current": current_version}
    except Exception:
        return None


# ── Flet pieces shared by the programs (the `ft` module is passed in: this file imports nothing from the UI) ─────────────
def start_notice(ft, page, app_name: str, current_version: str) -> None:
    """Call once after the window exists. Checks in the background and, if there is a newer release, shows a notice at the bottom of the window."""
    async def _run():
        try:
            info = await asyncio.get_running_loop().run_in_executor(None, available, current_version)
            if info:
                show_notice(ft, page, app_name, info)
        except Exception:
            pass
    try:
        page.run_task(_run)
    except Exception:
        pass


def show_notice(ft, page, app_name: str, info: dict) -> None:
    """A small bar at the bottom of the window (a Container in the page overlay). It is NOT a SnackBar: in Flet 0.85 a SnackBar with buttons inside, or with a hex close_icon_color, makes the whole window
    render blank/grey (seen on v0.1.7 the day v0.1.8 was released)."""
    def _close(e=None):
        try:
            if bar in page.overlay:
                page.overlay.remove(bar)
            page.update()
        except Exception:
            pass

    def _download(e):
        try:
            webbrowser.open(info["url"])
        except Exception:
            pass
        _close()

    def _skip(e):
        skip_version(info["version"])
        _close()

    def _turn_off(e):
        set_enabled(False)
        _close()

    btn = lambda text, fn: ft.TextButton(text, on_click=fn, style=ft.ButtonStyle(color="#93C5FD"))
    bar = ft.Container(
        content=ft.Column([
            ft.Text(f"A newer version is available: {info['version']} (you have {info['current']}).", color="#F9FAFB", size=12),
            ft.Row([btn("Open download page", _download), btn("Skip this version", _skip), btn("Turn off update checks", _turn_off)], spacing=4, wrap=True),
        ], spacing=2, tight=True),
        bgcolor="#1F2937", padding=12, border_radius=8, left=16, right=16, bottom=16)
    page.overlay.append(bar)
    page.update()


def settings_row(ft, page, app_name: str, current_version: str):
    """A row for a Settings dialog: the shared on/off switch and a 'Check now' button."""
    result = ft.Text("", size=11, color="#6B7280")

    sw = ft.Switch(label="Tell me when a newer version is available (all WorkflowPad programs)", value=is_enabled(), scale=0.9)

    def _changed(e):
        set_enabled(bool(sw.value))

    sw.on_change = _changed

    async def _check_now_async():
        result.value = "Checking..."
        page.update()
        info = await asyncio.get_running_loop().run_in_executor(None, available, current_version, True)
        if info:
            result.value = f"Newer version {info['version']} is available."
            show_notice(ft, page, app_name, info)
        else:
            result.value = "You have the newest version (or GitHub could not be reached)."
        page.update()

    def _check_now(e):
        page.run_task(_check_now_async)

    return ft.Column([
        sw,
        ft.Row([ft.TextButton("Check now", on_click=_check_now), result], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
    ], spacing=2, tight=True)
