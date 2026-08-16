"""
date_taken has exactly one canonical type, regardless of which writer touched it
==================================================================================
date_taken used to have two writers that disagreed: utils.extract_metadata()
wrote an EXIF string ("2023:07:14 10:22:31") on the rare derivative that still
had one, and backfill_dates.py wrote a Unix int everywhere else. A freshly
indexed photo and a backfilled one would then hold different types in the same
field — which either throws or silently mis-sorts under a numeric range filter
(Time tide).

The fix: extract_metadata() no longer writes date_taken at all, and the only
two writers left — embed_photos.py at index time, backfill_dates.py as a
rerunnable catch-up — both resolve it through the same photo_dates.py join.
These tests pin that there is now exactly one writer-visible shape for the
field, from either code path.
"""

from pathlib import Path

import pytest
from PIL import Image

pytestmark = pytest.mark.slow   # embed_photos imports chromadb + torch (~2s cold)


class TestExtractMetadataNoLongerWritesDateTaken:
    def test_a_real_derivative_gets_no_date_taken_key(self, tmp_path):
        from utils import extract_metadata

        photo = tmp_path / "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE_4_5005_c.jpeg"
        Image.new("RGB", (64, 48), "red").save(photo)

        meta = extract_metadata(photo)

        assert "date_taken" not in meta

    def test_other_metadata_fields_are_unaffected(self, tmp_path):
        """The removal is scoped to date_taken alone — apple_uuid, lat/lon,
        filename, path, and size_kb keep working exactly as before."""
        from utils import extract_metadata

        photo = tmp_path / "12345678-90AB-CDEF-1234-567890ABCDEF_4_5005_c.jpeg"
        Image.new("RGB", (64, 48), "blue").save(photo)

        meta = extract_metadata(photo)

        assert meta["apple_uuid"] == "12345678-90AB-CDEF-1234-567890ABCDEF"
        assert meta["filename"] == photo.name
        assert meta["path"] == str(photo.resolve())
        assert meta["lat"] == ""
        assert meta["lon"] == ""


class TestAttachResolvedDates:
    """embed_photos._attach_resolved_dates is the indexer's half of the join —
    the counterpart to backfill_dates.backfill()'s per-row write."""

    def test_a_matched_uuid_gets_an_int_date(self):
        from embed_photos import _attach_resolved_dates

        metadatas = [{"apple_uuid": "AAA", "path": "/lib/a.jpg"}]
        _attach_resolved_dates(metadatas, {"AAA": 1700000000})

        assert metadatas[0]["date_taken"] == 1700000000
        assert type(metadatas[0]["date_taken"]) is int

    def test_an_unmatched_uuid_gets_no_date_taken_key(self):
        from embed_photos import _attach_resolved_dates

        metadatas = [{"apple_uuid": "NOPE", "path": "/lib/a.jpg"}]
        _attach_resolved_dates(metadatas, {"AAA": 1700000000})

        assert "date_taken" not in metadatas[0]

    def test_a_matched_asset_with_a_null_date_gets_no_date_taken_key(self):
        from embed_photos import _attach_resolved_dates

        metadatas = [{"apple_uuid": "AAA", "path": "/lib/a.jpg"}]
        _attach_resolved_dates(metadatas, {"AAA": None})

        assert "date_taken" not in metadatas[0]

    def test_a_missing_apple_uuid_never_reaches_the_lookup(self):
        """Mirrors backfill_dates.py's falsy-uuid guard: an empty-string ZUUID
        in Photos.sqlite must never date an un-UUID'd row."""
        from embed_photos import _attach_resolved_dates

        metadatas = [{"apple_uuid": "", "path": "/lib/_odd.jpg"}]
        _attach_resolved_dates(metadatas, {"": 1700000000})

        assert "date_taken" not in metadatas[0]

    def test_other_metadata_keys_on_the_row_are_untouched(self):
        from embed_photos import _attach_resolved_dates

        metadatas = [{"apple_uuid": "AAA", "path": "/lib/a.jpg", "lat": "44.1"}]
        _attach_resolved_dates(metadatas, {"AAA": 1700000000})

        assert metadatas[0]["path"] == "/lib/a.jpg"
        assert metadatas[0]["lat"] == "44.1"


class TestDateTakenTypeMatchesAcrossBothWriters:
    def test_the_indexer_and_the_backfill_produce_the_same_type_for_the_same_input(self):
        """embed_photos.py (at index time) and backfill_dates.py (post-hoc) are
        the only two writers of date_taken. Same Core Data input, same output
        type — or a fresh photo and a backfilled one compare unequally under
        Time tide's range filters despite holding "the same" date."""
        from embed_photos import _attach_resolved_dates
        from photo_dates import core_data_to_unix

        computed = core_data_to_unix(711022951)   # an arbitrary real instant

        from_indexer = [{"apple_uuid": "AAA"}]
        _attach_resolved_dates(from_indexer, {"AAA": computed})

        # Mirrors backfill_dates.backfill()'s own write: {**metadata, "date_taken": computed}
        from_backfill = {**{"apple_uuid": "AAA", "date_taken": ""}, "date_taken": computed}

        assert from_indexer[0]["date_taken"] == from_backfill["date_taken"] == computed
        assert type(from_indexer[0]["date_taken"]) is type(from_backfill["date_taken"]) is int
