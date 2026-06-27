"""
Cleanup / OS integration
========================
Helpers that hand a photo off to native macOS apps for review and deletion.
Keeps the camera-roll-cleanup workflow inside the apps the user already trusts
(Photos.app, Finder) rather than reimplementing deletion ourselves.
"""

import subprocess
from pathlib import Path


def open_in_photos(path: str) -> dict:
    """Reveal a photo in Apple Photos.app.

    Photos.app has no API to open a file by path, so we search its library for
    the filename (without extension — that's the original asset name) and
    spotlight the first match. Returns {"success": bool, "error"?: str}.
    """
    # Strip quotes so the name can't break out of the AppleScript string literal.
    name = Path(path).stem.replace('"', "").replace("\\", "")
    applescript = f'''
    tell application "Photos"
      activate
      set matchingItems to (search for "{name}")
      if (count of matchingItems) > 0 then
        set theItem to item 1 of matchingItems
        spotlight theItem
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
