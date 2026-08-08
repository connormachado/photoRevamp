"""
Command and argument injection
==============================
Two external programs get user-influenced data: the bundled ffmpeg, and
`osascript`. Neither is invoked through a shell anywhere in this codebase — every
call site is list-argv — so classic shell metacharacter injection is not the
risk. The two that ARE real:

1. **AppleScript string breakout.** `osascript -e '<script>'` takes a *program*
   as its argument, and values are interpolated into that program's source with
   f-strings. A `"` in the value closes the string literal, and everything after
   it is executed as AppleScript. The uuid comes from a filename stem
   (`utils.extract_metadata`) and the path from disk, so neither is a validated
   token.

2. **Argument injection.** A path beginning with `-` is read as a flag by any CLI
   it is passed to, no shell required.

The current defence for (1) is a two-character denylist (`"` and `\\`). That is
narrower than proper quoting, so these tests pin the property that matters — no
attacker-controlled AppleScript executes — rather than the mechanism, which
leaves room to replace the denylist with argv passing later.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

# A payload that, unescaped, closes the string literal and runs a second command.
BREAKOUT = 'ABC"\ntell application "Finder" to delete every item of home\n--'


def scripts_from(fake_run):
    return fake_run.scripts()


# ── no shell, anywhere ────────────────────────────────────────────────────────

class TestNoShellInvolved:
    """`shell=True` would turn every one of these into a shell-injection sink."""

    def test_reveal_uses_argv_and_no_shell(self, fake_run):
        import cleanup

        fake_run.install()
        cleanup.reveal_in_photos("ABC-123")

        assert fake_run.calls, "nothing was invoked"
        for call in fake_run.calls:
            assert call.is_argv_list, f"command passed as a string: {call.argv!r}"
            assert not call.uses_shell

    def test_the_probe_uses_argv_and_no_shell(self, fake_run, ffmpeg_stderr):
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        video_motion.probe("/lib/clip.mov")

        for call in fake_run.calls:
            assert call.is_argv_list
            assert not call.uses_shell

    def test_the_renderer_uses_argv_and_no_shell(self, fake_run, ffmpeg_stderr,
                                                 tmp_path, monkeypatch):
        import export_video
        from edit_boundaries import Piece

        monkeypatch.setattr(export_video, "EXPORTS_DIR", tmp_path / "exports")
        src = tmp_path / "src.mov"
        src.write_bytes(b"video")

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        fake_run.side_effect = lambda call: (
            tmp_path.joinpath("exports").mkdir(exist_ok=True)
            or __import__("pathlib").Path(str(call.argv[-1])).write_bytes(b"out")
            if str(call.argv[-1]).endswith(".mp4") else None
        )

        export_video.render_plan(src, [Piece(0.0, 5.0)], out_name="out.mp4")

        for call in fake_run.calls:
            assert call.is_argv_list
            assert not call.uses_shell

    def test_the_codebase_never_passes_shell_true(self):
        """A grep-level guard: one `shell=True` anywhere reopens the whole class."""
        import pathlib

        backend = pathlib.Path(__file__).resolve().parent.parent / "backend"
        offenders = [
            p.name for p in backend.glob("*.py")
            if "shell=True" in p.read_text() or "os.system(" in p.read_text()
        ]
        assert offenders == []


# ── AppleScript string breakout ───────────────────────────────────────────────

def quote_counts(fake_run, build) -> tuple[list[int], list[int]]:
    """Quote counts of the scripts produced by a benign value vs a hostile one.

    This is the shape every breakout test here takes, and it needs stating
    plainly: the payload's TEXT does end up in the generated script, and that is
    fine. `"` and `\\` are stripped, so what lands inside the string literal is
    inert prose — asserting `"Finder" not in script` would be testing the wrong
    thing and would fail on safe code.

    What actually decides whether a value escapes is whether it can introduce a
    `"`, because AppleScript literals have no escape syntax and a quote is the
    only way out. So: build the same script twice and compare quote counts. Equal
    counts mean the value contributed none of its own — regardless of HOW the
    code achieves that, which leaves room to swap the current denylist for proper
    argv passing without rewriting these tests.
    """
    fake_run.calls.clear()
    build("BENIGN-UUID-0001")
    benign = [s.count('"') for s in fake_run.scripts()]

    fake_run.calls.clear()
    build(BREAKOUT)
    hostile = [s.count('"') for s in fake_run.scripts()]

    return benign, hostile


class TestAppleScriptBreakout:
    def test_a_hostile_uuid_cannot_add_a_quote_to_the_reveal_script(self, fake_run):
        import cleanup

        fake_run.install()
        benign, hostile = quote_counts(fake_run, cleanup.reveal_in_photos)

        assert benign and benign == hostile, (
            "the payload introduced quotes, so it can close the string literal"
        )

    def test_a_hostile_uuid_cannot_add_a_quote_to_the_size_lookup(self, fake_run):
        import cleanup

        fake_run.install()
        fake_run.set_response(stdout="0")
        benign, hostile = quote_counts(fake_run, cleanup.photo_size_bytes)

        assert benign and benign == hostile

    def test_the_uuid_lands_inside_exactly_one_pair_of_quotes(self, fake_run):
        """Spelled out for the simplest script: `tell application "Photos" to
        spotlight media item id "<uuid>"` — two literals, four quotes, no more."""
        import cleanup

        fake_run.install()
        cleanup.reveal_in_photos(BREAKOUT)

        spotlight = [s for s in fake_run.scripts() if "spotlight" in s]
        assert spotlight, "the spotlight script was never built"
        for script in spotlight:
            assert script.count('"') == 4

    def test_a_backslash_cannot_neutralise_the_closing_quote(self, fake_run):
        r"""`\"` would let the literal run on past its delimiter."""
        import cleanup

        fake_run.install()
        fake_run.calls.clear()
        cleanup.reveal_in_photos('ABC\\" & (do shell script "id") & "')

        # reveal fires two scripts; only the second interpolates the uuid. (The
        # first is a bare `activate`, deliberately kept byte-identical.)
        spotlight = [s for s in fake_run.scripts() if "spotlight" in s]
        assert spotlight
        for script in spotlight:
            assert script.count('"') == 4
            assert "\\" not in script

    def test_a_hostile_filename_cannot_add_a_quote_to_the_import_script(
        self, fake_run, tmp_path
    ):
        """The path comes off disk, so a crafted filename is the delivery route."""
        import export_video

        fake_run.install()
        fake_run.set_response(stdout="item-1")

        def build(name):
            path = tmp_path / f"{name}.mov"
            path.write_bytes(b"video")
            export_video.import_to_photos(path)

        fake_run.calls.clear()
        build("benign")
        benign = [s.count('"') for s in fake_run.scripts()]

        fake_run.calls.clear()
        hostile_path = tmp_path / 'cl"ip.mov'
        hostile_path.write_bytes(b"video")
        export_video.import_to_photos(hostile_path)
        hostile = [s.count('"') for s in fake_run.scripts()]

        assert benign and benign == hostile

    def test_a_hostile_item_id_cannot_add_a_quote_to_the_date_setter(self, fake_run):
        import export_video

        fake_run.install()
        benign, hostile = quote_counts(
            fake_run, lambda v: export_video._try_set_item_date(v, "2026-02-11T21:31:00"))

        assert benign and benign == hostile

    def test_a_hostile_item_id_cannot_add_a_quote_to_the_location_setter(self, fake_run):
        import export_video

        fake_run.install()
        benign, hostile = quote_counts(
            fake_run,
            lambda v: export_video._try_set_item_location(v, "+43.6552-072.2412/"))

        assert benign and benign == hostile

    def test_gps_coordinates_are_numeric_before_they_reach_the_script(self, fake_run):
        """Lat/lon are parsed to floats, so text cannot ride in on them at all."""
        import export_video

        fake_run.install()
        fake_run.calls.clear()
        export_video._try_set_item_location(
            "item-1", '+43.6552-072.2412/" & (do shell script "id") & "')

        for script in fake_run.scripts():
            assert "do shell script" not in script

    def test_a_newline_payload_fails_the_script_rather_than_executing(self, fake_run):
        """A `\\n` is not stripped, but an embedded newline inside an AppleScript
        literal is a COMPILE error — so osascript exits non-zero and the caller
        reports a failure. Broken, not exploited."""
        import cleanup

        fake_run.install()
        fake_run.set_response(stderr="syntax error: Expected end of line", returncode=1)

        result = cleanup.reveal_in_photos(BREAKOUT)
        assert result["success"] is False
        assert "syntax error" in result["error"]

    def test_a_failed_script_is_reported_not_raised(self, fake_run):
        """A breakout attempt that produces invalid AppleScript must surface as a
        clean error, not a 500."""
        import cleanup

        fake_run.install()
        fake_run.set_response(stderr="syntax error", returncode=1)

        result = cleanup.reveal_in_photos(BREAKOUT)
        assert result["success"] is False
        assert "error" in result


# ── argument injection ────────────────────────────────────────────────────────

class TestArgumentInjection:
    def test_a_leading_dash_video_id_never_becomes_an_ffmpeg_output_name(
        self, tmp_motion_db
    ):
        """`out_name` is joined onto EXPORTS_DIR and handed to ffmpeg as its
        output path."""
        import safe_paths

        with pytest.raises(safe_paths.UnsafePathError):
            safe_paths.safe_id_component("-y")

    def test_the_probe_passes_the_path_as_the_value_of_minus_i(
        self, fake_run, ffmpeg_stderr
    ):
        """Positionally guarded: a path starting with `-` still lands in the slot
        after `-i`, where ffmpeg reads it as a filename rather than a flag."""
        import video_motion

        fake_run.install()
        fake_run.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        video_motion.probe("-i /etc/passwd")

        assert fake_run.last.flag_value("-i") == "-i /etc/passwd"

    def test_uploaded_filenames_are_stripped_of_path_syntax(self):
        """The one input that becomes a real filename on disk."""
        import video_upload

        for hostile in ["../../etc/passwd", "/abs/path.mov", "..\\..\\win.mov"]:
            safe = video_upload._safe_name(hostile)
            assert "/" not in safe
            assert "\\" not in safe
            assert ".." not in safe


# ── ffconcat quoting ──────────────────────────────────────────────────────────

class TestFfconcatQuoting:
    """`_concat_demuxer_cmd` writes `file '<path>'` lines into a list.txt that
    ffmpeg's concat demuxer parses as a mini script."""

    def test_a_normal_path_is_quoted(self, tmp_path):
        import export_video
        from edit_boundaries import Piece

        src = tmp_path / "clip.mov"
        src.touch()
        export_video._concat_demuxer_cmd(src, [Piece(0.0, 1.0)], tmp_path)

        listing = (tmp_path / "list.txt").read_text()
        assert f"file '{src.resolve()}'" in listing

    def test_a_quote_in_the_source_path_cannot_inject_a_directive(self, tmp_path):
        import export_video
        from edit_boundaries import Piece

        hostile = tmp_path / "cl'ip.mov"
        hostile.touch()
        export_video._concat_demuxer_cmd(hostile, [Piece(0.0, 1.0)], tmp_path)

        listing = (tmp_path / "list.txt").read_text()
        for line in listing.splitlines():
            if line.startswith("file "):
                quoted = line[len("file "):]
                assert quoted.startswith("'") and quoted.endswith("'")
                assert "'" not in quoted[1:-1], (
                    "an unescaped quote ends the filename early, so the rest of "
                    "the path is parsed as ffconcat directives"
                )

    def test_the_cli_reachable_writer_is_also_safe(self, tmp_path, fake_run):
        """`video_motion.make_trimmed_clip` is the CLI ingest path's writer —
        the one an arbitrary filesystem path (not an upload) actually reaches."""
        import video_motion

        fake = fake_run.install()
        captured = {}

        def capture(call):
            i = call.flag_value("-i")
            if i and "list.txt" in i:
                captured["listing"] = Path(i).read_text()

        fake.side_effect = capture

        hostile = tmp_path / "cl'ip.mov"
        hostile.touch()
        out_path = tmp_path / "out.mkv"

        video_motion.make_trimmed_clip(hostile, [(0.0, 1.0)], out_path)

        assert "listing" in captured, "expected a concat-demuxer ffmpeg call"
        for line in captured["listing"].splitlines():
            if line.startswith("file "):
                assert "'" not in line[len("file "):][1:-1]

    def test_a_newline_in_the_source_path_is_refused_not_written(self, tmp_path):
        import ffconcat

        # A newline is a valid filename byte on POSIX filesystems (only NUL and
        # '/' are forbidden), so this is a real reachable path, not a fake one.
        hostile = tmp_path / "cl\ninject 'evil'.mov"
        hostile.touch()

        with pytest.raises(ffconcat.UnsafeConcatPathError):
            ffconcat.concat_path(hostile, tmp_path)

    def test_two_pieces_of_one_hostile_source_share_one_staged_alias(self, tmp_path):
        import export_video
        from edit_boundaries import Piece

        hostile = tmp_path / "cl'ip.mov"
        hostile.touch()
        export_video._concat_demuxer_cmd(hostile, [Piece(0.0, 1.0), Piece(2.0, 3.0)], tmp_path)

        listing = (tmp_path / "list.txt").read_text()
        file_lines = [ln for ln in listing.splitlines() if ln.startswith("file ")]
        assert len(file_lines) == 2
        assert len(set(file_lines)) == 1, "both pieces should reference the same staged alias"

        aliased_path = Path(file_lines[0][len("file '"):-1])
        assert aliased_path.is_symlink()
        assert aliased_path.resolve() == hostile.resolve()
