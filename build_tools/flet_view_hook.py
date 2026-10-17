"""PyInstaller runtime hook (runs first inside every frozen EXE).

Flet normally downloads its desktop client from GitHub into ~/.flet on first run. A portable build must not do
that, so the client is bundled (see WorkflowPad.spec) and FLET_VIEW_PATH is pointed at it. A value the user already
set is respected.
"""
import os
import sys

_client = os.path.join(getattr(sys, "_MEIPASS", ""), "flet_client")
if os.path.isfile(os.path.join(_client, "flet.exe")):
    os.environ.setdefault("FLET_VIEW_PATH", _client)
