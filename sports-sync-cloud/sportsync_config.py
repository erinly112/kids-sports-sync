"""
Shared config loader for kids-sports-sync scripts.
Reads config.json from the same directory as this file.
"""

import json
from pathlib import Path

_CONFIG = None
_CONFIG_PATH = Path(__file__).parent / "config.json"


def load() -> dict:
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"\nconfig.json not found.\n"
            f"Copy config.example.json → config.json and fill in your values.\n"
            f"Expected at: {_CONFIG_PATH}\n"
        )
    _CONFIG = json.loads(_CONFIG_PATH.read_text())
    return _CONFIG


def teams(platform: str = None) -> list:
    """Return all teams, optionally filtered by rsvp_platform."""
    cfg = load()
    t = cfg.get("teams", [])
    if platform:
        t = [x for x in t if x.get("rsvp_platform") == platform]
    return t


def kid_names() -> set:
    return {k["name"] for k in load().get("kids", [])}


def kid_colors() -> dict:
    return {k["name"]: k["color"] for k in load().get("kids", [])}
