"""
Backfill date_taken into existing ChromaDB metadata by joining Apple's Photos.sqlite.

The app indexes derivative JPEGs from the Photos library cache, and those
derivatives have their EXIF stripped — so date_taken is empty on almost every
row. Photos.sqlite has the ground truth (ZASSET.ZDATECREATED), keyed by the
same UUID already stored as apple_uuid on every Chroma row.

Safe to re-run: a row whose stored date_taken already matches what this script
would compute is left untouched (reported as "skipped"), which is also what
lets a rerun pick up rows a later indexing pass adds — their date_taken starts
"" and will never equal the computed value until this script writes it.

Defaults to a dry run (no writes). Pass --write to actually update ChromaDB.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone

import chromadb
import photo_dates as pd
from utils import COLLECTION_NAME, DEFAULT_DB_PATH

CHUNK_SIZE = 5000

# The Core Data join, the epoch conversion, and the Photos.sqlite path are all
# defined once in photo_dates.py (embed_photos.py's indexer joins against the
# same functions at index time) — re-exported here under their original
# private names so this module's own logic and its existing tests don't need
# to know the join moved.
CORE_DATA_EPOCH_OFFSET = pd.CORE_DATA_EPOCH_OFFSET
_core_data_to_unix = pd.core_data_to_unix
_load_uuid_dates = pd.load_uuid_dates
_photos_sqlite_path = pd.photos_sqlite_path


def backfill(db_path: str = str(DEFAULT_DB_PATH), write: bool = False) -> dict:
    """Join every Chroma row's apple_uuid against Photos.sqlite and (if write
    is True) update date_taken in place. Always returns a report dict; never
    writes when write=False.
    """
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_collection(COLLECTION_NAME)

    uuid_dates = _load_uuid_dates(_photos_sqlite_path())

    total = 0
    written = 0
    skipped = 0
    misses = Counter()
    year_counts = Counter()
    min_date = None
    max_date = None

    offset = 0
    while True:
        batch = collection.get(include=["metadatas"], limit=CHUNK_SIZE, offset=offset)
        if not batch["ids"]:
            break

        ids_to_update = []
        updated_metadatas = []

        for id_, metadata in zip(batch["ids"], batch["metadatas"]):
            total += 1
            metadata = metadata or {}
            uuid = metadata.get("apple_uuid", "")

            # A falsy uuid must never reach the dict lookup: if any Photos
            # asset ever carries an empty-string ZUUID, "" would be a valid
            # key and every un-UUID'd row would silently inherit its date.
            if not uuid or uuid not in uuid_dates:
                misses["no_match"] += 1
                continue

            computed = uuid_dates[uuid]
            if computed is None:
                misses["null_date"] += 1
                continue

            year_counts[datetime.fromtimestamp(computed, tz=timezone.utc).year] += 1
            if min_date is None or computed < min_date:
                min_date = computed
            if max_date is None or computed > max_date:
                max_date = computed

            if metadata.get("date_taken") == computed:
                skipped += 1
                continue

            ids_to_update.append(id_)
            updated_metadatas.append({**metadata, "date_taken": computed})

        if ids_to_update:
            written += len(ids_to_update)
            if write:
                collection.update(ids=ids_to_update, metadatas=updated_metadatas)

        offset += CHUNK_SIZE

    return {
        "total": total,
        "written": written,
        "skipped": skipped,
        "misses": dict(misses),
        "min_date": min_date,
        "max_date": max_date,
        "year_counts": dict(sorted(year_counts.items())),
        "dry_run": not write,
    }


def _print_report(report: dict) -> None:
    mode = "WRITE" if not report["dry_run"] else "DRY RUN — no changes made"
    print(f"\n{'='*60}\nDate backfill report ({mode})\n{'='*60}")
    print(f"Total Chroma rows:  {report['total']:,}")
    print(f"Written:            {report['written']:,}")
    print(f"Already correct:    {report['skipped']:,}")
    total_misses = sum(report["misses"].values())
    print(f"Misses:             {total_misses:,}")
    for reason, count in report["misses"].items():
        print(f"  - {reason}: {count:,}")

    if report["min_date"] is not None:
        min_iso = datetime.fromtimestamp(report["min_date"], tz=timezone.utc).isoformat()
        max_iso = datetime.fromtimestamp(report["max_date"], tz=timezone.utc).isoformat()
        print(f"\nDate range: {min_iso} -> {max_iso}")
        print("Per-year histogram:")
        for year, count in report["year_counts"].items():
            print(f"  {year}: {count:,}")

    if total_misses:
        print(f"\n⚠️  {total_misses:,} rows have no usable date — see breakdown above.")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill ChromaDB date_taken from Apple Photos' Photos.sqlite."
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(DEFAULT_DB_PATH),
        help="Path to the ChromaDB directory (default: photo_db/ next to repo root)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually update ChromaDB. Without this flag, only a report is printed.",
    )
    args = parser.parse_args()

    report = backfill(db_path=args.db, write=args.write)
    _print_report(report)


if __name__ == "__main__":
    main()
