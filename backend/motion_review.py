"""
Motion review
=============
Read-side logic for the Climb Cutter review room (Phase 2). Reads the proposals
that `video_motion.py` wrote under photo_db/motion_review/ and records the
human verdict (reject | approve) for each video. Nothing here is destructive —
originals in Photos are never touched; we only serve previews and log decisions.

Plain functions only (no Flask); server.py wraps these in routes, matching the
convention used by stats.py / cleanup.py.
"""

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import imageio_ffmpeg

import stats as stats_store
from utils import DEFAULT_DB_PATH

# Reuse the pip-bundled ffmpeg (same one video_motion.py uses) so we don't depend
# on a system ffmpeg being on PATH. This build includes libx264.
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# ── Paths ─────────────────────────────────────────────────────────────────────
MOTION_DIR = DEFAULT_DB_PATH / "motion_review"
PROPOSALS_DIR = MOTION_DIR / "proposals"
REVIEWS_DIR = MOTION_DIR / "reviews"
PREVIEW_DIR = MOTION_DIR / "preview"        # cached browser-playable transcodes
DECISIONS_LOG = MOTION_DIR / "decisions.jsonl"
SAVINGS_PATH = MOTION_DIR / "savings.json"  # running pool of reclaimed bytes

VALID_VERDICTS = {"reject", "approve"}


# ── Small IO helpers ──────────────────────────────────────────────────────────

def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON to path atomically (temp file + os.replace), like stats.py."""
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


def _read_json(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _saved_bytes(size_bytes: int, original_duration: float, trimmed_duration: float) -> int:
    """Bytes reclaimed: source size × fraction of duration removed."""
    if not size_bytes or not original_duration or original_duration <= 0:
        return 0
    frac = max(0.0, 1.0 - (trimmed_duration or 0) / original_duration)
    return int(size_bytes * frac)


def _estimated_saved_bytes(prop: dict) -> int:
    """Proportional estimate of bytes reclaimed for a proposal's own cuts."""
    sp = prop.get("source_path", "")
    if not sp or not os.path.exists(sp):
        return 0
    try:
        size = os.path.getsize(sp)
    except OSError:
        return 0
    return _saved_bytes(size, prop.get("original_duration") or 0, prop.get("trimmed_duration") or 0)


def _sanitize_cuts(cuts: list, duration: float) -> list:
    """Clamp to [0, duration], drop empties, sort, and merge overlaps.

    The frontend already constrains drags, but the backend is authoritative — a
    malformed POST can't produce garbage cut/keep segments."""
    cleaned = []
    for seg in cuts or []:
        try:
            s = max(0.0, float(seg["start"]))
            e = min(float(duration), float(seg["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if e - s > 1e-3:
            cleaned.append((s, e))
    cleaned.sort()
    merged = []
    for s, e in cleaned:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return [{"start": round(s, 3), "end": round(e, 3)} for s, e in merged]


def _complement_segments(cuts: list, duration: float) -> list:
    """Keep segments = the gaps between cuts over [0, duration]."""
    keeps = []
    cursor = 0.0
    for seg in sorted(cuts or [], key=lambda s: s["start"]):
        s = max(0.0, float(seg["start"]))
        e = min(float(duration), float(seg["end"]))
        if s > cursor + 1e-3:
            keeps.append({"start": round(cursor, 3), "end": round(s, 3)})
        cursor = max(cursor, e)
    if cursor < duration - 1e-3:
        keeps.append({"start": round(cursor, 3), "end": round(float(duration), 3)})
    return keeps


# ── Verdict state ─────────────────────────────────────────────────────────────

def _review_path(video_id: str) -> Path:
    return REVIEWS_DIR / f"{video_id}.json"


def _get_verdict(video_id: str) -> dict:
    """Return {verdict, reviewed_at} for a video, or empty dict if unreviewed."""
    return _read_json(_review_path(video_id)) or {}


# ── Queue ─────────────────────────────────────────────────────────────────────

def list_queue() -> list[dict]:
    """List every processed video awaiting (or with) review.

    Reads each proposals/*.json, merges its saved verdict, and returns a compact
    per-video payload for the UI. Sorted unreviewed-first, then by creation time.
    """
    videos = []
    if not PROPOSALS_DIR.exists():
        return videos

    for prop_path in sorted(PROPOSALS_DIR.glob("*.json")):
        prop = _read_json(prop_path)
        if not prop:
            continue
        video_id = prop.get("video_id", prop_path.stem)
        review = _get_verdict(video_id)
        source_path = prop.get("source_path", "")
        source_exists = bool(source_path) and os.path.exists(source_path)
        artifacts = prop.get("artifacts") or {}
        timelapse = artifacts.get("timelapse")
        probe = prop.get("probe") or {}
        orig_dur = prop.get("original_duration", 0)
        proposed_cuts = prop.get("cut_segments", [])

        # If the video was reviewed WITH edited cuts, surface those so a reload
        # resumes with the user's edits. Proposals on disk are never mutated.
        if review.get("cut_segments") is not None:
            cut_segments = review["cut_segments"]
            keep_segments = review.get("keep_segments") or _complement_segments(cut_segments, orig_dur)
            trimmed_duration = review.get("trimmed_duration", prop.get("trimmed_duration", 0))
        else:
            cut_segments = proposed_cuts
            keep_segments = prop.get("keep_segments", [])
            trimmed_duration = prop.get("trimmed_duration", 0)

        size_bytes = os.path.getsize(source_path) if source_exists else 0

        videos.append({
            "video_id": video_id,
            "apple_uuid": prop.get("apple_uuid", ""),
            "source_name": os.path.basename(source_path) if source_path else video_id,
            "source_exists": source_exists,
            "original_duration": orig_dur,
            "trimmed_duration": trimmed_duration,
            "cut_segments": cut_segments,
            "keep_segments": keep_segments,
            "proposed_cut_segments": proposed_cuts,
            "num_cuts": len(cut_segments),
            "has_timelapse": bool(timelapse) and os.path.exists(timelapse),
            "width": probe.get("width", 0),
            "height": probe.get("height", 0),
            "fps": probe.get("fps", 0),
            "source_size_bytes": size_bytes,
            "estimated_saved_bytes": _saved_bytes(size_bytes, orig_dur, trimmed_duration),
            "verdict": review.get("verdict"),
            "edited": bool(review.get("edited")),
            "reviewed_at": review.get("reviewed_at"),
            "created": prop.get("created", ""),
        })

    # Unreviewed first, then oldest-created first for a stable review order.
    videos.sort(key=lambda v: (v["verdict"] is not None, v["created"]))
    return videos


def get_proposal(video_id: str) -> dict | None:
    """Return a single proposal dict merged with its verdict, or None."""
    prop = _read_json(PROPOSALS_DIR / f"{video_id}.json")
    if not prop:
        return None
    prop["review"] = _get_verdict(video_id)
    return prop


# ── Preview media ─────────────────────────────────────────────────────────────

def source_h264_path(video_id: str) -> Path:
    """Path to a browser-playable (h264/mp4) copy of the source video.

    Real iPhone videos are HEVC .mov, which Chrome won't decode, so we transcode
    the original to h264 once and cache it under preview/. Cheap on repeat views.
    Raises FileNotFoundError if the proposal or its source file is missing.
    """
    prop = _read_json(PROPOSALS_DIR / f"{video_id}.json")
    if not prop:
        raise FileNotFoundError(f"no proposal for {video_id}")
    source_path = prop.get("source_path", "")
    if not source_path or not os.path.exists(source_path):
        raise FileNotFoundError(f"source video missing for {video_id}: {source_path}")

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = PREVIEW_DIR / f"{video_id}_h264.mp4"

    # Reuse the cache unless the source is newer than the transcode.
    if out.exists() and out.stat().st_mtime >= os.path.getmtime(source_path):
        return out

    tmp = out.with_suffix(".tmp.mp4")
    cmd = [
        FFMPEG, "-y", "-i", source_path,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac",
        "-movflags", "+faststart",   # lets the browser start before full download
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f"ffmpeg transcode failed: {proc.stderr[-500:]}")
    os.replace(tmp, out)
    return out


def timelapse_path(video_id: str) -> Path | None:
    """Return the baked timelapse-of-removed-sections mp4, if it exists."""
    prop = _read_json(PROPOSALS_DIR / f"{video_id}.json")
    if not prop:
        return None
    timelapse = (prop.get("artifacts") or {}).get("timelapse")
    if timelapse and os.path.exists(timelapse):
        return Path(timelapse)
    return None


# ── Reclaimed-data pool ───────────────────────────────────────────────────────

def get_savings() -> dict:
    """Return the running reclaimed-data pool: {total_bytes, per_video}."""
    data = _read_json(SAVINGS_PATH) or {}
    per_video = data.get("per_video", {})
    return {"total_bytes": int(data.get("total_bytes", 0)), "per_video": per_video}


def _apply_savings(video_id: str, verdict: str, saved_bytes: int) -> int:
    """Update the pool for one verdict and return the new total.

    Accounted per-video so it stays correct if a video is re-reviewed or flipped:
    approve records this video's estimated savings; reject removes it.
    """
    data = get_savings()
    per_video = dict(data["per_video"])
    if verdict == "approve":
        per_video[video_id] = int(saved_bytes)
    else:  # reject → this footage is being kept, so it saves nothing
        per_video.pop(video_id, None)
    total = sum(per_video.values())
    _atomic_write_json(SAVINGS_PATH, {"total_bytes": total, "per_video": per_video})
    # Mirror the total into stats.json so it lives alongside the "photos deleted"
    # counter and rides the same /stats payload. savings.json stays the ledger
    # (per-video breakdown) so re-reviews stay idempotent.
    stats_store.set_reclaimed_bytes(total)
    return total


# ── Decision recording ────────────────────────────────────────────────────────

def record_decision(video_id: str, verdict: str, cut_segments: list | None = None) -> dict:
    """Record a per-video verdict, optionally with edited cut boundaries.

    On approve, if `cut_segments` is provided the backend sanitizes them and
    recomputes keep_segments / trimmed_duration / saved bytes authoritatively —
    the frontend's numbers are only a preview. Writes an append-only audit line
    (decisions.jsonl) and reviews/<video_id>.json (latest state, for resume).
    """
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"invalid verdict: {verdict!r}")

    prop = _read_json(PROPOSALS_DIR / f"{video_id}.json")
    if not prop:
        raise FileNotFoundError(f"no proposal for {video_id}")

    ts = _now_iso()
    orig_dur = prop.get("original_duration", 0)
    sp = prop.get("source_path", "")
    size_bytes = os.path.getsize(sp) if sp and os.path.exists(sp) else 0

    # Edited cuts only meaningfully apply to an approval ("apply these cuts").
    if verdict == "approve" and cut_segments is not None:
        cuts = _sanitize_cuts(cut_segments, orig_dur)
        keeps = _complement_segments(cuts, orig_dur)
        trimmed_duration = round(sum(k["end"] - k["start"] for k in keeps), 3)
        edited = cuts != _sanitize_cuts(prop.get("cut_segments", []), orig_dur)
    else:
        cuts = prop.get("cut_segments", [])
        keeps = prop.get("keep_segments", [])
        trimmed_duration = prop.get("trimmed_duration", 0)
        edited = False

    saved_bytes = _saved_bytes(size_bytes, orig_dur, trimmed_duration)

    # 1) Append-only audit log (one JSON object per line).
    audit = {
        "ts": ts,
        "video_id": video_id,
        "apple_uuid": prop.get("apple_uuid", ""),
        "verdict": verdict,
        "original_duration": orig_dur,
        "trimmed_duration": trimmed_duration,
        "estimated_saved_bytes": saved_bytes,
        "edited": edited,
        "cut_segments": cuts,
    }
    DECISIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DECISIONS_LOG, "a") as f:
        f.write(json.dumps(audit) + "\n")

    # 2) Update the global reclaimed-data pool (approve adds, reject removes).
    total_saved = _apply_savings(video_id, verdict, saved_bytes)

    # 3) Current state (overwrite) for the UI to reload/resume with edits.
    review = {
        "video_id": video_id,
        "verdict": verdict,
        "reviewed_at": ts,
        "cut_segments": cuts,
        "keep_segments": keeps,
        "trimmed_duration": trimmed_duration,
        "edited": edited,
    }
    _atomic_write_json(_review_path(video_id), review)
    return {
        **review,
        "video_saved_bytes": saved_bytes,
        "savings_total_bytes": total_saved,
    }
