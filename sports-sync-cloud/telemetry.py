"""
Anonymous usage telemetry for kids-sports-sync.

What's sent: script name, team/kid counts, which platforms are used,
which optional features are active, and per-run metrics (events copied,
RSVPs set, etc.). No names, emails, or event details are ever included.

Disable with:  "telemetry": false  in config.json
"""

import datetime
import json
import uuid
from pathlib import Path

# URL of your telemetry receiver (Cloud Function, Apps Script web app, etc.)
# Leave empty to disable — all pings are silently skipped.
TELEMETRY_URL = "https://script.google.com/macros/s/AKfycbyD_b-HXdqq2JsoaR2_7gtDHqSZwIPUMQ1-FD7InmHwnlS0hcyOblWahYLKRPtxBpgVUg/exec"


def _instance_id(config_dir: Path) -> str:
    """Return a stable anonymous ID for this installation, creating it on first run."""
    id_file = config_dir / "telemetry-id.json"
    if id_file.exists():
        try:
            return json.loads(id_file.read_text())["id"]
        except Exception:
            pass
    new_id = str(uuid.uuid4())
    try:
        id_file.write_text(json.dumps({"id": new_id}))
    except Exception:
        pass
    return new_id


def _personalizations(cfg: dict, config_dir: Path) -> int:
    """
    Count optional features enabled beyond defaults.
    Each one represents a deliberate config choice by the user.
    """
    score = 0
    teams  = cfg.get("teams", [])
    family = cfg.get("family", {})

    if family.get("respect_manual_yes"):
        score += 1
    if any(t.get("driving") for t in teams):
        score += 1
    if (config_dir / "bays-fields.json").exists():
        score += 1
    for t in teams:
        if t.get("game_duration_minutes") or t.get("practice_duration_minutes"):
            score += 1
        if not t.get("adjust_duration", True):
            score += 1

    return score


def ping(config_dir: Path, cfg: dict, script: str, metrics: dict) -> None:
    """
    Fire-and-forget telemetry ping. Never raises, never blocks for long.

    metrics: script-specific counts, e.g. {"events_copied": 3, "rsvps_set": 5}
    """
    if not TELEMETRY_URL:
        return
    if not cfg.get("telemetry", True):
        return

    try:
        import urllib.request

        teams = cfg.get("teams", [])
        payload = {
            "instance_id":      _instance_id(config_dir),
            "ts":               datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "script":           script,
            "v":                "1",
            "teams":            len(teams),
            "kids":             len(cfg.get("kids", [])),
            "platforms":        sorted({t.get("rsvp_platform", "") for t in teams
                                        if t.get("rsvp_platform")}),
            "personalizations": _personalizations(cfg, config_dir),
            "email_enabled":    cfg.get("email_enabled", True),
        }
        payload.update(metrics)

        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()

        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            TELEMETRY_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5, context=ctx)
    except Exception:
        pass  # telemetry must never crash the main script
