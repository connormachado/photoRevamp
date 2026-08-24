"""
Per-chip run counters
=====================
A deliberate SIBLING of chips.json, not a field inside a chip record. Two
reasons, both about blast radius: editing a chip must never rewrite (or risk
losing) its accumulated stats, and a stats bump on every chip search must never
rewrite chip definitions. Keeping them in one file would make every search a
read-modify-write of the definitions themselves.

On disk (`photo_db/chip_stats.json`):
    {"schema_version": 1,
     "chips": {"<chip_id>": {"run_count": int, "last_run_at": int,
                             "last_result_count": int}}}

Written only by chip_resolve.resolve(). Reads never write, same rule as
chips.py / config_store.py. A chip with no recorded run reads back as zeros
rather than raising, so a caller never has to special-case a never-run chip.

Logic only — routes live in server.py.
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from utils import DEFAULT_DB_PATH

CHIP_STATS_PATH = DEFAULT_DB_PATH / "chip_stats.json"
SCHEMA_VERSION = 1

EMPTY_STATS = {"run_count": 0, "last_run_at": 0, "last_result_count": 0}

_LOCK = threading.RLock()


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON to path atomically (temp file + os.replace). Mirrors
    dismissed._atomic_write_json / chips._atomic_write_json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _normalise_entry(raw) -> dict:
    """Coerce whatever is on disk into a full {run_count, last_run_at,
    last_result_count} of ints. A hand-edited string or null reads as 0."""
    entry = dict(EMPTY_STATS)
    if isinstance(raw, dict):
        for key in EMPTY_STATS:
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                entry[key] = value
    return entry


def load() -> dict:
    """The whole store. Never writes, never raises."""
    with _LOCK:
        raw = None
        if CHIP_STATS_PATH.exists():
            try:
                with open(CHIP_STATS_PATH) as f:
                    raw = json.load(f)
            except (OSError, ValueError):
                raw = None
        if not isinstance(raw, dict):
            return {"schema_version": SCHEMA_VERSION, "chips": {}}
        chips = raw.get("chips")
        if not isinstance(chips, dict):
            chips = {}
        return {
            "schema_version": SCHEMA_VERSION,
            "chips": {
                chip_id: _normalise_entry(entry)
                for chip_id, entry in chips.items()
                if isinstance(chip_id, str)
            },
        }


def get(chip_id: str) -> dict:
    """One chip's counters — zeros for a chip that has never run."""
    return load()["chips"].get(chip_id, dict(EMPTY_STATS))


def record_run(chip_id: str, result_count: int, now: int | None = None) -> dict:
    """Bump `chip_id`'s counters after a resolve. Returns the new entry.

    `now` is injectable so a test can assert the recorded timestamp without
    freezing the clock globally.
    """
    with _LOCK:
        data = load()
        entry = data["chips"].get(chip_id, dict(EMPTY_STATS))
        entry = {
            "run_count": entry["run_count"] + 1,
            "last_run_at": int(time.time()) if now is None else int(now),
            "last_result_count": max(0, int(result_count)),
        }
        data["chips"][chip_id] = entry
        _atomic_write_json(CHIP_STATS_PATH, data)
        return entry
