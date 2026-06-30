"""
Delete counter persistence
===========================
Tracks how many photos the user has culled. Backed by a tiny JSON file
(`stats.json`) at the repo root so the count survives app restarts.

Logic only — routes live in server.py.
"""

import json
import os
import tempfile
from pathlib import Path

# stats.json lives at the repo root (one level up from backend/).
STATS_PATH = Path(__file__).resolve().parent.parent / "stats.json"

DEFAULTS = {"deleted": 0}


def get_stats() -> dict:
    """Read the persisted stats, falling back to defaults if the file is
    missing or unreadable."""
    try:
        with open(STATS_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULTS)
    # Merge over defaults so missing keys still come back populated.
    return {**DEFAULTS, **data}


def update_stats(delta: int) -> dict:
    """Add `delta` to the deleted count (floored at 0) and write it back
    atomically. Returns the updated stats."""
    stats = get_stats()
    stats["deleted"] = max(0, stats["deleted"] + int(delta))

    # Atomic write: dump to a temp file in the same dir, then rename over the
    # target so a crash mid-write can't leave a half-written stats.json.
    fd, tmp = tempfile.mkstemp(dir=STATS_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(stats, f)
        os.replace(tmp, STATS_PATH)
    except Exception:
        os.path.exists(tmp) and os.remove(tmp)
        raise

    return stats
