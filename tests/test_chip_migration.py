"""
Chip migration, resolution and stats.

The three modules that turn a chip from a stored record into selected photos:

  chip_migration.py — seeds builtins, re-keys dismissed.json to chip ids
  chip_resolve.py   — the ONE path that selects photos for a chip
  chip_stats.py     — per-chip run counters, a deliberate sibling file

The properties worth defending here are the destructive ones. A dismissal is a
user decision ("keep this one, it isn't junk"), and the migration is the only
code in this repo that rewrites the ledger those decisions live in — so
"idempotent" and "never drops an entry" are the tests that matter most.

`chips.py` itself is covered by test_chips.py.
"""

import json

import pytest

import chip_migration
import chip_resolve
import chip_stats
import chips
import dismissed
import search


# ── Helpers ───────────────────────────────────────────────────────────────────

def write_dismissed(path, data):
    path.write_text(json.dumps(data))
    dismissed.reload()


def capture_search_text(monkeypatch, results=None):
    """Replace search.search_text with a recorder. Returns the capture dict."""
    captured = {}

    def recorder(query, n, *args, **kwargs):
        captured["query"] = query
        captured["n"] = n
        captured["exclude_ids"] = kwargs.get("exclude_ids")
        return list(results or [])

    monkeypatch.setattr(search, "search_text", recorder)
    return captured


def semantic_chip(chip_id="blurry", prompt="blurry or out of focus photo", n=24):
    return chips.validate({
        "id": chip_id,
        "label": "Test chip",
        "engine": "semantic",
        "query": {"prompts": [prompt], "negatives": []},
        "result_size": n,
    })


# ── Seeding ───────────────────────────────────────────────────────────────────

class TestSeeding:
    def test_migrate_without_apply_writes_nothing(self, isolate_chips):
        chip_migration.migrate(apply=False)
        assert not isolate_chips.chips.exists()

    def test_migrate_with_apply_seeds_all_six_builtins(self, isolate_chips):
        result = chip_migration.migrate(apply=True)
        assert [c["id"] for c in result["chips"]] == [
            "accidental", "dark", "blurry", "screenshot", "receipt", "duplicate",
        ]
        assert isolate_chips.chips.exists()

    def test_seeding_is_idempotent(self, isolate_chips):
        chip_migration.migrate(apply=True)
        first = isolate_chips.chips.read_text()
        chip_migration.migrate(apply=True)
        assert isolate_chips.chips.read_text() == first


# ── Dismissal re-keying ───────────────────────────────────────────────────────

class TestDismissalMigration:
    def test_the_mapping_is_the_identity_mapping(self):
        # Not a formality: the frontend passed `chip.id` as the dismissal
        # `category`, so the old key space and the chip-id space are already
        # the same. If this ever stops being true, the migration has to grow a
        # real rename pair — silently changing an id orphans dismissals.
        assert all(k == v for k, v in chip_migration.OLD_KEY_TO_CHIP_ID.items())
        assert set(chip_migration.OLD_KEY_TO_CHIP_ID) == {
            c["id"] for c in chips.BUILTIN_CHIPS
        }

    def test_todays_ledger_needs_no_rewrite(self, isolate_dismissed, isolate_chips):
        write_dismissed(isolate_dismissed, {"accidental": ["a" * 32, "b" * 32]})
        chips.ensure_seeded()
        plan = chip_migration.plan_dismissal_migration()
        assert plan["changed"] is False
        assert plan["renames"] == []
        assert plan["unrecognised"] == []

    def test_apply_leaves_an_already_keyed_file_byte_identical(
        self, isolate_dismissed, isolate_chips
    ):
        write_dismissed(isolate_dismissed, {"accidental": ["a" * 32]})
        chips.ensure_seeded()
        before = isolate_dismissed.read_text()
        chip_migration.migrate_dismissals(apply=True)
        assert isolate_dismissed.read_text() == before

    def test_a_recorded_rename_moves_the_ids(
        self, isolate_dismissed, isolate_chips, monkeypatch
    ):
        monkeypatch.setitem(chip_migration.OLD_KEY_TO_CHIP_ID, "oldname", "blurry")
        write_dismissed(isolate_dismissed, {"oldname": ["a" * 32, "b" * 32]})
        chips.ensure_seeded()

        plan = chip_migration.migrate_dismissals(apply=True)

        assert plan["applied"] is True
        assert plan["renames"] == [("oldname", "blurry")]
        on_disk = json.loads(isolate_dismissed.read_text())
        assert on_disk == {"blurry": ["a" * 32, "b" * 32]}

    def test_migration_is_idempotent(
        self, isolate_dismissed, isolate_chips, monkeypatch
    ):
        monkeypatch.setitem(chip_migration.OLD_KEY_TO_CHIP_ID, "oldname", "blurry")
        write_dismissed(isolate_dismissed, {"oldname": ["a" * 32]})
        chips.ensure_seeded()

        chip_migration.migrate_dismissals(apply=True)
        after_first = isolate_dismissed.read_text()
        second = chip_migration.migrate_dismissals(apply=True)

        assert second["changed"] is False
        assert second["applied"] is False
        assert isolate_dismissed.read_text() == after_first

    def test_an_unrecognised_key_is_kept_not_dropped(
        self, isolate_dismissed, isolate_chips
    ):
        # A category with no mapping and no chip. It could be a chip the user
        # is about to create, or one whose rename nobody recorded. Either way,
        # deleting someone's "keep this one" decisions is never the safe default.
        write_dismissed(isolate_dismissed, {"mystery": ["a" * 32, "b" * 32]})
        chips.ensure_seeded()

        plan = chip_migration.migrate_dismissals(apply=True)

        assert plan["unrecognised"] == ["mystery"]
        assert plan["after"]["mystery"] == ["a" * 32, "b" * 32]
        assert json.loads(isolate_dismissed.read_text())["mystery"] == [
            "a" * 32, "b" * 32,
        ]

    def test_no_dismissal_is_ever_lost(
        self, isolate_dismissed, isolate_chips, monkeypatch
    ):
        monkeypatch.setitem(chip_migration.OLD_KEY_TO_CHIP_ID, "oldname", "blurry")
        write_dismissed(isolate_dismissed, {
            "accidental": ["a" * 32, "b" * 32],
            "oldname": ["c" * 32],
            "mystery": ["d" * 32],
        })
        chips.ensure_seeded()

        plan = chip_migration.migrate_dismissals(apply=True)

        assert plan["total_after"] == plan["total_before"] == 4
        surviving = {i for ids in plan["after"].values() for i in ids}
        assert surviving == {"a" * 32, "b" * 32, "c" * 32, "d" * 32}

    def test_two_old_keys_merging_into_one_chip_keeps_both_id_sets(
        self, isolate_dismissed, isolate_chips, monkeypatch
    ):
        monkeypatch.setitem(chip_migration.OLD_KEY_TO_CHIP_ID, "old_a", "blurry")
        monkeypatch.setitem(chip_migration.OLD_KEY_TO_CHIP_ID, "old_b", "blurry")
        write_dismissed(isolate_dismissed, {
            "old_a": ["a" * 32], "old_b": ["b" * 32],
        })
        chips.ensure_seeded()

        chip_migration.migrate_dismissals(apply=True)

        assert json.loads(isolate_dismissed.read_text())["blurry"] == [
            "a" * 32, "b" * 32,
        ]

    def test_a_missing_ledger_is_not_an_error(self, isolate_dismissed, isolate_chips):
        chips.ensure_seeded()
        plan = chip_migration.migrate_dismissals(apply=True)
        assert plan["total_before"] == 0
        assert plan["changed"] is False

    def test_a_corrupt_ledger_reads_as_empty_rather_than_raising(
        self, isolate_dismissed, isolate_chips
    ):
        isolate_dismissed.write_text("{not json")
        chips.ensure_seeded()
        assert chip_migration.plan_dismissal_migration()["before"] == {}


class TestBackup:
    def test_apply_backs_up_even_when_nothing_changes(
        self, isolate_dismissed, isolate_chips
    ):
        # A backup that only appears on the runs that change something is a
        # backup you cannot rely on.
        write_dismissed(isolate_dismissed, {"accidental": ["a" * 32]})
        chips.ensure_seeded()

        plan = chip_migration.migrate_dismissals(apply=True)

        assert plan["changed"] is False
        backups = list(isolate_dismissed.parent.glob("dismissed.backup.*.json"))
        assert len(backups) == 1
        assert backups[0].read_text() == isolate_dismissed.read_text()

    def test_the_backup_captures_the_pre_migration_contents(
        self, isolate_dismissed, isolate_chips, monkeypatch
    ):
        monkeypatch.setitem(chip_migration.OLD_KEY_TO_CHIP_ID, "oldname", "blurry")
        original = {"oldname": ["a" * 32]}
        write_dismissed(isolate_dismissed, original)
        chips.ensure_seeded()

        plan = chip_migration.migrate_dismissals(apply=True)

        assert json.loads(open(plan["backup"]).read()) == original
        assert json.loads(isolate_dismissed.read_text()) == {"blurry": ["a" * 32]}

    def test_a_dry_run_writes_no_backup(self, isolate_dismissed, isolate_chips):
        write_dismissed(isolate_dismissed, {"accidental": ["a" * 32]})
        chip_migration.migrate_dismissals(apply=False)
        assert list(isolate_dismissed.parent.glob("dismissed.backup.*.json")) == []

    def test_the_backup_name_uses_the_repos_utc_stamp_format(
        self, isolate_dismissed, isolate_chips
    ):
        # Matches photo_db/chroma_backup_<stamp>.sqlite3.
        write_dismissed(isolate_dismissed, {"accidental": ["a" * 32]})
        backup = chip_migration.backup_dismissed()
        stamp = backup.name[len("dismissed.backup."):-len(".json")]
        assert len(stamp) == 16 and stamp[8] == "T" and stamp.endswith("Z")


# ── resolve() ─────────────────────────────────────────────────────────────────

class TestResolveDispatch:
    def test_it_dispatches_to_the_engine_named_on_the_chip(
        self, fake_chroma, isolate_chips, monkeypatch
    ):
        seen = {}

        def fake_engine(chip, n, collection, model, tokenizer, device):
            seen["chip_id"] = chip["id"]
            seen["n"] = n
            return [{"id": "x"}]

        monkeypatch.setitem(chip_resolve.ENGINES, "semantic", fake_engine)
        chip_resolve.resolve(semantic_chip(), fake_chroma, None, None, None, n=7)
        assert seen == {"chip_id": "blurry", "n": 7}

    def test_an_engine_with_no_implementation_raises(self, fake_chroma):
        chip = dict(semantic_chip(), engine="pixel")
        with pytest.raises(ValueError, match="no implementation for engine"):
            chip_resolve.resolve(chip, fake_chroma, None, None, None)

    def test_every_declared_engine_has_an_implementation_and_a_validator(self):
        # chips.ENGINES is what validation accepts on write; chip_resolve.ENGINES
        # is what can actually run; chips.QUERY_VALIDATORS is what checks the
        # payload shape before either sees it. A name in any one of the three
        # without the other two either can't be saved, can't be resolved, or
        # stores an unvalidated payload — so all three must carry the same set.
        registries = {
            "chips.ENGINES": set(chips.ENGINES),
            "chip_resolve.ENGINES": set(chip_resolve.ENGINES),
            "chips.QUERY_VALIDATORS": set(chips.QUERY_VALIDATORS),
        }
        all_engines = set().union(*registries.values())

        problems = []
        for engine in sorted(all_engines):
            missing_from = [name for name, members in registries.items() if engine not in members]
            if missing_from:
                problems.append(f"{engine!r} is missing from {', '.join(missing_from)}")

        assert not problems, "; ".join(problems)


class TestResolveResultSize:
    def test_it_uses_the_chips_result_size_when_n_is_omitted(
        self, fake_chroma, monkeypatch
    ):
        captured = capture_search_text(monkeypatch)
        chip_resolve.resolve(semantic_chip(n=33), fake_chroma, None, None, None)
        assert captured["n"] == 33

    def test_an_explicit_n_overrides_the_chips_result_size(
        self, fake_chroma, monkeypatch
    ):
        # The chip's result_size is a DEFAULT, not a cap. This is what lets the
        # UI's 24/48 count toggle and Junk Hunt's 48 keep working exactly as
        # they did before the chip store existed.
        captured = capture_search_text(monkeypatch)
        chip_resolve.resolve(semantic_chip(n=24), fake_chroma, None, None, None, n=48)
        assert captured["n"] == 48

    def test_it_truncates_to_n(self, fake_chroma, monkeypatch):
        for i in range(10):
            fake_chroma.add_row(f"id{i}", path=f"/p/{i}.jpg", filename=f"{i}.jpg")
        monkeypatch.setattr(search, "embed_text", lambda *a, **k: [0.0])
        out = chip_resolve.resolve(
            semantic_chip(), fake_chroma, None, None, None, n=4
        )
        assert len(out) == 4


class TestResolveSemanticEngine:
    def test_it_sends_the_chips_prompt_to_clip(self, fake_chroma, monkeypatch):
        captured = capture_search_text(monkeypatch)
        chip_resolve.resolve(
            semantic_chip(prompt="receipt or invoice"), fake_chroma, None, None, None
        )
        assert captured["query"] == "receipt or invoice"

    def test_the_emoji_never_reaches_clip(self, fake_chroma, monkeypatch):
        # The whole reason emoji is its own field rather than part of the
        # prompt: it would just be noise in the embedding.
        captured = capture_search_text(monkeypatch)
        chip = dict(semantic_chip(), emoji="💨")
        chip_resolve.resolve(chip, fake_chroma, None, None, None)
        assert "💨" not in captured["query"]

    def test_a_dismissed_photo_is_excluded_from_the_results(
        self, fake_chroma, isolate_dismissed, monkeypatch
    ):
        for i in range(5):
            fake_chroma.add_row(f"id{i}", path=f"/p/{i}.jpg", filename=f"{i}.jpg")
        write_dismissed(isolate_dismissed, {"blurry": ["id2"]})
        monkeypatch.setattr(search, "embed_text", lambda *a, **k: [0.0])

        out = chip_resolve.resolve(
            semantic_chip(), fake_chroma, None, None, None, n=5
        )

        assert "id2" not in [r["id"] for r in out]

    def test_the_ledger_is_keyed_by_chip_id(
        self, fake_chroma, isolate_dismissed, monkeypatch
    ):
        # A dismissal under a DIFFERENT chip must not hide the photo here —
        # that scoping is the whole point of a per-chip ledger.
        for i in range(5):
            fake_chroma.add_row(f"id{i}", path=f"/p/{i}.jpg", filename=f"{i}.jpg")
        write_dismissed(isolate_dismissed, {"dark": ["id2"]})
        monkeypatch.setattr(search, "embed_text", lambda *a, **k: [0.0])

        out = chip_resolve.resolve(
            semantic_chip(chip_id="blurry"), fake_chroma, None, None, None, n=5
        )

        assert "id2" in [r["id"] for r in out]

    def test_it_over_fetches_to_still_return_a_full_page(
        self, fake_chroma, isolate_dismissed, monkeypatch
    ):
        # The over-fetch + post-filter contract moved here from the old
        # /search/text category branch: ask for n + len(dismissed) so a
        # dismissal-heavy chip still fills a page.
        captured = {}
        real_query = fake_chroma.query

        def recording_query(**kwargs):
            captured["n_results"] = kwargs["n_results"]
            return real_query(**kwargs)

        monkeypatch.setattr(fake_chroma, "query", recording_query)
        write_dismissed(isolate_dismissed, {"blurry": ["x" * 32, "y" * 32]})
        monkeypatch.setattr(search, "embed_text", lambda *a, **k: [0.0])

        chip_resolve.resolve(semantic_chip(), fake_chroma, None, None, None, n=10)

        assert captured["n_results"] == 12


# ── chip_stats ────────────────────────────────────────────────────────────────

class TestChipStats:
    def test_a_never_run_chip_reads_back_as_zeros(self):
        assert chip_stats.get("blurry") == {
            "run_count": 0, "last_run_at": 0, "last_result_count": 0,
        }

    def test_resolve_records_a_run(self, fake_chroma, monkeypatch):
        monkeypatch.setattr(search, "search_text", lambda *a, **k: [{"id": "a"}])
        chip_resolve.resolve(semantic_chip(), fake_chroma, None, None, None)
        entry = chip_stats.get("blurry")
        assert entry["run_count"] == 1
        assert entry["last_result_count"] == 1
        assert entry["last_run_at"] > 0

    def test_run_count_accumulates(self, fake_chroma, monkeypatch):
        monkeypatch.setattr(search, "search_text", lambda *a, **k: [])
        for _ in range(3):
            chip_resolve.resolve(semantic_chip(), fake_chroma, None, None, None)
        assert chip_stats.get("blurry")["run_count"] == 3

    def test_stats_are_scoped_per_chip(self, fake_chroma, monkeypatch):
        monkeypatch.setattr(search, "search_text", lambda *a, **k: [])
        chip_resolve.resolve(
            semantic_chip(chip_id="blurry"), fake_chroma, None, None, None
        )
        assert chip_stats.get("dark")["run_count"] == 0

    def test_a_corrupt_stats_file_reads_as_empty_rather_than_raising(
        self, isolate_chips
    ):
        isolate_chips.stats.write_text("{not json")
        assert chip_stats.load()["chips"] == {}

    def test_reads_never_write(self, isolate_chips):
        chip_stats.load()
        chip_stats.get("blurry")
        assert not isolate_chips.stats.exists()


class TestStatsAndDefinitionsStaySeparate:
    """The reason chip_stats.json is a sibling file rather than a field."""

    def test_editing_a_chip_does_not_touch_its_stats(
        self, isolate_chips, fake_chroma, monkeypatch
    ):
        chips.ensure_seeded()
        monkeypatch.setattr(search, "search_text", lambda *a, **k: [{"id": "a"}])
        chip_resolve.resolve(
            semantic_chip(chip_id="blurry"), fake_chroma, None, None, None
        )
        before = isolate_chips.stats.read_text()

        chips.update("blurry", label="Renamed", result_size=99)

        assert isolate_chips.stats.read_text() == before
        assert chip_stats.get("blurry")["run_count"] == 1

    def test_recording_a_run_does_not_rewrite_the_definitions(self, isolate_chips):
        chips.ensure_seeded()
        before = isolate_chips.chips.read_text()

        chip_stats.record_run("blurry", 24)

        assert isolate_chips.chips.read_text() == before

    def test_resetting_a_chip_preserves_its_stats(self, isolate_chips):
        chips.ensure_seeded()
        chip_stats.record_run("blurry", 24)
        chips.update("blurry", label="Renamed")

        chips.reset("blurry")

        assert chip_stats.get("blurry")["run_count"] == 1

    def test_deleting_a_chip_leaves_its_stats_alone(self, isolate_chips):
        # Stats outliving a deleted chip is deliberate: a chip id is never
        # reused, so an orphan entry is inert rather than misleading, and
        # losing history to an accidental delete is the worse failure.
        chips.upsert(semantic_chip(chip_id="scratch"))
        chip_stats.record_run("scratch", 5)

        chips.delete("scratch")

        assert chip_stats.get("scratch")["run_count"] == 1
