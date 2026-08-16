"""
Apple Photos date resolution
=============================
The single place that reads a photo's real capture date out of Apple's
Photos.sqlite and converts it to this app's canonical date_taken type (a plain
Unix timestamp, seconds, UTC). Two callers join against it: `embed_photos.py`
at index time (so a freshly-indexed photo gets a real date immediately, not a
placeholder), and `backfill_dates.py` (the rerunnable migration that dates
everything indexed before this existed). Neither may derive a date any other
way — one join, one epoch conversion, one type, so the two writers can never
drift into writing different date_taken shapes for the same field.

Logic only, no side effects beyond a read-only sqlite connection.
"""

import sqlite3
from pathlib import Path

import config_store

# Core Data stores timestamps as seconds since 2001-01-01, not the Unix epoch
# (1970-01-01). Omitting this offset silently shifts every date back 31 years.
CORE_DATA_EPOCH_OFFSET = 978307200


def photos_sqlite_path() -> Path:
    """Photos.sqlite, resolved through the shared config store so a
    user-configured library root (Settings > Photos Library) is honoured."""
    return config_store.get_library_root() / "database" / "Photos.sqlite"


def core_data_to_unix(seconds: float) -> int:
    """Convert a Core Data ZDATECREATED value to a Unix timestamp (int, UTC)."""
    return int(round(seconds + CORE_DATA_EPOCH_OFFSET))


def load_uuid_dates(sqlite_path: Path) -> dict:
    """Read {ZUUID: unix_timestamp_or_None} from Photos.sqlite, read-only.

    Opened immutable so this never takes a write lock on Apple's database,
    even if Photos.app happens to have it open.
    """
    # as_uri() percent-encodes the path — a bare f-string would let a library
    # root containing '#', '?', or '%' (all legal on macOS) truncate or
    # mis-resolve against the trailing ?immutable=1 query string.
    uri = f"{sqlite_path.as_uri()}?immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.cursor()
        cur.execute("SELECT ZUUID, ZDATECREATED FROM ZASSET WHERE ZUUID IS NOT NULL")
        return {
            uuid: (core_data_to_unix(seconds) if seconds is not None else None)
            for uuid, seconds in cur.fetchall()
        }
    finally:
        con.close()


def resolve_date(apple_uuid: str, uuid_dates: dict):
    """Look up apple_uuid in a {uuid: unix|None} map, returning None for "no
    date available" — a falsy uuid never reaches the lookup, so it can never
    collide with a stray empty-string ZUUID in Photos.sqlite.

    Collapses the two miss reasons backfill_dates.py's report keeps separate
    (no matching asset vs. a matched asset with no date) into one None, which
    is all a fresh index needs: either a photo gets a real date now, or it
    starts undated and a later backfill run resolves it.
    """
    if not apple_uuid:
        return None
    return uuid_dates.get(apple_uuid)
