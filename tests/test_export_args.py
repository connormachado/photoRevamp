"""
Export argument building
========================
`render_plan` turns a list of Pieces into an ffmpeg invocation. The interesting
logic is not the subprocess call — it is the *argument construction*, which is
where the four documented "ways to silently corrupt an export" live
(backend/CLAUDE.md).

The single most load-bearing decision is the strategy branch: a plan whose pieces
are all straight copies takes the concat-demuxer path (byte-identical to what the
Climb Cutter has always produced); one transforming piece switches the whole
render to filter_complex, which then needs a second stream-copy pass to strip the
display matrix or the clip lands sideways in Photos.

Nothing here runs ffmpeg. `fake_run` records the argv instead.
"""

import re

import pytest

from edit_boundaries import Piece

pytestmark = pytest.mark.slow   # export_video imports utils -> torch (~2s)


def flat(cmd) -> str:
    """The whole argv as one string, for substring assertions."""
    return " ".join(str(a) for a in cmd)


# ── _setpts ───────────────────────────────────────────────────────────────────

class TestSetpts:
    def test_speed_1_only_rebases_timestamps(self):
        """No division at 1x — the piece must stay a straight copy."""
        import export_video

        assert export_video._setpts(1.0) == "setpts=PTS-STARTPTS"

    def test_speeding_up_divides_the_presentation_timestamps(self):
        import export_video

        assert export_video._setpts(2.0) == "setpts=(PTS-STARTPTS)/2.000000"

    def test_slowing_down_uses_a_fractional_divisor(self):
        import export_video

        assert export_video._setpts(0.5) == "setpts=(PTS-STARTPTS)/0.500000"

    def test_float_noise_around_1_still_counts_as_no_change(self):
        import export_video

        assert export_video._setpts(1.0 + 1e-12) == "setpts=PTS-STARTPTS"


# ── _atempo_chain ─────────────────────────────────────────────────────────────

def atempo_product(chain: list) -> float:
    product = 1.0
    for f in chain:
        product *= float(f.split("=")[1])
    return product


class TestAtempoChain:
    """atempo clamps to 0.5–2.0 per instance, so bigger ramps must be chained."""

    def test_no_filter_at_1x(self):
        import export_video

        assert export_video._atempo_chain(1.0) == []

    def test_a_speed_inside_the_legal_range_needs_only_one_filter(self):
        import export_video

        assert export_video._atempo_chain(1.5) == ["atempo=1.500000"]

    def test_4x_is_split_into_two_filters(self):
        import export_video

        assert len(export_video._atempo_chain(4.0)) == 2

    def test_quarter_speed_is_split_into_two_filters(self):
        import export_video

        assert len(export_video._atempo_chain(0.25)) == 2

    @pytest.mark.parametrize("speed", [0.05, 0.1, 0.25, 0.5, 0.75, 1.5, 2.0,
                                       3.0, 4.0, 7.5, 16.0, 20.0])
    def test_the_chain_multiplies_out_to_the_requested_speed(self, speed):
        """The property that actually matters: audio ends up at the right rate."""
        import export_video

        chain = export_video._atempo_chain(speed)
        assert atempo_product(chain) == pytest.approx(speed, rel=1e-5)

    @pytest.mark.parametrize("speed", [0.05, 0.1, 0.25, 0.5, 3.0, 4.0, 16.0, 20.0])
    def test_every_link_stays_within_ffmpegs_legal_range(self, speed):
        """A single atempo outside 0.5-2.0 is rejected by ffmpeg outright."""
        import export_video

        for f in export_video._atempo_chain(speed):
            value = float(f.split("=")[1])
            assert 0.5 - 1e-9 <= value <= 2.0 + 1e-9, f"{f} is outside atempo's range"

    def test_the_extreme_speeds_the_ui_allows_are_expressible(self):
        """The registry clamps magnitude to [1, 20], so 20x and 1/20x are the bounds."""
        import export_video

        assert atempo_product(export_video._atempo_chain(20.0)) == pytest.approx(20.0)
        assert atempo_product(export_video._atempo_chain(0.05)) == pytest.approx(0.05)


# ── _metadata_args ────────────────────────────────────────────────────────────

class TestMetadataArgs:
    def test_no_metadata_produces_no_arguments(self):
        import export_video

        assert export_video._metadata_args({}) == []
        assert export_video._metadata_args({"date": None, "gps": None}) == []

    def test_a_date_becomes_a_creation_time_tag(self):
        import export_video

        args = export_video._metadata_args({"date": "2026-02-11T21:31:00-0500"})
        assert args == ["-metadata", "creation_time=2026-02-11T21:31:00-0500"]

    def test_gps_is_written_under_both_the_apple_and_generic_keys(self):
        """Photos reads the Apple key; the generic one keeps our own re-reads working."""
        import export_video

        args = export_video._metadata_args({"gps": "+43.6552-072.2412+180.799/"})
        assert "com.apple.quicktime.location.ISO6709=+43.6552-072.2412+180.799/" in args
        assert "location=+43.6552-072.2412+180.799/" in args

    def test_date_and_gps_are_both_emitted(self):
        import export_video

        args = export_video._metadata_args({"date": "2026-01-01T00:00:00Z", "gps": "+1-2/"})
        assert args.count("-metadata") == 3

    def test_an_empty_string_is_not_written_as_a_tag(self):
        """An empty creation_time would overwrite a good one with nothing."""
        import export_video

        assert export_video._metadata_args({"date": "", "gps": ""}) == []


# ── plan / segment normalisation ──────────────────────────────────────────────

class TestNormalizePlan:
    def test_nothing_in_nothing_out(self):
        import export_video

        assert export_video._normalize_plan([]) == []
        assert export_video._normalize_plan(None) == []

    def test_pieces_pass_through_with_their_transform_intact(self):
        """Losing speed or vf here would silently downgrade the render to a copy."""
        import export_video

        piece = Piece(0.0, 10.0, speed=2.0, vf=("fps=30",))
        [out] = export_video._normalize_plan([piece])
        assert out is piece
        assert out.speed == 2.0 and out.vf == ("fps=30",)

    def test_dicts_and_tuples_are_accepted_as_plain_pieces(self):
        import export_video

        out = export_video._normalize_plan([{"start": 0.0, "end": 5.0}, (10.0, 20.0)])
        assert [(p.start, p.end) for p in out] == [(0.0, 5.0), (10.0, 20.0)]
        assert all(p.is_plain for p in out)

    @pytest.mark.parametrize("junk", [
        {"start": 1.0}, {"end": 2.0}, "nonsense", None, (), (1,),
        {"start": "a", "end": "b"},
    ])
    def test_malformed_entries_are_skipped_not_raised(self, junk):
        import export_video

        assert export_video._normalize_plan([junk]) == []

    @pytest.mark.parametrize("span", [(5.0, 5.0), (5.0, 5.0005), (5.0, 4.0)])
    def test_unrenderable_spans_are_dropped(self, span):
        import export_video

        assert export_video._normalize_plan([span]) == []
        assert export_video._normalize_plan([Piece(*span)]) == []

    def test_order_is_preserved_because_a_plan_is_ordered(self):
        """Unlike segments, a plan's sequence IS the edit — never sort it."""
        import export_video

        out = export_video._normalize_plan([(30.0, 40.0), (0.0, 10.0)])
        assert [(p.start, p.end) for p in out] == [(30.0, 40.0), (0.0, 10.0)]

    def test_a_negative_start_is_clamped_to_the_start_of_the_video(self):
        import export_video

        [out] = export_video._normalize_plan([(-5.0, 10.0)])
        assert out.start == 0.0


class TestNormalizeSegments:
    def test_segments_are_sorted_because_they_are_a_set_of_spans(self):
        import export_video

        out = export_video._normalize_segments([{"start": 30.0, "end": 40.0},
                                                {"start": 0.0, "end": 10.0}])
        assert out == [(0.0, 10.0), (30.0, 40.0)]

    def test_malformed_entries_are_skipped(self):
        import export_video

        assert export_video._normalize_segments([{"start": 1.0}, None, "x", (1,)]) == []

    def test_empty_spans_are_dropped(self):
        import export_video

        assert export_video._normalize_segments([(5.0, 5.0)]) == []


# ── _filter_graph_cmd ─────────────────────────────────────────────────────────

class TestFilterGraphCmd:
    def test_audio_is_trimmed_in_lockstep_with_video(self, fake_run, ffmpeg_stderr,
                                                     tmp_path):
        """Video-only trimming desyncs the audio, which is the whole reason atrim
        and asetpts appear alongside every trim."""
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])

        cmd = export_video._filter_graph_cmd(tmp_path / "src.mov",
                                             [Piece(0.0, 5.0), Piece(10.0, 20.0)])
        graph = flat(cmd)
        assert graph.count("trim=start=") == 4    # 2 video (trim) + 2 audio (atrim)
        assert graph.count("atrim=start=") == 2
        assert "asetpts=PTS-STARTPTS" in graph

    def test_a_source_without_audio_gets_a_video_only_graph(self, fake_run,
                                                            ffmpeg_stderr, tmp_path):
        """`-map 0:a:0?`'s optionality has no filtergraph equivalent — mapping an
        audio stream that does not exist fails the whole render."""
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["silent_mp4"])

        cmd = export_video._filter_graph_cmd(tmp_path / "src.mp4", [Piece(0.0, 5.0)])
        graph = flat(cmd)
        assert "concat=n=1:v=1:a=0[vout]" in graph
        assert "[aout]" not in graph
        assert "atrim" not in graph

    def test_the_map_list_matches_the_graphs_outputs(self, fake_run, ffmpeg_stderr,
                                                     tmp_path):
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        cmd = export_video._filter_graph_cmd(tmp_path / "src.mov", [Piece(0.0, 5.0)])

        maps = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "-map"]
        assert maps == ["[vout]", "[aout]"]
        assert "concat=n=1:v=1:a=1[vout][aout]" in flat(cmd)

    def test_the_concat_count_matches_the_number_of_pieces(self, fake_run,
                                                           ffmpeg_stderr, tmp_path):
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        pieces = [Piece(0.0, 5.0), Piece(10.0, 15.0), Piece(20.0, 25.0)]
        cmd = export_video._filter_graph_cmd(tmp_path / "src.mov", pieces)
        assert "concat=n=3:" in flat(cmd)

    def test_a_pieces_own_filters_are_appended_after_setpts(self, fake_run,
                                                            ffmpeg_stderr, tmp_path):
        """Order matters: the fps pin has to run after setpts has retimed."""
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        piece = Piece(0.0, 5.0, speed=2.0, vf=("fps=59.97",))
        graph = flat(export_video._filter_graph_cmd(tmp_path / "src.mov", [piece]))

        chain = graph.split("[0:v]")[1].split("[v0]")[0]
        assert chain.index("setpts=") < chain.index("fps=59.97")

    def test_audio_is_time_stretched_to_match_a_speed_change(self, fake_run,
                                                             ffmpeg_stderr, tmp_path):
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        graph = flat(export_video._filter_graph_cmd(
            tmp_path / "src.mov", [Piece(0.0, 5.0, speed=4.0)]))
        assert "atempo=" in graph


# ── _concat_demuxer_cmd ───────────────────────────────────────────────────────

class TestConcatDemuxerCmd:
    def test_each_piece_becomes_an_inpoint_outpoint_pair(self, tmp_path):
        import export_video

        src = tmp_path / "src.mov"
        src.touch()
        export_video._concat_demuxer_cmd(src, [Piece(1.0, 2.0), Piece(5.0, 9.0)],
                                         tmp_path)

        listing = (tmp_path / "list.txt").read_text()
        assert listing.startswith("ffconcat version 1.0")
        assert listing.count("inpoint") == 2
        assert "inpoint 1.000000" in listing and "outpoint 2.000000" in listing
        assert "inpoint 5.000000" in listing and "outpoint 9.000000" in listing

    def test_only_the_first_video_and_audio_streams_are_mapped(self, tmp_path):
        """iPhone .MOV carries `apac` spatial audio and mebx data tracks this
        ffmpeg build cannot decode; auto-mapping fails the encode outright."""
        import export_video

        src = tmp_path / "src.mov"
        src.touch()
        cmd = export_video._concat_demuxer_cmd(src, [Piece(0.0, 5.0)], tmp_path)

        maps = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "-map"]
        assert maps == ["0:v:0", "0:a:0?"]

    def test_the_demuxer_is_told_not_to_reject_absolute_paths(self, tmp_path):
        import export_video

        src = tmp_path / "src.mov"
        src.touch()
        cmd = flat(export_video._concat_demuxer_cmd(src, [Piece(0.0, 5.0)], tmp_path))
        assert "-f concat" in cmd and "-safe 0" in cmd


# ── render_plan strategy selection ────────────────────────────────────────────

@pytest.fixture
def rendering(fake_run, ffmpeg_stderr, tmp_path, monkeypatch):
    """Set render_plan up to run end-to-end without ffmpeg or the real exports dir."""
    import export_video

    exports = tmp_path / "exports"
    monkeypatch.setattr(export_video, "EXPORTS_DIR", exports)

    src = tmp_path / "source.mov"
    src.write_bytes(b"not really a video")

    fake_run.install()
    # Harmless for every test that never passes progress_cb (they never touch
    # Popen at all); required for TestProgressReporting, which reuses this
    # same fixture rather than duplicating its EXPORTS_DIR/source/side_effect
    # setup.
    fake_run.install_popen()
    fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])

    def create_output(call):
        # render_plan and _strip_display_matrix both refuse to continue unless
        # their output file exists and is non-empty.
        target = str(call.argv[-1])
        if target.endswith(".mp4"):
            path = __import__("pathlib").Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"rendered")

    fake_run.side_effect = create_output

    class Harness:
        module = export_video
        source = src
        exports_dir = exports
        run = fake_run

        def cmds(self):
            """Only the RENDER invocations.

            The filtered path calls `has_audio_stream` first, which is itself an
            `ffmpeg -i` probe — counting it as the render made several assertions
            here pass vacuously. Renders are the ones that write a file, i.e.
            carry `-y`; probes never do.
            """
            return [[str(a) for a in c.argv] for c in fake_run.ffmpeg_calls
                    if "-y" in [str(a) for a in c.argv]]

    return Harness()


class TestRenderPlanStrategy:
    def test_an_all_plain_plan_uses_the_concat_demuxer(self, rendering):
        """The drop-only path, verified byte-identical to the pre-registry renderer."""
        rendering.module.render_plan(
            rendering.source, [Piece(0.0, 5.0), Piece(10.0, 20.0)], out_name="out.mp4"
        )
        first = flat(rendering.cmds()[0])
        assert "-f concat" in first
        assert "-filter_complex" not in first

    def test_one_transforming_piece_switches_the_whole_render_to_filter_complex(
        self, rendering
    ):
        """The concat demuxer cannot vary playback rate per entry, so a single
        speed piece moves every piece onto the filter path."""
        rendering.module.render_plan(
            rendering.source,
            [Piece(0.0, 5.0), Piece(10.0, 20.0, speed=2.0)],
            out_name="out.mp4",
        )
        first = flat(rendering.cmds()[0])
        assert "-filter_complex" in first
        assert "-f concat" not in first

    def test_the_filtered_path_runs_a_second_derotation_pass(self, rendering):
        """Without it the already-upright pixels get rotated again and the clip
        lands sideways in Photos."""
        rendering.module.render_plan(
            rendering.source, [Piece(0.0, 5.0, speed=2.0)], out_name="out.mp4"
        )
        cmds = rendering.cmds()
        assert len(cmds) == 2, "expected an encode pass and a stream-copy pass"
        last = flat(cmds[-1])
        assert "-display_rotation 0" in last
        assert "-c copy" in last

    def test_the_plain_path_runs_exactly_one_ffmpeg_pass(self, rendering):
        rendering.module.render_plan(
            rendering.source, [Piece(0.0, 5.0)], out_name="out.mp4"
        )
        assert len(rendering.cmds()) == 1

    def test_rotation_is_never_set_explicitly_on_the_encode(self, rendering):
        """`-metadata:s:v:0 rotate=` is a no-op in ffmpeg 7, and would
        double-rotate already-upright footage if it ever started working."""
        rendering.module.render_plan(
            rendering.source, [Piece(0.0, 5.0, speed=2.0)], out_name="out.mp4"
        )
        assert "rotate=" not in flat(rendering.cmds()[0])

    def test_metadata_is_stamped_on_the_encode_for_the_plain_path(self, rendering):
        rendering.module.render_plan(
            rendering.source, [Piece(0.0, 5.0)], out_name="out.mp4",
            metadata={"date": "2026-02-11T21:31:00-0500"},
        )
        assert "creation_time=2026-02-11T21:31:00-0500" in flat(rendering.cmds()[0])

    def test_metadata_is_deferred_to_the_remux_on_the_filtered_path(self, rendering):
        """A `-c copy` remux drops creation_time, so stamping it on the encode
        would throw it away."""
        rendering.module.render_plan(
            rendering.source, [Piece(0.0, 5.0, speed=2.0)], out_name="out.mp4",
            metadata={"date": "2026-02-11T21:31:00-0500"},
        )
        cmds = rendering.cmds()
        assert "creation_time" not in flat(cmds[0])
        assert "creation_time=2026-02-11T21:31:00-0500" in flat(cmds[-1])


class TestRenderPlanRefusals:
    def test_a_missing_source_is_reported_before_anything_is_spawned(self, rendering):
        with pytest.raises(FileNotFoundError):
            rendering.module.render_plan("/nope/missing.mov", [Piece(0.0, 5.0)])
        assert rendering.run.ffmpeg_calls == []

    def test_an_empty_plan_refuses_rather_than_exporting_nothing(self, rendering):
        """Exporting a zero-length clip into Photos would be worse than an error."""
        with pytest.raises(ValueError, match="no keep segments"):
            rendering.module.render_plan(rendering.source, [])

    def test_a_plan_of_only_slivers_is_treated_as_empty(self, rendering):
        with pytest.raises(ValueError, match="no keep segments"):
            rendering.module.render_plan(rendering.source, [Piece(5.0, 5.0005)])

    def test_a_failed_encode_raises_instead_of_returning_a_bad_path(self, rendering):
        rendering.run.side_effect = None       # nothing creates the output file
        rendering.run.set_response(stderr="Invalid data found", returncode=1)
        with pytest.raises(RuntimeError, match="ffmpeg render failed"):
            rendering.module.render_plan(rendering.source, [Piece(0.0, 5.0)])


# ── progress reporting (opt-in via progress_cb) ───────────────────────────────

class TestProgressReporting:
    """`progress_cb` is strictly opt-in: passing None must reproduce today's
    exact argv (this is the byte-for-byte pin the export-background-job plan
    calls for), and passing a callback must add exactly the three `-progress`
    tokens and nothing else. The render itself goes through `subprocess.Popen`
    only when a callback is given — `fake_run.install_popen()` on the shared
    `rendering` fixture covers that without duplicating its setup.
    """

    def test_the_default_argv_carries_no_progress_flags(self, rendering):
        rendering.module.render_plan(rendering.source, [Piece(0.0, 5.0)], out_name="out.mp4")
        cmd = flat(rendering.cmds()[0])
        assert "-progress" not in cmd
        assert "-nostats" not in cmd

    def test_a_progress_callback_adds_exactly_three_tokens_and_nothing_else_changes(
        self, rendering
    ):
        """The load-bearing pin: same out_name, so the only argv difference
        between these two calls may be the three progress tokens spliced in
        right before the final (output path) token.

        Each call gets its OWN `tempfile.mkdtemp()` (rendering's -f concat
        `list.txt` lives there), so the `-i <tmp>/list.txt` token legitimately
        differs run to run — that randomness is normalized away before the
        comparison, it is not part of what this test is pinning.
        """
        def normalize(argv):
            return [re.sub(r"vx_render_[^/\\]+", "vx_render_X", str(a)) for a in argv]

        rendering.module.render_plan(rendering.source, [Piece(0.0, 5.0)], out_name="out.mp4")
        plain_argv = normalize(rendering.cmds()[0])

        rendering.module.render_plan(
            rendering.source, [Piece(0.0, 5.0)], out_name="out.mp4",
            progress_cb=lambda f: None,
        )
        with_cb_argv = normalize(rendering.cmds()[-1])

        assert with_cb_argv == plain_argv[:-1] + ["-progress", "pipe:1", "-nostats", plain_argv[-1]]

    def test_the_derotate_remux_pass_never_carries_progress_flags(self, rendering):
        """The filtered path's second, stream-copy pass is a sub-second remux
        with no sub-progress of its own — it gets no flags, callback or not."""
        rendering.module.render_plan(
            rendering.source, [Piece(0.0, 5.0, speed=2.0)], out_name="out.mp4",
            progress_cb=lambda f: None,
        )
        cmds = rendering.cmds()
        assert len(cmds) == 2, "expected an encode pass and a stream-copy pass"
        remux = flat(cmds[-1])
        assert "-progress" not in remux and "-nostats" not in remux
        assert "-display_rotation 0" in remux and "-c copy" in remux

    def test_progress_lines_produce_monotonic_fractions_between_0_and_1(self, rendering):
        rendering.run.popen_stdout = [
            "out_time_us=0\n",
            "out_time_us=2500000\n",
            "out_time_us=5000000\n",
            "out_time_us=10000000\n",
            "progress=end\n",
        ]
        seen = []
        rendering.module.render_plan(
            rendering.source, [Piece(0.0, 10.0)], out_name="out.mp4",
            progress_cb=seen.append,
        )
        assert seen, "no progress at all was reported"
        assert seen == sorted(seen), "fractions must never go backwards"
        assert all(0.0 <= f <= 1.0 for f in seen)
        assert seen[-1] == pytest.approx(1.0)

    def test_unparseable_progress_lines_are_skipped_without_erroring(self, rendering):
        rendering.run.popen_stdout = [
            "frame=1\n", "fps=30\n", "bitrate=N/A\n", "not a progress line at all\n",
        ]
        seen = []
        out = rendering.module.render_plan(
            rendering.source, [Piece(0.0, 5.0)], out_name="out.mp4",
            progress_cb=seen.append,
        )
        assert out.exists(), "unparseable progress lines must not fail the render"
        assert seen == []

    def test_a_speed_region_never_reports_over_100_percent(self, rendering):
        """A 2x piece over 10 SOURCE seconds produces 5 OUTPUT seconds, and
        ffmpeg's own out_time_us tracks OUTPUT position — so the denominator
        must be the output duration, not the source span, or this would
        (harmlessly, but confusingly) report 200%."""
        rendering.run.popen_stdout = ["out_time_us=5000000\n"]  # all 5 output seconds
        seen = []
        rendering.module.render_plan(
            rendering.source, [Piece(0.0, 10.0, speed=2.0)], out_name="out.mp4",
            progress_cb=seen.append,
        )
        assert seen
        assert max(seen) <= 1.0
        assert seen[-1] == pytest.approx(1.0)
