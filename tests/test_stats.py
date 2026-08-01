"""
Delete counter + reclaimed-bytes accounting
===========================================
`stats.json` backs two things the UI shows: the "N photos deleted" counter and
the reclaimed-bytes headline. The headline is a DERIVED sum of three independent
sources, and `backend/CLAUDE.md` is explicit that `reclaimed_bytes` is never
assigned directly.

The invariant every test here leans on: after any writer,
`reclaimed_bytes == sum(reclaimed_breakdown.values())`. The three-way split
exists because a single shared scalar meant the next Climb Cutter verdict
silently wiped the photo-side bytes — that regression gets its own test.

`stats.STATS_PATH` is redirected to tmp_path by the autouse `isolate_stats`
fixture, so nothing here can touch the user's live counter.
"""

import json

import pytest

import stats

AVG = stats.AVG_PHOTO_BYTES


def total_is_consistent(result: dict) -> bool:
    return result["reclaimed_bytes"] == sum(result["reclaimed_breakdown"].values())


# ── reading ───────────────────────────────────────────────────────────────────

class TestReading:
    def test_a_missing_file_reads_as_zeroed_defaults(self):
        result = stats.get_stats()
        assert result["deleted"] == 0
        assert result["reclaimed_bytes"] == 0
        assert set(result["reclaimed_breakdown"]) == set(stats.BREAKDOWN_KEYS)

    def test_a_corrupt_file_reads_as_defaults_rather_than_crashing(self, isolate_stats):
        isolate_stats.write_text("{not json at all")
        result = stats.get_stats()
        assert result["deleted"] == 0
        assert result["reclaimed_bytes"] == 0

    def test_reading_never_hands_back_the_module_default_dict(self, seeded_stats):
        """Two reads must not share a mutable breakdown, or one caller corrupts the other."""
        seeded_stats.write({"deleted": 1})
        first = stats.get_stats()
        first["reclaimed_breakdown"]["photos_exact"] = 999_999
        second = stats.get_stats()
        assert second["reclaimed_breakdown"]["photos_exact"] == 0
        assert stats.DEFAULTS["reclaimed_breakdown"]["photos_exact"] == 0

    def test_a_partial_breakdown_is_filled_out(self, seeded_stats):
        seeded_stats.write({"reclaimed_breakdown": {"photos_exact": 500}})
        result = stats.get_stats()
        assert result["reclaimed_breakdown"] == {
            "photos_exact": 500, "photos_estimated": 0, "climb_cutter": 0
        }
        assert total_is_consistent(result)

    def test_negative_values_on_disk_are_floored(self, seeded_stats):
        seeded_stats.write({"reclaimed_breakdown": {"photos_exact": -5000}})
        result = stats.get_stats()
        assert result["reclaimed_breakdown"]["photos_exact"] == 0

    def test_a_stale_scalar_is_ignored_when_a_breakdown_exists(self, seeded_stats):
        """The breakdown is authoritative; the scalar is only ever derived output."""
        seeded_stats.write({
            "reclaimed_bytes": 999_999_999,
            "reclaimed_breakdown": {"photos_exact": 100, "photos_estimated": 0,
                                    "climb_cutter": 0},
        })
        assert stats.get_stats()["reclaimed_bytes"] == 100


class TestLegacyMigration:
    """Files written before the breakdown existed carry only a scalar."""

    def test_a_legacy_scalar_is_seeded_into_climb_cutter(self, seeded_stats):
        seeded_stats.write({"deleted": 12, "reclaimed_bytes": 5_000_000})
        result = stats.get_stats()
        assert result["reclaimed_breakdown"]["climb_cutter"] == 5_000_000
        assert result["reclaimed_breakdown"]["photos_exact"] == 0
        assert result["reclaimed_breakdown"]["photos_estimated"] == 0

    def test_the_headline_survives_the_upgrade_unchanged(self, seeded_stats):
        """The whole point of seeding rather than dropping: the number the user sees."""
        seeded_stats.write({"deleted": 12, "reclaimed_bytes": 5_000_000})
        assert stats.get_stats()["reclaimed_bytes"] == 5_000_000

    def test_the_deleted_count_survives_the_upgrade(self, seeded_stats):
        seeded_stats.write({"deleted": 12, "reclaimed_bytes": 5_000_000})
        assert stats.get_stats()["deleted"] == 12

    def test_migration_does_not_double_count_on_the_next_verdict(self, seeded_stats):
        """savings.json stays the ledger of record, so an absolute set replaces it."""
        seeded_stats.write({"reclaimed_bytes": 5_000_000})
        result = stats.set_climb_cutter_bytes(7_000_000)
        assert result["reclaimed_breakdown"]["climb_cutter"] == 7_000_000
        assert result["reclaimed_bytes"] == 7_000_000

    def test_a_garbage_scalar_does_not_crash_the_read(self, seeded_stats):
        seeded_stats.write({"reclaimed_bytes": None})
        assert stats.get_stats()["reclaimed_bytes"] == 0


# ── counting deletions ────────────────────────────────────────────────────────

class TestDeleteCounter:
    def test_incrementing_raises_the_count(self):
        assert stats.update_stats(1)["deleted"] == 1
        assert stats.update_stats(1)["deleted"] == 2

    def test_decrementing_lowers_the_count(self):
        stats.update_stats(5)
        assert stats.update_stats(-1)["deleted"] == 4

    def test_the_count_is_floored_at_zero(self):
        """Undoing more than was ever counted must not go negative."""
        stats.update_stats(1)
        assert stats.update_stats(-10)["deleted"] == 0

    def test_a_bulk_add_counts_every_photo(self):
        assert stats.update_stats(40)["deleted"] == 40

    def test_the_write_is_persisted(self, isolate_stats):
        stats.update_stats(3)
        on_disk = json.loads(isolate_stats.read_text())
        assert on_disk["deleted"] == 3


# ── crediting bytes ───────────────────────────────────────────────────────────

class TestByteCrediting:
    def test_a_known_size_lands_in_the_exact_pool(self):
        result = stats.update_stats(1, exact_bytes=4_200_000)
        assert result["reclaimed_breakdown"]["photos_exact"] == 4_200_000
        assert result["reclaimed_breakdown"]["photos_estimated"] == 0

    def test_an_unknown_size_falls_back_to_the_average(self):
        result = stats.update_stats(1)
        assert result["reclaimed_breakdown"]["photos_estimated"] == AVG
        assert result["reclaimed_breakdown"]["photos_exact"] == 0

    def test_a_bulk_add_uses_the_average_per_photo(self):
        """The bulk-estimate path: 40 photos with no sizes available."""
        result = stats.update_stats(40)
        assert result["reclaimed_breakdown"]["photos_estimated"] == 40 * AVG
        assert result["reclaimed_bytes"] == 40 * AVG

    def test_a_negative_size_is_treated_as_unknown(self):
        result = stats.update_stats(1, exact_bytes=-500)
        assert result["reclaimed_breakdown"]["photos_exact"] == 0
        assert result["reclaimed_breakdown"]["photos_estimated"] == AVG

    def test_a_zero_size_is_treated_as_unknown(self):
        """`photo_size_bytes` returns 0 when Photos wouldn't say — estimate instead."""
        result = stats.update_stats(1, exact_bytes=0)
        assert result["reclaimed_breakdown"]["photos_estimated"] == AVG

    def test_exact_bytes_credits_one_photo_even_if_delta_is_larger(self):
        """`exact_bytes` is documented as the size of THE photo being culled.

        A caller passing delta>1 alongside a single photo's size is a caller
        error; crediting the one real size (rather than multiplying it) is the
        conservative reading of the docstring.
        """
        result = stats.update_stats(3, exact_bytes=1_000_000)
        assert result["reclaimed_breakdown"]["photos_exact"] == 1_000_000
        assert result["deleted"] == 3

    def test_a_zero_delta_credits_nothing(self):
        result = stats.update_stats(0, exact_bytes=5_000)
        assert result["reclaimed_bytes"] == 0


class TestUndo:
    """Backing out a deletion has no per-photo ledger to consult, so it backs out
    the average — estimated pool first, spilling into exact only once drained."""

    def test_undo_drains_the_estimated_pool_first(self):
        stats.update_stats(3)                       # 3 x AVG estimated
        result = stats.update_stats(-1)
        assert result["reclaimed_breakdown"]["photos_estimated"] == 2 * AVG
        assert result["reclaimed_breakdown"]["photos_exact"] == 0

    def test_undo_spills_into_the_exact_pool_once_estimated_is_empty(self):
        stats.update_stats(1, exact_bytes=10_000_000)   # exact only
        result = stats.update_stats(-1)
        assert result["reclaimed_breakdown"]["photos_estimated"] == 0
        assert result["reclaimed_breakdown"]["photos_exact"] == 10_000_000 - AVG

    def test_undo_splits_across_both_pools_when_estimated_is_short(self):
        stats.update_stats(1, exact_bytes=10_000_000)
        stats.update_stats(1)                            # + 1 x AVG estimated
        result = stats.update_stats(-2)                  # owes 2 x AVG
        assert result["reclaimed_breakdown"]["photos_estimated"] == 0
        assert result["reclaimed_breakdown"]["photos_exact"] == 10_000_000 - AVG

    def test_neither_pool_can_go_negative(self):
        stats.update_stats(1)
        result = stats.update_stats(-50)
        assert result["reclaimed_breakdown"]["photos_estimated"] == 0
        assert result["reclaimed_breakdown"]["photos_exact"] == 0
        assert result["reclaimed_bytes"] == 0

    def test_undo_never_touches_the_climb_cutter_pool(self):
        """Video bytes are owned by savings.json — the photo undo must not raid them."""
        stats.set_climb_cutter_bytes(9_000_000)
        result = stats.update_stats(-10)
        assert result["reclaimed_breakdown"]["climb_cutter"] == 9_000_000

    def test_add_then_undo_returns_to_zero(self):
        stats.update_stats(1)
        result = stats.update_stats(-1)
        assert result["deleted"] == 0
        assert result["reclaimed_bytes"] == 0


# ── the three-way split ───────────────────────────────────────────────────────

class TestSourceIsolation:
    def test_setting_video_bytes_does_not_wipe_photo_bytes(self):
        """The exact regression the breakdown was introduced to fix.

        With one shared scalar, `set_climb_cutter_bytes` (an ABSOLUTE set) blew
        away everything the photo side had accumulated.
        """
        stats.update_stats(1, exact_bytes=4_000_000)
        stats.update_stats(2)
        before = stats.get_stats()["reclaimed_breakdown"]

        after = stats.set_climb_cutter_bytes(50_000_000)["reclaimed_breakdown"]
        assert after["photos_exact"] == before["photos_exact"] == 4_000_000
        assert after["photos_estimated"] == before["photos_estimated"] == 2 * AVG
        assert after["climb_cutter"] == 50_000_000

    def test_crediting_photos_does_not_disturb_video_bytes(self):
        stats.set_climb_cutter_bytes(50_000_000)
        result = stats.update_stats(1, exact_bytes=4_000_000)
        assert result["reclaimed_breakdown"]["climb_cutter"] == 50_000_000

    def test_setting_video_bytes_is_absolute_not_cumulative(self):
        stats.set_climb_cutter_bytes(10_000_000)
        result = stats.set_climb_cutter_bytes(3_000_000)
        assert result["reclaimed_breakdown"]["climb_cutter"] == 3_000_000

    def test_a_negative_video_total_is_floored(self):
        assert stats.set_climb_cutter_bytes(-5)["reclaimed_breakdown"]["climb_cutter"] == 0

    def test_the_legacy_alias_is_the_same_function(self):
        assert stats.set_reclaimed_bytes is stats.set_climb_cutter_bytes


class TestHeadlineInvariant:
    """`reclaimed_bytes` is derived. It must equal its parts after EVERY writer."""

    @pytest.mark.parametrize("writer", [
        lambda: stats.update_stats(1),
        lambda: stats.update_stats(1, exact_bytes=1234),
        lambda: stats.update_stats(-1),
        lambda: stats.update_stats(0),
        lambda: stats.set_climb_cutter_bytes(777),
        lambda: stats.set_climb_cutter_bytes(0),
    ])
    def test_the_total_matches_its_parts(self, writer):
        assert total_is_consistent(writer())

    def test_it_still_holds_after_a_long_mixed_sequence(self):
        stats.update_stats(1, exact_bytes=4_000_000)
        stats.set_climb_cutter_bytes(1_000_000)
        stats.update_stats(5)
        stats.update_stats(-2)
        stats.set_climb_cutter_bytes(2_500_000)
        result = stats.update_stats(-1)
        assert total_is_consistent(result)
        assert total_is_consistent(stats.get_stats())

    def test_the_persisted_file_also_satisfies_the_invariant(self, isolate_stats):
        stats.update_stats(3, exact_bytes=999)
        stats.set_climb_cutter_bytes(4242)
        on_disk = json.loads(isolate_stats.read_text())
        assert total_is_consistent(on_disk)


class TestAtomicWrite:
    def test_no_temp_files_are_left_behind(self, isolate_stats):
        stats.update_stats(1)
        leftovers = list(isolate_stats.parent.glob("*.tmp"))
        assert leftovers == []

    def test_the_file_is_valid_json_after_every_write(self, isolate_stats):
        for _ in range(5):
            stats.update_stats(1)
            json.loads(isolate_stats.read_text())
