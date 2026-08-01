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
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg

import edit_boundaries as eb
import export_video
import stats as stats_store
from utils import DEFAULT_DB_PATH

# Reuse the pip-bundled ffmpeg (same one video_motion.py uses) so we don't depend
# on a system ffmpeg being on PATH. This build includes libx264.
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# ── Paths ─────────────────────────────────────────────────────────────────────
MOTION_DIR = DEFAULT_DB_PATH / "motion_review"
PROPOSALS_DIR = MOTION_DIR / "proposals"
REVIEWS_DIR = MOTION_DIR / "reviews"
DRAFTS_DIR = MOTION_DIR / "drafts"          # in-progress, unapproved edit state
PREVIEW_DIR = MOTION_DIR / "preview"        # cached browser-playable transcodes
DECISIONS_LOG = MOTION_DIR / "decisions.jsonl"
SAVINGS_PATH = MOTION_DIR / "savings.json"  # running pool of reclaimed bytes

VALID_VERDICTS = {"reject", "approve"}

# Preview-proxy encode settings. The suffix carries a VERSION: bump it whenever
# the encode below changes and every cached proxy re-renders on next open, which
# a plain mtime check can't do (the source file hasn't moved).
PREVIEW_SUFFIX = "_h264_v2.mp4"
PREVIEW_LEGACY_SUFFIXES = ("_h264.mp4",)

# Cap the LONG side at 1280 — the panels are ~350px wide on screen, so a full
# 1080x1920 proxy was ~2.8x more pixels than anything could show, paid for on
# every decoded frame. `-2` keeps the other side even (h264 requires it).
# NOTE: this is a `-vf` scale, not `-filter_complex` — verified NOT to copy the
# source display matrix onto the output, so portrait clips stay upright with no
# rotation flag. (Don't assume; the export's filter path behaves differently —
# see the rotation invariant in CLAUDE.md.)
PREVIEW_SCALE = (
    "scale='if(gt(iw,ih),min(1280,iw),-2)':'if(gt(iw,ih),-2,min(1280,ih))'"
)


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


# ── Edit boundaries ───────────────────────────────────────────────────────────
# Regions are the source of truth; cut_segments / keep_segments are derived from
# them. Sanitizing and planning live in edit_boundaries.py so every boundary type
# goes through one place — see that module's docstring for the data model.

def _proposed_regions(prop: dict) -> list:
    """The proposal's own cuts as regions — the 'unedited' baseline."""
    orig_dur = prop.get("original_duration", 0)
    return eb.sanitize_regions(eb.regions_from_cuts(prop.get("cut_segments", [])), orig_dur)


def _resolve_regions(prop: dict, regions, cut_segments) -> list | None:
    """Coerce whatever a caller sent into a sanitized region list, or None.

    `regions` is the current wire format; `cut_segments` is the legacy one and is
    upgraded to cut regions. The backend is authoritative either way — the
    frontend's boundaries are only a preview until they pass through here.
    """
    orig_dur = prop.get("original_duration", 0)
    if regions is not None:
        return eb.sanitize_regions(regions, orig_dur)
    if cut_segments is not None:
        return eb.sanitize_regions(eb.regions_from_cuts(cut_segments), orig_dur)
    return None


def _derive(regions: list, prop: dict) -> dict:
    """Everything downstream still wants: cuts, keeps, trimmed duration, plan.

    Regions are the source of truth; cut_segments / keep_segments /
    trimmed_duration are computed from them by running the plan, so they stay
    correct for boundary types that transform footage instead of dropping it.
    """
    orig_dur = prop.get("original_duration", 0)
    plan = eb.build_plan(regions, orig_dur, prop.get("source_path", ""),
                         fps=(prop.get("probe") or {}).get("fps"))
    return {
        "plan": plan,
        "cut_segments": eb.regions_to_cuts(regions),
        "keep_segments": eb.plan_to_segments(plan),
        "trimmed_duration": eb.plan_output_duration(plan),
    }


# ── Verdict state ─────────────────────────────────────────────────────────────

def _review_path(video_id: str) -> Path:
    return REVIEWS_DIR / f"{video_id}.json"


def _get_verdict(video_id: str) -> dict:
    """Return {verdict, reviewed_at} for a video, or empty dict if unreviewed."""
    return _read_json(_review_path(video_id)) or {}


# ── Draft state ───────────────────────────────────────────────────────────────
# A draft is a resume point for an in-progress edit, not a decision — it never
# touches decisions.jsonl or savings.json. Written by the header save icon;
# cleared once export_to_photos supersedes it with a real approval.

def _draft_path(video_id: str) -> Path:
    return DRAFTS_DIR / f"{video_id}.json"


def save_draft(video_id: str, regions: list) -> dict:
    """Persist the in-progress (unapproved) edit state for a video."""
    prop = _read_json(PROPOSALS_DIR / f"{video_id}.json")
    if not prop:
        raise FileNotFoundError(f"no proposal for {video_id}")
    orig_dur = prop.get("original_duration", 0)
    draft = {
        "video_id": video_id,
        "regions": eb.sanitize_regions(regions or [], orig_dur),
        "saved_at": _now_iso(),
    }
    _atomic_write_json(_draft_path(video_id), draft)
    return draft


def _get_draft(video_id: str) -> dict:
    """Return {video_id, regions, saved_at} for a video, or {} if no draft."""
    return _read_json(_draft_path(video_id)) or {}


def _clear_draft(video_id: str) -> None:
    """Delete a video's draft file, if any. Best-effort — never raises."""
    try:
        _draft_path(video_id).unlink()
    except FileNotFoundError:
        pass


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

        # If the video was reviewed WITH edited boundaries, surface those so a
        # reload resumes with the user's edits. Proposals on disk are never
        # mutated. Reviews written before the edit-boundary registry existed
        # carry only cut_segments; they upgrade to cut regions on read.
        proposed_regions = _proposed_regions(prop)
        # A draft ALWAYS wins when present, regardless of prior verdict —
        # re-editing an already-exported video (to tweak before a future
        # re-export) is a normal thing to do, and the draft is by definition
        # the most recent thing the user was actively working on. This stays
        # correct after a real export because export_to_photos clears the
        # draft the moment it succeeds, so the just-exported review's regions
        # naturally take back over until a new draft is saved.
        draft = _get_draft(video_id)
        if draft.get("regions") is not None:
            regions = eb.sanitize_regions(draft["regions"], orig_dur)
        elif review.get("regions") is not None:
            regions = eb.sanitize_regions(review["regions"], orig_dur)
        elif review.get("cut_segments") is not None:
            regions = eb.sanitize_regions(
                eb.regions_from_cuts(review["cut_segments"]), orig_dur)
        else:
            regions = proposed_regions

        derived = _derive(regions, prop)
        cut_segments = derived["cut_segments"]
        keep_segments = derived["keep_segments"]
        trimmed_duration = derived["trimmed_duration"]

        size_bytes = os.path.getsize(source_path) if source_exists else 0

        videos.append({
            "video_id": video_id,
            "apple_uuid": prop.get("apple_uuid", ""),
            "source_name": os.path.basename(source_path) if source_path else video_id,
            "source_exists": source_exists,
            "original_duration": orig_dur,
            "trimmed_duration": trimmed_duration,
            "regions": regions,                        # ← source of truth
            "proposed_regions": proposed_regions,
            "cut_segments": cut_segments,              # ← derived, legacy shape
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
            "exported_at": review.get("exported_at"),
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

# One lock per video_id, so a slow transcode of one clip doesn't block another.
_PROXY_LOCKS: dict[str, threading.Lock] = {}
_PROXY_LOCKS_GUARD = threading.Lock()


def _proxy_lock(video_id: str) -> threading.Lock:
    """The transcode lock for one video, created on first use."""
    with _PROXY_LOCKS_GUARD:
        return _PROXY_LOCKS.setdefault(video_id, threading.Lock())


def _proxy_is_playable(path: Path) -> bool:
    """True if ffmpeg can find a video stream with a duration in *path*.

    Guards the one failure that used to be permanent: publishing a corrupt
    proxy poisons the cache, because the mtime check happily serves it forever.
    Cheap (~50ms) and paid once per transcode, not per request.
    """
    proc = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)],
                          capture_output=True, text=True)
    stderr = proc.stderr   # ffmpeg exits non-zero with no output file; expected.
    return "Video:" in stderr and "Duration: N/A" not in stderr


def source_h264_path(video_id: str) -> Path:
    """Path to a browser-playable (h264/mp4) copy of the source video.

    Real iPhone videos are HEVC .mov, which Chrome won't decode, so we transcode
    the original to h264 once and cache it under preview/. Cheap on repeat views.
    Raises FileNotFoundError if the proposal or its source file is missing.

    SERIALISED PER VIDEO, and that is not optional. The three review panels all
    point at one `/motion-review/source` URL, so opening a video fires three
    simultaneous GETs; on a cache miss each used to launch its own ffmpeg into
    the same temp path. The writers interleaved, one of them "won" and published
    a file with another's bytes inside its moov atom, and the two losers died
    with "Conversion failed!". The published corpse then cached forever (mtime
    check) so the video never played again — reproduced deterministically with
    three concurrent calls. The lock makes the two late callers wait and take
    the finished file; the per-call temp name and the playability check are
    belt-and-braces for anything the lock can't cover (a second process).
    """
    prop = _read_json(PROPOSALS_DIR / f"{video_id}.json")
    if not prop:
        raise FileNotFoundError(f"no proposal for {video_id}")
    source_path = prop.get("source_path", "")
    if not source_path or not os.path.exists(source_path):
        raise FileNotFoundError(f"source video missing for {video_id}: {source_path}")

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = PREVIEW_DIR / f"{video_id}{PREVIEW_SUFFIX}"

    def _cached() -> bool:
        return out.exists() and out.stat().st_mtime >= os.path.getmtime(source_path)

    if _cached():
        return out

    with _proxy_lock(video_id):
        # Re-check inside the lock: while we waited, another request very likely
        # finished the transcode we were about to start.
        if _cached():
            return out
        return _transcode_proxy(video_id, source_path, out)


def _transcode_proxy(video_id: str, source_path: str, out: Path) -> Path:
    """Encode the preview proxy for one video. Callers must hold its lock."""
    tmp = out.with_suffix(f".{uuid4().hex}.tmp.mp4")
    cmd = [
        FFMPEG, "-y", "-i", source_path,
        # iPhone .MOV carries streams this build can't decode (4-channel `apac`
        # spatial audio, `mebx` data tracks) — map explicitly, same as the export.
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", PREVIEW_SCALE,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        # SHORT GOP. The review panels seek constantly — every cut boundary and
        # every scrub is `video.currentTime = x`, and a seek costs the browser a
        # decode from the previous keyframe. x264's default keyint of 250 frames
        # put those ~4.2s apart at 60fps, so each skip stalled for up to 4s of
        # decoded video. At 30 frames they're 0.5s apart: ~8x cheaper seeks, and
        # the file gets SMALLER anyway thanks to the downscale above.
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        "-c:a", "aac",
        "-movflags", "+faststart",   # lets the browser start before full download
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg transcode failed: {proc.stderr[-500:]}")
    if not _proxy_is_playable(tmp):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"transcode produced an unplayable proxy for {video_id} — not caching it"
        )
    os.replace(tmp, out)
    # Drop the proxy an older PREVIEW_SUFFIX left behind — it's pure cache, and
    # nothing will ever ask for it again.
    for old in PREVIEW_LEGACY_SUFFIXES:
        stale = PREVIEW_DIR / f"{video_id}{old}"
        if stale.exists():
            stale.unlink()
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

def record_decision(
    video_id: str,
    verdict: str,
    regions: list | None = None,
    cut_segments: list | None = None,
) -> dict:
    """Record a per-video verdict, optionally with edited edit-boundary regions.

    On approve, if `regions` (or legacy `cut_segments`) is provided the backend
    sanitizes them and recomputes cut_segments / keep_segments /
    trimmed_duration / saved bytes authoritatively — the frontend's numbers are
    only a preview. Writes an append-only audit line (decisions.jsonl) and
    reviews/<video_id>.json (latest state, for resume).
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

    # Edited boundaries only meaningfully apply to an approval ("apply these").
    user_regions = _resolve_regions(prop, regions, cut_segments)
    if verdict == "approve" and user_regions is not None:
        final_regions = user_regions
        edited = not eb.regions_equal(final_regions, _proposed_regions(prop))
    else:
        final_regions = _proposed_regions(prop)
        edited = False

    derived = _derive(final_regions, prop)
    cuts = derived["cut_segments"]
    keeps = derived["keep_segments"]
    trimmed_duration = derived["trimmed_duration"]

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
        "regions": final_regions,
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
        "regions": final_regions,
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


# ── Export to Photos ──────────────────────────────────────────────────────────

def export_to_photos(
    video_id: str,
    regions: list | None = None,
    cut_segments: list | None = None,
) -> dict:
    """Render the kept footage, import it into Photos, reveal it, then record it.

    This is what the green "save" button does — approving a trim and writing it
    out are one action, not two. Ordering is deliberate: the render/import/reveal
    happen FIRST and the ledger is only written once Photos actually has the
    clip, so a failed export leaves no phantom approval behind.

    The original is never deleted or modified; the export is a new asset sitting
    beside it in the timeline, so deleting the original stays a manual decision.
    The savings pool is still credited (via record_decision) because it has always
    tracked the hypothetical "if you delete these originals you'd reclaim X" — it
    is a projection, not a record of bytes actually freed.

    Raises FileNotFoundError (no proposal / missing source) or RuntimeError (the
    ffmpeg render failed). Import/reveal trouble comes back inside the payload.
    """
    prop = _read_json(PROPOSALS_DIR / f"{video_id}.json")
    if not prop:
        raise FileNotFoundError(f"no proposal for {video_id}")

    source_path = prop.get("source_path", "")
    if not source_path or not os.path.exists(source_path):
        raise FileNotFoundError(f"source video missing for {video_id}: {source_path}")

    # Backend is authoritative over the boundaries, same as record_decision.
    final_regions = _resolve_regions(prop, regions, cut_segments)
    if final_regions is None:
        final_regions = _proposed_regions(prop)

    # The plan is what each boundary type said to do with its span; the renderer
    # just executes it, so this stays the same call for every future type.
    plan = eb.build_plan(final_regions, prop.get("original_duration", 0), source_path,
                         fps=(prop.get("probe") or {}).get("fps"))
    if not plan:
        raise ValueError("the cuts cover the whole video — there is nothing to export")

    # 1) Render → import → reveal. Nothing is logged until this succeeds.
    result = export_video.export_and_import(
        source_path, plan, out_name=f"{video_id}_trimmed.mp4"
    )

    # 2) Now record the approval through the normal path (decisions.jsonl,
    #    reviews/<id>.json, savings pool) so an export and an approve stay one
    #    consistent story rather than two competing records.
    review = record_decision(video_id, "approve", regions=final_regions)
    _clear_draft(video_id)  # the export is now the resumable state

    ts = _now_iso()
    imported = result.get("imported") or {}

    # 3) Append the export itself to the same audit log, tagged so it is
    #    distinguishable from a verdict line.
    audit = {
        "ts": ts,
        "action": "export",
        "video_id": video_id,
        "apple_uuid": prop.get("apple_uuid", ""),
        "export_path": result.get("rendered_path"),
        "export_size_bytes": result.get("size_bytes"),
        "source_date": result.get("source_date"),
        "gps": result.get("gps"),
        "photos_item_id": imported.get("item_id"),
        "imported": bool(imported.get("success")),
        "date_set_via_applescript": bool(imported.get("date_set")),
        "location_set_via_applescript": bool(imported.get("location_set")),
        "revealed": bool((result.get("revealed") or {}).get("success")),
    }
    with open(DECISIONS_LOG, "a") as f:
        f.write(json.dumps(audit) + "\n")

    # 4) Fold the export onto the resumable review state.
    review_state = _read_json(_review_path(video_id)) or {}
    review_state.update({
        "exported_at": ts,
        "export_path": result.get("rendered_path"),
        "export_size_bytes": result.get("size_bytes"),
        "photos_item_id": imported.get("item_id"),
    })
    _atomic_write_json(_review_path(video_id), review_state)

    return {**review, **result, "exported_at": ts}
