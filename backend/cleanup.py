"""
Cleanup / OS integration
========================
Helpers that hand a photo off to native macOS apps for review and deletion.
Keeps the camera-roll-cleanup workflow inside the apps the user already trusts
(Photos.app, Finder) rather than reimplementing deletion ourselves.
"""

import os
import subprocess

import chromadb

from utils import DEFAULT_DB_PATH, COLLECTION_NAME

def remove_missing_photos(collection):
    CHUNK_SIZE = 5000
    offset = 0
    ids_to_delete = []
    total_checked = 0

    while True:
        batch = collection.get(include=["metadatas"], limit=CHUNK_SIZE, offset=offset)
        if not batch["ids"]:
            break

        for id_, metadata in zip(batch["ids"], batch["metadatas"]):
            path = metadata.get("path")
            if path and not os.path.exists(path):
                ids_to_delete.append(id_)

        total_checked += len(batch["ids"])
        offset += CHUNK_SIZE

    if ids_to_delete:
        collection.delete(ids=ids_to_delete)

    return {"removed": len(ids_to_delete), "checked": total_checked}


def reveal_in_photos(uuid: str) -> dict:
    """Activate Photos.app and spotlight the media item with this asset UUID.

    Photos are indexed from the derivatives cache, whose paths Photos doesn't
    know about — so we reveal by the Apple Photos asset UUID (stored as
    `apple_uuid` in metadata) via `spotlight media item id`, which scrolls to
    and highlights the exact photo. Returns {"success": bool, "error"?: str}.
    """
    # Strip quotes so the UUID can't break out of the AppleScript string literal.
    uuid = uuid.replace('"', "").replace("\\", "")
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Photos" to activate'],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["osascript", "-e", f'tell application "Photos" to spotlight media item id "{uuid}"'],
            check=True, capture_output=True, text=True,
        )
        return {"success": True}
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": (e.stderr or "").strip() or str(e)}


def photo_size_bytes(uuid: str) -> int:
    """Best-effort original file size for a Photos asset, in bytes. 0 if unknown.

    The size we actually want is the *original's*, which only Photos knows — the
    paths we index are derivatives and are far smaller. `size of media item id`
    reports the real one.

    Deliberately its own osascript call rather than a return value bolted onto
    `reveal_in_photos`: that script has an unconfirmed intermittent failure, so
    it stays byte-identical and a size lookup can never be the cause. Every
    failure mode here is swallowed — a missing size must never turn a successful
    reveal into an error.
    """
    uuid = uuid.replace('"', "").replace("\\", "")
    try:
        out = subprocess.run(
            ["osascript", "-e", f'tell application "Photos" to return size of media item id "{uuid}"'],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return max(0, int(out.stdout.strip()))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        return 0
