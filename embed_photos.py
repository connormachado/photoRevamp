"""
Photo Embedding Pipeline
========================
Embeds all photos in a directory using CLIP and stores them in a local ChromaDB.

Install deps first:
    pip install open-clip-torch chromadb Pillow tqdm torch torchvision

Usage:
    python embed_photos.py --photos ~/Pictures --db ./photo_db
    python embed_photos.py --photos ~/Pictures --db ./photo_db --query "sunset at the beach"
    python embed_photos.py --photos ~/Pictures --db ./photo_db --similar /path/to/photo.jpg
"""

import argparse
import os
import json
import hashlib
from pathlib import Path
from datetime import datetime

from PIL import Image
from PIL.ExifTags import TAGS
import torch
import open_clip
import chromadb
from chromadb.config import Settings
from tqdm import tqdm

from pillow_heif import register_heif_opener
register_heif_opener()

# ── Constants ────────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".bmp", ".tiff"}
BATCH_SIZE = 64           # images per GPU/CPU batch — tune down if you OOM
COLLECTION_NAME = "photos"

# ── Model loading ─────────────────────────────────────────────────────────────

def load_model():
    """Load CLIP ViT-B/32 via open_clip. Downloads ~350MB on first run."""
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Using device: {device}")

    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="openai"
    )
    tokenizer = open_clip.get_tokenizer("ViT-B-32")
    model = model.to(device).eval()
    return model, preprocess, tokenizer, device


# ── EXIF helpers ──────────────────────────────────────────────────────────────

def extract_metadata(path: Path) -> dict:
    """Pull date, GPS, and basic info from EXIF where available."""
    meta = {
        "filename": path.name,
        "path": str(path.resolve()),
        "size_kb": round(path.stat().st_size / 1024, 1),
        "date_taken": "",
        "lat": "",
        "lon": "",
    }
    try:
        img = Image.open(path)
        exif_data = img._getexif()
        if exif_data:
            for tag_id, value in exif_data.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag == "DateTimeOriginal":
                    meta["date_taken"] = str(value)
                if tag == "GPSInfo" and isinstance(value, dict):
                    # Decode GPS if present
                    def to_deg(v):
                        d, m, s = v
                        return float(d) + float(m) / 60 + float(s) / 3600
                    try:
                        lat = to_deg(value[2])
                        lon = to_deg(value[4])
                        if value[1] == "S":
                            lat = -lat
                        if value[3] == "W":
                            lon = -lon
                        meta["lat"] = str(round(lat, 6))
                        meta["lon"] = str(round(lon, 6))
                    except Exception:
                        pass
    except Exception:
        pass
    return meta


def file_id(path: Path) -> str:
    """Stable ID: hash of the absolute path string."""
    return hashlib.md5(str(path.resolve()).encode()).hexdigest()


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_images_batch(paths: list[Path], model, preprocess, device) -> list[list[float]]:
    """Return CLIP embeddings for a batch of image paths."""
    images = []
    valid_paths = []
    for p in paths:
        try:
            img = preprocess(Image.open(p).convert("RGB"))
            images.append(img)
            valid_paths.append(p)
        except Exception as e:
            print(f"  ⚠ Skipping {p.name}: {e}")

    if not images:
        return [], []

    batch = torch.stack(images).to(device)
    with torch.no_grad(), torch.amp.autocast(device_type=device if device != "mps" else "cpu"):
        features = model.encode_image(batch)
        features = features / features.norm(dim=-1, keepdim=True)  # L2 normalize

    return features.cpu().float().tolist(), valid_paths


def embed_text(query: str, model, tokenizer, device) -> list[float]:
    """Return CLIP embedding for a text query."""
    tokens = tokenizer([query]).to(device)
    with torch.no_grad():
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.cpu().float().tolist()[0]


# ── Indexing ──────────────────────────────────────────────────────────────────

def index_photos(photos_dir: str, db_path: str):
    """Walk photos_dir, embed everything not yet in the DB, upsert into ChromaDB."""
    photos_root = Path(photos_dir).expanduser()
    all_photos = [
        p for p in photos_root.rglob("*")
        if p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    print(f"Found {len(all_photos):,} photos in {photos_root}")

    # Init ChromaDB (local persistent)
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}   # cosine similarity
    )

    already_indexed = set(collection.get(include=[])["ids"])
    to_index = [p for p in all_photos if file_id(p) not in already_indexed]
    print(f"Already indexed: {len(already_indexed):,} | To index: {len(to_index):,}")

    if not to_index:
        print("Nothing new to index — DB is up to date.")
        return collection

    model, preprocess, _, device = load_model()

    # Process in batches
    for i in tqdm(range(0, len(to_index), BATCH_SIZE), desc="Embedding"):
        batch_paths = to_index[i : i + BATCH_SIZE]
        embeddings, valid_paths = embed_images_batch(batch_paths, model, preprocess, device)

        if not embeddings:
            continue

        ids = [file_id(p) for p in valid_paths]
        metadatas = [extract_metadata(p) for p in valid_paths]

        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=[m["path"] for m in metadatas],  # store path as "document"
        )

    total = collection.count()
    print(f"\n✅ Done. Total photos in DB: {total:,}")
    return collection


# ── Search ────────────────────────────────────────────────────────────────────

def search_by_text(query: str, db_path: str, n_results: int = 12):
    """Search the DB with a natural language query."""
    model, _, tokenizer, device = load_model()
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_collection(COLLECTION_NAME)

    query_vec = embed_text(query, model, tokenizer, device)
    results = collection.query(
        query_embeddings=[query_vec],
        n_results=n_results,
        include=["metadatas", "distances"]
    )

    print(f'\nTop {n_results} results for: "{query}"\n{"─"*50}')
    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        score = round(1 - dist, 3)   # cosine similarity (higher = better)
        print(f"  [{score}]  {meta['path']}")
        if meta.get("date_taken"):
            print(f"         📅 {meta['date_taken']}")
    return results


def search_by_image(image_path: str, db_path: str, n_results: int = 12):
    """Find visually similar photos to a given image."""
    model, preprocess, _, device = load_model()
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_collection(COLLECTION_NAME)

    query_path = Path(image_path).expanduser()
    embeddings, _ = embed_images_batch([query_path], model, preprocess, device)
    if not embeddings:
        print("Could not embed query image.")
        return

    results = collection.query(
        query_embeddings=[embeddings[0]],
        n_results=n_results + 1,   # +1 because the image itself may appear
        include=["metadatas", "distances"]
    )

    print(f'\nTop visually similar photos to: {query_path.name}\n{"─"*50}')
    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        score = round(1 - dist, 3)
        path = meta["path"]
        if path == str(query_path.resolve()):
            continue  # skip the query image itself
        print(f"  [{score}]  {path}")
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Photo embedding pipeline")
    parser.add_argument("--photos", type=str, help="Path to your photo library")
    parser.add_argument("--db",     type=str, default="./photo_db", help="Path to store the ChromaDB")
    parser.add_argument("--query",  type=str, help="Natural language search query")
    parser.add_argument("--similar",type=str, help="Path to an image — find similar photos")
    parser.add_argument("--n",      type=int, default=12, help="Number of results to return")
    args = parser.parse_args()

    if args.query:
        search_by_text(args.query, args.db, args.n)
    elif args.similar:
        search_by_image(args.similar, args.db, args.n)
    elif args.photos:
        index_photos(args.photos, args.db)
    else:
        parser.print_help()