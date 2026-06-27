"""
Shared helpers
==============
Functions used across the backend: model loading, EXIF metadata extraction,
and stable photo IDs. Keep logic here identical to the original so existing
ChromaDB IDs and embeddings stay valid.
"""

import hashlib
from pathlib import Path

from PIL import Image
from PIL.ExifTags import TAGS
import torch
import open_clip

# ── Paths ─────────────────────────────────────────────────────────────────────
# Anchor everything to the repo root (one level above backend/) so paths work no
# matter which directory the app is launched from.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "photo_db"

COLLECTION_NAME = "photos"


# ── Model loading ─────────────────────────────────────────────────────────────

def load_model():
    """Load CLIP ViT-B/32 via open_clip. Downloads ~350MB on first run.

    Returns (model, preprocess, tokenizer, device). Uses the MPS torch device on
    Apple Silicon, falling back to CUDA or CPU.
    """
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
    """Stable ID: MD5 hash of the absolute path string.

    Keeps indexing incremental and resumable — must not change or existing
    ChromaDB IDs will no longer match.
    """
    return hashlib.md5(str(path.resolve()).encode()).hexdigest()
