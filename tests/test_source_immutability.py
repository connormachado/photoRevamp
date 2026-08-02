"""
The original is never touched
=============================
Design decision B — "the export is a NEW asset beside the original; deleting the
original stays a manual user decision" — was true only by construction until this
file existed. Nothing enforced it, and the next features on the list (reject
rework, purge) are exactly the ones that would break it by accident.

These tests are BEHAVIORAL: they call the real entry points against a real source
file on disk and assert on the filesystem afterwards. None of them reads the
implementation back to itself, so a future `os.unlink(source_path)` added
anywhere under the export or decision paths fails them regardless of how it is
spelled.

What "unchanged" means here
---------------------------
`fingerprint()` captures size + content md5 + mtime_ns. mtime is included on
purpose: a rewrite that happens to produce identical bytes, or a bare `touch`,
is still a modification of the user's file and should fail.

The one thing filesystem assertions cannot see
----------------------------------------------
`conftest` cuts every test off from real processes, so ffmpeg never runs. A
Python-level `unlink`/`os.replace`/`open(src, "w")` IS caught by the snapshots
below; an *ffmpeg* invocation that named the source as its output would not be,
because the fake never writes it. `TestFfmpegNeverWritesToTheSource` closes that
half by asserting on the argv every render actually handed to the binary — the
two halves together cover both ways the source could be destroyed.

`fake_ffmpeg` deliberately models ffmpeg as "writes its declared output file and
nothing else", which is what the real binary does. It is the honest stand-in: if
production code ever passed the source AS the output, the argv tests catch it.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest


SOURCE_BYTES = b"CLIMB-SOURCE-ORIGINAL-" + bytes(range(256)) * 64
VIDEO_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"      # md5-shaped, as a real one is


# ── fingerprinting helpers ────────────────────────────────────────────────────

def fingerprint(path: Path) -> tuple:
    """(size, mtime_ns, md5) — everything that must not change about a file."""
    st = path.stat()
    return (st.st_size, st.st_mtime_ns, hashlib.md5(path.read_bytes()).hexdigest())


def snapshot_tree(root: Path) -> dict[str, tuple]:
    """Fingerprint every file under *root*, keyed by path relative to it."""
    return {
        str(p.relative_to(root)): fingerprint(p)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ── the harness ───────────────────────────────────────────────────────────────

@pytest.fixture
def queued_video(tmp_motion_db, fake_run, ffmpeg_stderr, tmp_path, monkeypatch):
    """A video sitting in the review queue, ready to export for real.

    Everything a genuine export needs is present and tmp-backed: a source file
    with real bytes, a proposal on disk pointing at it, a redirected exports dir,
    and a subprocess stand-in that behaves the way ffmpeg/osascript do — ffmpeg
    creates the file it was told to write, osascript hands back a Photos item id.
    """
    import export_video

    exports = tmp_path / "exports"
    monkeypatch.setattr(export_video, "EXPORTS_DIR", exports)

    # The user's own files. `library/` stands in for anything the app merely
    # REFERENCES rather than owns — the decoys are there so a wildcard delete
    # would be caught, not just a delete aimed at the source.
    library = tmp_path / "library"
    library.mkdir()
    source = library / "IMG_CLIMB.mov"
    source.write_bytes(SOURCE_BYTES)
    (library / "IMG_UNRELATED.mov").write_bytes(b"a different clip entirely")
    (library / "notes.txt").write_text("not ours to touch")

    tmp_motion_db.proposal(
        video_id=VIDEO_ID,
        source_path=str(source),
        original_duration=60.0,
        trimmed_duration=40.0,
        cut_segments=[{"start": 40.0, "end": 60.0}],
        probe={"width": 1920, "height": 1080, "fps": 59.97},
    )

    fake_run.install()
    # One default serves both boundaries: ffmpeg is read off stderr, osascript
    # off stdout, and neither looks at the other's channel.
    fake_run.set_response(stdout="PHOTOS-ITEM-0001/L0/001",
                          stderr=ffmpeg_stderr["iphone_mov"])

    def fake_ffmpeg(call):
        """Create the output file a render was told to produce — nothing else."""
        argv = [str(a) for a in (call.argv or [])]
        if not argv or "ffmpeg" not in argv[0] or "-y" not in argv:
            return                      # a probe: reads, writes nothing
        target = Path(argv[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"rendered output bytes")

    fake_run.side_effect = fake_ffmpeg

    class Harness:
        module = tmp_motion_db.module
        video_id = VIDEO_ID
        src = source
        library_dir = library
        exports_dir = exports
        run = fake_run
        db = tmp_motion_db

    return Harness()


SPEED_REGIONS = [
    {"type": "speed", "start": 10.0, "end": 20.0,
     "params": {"direction": "up", "magnitude": 2.0}},
]
CUT_REGIONS = [{"type": "cut", "start": 45.0, "end": 60.0}]


# ── 1. export / render ────────────────────────────────────────────────────────

class TestExportLeavesTheSourceAlone:
    def test_a_drop_only_export_does_not_touch_the_source(self, queued_video):
        before = fingerprint(queued_video.src)

        result = queued_video.module.export_to_photos(
            queued_video.video_id, regions=CUT_REGIONS)

        assert queued_video.src.exists(), "the export deleted the original"
        assert fingerprint(queued_video.src) == before, "the export modified the original"
        # Non-vacuous: prove an export really happened rather than bailing early.
        assert Path(result["rendered_path"]).exists()
        assert Path(result["rendered_path"]) != queued_video.src

    def test_a_speed_export_does_not_touch_the_source(self, queued_video):
        """The filter_complex path plus its second stream-copy remux — a
        different render strategy with an extra output file, so it gets its own
        assertion rather than riding on the drop-only case."""
        before = fingerprint(queued_video.src)

        result = queued_video.module.export_to_photos(
            queued_video.video_id, regions=SPEED_REGIONS)

        assert queued_video.src.exists()
        assert fingerprint(queued_video.src) == before
        assert Path(result["rendered_path"]).exists()

    def test_the_export_lands_beside_the_original_not_over_it(self, queued_video):
        result = queued_video.module.export_to_photos(
            queued_video.video_id, regions=CUT_REGIONS)

        rendered = Path(result["rendered_path"]).resolve()
        assert rendered != queued_video.src.resolve()
        assert not rendered.is_relative_to(queued_video.library_dir.resolve()), \
            "the export was written into the user's library, not the app's exports dir"

    def test_nothing_in_the_user_library_changes_during_an_export(self, queued_video):
        """The decoys matter: this catches a delete aimed at the folder rather
        than at the source file."""
        before = snapshot_tree(queued_video.library_dir)

        queued_video.module.export_to_photos(queued_video.video_id, regions=CUT_REGIONS)

        assert snapshot_tree(queued_video.library_dir) == before

    def test_a_failed_render_still_leaves_the_source_intact(self, queued_video):
        """The unhappy path is where a cleanup routine would plausibly be added."""
        before = fingerprint(queued_video.src)
        queued_video.run.side_effect = None        # ffmpeg writes no output file

        with pytest.raises(RuntimeError):
            queued_video.module.export_to_photos(queued_video.video_id,
                                                 regions=CUT_REGIONS)

        assert queued_video.src.exists()
        assert fingerprint(queued_video.src) == before


# ── 2. reject / decision ──────────────────────────────────────────────────────

class TestDecisionsLeaveTheSourceAlone:
    @pytest.mark.parametrize("verdict", ["reject", "approve"])
    def test_recording_a_verdict_does_not_touch_the_source(self, queued_video, verdict):
        before = fingerprint(queued_video.src)

        queued_video.module.record_decision(queued_video.video_id, verdict)

        assert queued_video.src.exists(), f"{verdict} deleted the original"
        assert fingerprint(queued_video.src) == before, f"{verdict} modified the original"

    @pytest.mark.parametrize("verdict", ["reject", "approve"])
    def test_recording_a_verdict_changes_nothing_in_the_user_library(
        self, queued_video, verdict
    ):
        before = snapshot_tree(queued_video.library_dir)

        queued_video.module.record_decision(queued_video.video_id, verdict)

        assert snapshot_tree(queued_video.library_dir) == before

    def test_rejecting_an_already_exported_video_still_spares_the_source(
        self, queued_video
    ):
        """A reject AFTER an export is the shape the reject-rework feature will
        take, and the one most likely to grow a "clean it up" delete."""
        queued_video.module.export_to_photos(queued_video.video_id, regions=CUT_REGIONS)
        before = fingerprint(queued_video.src)

        queued_video.module.record_decision(queued_video.video_id, "reject")

        assert queued_video.src.exists()
        assert fingerprint(queued_video.src) == before

    def test_a_draft_save_does_not_touch_the_source(self, queued_video):
        before = fingerprint(queued_video.src)

        queued_video.module.save_draft(queued_video.video_id, CUT_REGIONS)

        assert fingerprint(queued_video.src) == before

    def test_removing_the_queue_entry_spares_a_referenced_source(self, queued_video):
        """Removal is the feature this file was written in anticipation of. The
        fixture's source lives in library/, i.e. the app only REFERENCES it, so
        dropping the row must free nothing outside the app's own tree."""
        import queue_removal

        before = snapshot_tree(queued_video.library_dir)

        result = queue_removal.remove_from_queue(queued_video.video_id)

        assert snapshot_tree(queued_video.library_dir) == before
        assert result["deleted_source"] is False

    def test_removing_an_exported_video_still_spares_the_source(self, queued_video):
        import queue_removal

        queued_video.module.export_to_photos(queued_video.video_id, regions=CUT_REGIONS)
        before = fingerprint(queued_video.src)

        queue_removal.remove_from_queue(queued_video.video_id)

        assert queued_video.src.exists()
        assert fingerprint(queued_video.src) == before


# ── 3. preview proxy ──────────────────────────────────────────────────────────

class TestPreviewTranscodeLeavesTheSourceAlone:
    def test_building_the_preview_proxy_does_not_touch_the_source(self, queued_video):
        before = fingerprint(queued_video.src)

        proxy = queued_video.module.source_h264_path(queued_video.video_id)

        assert proxy.exists() and proxy.resolve() != queued_video.src.resolve()
        assert fingerprint(queued_video.src) == before


# ── 4. only app-created files are ever deleted ────────────────────────────────

class TestOnlyAppCreatedFilesAreDeleted:
    """The general guard, stated without naming the source.

    Everything above proves one specific file survives. This one proves the
    stronger property the app actually promises: across a full lifecycle, the
    only paths that DISAPPEAR are ones the app created inside its own tree. It
    keeps working for files that don't exist yet, which is what makes it a real
    guard against the purge feature rather than a snapshot of today's behaviour.
    """

    def _app_owned(self, queued_video) -> list[Path]:
        return [
            queued_video.db.root.resolve(),          # photo_db/motion_review/*
            queued_video.exports_dir.resolve(),      # rendered exports
            Path(tempfile.gettempdir()).resolve(),   # mkdtemp scratch
        ]

    def test_the_only_vanished_files_are_the_apps_own(self, queued_video, tmp_path):
        # A draft is seeded so at least one real deletion happens — otherwise a
        # broken detector would pass this test by finding nothing at all.
        queued_video.module.save_draft(queued_video.video_id, CUT_REGIONS)
        draft = queued_video.db.drafts / f"{queued_video.video_id}.json"
        assert draft.exists()

        before = snapshot_tree(tmp_path)

        queued_video.module.export_to_photos(queued_video.video_id, regions=CUT_REGIONS)
        queued_video.module.record_decision(queued_video.video_id, "reject")
        queued_video.module.source_h264_path(queued_video.video_id)
        # Removal deletes files by design, which is exactly why it belongs in
        # the lifecycle this test sweeps rather than in a carve-out beside it.
        import queue_removal
        queue_removal.remove_from_queue(queued_video.video_id)

        after = snapshot_tree(tmp_path)
        vanished = set(before) - set(after)

        assert vanished, "no file was deleted at all — this test proved nothing"
        assert not draft.exists(), "the export should have cleared the draft"

        app_owned = self._app_owned(queued_video)
        for rel in vanished:
            path = (tmp_path / rel).resolve()
            assert any(path.is_relative_to(root) for root in app_owned), (
                f"the app deleted {path}, which it did not create"
            )

    def test_deleting_a_working_copy_stays_inside_the_apps_tree(
        self, queued_video, tmp_path
    ):
        """The one case where a *source* is deleted on purpose. It has to land
        inside the same boundary as every other deletion, or the promise is that
        the app deletes originals it happens to like the look of."""
        import queue_removal

        uploads = queue_removal._uploads_dir() / "contenthash"
        uploads.mkdir(parents=True)
        working_copy = uploads / "IMG_UPLOADED.mov"
        working_copy.write_bytes(b"a copy the app made for itself")
        queued_video.db.proposal(
            video_id="ownedvid", source_path=str(working_copy), owned=True
        )

        before = snapshot_tree(tmp_path)
        queue_removal.remove_from_queue("ownedvid")
        after = snapshot_tree(tmp_path)

        vanished = set(before) - set(after)
        assert str(working_copy.relative_to(tmp_path)) in vanished, (
            "the working copy was supposed to be freed"
        )
        app_owned = self._app_owned(queued_video)
        for rel in vanished:
            path = (tmp_path / rel).resolve()
            assert any(path.is_relative_to(root) for root in app_owned), (
                f"the app deleted {path}, which it did not create"
            )

    def test_no_file_outside_the_apps_own_tree_is_modified(self, queued_video, tmp_path):
        before = snapshot_tree(tmp_path)

        queued_video.module.export_to_photos(queued_video.video_id, regions=SPEED_REGIONS)
        queued_video.module.record_decision(queued_video.video_id, "approve")

        after = snapshot_tree(tmp_path)
        app_owned = self._app_owned(queued_video)

        for rel, fp in before.items():
            if rel not in after:
                continue
            if fp == after[rel]:
                continue
            path = (tmp_path / rel).resolve()
            assert any(path.is_relative_to(root) for root in app_owned), (
                f"the app modified {path}, which it does not own"
            )


# ── 5. the half a filesystem snapshot cannot see ──────────────────────────────

class TestFfmpegNeverWritesToTheSource:
    """ffmpeg is faked, so an `ffmpeg -y ... <source>` would leave no trace on
    disk for the snapshots above to find. This is the other half of the guard:
    every render's declared output is checked directly.

    In every command this app builds, the output file is the final argv token
    (that is ffmpeg's own syntax, not a convention of ours), and probes carry no
    `-y` because they write nothing.
    """

    def _render_outputs(self, queued_video) -> list[Path]:
        outs = []
        for call in queued_video.run.ffmpeg_calls:
            argv = [str(a) for a in call.argv]
            if "-y" in argv:
                outs.append(Path(argv[-1]).resolve())
        return outs

    @pytest.mark.parametrize("regions", [CUT_REGIONS, SPEED_REGIONS],
                             ids=["drop_only", "speed"])
    def test_no_render_names_the_source_as_its_output(self, queued_video, regions):
        queued_video.module.export_to_photos(queued_video.video_id, regions=regions)

        outputs = self._render_outputs(queued_video)
        assert outputs, "no render was recorded — this test proved nothing"
        assert queued_video.src.resolve() not in outputs

    @pytest.mark.parametrize("regions", [CUT_REGIONS, SPEED_REGIONS],
                             ids=["drop_only", "speed"])
    def test_every_render_writes_inside_a_directory_the_app_owns(
        self, queued_video, regions
    ):
        queued_video.module.export_to_photos(queued_video.video_id, regions=regions)

        owned = [queued_video.exports_dir.resolve(),
                 queued_video.db.root.resolve(),
                 Path(tempfile.gettempdir()).resolve()]
        library = queued_video.library_dir.resolve()

        for out in self._render_outputs(queued_video):
            assert not out.is_relative_to(library), \
                f"a render wrote into the user's library: {out}"
            assert any(out.is_relative_to(root) for root in owned), \
                f"a render wrote outside every app-owned directory: {out}"

    def test_the_preview_transcode_writes_only_into_the_preview_dir(self, queued_video):
        queued_video.module.source_h264_path(queued_video.video_id)

        outputs = self._render_outputs(queued_video)
        assert outputs
        preview = queued_video.db.preview.resolve()
        for out in outputs:
            assert out.is_relative_to(preview), f"the proxy transcode wrote to {out}"

    def test_the_source_is_only_ever_an_ffmpeg_input(self, queued_video):
        """Wherever the source appears in an argv, it is the value of `-i` (or an
        entry in the concat list file), never an output operand.

        A metadata probe ends with the source as its final token *and that is
        correct* — `ffmpeg -hide_banner -i <src>` has no output operand at all,
        which is exactly why it carries no `-y`. So the output check applies to
        writing invocations only; conflating the two would make this assert on
        the wrong thing.
        """
        queued_video.module.export_to_photos(queued_video.video_id, regions=CUT_REGIONS)
        src = str(queued_video.src.resolve())

        seen_as_input = False
        for call in queued_video.run.ffmpeg_calls:
            argv = [str(a) for a in call.argv]
            if call.flag_value("-i") in (src, str(queued_video.src)):
                seen_as_input = True
            if "-y" in argv:
                assert argv[-1] != src, "the source was passed as an ffmpeg output"

        assert seen_as_input, "the source was never read — this test proved nothing"


# ── 6. the ledger is a projection, not a deletion record ──────────────────────

def test_crediting_savings_never_implies_the_original_was_removed(queued_video):
    """savings.json says "if you deleted these you'd reclaim X". Bytes being
    credited must never coincide with the file actually going away."""
    queued_video.module.export_to_photos(queued_video.video_id, regions=CUT_REGIONS)

    savings = json.loads(queued_video.db.savings.read_text())
    assert savings["total_bytes"] > 0, "no savings were credited — nothing was proven"
    assert queued_video.src.exists()
    assert os.path.getsize(queued_video.src) == len(SOURCE_BYTES)
