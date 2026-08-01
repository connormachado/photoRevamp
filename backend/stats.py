"""
Delete counter + reclaimed-bytes persistence
============================================
Tracks how many photos the user has culled and how much space that (plus the
Climb Cutter video trims) is projected to reclaim. Backed by a tiny JSON file
(`stats.json`) at the repo root so both survive app restarts.

Logic only — routes live in server.py.
"""

import json
import os
import tempfile
from pathlib import Path

# stats.json lives at the repo root (one level up from backend/).
STATS_PATH = Path(__file__).resolve().parent.parent / "stats.json"

# Average bytes per photo, used to estimate reclaimed space whenever the exact
# size isn't available — bulk-pad entries and the ± buttons, which log a count
# with no particular photo attached. 3.5 MiB is a rough iPhone HEIC/JPEG mix;
# retune this single number if the estimate drifts.
AVG_PHOTO_BYTES = 3_670_016  # 3.5 MB

# The three independent sources that make up the reclaimed total. Kept split so
# the headline stays auditable and so no source can clobber another's bytes.
#   photos_exact     — a real size read out of Photos at reveal time
#   photos_estimated — count-only deletions valued at AVG_PHOTO_BYTES each
#   climb_cutter     — mirrored from photo_db/motion_review/savings.json
BREAKDOWN_KEYS = ("photos_exact", "photos_estimated", "climb_cutter")

# `deleted` = photos culled; `reclaimed_bytes` = the DERIVED sum of the
# breakdown below — never assign it directly, call _recompute_total().
DEFAULTS = {
    "deleted": 0,
    "reclaimed_bytes": 0,
    "reclaimed_breakdown": {k: 0 for k in BREAKDOWN_KEYS},
}


def get_stats() -> dict:
    """Read the persisted stats, falling back to defaults if the file is
    missing or unreadable."""
    try:
        with open(STATS_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    # Merge over defaults so missing keys still come back populated. The
    # breakdown is rebuilt rather than merged, so callers never get a handle on
    # DEFAULTS' own nested dict and mutate the module-level default in place.
    stats = {**DEFAULTS, **data}
    stats["reclaimed_breakdown"] = _normalise_breakdown(data)
    _recompute_total(stats)
    return stats


def _normalise_breakdown(data: dict) -> dict:
    """Coerce whatever is on disk into a full {source: int} breakdown.

    Migration: files written before the breakdown existed carry only a scalar
    `reclaimed_bytes`, and Climb Cutter was its sole writer — so that whole
    total belongs to `climb_cutter`. Seeding it there (rather than dropping it)
    keeps the headline unchanged across the upgrade and, because savings.json
    stays the ledger of record, can't double-count on the next verdict.
    """
    raw = data.get("reclaimed_breakdown")
    if not isinstance(raw, dict):
        return {**{k: 0 for k in BREAKDOWN_KEYS},
                "climb_cutter": max(0, int(data.get("reclaimed_bytes", 0) or 0))}
    # Present but possibly partial — fill in any missing source with 0.
    return {k: max(0, int(raw.get(k, 0) or 0)) for k in BREAKDOWN_KEYS}


def _recompute_total(stats: dict) -> None:
    """Set `reclaimed_bytes` to the sum of its parts. Every writer calls this
    before persisting, so the headline can never drift from the breakdown."""
    stats["reclaimed_bytes"] = sum(stats["reclaimed_breakdown"].values())


def _write_stats(stats: dict) -> None:
    """Atomic write: dump to a temp file in the same dir, then rename over the
    target so a crash mid-write can't leave a half-written stats.json."""
    fd, tmp = tempfile.mkstemp(dir=STATS_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(stats, f)
        os.replace(tmp, STATS_PATH)
    except Exception:
        os.path.exists(tmp) and os.remove(tmp)
        raise


def update_stats(delta: int, exact_bytes: int = 0) -> dict:
    """Add `delta` to the deleted count (floored at 0), credit the matching
    bytes, and write it all back atomically. Returns the updated stats.

    `exact_bytes` is the real size of the photo being culled, when we managed to
    read one out of Photos; without it the deletion is valued at the
    AVG_PHOTO_BYTES estimate instead.
    """
    stats = get_stats()
    delta = int(delta)
    stats["deleted"] = max(0, stats["deleted"] + delta)

    breakdown = stats["reclaimed_breakdown"]
    exact_bytes = max(0, int(exact_bytes or 0))
    if delta > 0 and exact_bytes:
        breakdown["photos_exact"] += exact_bytes
    elif delta > 0:
        breakdown["photos_estimated"] += delta * AVG_PHOTO_BYTES
    elif delta < 0:
        # Undo. There's no per-photo ledger to look a real size up in, so back
        # out the average: from the estimated pool first, spilling into the
        # exact pool only once estimated is drained. Both floored at 0.
        owed = -delta * AVG_PHOTO_BYTES
        from_estimated = min(owed, breakdown["photos_estimated"])
        breakdown["photos_estimated"] -= from_estimated
        breakdown["photos_exact"] = max(0, breakdown["photos_exact"] - (owed - from_estimated))

    _recompute_total(stats)
    _write_stats(stats)
    return stats


def set_climb_cutter_bytes(total: int) -> dict:
    """Store the absolute total of reclaimed video bytes. Returns updated stats.

    Absolute (not a delta) because the motion-review ledger recomputes the whole
    video total each time a verdict changes. It lands in its own breakdown slot,
    so recomputing it can't disturb the photo-side bytes.
    """
    stats = get_stats()
    stats["reclaimed_breakdown"]["climb_cutter"] = max(0, int(total))
    _recompute_total(stats)
    _write_stats(stats)
    return stats


# Old name from before the total was split by source.
set_reclaimed_bytes = set_climb_cutter_bytes
