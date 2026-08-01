"""
Climb Cutter savings ledger
===========================
`savings.json` is the authoritative per-video record of projected reclaimed
bytes; `stats.json`'s `climb_cutter` slot is a MIRROR of its total. Two
properties matter and neither is obvious from reading either file alone:

1. **Idempotence.** A video can be re-reviewed and flipped any number of times.
   The ledger is keyed per video and the total is always re-summed, so approving
   twice must not double-count and rejecting must remove the credit.
2. **The mirror holds.** After any verdict, stats.json's `climb_cutter` equals
   savings.json's total. Two files, written in sequence, with no transaction —
   so this is exactly the kind of thing that drifts silently.

Note what these numbers mean: the original video is never deleted, so the ledger
is a projection ("if you deleted these originals you would reclaim X"), not a
record of bytes actually freed.

Both `SAVINGS_PATH` and `stats.STATS_PATH` are redirected to tmp_path.
"""

import json

import pytest

pytestmark = pytest.mark.slow   # motion_review imports utils -> torch (~2.4s)


# ── _saved_bytes ──────────────────────────────────────────────────────────────

class TestSavedBytes:
    def test_savings_are_proportional_to_the_duration_removed(self, tmp_motion_db):
        """Half the footage cut from a 100MB clip projects 50MB."""
        mr = tmp_motion_db.module
        assert mr._saved_bytes(100_000_000, 60.0, 30.0) == 50_000_000

    def test_cutting_nothing_saves_nothing(self, tmp_motion_db):
        mr = tmp_motion_db.module
        assert mr._saved_bytes(100_000_000, 60.0, 60.0) == 0

    def test_cutting_everything_projects_the_whole_file(self, tmp_motion_db):
        mr = tmp_motion_db.module
        assert mr._saved_bytes(100_000_000, 60.0, 0.0) == 100_000_000

    @pytest.mark.parametrize("size,orig,trimmed", [
        (0, 60.0, 30.0),        # unknown file size
        (100, 0, 30.0),         # unknown original duration
        (100, -5.0, 1.0),       # nonsense duration
        (100, None, 30.0),
    ])
    def test_missing_or_nonsense_inputs_project_zero_rather_than_crashing(
        self, tmp_motion_db, size, orig, trimmed
    ):
        mr = tmp_motion_db.module
        assert mr._saved_bytes(size, orig, trimmed) == 0

    def test_a_longer_output_than_input_never_goes_negative(self, tmp_motion_db):
        """A slowed-down clip can run longer than its source; that is not a
        negative saving."""
        mr = tmp_motion_db.module
        assert mr._saved_bytes(100_000_000, 60.0, 120.0) == 0

    def test_a_missing_trimmed_duration_is_read_as_zero(self, tmp_motion_db):
        mr = tmp_motion_db.module
        assert mr._saved_bytes(100_000_000, 60.0, None) == 100_000_000


# ── the ledger ────────────────────────────────────────────────────────────────

class TestLedgerReads:
    def test_a_missing_ledger_reads_as_empty(self, tmp_motion_db):
        assert tmp_motion_db.module.get_savings() == {"total_bytes": 0, "per_video": {}}

    def test_a_corrupt_ledger_reads_as_empty_rather_than_crashing(self, tmp_motion_db):
        tmp_motion_db.savings.write_text("{{{ not json")
        assert tmp_motion_db.module.get_savings()["total_bytes"] == 0


class TestLedgerIdempotence:
    def test_approving_records_the_videos_savings(self, tmp_motion_db):
        mr = tmp_motion_db.module
        total = mr._apply_savings("vid1", "approve", 5_000_000)
        assert total == 5_000_000
        assert mr.get_savings()["per_video"] == {"vid1": 5_000_000}

    def test_approving_the_same_video_twice_does_not_double_count(self, tmp_motion_db):
        """Re-reviewing a clip is a normal workflow, not an edge case."""
        mr = tmp_motion_db.module
        mr._apply_savings("vid1", "approve", 5_000_000)
        total = mr._apply_savings("vid1", "approve", 5_000_000)
        assert total == 5_000_000

    def test_re_approving_with_a_new_edit_replaces_the_old_figure(self, tmp_motion_db):
        mr = tmp_motion_db.module
        mr._apply_savings("vid1", "approve", 5_000_000)
        total = mr._apply_savings("vid1", "approve", 8_000_000)
        assert total == 8_000_000
        assert mr.get_savings()["per_video"] == {"vid1": 8_000_000}

    def test_rejecting_removes_the_credit(self, tmp_motion_db):
        """Rejected footage is being kept, so it saves nothing."""
        mr = tmp_motion_db.module
        mr._apply_savings("vid1", "approve", 5_000_000)
        assert mr._apply_savings("vid1", "reject", 0) == 0
        assert mr.get_savings()["per_video"] == {}

    def test_rejecting_a_video_that_was_never_approved_is_a_no_op(self, tmp_motion_db):
        mr = tmp_motion_db.module
        assert mr._apply_savings("never-seen", "reject", 0) == 0

    def test_videos_accumulate_independently(self, tmp_motion_db):
        mr = tmp_motion_db.module
        mr._apply_savings("vid1", "approve", 1_000_000)
        mr._apply_savings("vid2", "approve", 2_000_000)
        assert mr._apply_savings("vid3", "approve", 3_000_000) == 6_000_000

    def test_rejecting_one_video_leaves_the_others_intact(self, tmp_motion_db):
        mr = tmp_motion_db.module
        mr._apply_savings("vid1", "approve", 1_000_000)
        mr._apply_savings("vid2", "approve", 2_000_000)
        assert mr._apply_savings("vid1", "reject", 0) == 2_000_000
        assert mr.get_savings()["per_video"] == {"vid2": 2_000_000}

    def test_the_total_is_always_the_sum_of_its_parts(self, tmp_motion_db):
        """Property check over a churn of flips."""
        mr = tmp_motion_db.module
        for vid, verdict, saved in [
            ("a", "approve", 100), ("b", "approve", 200), ("a", "reject", 0),
            ("c", "approve", 300), ("b", "approve", 250), ("c", "reject", 0),
            ("a", "approve", 50),
        ]:
            total = mr._apply_savings(vid, verdict, saved)
            ledger = mr.get_savings()
            assert total == sum(ledger["per_video"].values())
            assert ledger["total_bytes"] == total


class TestStatsMirror:
    """savings.json is the ledger; stats.json's climb_cutter slot mirrors its total."""

    def test_the_mirror_matches_after_an_approval(self, tmp_motion_db):
        import stats

        mr = tmp_motion_db.module
        mr._apply_savings("vid1", "approve", 5_000_000)
        assert stats.get_stats()["reclaimed_breakdown"]["climb_cutter"] == 5_000_000

    def test_the_mirror_follows_the_ledger_back_down_on_reject(self, tmp_motion_db):
        import stats

        mr = tmp_motion_db.module
        mr._apply_savings("vid1", "approve", 5_000_000)
        mr._apply_savings("vid1", "reject", 0)
        assert stats.get_stats()["reclaimed_breakdown"]["climb_cutter"] == 0

    def test_the_mirror_holds_across_a_churn_of_verdicts(self, tmp_motion_db):
        import stats

        mr = tmp_motion_db.module
        for vid, verdict, saved in [
            ("a", "approve", 111), ("b", "approve", 222), ("a", "approve", 333),
            ("b", "reject", 0), ("c", "approve", 444),
        ]:
            mr._apply_savings(vid, verdict, saved)
            assert (stats.get_stats()["reclaimed_breakdown"]["climb_cutter"]
                    == mr.get_savings()["total_bytes"])

    def test_video_savings_never_disturb_photo_bytes(self, tmp_motion_db):
        """The reason climb_cutter has its own slot instead of one shared scalar."""
        import stats

        stats.update_stats(1, exact_bytes=4_000_000)
        tmp_motion_db.module._apply_savings("vid1", "approve", 5_000_000)

        breakdown = stats.get_stats()["reclaimed_breakdown"]
        assert breakdown["photos_exact"] == 4_000_000
        assert breakdown["climb_cutter"] == 5_000_000
        assert stats.get_stats()["reclaimed_bytes"] == 9_000_000


# ── record_decision ───────────────────────────────────────────────────────────

@pytest.fixture
def reviewable(tmp_motion_db, tmp_path):
    """A proposal whose source file actually exists, with a known size."""
    source = tmp_path / "vid1.mov"
    source.write_bytes(b"x" * 1_000_000)
    tmp_motion_db.proposal(
        "vid1",
        source_path=str(source),
        original_duration=60.0,
        trimmed_duration=40.0,
        cut_segments=[{"start": 40.0, "end": 60.0}],
    )
    return tmp_motion_db


class TestRecordDecisionValidation:
    @pytest.mark.parametrize("verdict", ["maybe", "", "APPROVE", None, "delete"])
    def test_an_unknown_verdict_is_refused(self, reviewable, verdict):
        with pytest.raises(ValueError, match="invalid verdict"):
            reviewable.module.record_decision("vid1", verdict)

    def test_a_video_with_no_proposal_is_refused(self, reviewable):
        with pytest.raises(FileNotFoundError):
            reviewable.module.record_decision("no-such-video", "approve")

    def test_a_refused_decision_writes_nothing(self, reviewable):
        """A rejected input must not leave a half-recorded verdict behind."""
        with pytest.raises(ValueError):
            reviewable.module.record_decision("vid1", "nonsense")
        assert not reviewable.decisions.exists()
        assert list(reviewable.reviews.iterdir()) == []


class TestRecordDecisionBookkeeping:
    def test_approving_credits_the_ledger(self, reviewable):
        result = reviewable.module.record_decision("vid1", "approve")
        # 20s of 60s removed from a 1,000,000-byte file
        assert result["video_saved_bytes"] == pytest.approx(333_333, abs=2)
        assert result["savings_total_bytes"] == result["video_saved_bytes"]

    def test_rejecting_after_approving_removes_the_credit(self, reviewable):
        reviewable.module.record_decision("vid1", "approve")
        result = reviewable.module.record_decision("vid1", "reject")
        assert result["savings_total_bytes"] == 0

    def test_the_audit_log_is_append_only(self, reviewable):
        """decisions.jsonl is the history; flipping a verdict adds a line, never
        rewrites one."""
        reviewable.module.record_decision("vid1", "approve")
        reviewable.module.record_decision("vid1", "reject")
        reviewable.module.record_decision("vid1", "approve")

        lines = reviewable.decisions.read_text().strip().split("\n")
        assert len(lines) == 3
        assert [json.loads(line)["verdict"] for line in lines] == [
            "approve", "reject", "approve"
        ]

    def test_every_audit_line_is_valid_json(self, reviewable):
        reviewable.module.record_decision("vid1", "approve")
        reviewable.module.record_decision("vid1", "reject")
        for line in reviewable.decisions.read_text().strip().split("\n"):
            json.loads(line)

    def test_the_review_file_holds_only_the_latest_state(self, reviewable):
        reviewable.module.record_decision("vid1", "approve")
        reviewable.module.record_decision("vid1", "reject")
        review = json.loads((reviewable.reviews / "vid1.json").read_text())
        assert review["verdict"] == "reject"

    def test_the_backend_recomputes_cuts_rather_than_trusting_the_client(
        self, reviewable
    ):
        """The frontend's numbers are only a preview — a POST claiming an absurd
        trimmed_duration must not set the savings figure."""
        result = reviewable.module.record_decision(
            "vid1", "approve",
            regions=[{"type": "cut", "start": 0.0, "end": 30.0}],
        )
        assert result["trimmed_duration"] == pytest.approx(30.0)
        assert result["cut_segments"] == [{"start": 0.0, "end": 30.0}]

    def test_client_regions_are_sanitized_before_being_recorded(self, reviewable):
        """Out-of-range bounds from the wire get clamped to the video."""
        result = reviewable.module.record_decision(
            "vid1", "approve",
            regions=[{"type": "cut", "start": -100.0, "end": 9999.0}],
        )
        for reg in result["regions"]:
            assert reg["start"] >= 0.0
            assert reg["end"] <= 60.0

    def test_an_edit_is_flagged_as_edited(self, reviewable):
        result = reviewable.module.record_decision(
            "vid1", "approve",
            regions=[{"type": "cut", "start": 5.0, "end": 10.0}],
        )
        assert result["edited"] is True

    def test_accepting_the_proposal_unchanged_is_not_an_edit(self, reviewable):
        result = reviewable.module.record_decision("vid1", "approve")
        assert result["edited"] is False

    def test_edits_are_ignored_on_a_reject(self, reviewable):
        """Rejecting means "keep the original" — there is nothing to apply."""
        result = reviewable.module.record_decision(
            "vid1", "reject",
            regions=[{"type": "cut", "start": 5.0, "end": 10.0}],
        )
        assert result["edited"] is False

    def test_a_source_file_that_has_vanished_records_zero_savings(
        self, tmp_motion_db
    ):
        """The proposal outlives the file if the user moved it — not a crash."""
        tmp_motion_db.proposal("vid2", source_path="/gone/missing.mov",
                               original_duration=60.0)
        result = tmp_motion_db.module.record_decision("vid2", "approve")
        assert result["video_saved_bytes"] == 0
