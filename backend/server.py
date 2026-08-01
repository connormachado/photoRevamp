"""
Photo Search API Server
=======================
Thin Flask wrapper so the React UI can talk to your local ChromaDB.
Routes only — all logic lives in search.py / utils.py.

Install:
    pip install flask flask-cors open-clip-torch chromadb Pillow torch

Run (after you've already indexed your photos):
    python backend/server.py --db ./photo_db

Then open the React UI at localhost:5173 (or wherever Vite serves it).
"""

import argparse
import base64
import io
import os

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from PIL import Image
import pillow_heif
import chromadb

import search
import graph_view
import cleanup
import motion_review
import video_upload
import embed_job
import stats as stats_store
import safe_paths
from utils import load_model, DEFAULT_DB_PATH, COLLECTION_NAME
from pathlib import Path

# Teach PIL to open HEIC/HEIF so thumbnails and full images render in the browser.
pillow_heif.register_heif_opener()

app = Flask(__name__)

# CORS is scoped to the Vite dev server rather than left wide open. A bare
# `CORS(app)` sends `Access-Control-Allow-Origin: *` on every route, which — with
# no authentication anywhere in this app — means any page the user happens to be
# browsing can call these routes and READ the response. Binding to 127.0.0.1 does
# not help: the browser is already inside that boundary.
# PHOTO_MEMORY_ORIGINS (comma-separated) overrides it if the frontend ever moves.
_origins = os.environ.get("PHOTO_MEMORY_ORIGINS", "").strip()
CORS(app, origins=(
    [o.strip() for o in _origins.split(",") if o.strip()] if _origins
    else ["http://localhost:5173", "http://127.0.0.1:5173"]
))

# Climb Cutter uploads are whole climbing videos — hundreds of MB is normal.
# Flask 3.1 already defaults MAX_CONTENT_LENGTH to None (unlimited), so this is
# recording the intent rather than lifting a limit; it also caps a runaway
# upload, which matters because these land on an already-full volume.
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 ** 3   # 8 GiB

# ── Globals (loaded once at startup) ─────────────────────────────────────────
model = None
preprocess = None
tokenizer = None
device = None
collection = None
db_path_global = None


def load_everything(db_path: str):
    global model, preprocess, tokenizer, device, db_path_global

    print("Loading CLIP...")
    model, preprocess, tokenizer, device = load_model()

    db_path_global = db_path
    reload_collection()
    print(f"DB loaded. {collection.count():,} photos indexed.")


def reload_collection():
    """Re-open the ChromaDB collection from disk.

    Chroma loads its HNSW index into memory per process. When the embed
    subprocess adds new vectors, this process keeps serving the index it read at
    startup — so new photos are invisible to search and the header count until
    we reopen. Called once after each completed embed run.
    """
    global collection
    client = chromadb.PersistentClient(path=db_path_global)
    collection = client.get_collection(COLLECTION_NAME)


# ── Request parsing ───────────────────────────────────────────────────────────
# Coercing query strings and JSON bodies is a routing-layer concern, so these
# stay here rather than becoming a module. Both exist because the routes used to
# call `int(...)` and `.get(...)` on unvalidated input: a non-numeric `?n=`, or a
# body that parsed as a bare JSON string, raised straight out of the view and
# returned a 500 (with a full traceback attached, when debug was on).

def _json_body() -> dict:
    """The request's JSON body as a dict, or {} for anything else.

    `request.get_json()` returns whatever parsed — a str for `"hello"`, a list
    for `[1,2]` — and the routes then call `.get` on it. Anything that is not an
    object is treated as no body at all, so the routes' own required-field checks
    produce the 400.
    """
    try:
        data = request.get_json(silent=True)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _int_param(name: str, default: int, minimum: int | None = None,
               maximum: int | None = None, source=None):
    """A query-string integer, clamped. Returns None when it isn't a number.

    `maximum` is not cosmetic on `n`: it flows into `collection.query(n_results=)`,
    and an unbounded value is a trivial way to make the server chew through the
    whole index.
    """
    raw = (source if source is not None else request.args).get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


MAX_RESULTS = 500


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/stats")
def stats():
    # Merge the indexed-photo count (used by the header) with the persisted
    # delete counter so the UI can read both from one payload. `avg_photo_bytes`
    # rides along so the UI can name the estimate without keeping its own copy
    # of the constant — it's config, not state, so it is NOT persisted.
    return jsonify({
        "total": collection.count(),
        "avg_photo_bytes": stats_store.AVG_PHOTO_BYTES,
        **stats_store.get_stats(),
    })


@app.route("/stats/increment", methods=["POST"])
def stats_increment():
    """Bump the delete counter by {"delta": +1 | -1} and return updated stats.

    Optional "exact_bytes" credits the photo's real size to the reclaimed total
    instead of the per-photo average (see stats.AVG_PHOTO_BYTES).
    """
    data = _json_body()
    delta = _int_param("delta", 0, source=data)
    if delta is None:
        return jsonify({"error": "delta must be a number"}), 400
    # A malformed size must not cost the caller their count bump — fall back to
    # the average rather than 500ing the whole write.
    try:
        exact_bytes = int(data.get("exact_bytes") or 0)
    except (TypeError, ValueError):
        exact_bytes = 0
    return jsonify(stats_store.update_stats(delta, exact_bytes))


@app.route("/search/text", methods=["POST"])
def search_text():
    data = _json_body()
    n = _int_param("n", 24, minimum=1, maximum=MAX_RESULTS, source=data)
    if n is None:
        return jsonify({"error": "n must be a number"}), 400
    query = str(data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "empty query"}), 400

    results = search.search_text(query, n, collection, model, tokenizer, device)
    return jsonify({"results": results})


@app.route("/search/image", methods=["POST"])
def search_image():
    """Accepts a base64-encoded image, finds visually similar photos."""
    data = _json_body()
    n = _int_param("n", 24, minimum=1, maximum=MAX_RESULTS, source=data)
    if n is None:
        return jsonify({"error": "n must be a number"}), 400
    b64 = data.get("image_b64") or ""
    if not b64 or not isinstance(b64, str):
        return jsonify({"error": "no image"}), 400

    # A dropped file that isn't a decodable image is user error, not a server
    # fault — including PIL's decompression-bomb guard, which fires on a small
    # payload that would expand to gigabytes of pixels.
    try:
        img = Image.open(io.BytesIO(base64.b64decode(b64, validate=True))).convert("RGB")
    except Exception:
        return jsonify({"error": "could not decode image"}), 400

    results = search.search_image(img, n, collection, model, preprocess, device)
    return jsonify({"results": results})


@app.route("/api/graph-view")
def graph_view_route():
    query = request.args.get("query", "").strip()
    n = _int_param("n", 50, minimum=1, maximum=MAX_RESULTS)
    if n is None:
        return jsonify({"error": "n must be a number"}), 400
    if not query:
        return jsonify({"error": "empty query"}), 400
    payload = graph_view.graph_view(query, n, collection, model, tokenizer, device)
    return jsonify(payload)


@app.route("/thumbnail")
def thumbnail():
    """Serves a resized thumbnail for a given photo path."""
    size = _int_param("size", 300, minimum=1, maximum=4096)
    if size is None:
        return jsonify({"error": "size must be a number"}), 400
    try:
        p = safe_paths.resolve_within_roots(request.args.get("path", ""))
    except safe_paths.UnsafePathError as e:
        return jsonify({"error": str(e)}), 403
    if not p.exists():
        return jsonify({"error": "file not found"}), 404

    img = Image.open(p).convert("RGB")
    img.thumbnail((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    buf.seek(0)
    return send_file(buf, mimetype="image/jpeg")


@app.route("/full")
def full_image():
    """Serves the original full-res image.

    HEIC is converted to JPEG in memory because Chrome refuses to render HEIC
    natively (ERR_BLOCKED_BY_ORB). Everything else is sent as-is.
    """
    try:
        p = safe_paths.resolve_within_roots(request.args.get("path", ""))
    except safe_paths.UnsafePathError as e:
        return jsonify({"error": str(e)}), 403
    if not p.exists():
        return jsonify({"error": "file not found"}), 404

    if p.suffix.lower() == ".heic":
        img = Image.open(p).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        buf.seek(0)
        return send_file(buf, mimetype="image/jpeg")

    return send_file(str(p))


@app.route("/reveal", methods=["POST"])
def reveal_in_photos():
    """Spotlight a photo in Apple Photos.app via its stored Apple asset UUID."""
    data = _json_body()
    file_id = data.get("id")
    if not file_id:
        return jsonify({"error": "No id provided"}), 400

    result = collection.get(ids=[file_id], include=["metadatas"])
    if not result["ids"]:
        return jsonify({"error": "Photo not found"}), 404

    uuid = (result["metadatas"][0] or {}).get("apple_uuid", "")
    if not uuid:
        return jsonify({"error": "No Apple UUID found for this photo"}), 400

    res = cleanup.reveal_in_photos(uuid)
    if not res.get("success"):
        return jsonify({"error": res.get("error", "osascript failed")}), 500
    # Revealing is the app's only "about to delete" signal, and this is the one
    # moment the original's real size is available — hand it back so the client
    # can credit exact bytes rather than the average. 0 if Photos wouldn't say.
    return jsonify({"success": True, "size_bytes": cleanup.photo_size_bytes(uuid)})


# ── Library indexing (in-app embed trigger) ──────────────────────────────────

# Tracks which run we've already reloaded the collection for, so a run's status
# is only acted on once no matter how many times the UI polls.
_embed_reloaded_for = None


@app.route("/api/embed/start", methods=["POST"])
def embed_start():
    """Launch an incremental catch-up index in the background."""
    result = embed_job.start_job()
    if not result["started"]:
        code = 409 if result["reason_code"] == "already_running" else 400
        return jsonify(result), code
    return jsonify(result)


@app.route("/api/embed/status")
def embed_status():
    """Current embed job status — polled by the UI every couple of seconds."""
    global _embed_reloaded_for
    status = embed_job.read_status()

    if status["state"] == "done" and status["started_at"] != _embed_reloaded_for:
        _embed_reloaded_for = status["started_at"]
        reload_collection()
        status = {**status, "total_in_db": collection.count()}

    return jsonify(status)


@app.route("/cleanup", methods=["POST"])
def cleanup_missing():
    """Prune ChromaDB entries whose files have been deleted from disk."""
    result = cleanup.remove_missing_photos(collection)
    return jsonify(result)


# ── Climb Cutter: motion review room (Phase 2) ───────────────────────────────

@app.route("/motion-review/queue")
def motion_review_queue():
    """List videos processed by video_motion.py that are awaiting/have review."""
    return jsonify({"videos": motion_review.list_queue()})


@app.route("/motion-review/source")
def motion_review_source():
    """Serve a browser-playable (h264) copy of the original source video.

    send_file honors HTTP Range by default (conditional=True), which the
    <video> element needs for seeking.
    """
    video_id = request.args.get("id", "")
    if not video_id:
        return jsonify({"error": "no id provided"}), 400
    try:
        path = motion_review.source_h264_path(video_id)
    except ValueError as e:          # includes safe_paths.UnsafePathError
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    return send_file(str(path), mimetype="video/mp4")


@app.route("/motion-review/timelapse")
def motion_review_timelapse():
    """Serve the baked timelapse-of-removed-sections mp4 (fallback preview)."""
    video_id = request.args.get("id", "")
    try:
        path = motion_review.timelapse_path(video_id)
    except ValueError as e:          # includes safe_paths.UnsafePathError
        return jsonify({"error": str(e)}), 400
    if not path:
        return jsonify({"error": "no timelapse for this video"}), 404
    return send_file(str(path), mimetype="video/mp4")


@app.route("/motion-review/savings")
def motion_review_savings():
    """Return the running pool of reclaimed data across approved cuts."""
    return jsonify(motion_review.get_savings())


@app.route("/motion-review/decision", methods=["POST"])
def motion_review_decision():
    """Record a per-video verdict {"video_id", "verdict": "reject"|"approve"}."""
    data = _json_body()
    video_id = data.get("video_id", "")
    verdict = data.get("verdict", "")
    regions = data.get("regions")            # optional edited boundaries
    cut_segments = data.get("cut_segments")  # legacy shape, still accepted
    if not video_id:
        return jsonify({"error": "no video_id provided"}), 400
    try:
        record = motion_review.record_decision(video_id, verdict, regions, cut_segments)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(record)


@app.route("/motion-review/export", methods=["POST"])
def motion_review_export():
    """Render the kept footage, import it into Photos, reveal it, and log it.

    This is the green save button: approving a trim and writing it out are one
    action. The original is never touched — the export lands beside it.
    """
    data = _json_body()
    video_id = data.get("video_id", "")
    regions = data.get("regions")            # optional edited boundaries
    cut_segments = data.get("cut_segments")  # legacy shape, still accepted
    if not video_id:
        return jsonify({"error": "no video_id provided"}), 400
    try:
        record = motion_review.export_to_photos(video_id, regions, cut_segments)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(record)


@app.route("/motion-review/draft", methods=["POST"])
def motion_review_draft():
    """Persist in-progress edit state {"video_id", "regions"} as a resumable
    draft — no ledger write, no audit log entry. This is the header save icon."""
    data = _json_body()
    video_id = data.get("video_id", "")
    regions = data.get("regions")
    if not video_id:
        return jsonify({"error": "no video_id provided"}), 400
    try:
        draft = motion_review.save_draft(video_id, regions or [])
    except ValueError as e:          # includes safe_paths.UnsafePathError
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(draft)


@app.route("/motion-review/upload", methods=["POST"])
def motion_review_upload():
    """Ingest videos picked in the browser: save the bytes, then analyse each
    one into the queue. Synchronous — analysis runs ~0.35x realtime, so a long
    clip holds the request open while the UI shows a busy state."""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files provided"}), 400
    results = [video_upload.save_and_process(f) for f in files]
    return jsonify({
        "queued": sum(r["status"] == "queued" for r in results),
        "results": results,
    })


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH))
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()

    load_everything(args.db)

    # Prune entries for photos deleted off disk so they stop polluting search.
    startup_result = cleanup.remove_missing_photos(collection)
    print(f"[startup] Cleanup: removed {startup_result['removed']} missing photos out of {startup_result['checked']} checked")

    # debug=True enables the Werkzeug interactive debugger and sends full
    # tracebacks — absolute paths, source lines, local variables — to every
    # caller, cross-origin. Opt in with PHOTO_MEMORY_DEBUG=1 while developing;
    # it must not be the default for anything shared.
    app.run(port=args.port, debug=os.environ.get("PHOTO_MEMORY_DEBUG") == "1")
