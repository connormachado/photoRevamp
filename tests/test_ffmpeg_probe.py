"""
ffmpeg stderr metadata parsers
==============================
There is no ffprobe in this project, so `video_motion.probe` and
`export_video.read_source_metadata` both scrape `ffmpeg -i` **stderr** with
regexes. That makes them the most fragile parsing in the codebase: their input is
a human-readable diagnostic stream that ffmpeg has no contract to keep stable,
and a caller that gets `None` back silently produces a wrong export.

The blobs come from tests/ffmpeg_samples.py. `iphone_mov` is real captured
output — see that module for why that matters.

Both parsers deliberately ignore ffmpeg's exit code (it always fails when given
no output file), so the malformed cases below are not hypothetical: a killed or
confused ffmpeg really does return partial text with a non-zero status.
"""

import pytest

pytestmark = pytest.mark.slow   # these modules import utils -> torch (~2s)


# ── video_motion.probe ────────────────────────────────────────────────────────

class TestProbeHappyPath:
    def test_reads_duration_codec_dimensions_and_fps_from_a_real_iphone_clip(
        self, fake_run, ffmpeg_stderr
    ):
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"], returncode=1)

        info = video_motion.probe("/lib/clip.MOV")
        assert info["duration"] == pytest.approx(59.18)
        assert info["codec"] == "h264"
        assert info["width"] == 1920
        assert info["height"] == 1080
        assert info["fps"] == pytest.approx(59.97)

    def test_duration_is_converted_from_hours_minutes_seconds(self, fake_run, ffmpeg_stderr):
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["no_fps_only_tbr"])
        # Duration: 00:12:00.00
        assert video_motion.probe("/lib/old.mov")["duration"] == pytest.approx(720.0)

    def test_falls_back_to_tbr_when_no_fps_is_reported(self, fake_run, ffmpeg_stderr):
        """Variable-frame-rate sources report only `tbr`; without the fallback the
        motion sampler would divide by None."""
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["no_fps_only_tbr"])
        assert video_motion.probe("/lib/old.mov")["fps"] == pytest.approx(25.0)

    def test_a_nonzero_exit_code_is_not_treated_as_failure(self, fake_run, ffmpeg_stderr):
        """`ffmpeg -i` with no output file ALWAYS exits non-zero. That is the
        normal case here, not an error."""
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["silent_mp4"], returncode=1)
        assert video_motion.probe("/lib/clip.mp4")["duration"] == pytest.approx(64.5)

    def test_the_probe_asks_ffmpeg_for_the_right_file(self, fake_run, ffmpeg_stderr):
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["silent_mp4"])
        video_motion.probe("/lib/my clip.mp4")

        call = fake_run.last
        assert call.is_argv_list
        assert "ffmpeg" in call.program
        assert call.flag_value("-i") == "/lib/my clip.mp4"


class TestProbeMalformedInput:
    """Every one of these must degrade to None, never raise."""

    @pytest.mark.parametrize("case", [
        "malformed_empty", "malformed_truncated", "malformed_no_duration",
        "malformed_duration_na", "malformed_garbage", "malformed_not_found",
    ])
    def test_never_raises_and_always_returns_the_full_shape(
        self, fake_run, ffmpeg_stderr, case
    ):
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr[case], returncode=1)

        info = video_motion.probe("/lib/broken.mov")
        assert set(info) == {"duration", "codec", "width", "height", "fps"}

    def test_a_missing_duration_reads_as_none_not_zero(self, fake_run, ffmpeg_stderr):
        """None and 0.0 mean different things downstream — 0.0 would look like a
        real (empty) video and produce a divide-by-zero in the savings math."""
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["malformed_no_duration"])
        assert video_motion.probe("/lib/x.mov")["duration"] is None

    def test_duration_na_is_not_mistaken_for_a_number(self, fake_run, ffmpeg_stderr):
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["malformed_duration_na"])
        assert video_motion.probe("/lib/stream.ts")["duration"] is None

    def test_a_stream_line_still_parses_when_the_duration_is_missing(
        self, fake_run, ffmpeg_stderr
    ):
        """Partial data is better than none — the fields are independent."""
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["malformed_no_duration"])
        info = video_motion.probe("/lib/x.mov")
        assert info["width"] == 1920
        assert info["height"] == 1080

    def test_empty_stderr_yields_all_none(self, fake_run, ffmpeg_stderr):
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["malformed_empty"])
        assert all(v is None for v in video_motion.probe("/lib/x.mov").values())


# ── export_video.read_source_metadata ─────────────────────────────────────────

class TestSourceMetadataHappyPath:
    def test_prefers_the_apple_local_wallclock_date_over_utc(self, fake_run, ffmpeg_stderr):
        """Photos files a clip by its LOCAL capture time. Using the UTC
        `creation_time` instead shifts the clip's date by the timezone offset —
        and across midnight, into the wrong day."""
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"], returncode=1)

        meta = export_video.read_source_metadata("/lib/clip.MOV")
        assert meta["date"] == "2026-02-11T21:31:00-0500"
        assert meta["date_utc"] == "2026-02-12T02:31:00.000000Z"

    def test_falls_back_to_utc_when_there_is_no_apple_tag(self, fake_run, ffmpeg_stderr):
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["silent_mp4"])

        meta = export_video.read_source_metadata("/lib/clip.mp4")
        assert meta["date"] == meta["date_utc"] == "2024-07-04T18:09:33.000000Z"

    def test_reads_the_iso6709_location_verbatim(self, fake_run, ffmpeg_stderr):
        """The string is written straight back out on export, so it must not be
        reformatted on the way through."""
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        meta = export_video.read_source_metadata("/lib/clip.MOV")
        assert meta["gps"] == "+43.6552-072.2412+180.799/"

    def test_the_horizontal_accuracy_tag_is_not_mistaken_for_the_location(
        self, fake_run, ffmpeg_stderr
    ):
        """`com.apple.quicktime.location.accuracy.horizontal` sits ABOVE the real
        location tag in Apple's metadata block and is a bare float."""
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        assert export_video.read_source_metadata("/x")["gps"] != "16.073758"

    def test_reads_the_plain_location_tag_written_by_ffmpegs_own_muxer(
        self, fake_run, ffmpeg_stderr
    ):
        """Doubles as a verification read on our own exports."""
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["no_fps_only_tbr"])
        assert export_video.read_source_metadata("/lib/old.mov")["gps"] == "+37.7749-122.4194/"

    def test_reads_the_display_matrix_rotation_as_a_signed_float(
        self, fake_run, ffmpeg_stderr
    ):
        """Rotation drives the double-rotation fix in the filter_complex export
        path; the sign matters."""
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        assert export_video.read_source_metadata("/lib/clip.MOV")["rotation"] == -90.0

    def test_no_rotation_side_data_reads_as_none_not_zero(self, fake_run, ffmpeg_stderr):
        """None ('no matrix present') and 0.0 ('matrix says upright') are
        different facts about the file."""
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["silent_mp4"])
        assert export_video.read_source_metadata("/lib/clip.mp4")["rotation"] is None

    def test_a_clip_with_no_location_reports_none(self, fake_run, ffmpeg_stderr):
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["silent_mp4"])
        assert export_video.read_source_metadata("/lib/clip.mp4")["gps"] is None


class TestSourceMetadataMalformedInput:
    @pytest.mark.parametrize("case", [
        "malformed_empty", "malformed_truncated", "malformed_no_duration",
        "malformed_duration_na", "malformed_garbage", "malformed_not_found",
    ])
    def test_never_raises_and_always_returns_the_full_shape(
        self, fake_run, ffmpeg_stderr, case
    ):
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr[case], returncode=1)

        meta = export_video.read_source_metadata("/lib/broken.mov")
        assert set(meta) == {"date", "date_utc", "gps", "rotation"}

    def test_a_truncated_tag_is_not_half_parsed(self, fake_run, ffmpeg_stderr):
        """The blob cuts off mid-way through `creation_ti`."""
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["malformed_truncated"])
        meta = export_video.read_source_metadata("/lib/broken.mov")
        assert meta["date"] is None
        assert meta["date_utc"] is None

    def test_empty_stderr_yields_all_none(self, fake_run, ffmpeg_stderr):
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["malformed_empty"])
        assert all(v is None for v in
                   export_video.read_source_metadata("/lib/x.mov").values())


class TestHasAudioStream:
    def test_detects_the_audio_track_on_a_real_iphone_clip(self, fake_run, ffmpeg_stderr):
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"], returncode=1)
        assert export_video.has_audio_stream("/lib/clip.MOV") is True

    def test_reports_false_for_a_video_only_file(self, fake_run, ffmpeg_stderr):
        """Getting this wrong makes the filter_complex export map a stream that
        does not exist, and the whole render fails."""
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["silent_mp4"])
        assert export_video.has_audio_stream("/lib/clip.mp4") is False

    @pytest.mark.parametrize("case", ["malformed_empty", "malformed_garbage",
                                      "malformed_truncated"])
    def test_malformed_output_is_reported_as_no_audio_rather_than_raising(
        self, fake_run, ffmpeg_stderr, case
    ):
        import export_video

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr[case])
        assert export_video.has_audio_stream("/lib/x.mov") is False
