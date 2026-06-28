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
import cleanup
from utils import load_model, DEFAULT_DB_PATH, COLLECTION_NAME
from pathlib import Path

# Teach PIL to open HEIC/HEIF so thumbnails and full images render in the browser.
pillow_heif.register_heif_opener()

app = Flask(__name__)
CORS(app)

# ── Globals (loaded once at startup) ─────────────────────────────────────────
model = None
preprocess = None
tokenizer = None
device = None
collection = None


def load_everything(db_path: str):
    global model, preprocess, tokenizer, device, collection

    print("Loading CLIP...")
    model, preprocess, tokenizer, device = load_model()

    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_collection(COLLECTION_NAME)
    print(f"DB loaded. {collection.count():,} photos indexed.")


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/stats")
def stats():
    return jsonify({"total": collection.count()})


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


@app.route("/open-in-photos", methods=["POST"])
def open_in_photos():
    """Reveal a photo in Apple Photos.app so the user can review/delete it."""
    data = request.json or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"success": False, "error": "no path"}), 400
    return jsonify(cleanup.open_in_photos(path))


@app.route("/cleanup", methods=["POST"])
def cleanup_missing():
    """Prune ChromaDB entries whose files have been deleted from disk."""
    result = cleanup.remove_missing_photos(collection)
    return jsonify(result)


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

    app.run(port=args.port, debug=False)
