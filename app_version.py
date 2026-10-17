"""Structural version utilities shared by the WorkflowPad apps (SemVer: MAJOR.MINOR.PATCH).

Each app owns its own APP_ID / APP_NAME / APP_VERSION. A later update checker will compare an app's
APP_VERSION with the matching entry in update.json using is_newer_version().
"""


def parse_version(version: str) -> tuple[int, int, int]:
    """'0.1.12' -> (0, 1, 12). Compared numerically, never as text ('0.10.0' > '0.9.0')."""
    text = str(version).strip().lstrip("vV")
    parts = text.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a MAJOR.MINOR.PATCH version: {version!r}")
    major, minor, patch = (int(p) for p in parts)
    return major, minor, patch


def is_newer_version(current: str, remote: str) -> bool:
    """True only when `remote` is strictly newer than `current`."""
    return parse_version(remote) > parse_version(current)
