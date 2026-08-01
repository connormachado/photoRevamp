"""
Per-category dismissal ledger
==============================
Backs the junk-cull chips' "hide this one, not here" control (see
backend/dismissed.py). The invariants under test:

* a dismissal is scoped to exactly one category — hiding a photo from
  "blurry" must never affect "dark";
* the ledger survives a missing or corrupt file rather than crashing;
* writes are atomic (temp file + os.replace), like every other ledger in this
  app;
* an invalid category is rejected outright rather than written to disk;
* `search.search_text`'s over-fetch guarantees a full page even with
  dismissals in play.

`dismissed.DISMISSED_PATH` is redirected to tmp_path by the autouse
`isolate_dismissed` fixture in conftest.py, and its in-memory cache is reset
before and after each test.
"""

import json

import pytest

import dismissed
import safe_paths


# ── reading ───────────────────────────────────────────────────────────────────

class TestReading:
    def test_a_missing_file_reads_as_an_empty_ledger(self):
        assert dismissed.get_dismissed() == {}
        assert dismissed.get_dismissed("blurry") == []

    def test_a_corrupt_file_reads_as_empty_rather_than_crashing(self, isolate_dismissed):
        isolate_dismissed.write_text("{not json")
        dismissed.reload()
        assert dismissed.get_dismissed() == {}

    def test_a_non_object_json_file_reads_as_empty(self, isolate_dismissed):
        isolate_dismissed.write_text("[1, 2, 3]")
        dismissed.reload()
        assert dismissed.get_dismissed() == {}

    def test_a_malformed_category_value_is_skipped_not_fatal(self, isolate_dismissed):
        # A category's value must be a list; anything else in a hand-edited
        # file is dropped rather than crashing the whole read.
        isolate_dismissed.write_text(json.dumps({"blurry": ["a", "b"], "dark": "not-a-list"}))
        dismissed.reload()
        assert dismissed.get_dismissed() == {"blurry": ["a", "b"]}


# ── writing ───────────────────────────────────────────────────────────────────

class TestDismissAndRestore:
    def test_dismiss_adds_the_id_to_the_category(self):
        count = dismissed.dismiss("blurry", "abc123")
        assert count == 1
        assert dismissed.get_dismissed("blurry") == ["abc123"]

    def test_dismiss_is_idempotent(self):
        dismissed.dismiss("blurry", "abc123")
        count = dismissed.dismiss("blurry", "abc123")
        assert count == 1

    def test_restore_removes_the_id(self):
        dismissed.dismiss("blurry", "abc123")
        count = dismissed.restore("blurry", "abc123")
        assert count == 0
        assert dismissed.get_dismissed("blurry") == []

    def test_restoring_an_id_that_was_never_dismissed_is_a_no_op(self):
        count = dismissed.restore("blurry", "never-there")
        assert count == 0

    def test_per_category_isolation(self):
        # Dismissing in "blurry" must leave "dark" untouched — the whole
        # point of a per-category ledger rather than one global hide list.
        dismissed.dismiss("blurry", "shared-photo")
        assert dismissed.get_dismissed("blurry") == ["shared-photo"]
        assert dismissed.get_dismissed("dark") == []

    def test_writes_persist_across_a_cache_reload(self, isolate_dismissed):
        dismissed.dismiss("blurry", "abc123")
        dismissed.reload()  # force a re-read from disk, bypassing the cache
        assert dismissed.get_dismissed("blurry") == ["abc123"]

    def test_on_disk_shape_is_a_plain_json_map_of_lists(self, isolate_dismissed):
        dismissed.dismiss("blurry", "abc123")
        dismissed.dismiss("dark", "def456")
        on_disk = json.loads(isolate_dismissed.read_text())
        assert on_disk == {"blurry": ["abc123"], "dark": ["def456"]}

    def test_an_empty_category_after_full_restore_is_dropped_from_disk(self, isolate_dismissed):
        # _persist only writes non-empty categories, so a fully-restored
        # category doesn't linger on disk as an empty list forever.
        dismissed.dismiss("blurry", "abc123")
        dismissed.restore("blurry", "abc123")
        on_disk = json.loads(isolate_dismissed.read_text())
        assert on_disk == {}


class TestAtomicWrite:
    def test_a_failed_write_leaves_no_partial_file(self, isolate_dismissed, monkeypatch):
        import os

        real_replace = os.replace

        def failing_replace(*args, **kwargs):
            raise OSError("simulated crash mid-write")

        monkeypatch.setattr(os, "replace", failing_replace)
        with pytest.raises(OSError):
            dismissed.dismiss("blurry", "abc123")
        monkeypatch.setattr(os, "replace", real_replace)

        # No target file, and no leftover .tmp file in the directory either.
        assert not isolate_dismissed.exists()
        leftovers = list(isolate_dismissed.parent.glob("*.tmp"))
        assert leftovers == []


class TestCategoryValidation:
    @pytest.mark.parametrize("category", [
        "../../etc/passwd", "has space", "UPPERCASE", "", None, 123, [],
        "a" * 41,  # over the 40-char cap
    ])
    def test_an_invalid_category_is_rejected_for_writes(self, category):
        with pytest.raises(ValueError):
            dismissed.dismiss(category, "abc123")
        with pytest.raises(ValueError):
            dismissed.restore(category, "abc123")

    @pytest.mark.parametrize("category", [
        # None is deliberately excluded: get_dismissed(None) is the
        # documented way to fetch the whole map, not an invalid category.
        "../../etc/passwd", "has space", "UPPERCASE", "", 123, [],
        "a" * 41,
    ])
    def test_an_invalid_category_is_rejected_for_reads(self, category):
        with pytest.raises(ValueError):
            dismissed.get_dismissed(category)

    def test_an_invalid_category_never_reaches_disk(self, isolate_dismissed):
        with pytest.raises(ValueError):
            dismissed.dismiss("../escape", "abc123")
        assert not isolate_dismissed.exists()

    @pytest.mark.parametrize("category", ["blurry", "dark_2", "a", "a-b-c", "123"])
    def test_a_valid_category_is_accepted(self, category):
        dismissed.dismiss(category, "abc123")
        assert dismissed.get_dismissed(category) == ["abc123"]


class TestPhotoIdValidation:
    @pytest.mark.parametrize("photo_id", ["../../etc/passwd", "a/b", "a\\b", "", None, ".."])
    def test_an_unsafe_photo_id_is_rejected(self, photo_id):
        with pytest.raises(safe_paths.UnsafePathError):
            dismissed.dismiss("blurry", photo_id)


# ── search-time exclusion ───────────────────────────────────────────────────

class TestSearchOverfetch:
    """search.search_text's exclude_ids over-fetch, exercised against the
    fake Chroma collection so no CLIP model is needed."""

    def _stub_embed(self, monkeypatch):
        import search
        monkeypatch.setattr(search, "embed_text", lambda *a, **k: [0.0])

    def test_a_query_with_dismissals_still_returns_a_full_page(self, fake_chroma, monkeypatch):
        import search

        self._stub_embed(monkeypatch)
        for i in range(10):
            fake_chroma.add_row(f"id{i}", path=f"/lib/{i}.jpg")

        exclude = {"id0", "id1", "id2"}
        results = search.search_text("blurry photo", 5, fake_chroma, None, None, None,
                                     exclude_ids=exclude)
        assert len(results) == 5
        assert all(r["id"] not in exclude for r in results)

    def test_no_exclude_ids_behaves_exactly_as_before(self, fake_chroma, monkeypatch):
        import search

        self._stub_embed(monkeypatch)
        for i in range(5):
            fake_chroma.add_row(f"id{i}", path=f"/lib/{i}.jpg")

        results = search.search_text("blurry photo", 5, fake_chroma, None, None, None)
        assert len(results) == 5

    def test_overfetch_is_capped_rather_than_unbounded(self, fake_chroma, monkeypatch):
        import search

        self._stub_embed(monkeypatch)
        seen_n = {}

        real_query = fake_chroma.query
        def spying_query(n_results=10, **kwargs):
            seen_n["n_results"] = n_results
            return real_query(n_results=n_results, **kwargs)
        monkeypatch.setattr(fake_chroma, "query", spying_query)

        huge_exclude = {f"id{i}" for i in range(5000)}
        search.search_text("blurry photo", 5, fake_chroma, None, None, None,
                           exclude_ids=huge_exclude)
        assert seen_n["n_results"] <= search.OVERFETCH_CAP
