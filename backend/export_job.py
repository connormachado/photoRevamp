"""
Export job control
===================
Runs a Climb Cutter export (`motion_review.export_to_photos`) on a background
thread and tracks its progress in a small JSON status file, so a multi-minute
ffmpeg re-encode no longer blocks the Flask request thread — the route kicks
off a job and returns immediately; the UI polls `GET /motion-review/export/status`.

Why a THREAD and not a detached subprocess, unlike embed_job.py
-----------------------------------------------------------------
`embed_job.py` shells out because the alternative — running CLIP inline —
would load a second model onto the MPS device *in the request thread* and
block the whole server. Export has no such problem: its heavy work
(`subprocess.run`/`Popen` ffmpeg, `osascript`) already runs out-of-process and
releases the GIL while it waits, so a thread gets real concurrency without
paying a subprocess's costs — a child process would inherit none of the test
suite's in-process monkeypatches (`isolate_stats`, `tmp_motion_db` patch
*this* interpreter's module attributes), would put `motion_review`'s
`_LEDGER_LOCK` out of reach across process boundaries, and would orphan a
minutes-long encode the moment `make start`'s Ctrl-C tears down the server.

Staleness is judged by boot id, not pid
-----------------------------------------
`embed_job._pid_alive` uses `os.kill(pid, 0)`, which macOS can get wrong: pids
are reused, so a stale "running" status could in principle attach itself to
some unrelated live process and never downgrade. `BOOT_ID` is a fresh uuid4
per process start; a job claiming a *different* boot id was written before
this process existed (a restart happened mid-export) and is unconditionally
stale, no liveness probe required. The in-process `_LIVE` thread handle covers
same-process staleness (the thread died without writing a terminal status —
should not happen, since the thread body's own except/finally always does,
but this is the belt to that suspenders), and a wall-clock ceiling catches
anything that neither of those does.

One job at a time, globally
-----------------------------
Stricter than "one export per video" — there is exactly one job-status file
and one writer, which is simpler to reason about and keeps the machine
responsive against whatever else (preview transcodes) might be running.
`is_exporting()` is what `/motion-review/decision` and `/motion-review/remove`
consult to refuse a request that would otherwise race a running export.

Plain functions only (no Flask); server.py wraps these in routes.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import motion_review
import safe_paths

# A fresh id every time this process starts. A job status file whose boot_id
# doesn't match this process's is left over from before a restart — see the
# module docstring's staleness note.
BOOT_ID = uuid4().hex

# Guards the state transition (read current status -> decide -> write new
# status) so two near-simultaneous calls to start_export can't both see
# "idle" and both start a thread. Reentrant: start_export calls read_status()
# while already holding it (the staleness-downgrade check inside read_status
# needs the same lock), and the thread body's `finally` takes it too.
_LOCK = threading.RLock()

# The one background export thread, if any is running. None whenever no job
# is in flight. Only ever read/written under _LOCK.
_LIVE: threading.Thread | None = None

# past this many seconds since started_at, a non-terminal job is presumed
# wedged rather than trusted forever. Generous — a real climbing clip's
# re-encode is minutes, not hours.
MAX_JOB_SECONDS = 60 * 60

TERMINAL_STATES = {"idle", "done", "failed"}

IDLE_JOB = {
    "job_id": None,
    "video_id": None,
    "state": "idle",   # idle | queued | rendering | importing | revealing | done | failed
    "progress": 0.0,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "boot_id": None,
    "result": None,
}


def _job_path() -> Path:
    """Where the one job-status file lives.

    A FUNCTION, not a module-level constant, so it re-reads
    `motion_review.MOTION_DIR` on every call — that is what makes it follow
    the `tmp_motion_db` fixture's monkeypatch of that attribute, the same
    reasoning `queue_removal._uploads_dir()` documents for the same pattern.
    """
    return motion_review.MOTION_DIR / "export_job.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_raw(path: Path) -> dict:
    try:
        return {**IDLE_JOB, **json.loads(path.read_text())}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(IDLE_JOB)


def _write_raw(updates: dict, path: Path) -> dict:
    """Merge `updates` into the status file and write it back atomically.

    Same tmp-file + os.replace idiom as embed_job.write_status: the UI polls
    this file roughly once a second and must never catch a half-written
    document.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**_read_raw(path), **updates}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, indent=2))
    os.replace(tmp, path)
    return merged


# ── Status ────────────────────────────────────────────────────────────────────

def read_status() -> dict:
    """Return the current job status, downgrading a STALE non-terminal one.

    "Stale" is any of three independent things: the status was written by a
    different process (a restart happened mid-export), this process has no
    live thread for it (should be unreachable, but a status file surviving a
    crash that skipped the thread body's own except/finally is exactly the
    case this exists for), or it has been running longer than
    MAX_JOB_SECONDS. Any one of the three is enough — an export cannot
    plausibly still be legitimately in flight if even one holds.
    """
    path = _job_path()
    status = _read_raw(path)

    if status.get("state") in TERMINAL_STATES:
        return status

    with _LOCK:
        # Re-read inside the lock: another thread may have already resolved
        # this (finished normally, or another read_status call downgraded it)
        # between the read above and taking the lock.
        status = _read_raw(path)
        if status.get("state") in TERMINAL_STATES:
            return status

        stale_boot = status.get("boot_id") != BOOT_ID
        dead_thread = _LIVE is None or not _LIVE.is_alive()
        wall_clock_expired = False
        started_at = status.get("started_at")
        if started_at:
            try:
                started = datetime.fromisoformat(started_at)
                now = datetime.now(started.tzinfo) if started.tzinfo else datetime.now()
                wall_clock_expired = (now - started).total_seconds() > MAX_JOB_SECONDS
            except (ValueError, TypeError):
                wall_clock_expired = False

        if stale_boot or dead_thread or wall_clock_expired:
            status = _write_raw({
                "state": "failed",
                "finished_at": _now_iso(),
                "error": "The export stopped before it finished (did the server restart?)",
            }, path)

    return status


def is_exporting(video_id: str | None = None) -> bool:
    """True if an export is currently in flight.

    With no argument: true if ANY export is running (global one-at-a-time, so
    at most one ever is). With a video_id: true only if the running export
    (if any) is for that specific video — what `/decision` and `/remove` use
    to refuse a request that would otherwise race the export's render or
    ledger write.
    """
    status = read_status()
    if status.get("state") in TERMINAL_STATES:
        return False
    if video_id is None:
        return True
    return status.get("video_id") == video_id


# ── Kickoff ───────────────────────────────────────────────────────────────────

def start_export(video_id: str, regions: list | None = None,
                  cut_segments: list | None = None) -> dict:
    """Kick off a background export for *video_id*. Returns immediately.

    {"started": bool, "reason_code": str|None, "reason": str|None, "status": dict}

    The path-safety and proposal-existence checks below run SYNCHRONOUSLY,
    before any thread or status write, so a traversing or unknown video_id
    still gets an immediate 4xx/404 out of the route rather than a 202 that
    can only fail later, asynchronously and invisibly to
    tests/test_route_security.py's traversal sweep and
    tests/test_input_validation.py's missing/unknown-id assertions — both of
    which predate this module and expect the SAME status codes a synchronous
    export always gave.

    Raises ValueError/UnsafePathError (bad id) or FileNotFoundError (no such
    proposal). Never raises for "already running" — that is a normal,
    expected outcome and comes back as `started: False` instead.
    """
    global _LIVE

    safe_paths.safe_id_component(video_id)          # raises on a traversing id

    if motion_review.get_proposal(video_id) is None:
        raise FileNotFoundError(f"no proposal for {video_id}")

    with _LOCK:
        status = read_status()
        if status.get("state") not in TERMINAL_STATES:
            return {
                "started": False,
                "reason_code": "already_running",
                "reason": "An export is already in progress.",
                "status": status,
            }

        job_id = uuid4().hex
        thread = threading.Thread(
            target=_run_export, args=(job_id, video_id, regions, cut_segments),
            daemon=True,
        )

        try:
            _LIVE = thread
            status = _write_raw({
                "job_id": job_id,
                "video_id": video_id,
                "state": "queued",
                "progress": 0.0,
                "started_at": _now_iso(),
                "finished_at": None,
                "error": None,
                "boot_id": BOOT_ID,
                "result": None,
            }, _job_path())
            thread.start()
        except Exception as exc:
            # Never leave "queued" written with nothing actually running.
            _LIVE = None
            status = _write_raw({
                "state": "failed",
                "finished_at": _now_iso(),
                "error": f"{type(exc).__name__}: {exc}",
            }, _job_path())
            return {
                "started": False,
                "reason_code": "failed_to_start",
                "reason": str(exc),
                "status": status,
            }

    return {"started": True, "reason_code": None, "reason": None, "status": status}


# ── Background thread body ────────────────────────────────────────────────────

def _on_progress(job_id: str, phase: str, frac: float | None) -> None:
    """Map an `export_and_import` phase onto this job's overall 0..1 progress.

    Rendering is genuinely almost all of the wall-clock time (a full ffmpeg
    re-encode), so it gets the first 90%; importing and revealing are each a
    couple of AppleScript calls with no meaningful sub-progress of their own,
    so they just advance the needle to a fixed point. `done` (1.0) is written
    by `_run_export` itself on success, not through here.
    """
    if phase == "rendering":
        state, progress = "rendering", 0.90 * (frac or 0.0)
    elif phase == "importing":
        state, progress = "importing", 0.90
    elif phase == "revealing":
        state, progress = "revealing", 0.97
    else:
        return
    _write_raw({"job_id": job_id, "state": state, "progress": progress}, _job_path())


def _run_export(job_id: str, video_id: str, regions: list | None,
                cut_segments: list | None) -> None:
    """The thread target: render/import/reveal, then write a terminal status.

    The terminal write happens BEFORE `_LIVE` is cleared (in the `finally`
    below), not after — so a poll can never observe "state: done" alongside a
    dead/absent thread and misread that as a wedged job.
    """
    global _LIVE
    try:
        result = motion_review.export_to_photos(
            video_id, regions=regions, cut_segments=cut_segments,
            progress_cb=lambda phase, frac: _on_progress(job_id, phase, frac),
        )
        _write_raw({
            "job_id": job_id,
            "state": "done",
            "progress": 1.0,
            "finished_at": _now_iso(),
            "error": None,
            "result": result,
        }, _job_path())
    except Exception as exc:
        _write_raw({
            "job_id": job_id,
            "state": "failed",
            "finished_at": _now_iso(),
            "error": f"{type(exc).__name__}: {exc}",
        }, _job_path())
    finally:
        with _LOCK:
            _LIVE = None
