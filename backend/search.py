"""
Search logic
============
Text and image search against the ChromaDB photo collection, plus the
embed_text / embed_image helpers they rely on. Framework-agnostic: these
functions return plain Python data so the routes in server.py can wrap them.
"""

import torch


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_text(query: str, model, tokenizer, device) -> list[float]:
    """Return the L2-normalized CLIP embedding for a text query."""
    tokens = tokenizer([query]).to(device)
    with torch.no_grad():
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.cpu().float().tolist()[0]


def embed_image(img, model, preprocess, device) -> list[float]:
    """Return the L2-normalized CLIP embedding for a PIL image (RGB)."""
    tensor = preprocess(img).unsqueeze(0).to(device)
    with torch.no_grad():
        features = model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.cpu().float().tolist()[0]


# ── Search ────────────────────────────────────────────────────────────────────

def search_text(query, n, collection, model, tokenizer, device) -> list[dict]:
    """Natural-language search. Returns a list of formatted result dicts."""
    vec = embed_text(query, model, tokenizer, device)
    results = collection.query(
        query_embeddings=[vec],
        n_results=n,
        include=["metadatas", "distances"],
    )
    return format_results(results)


def search_image(img, n, collection, model, preprocess, device) -> list[dict]:
    """Visual similarity search for a PIL image. Returns formatted result dicts."""
    vec = embed_image(img, model, preprocess, device)
    results = collection.query(
        query_embeddings=[vec],
        n_results=n,
        include=["metadatas", "distances"],
    )
    return format_results(results)


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_results(results) -> list[dict]:
    """Flatten a ChromaDB query response into the shape the frontend expects."""
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
    return out
