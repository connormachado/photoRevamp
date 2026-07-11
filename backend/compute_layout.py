"""
UMAP Layout + Agglomerative Cluster Precomputation
===================================================
Reads all CLIP embeddings from ChromaDB, fits a 2-D UMAP projection, assigns
every photo to both a broad and a fine Agglomerative cluster, and writes the
x/y/cluster_id fields back into ChromaDB metadata.

Saved artefacts (under photo_db/models/):
    umap.joblib          — serialised UMAP reducer (for incremental .transform())
    layout_meta.json     — fit timestamp, photo count, hyper-params
    clusters.json        — centroid + representative for each broad/fine cluster

Usage
-----
Full fit (first run, or forced refit):
    python compute_layout.py
    python compute_layout.py --full-refit

Incremental (project new photos onto existing UMAP, assign nearest cluster):
    python compute_layout.py          # auto-selects incremental if model exists
"""

import argparse
import json
import numpy as np
from datetime import datetime
from pathlib import Path

import chromadb
import joblib
import umap
from sklearn.cluster import AgglomerativeClustering

from utils import COLLECTION_NAME, DEFAULT_DB_PATH

# ── Constants ─────────────────────────────────────────────────────────────────

MODELS_DIR       = DEFAULT_DB_PATH / "models"
UMAP_MODEL_PATH  = MODELS_DIR / "umap.joblib"
LAYOUT_META_PATH = MODELS_DIR / "layout_meta.json"
CLUSTERS_PATH    = MODELS_DIR / "clusters.json"

CHUNK_SIZE = 5000   # stay well under SQLite's variable limit

BROAD_K = 12        # coarse clusters shown in the map overview
FINE_K  = 60        # fine clusters used for neighbour browsing


# ── ChromaDB helpers ──────────────────────────────────────────────────────────

def get_collection(db_path: str):
    """Mirror embed_photos.py client init — get or create with cosine space."""
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


def read_all(collection, limit=None):
    """Chunked pagination over the whole collection.

    Returns
    -------
    ids        : list[str]
    embeddings : np.ndarray, shape (N, D), dtype float32
    metadatas  : list[dict]

    If `limit` is set, stops after collecting that many records.
    """
    all_ids        = []
    all_embeddings = []
    all_metadatas  = []
    offset         = 0

    while True:
        remaining = None
        if limit is not None:
            remaining = limit - len(all_ids)
            if remaining <= 0:
                break

        fetch = min(CHUNK_SIZE, remaining) if remaining is not None else CHUNK_SIZE
        batch = collection.get(
            include=["embeddings", "metadatas"],
            limit=fetch,
            offset=offset,
        )

        if not batch["ids"]:
            break

        all_ids.extend(batch["ids"])
        all_metadatas.extend(batch["metadatas"])

        # ChromaDB 1.5.x may return embeddings as ndarray or list — normalise both
        embs = batch["embeddings"]
        if len(embs) == 0:
            break
        all_embeddings.append(np.asarray(embs, dtype=np.float32))

        offset += len(batch["ids"])

    if not all_embeddings:
        return [], np.empty((0, 0), dtype=np.float32), []

    return all_ids, np.concatenate(all_embeddings, axis=0), all_metadatas


# ── Write-back helper ─────────────────────────────────────────────────────────

def write_back(collection, ids, metadatas, coords, labels_broad, labels_fine):
    """Merge x/y/cluster fields into existing metadata and update ChromaDB.

    Chunks all writes to <= CHUNK_SIZE to respect SQLite variable limits.
    Existing metadata fields are preserved via {**meta, ...} spread.
    """
    n = len(ids)
    for start in range(0, n, CHUNK_SIZE):
        end = min(start + CHUNK_SIZE, n)

        chunk_ids   = ids[start:end]
        chunk_metas = []
        for i in range(start, end):
            meta = metadatas[i]
            chunk_metas.append({
                **meta,
                "x":               float(coords[i, 0]),
                "y":               float(coords[i, 1]),
                "cluster_id_broad": int(labels_broad[i]),
                "cluster_id_fine":  int(labels_fine[i]),
            })

        collection.update(ids=chunk_ids, metadatas=chunk_metas)

    print(f"  ✅ Wrote x/y/cluster back for {n:,} photos.")


# ── Cluster summary helpers ───────────────────────────────────────────────────

def _build_cluster_summary(ids, metadatas, coords, labels):
    """Return a dict keyed by str(cluster_id) with centroid, rep, size."""
    unique_labels = sorted(set(int(l) for l in labels))
    summary = {}
    for cid in unique_labels:
        mask = np.where(labels == cid)[0]
        cluster_coords = coords[mask]
        centroid = cluster_coords.mean(axis=0)
        dists    = np.linalg.norm(cluster_coords - centroid, axis=1)
        rep_local_idx = int(np.argmin(dists))
        rep_global_idx = int(mask[rep_local_idx])
        summary[str(cid)] = {
            "centroid":            [float(centroid[0]), float(centroid[1])],
            "representative_id":   ids[rep_global_idx],
            "representative_path": metadatas[rep_global_idx].get("path", ""),
            "size":                int(len(mask)),
        }
    return summary


# ── Full fit ──────────────────────────────────────────────────────────────────

def full_fit(collection, broad_k: int, fine_k: int, limit=None):
    """Fit UMAP from scratch on all (or up to `limit`) embeddings.

    Steps
    -----
    1. Read all embeddings from ChromaDB.
    2. Fit UMAP reducer -> 2-D coords.
    3. Run AgglomerativeClustering at broad_k and fine_k.
    4. Write x/y/cluster fields back into every photo's metadata.
    5. Persist reducer + metadata artefacts under MODELS_DIR.
    """
    print(f"\n{'─'*60}")
    print("FULL FIT — reading all embeddings from ChromaDB …")
    ids, X, metadatas = read_all(collection, limit=limit)
    N = len(ids)

    if N == 0:
        print("⚠  No photos found in ChromaDB — nothing to do.")
        return

    print(f"  Loaded {N:,} embeddings, shape {X.shape}")

    # ── UMAP ──────────────────────────────────────────────────────────────────
    print("  Fitting UMAP (this may take a few minutes for large libraries) …")
    reducer = umap.UMAP(n_components=2, random_state=42)
    coords  = reducer.fit_transform(X)      # (N, 2)
    print(f"  UMAP done — coord range x=[{coords[:,0].min():.3f}, {coords[:,0].max():.3f}]"
          f"  y=[{coords[:,1].min():.3f}, {coords[:,1].max():.3f}]")

    # ── Clustering ────────────────────────────────────────────────────────────
    bk = min(broad_k, N)
    fk = min(fine_k,  N)

    if N == 1:
        labels_broad = np.array([0], dtype=np.int32)
        labels_fine  = np.array([0], dtype=np.int32)
    else:
        print(f"  Clustering: broad_k={bk}, fine_k={fk} …")
        labels_broad = AgglomerativeClustering(n_clusters=bk).fit_predict(coords).astype(np.int32)
        labels_fine  = AgglomerativeClustering(n_clusters=fk).fit_predict(coords).astype(np.int32)

    print(f"  Clustering done.")

    # ── Write back ────────────────────────────────────────────────────────────
    print(f"  Writing x/y/cluster metadata back to ChromaDB …")
    write_back(collection, ids, metadatas, coords, labels_broad, labels_fine)

    # ── Persist model artefacts ───────────────────────────────────────────────
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    joblib.dump(reducer, UMAP_MODEL_PATH)
    print(f"  Saved UMAP reducer → {UMAP_MODEL_PATH}")

    layout_meta = {
        "fit_timestamp":  datetime.now().isoformat(),
        "count_at_fit":   N,
        "umap_params":    {"n_components": 2, "random_state": 42},
        "broad_k":        bk,
        "fine_k":         fk,
    }
    LAYOUT_META_PATH.write_text(json.dumps(layout_meta, indent=2))
    print(f"  Saved layout meta → {LAYOUT_META_PATH}")

    broad_summary = _build_cluster_summary(ids, metadatas, coords, labels_broad)
    fine_summary  = _build_cluster_summary(ids, metadatas, coords, labels_fine)
    clusters_data = {"broad": broad_summary, "fine": fine_summary}
    CLUSTERS_PATH.write_text(json.dumps(clusters_data, indent=2))
    print(f"  Saved cluster summary → {CLUSTERS_PATH}")

    print(f"\n✅ FULL FIT complete — {N:,} photos projected and written back.")
    print(f"{'─'*60}\n")


# ── Incremental projection ────────────────────────────────────────────────────

def incremental(collection, limit=None):
    """Project new (un-laid-out) photos onto an existing UMAP, assign clusters.

    A photo is considered 'new' if its metadata lacks the 'x' key.
    Never overwrites existing x/y/cluster values.
    """
    print(f"\n{'─'*60}")
    print("INCREMENTAL — loading saved UMAP model …")
    reducer     = joblib.load(UMAP_MODEL_PATH)
    clusters    = json.loads(CLUSTERS_PATH.read_text())
    layout_meta = json.loads(LAYOUT_META_PATH.read_text())
    print(f"  Loaded saved model — incremental (no refit)")
    print(f"  Model was fit on {layout_meta['count_at_fit']:,} photos at {layout_meta['fit_timestamp']}")

    # ── Read all, find new ────────────────────────────────────────────────────
    print("  Reading all metadata to find new photos …")
    ids, X, metadatas = read_all(collection, limit=limit)
    N = len(ids)
    print(f"  Total photos in DB: {N:,}")

    new_indices = [i for i, m in enumerate(metadatas) if "x" not in m]
    n_new = len(new_indices)

    if n_new == 0:
        print("✅ All photos already have layout — nothing to do.")
        print(f"{'─'*60}\n")
        return

    # ── Drift warning ─────────────────────────────────────────────────────────
    count_at_fit = layout_meta["count_at_fit"]
    if count_at_fit > 0 and n_new > 0.20 * count_at_fit:
        print(
            f"\n⚠  WARNING: {n_new:,} new photos is more than 20 % of the"
            f" {count_at_fit:,} photos the UMAP was fit on."
            f"\n   The projected positions may drift noticeably."
            f"\n   Consider running with --full-refit soon.\n"
        )

    # ── Project new embeddings ────────────────────────────────────────────────
    X_new        = X[new_indices]
    ids_new      = [ids[i]      for i in new_indices]
    metas_new    = [metadatas[i] for i in new_indices]

    print(f"  Projecting {n_new:,} new photos through UMAP.transform() …")
    coords_new = reducer.transform(X_new)   # (n_new, 2)

    # ── Assign nearest cluster ────────────────────────────────────────────────
    def nearest_cluster(coords_new, cluster_dict):
        """Return label array (int32) for each row in coords_new."""
        cids      = [int(k) for k in cluster_dict.keys()]
        centroids = np.array([cluster_dict[str(c)]["centroid"] for c in cids], dtype=np.float32)
        labels    = np.empty(len(coords_new), dtype=np.int32)
        for i, pt in enumerate(coords_new):
            dists      = np.linalg.norm(centroids - pt, axis=1)
            labels[i]  = cids[int(np.argmin(dists))]
        return labels

    labels_broad_new = nearest_cluster(coords_new, clusters["broad"])
    labels_fine_new  = nearest_cluster(coords_new, clusters["fine"])

    # ── Write back (new photos only) ──────────────────────────────────────────
    print(f"  Writing layout metadata for {n_new:,} new photos …")
    write_back(collection, ids_new, metas_new, coords_new, labels_broad_new, labels_fine_new)

    print(f"\n✅ INCREMENTAL done — {n_new:,} new photos projected, assigned, and written back.")
    print(f"{'─'*60}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Precompute UMAP 2-D layout and Agglomerative cluster labels for ChromaDB photos."
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(DEFAULT_DB_PATH),
        help="Path to the ChromaDB directory (default: photo_db/ next to repo root)",
    )
    parser.add_argument(
        "--full-refit",
        action="store_true",
        help="Force a full UMAP refit even if a saved model already exists.",
    )
    parser.add_argument(
        "--broad",
        type=int,
        default=BROAD_K,
        help=f"Number of broad (coarse) clusters (default: {BROAD_K})",
    )
    parser.add_argument(
        "--fine",
        type=int,
        default=FINE_K,
        help=f"Number of fine clusters (default: {FINE_K})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of photos processed (useful for quick tests).",
    )
    args = parser.parse_args()

    collection = get_collection(args.db)
    print(f"Connected to ChromaDB at {args.db!r} — {collection.count():,} photos indexed.")

    if args.full_refit or not UMAP_MODEL_PATH.exists():
        full_fit(collection, broad_k=args.broad, fine_k=args.fine, limit=args.limit)
    else:
        incremental(collection, limit=args.limit)


if __name__ == "__main__":
    main()
