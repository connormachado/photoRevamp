"""
Graph View
==========
Returns semantic-search results enriched with Phase 1 UMAP layout coordinates
and cluster IDs, plus a broad-cluster summary. Framework-agnostic: returns plain
Python data so the route in server.py can wrap it with jsonify().
"""

import json
import urllib.parse

import search
from utils import DEFAULT_DB_PATH


# ── Constants ─────────────────────────────────────────────────────────────────

MAX_RESULTS = 50
CLUSTERS_PATH = DEFAULT_DB_PATH / "models" / "clusters.json"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _thumb_url(path: str) -> str:
    """Build a /thumbnail URL for an absolute photo path."""
    return f"/thumbnail?path={urllib.parse.quote(path, safe='')}"


def _load_clusters() -> dict:
    """Load clusters.json from disk. Returns {} if the file does not exist."""
    try:
        with open(CLUSTERS_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# ── Main function ─────────────────────────────────────────────────────────────

def graph_view(query: str, n, collection, model, tokenizer, device) -> dict:
    """
    Run a semantic search and return results enriched with layout coordinates
    and cluster information.

    Parameters
    ----------
    query     : natural-language search string
    n         : number of results requested (clamped to 1–MAX_RESULTS)
    collection: ChromaDB collection (already open)
    model, tokenizer, device: CLIP model components from load_model()

    Returns
    -------
    {
        "query":    str,
        "count":    int,
        "photos":   [ { id, apple_uuid, path, thumbnail_url, score, date_taken,
                        x, y, cluster_id_broad, cluster_id_fine }, ... ],
        "clusters": [ { cluster_id, representative_id, representative_path,
                        representative_thumbnail_url, size }, ... ]
    }
    """

    # ── 1. Clamp result count ──────────────────────────────────────────────────
    n = min(max(int(n), 1), MAX_RESULTS)

    # ── 2. Embed query and search ChromaDB ────────────────────────────────────
    vec = search.embed_text(query, model, tokenizer, device)
    res = collection.query(
        query_embeddings=[vec],
        n_results=n,
        include=["metadatas", "distances"],
    )

    # ── 3. Build photos list ──────────────────────────────────────────────────
    ids   = res.get("ids", [[]])[0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]

    photos = []
    for id_, meta, dist in zip(ids, metas, dists):
        meta = meta or {}

        # Coerce layout coordinates — may be absent on un-layouted entries
        x = float(meta["x"]) if meta.get("x") not in (None, "") else None
        y = float(meta["y"]) if meta.get("y") not in (None, "") else None

        # Coerce cluster IDs — may be absent or stored as strings
        broad = (
            int(meta["cluster_id_broad"])
            if meta.get("cluster_id_broad") not in (None, "")
            else None
        )
        fine = (
            int(meta["cluster_id_fine"])
            if meta.get("cluster_id_fine") not in (None, "")
            else None
        )

        photos.append({
            "id":               id_,
            "apple_uuid":       meta.get("apple_uuid", ""),
            "path":             meta.get("path", ""),
            "thumbnail_url":    _thumb_url(meta.get("path", "")),
            "score":            round(1 - dist, 4),
            "date_taken":       meta.get("date_taken") or None,
            "x":                x,
            "y":                y,
            "cluster_id_broad": broad,
            "cluster_id_fine":  fine,
        })

    # ── 4. Build broad-cluster summary ────────────────────────────────────────
    raw_clusters = _load_clusters().get("broad", {})
    clusters = []
    for cid, c in sorted(raw_clusters.items(), key=lambda kv: int(kv[0])):
        clusters.append({
            "cluster_id":                  int(cid),
            "representative_id":           c.get("representative_id", ""),
            "representative_path":         c.get("representative_path", ""),
            "representative_thumbnail_url": _thumb_url(c.get("representative_path", "")),
            "size":                        int(c.get("size", 0)),
        })

    # ── 5. Return payload ─────────────────────────────────────────────────────
    return {
        "query":    query,
        "count":    len(photos),
        "photos":   photos,
        "clusters": clusters,
    }
