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


def remove_missing_photos(collection=None) -> dict:
    """Prune ChromaDB entries whose underlying file no longer exists on disk.

    When a photo is deleted (in Photos.app or Finder), ChromaDB still holds its
    embedding + metadata and it keeps surfacing in search with a dead path. This
    reads every entry's stored `path`, checks os.path.exists, and deletes the
    ones that are gone.

    Does ZERO embedding work — only reads from and deletes within ChromaDB. Safe
    to call repeatedly: a clean library removes nothing. Pass `collection` to
    reuse an open handle (server does this); otherwise it opens its own client.
    Returns {"removed": <count>, "checked": <total>}.
    """
    if collection is None:
        client = chromadb.PersistentClient(path=str(DEFAULT_DB_PATH))
        collection = client.get_collection(COLLECTION_NAME)

    stored = collection.get(include=["metadatas"])  # ids are always returned
    ids = stored["ids"]
    metadatas = stored["metadatas"]

    dead_ids = [
        id_
        for id_, meta in zip(ids, metadatas)
        if not os.path.exists((meta or {}).get("path", ""))
    ]

    if dead_ids:
        collection.delete(ids=dead_ids)

    return {"removed": len(dead_ids), "checked": len(ids)}


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
