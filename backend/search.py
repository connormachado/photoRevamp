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

# Upper bound on how far a dismissed-ids over-fetch can push n_results. Chroma
# clamps to collection size on its own; this just guards a pathological ledger
# (thousands of dismissals in one category) from asking for an absurd n.
OVERFETCH_CAP = 2000


def search_text(query, n, collection, model, tokenizer, device,
                 exclude_ids: set[str] | None = None) -> list[dict]:
    """Natural-language search. Returns a list of formatted result dicts.

    `exclude_ids`, when given, over-fetches by exactly its size (capped) and
    filters afterward — the minimum over-fetch that still guarantees a full
    page, since every excluded id can displace at most one slot.
    """
    vec = embed_text(query, model, tokenizer, device)
    fetch_n = n if not exclude_ids else min(n + len(exclude_ids), OVERFETCH_CAP)
    results = collection.query(
        query_embeddings=[vec],
        n_results=fetch_n,
        include=["metadatas", "distances"],
    )
    formatted = format_results(results)
    if exclude_ids:
        formatted = [r for r in formatted if r["id"] not in exclude_ids]
    return formatted[:n]


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
    ids = results.get("ids", [[]])[0]
    for id_, meta, dist in zip(ids, results["metadatas"][0], results["distances"][0]):
        out.append({
            "id": id_,  # ChromaDB file_id (MD5 of path) — used by /reveal
            "path": meta.get("path", ""),
            "filename": meta.get("filename", ""),
            "score": round(1 - dist, 4),
            "date_taken": meta.get("date_taken", ""),
            "lat": meta.get("lat", ""),
            "lon": meta.get("lon", ""),
            "size_kb": meta.get("size_kb", ""),
            "apple_uuid": meta.get("apple_uuid", ""),  # debug/traceability only
        })
    return out
