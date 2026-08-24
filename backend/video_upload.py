"""
Video upload → Climb Cutter queue
==================================
Accepts a video uploaded from the browser file picker, parks it on disk, and
runs it through the same dead-time analysis the CLI uses, so it shows up in the
review queue.

Why this exists: there is no other HTTP ingest path. `GET /motion-review/queue`
just lists `proposals/*.json`, and only `video_motion.process_video` writes
those. So "enqueue" here means *analyse*, and that analysis — not the byte
transfer — is what the caller waits on (~0.35x realtime, so ~20s per minute of
1080p60 footage). The route is deliberately synchronous; the UI shows a busy
state rather than polling a job.

The browser hands over bytes, never a path, so every upload is a full second
copy of a file that usually already lives in the Photos library. Uploads are
therefore keyed by CONTENT hash (see `_settle_path`): re-picking the same clip
lands on the same path, which means the same `video_id`, which means we reuse
the existing proposal instead of re-analysing and instead of adding a duplicate
queue row.

Plain functions only (no Flask); server.py wraps this in a route.
"""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

from werkzeug.utils import secure_filename

import export_video
import video_motion
from utils import file_id, DEFAULT_DB_PATH

# ── Paths / constants ─────────────────────────────────────────────────────────

MOTION_DIR = DEFAULT_DB_PATH / "motion_review"
UPLOADS_DIR = MOTION_DIR / "uploads"
INCOMING_DIR = UPLOADS_DIR / ".incoming"     # staging; hidden from the hash dirs
PROPOSALS_DIR = MOTION_DIR / "proposals"

# The <input accept="video/*"> attribute is only a hint — the picker can still
# hand back anything, so the real gate is here.
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}

HASH_CHUNK = 1 << 20     # 1 MiB
HASH_PREFIX_LEN = 16     # 64 bits of the content hash is plenty for one library


# ── Helpers ───────────────────────────────────────────────────────────────────

def _content_hash(path: Path) -> str:
    """MD5 of the file's BYTES, truncated — the dedupe key for uploads.

    Deliberately not `utils.file_id`, which hashes the *path*: we need to know
    "is this the same clip?" before we have decided where to put it.
    """
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()[:HASH_PREFIX_LEN]


APFS_NAME_MAX = 255      # bytes per path component (macOS/APFS)


def _safe_name(filename: str) -> str:
    """A filesystem-safe basename, preserving the extension where possible.

    `secure_filename` can legitimately return "" (e.g. a name that is entirely
    non-ASCII), which would otherwise produce a directory-as-destination bug.

    The extension is derived from the ORIGINAL name and reattached after the
    stem is sanitised, independently of whatever `secure_filename` does to the
    stem — sanitising the full "stem.ext" string in one pass let a stem that
    transliterates to nothing (e.g. "日本語") strip the separating dot along
    with it, leaving a bare extension like "mp4" with no dot and no stem.
    Length is capped in UTF-8 bytes (APFS's NAME_MAX is a byte limit, not a
    character one) by truncating the stem only, never the extension.
    """
    filename = filename or ""
    raw_ext = Path(filename).suffix
    if raw_ext.lower() not in VIDEO_EXTS:
        ext = ".mov"
    elif raw_ext.isascii():
        ext = raw_ext                # same characters, different case: keep it, e.g. ".MOV"
    else:
        # str.lower() folds some non-ASCII codepoints onto ASCII letters (e.g.
        # U+212A KELVIN SIGN -> "k"), so a name can pass the VIDEO_EXTS check
        # without its extension actually BEING ASCII. Emit the canonical form
        # that passed the check, not the confusable original — otherwise this
        # file's extension is a different codepoint from every real ".mkv".
        ext = raw_ext.lower()

    stem = secure_filename(Path(filename).stem)
    if not stem:
        stem = "video"

    max_stem_bytes = APFS_NAME_MAX - len(ext.encode("utf-8"))
    stem_bytes = stem.encode("utf-8")
    if len(stem_bytes) > max_stem_bytes:
        stem = stem_bytes[:max_stem_bytes].decode("utf-8", "ignore")

    return f"{stem}{ext}"


def _settle_path(staged: Path, filename: str) -> Path:
    """Move *staged* to its permanent home, `uploads/<content-hash>/<name>`.

    The hash directory keeps two different `IMG_1234.MOV`s apart while letting
    the original basename survive as the queue's `source_name`. Because the
    destination is content-derived it is also stable across re-uploads, which is
    what makes dedupe work — and it must be final BEFORE analysis runs, since
    `video_id` is md5-of-absolute-path and moving the file later would orphan
    the proposal, review, draft and preview proxy.

    If the directory is already occupied we reuse whatever is in it and throw
    the new copy away, EVEN IF the upload came in under a different name. The
    name is not what identifies a clip here; matching on it would put a second
    few-hundred-MB copy of identical footage on disk and a duplicate row in the
    queue, just because the file got renamed between picks.
    """
    dest_dir = UPLOADS_DIR / _content_hash(staged)
    dest_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(p for p in dest_dir.iterdir() if p.is_file())
    if existing:
        staged.unlink(missing_ok=True)
        return existing[0]

    dest = dest_dir / _safe_name(filename)
    os.replace(staged, dest)             # same filesystem → atomic
    return dest


# ── Entry point ───────────────────────────────────────────────────────────────

def save_and_process(file_storage) -> dict:
    """Persist one uploaded video and analyse it into the review queue.

    Returns {filename, video_id, status, has_date, has_gps, error} where status
    is one of "queued" | "already_queued" | "error". Never raises: the route
    processes a whole selection and one bad file must not sink the rest.
    """
    filename = file_storage.filename or ""
    result = {
        "filename": filename,
        "video_id": None,
        "status": "error",
        "has_date": False,
        "has_gps": False,
        "error": None,
    }

    ext = Path(filename).suffix.lower()
    if ext not in VIDEO_EXTS:
        result["error"] = (
            f"Not a video file ({ext or 'no extension'}). "
            f"Accepted: {', '.join(sorted(VIDEO_EXTS))}"
        )
        return result

    staged = INCOMING_DIR / f"{uuid4().hex}.tmp"
    try:
        INCOMING_DIR.mkdir(parents=True, exist_ok=True)
        # Werkzeug has already streamed the part to a spooled temp file, so this
        # is a copy between files, not a buffer in memory.
        file_storage.save(str(staged))

        dest = _settle_path(staged, filename)
        video_id = file_id(dest)
        result["video_id"] = video_id

        # Date/GPS come from the FILE at export time, never from Photos — so a
        # copy handed over by the macOS picker with its QuickTime tags stripped
        # would export undated and unlocated, silently. Surface it here instead.
        meta = export_video.read_source_metadata(dest)
        result["has_date"] = bool(meta.get("date"))
        result["has_gps"] = bool(meta.get("gps"))

        if (PROPOSALS_DIR / f"{video_id}.json").exists():
            result["status"] = "already_queued"
            return result

        # owned=True: `dest` is a copy WE made under uploads/, so removing the
        # queue entry is allowed to delete it. Nothing else sets this.
        video_motion.process_video(str(dest), video_motion.load_config(), owned=True)
        result["status"] = "queued"
        return result

    except Exception as exc:
        result["error"] = str(exc)
        return result
    finally:
        staged.unlink(missing_ok=True)   # no-op once os.replace has moved it
