"""
Backfill apple_uuid into existing ChromaDB metadata entries.
Run once after adding apple_uuid to extract_metadata().
Safe to re-run — skips entries that already have a UUID.
"""

import chromadb
from pathlib import Path
from utils import COLLECTION_NAME, DEFAULT_DB_PATH

CHUNK_SIZE = 5000

def backfill():
    client = chromadb.PersistentClient(path=str(DEFAULT_DB_PATH))
    collection = client.get_collection(COLLECTION_NAME)

    offset = 0
    total_updated = 0
    total_skipped = 0

    while True:
        batch = collection.get(include=["metadatas"], limit=CHUNK_SIZE, offset=offset)
        if not batch["ids"]:
            break

        ids_to_update = []
        updated_metadatas = []

        for id_, metadata in zip(batch["ids"], batch["metadatas"]):
            if metadata.get("apple_uuid"):
                total_skipped += 1
                continue

            path = metadata.get("path", "")
            stem = Path(path).stem  # e.g. 96A63CE2-CA03-4438-840A-5C8A21FB8FBD_4_5005_c
            parts = stem.split("_")
            uuid = parts[0] if parts else ""

            if uuid:
                updated_metadata = {**metadata, "apple_uuid": uuid}
                ids_to_update.append(id_)
                updated_metadatas.append(updated_metadata)

        if ids_to_update:
            collection.update(ids=ids_to_update, metadatas=updated_metadatas)
            total_updated += len(ids_to_update)
            print(f"Updated {total_updated:,} entries so far...")

        offset += CHUNK_SIZE

    print(f"\n✅ Done. Updated: {total_updated:,} | Already had UUID: {total_skipped:,}")

if __name__ == "__main__":
    backfill()
