"""
Shared config loader for kids-sports-sync scripts.
Reads config.json from SCRIPT_CONFIG_DIR if set, otherwise from the script directory.
"""

import json
import os
from pathlib import Path

_CONFIG = None


def _config_path() -> Path:
    env = os.environ.get("SCRIPT_CONFIG_DIR")
    if env:
        return Path(env) / "config.json"
    return Path(__file__).parent / "config.json"


def load() -> dict:
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG
    p = _config_path()
    if not p.exists():
        raise FileNotFoundError(
            f"\nconfig.json not found.\n"
            f"Copy config.example.json → config.json and fill in your values.\n"
            f"Expected at: {p}\n"
        )
    _CONFIG = json.loads(p.read_text())
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
