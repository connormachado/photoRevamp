"""
Shared key/value app-settings store
====================================
The one general, persisted place for app-wide settings — as opposed to
video_motion.py's own `motion_review/config.json`, which stays feature-scoped
tuning knobs (pixel_diff_threshold, sample_fps, ...) and is untouched by this
module. Seeded with the Photos library root; later settings get added here
too, which is why the API is a plain get/set over an opaque dict rather than
anything shaped around one key.

On disk (`photo_db/config.json`): {"schema_version": 1, "library_root": "..."}.
Atomic writes mirror dismissed.py / video_motion.py (temp file + os.replace).
`set()` always round-trips the FULL on-disk dict via `load()` — never a
filtered/known-keys-only subset — so a key this module doesn't recognize
(written by a future caller, or hand-edited in) survives being read back out
and re-saved.

`load()`/`get()` NEVER write to disk, unlike video_motion.load_config()'s
persist-on-missing-file behaviour. That matters because get_library_root() is
called at IMPORT time by safe_paths.py/embed_job.py/video_motion.py, and
importing those modules must stay side-effect-free — tests import safe_paths
during collection, before any fixture has redirected CONFIG_PATH, so a
write-on-read here would drop a real file into the live, gitignored
photo_db/ on the first `pytest` invocation. Only `set()` and the explicit,
server.py-startup-only `ensure_seeded()` ever touch disk.

A manually-edited config.json takes effect on the next server restart, not
live — the same restart-to-apply behaviour PHOTO_MEMORY_ROOTS already has.

Logic only — routes live in server.py.
"""

import json
import os
import tempfile
import threading
from pathlib import Path

from utils import DEFAULT_DB_PATH

CONFIG_PATH = DEFAULT_DB_PATH / "config.json"
SCHEMA_VERSION = 1

# Detected once at import — the same literal safe_paths.py / embed_job.py /
# video_motion.py used to hardcode independently.
_DEFAULT_LIBRARY_ROOT = str(Path.home() / "Pictures" / "Photos Library.photoslibrary")

_DEFAULTS = {
    "schema_version": SCHEMA_VERSION,
    "library_root": _DEFAULT_LIBRARY_ROOT,
}

# Reentrant: set() calls load() while already holding _LOCK, same reason
# motion_review._LEDGER_LOCK is an RLock rather than a plain Lock.
_LOCK = threading.RLock()


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON to path atomically (temp file + os.replace). Mirrors
    dismissed._atomic_write_json / motion_review._atomic_write_json."""
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


def load() -> dict:
    """Full config dict, merged over defaults. Never writes, never raises —
    a missing or corrupt file just falls back to in-memory defaults."""
    with _LOCK:
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH) as f:
                    on_disk = json.load(f)
                if isinstance(on_disk, dict):
                    # Unknown keys in on_disk survive this merge untouched —
                    # the mechanism that lets a future caller's key round-trip.
                    return {**_DEFAULTS, **on_disk}
            except (OSError, ValueError):
                # ValueError covers json.JSONDecodeError AND UnicodeDecodeError
                # (a file saved in the wrong encoding) — both are "unreadable
                # config", and this function's contract is to never raise on one.
                pass
        return dict(_DEFAULTS)


def get(key: str, default=None):
    return load().get(key, default)


def set(key: str, value) -> dict:
    """Read-modify-write the full on-disk dict. The only function that writes."""
    with _LOCK:
        data = load()
        data[key] = value
        _atomic_write_json(CONFIG_PATH, data)
        return data


def get_library_root() -> Path:
    """The one key read by safe_paths.py / embed_job.py / video_motion.py.

    A stored value that isn't a non-empty string (JSON null/number/list from a
    bad hand-edit, or "") is treated as absent rather than handed to Path() —
    an empty string would otherwise resolve to the process's cwd, and a wrong
    type would raise at import time and take the whole server down with it.
    """
    value = get("library_root", _DEFAULT_LIBRARY_ROOT)
    if not isinstance(value, str) or not value.strip():
        value = _DEFAULT_LIBRARY_ROOT
    return Path(value).expanduser()


def validate_library_root(root: Path | str | None = None) -> dict:
    """Pure inspection, no state changes. Defaults to the current
    get_library_root() if root isn't given. Checks existence and the two
    subtrees this app depends on: resources/derivatives (photo indexing) and
    originals (video resolution)."""
    path = Path(root).expanduser() if root is not None else get_library_root()
    exists = path.exists()
    is_dir = exists and path.is_dir()
    has_derivatives = is_dir and (path / "resources" / "derivatives").is_dir()
    has_originals = is_dir and (path / "originals").is_dir()
    return {
        "path": str(path),
        "exists": exists,
        "is_dir": is_dir,
        "has_derivatives": has_derivatives,
        "has_originals": has_originals,
        "valid": is_dir and has_derivatives and has_originals,
    }


def ensure_seeded() -> dict:
    """Create config.json with defaults if it doesn't exist yet. Idempotent.
    Called ONCE from server.py's __main__ block — never from a module
    import — so importing safe_paths/embed_job/video_motion stays
    side-effect-free (see the module docstring)."""
    with _LOCK:
        if not CONFIG_PATH.exists():
            _atomic_write_json(CONFIG_PATH, dict(_DEFAULTS))
    return load()
