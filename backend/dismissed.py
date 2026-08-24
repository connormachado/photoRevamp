"""
Per-category dismissal ledger
==============================
Backs the junk-cull chips' "hide this one, not here" control. A dismissal is
purely a display filter: it is never a delete, never touches the photo file,
and is scoped to one category — hiding a photo from "blurry" leaves it visible
under "dark".

On disk (`photo_db/dismissed.json`): {"category": ["<file_id>", ...]}. JSON has
no set type, so the on-disk shape is a list; in memory it's {category: set()}.

Logic only — routes live in server.py.
"""

import json
import os
import re
import tempfile
from pathlib import Path

import safe_paths
from utils import DEFAULT_DB_PATH

DISMISSED_PATH = DEFAULT_DB_PATH / "dismissed.json"

# A category key IS a chip id — chips.py imports this exact object rather than
# re-declaring it, so the two can never drift into copies that disagree.
#
# `\Z`, not `$`: Python's `$` also matches just before a trailing newline, so
# `^[a-z0-9_-]{1,40}$` accepted "dark\n" — a key that renders identically to
# "dark" in the tick row but is a DIFFERENT ledger key, silently splitting a
# chip's dismissals in two. That is the exact failure this validation exists to
# prevent, so the anchor has to be the strict one.
_CATEGORY_RE = re.compile(r"^[a-z0-9_-]{1,40}\Z")

# Populated on first access, updated in place on every mutation — the one
# module in this app that diverges from stats.py's read-every-call style,
# because this is read on every chip search rather than only on a UI mount.
_CACHE: dict[str, set[str]] | None = None


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON to path atomically (temp file + os.replace). Mirrors
    motion_review._atomic_write_json."""
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


def _load() -> dict[str, set[str]]:
    try:
        with open(DISMISSED_PATH) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        category: set(ids)
        for category, ids in raw.items()
        if isinstance(ids, list)
    }


def _cache() -> dict[str, set[str]]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _load()
    return _CACHE


def reload() -> None:
    """Drop the in-memory cache so the next access re-reads disk.

    Lets tests point DISMISSED_PATH at tmp and reset between runs, since the
    normal cache would otherwise carry state across them.
    """
    global _CACHE
    _CACHE = None


def _validate_category(category: str) -> str:
    if not isinstance(category, str) or not _CATEGORY_RE.match(category):
        raise ValueError(f"invalid category: {category!r}")
    return category


def _persist() -> None:
    _atomic_write_json(
        DISMISSED_PATH,
        {category: sorted(ids) for category, ids in _cache().items() if ids},
    )


def get_dismissed(category: str | None = None) -> dict[str, list[str]] | list[str]:
    """The whole map ({category: [ids]}), or one category's list of ids."""
    cache = _cache()
    if category is None:
        return {category: sorted(ids) for category, ids in cache.items()}
    _validate_category(category)
    return sorted(cache.get(category, ()))


def dismiss(category: str, photo_id: str) -> int:
    """Add photo_id to category's hide list. Returns the category's new count."""
    _validate_category(category)
    photo_id = safe_paths.safe_id_component(photo_id)
    cache = _cache()
    cache.setdefault(category, set()).add(photo_id)
    _persist()
    return len(cache[category])


def restore(category: str, photo_id: str) -> int:
    """Remove photo_id from category's hide list. Returns the category's new count."""
    _validate_category(category)
    photo_id = safe_paths.safe_id_component(photo_id)
    cache = _cache()
    cache.get(category, set()).discard(photo_id)
    _persist()
    return len(cache.get(category, ()))
