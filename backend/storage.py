"""
Climb Cutter working-copy storage
==================================
Two questions this answers: how much disk the app's own uploaded copies are
using right now, and how to bulk-free it without touching an original or
wiping the savings credit the user already earned.

`get_usage` reads the disk directly rather than summing queue entries — the
uploads dir is the ground truth, and content-hash dedupe already guarantees
one file per queue row (see `video_upload._settle_path`), so the two numbers
never need reconciling.

`purge_all_working_copies` is deliberately not new deletion logic: it is a
loop over `queue_removal.remove_from_queue`, the same keep-savings remove the
"Remove from queue" button already uses. That function already knows how to
tell an owned working copy from someone's original and already leaves
savings.json alone — duplicating that here would be a second place to get it
wrong.

Plain functions only (no Flask); server.py wraps this in routes.
"""

import export_job
import motion_review as mr
import queue_removal


def _uploads_dir():
    return mr.MOTION_DIR / "uploads"


def get_usage() -> dict:
    """Total bytes and file count under uploads/, excluding the .incoming
    staging dir (in-flight uploads, not a stored working copy)."""
    uploads_dir = _uploads_dir()
    incoming = uploads_dir / ".incoming"
    total_bytes = 0
    count = 0
    if uploads_dir.exists():
        for path in uploads_dir.rglob("*"):
            if not path.is_file():
                continue
            if incoming in path.parents:
                continue
            try:
                total_bytes += path.stat().st_size
            except OSError:
                continue
            count += 1
    return {"total_bytes": total_bytes, "count": count}


def purge_all_working_copies() -> dict:
    """Remove every OWNED queue entry via the keep-savings remove path.

    Skips (rather than fails on) a video currently exporting — one in-flight
    export must not block cleaning up everything else. Returns a summary the
    UI can report back to the user.
    """
    purged = 0
    freed_bytes = 0
    skipped = 0
    for entry in mr.list_queue():
        if not entry.get("owned"):
            continue
        video_id = entry["video_id"]
        if export_job.is_exporting(video_id):
            skipped += 1
            continue
        result = queue_removal.remove_from_queue(video_id)
        purged += 1
        freed_bytes += result["freed_bytes"]
    return {"purged": purged, "freed_bytes": freed_bytes, "skipped": skipped}
