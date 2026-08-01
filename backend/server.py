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
from utils import load_model, DEFAULT_DB_PATH, COLLECTION_NAME
from pathlib import Path

# Teach PIL to open HEIC/HEIF so thumbnails and full images render in the browser.
pillow_heif.register_heif_opener()

app = Flask(__name__)
CORS(app)

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


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/stats")
def stats():
    # Merge the indexed-photo count (used by the header) with the persisted
    # delete counter so the UI can read both from one payload.
    return jsonify({"total": collection.count(), **stats_store.get_stats()})


@app.route("/stats/increment", methods=["POST"])
def stats_increment():
    """Bump the delete counter by {"delta": +1 | -1} and return updated stats."""
    data = request.get_json() or {}
    delta = int(data.get("delta", 0))
    return jsonify(stats_store.update_stats(delta))


@app.route("/search/text", methods=["POST"])
def search_text():
    data = request.json
    query = data.get("query", "").strip()
    n = int(data.get("n", 24))
    if not query:
        return jsonify({"error": "empty query"}), 400

    results = search.search_text(query, n, collection, model, tokenizer, device)
    return jsonify({"results": results})


@app.route("/search/image", methods=["POST"])
def search_image():
    """Accepts a base64-encoded image, finds visually similar photos."""
    data = request.json
    b64 = data.get("image_b64", "")
    n = int(data.get("n", 24))
    if not b64:
        return jsonify({"error": "no image"}), 400

    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    results = search.search_image(img, n, collection, model, preprocess, device)
    return jsonify({"results": results})


@app.route("/api/graph-view")
def graph_view_route():
    query = request.args.get("query", "").strip()
    n = int(request.args.get("n", 50))
    if not query:
        return jsonify({"error": "empty query"}), 400
    payload = graph_view.graph_view(query, n, collection, model, tokenizer, device)
    return jsonify(payload)


@app.route("/thumbnail")
def thumbnail():
    """Serves a resized thumbnail for a given photo path."""
    path = request.args.get("path", "")
    size = int(request.args.get("size", 300))
    p = Path(path)
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
    path = request.args.get("path", "")
    p = Path(path)
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
    data = request.get_json() or {}
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
    return jsonify({"success": True})


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
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    return send_file(str(path), mimetype="video/mp4")


@app.route("/motion-review/timelapse")
def motion_review_timelapse():
    """Serve the baked timelapse-of-removed-sections mp4 (fallback preview)."""
    video_id = request.args.get("id", "")
    path = motion_review.timelapse_path(video_id)
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
    data = request.get_json() or {}
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
    data = request.get_json() or {}
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
    data = request.get_json() or {}
    video_id = data.get("video_id", "")
    regions = data.get("regions")
    if not video_id:
        return jsonify({"error": "no video_id provided"}), 400
    try:
        draft = motion_review.save_draft(video_id, regions or [])
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

    app.run(port=args.port, debug=True)
