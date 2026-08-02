"""
Queue removal — dropping a Climb Cutter entry and reclaiming its disk
=====================================================================
`reject` is bookkeeping (see `motion_review.record_decision`): it writes a
verdict and leaves the row in the queue forever. This module is the other half —
it makes the row *go away* and frees the bytes the app itself put on disk.

The whole point of a separate module is the one thing it must never do: delete a
file the user already had. `video_motion.process_video` writes an identical
proposal whether the source is a copy WE made under `uploads/` or a Photos
original the entry merely references, so "is this ours?" is a question with a
real wrong answer. `_owned_source` is where it gets answered, and it demands two
independent yeses:

  1. the proposal's `owned` flag (set only by the upload route), and
  2. the path actually resolving inside the uploads dir.

Neither alone is enough. The flag is a claim written by whatever called
`process_video`; the containment check is enforcement, and because
`resolve_within_roots` resolves symlinks *before* comparing, a symlink parked in
`uploads/` that points into the Photos library fails it. A proposal that claims
`owned: true` about a path outside `uploads/` gets nothing deleted.

What survives a removal: `reviews/<id>.json` and every existing line of
`decisions.jsonl`. Removal is cleanup, not a history rewrite — it appends one
`action: "remove"` line and rewrites nothing. `savings.json` is left alone too;
the reject that precedes a removal has already popped the video from it via
`_apply_savings`.

Plain functions only (no Flask); server.py wraps this in a route.
"""

import json
import os
from pathlib import Path

import motion_review as mr
import safe_paths


def _uploads_dir() -> Path:
    """The dir holding copies the app made for itself.

    Derived from `mr.MOTION_DIR` rather than imported from `video_upload` so it
    follows the test fixture's redirect (and so this module doesn't drag
    numpy/ffmpeg in behind it). It MUST stay equal to `video_upload.UPLOADS_DIR`
    — `test_queue_removal.py` asserts the two agree.
    """
    return mr.MOTION_DIR / "uploads"


def _resolves_inside(raw: str, root: Path) -> Path | None:
    """*raw* as a resolved path if it sits inside *root*, else None.

    Refusal is a None, never an exception: every caller here is deciding whether
    to unlink something, and an error that could be caught and shrugged off is a
    worse shape for that decision than a value that plainly says "no".
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        return safe_paths.resolve_within_roots(raw, [root])
    except safe_paths.UnsafePathError:
        return None


def _owned_source(prop: dict) -> Path | None:
    """The source file this app is allowed to delete, or None.

    None covers every case that isn't a working copy of ours: a Photos original,
    a hand-fed CLI path, a symlink out of `uploads/`, or a proposal whose flag
    says one thing and whose path says another.
    """
    source_path = prop.get("source_path", "")
    resolved = _resolves_inside(source_path, _uploads_dir())
    if resolved is None:
        return None                      # not under uploads/ — never ours

    flag = prop.get("owned")
    if flag is True:
        return resolved
    if flag is None:
        # Proposals written before the flag existed. Sitting under uploads/ is
        # the only way a file got there, so infer it — but only when the field
        # is genuinely absent. An explicit `owned: false` is a decision and is
        # honoured as one.
        return resolved
    return None


def _unlink_within(path_str: str | None, root: Path) -> int:
    """Delete *path_str* if it resolves inside *root*. Returns bytes freed."""
    if not path_str:
        return 0
    resolved = _resolves_inside(str(path_str), root)
    if resolved is None:
        return 0
    try:
        size = resolved.stat().st_size
    except OSError:
        return 0
    try:
        resolved.unlink()
    except OSError:
        return 0
    return size


def remove_from_queue(video_id: str) -> dict:
    """Drop a video from the review queue and free the files the app created.

    Raises FileNotFoundError if there is no such queue entry, and ValueError
    (via `safe_id_component`) if the id carries path syntax.
    """
    prop_path = mr._proposal_path(video_id)          # validates the id
    prop = mr._read_json(prop_path)
    if not prop:
        raise FileNotFoundError(f"no proposal for {video_id}")

    motion_dir = mr.MOTION_DIR
    source_name = os.path.basename(prop.get("source_path", "")) or video_id
    freed = 0

    # Derivatives first. These are app-created for EVERY entry regardless of who
    # owns the source, and re-analysing the video regenerates them, so leaving
    # them orphaned once the row is gone is pure waste.
    artifacts = prop.get("artifacts") or {}
    freed += _unlink_within(artifacts.get("trimmed"), motion_dir)
    freed += _unlink_within(artifacts.get("timelapse"), motion_dir)

    # Preview proxies, current suffix plus every retired one.
    for suffix in (mr.PREVIEW_SUFFIX, *mr.PREVIEW_LEGACY_SUFFIXES):
        freed += _unlink_within(str(mr._preview_path(video_id, suffix)), motion_dir)

    # The draft is a resume point for an entry that is about to stop existing —
    # not history. The review is history and stays.
    freed += _unlink_within(str(mr._draft_path(video_id)), motion_dir)

    # The working copy, only if it is genuinely ours.
    owned_source = _owned_source(prop)
    deleted_source = False
    if owned_source is not None and owned_source.exists():
        # Containment is re-checked here, one call away from the unlink itself,
        # rather than trusted from the decision made above.
        freed += _unlink_within(str(owned_source), _uploads_dir())
        deleted_source = not owned_source.exists()
        # uploads/<content-hash>/ exists only to hold that one file.
        try:
            owned_source.parent.rmdir()
        except OSError:
            pass

    # The proposal LAST: it is what makes the row exist, so anything that failed
    # above leaves a still-visible entry the user can retry rather than an
    # invisible pile of orphans.
    freed += _unlink_within(str(prop_path), motion_dir)

    _log_removal(video_id, prop, freed, deleted_source)

    return {
        "video_id": video_id,
        "removed": True,
        "freed_bytes": freed,
        "deleted_source": deleted_source,
        "source_name": source_name,
    }


def _log_removal(video_id: str, prop: dict, freed: int, deleted_source: bool) -> None:
    """Append one line to decisions.jsonl. Adds to the audit; rewrites nothing."""
    audit = {
        "ts": mr._now_iso(),
        "action": "remove",
        "video_id": video_id,
        "apple_uuid": prop.get("apple_uuid", ""),
        "source_name": os.path.basename(prop.get("source_path", "")),
        "freed_bytes": freed,
        "deleted_source": deleted_source,
    }
    mr.DECISIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(mr.DECISIONS_LOG, "a") as f:
        f.write(json.dumps(audit) + "\n")
