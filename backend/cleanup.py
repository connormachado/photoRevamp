"""
Cleanup / OS integration
========================
Helpers that hand a photo off to native macOS apps for review and deletion.
Keeps the camera-roll-cleanup workflow inside the apps the user already trusts
(Photos.app, Finder) rather than reimplementing deletion ourselves.
"""

import os
import subprocess
from pathlib import Path

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


def open_in_photos(path: str) -> dict:
    """Reveal a photo in Apple Photos.app.

    Photos.app has no API to open a file by path, so we find the media item and
    `spotlight` it — that scrolls to and highlights the specific item (unlike the
    unreliable `search` command). Returns {"success": bool, "error"?: str}.

    For iCloud libraries the on-disk originals are UUID-named (e.g.
    "9F958F95-….heic") and that UUID is the prefix of the Photos media item id
    ("9F958F95-…/L0/001"), so we match on `id` first. We fall back to matching
    `filename` for libraries where the on-disk name is the original camera name.
    """
    # The on-disk stem: a UUID for iCloud libraries, else the original filename.
    # Strip quotes so the name can't break out of the AppleScript string literal.
    name = Path(path).stem.replace('"', "").replace("\\", "")
    applescript = f'''
    tell application "Photos"
      activate
      set theItems to (every media item whose id contains "{name}")
      if (count of theItems) is 0 then
        set theItems to (every media item whose filename contains "{name}")
      end if
      if (count of theItems) > 0 then
        set theItem to item 1 of theItems
        spotlight theItem
      else
        error "No photo matching \\"{name}\\" found in the Photos library"
      end if
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip() or "osascript failed"}
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
