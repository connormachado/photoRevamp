"""
Edit-boundary registry — region hygiene and plan building
=========================================================
`edit_boundaries` is the contract between the review room and the renderer: a
region list comes off the wire, `sanitize_regions` makes it trustworthy, and
`build_plan` turns it into the ordered Pieces ffmpeg executes.

These tests assert the *contract* stated in the module docstring — "regions are
sorted by start and never overlap", "time not covered by a region is an implicit
keep at speed 1" — rather than re-deriving the current arithmetic.

Cheap: this module is stdlib-only and imports in ~0.03s.
"""

import pytest

import edit_boundaries as eb


# ── sanitize_regions ──────────────────────────────────────────────────────────

class TestSanitizeEmptyAndGarbage:
    """A malformed POST must never produce garbage regions."""

    @pytest.mark.parametrize("bad", [None, [], [None], ["string"], [42], [{}]])
    def test_nothing_usable_yields_no_regions(self, bad):
        assert eb.sanitize_regions(bad, 60.0) == []

    def test_unknown_type_is_dropped_entirely(self):
        regs = [{"type": "teleport", "start": 1.0, "end": 5.0}]
        assert eb.sanitize_regions(regs, 60.0) == []

    def test_region_missing_start_or_end_is_dropped(self):
        assert eb.sanitize_regions([{"type": "cut", "start": 1.0}], 60.0) == []
        assert eb.sanitize_regions([{"type": "cut", "end": 5.0}], 60.0) == []

    def test_non_numeric_bounds_are_dropped_not_raised(self):
        regs = [{"type": "cut", "start": "abc", "end": 5.0}]
        assert eb.sanitize_regions(regs, 60.0) == []

    def test_missing_type_defaults_to_cut(self):
        [reg] = eb.sanitize_regions([{"start": 1.0, "end": 5.0}], 60.0)
        assert reg["type"] == eb.DEFAULT_TYPE_ID == "cut"


class TestSanitizeClamping:
    """Regions are clamped into [0, duration] — the video's real extent."""

    def test_negative_start_clamps_to_zero(self):
        [reg] = eb.sanitize_regions([{"type": "cut", "start": -10.0, "end": 5.0}], 60.0)
        assert reg["start"] == 0.0

    def test_end_past_duration_clamps_to_duration(self):
        [reg] = eb.sanitize_regions([{"type": "cut", "start": 50.0, "end": 999.0}], 60.0)
        assert reg["end"] == 60.0

    def test_region_entirely_past_duration_is_dropped(self):
        regs = [{"type": "cut", "start": 90.0, "end": 120.0}]
        assert eb.sanitize_regions(regs, 60.0) == []

    def test_inverted_region_is_dropped(self):
        """end before start is nonsense, not a region to be silently flipped."""
        regs = [{"type": "cut", "start": 30.0, "end": 10.0}]
        assert eb.sanitize_regions(regs, 60.0) == []

    @pytest.mark.parametrize("width", [0.0, 0.0005, 0.001])
    def test_spans_at_or_below_the_epsilon_are_dropped(self, width):
        """A sub-millisecond region can't be rendered; it must not survive."""
        regs = [{"type": "cut", "start": 10.0, "end": 10.0 + width}]
        assert eb.sanitize_regions(regs, 60.0) == []

    def test_span_just_above_the_epsilon_survives(self):
        regs = [{"type": "cut", "start": 10.0, "end": 10.002}]
        assert len(eb.sanitize_regions(regs, 60.0)) == 1

    def test_bounds_are_rounded_to_milliseconds(self):
        regs = [{"type": "cut", "start": 1.23456789, "end": 5.98765432}]
        [reg] = eb.sanitize_regions(regs, 60.0)
        assert reg["start"] == 1.235
        assert reg["end"] == 5.988


class TestSanitizeOrderingAndOverlap:
    """The stated invariant: sorted by start, never overlapping."""

    def test_out_of_order_input_comes_back_sorted(self):
        regs = [
            {"type": "cut", "start": 40.0, "end": 45.0},
            {"type": "cut", "start": 10.0, "end": 15.0},
            {"type": "cut", "start": 25.0, "end": 30.0},
        ]
        out = eb.sanitize_regions(regs, 60.0)
        assert [r["start"] for r in out] == [10.0, 25.0, 40.0]

    def test_overlapping_same_type_regions_merge_into_one(self):
        regs = [
            {"type": "cut", "start": 10.0, "end": 20.0},
            {"type": "cut", "start": 15.0, "end": 30.0},
        ]
        [reg] = eb.sanitize_regions(regs, 60.0)
        assert (reg["start"], reg["end"]) == (10.0, 30.0)

    def test_fully_contained_duplicate_does_not_shrink_its_host(self):
        """max() on the merge — a nested region must not truncate the outer one."""
        regs = [
            {"type": "cut", "start": 10.0, "end": 30.0},
            {"type": "cut", "start": 15.0, "end": 20.0},
        ]
        [reg] = eb.sanitize_regions(regs, 60.0)
        assert (reg["start"], reg["end"]) == (10.0, 30.0)

    def test_same_type_with_different_params_does_not_merge(self):
        """Two different speeds are two different transforms — truncate, don't merge."""
        regs = [
            {"type": "speed", "start": 10.0, "end": 20.0,
             "params": {"direction": "up", "magnitude": 2.0}},
            {"type": "speed", "start": 15.0, "end": 30.0,
             "params": {"direction": "up", "magnitude": 4.0}},
        ]
        out = eb.sanitize_regions(regs, 60.0)
        assert len(out) == 2
        assert out[0]["end"] == out[1]["start"] == 20.0

    def test_different_types_overlapping_truncates_the_later_one(self):
        """A span can only carry one transform, so the second yields."""
        regs = [
            {"type": "cut", "start": 10.0, "end": 20.0},
            {"type": "speed", "start": 15.0, "end": 30.0},
        ]
        out = eb.sanitize_regions(regs, 60.0)
        assert len(out) == 2
        assert (out[0]["type"], out[0]["start"], out[0]["end"]) == ("cut", 10.0, 20.0)
        assert (out[1]["type"], out[1]["start"], out[1]["end"]) == ("speed", 20.0, 30.0)

    def test_truncation_that_leaves_a_sliver_drops_it(self):
        regs = [
            {"type": "cut", "start": 10.0, "end": 20.0},
            {"type": "speed", "start": 15.0, "end": 20.0005},
        ]
        out = eb.sanitize_regions(regs, 60.0)
        assert [r["type"] for r in out] == ["cut"]

    def test_touching_regions_stay_separate_and_do_not_overlap(self):
        """Adjacent (not overlapping) regions are left alone; neither overlaps."""
        regs = [
            {"type": "cut", "start": 10.0, "end": 20.0},
            {"type": "cut", "start": 20.0, "end": 30.0},
        ]
        out = eb.sanitize_regions(regs, 60.0)
        for a, b in zip(out, out[1:]):
            assert a["end"] <= b["start"]

    def test_output_never_overlaps_for_a_messy_pile_of_input(self):
        """Property check across mixed types, orders and overlaps."""
        regs = [
            {"type": "cut", "start": 30.0, "end": 40.0},
            {"type": "speed", "start": 5.0, "end": 12.0},
            {"type": "cut", "start": 8.0, "end": 15.0},
            {"type": "cut", "start": 14.0, "end": 35.0},
            {"type": "speed", "start": -5.0, "end": 3.0},
        ]
        out = eb.sanitize_regions(regs, 60.0)
        for a, b in zip(out, out[1:]):
            assert a["end"] <= b["start"], f"{a} overlaps {b}"
            assert a["start"] <= b["start"]


class TestSanitizeParamsAndIds:
    def test_defaults_are_filled_in_from_the_registry(self):
        [reg] = eb.sanitize_regions([{"type": "speed", "start": 1.0, "end": 5.0}], 60.0)
        assert reg["params"] == eb.REGISTRY["speed"].default_params

    def test_supplied_params_override_defaults_but_keep_the_rest(self):
        regs = [{"type": "speed", "start": 1.0, "end": 5.0, "params": {"magnitude": 8.0}}]
        [reg] = eb.sanitize_regions(regs, 60.0)
        assert reg["params"]["magnitude"] == 8.0
        assert reg["params"]["direction"] == "up"   # default survives

    def test_an_existing_id_is_preserved(self):
        regs = [{"id": "r-keepme", "type": "cut", "start": 1.0, "end": 5.0}]
        [reg] = eb.sanitize_regions(regs, 60.0)
        assert reg["id"] == "r-keepme"

    def test_a_missing_id_is_generated(self):
        [reg] = eb.sanitize_regions([{"type": "cut", "start": 1.0, "end": 5.0}], 60.0)
        assert reg["id"].startswith("r-")


# ── _effective_speed ──────────────────────────────────────────────────────────

class TestEffectiveSpeed:
    def test_defaults_to_2x_up(self):
        assert eb._effective_speed({}) == 2.0

    def test_up_is_the_magnitude(self):
        assert eb._effective_speed({"direction": "up", "magnitude": 4.0}) == 4.0

    def test_down_is_the_reciprocal(self):
        assert eb._effective_speed({"direction": "down", "magnitude": 4.0}) == 0.25

    @pytest.mark.parametrize("mag", [0.0, -3.0, 0.5])
    def test_magnitude_below_one_clamps_to_one(self, mag):
        """Clamping at 1 is also what keeps `down` from dividing by zero."""
        assert eb._effective_speed({"direction": "up", "magnitude": mag}) == 1.0
        assert eb._effective_speed({"direction": "down", "magnitude": mag}) == 1.0

    def test_magnitude_above_the_ceiling_clamps(self):
        assert eb._effective_speed({"magnitude": 9999.0}) == eb.SPEED_MAX_MAGNITUDE

    @pytest.mark.parametrize("junk", [{"magnitude": "abc"}, {"magnitude": None},
                                      {"magnitude": []}, None])
    def test_unparseable_magnitude_falls_back_to_the_default(self, junk):
        assert eb._effective_speed(junk) == 2.0

    def test_numeric_string_magnitude_is_accepted(self):
        assert eb._effective_speed({"magnitude": "3"}) == 3.0


# ── build_plan ────────────────────────────────────────────────────────────────

class TestBuildPlanBasics:
    def test_no_regions_keeps_the_whole_video_as_one_plain_piece(self):
        [piece] = eb.build_plan([], 60.0)
        assert (piece.start, piece.end) == (0.0, 60.0)
        assert piece.is_plain

    def test_zero_duration_produces_no_pieces(self):
        assert eb.build_plan([], 0.0) == []

    def test_a_middle_cut_leaves_the_two_surrounding_gaps(self):
        regs = [{"type": "cut", "start": 20.0, "end": 30.0, "params": {}}]
        plan = eb.build_plan(regs, 60.0)
        assert [(p.start, p.end) for p in plan] == [(0.0, 20.0), (30.0, 60.0)]

    def test_a_cut_at_the_very_start_emits_no_leading_gap(self):
        regs = [{"type": "cut", "start": 0.0, "end": 10.0, "params": {}}]
        plan = eb.build_plan(regs, 60.0)
        assert [(p.start, p.end) for p in plan] == [(10.0, 60.0)]

    def test_a_cut_running_to_the_end_emits_no_trailing_gap(self):
        regs = [{"type": "cut", "start": 50.0, "end": 60.0, "params": {}}]
        plan = eb.build_plan(regs, 60.0)
        assert [(p.start, p.end) for p in plan] == [(0.0, 50.0)]

    def test_a_cut_spanning_everything_leaves_nothing(self):
        regs = [{"type": "cut", "start": 0.0, "end": 60.0, "params": {}}]
        assert eb.build_plan(regs, 60.0) == []

    def test_gaps_below_the_epsilon_are_not_emitted_as_pieces(self):
        """A 0.5ms sliver between two cuts is not a renderable piece."""
        regs = [
            {"type": "cut", "start": 0.0, "end": 20.0, "params": {}},
            {"type": "cut", "start": 20.0005, "end": 40.0, "params": {}},
        ]
        plan = eb.build_plan(regs, 60.0)
        assert [(p.start, p.end) for p in plan] == [(40.0, 60.0)]

    def test_regions_are_walked_in_time_order_regardless_of_input_order(self):
        regs = [
            {"type": "cut", "start": 40.0, "end": 50.0, "params": {}},
            {"type": "cut", "start": 10.0, "end": 20.0, "params": {}},
        ]
        plan = eb.build_plan(regs, 60.0)
        assert [(p.start, p.end) for p in plan] == [(0.0, 10.0), (20.0, 40.0), (50.0, 60.0)]

    def test_region_bounds_are_clamped_to_the_video(self):
        regs = [{"type": "cut", "start": -5.0, "end": 999.0, "params": {}}]
        assert eb.build_plan(regs, 60.0) == []


class TestBuildPlanSpeed:
    def test_a_speed_region_keeps_its_footage_but_retimes_it(self):
        regs = [{"type": "speed", "start": 20.0, "end": 40.0,
                 "params": {"direction": "up", "magnitude": 2.0}}]
        plan = eb.build_plan(regs, 60.0)
        middle = plan[1]
        assert (middle.start, middle.end) == (20.0, 40.0)
        assert middle.speed == 2.0
        assert middle.source_duration == 20.0
        assert middle.output_duration == 10.0

    def test_slowing_down_lengthens_the_output(self):
        regs = [{"type": "speed", "start": 0.0, "end": 10.0,
                 "params": {"direction": "down", "magnitude": 2.0}}]
        [piece] = eb.build_plan(regs, 10.0)
        assert piece.speed == 0.5
        assert piece.output_duration == 20.0

    def test_speed_at_1x_stays_on_the_fast_render_path(self):
        """A 1x speed region must emit a PLAIN piece.

        Documented invariant: a no-op speed region must not drag the whole render
        off the concat-demuxer path and onto filter_complex.
        """
        regs = [{"type": "speed", "start": 20.0, "end": 40.0,
                 "params": {"direction": "up", "magnitude": 1.0}}]
        plan = eb.build_plan(regs, 60.0, fps=59.97)
        assert all(p.is_plain for p in plan)

    def test_fps_is_pinned_onto_a_retimed_piece(self):
        """setpts multiplies the frame rate; the fps filter normalises it back."""
        regs = [{"type": "speed", "start": 0.0, "end": 10.0,
                 "params": {"direction": "up", "magnitude": 2.0}}]
        [piece] = eb.build_plan(regs, 10.0, fps=59.97)
        assert piece.vf == ("fps=59.97",)

    def test_without_a_known_fps_no_filter_is_invented(self):
        regs = [{"type": "speed", "start": 0.0, "end": 10.0,
                 "params": {"direction": "up", "magnitude": 2.0}}]
        [piece] = eb.build_plan(regs, 10.0, fps=None)
        assert piece.vf == ()

    def test_a_speed_region_is_not_plain_so_it_forces_the_filter_path(self):
        regs = [{"type": "speed", "start": 0.0, "end": 10.0,
                 "params": {"direction": "up", "magnitude": 3.0}}]
        [piece] = eb.build_plan(regs, 10.0)
        assert not piece.is_plain

    def test_cut_and_speed_compose(self):
        regs = [
            {"type": "cut", "start": 10.0, "end": 20.0, "params": {}},
            {"type": "speed", "start": 30.0, "end": 40.0,
             "params": {"direction": "up", "magnitude": 2.0}},
        ]
        plan = eb.build_plan(regs, 60.0)
        assert [(p.start, p.end) for p in plan] == [
            (0.0, 10.0), (20.0, 30.0), (30.0, 40.0), (40.0, 60.0)
        ]
        # 10 + 10 + (10/2) + 20
        assert eb.plan_output_duration(plan) == 45.0


class TestBuildPlanUnknownType:
    @pytest.mark.xfail(
        strict=True,
        reason="DESIGN QUESTION for Connor, not a clear-cut bug — flagged rather "
               "than silently encoded. build_plan DROPS footage under an "
               "unrecognised region type: get_type returns None so no Pieces are "
               "emitted, yet `cursor` still advances past the span, which is "
               "exactly what a cut does. The frontend reaches the same outcome by "
               "a different route and DOCUMENTS it (boundaryTypes.getType falls "
               "back to `cut` so a 'stale/foreign region still renders'), so the "
               "two halves agree — this is not a mirror divergence. The open "
               "question is whether dropping is the right default at all: the "
               "reachable case is a draft or review written by a newer build that "
               "registered a type this one lacks, and losing the user's footage is "
               "a harsh failure mode for 'I don't recognise this'. Keeping the "
               "span (treating unknown as no-op) is the safer reading. "
               "sanitize_regions strips unknown types first, so normal API traffic "
               "never gets here. Decide the intent, then this test flips to pass.",
    )
    def test_unknown_type_should_keep_footage_rather_than_drop_it(self):
        regs = [{"type": "from_the_future", "start": 20.0, "end": 30.0, "params": {}}]
        plan = eb.build_plan(regs, 60.0)
        covered = sum(p.source_duration for p in plan)
        assert covered == pytest.approx(60.0), (
            "footage under an unknown region type was dropped from the plan"
        )


# ── plan helpers ──────────────────────────────────────────────────────────────

class TestPlanHelpers:
    def test_output_duration_of_an_empty_plan_is_zero(self):
        assert eb.plan_output_duration([]) == 0.0
        assert eb.plan_output_duration(None) == 0.0

    def test_output_duration_sums_plain_pieces(self):
        plan = [eb.Piece(0.0, 10.0), eb.Piece(20.0, 35.0)]
        assert eb.plan_output_duration(plan) == 25.0

    def test_plan_to_segments_reports_source_spans_not_output_spans(self):
        """keep_segments are positions in the ORIGINAL file, so speed is irrelevant."""
        plan = [eb.Piece(0.0, 10.0), eb.Piece(20.0, 40.0, speed=4.0)]
        assert eb.plan_to_segments(plan) == [
            {"start": 0.0, "end": 10.0},
            {"start": 20.0, "end": 40.0},
        ]

    def test_piece_output_duration_never_divides_by_zero(self):
        assert eb.Piece(0.0, 10.0, speed=0.0).output_duration == 10.0

    def test_piece_duration_is_never_negative(self):
        assert eb.Piece(30.0, 10.0).source_duration == 0.0


# ── legacy cut-list interop ───────────────────────────────────────────────────

class TestCutListInterop:
    def test_legacy_dict_cuts_upgrade_to_cut_regions(self):
        out = eb.regions_from_cuts([{"start": 5.0, "end": 9.0}])
        assert out[0]["type"] == "cut"
        assert (out[0]["start"], out[0]["end"]) == (5.0, 9.0)

    def test_legacy_tuple_cuts_are_accepted(self):
        [reg] = eb.regions_from_cuts([(5.0, 9.0)])
        assert (reg["start"], reg["end"]) == (5.0, 9.0)

    def test_malformed_legacy_entries_are_skipped_not_raised(self):
        assert eb.regions_from_cuts([{"start": 1.0}, "junk", None, (), (1,)]) == []

    def test_regions_to_cuts_reports_only_footage_removing_types(self):
        regs = [
            {"type": "cut", "start": 10.0, "end": 20.0, "params": {}},
            {"type": "speed", "start": 30.0, "end": 40.0, "params": {}},
        ]
        assert eb.regions_to_cuts(regs) == [{"start": 10.0, "end": 20.0}]

    def test_regions_to_cuts_is_sorted(self):
        regs = [
            {"type": "cut", "start": 40.0, "end": 50.0, "params": {}},
            {"type": "cut", "start": 10.0, "end": 20.0, "params": {}},
        ]
        assert [c["start"] for c in eb.regions_to_cuts(regs)] == [10.0, 40.0]

    def test_cut_regions_survive_a_round_trip(self):
        original = eb.sanitize_regions(
            [{"type": "cut", "start": 10.0, "end": 20.0}], 60.0
        )
        round_tripped = eb.regions_from_cuts(eb.regions_to_cuts(original))
        assert eb.regions_equal(original, round_tripped)


class TestRegionsEqual:
    def test_ids_are_ignored(self):
        a = [{"id": "r-1", "type": "cut", "start": 1.0, "end": 2.0, "params": {}}]
        b = [{"id": "r-2", "type": "cut", "start": 1.0, "end": 2.0, "params": {}}]
        assert eb.regions_equal(a, b)

    def test_differing_params_are_not_equal(self):
        a = [{"type": "speed", "start": 1.0, "end": 2.0, "params": {"magnitude": 2.0}}]
        b = [{"type": "speed", "start": 1.0, "end": 2.0, "params": {"magnitude": 4.0}}]
        assert not eb.regions_equal(a, b)

    def test_param_ordering_does_not_matter(self):
        a = [{"type": "speed", "start": 1.0, "end": 2.0,
              "params": {"direction": "up", "magnitude": 2.0}}]
        b = [{"type": "speed", "start": 1.0, "end": 2.0,
              "params": {"magnitude": 2.0, "direction": "up"}}]
        assert eb.regions_equal(a, b)

    def test_sub_millisecond_drift_compares_equal(self):
        """Float noise from a UI drag must not read as an unsaved change."""
        a = [{"type": "cut", "start": 1.00001, "end": 2.0, "params": {}}]
        b = [{"type": "cut", "start": 1.00002, "end": 2.0, "params": {}}]
        assert eb.regions_equal(a, b)

    def test_both_empty_are_equal(self):
        assert eb.regions_equal([], [])
        assert eb.regions_equal(None, [])

    def test_different_lengths_are_not_equal(self):
        a = [{"type": "cut", "start": 1.0, "end": 2.0, "params": {}}]
        assert not eb.regions_equal(a, [])
