"""
Shared key/value app-settings store
====================================
`config_store` is the one general, persisted settings dict (`photo_db/config.json`).
Its contract is stated in the module docstring, and these tests assert that
contract rather than re-deriving the current code:

* **reads never write.** `load()`/`get()` must fall back to in-memory defaults on
  a missing or corrupt file — without raising, without creating the file, and
  without creating its parent directory. This is the load-bearing one:
  `get_library_root()` runs at IMPORT time in safe_paths/embed_job/video_motion,
  so a write-on-read drops a file into the live `photo_db/` the first time
  anything imports them (including pytest collection, before any fixture has
  redirected CONFIG_PATH).
* **`set()` round-trips the FULL on-disk dict**, so a key this module has never
  heard of — hand-edited in, or written by a future caller — survives a write
  that touches a different key.
* **writes are atomic** (temp file + `os.replace`): an interrupted write leaves
  the previous config intact and no `.tmp` debris behind.
* **`ensure_seeded()` is the only other writer**, and is idempotent.
* **`validate_library_root()` is pure inspection**, changing nothing on disk.

`config_store.CONFIG_PATH` is redirected into tmp_path for every test by the
autouse `isolate_config_store` fixture in conftest.py; tests that assert on the
file itself request it by name for readability.

Slow: config_store imports utils, which pulls torch in at module scope.
"""

import json
import os
import threading
from pathlib import Path

import pytest

import config_store

pytestmark = pytest.mark.slow


# The default this module is documented to detect — spelled out from its
# ingredients rather than read off `config_store._DEFAULT_LIBRARY_ROOT`, so the
# test still means something if that constant is edited.
EXPECTED_DEFAULT_ROOT = Path.home() / "Pictures" / "Photos Library.photoslibrary"


def fake_library(tmp_path, *, derivatives=True, originals=True, name="lib.photoslibrary"):
    """Build a stand-in Photos library bundle with either subtree present or absent."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    if derivatives:
        (root / "resources" / "derivatives").mkdir(parents=True, exist_ok=True)
    if originals:
        (root / "originals").mkdir(parents=True, exist_ok=True)
    return root


CORRUPT_BODIES = [
    pytest.param("{not json", id="truncated-garbage"),
    pytest.param("", id="empty-file"),
    pytest.param("   \n ", id="whitespace-only"),
    pytest.param('{"library_root": "/x",', id="truncated-mid-write"),
    pytest.param("[1, 2, 3]", id="json-list-not-object"),
    pytest.param("null", id="json-null"),
    pytest.param('"just a string"', id="json-string"),
    pytest.param("42", id="json-number"),
]


# ── the headline invariant: reading never touches disk ────────────────────────

class TestReadsNeverWrite:
    """`load()`/`get()` are read-only. Breaking this drops a file into the
    user's live photo_db/ the moment safe_paths is imported."""

    def test_a_missing_file_is_not_created_by_any_read(self, isolate_config_store):
        config_store.load()
        config_store.get("library_root")
        config_store.get("nope", "fallback")
        config_store.get_library_root()
        config_store.validate_library_root()

        assert not isolate_config_store.exists()
        assert list(isolate_config_store.parent.glob("*.tmp")) == []

    def test_reading_does_not_create_the_parent_directory_either(self, tmp_path, monkeypatch):
        # The real CONFIG_PATH lives under photo_db/, which does not exist in a
        # fresh clone. A read must not conjure it.
        nested = tmp_path / "photo_db" / "config.json"
        monkeypatch.setattr(config_store, "CONFIG_PATH", nested)

        config_store.load()
        config_store.get_library_root()

        assert not nested.parent.exists()

    @pytest.mark.parametrize("body", CORRUPT_BODIES)
    def test_a_corrupt_file_is_never_repaired_or_overwritten_by_a_read(
        self, isolate_config_store, body
    ):
        isolate_config_store.write_text(body)
        config_store.load()
        config_store.get_library_root()
        assert isolate_config_store.read_text() == body

    def test_a_valid_file_is_not_rewritten_by_a_read(self, isolate_config_store):
        body = json.dumps({"schema_version": 1, "library_root": "/somewhere"})
        isolate_config_store.write_text(body)
        before = isolate_config_store.stat().st_mtime_ns

        config_store.load()
        config_store.get("library_root")

        assert isolate_config_store.read_text() == body
        assert isolate_config_store.stat().st_mtime_ns == before


# ── defaults ──────────────────────────────────────────────────────────────────

class TestDefaults:
    def test_a_missing_file_yields_the_detected_defaults(self):
        data = config_store.load()
        assert data["schema_version"] == config_store.SCHEMA_VERSION
        assert Path(data["library_root"]) == EXPECTED_DEFAULT_ROOT

    @pytest.mark.parametrize("body", CORRUPT_BODIES)
    def test_a_corrupt_file_falls_back_exactly_like_a_missing_one(
        self, isolate_config_store, body
    ):
        isolate_config_store.write_text(body)
        assert config_store.load() == {
            "schema_version": config_store.SCHEMA_VERSION,
            "library_root": str(EXPECTED_DEFAULT_ROOT),
        }

    def test_a_partial_file_gets_the_missing_keys_from_defaults(self, isolate_config_store):
        isolate_config_store.write_text(json.dumps({"library_root": "/custom/lib"}))
        data = config_store.load()
        assert data["library_root"] == "/custom/lib"
        assert data["schema_version"] == config_store.SCHEMA_VERSION

    def test_the_on_disk_value_wins_over_the_default(self, isolate_config_store):
        # There is no migration step; a future schema_version read back must be
        # reported as it is on disk, not silently reset to the current one.
        isolate_config_store.write_text(json.dumps({"schema_version": 99}))
        assert config_store.load()["schema_version"] == 99

    def test_mutating_a_loaded_dict_cannot_poison_the_next_load(self):
        # load() must hand out a copy — otherwise one caller's edit becomes
        # every later caller's "default", process-wide.
        first = config_store.load()
        first["library_root"] = "/hijacked"
        first["injected"] = True

        second = config_store.load()
        assert second["library_root"] != "/hijacked"
        assert "injected" not in second


class TestGet:
    def test_get_returns_the_stored_value(self, isolate_config_store):
        isolate_config_store.write_text(json.dumps({"library_root": "/custom/lib"}))
        assert config_store.get("library_root") == "/custom/lib"

    def test_an_unknown_key_returns_the_caller_default(self):
        assert config_store.get("no_such_key", "fallback") == "fallback"

    def test_an_unknown_key_with_no_default_is_none(self):
        assert config_store.get("no_such_key") is None

    def test_a_stored_falsy_value_is_returned_rather_than_the_default(
        self, isolate_config_store
    ):
        # `get` must distinguish "absent" from "present but falsy" — a stored
        # False/0/"" is a real setting, not a missing one.
        isolate_config_store.write_text(json.dumps({"flag": False, "count": 0, "text": ""}))
        assert config_store.get("flag", "DEFAULT") is False
        assert config_store.get("count", "DEFAULT") == 0
        assert config_store.get("text", "DEFAULT") == ""


# ── set(): the only writer ────────────────────────────────────────────────────

class TestSet:
    @pytest.mark.parametrize("value", [
        "a string", 42, 3.5, True, False, None, 0, "",
        ["a", "b"], {"nested": {"deep": 1}}, [],
    ])
    def test_a_json_representable_value_round_trips(self, value):
        # The API is deliberately an opaque get/set, so anything JSON can carry
        # must come back out of a fresh read unchanged.
        config_store.set("some_setting", value)
        assert config_store.get("some_setting") == value

    def test_set_returns_what_a_later_load_sees(self):
        returned = config_store.set("some_setting", {"a": [1, 2]})
        assert returned == config_store.load()

    def test_the_value_lands_on_disk_not_just_in_memory(self, isolate_config_store):
        config_store.set("library_root", "/custom/lib")
        assert json.loads(isolate_config_store.read_text())["library_root"] == "/custom/lib"

    def test_schema_version_is_written_even_when_setting_another_key(
        self, isolate_config_store
    ):
        config_store.set("library_root", "/custom/lib")
        on_disk = json.loads(isolate_config_store.read_text())
        assert on_disk["schema_version"] == config_store.SCHEMA_VERSION

    def test_a_hand_written_unknown_key_survives_a_set_of_another_key(
        self, isolate_config_store
    ):
        # The documented reason set() round-trips the FULL dict via load():
        # a key from a future caller (or a hand edit) must not be filtered out.
        isolate_config_store.write_text(json.dumps({
            "schema_version": config_store.SCHEMA_VERSION,
            "library_root": "/original/lib",
            "future_feature_enabled": True,
            "future_nested": {"threshold": 7},
        }))

        config_store.set("library_root", "/new/lib")

        on_disk = json.loads(isolate_config_store.read_text())
        assert on_disk["future_feature_enabled"] is True
        assert on_disk["future_nested"] == {"threshold": 7}
        assert on_disk["library_root"] == "/new/lib"

    def test_a_falsy_unknown_key_survives_too(self, isolate_config_store):
        # A truthiness test somewhere in the round-trip would drop these.
        isolate_config_store.write_text(json.dumps({"zero": 0, "blank": "", "off": False}))
        config_store.set("library_root", "/new/lib")

        on_disk = json.loads(isolate_config_store.read_text())
        assert on_disk["zero"] == 0
        assert on_disk["blank"] == ""
        assert on_disk["off"] is False

    def test_repeated_sets_accumulate_rather_than_replace(self):
        config_store.set("first", 1)
        config_store.set("second", 2)
        config_store.set("third", 3)
        data = config_store.load()
        assert (data["first"], data["second"], data["third"]) == (1, 2, 3)

    def test_setting_the_same_key_twice_is_idempotent_on_disk(self, isolate_config_store):
        config_store.set("library_root", "/custom/lib")
        first = isolate_config_store.read_text()
        config_store.set("library_root", "/custom/lib")
        assert isolate_config_store.read_text() == first

    def test_a_later_set_overwrites_only_its_own_key(self):
        config_store.set("keep_me", "untouched")
        config_store.set("library_root", "/new/lib")
        assert config_store.get("keep_me") == "untouched"

    def test_set_recovers_from_a_corrupt_file(self, isolate_config_store):
        # The corrupt bytes are unreadable anyway; a write must still succeed
        # and leave a file that parses.
        isolate_config_store.write_text("{not json")
        config_store.set("library_root", "/custom/lib")

        on_disk = json.loads(isolate_config_store.read_text())
        assert on_disk["library_root"] == "/custom/lib"
        assert on_disk["schema_version"] == config_store.SCHEMA_VERSION

    def test_set_creates_the_parent_directory_when_missing(self, tmp_path, monkeypatch):
        nested = tmp_path / "photo_db" / "config.json"
        monkeypatch.setattr(config_store, "CONFIG_PATH", nested)

        config_store.set("library_root", "/custom/lib")
        assert json.loads(nested.read_text())["library_root"] == "/custom/lib"

    def test_the_file_stays_hand_editable(self, isolate_config_store):
        # config.json is documented as a manual-edit escape hatch, so it is
        # pretty-printed rather than one dense line.
        config_store.set("library_root", "/custom/lib")
        text = isolate_config_store.read_text()
        assert json.loads(text)  # parses
        assert "\n" in text.strip()


# ── atomic writes ─────────────────────────────────────────────────────────────

class TestAtomicWrite:
    def test_an_interrupted_write_leaves_the_previous_config_intact(
        self, isolate_config_store, monkeypatch
    ):
        original = json.dumps({
            "schema_version": config_store.SCHEMA_VERSION,
            "library_root": "/original/lib",
            "future_key": "keep me",
        })
        isolate_config_store.write_text(original)

        real_replace = os.replace

        def failing_replace(*args, **kwargs):
            raise OSError("simulated crash mid-write")

        monkeypatch.setattr(os, "replace", failing_replace)
        with pytest.raises(OSError):
            config_store.set("library_root", "/new/lib")
        monkeypatch.setattr(os, "replace", real_replace)

        # The half-written state is invisible: old content, no .tmp debris.
        assert isolate_config_store.read_text() == original
        assert list(isolate_config_store.parent.glob("*.tmp")) == []
        assert config_store.get("library_root") == "/original/lib"

    def test_an_interrupted_first_write_creates_no_file_at_all(
        self, isolate_config_store, monkeypatch
    ):
        real_replace = os.replace
        monkeypatch.setattr(
            os, "replace",
            lambda *a, **k: (_ for _ in ()).throw(OSError("simulated crash mid-write")),
        )
        with pytest.raises(OSError):
            config_store.set("library_root", "/new/lib")
        monkeypatch.setattr(os, "replace", real_replace)

        assert not isolate_config_store.exists()
        assert list(isolate_config_store.parent.glob("*.tmp")) == []

    def test_a_value_json_cannot_encode_fails_without_leaving_debris(
        self, isolate_config_store
    ):
        # Fails partway through json.dump, i.e. after the temp file already
        # holds bytes — the other half of "clean up on any exception".
        original = json.dumps({"library_root": "/original/lib"})
        isolate_config_store.write_text(original)

        with pytest.raises(TypeError):
            config_store.set("library_root", object())

        assert isolate_config_store.read_text() == original
        assert list(isolate_config_store.parent.glob("*.tmp")) == []

    def test_a_successful_write_leaves_no_temp_file(self, isolate_config_store):
        config_store.set("library_root", "/custom/lib")
        assert list(isolate_config_store.parent.glob("*.tmp")) == []


# ── ensure_seeded ─────────────────────────────────────────────────────────────

class TestEnsureSeeded:
    def test_it_creates_the_file_with_defaults(self, isolate_config_store):
        returned = config_store.ensure_seeded()

        assert isolate_config_store.exists()
        on_disk = json.loads(isolate_config_store.read_text())
        assert on_disk["schema_version"] == config_store.SCHEMA_VERSION
        assert Path(on_disk["library_root"]) == EXPECTED_DEFAULT_ROOT
        assert returned == on_disk

    def test_it_never_clobbers_an_existing_config(self, isolate_config_store):
        config_store.set("library_root", "/custom/lib")
        config_store.set("future_key", "keep me")

        returned = config_store.ensure_seeded()

        assert returned["library_root"] == "/custom/lib"
        assert returned["future_key"] == "keep me"

    def test_it_is_idempotent(self, isolate_config_store):
        config_store.ensure_seeded()
        first = isolate_config_store.read_text()
        config_store.ensure_seeded()
        config_store.ensure_seeded()
        assert isolate_config_store.read_text() == first


# ── get_library_root ──────────────────────────────────────────────────────────

class TestGetLibraryRoot:
    def test_it_returns_a_path_not_a_string(self):
        assert isinstance(config_store.get_library_root(), Path)

    def test_it_defaults_to_the_detected_photos_library(self):
        assert config_store.get_library_root() == EXPECTED_DEFAULT_ROOT

    def test_it_reflects_a_configured_root(self):
        config_store.set("library_root", "/Volumes/External/Photos.photoslibrary")
        assert config_store.get_library_root() == Path("/Volumes/External/Photos.photoslibrary")

    def test_a_hand_edited_file_is_picked_up_without_a_set(self, isolate_config_store):
        # The documented escape hatch: edit config.json, restart, done.
        isolate_config_store.write_text(json.dumps({"library_root": "/hand/edited.photoslibrary"}))
        assert config_store.get_library_root() == Path("/hand/edited.photoslibrary")

    def test_a_tilde_is_expanded(self):
        config_store.set("library_root", "~/Pictures/Other.photoslibrary")
        root = config_store.get_library_root()
        assert "~" not in str(root)
        assert root == Path.home() / "Pictures" / "Other.photoslibrary"

    def test_a_corrupt_file_still_yields_a_usable_root(self, isolate_config_store):
        isolate_config_store.write_text("{not json")
        assert config_store.get_library_root() == EXPECTED_DEFAULT_ROOT

    @pytest.mark.parametrize("bad", [None, 123, ["/a/lib"], {"path": "/a/lib"}])
    def test_a_non_string_library_root_falls_back_instead_of_crashing(
        self, isolate_config_store, bad
    ):
        isolate_config_store.write_text(json.dumps({"library_root": bad}))
        assert config_store.get_library_root() == EXPECTED_DEFAULT_ROOT

    def test_an_empty_library_root_does_not_become_the_working_directory(
        self, isolate_config_store
    ):
        isolate_config_store.write_text(json.dumps({"library_root": ""}))
        assert config_store.get_library_root() == EXPECTED_DEFAULT_ROOT


# ── validate_library_root ─────────────────────────────────────────────────────

class TestValidateLibraryRoot:
    def test_a_complete_library_is_valid(self, tmp_path):
        root = fake_library(tmp_path)
        result = config_store.validate_library_root(root)
        assert result == {
            "path": str(root),
            "exists": True,
            "is_dir": True,
            "has_derivatives": True,
            "has_originals": True,
            "valid": True,
        }

    def test_a_library_without_originals_is_not_valid(self, tmp_path):
        # originals/ is how video resolution finds the source file; a library
        # missing it is only half usable, so it must not report valid.
        root = fake_library(tmp_path, originals=False)
        result = config_store.validate_library_root(root)
        assert result["has_derivatives"] is True
        assert result["has_originals"] is False
        assert result["valid"] is False

    def test_a_library_without_derivatives_is_not_valid(self, tmp_path):
        root = fake_library(tmp_path, derivatives=False)
        result = config_store.validate_library_root(root)
        assert result["has_derivatives"] is False
        assert result["has_originals"] is True
        assert result["valid"] is False

    def test_an_empty_directory_is_not_valid(self, tmp_path):
        root = fake_library(tmp_path, derivatives=False, originals=False)
        result = config_store.validate_library_root(root)
        assert result["exists"] is True
        assert result["is_dir"] is True
        assert result["valid"] is False

    def test_a_missing_path_reports_absent_rather_than_raising(self, tmp_path):
        missing = tmp_path / "not-there.photoslibrary"
        result = config_store.validate_library_root(missing)
        assert result["exists"] is False
        assert result["is_dir"] is False
        assert result["has_derivatives"] is False
        assert result["has_originals"] is False
        assert result["valid"] is False
        assert result["path"] == str(missing)

    def test_a_file_is_not_mistaken_for_a_library(self, tmp_path):
        f = tmp_path / "lib.photoslibrary"
        f.write_text("i am a file")
        result = config_store.validate_library_root(f)
        assert result["exists"] is True
        assert result["is_dir"] is False
        assert result["valid"] is False

    def test_a_subtree_that_is_a_file_does_not_count(self, tmp_path):
        # `originals` as a regular file must not satisfy the check.
        root = fake_library(tmp_path, originals=False)
        (root / "originals").write_text("not a directory")
        result = config_store.validate_library_root(root)
        assert result["has_originals"] is False
        assert result["valid"] is False

    def test_it_defaults_to_the_configured_root(self, tmp_path):
        root = fake_library(tmp_path)
        config_store.set("library_root", str(root))
        result = config_store.validate_library_root()
        assert result["path"] == str(root)
        assert result["valid"] is True

    def test_a_string_argument_works_the_same_as_a_path(self, tmp_path):
        root = fake_library(tmp_path)
        assert config_store.validate_library_root(str(root)) == \
            config_store.validate_library_root(root)

    def test_a_tilde_argument_is_expanded(self):
        result = config_store.validate_library_root("~/definitely-not-a-real-library")
        assert "~" not in result["path"]
        assert result["path"].startswith(str(Path.home()))

    @pytest.mark.parametrize("scenario", ["complete", "no-originals", "no-derivatives", "missing"])
    def test_the_report_never_claims_a_subtree_it_could_not_have_seen(
        self, tmp_path, scenario
    ):
        # Internal consistency: subtree flags and `valid` can only ever be true
        # of a real directory, whatever the scenario.
        if scenario == "missing":
            root = tmp_path / "nope"
        else:
            root = fake_library(
                tmp_path,
                derivatives=(scenario != "no-derivatives"),
                originals=(scenario != "no-originals"),
            )
        result = config_store.validate_library_root(root)

        if not result["is_dir"]:
            assert not result["has_derivatives"]
            assert not result["has_originals"]
        if result["valid"]:
            assert result["is_dir"] and result["has_derivatives"] and result["has_originals"]

    def test_it_changes_nothing_on_disk(self, tmp_path, isolate_config_store):
        root = fake_library(tmp_path)
        before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

        config_store.validate_library_root(root)
        config_store.validate_library_root(tmp_path / "missing")
        config_store.validate_library_root()

        assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
        assert not isolate_config_store.exists()


# ── concurrency ───────────────────────────────────────────────────────────────

class TestLocking:
    def test_concurrent_writers_do_not_lose_each_others_keys(self):
        # set() is a read-modify-write of the whole dict, so without the lock
        # the last writer wins and the others' keys vanish.
        errors = []

        def writer(n):
            try:
                config_store.set(f"key_{n}", n)
            except Exception as exc:  # surfaced below, never swallowed
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        data = config_store.load()
        assert {f"key_{n}": n for n in range(12)}.items() <= data.items()

    def test_set_can_be_called_while_the_lock_is_already_held(self):
        # The lock is documented as reentrant because set() calls load() under
        # it. A caller holding _LOCK (as a future compound operation would)
        # must not deadlock. Run in a worker so a regression fails instead of
        # hanging the session.
        done = threading.Event()

        def nested():
            with config_store._LOCK:
                config_store.set("library_root", "/custom/lib")
            done.set()

        t = threading.Thread(target=nested, daemon=True)
        t.start()
        t.join(timeout=10)

        assert done.is_set(), "set() deadlocked when the lock was already held"
        assert config_store.get("library_root") == "/custom/lib"


# ── corrupt-file handling that reads never recover from ──────────────────────

class TestEncodingRobustness:
    @pytest.mark.parametrize("raw", [
        pytest.param(json.dumps({"library_root": "/x"}).encode("utf-16"), id="utf-16-bom"),
        pytest.param("{\"library_root\": \"/caf\xe9\"}".encode("latin-1"), id="latin-1"),
        pytest.param(b"\xff\xfe\x00\x01\x02", id="binary-garbage"),
    ])
    def test_a_file_in_the_wrong_encoding_falls_back_like_any_corrupt_file(
        self, isolate_config_store, raw
    ):
        isolate_config_store.write_bytes(raw)
        assert config_store.load()["schema_version"] == config_store.SCHEMA_VERSION
