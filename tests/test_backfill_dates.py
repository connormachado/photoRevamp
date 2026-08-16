"""
Date backfill — the Core Data join, chunked writes, and re-run safety
=====================================================================
`backfill_dates` is a migration: it reads Apple's Photos.sqlite (read-only) and
stamps a `date_taken` Unix timestamp onto every ChromaDB row. Migrations get run
once, at scale, against irreplaceable data, so the properties worth pinning are
the ones whose failure would be *silent*:

* the **Core Data epoch offset** is applied — dropping it shifts the whole
  library back 31 years and every temporal feature inherits the error, which is
  why `prompts/date-backfill-prompt.md` calls it out twice;
* Photos.sqlite is opened **immutable** — Apple's library is read-only for this
  app, and the guard below is behavioural (it reads a WAL database inside a
  directory the process cannot write to, which a plain connection cannot do);
* a row that can't be dated is **counted as a miss**, never written with a null;
* every other metadata key on a row **survives** the update (the `{**meta, ...}`
  spread contract shared with `compute_layout.write_back`);
* a second run is a **no-op** — this script is re-run after every future
  indexing pass.

Both databases are faked: Chroma via conftest's `FakeCollection` (plus the
pagination and `update()` this module needs), Photos.sqlite via a real but
temporary on-disk file. Nothing here may open the user's real Photos library.

Import cost: `backfill_dates` pulls in chromadb and utils -> torch.
"""

import os
import sqlite3
import sys
import types
from datetime import datetime, timezone

import pytest

from conftest import FakeCollection

import backfill_dates as bd

pytestmark = pytest.mark.slow   # imports chromadb + utils -> torch (~2s cold)


# Independently derived reference points — none of them read the module's own
# constant, so a test that agrees with them agrees with the calendar.
UNIX_2023 = int(datetime(2023, 7, 14, 10, 22, 31, tzinfo=timezone.utc).timestamp())
CORE_2023 = 711022951          # the same instant in Core Data seconds
CORE_1999 = -31626000          # 1999-12-31T23:00:00Z — the live library's minimum


def uuid_for(i: int) -> str:
    return f"UUID-{i:04d}"


# ── Test doubles ──────────────────────────────────────────────────────────────

class BackfillCollection(FakeCollection):
    """conftest's `FakeCollection` plus the two surfaces `backfill` uses: real
    limit/offset pagination in `get()`, and `update()`.

    `get()` hands out copies, so "the existing metadata survived" can't be
    satisfied by the caller and the collection sharing one dict.
    """

    def __init__(self):
        super().__init__()
        self.update_calls: list[dict] = []

    def get(self, ids=None, include=None, limit=None, offset=0, **_):
        keys = list(self.rows) if ids is None else [i for i in ids if i in self.rows]
        page = keys[offset:] if limit is None else keys[offset:offset + limit]
        return {
            "ids": list(page),
            "metadatas": [dict(self.rows[i]) for i in page],
            "documents": [None] * len(page),
        }

    def update(self, ids=None, metadatas=None, **_):
        ids, metadatas = list(ids or []), list(metadatas or [])
        assert len(ids) == len(metadatas), "update() got mismatched ids/metadatas"
        self.update_calls.append({"ids": ids, "metadatas": [dict(m) for m in metadatas]})
        for row_id, meta in zip(ids, metadatas):
            self.rows[row_id] = dict(meta)

    # ── query helpers ────────────────────────────────────────────────────────
    @property
    def updated_ids(self) -> list[str]:
        return [i for call in self.update_calls for i in call["ids"]]

    @property
    def written_metadatas(self) -> list[dict]:
        return [m for call in self.update_calls for m in call["metadatas"]]


def make_collection(n, *, prefix="id", **extra):
    """`n` rows carrying an apple_uuid, a path, and an empty date_taken —
    the state `extract_metadata` actually leaves behind on a derivative."""
    coll = BackfillCollection()
    for i in range(n):
        coll.add_row(
            f"{prefix}{i:04d}",
            apple_uuid=uuid_for(i),
            path=f"/lib/photo_{i}.jpg",
            size_kb=70 + i,
            date_taken="",
            **extra,
        )
    return coll


def dates_for(n, first=CORE_2023):
    """{uuid: unix timestamp} for rows 0..n-1, one day apart so a mis-paired
    id/metadata write shows up as a wrong date rather than a coincidence."""
    return {uuid_for(i): bd._core_data_to_unix(first + i * 86400) for i in range(n)}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def photos_db(tmp_path):
    """Builder for a real, temporary SQLite file shaped like the two columns of
    Photos.sqlite this module reads.

    A real file rather than a stub because the *connection* — the immutable URI,
    the missing-file case, the odd path — is what's under test.
    """
    def build(rows=(("AAA", float(CORE_2023)),), *,
              dirname="Photos Library.photoslibrary", wal=False, wide=False):
        folder = tmp_path / dirname / "database"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "Photos.sqlite"
        con = sqlite3.connect(path)
        try:
            if wal:
                con.execute("PRAGMA journal_mode=WAL")
            if wide:
                # Real ZASSET has ~100 columns in an arbitrary order; the read
                # must not depend on ZUUID/ZDATECREATED being first or adjacent.
                con.execute(
                    "CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZDATECREATED FLOAT,"
                    " ZFILENAME TEXT, ZUUID TEXT, ZWIDTH INTEGER)"
                )
                con.executemany(
                    "INSERT INTO ZASSET (ZDATECREATED, ZFILENAME, ZUUID, ZWIDTH)"
                    " VALUES (?, ?, ?, ?)",
                    [(secs, "IMG_0001.HEIC", uuid, 4032) for uuid, secs in rows],
                )
            else:
                con.execute(
                    "CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZUUID TEXT,"
                    " ZDATECREATED FLOAT)"
                )
                con.executemany(
                    "INSERT INTO ZASSET (ZUUID, ZDATECREATED) VALUES (?, ?)", list(rows)
                )
            con.commit()
        finally:
            con.close()
        return path

    return build


@pytest.fixture
def locked_down_dir():
    """chmod a directory read-only for the duration of one test, then restore.

    Restoring in teardown matters: pytest's tmp_path cleanup can't remove a
    directory it has no write permission on.
    """
    restore = []

    def lock(path):
        restore.append((path, os.stat(path).st_mode))
        os.chmod(path, 0o500)

    yield lock
    for path, mode in restore:
        os.chmod(path, mode)


@pytest.fixture
def stub_chroma(monkeypatch):
    """Put a `BackfillCollection` behind `chromadb.PersistentClient`.

    `get_or_create_collection` is a landmine: a migration that creates the
    collection it was supposed to find would report "0 rows" on a typo'd --db
    instead of failing.
    """
    collection = BackfillCollection()
    seen = types.SimpleNamespace(collection=collection, paths=[], names=[])

    class StubClient:
        def __init__(self, path):
            seen.paths.append(path)

        def get_collection(self, name):
            seen.names.append(name)
            return seen.collection

        def get_or_create_collection(self, *args, **kwargs):
            raise AssertionError(
                "backfill used get_or_create_collection — a wrong --db path would "
                "then create an empty collection and report a clean zero-row run"
            )

    monkeypatch.setattr(bd, "chromadb", types.SimpleNamespace(PersistentClient=StubClient))
    return seen


@pytest.fixture
def photos_dates(monkeypatch):
    """Replace the Photos.sqlite read with a canned {uuid: unix|None} map, and
    record the path it was asked for."""
    state = types.SimpleNamespace(map={}, calls=[])

    def fake_load(path):
        state.calls.append(path)
        return dict(state.map)

    monkeypatch.setattr(bd, "_load_uuid_dates", fake_load)
    return state


# ── _core_data_to_unix ────────────────────────────────────────────────────────

class TestCoreDataEpoch:
    """Core Data counts from 2001-01-01, not 1970. Getting this wrong is silent:
    every date is still a plausible date, just 31 years early."""

    def test_the_offset_is_exactly_the_2001_epoch_in_unix_seconds(self):
        # Derived from the calendar, not copied from the module.
        assert bd.CORE_DATA_EPOCH_OFFSET == datetime(2001, 1, 1, tzinfo=timezone.utc).timestamp()

    def test_zero_is_the_first_instant_of_2001(self):
        assert bd._core_data_to_unix(0) == 978307200
        assert datetime.fromtimestamp(bd._core_data_to_unix(0), tz=timezone.utc) == datetime(
            2001, 1, 1, tzinfo=timezone.utc
        )

    def test_a_known_photo_converts_to_its_real_wall_clock_time(self):
        """The tripwire: with the offset dropped this value reads 1992-07-14."""
        converted = bd._core_data_to_unix(CORE_2023)

        assert converted == UNIX_2023
        assert datetime.fromtimestamp(converted, tz=timezone.utc) == datetime(
            2023, 7, 14, 10, 22, 31, tzinfo=timezone.utc
        )

    @pytest.mark.parametrize("year", [1999, 2005, 2014, 2026])
    def test_no_realistic_photo_lands_in_the_pre_1999_past(self, year):
        """`prompts/date-backfill-prompt.md`: a cluster of dates 31 years before
        the real ones is the epoch bug's signature. Pin the whole span the live
        library covers (1999-12-31 -> 2026-07-25), not one sample."""
        instant = datetime(year, 6, 1, 12, 0, tzinfo=timezone.utc)
        core_seconds = instant.timestamp() - 978307200

        converted = bd._core_data_to_unix(core_seconds)

        assert datetime.fromtimestamp(converted, tz=timezone.utc).year == year

    def test_a_pre_2001_photo_keeps_its_date_instead_of_underflowing(self):
        """Core Data goes negative before 2001, and the library's oldest photo
        (1999-12-31) is on that side of the epoch."""
        converted = bd._core_data_to_unix(CORE_1999)

        assert datetime.fromtimestamp(converted, tz=timezone.utc).year == 1999
        assert converted > 0

    def test_the_result_is_a_whole_number_of_seconds(self):
        """Chroma metadata must be JSON-native and Time tide range-filters on it."""
        converted = bd._core_data_to_unix(CORE_2023 + 0.647)

        assert type(converted) is int
        assert abs(converted - (CORE_2023 + 0.647 + 978307200)) <= 0.5

    def test_a_later_core_data_value_is_always_a_later_timestamp(self):
        values = [CORE_1999, -1.0, 0.0, 0.5, CORE_2023, CORE_2023 + 86400]

        converted = [bd._core_data_to_unix(v) for v in values]

        assert converted == sorted(converted)


# ── _load_uuid_dates ──────────────────────────────────────────────────────────

class TestLoadUuidDates:
    """The read side of the join: {ZUUID: unix|None} out of a real SQLite file."""

    def test_returns_a_converted_timestamp_for_each_asset(self, photos_db):
        path = photos_db([("AAA", float(CORE_2023)), ("BBB", float(CORE_1999))])

        assert bd._load_uuid_dates(path) == {"AAA": UNIX_2023, "BBB": bd._core_data_to_unix(CORE_1999)}

    def test_an_asset_with_no_date_maps_to_none_rather_than_disappearing(self, photos_db):
        """The distinction is load-bearing downstream: "asset exists but has no
        date" is a null_date miss, "no such asset" is a no_match miss."""
        path = photos_db([("AAA", float(CORE_2023)), ("NODATE", None)])

        assert bd._load_uuid_dates(path) == {"AAA": UNIX_2023, "NODATE": None}

    def test_an_asset_with_no_uuid_is_excluded(self, photos_db):
        path = photos_db([("AAA", float(CORE_2023)), (None, float(CORE_2023))])

        assert list(bd._load_uuid_dates(path)) == ["AAA"]

    def test_an_empty_table_yields_an_empty_map(self, photos_db):
        assert bd._load_uuid_dates(photos_db([])) == {}

    def test_column_position_and_unrelated_columns_do_not_matter(self, photos_db):
        path = photos_db([("AAA", float(CORE_2023))], wide=True)

        assert bd._load_uuid_dates(path) == {"AAA": UNIX_2023}

    def test_an_integer_valued_date_column_still_converts(self, photos_db):
        """SQLite is dynamically typed — a FLOAT column can hold an INTEGER."""
        path = photos_db([("AAA", CORE_2023)])

        assert bd._load_uuid_dates(path) == {"AAA": UNIX_2023}

    def test_a_library_path_containing_a_space_is_read(self, photos_db):
        """The default library really is ".../Photos Library.photoslibrary"."""
        path = photos_db(dirname="Photos Library.photoslibrary")

        assert bd._load_uuid_dates(path) == {"AAA": UNIX_2023}

    @pytest.mark.parametrize("dirname", ["lib#1.photoslibrary", "lib?.photoslibrary",
                                         "pct%20lib.photoslibrary"])
    def test_a_library_path_needing_uri_escaping_is_read(self, photos_db, dirname):
        path = photos_db(dirname=dirname)

        assert bd._load_uuid_dates(path) == {"AAA": UNIX_2023}


class TestPhotosLibraryIsReadOnly:
    """Apple's library is read-only for this app — the module docstring promises
    the connection "never takes a write lock" even if Photos.app holds it."""

    def test_a_wal_database_is_read_from_a_directory_we_cannot_write_to(
        self, photos_db, locked_down_dir
    ):
        """The behavioural proof that `immutable=1` is doing its job.

        Photos.sqlite is a WAL database, and a normal connection to one must
        create -wal/-shm sidecars before it can read a byte — so a read-only
        directory fails with "attempt to write a readonly database". An
        immutable connection reads it anyway. Drop the flag and this goes red.
        """
        if os.geteuid() == 0:
            pytest.skip("running as root bypasses the directory permission bits")
        path = photos_db([("AAA", float(CORE_2023))], wal=True)
        locked_down_dir(path.parent)

        assert bd._load_uuid_dates(path) == {"AAA": UNIX_2023}

    def test_reading_leaves_no_sidecar_files_beside_the_database(self, photos_db):
        path = photos_db([("AAA", float(CORE_2023))], wal=True)
        before = sorted(p.name for p in path.parent.iterdir())

        bd._load_uuid_dates(path)

        assert sorted(p.name for p in path.parent.iterdir()) == before

    def test_reading_does_not_modify_the_database_file(self, photos_db):
        path = photos_db([("AAA", float(CORE_2023))])
        before = path.read_bytes()

        bd._load_uuid_dates(path)

        assert path.read_bytes() == before

    def test_a_missing_database_raises_instead_of_reporting_no_dates(self, tmp_path):
        """Silently returning {} would turn a misconfigured library root into
        "56,612 rows had no match" — a data problem the operator would chase."""
        with pytest.raises(sqlite3.Error):
            bd._load_uuid_dates(tmp_path / "nowhere" / "Photos.sqlite")

    def test_a_file_that_is_not_a_database_raises(self, tmp_path):
        bogus = tmp_path / "Photos.sqlite"
        bogus.write_bytes(b"this is not a database")

        with pytest.raises(sqlite3.Error):
            bd._load_uuid_dates(bogus)


class TestPhotosSqlitePath:
    """The library root is user-configurable, so the path must be derived, not
    hardcoded (`prompts/date-backfill-prompt.md`: no username in a path)."""

    def test_the_path_sits_under_the_library_root(self):
        import config_store

        assert bd._photos_sqlite_path() == (
            config_store.get_library_root() / "database" / "Photos.sqlite"
        )

    def test_a_reconfigured_library_root_is_honoured(self, tmp_path):
        import config_store

        config_store.set("library_root", str(tmp_path / "Elsewhere.photoslibrary"))

        assert bd._photos_sqlite_path() == (
            tmp_path / "Elsewhere.photoslibrary" / "database" / "Photos.sqlite"
        )


# ── backfill: the happy path ──────────────────────────────────────────────────

class TestBackfillWrites:
    def test_every_matched_row_gets_its_own_date(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(4)
        photos_dates.map = dates_for(4)

        report = bd.backfill(db_path="/tmp/db", write=True)

        for i in range(4):
            assert stub_chroma.collection.rows[f"id{i:04d}"]["date_taken"] == (
                photos_dates.map[uuid_for(i)]
            )
        assert (report["total"], report["written"], report["skipped"]) == (4, 4, 0)

    def test_the_id_written_matches_the_metadata_written(self, stub_chroma, photos_dates):
        """Mis-pairing ids against metadatas is the classic chunked-update bug,
        and it is invisible in the report — every row still gets *a* date."""
        stub_chroma.collection = make_collection(3)
        photos_dates.map = dates_for(3)

        bd.backfill(db_path="/tmp/db", write=True)

        for call in stub_chroma.collection.update_calls:
            for row_id, meta in zip(call["ids"], call["metadatas"]):
                assert meta["date_taken"] == photos_dates.map[meta["apple_uuid"]]
                assert meta["path"] == f"/lib/photo_{int(row_id[2:])}.jpg"

    def test_every_other_metadata_key_survives_the_update(self, stub_chroma, photos_dates):
        """The `{**metadata, ...}` spread contract — a migration that drops the
        layout coordinates or the path would take the whole app down with it."""
        stub_chroma.collection = make_collection(2, lat="44.1", x=1.5, y=-2.5,
                                                 cluster_id_broad=3)
        photos_dates.map = dates_for(2)

        bd.backfill(db_path="/tmp/db", write=True)

        row = stub_chroma.collection.rows["id0001"]
        assert row["path"] == "/lib/photo_1.jpg"
        assert row["size_kb"] == 71
        assert row["apple_uuid"] == uuid_for(1)
        assert (row["lat"], row["x"], row["y"], row["cluster_id_broad"]) == ("44.1", 1.5, -2.5, 3)

    def test_date_taken_is_the_only_key_that_changes(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(3, lat="44.1", lon="-72.5")
        photos_dates.map = dates_for(3)
        before = {i: dict(row) for i, row in stub_chroma.collection.rows.items()}

        bd.backfill(db_path="/tmp/db", write=True)

        for row_id, row in stub_chroma.collection.rows.items():
            changed = {k for k in row if row[k] != before[row_id].get(k)}
            assert changed == {"date_taken"}

    def test_the_stored_value_is_a_plain_int(self, stub_chroma, photos_dates):
        """Chroma metadata is JSON-native; Time tide range-filters on this."""
        stub_chroma.collection = make_collection(1)
        photos_dates.map = dates_for(1)

        bd.backfill(db_path="/tmp/db", write=True)

        assert type(stub_chroma.collection.rows["id0000"]["date_taken"]) is int

    def test_a_legacy_exif_string_date_is_replaced_by_the_timestamp(
        self, stub_chroma, photos_dates
    ):
        """A handful of rows were indexed from a derivative that still had EXIF,
        so their date_taken is "2023:07:14 10:22:31". The frontend reads
        date_taken as Unix seconds (App.jsx formatDateTaken), so the backfill
        normalises those rows rather than leaving two formats in one column."""
        coll = BackfillCollection()
        coll.add_row("legacy", apple_uuid=uuid_for(0), path="/lib/legacy.jpg",
                     date_taken="2023:07:14 10:22:31")
        stub_chroma.collection = coll
        photos_dates.map = dates_for(1)

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert coll.rows["legacy"]["date_taken"] == photos_dates.map[uuid_for(0)]
        assert report["written"] == 1

    def test_a_row_missing_date_taken_entirely_is_written(self, stub_chroma, photos_dates):
        coll = BackfillCollection()
        coll.add_row("bare", apple_uuid=uuid_for(0), path="/lib/bare.jpg")
        stub_chroma.collection = coll
        photos_dates.map = dates_for(1)

        bd.backfill(db_path="/tmp/db", write=True)

        assert coll.rows["bare"]["date_taken"] == photos_dates.map[uuid_for(0)]


# ── backfill: rows that cannot be dated ───────────────────────────────────────

class TestBackfillMisses:
    """`prompts/date-backfill-prompt.md` step 4: misses are counted and reported,
    never written as a null and never silently skipped."""

    def test_a_row_with_no_matching_asset_is_counted_and_left_alone(
        self, stub_chroma, photos_dates
    ):
        stub_chroma.collection = make_collection(3)
        photos_dates.map = {uuid_for(0): UNIX_2023}      # rows 1 and 2 have no asset

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["misses"] == {"no_match": 2}
        assert stub_chroma.collection.updated_ids == ["id0000"]
        assert stub_chroma.collection.rows["id0001"]["date_taken"] == ""

    def test_an_asset_with_a_null_date_is_counted_separately(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(3)
        photos_dates.map = {uuid_for(0): UNIX_2023, uuid_for(1): None, uuid_for(2): None}

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["misses"] == {"null_date": 2}
        assert stub_chroma.collection.updated_ids == ["id0000"]

    def test_the_two_miss_reasons_are_reported_apart(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(3)
        photos_dates.map = {uuid_for(0): UNIX_2023, uuid_for(1): None}

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["misses"] == {"no_match": 1, "null_date": 1}

    def test_no_update_ever_carries_a_null_date(self, stub_chroma, photos_dates):
        """Chroma rejects a None metadata value, so a null would abort the
        migration partway through a rewrite of the live library."""
        stub_chroma.collection = make_collection(6)
        photos_dates.map = {uuid_for(i): (None if i % 2 else UNIX_2023) for i in range(6)}

        bd.backfill(db_path="/tmp/db", write=True)

        for meta in stub_chroma.collection.written_metadatas:
            assert None not in meta.values()

    def test_a_row_whose_metadata_is_missing_entirely_is_a_miss_not_a_crash(
        self, stub_chroma, photos_dates
    ):
        class NoMetadataCollection(BackfillCollection):
            def get(self, **kwargs):
                batch = super().get(**kwargs)
                batch["metadatas"] = [None] * len(batch["ids"])
                return batch

        coll = NoMetadataCollection()
        coll.add_row("ghost", apple_uuid=uuid_for(0))
        stub_chroma.collection = coll
        photos_dates.map = dates_for(1)

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["misses"] == {"no_match": 1}
        assert coll.update_calls == []

    def test_a_row_without_an_apple_uuid_is_a_miss(self, stub_chroma, photos_dates):
        coll = BackfillCollection()
        coll.add_row("no_uuid", path="/lib/_odd.jpg", date_taken="")
        stub_chroma.collection = coll
        photos_dates.map = {uuid_for(0): UNIX_2023}

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["misses"] == {"no_match": 1}
        assert coll.update_calls == []

    def test_a_row_without_an_apple_uuid_is_never_dated_from_an_empty_uuid_asset(
        self, stub_chroma, photos_dates
    ):
        coll = BackfillCollection()
        coll.add_row("no_uuid", path="/lib/_odd.jpg", date_taken="")
        stub_chroma.collection = coll
        photos_dates.map = {"": UNIX_2023, uuid_for(0): UNIX_2023}

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert coll.rows["no_uuid"]["date_taken"] == ""
        assert report["misses"] == {"no_match": 1}


# ── backfill: chunking ────────────────────────────────────────────────────────

class TestBackfillChunking:
    """5,000 rows per update is the repo's documented bulk limit — exceeding it
    trips SQLite's bound-variable ceiling partway through the migration."""

    def test_the_chunk_size_stays_within_the_documented_bulk_limit(self):
        assert bd.CHUNK_SIZE <= 5000

    def test_no_single_update_exceeds_the_real_chunk_size(self, stub_chroma, photos_dates):
        n = bd.CHUNK_SIZE + 1
        stub_chroma.collection = make_collection(n)
        photos_dates.map = dates_for(n)

        bd.backfill(db_path="/tmp/db", write=True)

        assert len(stub_chroma.collection.update_calls) == 2
        assert all(len(c["ids"]) <= bd.CHUNK_SIZE for c in stub_chroma.collection.update_calls)

    def test_every_row_across_page_boundaries_is_written_exactly_once(
        self, stub_chroma, photos_dates, monkeypatch
    ):
        monkeypatch.setattr(bd, "CHUNK_SIZE", 5)       # 23 rows => 5 uneven pages
        stub_chroma.collection = make_collection(23)
        photos_dates.map = dates_for(23)

        report = bd.backfill(db_path="/tmp/db", write=True)

        written = stub_chroma.collection.updated_ids
        assert sorted(written) == sorted(stub_chroma.collection.rows)
        assert len(written) == len(set(written)) == 23
        assert report["total"] == 23

    def test_pagination_does_not_stop_at_a_page_of_pure_misses(
        self, stub_chroma, photos_dates, monkeypatch
    ):
        """A page where nothing matches issues no update — the loop must keep
        reading anyway, or half the library never gets a date."""
        monkeypatch.setattr(bd, "CHUNK_SIZE", 4)
        stub_chroma.collection = make_collection(12)
        photos_dates.map = {uuid_for(i): UNIX_2023 for i in (0, 1, 10, 11)}

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["total"] == 12
        assert sorted(stub_chroma.collection.updated_ids) == [
            "id0000", "id0001", "id0010", "id0011"
        ]

    def test_the_photos_database_is_read_once_not_per_page(
        self, stub_chroma, photos_dates, monkeypatch
    ):
        """Re-reading 56k assets per page would make the run quadratic."""
        monkeypatch.setattr(bd, "CHUNK_SIZE", 2)
        stub_chroma.collection = make_collection(9)
        photos_dates.map = dates_for(9)

        bd.backfill(db_path="/tmp/db", write=True)

        assert len(photos_dates.calls) == 1


# ── backfill: dry run ─────────────────────────────────────────────────────────

class TestBackfillDryRun:
    """The default is a dry run: it reports what a write would do, and writes
    nothing."""

    def test_the_default_call_writes_nothing(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(3)
        photos_dates.map = dates_for(3)

        report = bd.backfill(db_path="/tmp/db")

        assert stub_chroma.collection.update_calls == []
        assert report["dry_run"] is True

    def test_no_row_is_touched_by_a_dry_run(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(3)
        photos_dates.map = dates_for(3)
        before = {i: dict(row) for i, row in stub_chroma.collection.rows.items()}

        bd.backfill(db_path="/tmp/db", write=False)

        assert {i: dict(r) for i, r in stub_chroma.collection.rows.items()} == before

    def test_a_dry_run_reports_the_same_counts_the_write_would_produce(
        self, stub_chroma, photos_dates
    ):
        """Otherwise the dry run is not a preview of anything."""
        photos_dates.map = {uuid_for(i): (None if i == 1 else UNIX_2023 + i) for i in range(4)}
        stub_chroma.collection = make_collection(4)
        del photos_dates.map[uuid_for(3)]                 # one no_match too

        dry = bd.backfill(db_path="/tmp/db", write=False)
        stub_chroma.collection = make_collection(4)       # same library, fresh state
        wet = bd.backfill(db_path="/tmp/db", write=True)

        assert dry["written"] == wet["written"] == 2
        assert dry["misses"] == wet["misses"] == {"null_date": 1, "no_match": 1}
        assert dry["total"] == wet["total"]

    def test_a_dry_run_flags_itself_and_a_write_does_not(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(1)
        photos_dates.map = dates_for(1)

        assert bd.backfill(db_path="/tmp/db", write=False)["dry_run"] is True
        stub_chroma.collection = make_collection(1)
        assert bd.backfill(db_path="/tmp/db", write=True)["dry_run"] is False


# ── backfill: re-run safety ───────────────────────────────────────────────────

class TestBackfillIsRerunnable:
    """This script is re-run after every future indexing pass (module docstring),
    so a second run must be a no-op — not a second rewrite of 56k rows."""

    def test_a_second_run_writes_nothing_and_reports_everything_skipped(
        self, stub_chroma, photos_dates
    ):
        stub_chroma.collection = make_collection(5)
        photos_dates.map = dates_for(5)

        first = bd.backfill(db_path="/tmp/db", write=True)
        stub_chroma.collection.update_calls.clear()
        second = bd.backfill(db_path="/tmp/db", write=True)

        assert (first["written"], first["skipped"]) == (5, 0)
        assert (second["written"], second["skipped"]) == (0, 5)
        assert stub_chroma.collection.update_calls == []

    def test_a_second_run_leaves_the_metadata_byte_identical(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(5, lat="44.1")
        photos_dates.map = dates_for(5)

        bd.backfill(db_path="/tmp/db", write=True)
        after_first = {i: dict(r) for i, r in stub_chroma.collection.rows.items()}
        bd.backfill(db_path="/tmp/db", write=True)

        assert {i: dict(r) for i, r in stub_chroma.collection.rows.items()} == after_first

    def test_a_rerun_picks_up_rows_added_since_the_last_run(self, stub_chroma, photos_dates):
        """The reason skipping is by value rather than by "has any date": a later
        indexing pass adds rows whose date_taken starts empty."""
        stub_chroma.collection = make_collection(3)
        photos_dates.map = dates_for(5)

        bd.backfill(db_path="/tmp/db", write=True)
        for i in (3, 4):
            stub_chroma.collection.add_row(f"id{i:04d}", apple_uuid=uuid_for(i),
                                           path=f"/lib/photo_{i}.jpg", date_taken="")
        stub_chroma.collection.update_calls.clear()
        second = bd.backfill(db_path="/tmp/db", write=True)

        assert (second["written"], second["skipped"]) == (2, 3)
        assert stub_chroma.collection.updated_ids == ["id0003", "id0004"]

    def test_a_row_whose_date_changed_in_photos_is_rewritten(self, stub_chroma, photos_dates):
        """Editing a photo's date in Photos.app must propagate on the next run."""
        stub_chroma.collection = make_collection(2)
        photos_dates.map = dates_for(2)

        bd.backfill(db_path="/tmp/db", write=True)
        photos_dates.map[uuid_for(1)] = UNIX_2023 + 999
        stub_chroma.collection.update_calls.clear()
        second = bd.backfill(db_path="/tmp/db", write=True)

        assert stub_chroma.collection.updated_ids == ["id0001"]
        assert stub_chroma.collection.rows["id0001"]["date_taken"] == UNIX_2023 + 999
        assert (second["written"], second["skipped"]) == (1, 1)

    def test_a_dry_run_after_a_write_reports_nothing_left_to_do(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(4)
        photos_dates.map = dates_for(4)

        bd.backfill(db_path="/tmp/db", write=True)
        report = bd.backfill(db_path="/tmp/db")

        assert report["written"] == 0
        assert report["skipped"] == 4


# ── backfill: the report ──────────────────────────────────────────────────────

class TestBackfillReport:
    """The report is the only output of a dry run, and the operator's evidence
    that the epoch conversion worked."""

    def test_every_row_is_accounted_for_exactly_once(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(10)
        photos_dates.map = {uuid_for(i): (None if i in (2, 3) else UNIX_2023 + i)
                            for i in range(7)}          # rows 7..9 have no asset

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["total"] == 10
        assert report["written"] + report["skipped"] + sum(report["misses"].values()) == 10

    def test_the_date_range_spans_the_dated_rows(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(4)
        photos_dates.map = dates_for(4)

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["min_date"] == min(photos_dates.map.values())
        assert report["max_date"] == max(photos_dates.map.values())

    def test_undatable_rows_do_not_drag_the_range(self, stub_chroma, photos_dates):
        stub_chroma.collection = make_collection(3)
        photos_dates.map = {uuid_for(0): UNIX_2023, uuid_for(1): None}

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["min_date"] == report["max_date"] == UNIX_2023

    def test_the_histogram_counts_one_year_per_dated_row_and_is_ordered(
        self, stub_chroma, photos_dates
    ):
        stub_chroma.collection = make_collection(4)
        photos_dates.map = {
            uuid_for(0): int(datetime(2019, 5, 1, tzinfo=timezone.utc).timestamp()),
            uuid_for(1): int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp()),
            uuid_for(2): int(datetime(2019, 9, 9, tzinfo=timezone.utc).timestamp()),
            uuid_for(3): None,
        }

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["year_counts"] == {2019: 2, 2024: 1}
        assert list(report["year_counts"]) == sorted(report["year_counts"])

    def test_the_report_describes_the_library_not_just_the_delta(
        self, stub_chroma, photos_dates
    ):
        """A re-run writes nothing, but its range and histogram must still cover
        every dated row — that is the sanity check the operator reads."""
        stub_chroma.collection = make_collection(4)
        photos_dates.map = dates_for(4)

        first = bd.backfill(db_path="/tmp/db", write=True)
        second = bd.backfill(db_path="/tmp/db", write=True)

        assert second["year_counts"] == first["year_counts"]
        assert (second["min_date"], second["max_date"]) == (first["min_date"], first["max_date"])

    def test_an_empty_collection_reports_zeroes_rather_than_raising(
        self, stub_chroma, photos_dates
    ):
        photos_dates.map = dates_for(3)

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["total"] == report["written"] == report["skipped"] == 0
        assert report["misses"] == {}
        assert report["min_date"] is None and report["max_date"] is None
        assert stub_chroma.collection.update_calls == []

    def test_when_nothing_matches_there_is_no_date_range_to_report(
        self, stub_chroma, photos_dates
    ):
        stub_chroma.collection = make_collection(3)
        photos_dates.map = {}

        report = bd.backfill(db_path="/tmp/db", write=True)

        assert report["misses"] == {"no_match": 3}
        assert report["min_date"] is None and report["max_date"] is None
        assert report["year_counts"] == {}


# ── backfill: the two database seams ──────────────────────────────────────────

class TestBackfillSeams:
    def test_the_collection_is_opened_at_the_requested_db_path(
        self, stub_chroma, photos_dates, tmp_path
    ):
        bd.backfill(db_path=str(tmp_path / "other_db"), write=False)

        assert stub_chroma.paths == [str(tmp_path / "other_db")]

    def test_it_opens_the_collection_the_indexer_wrote(self, stub_chroma, photos_dates):
        from utils import COLLECTION_NAME

        bd.backfill(db_path="/tmp/db", write=False)

        assert stub_chroma.names == [COLLECTION_NAME]

    def test_photos_sqlite_is_read_from_the_configured_library(
        self, stub_chroma, photos_dates
    ):
        bd.backfill(db_path="/tmp/db", write=False)

        assert photos_dates.calls == [bd._photos_sqlite_path()]

    def test_an_unreadable_photos_library_aborts_before_any_write(
        self, stub_chroma, monkeypatch
    ):
        """Failing loudly beats writing half a library from a partial map."""
        stub_chroma.collection = make_collection(3)

        def boom(path):
            raise sqlite3.OperationalError("unable to open database file")

        monkeypatch.setattr(bd, "_load_uuid_dates", boom)

        with pytest.raises(sqlite3.Error):
            bd.backfill(db_path="/tmp/db", write=True)
        assert stub_chroma.collection.update_calls == []


# ── _print_report ─────────────────────────────────────────────────────────────

class TestPrintReport:
    """The operator's read-out. `prompts/date-backfill-prompt.md` step 4: a
    non-zero miss count must be visible, not buried."""

    @staticmethod
    def _report(**overrides):
        report = {
            "total": 10, "written": 7, "skipped": 1,
            "misses": {"no_match": 1, "null_date": 1},
            "min_date": UNIX_2023, "max_date": UNIX_2023 + 86400,
            "year_counts": {2023: 8}, "dry_run": True,
        }
        report.update(overrides)
        return report

    def test_a_dry_run_says_no_changes_were_made(self, capsys):
        bd._print_report(self._report(dry_run=True))

        out = capsys.readouterr().out
        assert "DRY RUN" in out and "no changes made" in out

    def test_a_write_run_is_labelled_write(self, capsys):
        bd._print_report(self._report(dry_run=False))

        out = capsys.readouterr().out
        assert "WRITE" in out and "DRY RUN" not in out

    def test_each_miss_reason_is_named_with_its_count(self, capsys):
        bd._print_report(self._report())

        out = capsys.readouterr().out
        assert "no_match: 1" in out
        assert "null_date: 1" in out

    def test_a_non_zero_miss_count_is_called_out(self, capsys):
        bd._print_report(self._report())

        alarm = [ln for ln in capsys.readouterr().out.splitlines() if "no usable date" in ln]
        assert len(alarm) == 1
        assert "2" in alarm[0]                 # the summed miss count, not a reason

    def test_a_clean_run_raises_no_alarm(self, capsys):
        bd._print_report(self._report(misses={}))

        assert "no usable date" not in capsys.readouterr().out

    def test_the_date_range_is_printed_as_readable_iso(self, capsys):
        bd._print_report(self._report())

        out = capsys.readouterr().out
        assert "2023-07-14T10:22:31" in out
        assert "2023: 8" in out

    def test_a_run_with_no_dated_rows_prints_no_range_and_does_not_crash(self, capsys):
        bd._print_report(self._report(min_date=None, max_date=None, year_counts={},
                                      written=0, skipped=0, misses={"no_match": 10}))

        out = capsys.readouterr().out
        assert "Date range" not in out
        assert "no_match: 10" in out


# ── CLI ───────────────────────────────────────────────────────────────────────

class TestCli:
    """--write is the only thing standing between a report and 56k rewrites."""

    @pytest.fixture
    def cli(self, monkeypatch):
        calls = []

        def fake_backfill(db_path, write):
            calls.append({"db_path": db_path, "write": write})
            return {
                "total": 1, "written": 0, "skipped": 1, "misses": {},
                "min_date": UNIX_2023, "max_date": UNIX_2023,
                "year_counts": {2023: 1}, "dry_run": not write,
            }

        monkeypatch.setattr(bd, "backfill", fake_backfill)

        def run(*argv):
            monkeypatch.setattr(sys, "argv", ["backfill_dates.py", *argv])
            bd.main()
            return calls

        return run

    def test_the_default_invocation_is_a_dry_run(self, cli, capsys):
        assert cli()[0]["write"] is False

    def test_write_opts_into_the_real_thing(self, cli, capsys):
        assert cli("--write")[0]["write"] is True

    def test_the_default_db_is_the_repo_photo_db(self, cli, capsys):
        from utils import DEFAULT_DB_PATH

        assert cli()[0]["db_path"] == str(DEFAULT_DB_PATH)

    def test_db_selects_another_chroma_directory(self, cli, tmp_path, capsys):
        assert cli("--db", str(tmp_path / "other"))[0]["db_path"] == str(tmp_path / "other")

    def test_the_report_is_printed(self, cli, capsys):
        cli()

        assert "Date backfill report" in capsys.readouterr().out
