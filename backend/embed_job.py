"""
Embed job control
=================
Runs the indexing pipeline as a background subprocess and tracks its progress in
a small JSON status file, so the web UI can kick off a catch-up index without
the terminal.

Why a subprocess and not an import: `embed_photos.index_photos` is perfectly
importable, but calling it inside Flask would load a second copy of CLIP onto
the MPS device *in the request thread* and block the server for the entire run —
including the status polls the UI needs to answer. Shelling out keeps the heavy
work in its own process; this module only spawns it and reads its status file.

`embed_photos.py` remains the single source of embedding truth — nothing here
re-implements any part of it.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import config_store
from utils import DEFAULT_DB_PATH

BACKEND_DIR = Path(__file__).resolve().parent

# The derivatives cache is the only tree that matches embed_photos.py's file
# filter (*_5005_c.jpeg), and it's what every path currently in ChromaDB points
# at. Scanning it takes well under a second.
PHOTOS_ROOT = config_store.get_library_root() / "resources" / "derivatives"

STATUS_PATH = DEFAULT_DB_PATH / "embed_status.json"
LOG_PATH = DEFAULT_DB_PATH / "embed_log.txt"

# States: idle → scanning → running → done | failed
IDLE_STATUS = {
    "state": "idle",
    "done": 0,
    "total": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "pid": None,
    "total_in_db": None,
}


# ── Status file ───────────────────────────────────────────────────────────────

def _pid_alive(pid) -> bool:
    """True if this PID is still a live process. Signal 0 checks without killing."""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def read_status(path: Path = STATUS_PATH) -> dict:
    """Return the current job status, defaulting to idle if no run has happened.

    Also downgrades a *stale* running state: if the status says a job is in
    flight but its PID is gone (killed, or crashed hard enough to skip its own
    error handler), report it as failed rather than leaving the UI spinning
    against a process that no longer exists.
    """
    try:
        status = {**IDLE_STATUS, **json.loads(path.read_text())}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(IDLE_STATUS)

    if status.get("state") in ("scanning", "running"):
        pid = status.get("pid")
        # pid is None only in the brief window between spawning the child and
        # recording its pid — not stale, just not written yet.
        if pid is not None and not _pid_alive(pid):
            status["state"] = "failed"
            status["error"] = "The indexing process exited without finishing."
    return status


def write_status(updates: dict, path: Path = STATUS_PATH) -> dict:
    """Merge `updates` into the status file and write it back atomically.

    Written to a temp file then renamed, because the UI polls this file every
    couple of seconds and must never catch a half-written JSON document.
    Merging (rather than overwriting) means the child can report progress
    without clobbering the pid the parent recorded, and vice versa.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = {**IDLE_STATUS, **json.loads(path.read_text())}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        current = dict(IDLE_STATUS)

    merged = {**current, **updates}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, indent=2))
    os.replace(tmp, path)   # atomic on POSIX
    return merged


# ── Launching ─────────────────────────────────────────────────────────────────

def start_job(photos_root: Path = PHOTOS_ROOT, db_path: Path = DEFAULT_DB_PATH) -> dict:
    """Spawn an incremental embed in the background.

    Refuses if one is already running — a second process would race the first
    over the same ChromaDB. Returns {"started", "reason", "reason_code", "status"}.
    """
    status = read_status()

    if status.get("state") in ("scanning", "running") and _pid_alive(status.get("pid")):
        return {
            "started": False,
            "reason_code": "already_running",
            "reason": "An indexing run is already in progress.",
            "status": status,
        }

    if not photos_root.exists():
        return {
            "started": False,
            "reason_code": "no_photos_root",
            "reason": f"Photo library not found at {photos_root}",
            "status": status,
        }

    # Seed the status before spawning so an immediate poll sees "scanning"
    # rather than the previous run's leftover "done".
    started_at = datetime.now().isoformat(timespec="seconds")
    write_status({
        "state": "scanning",
        "done": 0,
        "total": None,
        "started_at": started_at,
        "finished_at": None,
        "error": None,
        "pid": None,
        "total_in_db": None,
    })

    cmd = [
        sys.executable,                                  # inherits the arm64 venv → MPS, not Rosetta
        str(BACKEND_DIR / "embed_photos.py"),
        "--photos", str(photos_root),
        "--db", str(db_path),
        "--status-file", str(STATUS_PATH),
    ]

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG_PATH, "a")
    log.write(f"\n===== embed run started {started_at} =====\n")
    log.flush()

    # start_new_session detaches the child from the server's process group, so
    # Ctrl-C in the `make start` terminal stops the servers without killing an
    # embed halfway through a ChromaDB write.
    proc = subprocess.Popen(
        cmd,
        cwd=str(BACKEND_DIR),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    status = write_status({"pid": proc.pid})
    return {"started": True, "reason_code": None, "reason": None, "status": status}
