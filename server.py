"""
Photo Search API Server
=======================
Thin Flask wrapper so the React UI can talk to your local ChromaDB.

Install:
    pip install flask flask-cors open-clip-torch chromadb Pillow torch

Run (after you've already indexed your photos):
    python server.py --db ./photo_db

Then open the React UI at localhost:5173 (or wherever Vite serves it).
"""

import argparse
import base64
import io
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from PIL import Image
import torch
import open_clip
import chromadb

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

    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Loading CLIP on {device}...")
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
    tokenizer = open_clip.get_tokenizer("ViT-B-32")
    model = model.to(device).eval()

    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_collection("photos")
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

    tokens = tokenizer([query]).to(device)
    with torch.no_grad():
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)
    vec = features.cpu().float().tolist()[0]

    results = collection.query(
        query_embeddings=[vec],
        n_results=n,
        include=["metadatas", "distances"]
    )
    return _format_results(results)


@app.route("/search/image", methods=["POST"])
def search_image():
    """Accepts a base64-encoded image, finds visually similar photos."""
    data = request.json
    b64 = data.get("image_b64", "")
    n = int(data.get("n", 24))
    if not b64:
        return jsonify({"error": "no image"}), 400

    image_data = base64.b64decode(b64)
    img = Image.open(io.BytesIO(image_data)).convert("RGB")
    tensor = preprocess(img).unsqueeze(0).to(device)

    with torch.no_grad():
        features = model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
    vec = features.cpu().float().tolist()[0]

    results = collection.query(
        query_embeddings=[vec],
        n_results=n,
        include=["metadatas", "distances"]
    )
    return _format_results(results)


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
    """Serves the original full-res image."""
    path = request.args.get("path", "")
    p = Path(path)
    if not p.exists():
        return jsonify({"error": "file not found"}), 404
    return send_file(str(p))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_results(results):
    out = []
    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        out.append({
            "path": meta.get("path", ""),
            "filename": meta.get("filename", ""),
            "score": round(1 - dist, 4),
            "date_taken": meta.get("date_taken", ""),
            "lat": meta.get("lat", ""),
            "lon": meta.get("lon", ""),
            "size_kb": meta.get("size_kb", ""),
        })
    return jsonify({"results": out})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default="./photo_db")
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()

    load_everything(args.db)
    app.run(port=args.port, debug=False)