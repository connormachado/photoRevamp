"""
UMAP layout + Agglomerative clustering — the map's contract
===========================================================
`compute_layout` is the only writer of `x`, `y`, `cluster_id_broad` and
`cluster_id_fine`. Everything downstream (GraphView, neighbour browsing) reads
those four fields, so the properties worth pinning are the ones a silent
regression would break invisibly:

* a full fit gives **every** embedded photo a coordinate — none dropped, none
  paired with another photo's coordinate across a chunk boundary;
* the fit is **reproducible** — `random_state` is pinned, so re-running does not
  scramble the user's spatial memory of the map;
* the incremental path **projects onto the saved reducer and never refits**;
* clustering always goes through a **connectivity graph**, which is what keeps
  it off scipy's dense `pdist` path (~11.9 GiB at the real library size — see
  `backend/CLAUDE.md`). That one is guarded by sabotage rather than by argument
  inspection: `scipy.cluster.hierarchy.ward` is replaced with a landmine, and a
  companion test fires the landmine on purpose so the guard can't go vacuous.

A real UMAP fit is stubbed out almost everywhere (one class deliberately runs the
real thing, because determinism cannot be tested through a stub). The clustering
helpers, by contrast, run for real on small synthetic coordinate arrays — they
are cheap and they are the part that just changed.

Nothing here may touch `photo_db/` — least of all `photo_db/models/`, where the
407 MB fitted reducer lives. `tmp_models_dir` is autouse for that reason.
"""

import json
import sys
import types
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import scipy.cluster.hierarchy as scipy_hierarchy

from conftest import FakeCollection

import compute_layout as cl

pytestmark = pytest.mark.slow   # imports umap + numba + utils -> torch (~8s cold)


# ── Test doubles ──────────────────────────────────────────────────────────────

class StubReducer:
    """Deterministic stand-in for a fitted `umap.UMAP`.

    Projects onto the first two embedding dimensions. That is not a claim about
    UMAP — it is what makes "photo i got photo i's coordinate" checkable: a test
    can seed embeddings whose first two dims encode the photo's index and then
    assert the exact coordinate that must come back.

    Module-level (not a closure) so `joblib.dump` can pickle it, which is what
    `full_fit` does with the reducer it fits.
    """

    def __init__(self, **params):
        self.params = params
        self.fit_calls = 0
        self.transform_calls = []

    @staticmethod
    def _project(X):
        X = np.asarray(X, dtype=np.float32)
        return np.column_stack([X[:, 0], X[:, 1]]).astype(np.float32)

    def fit_transform(self, X):
        self.fit_calls += 1
        return self._project(X)

    def fit(self, X):
        self.fit_calls += 1
        return self

    def transform(self, X):
        self.transform_calls.append(np.asarray(X, dtype=np.float32))
        return self._project(X)


class RefitForbiddenReducer(StubReducer):
    """A saved reducer that explodes if anything tries to re-fit it.

    `incremental()`'s entire reason to exist is that the saved layout stays put;
    a refit would move every existing photo without rewriting it.
    """

    def fit_transform(self, X):
        raise AssertionError("incremental() refit the saved reducer")

    def fit(self, X):
        raise AssertionError("incremental() refit the saved reducer")


class LayoutCollection(FakeCollection):
    """conftest's `FakeCollection` plus the two surfaces `compute_layout` uses:
    embeddings + real offset/limit pagination in `get()`, and `update()`.

    Iteration order is insertion order, mirroring Chroma's stable ordering, so a
    test can reason about which page a photo lands on.
    """

    def __init__(self):
        super().__init__()
        self.embeddings: dict[str, np.ndarray] = {}
        self.update_calls: list[dict] = []

    def add_photo(self, row_id, embedding, **metadata):
        self.rows[row_id] = dict(metadata)
        self.embeddings[row_id] = np.asarray(embedding, dtype=np.float32)
        return row_id

    def get(self, ids=None, include=None, limit=None, offset=0, **_):
        keys = list(self.rows) if ids is None else [i for i in ids if i in self.rows]
        page = keys[offset:] if limit is None else keys[offset:offset + limit]
        out = {
            "ids": list(page),
            # copies: a test asserting "existing metadata survived" must not be
            # satisfied by the collection and the caller sharing one dict.
            "metadatas": [dict(self.rows[i]) for i in page],
            "documents": [None] * len(page),
        }
        if include is None or "embeddings" in include:
            out["embeddings"] = (
                np.array([self.embeddings[i] for i in page], dtype=np.float32)
                if page else np.empty((0, 0), dtype=np.float32)
            )
        return out

    def update(self, ids=None, metadatas=None, **_):
        ids, metadatas = list(ids or []), list(metadatas or [])
        assert len(ids) == len(metadatas), "update() got mismatched ids/metadatas"
        self.update_calls.append({"ids": ids, "metadatas": [dict(m) for m in metadatas]})
        for row_id, meta in zip(ids, metadatas):
            self.rows[row_id] = dict(meta)


def make_collection(n, *, dims=4, laid_out=False, prefix="photo"):
    """`n` photos whose first two embedding dims are (i, -i).

    With `StubReducer` that means photo i must land at exactly (i, -i), which is
    what turns "nothing was dropped or mis-paired" into an exact assertion.
    """
    coll = LayoutCollection()
    for i in range(n):
        emb = [float(i), float(-i)] + [0.5] * (dims - 2)
        meta = {"path": f"/lib/{prefix}_{i}.jpg", "size_kb": 70 + i}
        if laid_out:
            meta |= {"x": -999.0, "y": -999.0, "cluster_id_broad": 0, "cluster_id_fine": 0}
        coll.add_photo(f"{prefix}{i:04d}", emb, **meta)
    return coll


# ── Fixtures ──────────────────────────────────────────────────────────────────

ARTEFACT_CONSTANTS = ("MODELS_DIR", "UMAP_MODEL_PATH", "LAYOUT_META_PATH", "CLUSTERS_PATH")


@pytest.fixture(autouse=True)
def tmp_models_dir(tmp_path, monkeypatch):
    """Redirect every artefact path into tmp_path, for EVERY test in this file.

    Autouse for the same reason `isolate_stats` is: the defaults point at
    `photo_db/models/`, where a 407 MB freshly-fit reducer lives. One forgotten
    monkeypatch in one test would clobber it.
    """
    models = tmp_path / "models"
    originals = {name: getattr(cl, name) for name in ARTEFACT_CONSTANTS}

    monkeypatch.setattr(cl, "MODELS_DIR", models)
    monkeypatch.setattr(cl, "UMAP_MODEL_PATH", models / "umap.joblib")
    monkeypatch.setattr(cl, "LAYOUT_META_PATH", models / "layout_meta.json")
    monkeypatch.setattr(cl, "CLUSTERS_PATH", models / "clusters.json")

    ns = types.SimpleNamespace(dir=models, originals=originals, root=tmp_path)
    return ns


@pytest.fixture
def stub_umap(monkeypatch):
    """Swap `umap.UMAP` for `StubReducer`; returns the list of reducers built."""
    built: list[StubReducer] = []

    def factory(**kwargs):
        reducer = StubReducer(**kwargs)
        built.append(reducer)
        return reducer

    monkeypatch.setattr(cl, "umap", types.SimpleNamespace(UMAP=factory))
    return built


@pytest.fixture
def no_dense_ward(monkeypatch):
    """Turn scipy's dense Ward into a landmine.

    `sklearn`'s `ward_tree` only calls `scipy.cluster.hierarchy.ward` when
    `connectivity is None` — the branch that allocates the full condensed
    distance matrix. Making it raise means "the dense path was taken" shows up
    as a test failure rather than as an 11.9 GiB allocation on a 16 GiB machine.
    """
    def landmine(*args, **kwargs):
        raise AssertionError(
            "scipy.cluster.hierarchy.ward was reached — clustering fell through "
            "to the dense condensed-distance-matrix path"
        )

    monkeypatch.setattr(scipy_hierarchy, "ward", landmine)
    return landmine


@pytest.fixture
def saved_model(tmp_models_dir, monkeypatch):
    """Stand up the three artefacts an `incremental()` run loads.

    `joblib` is stubbed rather than round-tripped so the test keeps a reference
    to the very reducer `incremental` will use, and can assert on what was
    handed to `.transform()`.
    """
    class SavedModel:
        def __init__(self):
            self.dumped = []

        def install(self, reducer=None, clusters=None, count_at_fit=1000):
            reducer = reducer if reducer is not None else RefitForbiddenReducer()
            clusters = clusters if clusters is not None else {
                "broad": {
                    "0": {"centroid": [0.0, 0.0], "representative_id": "a",
                          "representative_path": "/lib/a.jpg", "size": 500},
                    "1": {"centroid": [100.0, -100.0], "representative_id": "b",
                          "representative_path": "/lib/b.jpg", "size": 500},
                },
                "fine": {
                    "0": {"centroid": [0.0, 0.0], "representative_id": "a",
                          "representative_path": "/lib/a.jpg", "size": 250},
                    "1": {"centroid": [50.0, -50.0], "representative_id": "c",
                          "representative_path": "/lib/c.jpg", "size": 250},
                    "2": {"centroid": [100.0, -100.0], "representative_id": "b",
                          "representative_path": "/lib/b.jpg", "size": 500},
                },
            }
            cl.MODELS_DIR.mkdir(parents=True, exist_ok=True)
            cl.CLUSTERS_PATH.write_text(json.dumps(clusters))
            cl.LAYOUT_META_PATH.write_text(json.dumps({
                "fit_timestamp": "2026-01-01T00:00:00",
                "count_at_fit": count_at_fit,
                "umap_params": {"n_components": 2, "random_state": 42},
                "broad_k": 12, "fine_k": 60, "connectivity_k": 15,
            }))
            monkeypatch.setattr(cl, "joblib", types.SimpleNamespace(
                load=lambda path: reducer,
                dump=lambda obj, path: self.dumped.append((obj, path)),
            ))
            self.reducer = reducer
            self.clusters = clusters
            return reducer

    return SavedModel()


# ── Harness self-checks ───────────────────────────────────────────────────────

class TestArtefactIsolation:
    """The live `photo_db/models/` must be unreachable from this file."""

    def test_every_artefact_path_is_redirected_into_tmp(self, tmp_models_dir):
        for name in ARTEFACT_CONSTANTS:
            assert tmp_models_dir.root in getattr(cl, name).parents, name

    def test_the_redirected_constants_really_do_default_into_photo_db(self, tmp_models_dir):
        # If this ever stops holding, the fixture above is redirecting something
        # harmless and the real artefacts are being written by some other name.
        for name, original in tmp_models_dir.originals.items():
            assert cl.DEFAULT_DB_PATH in original.parents, name

    def test_no_module_level_path_still_points_inside_the_live_db(self, tmp_models_dir):
        """Catches a *new* artefact constant that the fixture doesn't know about."""
        stray = [
            name for name, value in vars(cl).items()
            if isinstance(value, Path)
            and name != "DEFAULT_DB_PATH"          # the read-only root itself
            and cl.DEFAULT_DB_PATH in value.parents
        ]
        assert stray == [], f"unredirected artefact path(s): {stray}"


# ── read_all ──────────────────────────────────────────────────────────────────

class TestReadAll:
    """Pagination contract: ids, embeddings and metadatas come back aligned."""

    def test_reads_every_photo_across_page_boundaries(self, monkeypatch):
        monkeypatch.setattr(cl, "CHUNK_SIZE", 25)   # 137 photos => 6 pages
        coll = make_collection(137)

        ids, X, metas = cl.read_all(coll)

        assert len(ids) == 137
        assert X.shape == (137, 4)
        assert len(metas) == 137
        # Row i must still be photo i's embedding after five page joins.
        assert [m["path"] for m in metas] == [f"/lib/photo_{i}.jpg" for i in range(137)]
        assert np.array_equal(X[:, 0], np.arange(137, dtype=np.float32))

    def test_empty_collection_returns_empty_rather_than_raising(self):
        ids, X, metas = cl.read_all(LayoutCollection())

        assert ids == []
        assert metas == []
        assert X.shape[0] == 0

    def test_limit_caps_the_number_of_records_read(self, monkeypatch):
        monkeypatch.setattr(cl, "CHUNK_SIZE", 7)
        coll = make_collection(30)

        ids, X, metas = cl.read_all(coll, limit=10)

        assert len(ids) == len(metas) == X.shape[0] == 10
        assert ids == [f"photo{i:04d}" for i in range(10)]

    def test_limit_larger_than_the_collection_reads_everything(self):
        ids, X, _ = cl.read_all(make_collection(5), limit=500)
        assert len(ids) == X.shape[0] == 5

    @pytest.mark.parametrize("limit", [0, -1])
    def test_a_non_positive_limit_is_rejected(self, limit):
        """`--limit 0` reading nothing was a footgun — a whole-library refit that
        quietly did nothing looks identical to one that worked. It raises now."""
        with pytest.raises(ValueError, match="positive"):
            cl.read_all(make_collection(5), limit=limit)

    def test_list_shaped_embeddings_are_normalised_to_a_float32_matrix(self):
        """ChromaDB 1.5.x may hand back lists instead of an ndarray."""
        class ListEmbeddingCollection(LayoutCollection):
            def get(self, **kwargs):
                batch = super().get(**kwargs)
                if "embeddings" in batch:
                    batch["embeddings"] = [list(map(float, e)) for e in batch["embeddings"]]
                return batch

        coll = ListEmbeddingCollection()
        for i in range(4):
            coll.add_photo(f"p{i}", [float(i), float(-i), 0.5, 0.5], path=f"/lib/{i}.jpg")

        _, X, _ = cl.read_all(coll)

        assert X.dtype == np.float32
        assert X.shape == (4, 4)

    def test_a_first_page_with_no_embeddings_degrades_to_an_empty_result(self):
        """Nothing readable at all is handled: all three outputs come back empty."""
        class EmbeddinglessCollection(LayoutCollection):
            def get(self, **kwargs):
                batch = super().get(**kwargs)
                batch["embeddings"] = []
                return batch

        coll = EmbeddinglessCollection()
        for i in range(3):
            coll.add_photo(f"p{i}", [float(i), 0.0, 0.0, 0.0], path=f"/lib/{i}.jpg")

        ids, X, metas = cl.read_all(coll)

        assert (ids, metas) == ([], [])
        assert X.shape[0] == 0

    def test_ids_embeddings_and_metadatas_stay_aligned_when_a_later_page_has_none(
        self, monkeypatch
    ):
        """This was a real defect, marked xfail(strict) rather than fixed.

        read_all used to extend ids+metadatas BEFORE checking that the page
        carried embeddings, so a later empty-embeddings page returned more ids
        than coordinate rows (4 ids against X.shape (2, 4)). full_fit then
        clustered the short coords array and IndexErrored inside write_back —
        which commits in chunks, so the crash landed with part of the library
        already rewritten. The check moved above the extends; the three outputs
        now grow together or not at all.
        """
        monkeypatch.setattr(cl, "CHUNK_SIZE", 2)

        class TruncatedEmbeddingCollection(LayoutCollection):
            """Page 1 carries embeddings; every later page carries ids but none."""

            def __init__(self):
                super().__init__()
                self.pages = 0

            def get(self, **kwargs):
                batch = super().get(**kwargs)
                self.pages += 1
                if self.pages > 1:
                    batch["embeddings"] = []
                return batch

        coll = TruncatedEmbeddingCollection()
        for i in range(6):
            coll.add_photo(f"p{i}", [float(i), 0.0, 0.0, 0.0], path=f"/lib/{i}.jpg")

        ids, X, metas = cl.read_all(coll)

        assert len(ids) == X.shape[0] == len(metas)
        # And it stops at the last good page rather than skipping the bad one —
        # the conservative `break` is deliberate, so pin it.
        assert ids == ["p0", "p1"]

    def test_a_desynced_page_raises_instead_of_returning_a_mismatched_triple(self):
        """The terminal invariant: read_all must never HAND OUT a bad pairing.

        Every caller does `ids[i] <-> coords[i]`, so there is no safe way to
        consume a triple whose parts differ in length. Raising here is loud and
        happens before any write; returning it would surface as an IndexError
        partway through write_back's chunked rewrite of the live library.
        """
        class ShortEmbeddingPageCollection(LayoutCollection):
            """One page: 4 ids and metadatas, but only 3 embedding rows."""

            def get(self, **kwargs):
                batch = super().get(**kwargs)
                if len(batch.get("embeddings", [])) > 3:
                    batch["embeddings"] = batch["embeddings"][:3]
                return batch

        coll = ShortEmbeddingPageCollection()
        for i in range(4):
            coll.add_photo(f"p{i}", [float(i), 0.0, 0.0, 0.0], path=f"/lib/{i}.jpg")

        with pytest.raises(RuntimeError, match="desynced"):
            cl.read_all(coll)

    def test_reading_fewer_photos_than_the_collection_holds_warns_loudly(self, capsys):
        """Truncation is the silent half of the same bug — the invariant check
        stays happy while the layout covers a subset. Compare against the live
        count and say so; this is the manual check that found 8,308 photos with
        no coordinates, baked in so nobody has to think to run it."""
        class StopsEarlyCollection(LayoutCollection):
            def get(self, **kwargs):
                batch = super().get(**kwargs)
                batch["embeddings"] = []
                return batch

        coll = StopsEarlyCollection()
        for i in range(7):
            coll.add_photo(f"p{i}", [float(i), 0.0, 0.0, 0.0], path=f"/lib/{i}.jpg")

        ids, _, _ = cl.read_all(coll)
        out = capsys.readouterr().out

        assert ids == []
        assert "WARNING" in out
        assert "0 of 7" in out          # both numbers, not just a vague grumble
        assert "SUBSET" in out

    def test_a_complete_read_stays_quiet(self, capsys):
        """The coverage warning has to be silent on the happy path or it is noise."""
        cl.read_all(make_collection(5))
        assert "WARNING" not in capsys.readouterr().out

    def test_the_coverage_warning_does_not_fire_for_a_deliberate_limit(self, capsys):
        """`--limit` reads a subset ON PURPOSE — warning there would train the
        operator to ignore the warning that matters."""
        cl.read_all(make_collection(20), limit=5)
        assert "WARNING" not in capsys.readouterr().out


# ── write_back ────────────────────────────────────────────────────────────────

class TestWriteBack:
    """Merging layout fields into metadata without losing or mis-pairing rows."""

    def test_existing_metadata_fields_survive_the_merge(self):
        coll = make_collection(3)
        ids, _, metas = cl.read_all(coll)

        cl.write_back(coll, ids, metas, np.zeros((3, 2), dtype=np.float32),
                      np.zeros(3, dtype=np.int32), np.zeros(3, dtype=np.int32))

        assert coll.rows["photo0001"]["path"] == "/lib/photo_1.jpg"
        assert coll.rows["photo0001"]["size_kb"] == 71

    def test_each_photo_gets_its_own_coordinate_across_chunk_boundaries(self, monkeypatch):
        monkeypatch.setattr(cl, "CHUNK_SIZE", 7)   # 20 rows => 3 uneven chunks
        coll = make_collection(20)
        ids, _, metas = cl.read_all(coll)
        coords = np.array([[i * 1.5, -i * 1.5] for i in range(20)], dtype=np.float32)
        broad = np.array([i % 3 for i in range(20)], dtype=np.int32)
        fine = np.array([i % 7 for i in range(20)], dtype=np.int32)

        cl.write_back(coll, ids, metas, coords, broad, fine)

        for i, row_id in enumerate(ids):
            row = coll.rows[row_id]
            assert (row["x"], row["y"]) == (i * 1.5, -i * 1.5)
            assert row["cluster_id_broad"] == i % 3
            assert row["cluster_id_fine"] == i % 7

    def test_every_id_is_written_exactly_once(self, monkeypatch):
        monkeypatch.setattr(cl, "CHUNK_SIZE", 7)
        coll = make_collection(20)
        ids, _, metas = cl.read_all(coll)

        cl.write_back(coll, ids, metas, np.zeros((20, 2), dtype=np.float32),
                      np.zeros(20, dtype=np.int32), np.zeros(20, dtype=np.int32))

        written = [i for call in coll.update_calls for i in call["ids"]]
        assert sorted(written) == sorted(ids)
        assert len(written) == len(set(written))

    def test_no_single_update_exceeds_the_chunk_size(self, monkeypatch):
        """The chunking exists to stay under SQLite's bound-variable limit."""
        monkeypatch.setattr(cl, "CHUNK_SIZE", 7)
        coll = make_collection(20)
        ids, _, metas = cl.read_all(coll)

        cl.write_back(coll, ids, metas, np.zeros((20, 2), dtype=np.float32),
                      np.zeros(20, dtype=np.int32), np.zeros(20, dtype=np.int32))

        assert coll.update_calls
        assert all(len(call["ids"]) <= 7 for call in coll.update_calls)

    def test_values_are_plain_python_scalars_not_numpy(self):
        """Chroma metadata must be JSON-native; a np.float32 is not."""
        coll = make_collection(2)
        ids, _, metas = cl.read_all(coll)
        coords = np.array([[1.5, 2.5], [3.5, 4.5]], dtype=np.float32)

        cl.write_back(coll, ids, metas, coords,
                      np.array([1, 2], dtype=np.int32), np.array([3, 4], dtype=np.int32))

        row = coll.rows[ids[0]]
        assert type(row["x"]) is float and type(row["y"]) is float
        assert type(row["cluster_id_broad"]) is int and type(row["cluster_id_fine"]) is int
        json.dumps(row)   # must not raise

    def test_writing_nothing_issues_no_update(self):
        coll = make_collection(3)

        cl.write_back(coll, [], [], np.empty((0, 2), dtype=np.float32),
                      np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int32))

        assert coll.update_calls == []
        assert "x" not in coll.rows["photo0000"]


# ── _connectivity_graph ───────────────────────────────────────────────────────

class TestConnectivityGraph:
    """A sparse kNN adjacency — the thing that keeps Ward off the dense path."""

    def test_graph_is_square_sparse_and_has_k_neighbours_per_node(self):
        coords = np.random.default_rng(0).random((200, 2)).astype(np.float32)

        graph = cl._connectivity_graph(coords)

        assert graph.shape == (200, 200)
        assert graph.nnz == 200 * cl.CONNECTIVITY_K
        assert graph.nnz < 200 * 200 / 10   # sparse by a wide margin, not dense

    def test_no_node_is_its_own_neighbour(self):
        coords = np.random.default_rng(1).random((50, 2)).astype(np.float32)

        graph = cl._connectivity_graph(coords)

        assert graph.diagonal().sum() == 0

    def test_k_is_clamped_to_the_number_of_available_neighbours(self):
        """5 photos can't have 15 neighbours each — clamp, don't raise."""
        coords = np.random.default_rng(2).random((5, 2)).astype(np.float32)

        graph = cl._connectivity_graph(coords)

        assert graph.nnz == 5 * 4

    @pytest.mark.parametrize("n", [0, 1])
    def test_too_small_to_have_neighbours_returns_none(self, n):
        assert cl._connectivity_graph(np.zeros((n, 2), dtype=np.float32)) is None

    def test_two_points_still_produce_a_graph(self):
        graph = cl._connectivity_graph(np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32))
        assert graph is not None and graph.nnz == 2


# ── The dense-Ward guard ──────────────────────────────────────────────────────

class TestDenseWardPathStaysUnreachable:
    """`connectivity=None` allocates N(N-1)/2 float64 — ~11.9 GiB at 56,612
    photos. These tests assert the dense path is never entered, and that the
    detector doing the asserting actually works."""

    @staticmethod
    def _blobs(per_blob=20, seed=7):
        rng = np.random.default_rng(seed)
        return np.vstack([
            rng.normal(centre, 0.3, size=(per_blob, 2))
            for centre in ([0, 0], [10, 0], [0, 10])
        ]).astype(np.float32)

    def test_the_landmine_fires_when_the_dense_path_is_taken(self, no_dense_ward):
        """Without this, every other test in the class could be vacuous."""
        coords = self._blobs()

        with pytest.raises(AssertionError, match="dense"):
            cl._cluster_labels(coords, 3, None)

    def test_cluster_labels_with_a_connectivity_graph_avoids_the_dense_path(self, no_dense_ward):
        coords = self._blobs()

        labels = cl._cluster_labels(coords, 3, cl._connectivity_graph(coords))

        assert len(labels) == len(coords)

    def test_a_full_fit_never_reaches_the_dense_path(self, stub_umap, no_dense_ward):
        coll = make_collection(60)

        cl.full_fit(coll, broad_k=3, fine_k=8)

        assert all("x" in row for row in coll.rows.values())

    def test_layout_meta_records_the_connectivity_k_actually_used(self, stub_umap):
        """`connectivity_k` in the artefact is the audit trail for the above."""
        coll = make_collection(60)

        cl.full_fit(coll, broad_k=3, fine_k=8)

        meta = json.loads(cl.LAYOUT_META_PATH.read_text())
        assert meta["connectivity_k"] == cl.CONNECTIVITY_K

    def test_connectivity_k_is_clamped_and_recorded_for_a_tiny_library(self, stub_umap):
        coll = make_collection(6)

        cl.full_fit(coll, broad_k=2, fine_k=3)

        meta = json.loads(cl.LAYOUT_META_PATH.read_text())
        assert meta["connectivity_k"] == 5   # min(CONNECTIVITY_K, N - 1)


# ── _cluster_labels ───────────────────────────────────────────────────────────

class TestClusterLabels:
    """Real sklearn on small synthetic coords — cheap, and it just changed."""

    @staticmethod
    def _blobs(per_blob=15, seed=11):
        rng = np.random.default_rng(seed)
        coords = np.vstack([
            rng.normal(centre, 0.2, size=(per_blob, 2))
            for centre in ([0, 0], [20, 0], [0, 20])
        ]).astype(np.float32)
        truth = np.repeat([0, 1, 2], per_blob)
        return coords, truth

    def test_well_separated_blobs_come_out_as_one_cluster_each(self):
        coords, truth = self._blobs()

        labels = cl._cluster_labels(coords, 3, cl._connectivity_graph(coords))

        # Label *values* are arbitrary; the partition is what matters.
        for blob in (0, 1, 2):
            assert len(set(labels[truth == blob])) == 1
        assert len(set(labels)) == 3

    def test_returns_one_label_per_row_in_range(self):
        coords, _ = self._blobs()

        labels = cl._cluster_labels(coords, 4, cl._connectivity_graph(coords))

        assert labels.shape == (len(coords),)
        assert labels.dtype == np.int32
        assert set(labels) <= set(range(4))

    def test_is_deterministic_for_identical_input(self):
        coords, _ = self._blobs()
        graph = cl._connectivity_graph(coords)

        first = cl._cluster_labels(coords, 5, graph)
        second = cl._cluster_labels(coords, 5, cl._connectivity_graph(coords))

        assert np.array_equal(first, second)

    def test_k_equal_to_n_gives_every_point_its_own_cluster(self):
        coords = np.array([[0.0, 0.0], [1.0, 1.0], [5.0, 5.0], [9.0, 9.0]], dtype=np.float32)

        labels = cl._cluster_labels(coords, 4, cl._connectivity_graph(coords))

        assert len(set(labels)) == 4


# ── _build_cluster_summary ────────────────────────────────────────────────────

class TestBuildClusterSummary:
    """clusters.json is what `incremental()` assigns new photos against."""

    @staticmethod
    def _fixture_data():
        coords = np.array([
            [0.0, 0.0], [2.0, 0.0],            # cluster 0
            [10.0, 10.0], [11.0, 10.0], [12.0, 10.0],   # cluster 1
        ], dtype=np.float32)
        labels = np.array([0, 0, 1, 1, 1], dtype=np.int32)
        ids = [f"p{i}" for i in range(5)]
        metas = [{"path": f"/lib/p{i}.jpg"} for i in range(5)]
        return ids, metas, coords, labels

    def test_every_cluster_gets_an_entry_keyed_by_its_string_id(self):
        ids, metas, coords, labels = self._fixture_data()

        summary = cl._build_cluster_summary(ids, metas, coords, labels)

        assert set(summary) == {"0", "1"}

    def test_sizes_are_the_membership_counts_and_sum_to_n(self):
        ids, metas, coords, labels = self._fixture_data()

        summary = cl._build_cluster_summary(ids, metas, coords, labels)

        assert summary["0"]["size"] == 2
        assert summary["1"]["size"] == 3
        assert sum(c["size"] for c in summary.values()) == len(ids)

    def test_centroid_is_the_mean_of_its_members(self):
        ids, metas, coords, labels = self._fixture_data()

        summary = cl._build_cluster_summary(ids, metas, coords, labels)

        assert summary["0"]["centroid"] == pytest.approx([1.0, 0.0])
        assert summary["1"]["centroid"] == pytest.approx([11.0, 10.0])

    def test_representative_is_the_member_nearest_its_centroid(self):
        """Indexed globally — the classic bug is returning ids[local_index]."""
        ids, metas, coords, labels = self._fixture_data()

        summary = cl._build_cluster_summary(ids, metas, coords, labels)

        assert summary["1"]["representative_id"] == "p3"          # not "p1"
        assert summary["1"]["representative_path"] == "/lib/p3.jpg"

    def test_a_photo_without_a_path_yields_an_empty_string_not_a_crash(self):
        summary = cl._build_cluster_summary(
            ["p0"], [{}], np.array([[1.0, 2.0]], dtype=np.float32),
            np.array([0], dtype=np.int32),
        )

        assert summary["0"]["representative_path"] == ""

    def test_summary_is_json_serialisable(self):
        """It is written straight out with json.dumps — numpy must not leak in."""
        ids, metas, coords, labels = self._fixture_data()

        summary = cl._build_cluster_summary(ids, metas, coords, labels)

        assert json.loads(json.dumps(summary)) == summary

    def test_non_contiguous_labels_keep_their_own_ids(self):
        summary = cl._build_cluster_summary(
            ["a", "b"], [{"path": "/a"}, {"path": "/b"}],
            np.array([[0.0, 0.0], [5.0, 5.0]], dtype=np.float32),
            np.array([3, 7], dtype=np.int32),
        )

        assert set(summary) == {"3", "7"}

    def test_a_single_photo_cluster_is_its_own_representative(self):
        summary = cl._build_cluster_summary(
            ["solo"], [{"path": "/lib/solo.jpg"}],
            np.array([[4.0, -4.0]], dtype=np.float32), np.array([0], dtype=np.int32),
        )

        assert summary["0"] == {
            "centroid": [4.0, -4.0],
            "representative_id": "solo",
            "representative_path": "/lib/solo.jpg",
            "size": 1,
        }


# ── full_fit ──────────────────────────────────────────────────────────────────

class TestFullFitCoverage:
    """(a) A full refit must give EVERY embedded photo a coordinate."""

    def test_every_photo_is_written_and_gets_its_own_coordinate(self, stub_umap, monkeypatch):
        monkeypatch.setattr(cl, "CHUNK_SIZE", 25)   # force multi-chunk read + write
        coll = make_collection(137)

        cl.full_fit(coll, broad_k=3, fine_k=8)

        assert len(coll.rows) == 137
        for i in range(137):
            row = coll.rows[f"photo{i:04d}"]
            # StubReducer projects onto dims 0/1, which encode the photo index —
            # so a dropped or shifted row shows up here, not just as a count.
            assert (row["x"], row["y"]) == (float(i), float(-i))
            assert "cluster_id_broad" in row and "cluster_id_fine" in row

    def test_existing_metadata_survives_a_full_fit(self, stub_umap):
        coll = make_collection(20)

        cl.full_fit(coll, broad_k=2, fine_k=4)

        assert coll.rows["photo0007"]["path"] == "/lib/photo_7.jpg"
        assert coll.rows["photo0007"]["size_kb"] == 77

    def test_a_rerun_overwrites_rather_than_duplicating_layout_fields(self, stub_umap):
        """full_fit is the authoritative writer; running it twice is idempotent."""
        coll = make_collection(20)

        cl.full_fit(coll, broad_k=2, fine_k=4)
        first = {i: dict(row) for i, row in coll.rows.items()}
        cl.full_fit(coll, broad_k=2, fine_k=4)

        assert {i: dict(row) for i, row in coll.rows.items()} == first

    def test_limit_processes_only_that_many_photos_and_leaves_the_rest_alone(self, stub_umap):
        coll = make_collection(30)

        cl.full_fit(coll, broad_k=2, fine_k=4, limit=10)

        assert all("x" in coll.rows[f"photo{i:04d}"] for i in range(10))
        assert all("x" not in coll.rows[f"photo{i:04d}"] for i in range(10, 30))
        assert json.loads(cl.LAYOUT_META_PATH.read_text())["count_at_fit"] == 10


class TestFullFitEdges:
    def test_an_empty_collection_writes_no_artefacts_and_does_not_raise(self, stub_umap):
        coll = LayoutCollection()

        cl.full_fit(coll, broad_k=12, fine_k=60)

        assert coll.update_calls == []
        assert not cl.UMAP_MODEL_PATH.exists()
        assert not cl.LAYOUT_META_PATH.exists()
        assert not cl.CLUSTERS_PATH.exists()

    def test_a_single_photo_still_gets_a_coordinate_and_a_cluster(self, stub_umap):
        coll = make_collection(1)

        cl.full_fit(coll, broad_k=12, fine_k=60)

        row = coll.rows["photo0000"]
        assert (row["x"], row["y"]) == (0.0, 0.0)
        assert row["cluster_id_broad"] == 0 and row["cluster_id_fine"] == 0
        assert json.loads(cl.CLUSTERS_PATH.read_text())["broad"] == {
            "0": {"centroid": [0.0, 0.0], "representative_id": "photo0000",
                  "representative_path": "/lib/photo_0.jpg", "size": 1}
        }

    def test_k_is_clamped_to_the_photo_count(self, stub_umap):
        """Asking for 60 clusters over 5 photos means 5 clusters, not a crash."""
        coll = make_collection(5)

        cl.full_fit(coll, broad_k=12, fine_k=60)

        labels = {row["cluster_id_fine"] for row in coll.rows.values()}
        assert len(labels) == 5
        meta = json.loads(cl.LAYOUT_META_PATH.read_text())
        assert (meta["broad_k"], meta["fine_k"]) == (5, 5)


class TestFullFitArtefacts:
    def test_all_three_artefacts_are_written_under_models_dir(self, stub_umap):
        coll = make_collection(30)

        cl.full_fit(coll, broad_k=3, fine_k=6)

        assert cl.UMAP_MODEL_PATH.exists()
        assert cl.LAYOUT_META_PATH.exists()
        assert cl.CLUSTERS_PATH.exists()

    def test_the_saved_reducer_reloads_and_is_the_one_that_was_fitted(self, stub_umap):
        coll = make_collection(30)

        cl.full_fit(coll, broad_k=3, fine_k=6)

        import joblib
        reloaded = joblib.load(cl.UMAP_MODEL_PATH)
        assert isinstance(reloaded, StubReducer)
        assert reloaded.params == stub_umap[0].params

    def test_layout_meta_describes_the_fit_that_just_happened(self, stub_umap):
        coll = make_collection(30)

        cl.full_fit(coll, broad_k=3, fine_k=6)

        meta = json.loads(cl.LAYOUT_META_PATH.read_text())
        assert meta["count_at_fit"] == 30
        assert (meta["broad_k"], meta["fine_k"]) == (3, 6)
        datetime.fromisoformat(meta["fit_timestamp"])   # parseable, not a blob

    def test_every_cluster_id_written_to_a_photo_exists_in_clusters_json(self, stub_umap):
        """`incremental()` looks centroids up by that id — a gap orphans photos."""
        coll = make_collection(40)

        cl.full_fit(coll, broad_k=4, fine_k=9)

        clusters = json.loads(cl.CLUSTERS_PATH.read_text())
        for row in coll.rows.values():
            assert str(row["cluster_id_broad"]) in clusters["broad"]
            assert str(row["cluster_id_fine"]) in clusters["fine"]

    def test_cluster_sizes_account_for_every_photo(self, stub_umap):
        coll = make_collection(40)

        cl.full_fit(coll, broad_k=4, fine_k=9)

        clusters = json.loads(cl.CLUSTERS_PATH.read_text())
        assert sum(c["size"] for c in clusters["broad"].values()) == 40
        assert sum(c["size"] for c in clusters["fine"].values()) == 40

    def test_models_dir_is_created_when_missing(self, stub_umap, tmp_models_dir):
        assert not tmp_models_dir.dir.exists()

        cl.full_fit(make_collection(10), broad_k=2, fine_k=3)

        assert tmp_models_dir.dir.is_dir()

    def test_the_reducer_is_built_two_dimensional_with_a_pinned_seed(self, stub_umap):
        """An unpinned reducer re-scrambles the map on every refit."""
        cl.full_fit(make_collection(10), broad_k=2, fine_k=3)

        [reducer] = stub_umap
        assert reducer.params["n_components"] == 2
        assert reducer.params["random_state"] is not None
        # The artefact must describe the params actually used, or a later reader
        # can't tell whether the layout was reproducible.
        meta = json.loads(cl.LAYOUT_META_PATH.read_text())
        assert meta["umap_params"] == reducer.params


class TestReproducibility:
    """(b) Two fits of identical input must land in exactly the same place.

    This is the one place the REAL UMAP runs: a stubbed reducer cannot tell you
    whether `random_state` is doing its job. Costs ~10s the first time (numba
    JIT); the second fit is ~0.2s.
    """

    def test_two_full_fits_on_identical_input_produce_identical_coordinates(self):
        rng = np.random.default_rng(4242)
        embeddings = rng.normal(size=(60, 8)).astype(np.float32)

        def fresh():
            coll = LayoutCollection()
            for i, emb in enumerate(embeddings):
                coll.add_photo(f"photo{i:04d}", emb, path=f"/lib/{i}.jpg")
            return coll

        first, second = fresh(), fresh()
        cl.full_fit(first, broad_k=4, fine_k=8)
        clusters_after_first = cl.CLUSTERS_PATH.read_text()
        cl.full_fit(second, broad_k=4, fine_k=8)

        for row_id in first.rows:
            a, b = first.rows[row_id], second.rows[row_id]
            assert (a["x"], a["y"]) == (b["x"], b["y"]), f"{row_id} moved between fits"
            assert a["cluster_id_broad"] == b["cluster_id_broad"]
            assert a["cluster_id_fine"] == b["cluster_id_fine"]

        # The cluster artefact the map reads must be stable too, not just the points.
        assert cl.CLUSTERS_PATH.read_text() == clusters_after_first


# ── incremental ───────────────────────────────────────────────────────────────

class TestIncrementalProjection:
    """(c) New photos are projected onto the SAVED reducer — never a refit."""

    @staticmethod
    def _mixed_collection(n_old=6, n_new=3):
        """`n_old` photos already carrying x/y, then `n_new` without."""
        coll = LayoutCollection()
        for i in range(n_old):
            coll.add_photo(
                f"old{i:03d}", [float(i), float(-i), 0.5, 0.5],
                path=f"/lib/old_{i}.jpg", x=float(i * 100), y=float(i * -100),
                cluster_id_broad=1, cluster_id_fine=2,
            )
        for i in range(n_new):
            coll.add_photo(
                f"new{i:03d}", [float(i), float(-i), 0.5, 0.5],
                path=f"/lib/new_{i}.jpg", size_kb=88,
            )
        return coll

    def test_new_photos_are_projected_through_transform(self, saved_model):
        reducer = saved_model.install()
        coll = self._mixed_collection()

        cl.incremental(coll)

        assert reducer.fit_calls == 0
        assert len(reducer.transform_calls) == 1
        # Exactly the three un-laid-out photos, in their collection order.
        assert reducer.transform_calls[0].shape == (3, 4)
        assert np.array_equal(reducer.transform_calls[0][:, 0], np.array([0.0, 1.0, 2.0]))

    def test_photos_that_already_have_a_layout_are_left_untouched(self, saved_model):
        saved_model.install()
        coll = self._mixed_collection()

        cl.incremental(coll)

        for i in range(6):
            row = coll.rows[f"old{i:03d}"]
            assert (row["x"], row["y"]) == (float(i * 100), float(i * -100))
        written = {i for call in coll.update_calls for i in call["ids"]}
        assert written == {"new000", "new001", "new002"}

    def test_new_photos_receive_coordinates_and_both_cluster_ids(self, saved_model):
        saved_model.install()
        coll = self._mixed_collection()

        cl.incremental(coll)

        for i in range(3):
            row = coll.rows[f"new{i:03d}"]
            assert (row["x"], row["y"]) == (float(i), float(-i))
            assert "cluster_id_broad" in row and "cluster_id_fine" in row

    def test_new_photos_keep_their_existing_metadata(self, saved_model):
        saved_model.install()
        coll = self._mixed_collection()

        cl.incremental(coll)

        assert coll.rows["new001"]["path"] == "/lib/new_1.jpg"
        assert coll.rows["new001"]["size_kb"] == 88

    def test_nothing_new_means_no_projection_and_no_write(self, saved_model):
        reducer = saved_model.install()
        coll = self._mixed_collection(n_old=4, n_new=0)

        cl.incremental(coll)

        assert reducer.transform_calls == []
        assert coll.update_calls == []

    def test_an_empty_collection_is_a_no_op(self, saved_model):
        reducer = saved_model.install()
        coll = LayoutCollection()

        cl.incremental(coll)

        assert reducer.transform_calls == []
        assert coll.update_calls == []


class TestIncrementalClusterAssignment:
    """A new photo joins the cluster whose centroid it is nearest."""

    @staticmethod
    def _collection_at(x, y):
        coll = LayoutCollection()
        # StubReducer projects dims 0/1 straight through, so the embedding IS
        # the target coordinate — which is what makes "nearest centroid" exact.
        coll.add_photo("newbie", [x, y, 0.0, 0.0], path="/lib/newbie.jpg")
        return coll

    def test_a_photo_lands_in_the_nearest_cluster(self, saved_model):
        saved_model.install(reducer=RefitForbiddenReducer())
        coll = self._collection_at(99.0, -99.0)   # hugs the "1" broad centroid

        cl.incremental(coll)

        assert coll.rows["newbie"]["cluster_id_broad"] == 1

    def test_broad_and_fine_are_assigned_from_their_own_summaries(self, saved_model):
        saved_model.install(reducer=RefitForbiddenReducer())
        # (45, -45): nearest broad centroid is "0" at the origin (63.6 vs 77.8),
        # but the nearest fine centroid is "1" at (50, -50), only 7.1 away.
        coll = self._collection_at(45.0, -45.0)

        cl.incremental(coll)

        row = coll.rows["newbie"]
        assert row["cluster_id_broad"] == 0
        assert row["cluster_id_fine"] == 1

    def test_the_stored_cluster_id_is_the_key_not_its_position(self, saved_model):
        """Cluster ids need not be 0..k-1 — never return the array index."""
        clusters = {
            "broad": {
                "3": {"centroid": [0.0, 0.0], "representative_id": "a",
                      "representative_path": "/a", "size": 1},
                "7": {"centroid": [100.0, 100.0], "representative_id": "b",
                      "representative_path": "/b", "size": 1},
            },
            "fine": {
                "9": {"centroid": [0.0, 0.0], "representative_id": "a",
                      "representative_path": "/a", "size": 1},
            },
        }
        saved_model.install(reducer=RefitForbiddenReducer(), clusters=clusters)
        coll = self._collection_at(99.0, 99.0)

        cl.incremental(coll)

        assert coll.rows["newbie"]["cluster_id_broad"] == 7
        assert coll.rows["newbie"]["cluster_id_fine"] == 9

    def test_cluster_ids_are_plain_ints(self, saved_model):
        saved_model.install(reducer=RefitForbiddenReducer())
        coll = self._collection_at(1.0, 1.0)

        cl.incremental(coll)

        json.dumps(coll.rows["newbie"])   # numpy int32 would raise here
        assert type(coll.rows["newbie"]["cluster_id_broad"]) is int


class TestIncrementalDriftWarning:
    """Projecting far more photos than were fitted degrades the layout, so the
    run must say so — that warning is the signal to refit (backend/CLAUDE.md)."""

    @staticmethod
    def _n_new(n):
        coll = LayoutCollection()
        for i in range(n):
            coll.add_photo(f"new{i:04d}", [float(i), float(-i), 0.5, 0.5],
                           path=f"/lib/new_{i}.jpg")
        return coll

    def test_more_than_twenty_percent_new_warns_and_names_the_remedy(self, saved_model, capsys):
        saved_model.install(count_at_fit=100)

        cl.incremental(self._n_new(21))

        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "full-refit" in out

    @pytest.mark.parametrize("n_new", [1, 20])
    def test_at_or_below_twenty_percent_stays_quiet(self, saved_model, capsys, n_new):
        saved_model.install(count_at_fit=100)

        cl.incremental(self._n_new(n_new))

        assert "WARNING" not in capsys.readouterr().out


# ── CLI dispatch ──────────────────────────────────────────────────────────────

class TestCliDispatch:
    """--full-refit OR a missing model => full_fit; otherwise => incremental."""

    @pytest.fixture
    def cli(self, monkeypatch):
        """Record which path `main()` chose, without running either."""
        calls = {"full_fit": [], "incremental": [], "get_collection": []}
        collection = make_collection(3)

        def fake_get_collection(db_path):
            calls["get_collection"].append(db_path)
            return collection

        monkeypatch.setattr(cl, "get_collection", fake_get_collection)
        monkeypatch.setattr(cl, "full_fit", lambda coll, **kw: calls["full_fit"].append(kw))
        monkeypatch.setattr(cl, "incremental", lambda coll, **kw: calls["incremental"].append(kw))

        def run(*argv):
            monkeypatch.setattr(sys, "argv", ["compute_layout.py", *argv])
            cl.main()
            return calls

        calls["run"] = run
        return calls

    def _save_a_model(self):
        cl.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        cl.UMAP_MODEL_PATH.write_bytes(b"pretend reducer")

    def test_no_saved_model_forces_a_full_fit(self, cli):
        cli["run"]()

        assert len(cli["full_fit"]) == 1
        assert cli["incremental"] == []

    def test_a_saved_model_selects_the_incremental_path(self, cli):
        self._save_a_model()

        cli["run"]()

        assert len(cli["incremental"]) == 1
        assert cli["full_fit"] == []

    def test_full_refit_overrides_a_saved_model(self, cli):
        self._save_a_model()

        cli["run"]("--full-refit")

        assert len(cli["full_fit"]) == 1
        assert cli["incremental"] == []

    def test_cluster_counts_and_limit_reach_full_fit(self, cli):
        cli["run"]("--full-refit", "--broad", "5", "--fine", "25", "--limit", "300")

        assert cli["full_fit"] == [{"broad_k": 5, "fine_k": 25, "limit": 300}]

    def test_defaults_are_the_module_constants(self, cli):
        cli["run"]()

        assert cli["full_fit"] == [{"broad_k": cl.BROAD_K, "fine_k": cl.FINE_K, "limit": None}]

    def test_limit_reaches_the_incremental_path(self, cli):
        self._save_a_model()

        cli["run"]("--limit", "50")

        assert cli["incremental"] == [{"limit": 50}]

    def test_db_argument_selects_the_collection(self, cli, tmp_path):
        cli["run"]("--db", str(tmp_path / "other_db"))

        assert cli["get_collection"] == [str(tmp_path / "other_db")]

    def test_the_default_db_is_the_repo_photo_db(self, cli):
        cli["run"]()

        assert cli["get_collection"] == [str(cl.DEFAULT_DB_PATH)]


class TestGetCollection:
    """It must land on the SAME collection the indexer wrote (embed_photos.py)."""

    def test_opens_the_shared_collection_with_cosine_space(self, monkeypatch, tmp_path):
        seen = {}

        class StubClient:
            def __init__(self, path):
                seen["path"] = path

            def get_or_create_collection(self, name, metadata):
                seen["name"] = name
                seen["metadata"] = metadata
                return "the-collection"

        monkeypatch.setattr(cl, "chromadb", types.SimpleNamespace(PersistentClient=StubClient))

        assert cl.get_collection(str(tmp_path)) == "the-collection"
        assert seen["path"] == str(tmp_path)
        assert seen["name"] == cl.COLLECTION_NAME
        assert seen["metadata"]["hnsw:space"] == "cosine"
