"""
Export job control
===================
`export_job.py` moves the render/import/reveal pipeline off the Flask request
thread onto a background one. The properties that matter, in order of how bad
it is if they silently regress:

1. The ledger (savings.json / decisions.jsonl) is only ever written once an
   export genuinely finishes — never while it is still in flight.
2. Only one export can ever be running at a time, and a second kickoff while
   one is live is refused rather than launching a second render.
3. A job that never reaches a terminal state on its own (a crash, a killed
   process) is eventually treated as failed rather than wedging the guard
   forever — by three independent routes, each tested in isolation.
4. Kickoff validation (bad id, unknown video) happens BEFORE any thread or
   status file exists, so it stays a synchronous 4xx/404 exactly like the
   export route always gave.

Most tests here stub `motion_review.export_to_photos` (or, one level deeper,
`export_video.export_and_import`) with a function that blocks on a
`threading.Event` until the test releases it — that is what turns "the export
is in flight" from a race into a state the test controls. Every test that
does this releases and waits for the job to reach a terminal state before
returning, via `_wait_until`, so no blocked thread leaks into the next test.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow   # motion_review imports export_video -> torch


# ── small helpers ─────────────────────────────────────────────────────────────

def _wait_until(predicate, timeout=5.0, interval=0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _spinning_thread():
    """A daemon thread that blocks until told to stop — stands in for a "live"
    background job without actually running one, for the staleness tests that
    need `_LIVE.is_alive()` to be true."""
    ev = threading.Event()
    t = threading.Thread(target=ev.wait, daemon=True)
    t.start()
    return t, ev


@pytest.fixture
def export_job_env(tmp_motion_db):
    """Import export_job against the redirected motion_review dirs, and make
    sure NOTHING it did leaks into the next test regardless of pass/fail —
    every test that hangs a thread off `_LIVE` must still release it, but this
    is the backstop that guarantees the module-level handle itself is clean."""
    import export_job

    yield export_job

    with export_job._LOCK:
        export_job._LIVE = None


# ── kickoff validation runs before any thread/file exists ────────────────────

class TestKickoffValidation:
    def test_a_traversing_video_id_raises_before_any_thread_or_file_exists(
        self, export_job_env, tmp_motion_db
    ):
        import safe_paths

        with pytest.raises(safe_paths.UnsafePathError):
            export_job_env.start_export("../../etc/passwd")

        assert export_job_env._LIVE is None
        assert not export_job_env._job_path().exists()

    def test_an_unknown_video_raises_before_any_thread_starts(
        self, export_job_env, tmp_motion_db
    ):
        with pytest.raises(FileNotFoundError):
            export_job_env.start_export("never-seen")

        assert export_job_env._LIVE is None
        assert not export_job_env._job_path().exists()


# ── non-blocking kickoff + status walk ────────────────────────────────────────

class TestNonBlockingKickoff:
    def test_start_export_returns_before_the_export_finishes(
        self, export_job_env, tmp_motion_db, monkeypatch
    ):
        import motion_review

        tmp_motion_db.proposal("vid1")
        started = threading.Event()
        release = threading.Event()

        def fake_export(video_id, regions=None, cut_segments=None, progress_cb=None):
            started.set()
            release.wait(timeout=5)
            return {"ok": True}

        monkeypatch.setattr(motion_review, "export_to_photos", fake_export)

        t0 = time.time()
        result = export_job_env.start_export("vid1")
        elapsed = time.time() - t0

        assert result["started"] is True
        assert elapsed < 1.0, "start_export blocked on the export itself"
        assert started.wait(timeout=2), "the background thread never ran"

        release.set()
        assert _wait_until(lambda: export_job_env.read_status()["state"] == "done")

    def test_read_status_walks_from_queued_to_rendering_to_done_with_the_result_verbatim(
        self, export_job_env, tmp_motion_db, monkeypatch
    ):
        import motion_review

        tmp_motion_db.proposal("vid1")
        rendering_seen = threading.Event()
        release = threading.Event()
        expected_result = {"rendered_path": "/x/out.mp4", "size_bytes": 123}

        def fake_export(video_id, regions=None, cut_segments=None, progress_cb=None):
            progress_cb("rendering", 0.0)
            rendering_seen.set()
            release.wait(timeout=5)
            return expected_result

        monkeypatch.setattr(motion_review, "export_to_photos", fake_export)

        result = export_job_env.start_export("vid1")
        assert result["started"] is True
        assert result["status"]["state"] == "queued"

        assert rendering_seen.wait(timeout=2)
        assert _wait_until(lambda: export_job_env.read_status()["state"] == "rendering")

        release.set()
        assert _wait_until(lambda: export_job_env.read_status()["state"] == "done")

        final = export_job_env.read_status()
        assert final["result"] == expected_result
        assert final["progress"] == 1.0
        assert final["finished_at"]


class TestProgressBands:
    """`_on_progress`'s phase -> percent mapping, exercised directly so it
    doesn't have to race a real thread to observe each intermediate value.
    Uses `_read_raw` rather than `read_status()`, because `read_status()`
    would treat a hand-seeded non-terminal job with no live thread as stale
    and immediately rewrite it — exactly the behaviour tested elsewhere in
    this file, but not what this test is about."""

    def _seed(self, export_job_env):
        export_job_env._write_raw(
            {"job_id": "jid", "video_id": "vid1", "state": "queued",
             "boot_id": export_job_env.BOOT_ID},
            export_job_env._job_path(),
        )

    def test_rendering_is_scaled_into_the_first_90_percent(self, export_job_env, tmp_motion_db):
        self._seed(export_job_env)
        export_job_env._on_progress("jid", "rendering", 0.5)
        status = export_job_env._read_raw(export_job_env._job_path())
        assert status["state"] == "rendering"
        assert status["progress"] == pytest.approx(0.45)

    def test_importing_parks_at_90_percent(self, export_job_env, tmp_motion_db):
        self._seed(export_job_env)
        export_job_env._on_progress("jid", "importing", None)
        status = export_job_env._read_raw(export_job_env._job_path())
        assert status["state"] == "importing"
        assert status["progress"] == pytest.approx(0.90)

    def test_revealing_parks_at_97_percent(self, export_job_env, tmp_motion_db):
        self._seed(export_job_env)
        export_job_env._on_progress("jid", "revealing", None)
        status = export_job_env._read_raw(export_job_env._job_path())
        assert status["state"] == "revealing"
        assert status["progress"] == pytest.approx(0.97)

    def test_an_unrecognised_phase_is_ignored_rather_than_written(
        self, export_job_env, tmp_motion_db
    ):
        self._seed(export_job_env)
        before = export_job_env._read_raw(export_job_env._job_path())
        export_job_env._on_progress("jid", "some-future-phase", 0.5)
        after = export_job_env._read_raw(export_job_env._job_path())
        assert after == before


# ── the ledger only moves on completion ───────────────────────────────────────

class TestSavingsCreditedOnlyOnCompletion:
    def test_nothing_is_written_to_the_ledger_while_the_export_is_still_blocked(
        self, export_job_env, tmp_motion_db, monkeypatch, tmp_path
    ):
        import export_video

        source = tmp_path / "clip.mov"
        source.write_bytes(b"x" * 10_000)
        tmp_motion_db.proposal(
            "vid1", source_path=str(source), original_duration=60.0,
            cut_segments=[{"start": 40.0, "end": 60.0}],
        )

        hit = threading.Event()
        release = threading.Event()

        def fake_export_and_import(source_path, plan, out_name=None, progress_cb=None):
            hit.set()
            release.wait(timeout=5)
            return {
                "rendered_path": str(tmp_path / "out.mp4"),
                "size_bytes": 1000,
                "source_date": None,
                "gps": None,
                "imported": {"success": True, "item_id": "ITEM-1",
                            "date_set": False, "location_set": False},
                "revealed": {"success": True},
            }

        monkeypatch.setattr(export_video, "export_and_import", fake_export_and_import)

        result = export_job_env.start_export("vid1")
        assert result["started"] is True
        assert hit.wait(timeout=2)

        assert not tmp_motion_db.savings.exists()
        assert not tmp_motion_db.decisions.exists()

        release.set()
        assert _wait_until(lambda: export_job_env.read_status()["state"] == "done")

        savings = json.loads(tmp_motion_db.savings.read_text())
        assert savings["total_bytes"] > 0
        assert tmp_motion_db.decisions.exists()
        assert "vid1" in tmp_motion_db.decisions.read_text()


class TestFailureInjection:
    def test_a_raising_export_ends_failed_with_no_ledger_writes(
        self, export_job_env, tmp_motion_db, monkeypatch
    ):
        import motion_review

        tmp_motion_db.proposal("vid1")

        def boom(video_id, regions=None, cut_segments=None, progress_cb=None):
            raise RuntimeError("ffmpeg render failed: boom")

        monkeypatch.setattr(motion_review, "export_to_photos", boom)

        result = export_job_env.start_export("vid1")
        assert result["started"] is True

        assert _wait_until(lambda: export_job_env.read_status()["state"] == "failed")
        status = export_job_env.read_status()
        assert status["error"] and "boom" in status["error"]
        assert not tmp_motion_db.savings.exists()
        assert not tmp_motion_db.decisions.exists()


# ── one export at a time ──────────────────────────────────────────────────────

class TestConcurrencyGuard:
    def test_a_second_kickoff_mid_flight_is_refused_and_only_one_render_happens(
        self, export_job_env, tmp_motion_db, fake_run, ffmpeg_stderr, tmp_path, monkeypatch
    ):
        import export_video

        exports = tmp_path / "exports"
        monkeypatch.setattr(export_video, "EXPORTS_DIR", exports)

        source = tmp_path / "clip.mov"
        source.write_bytes(b"x" * 5000)
        tmp_motion_db.proposal(
            "vid1", source_path=str(source), original_duration=60.0,
            cut_segments=[{"start": 40.0, "end": 60.0}],
        )

        fake_run.install()
        fake_run.install_popen()
        fake_run.set_response(stdout="PHOTOS-ITEM-1", stderr=ffmpeg_stderr["iphone_mov"])

        hit = threading.Event()
        release = threading.Event()

        def block_the_render(call):
            argv = [str(a) for a in (call.argv or [])]
            if argv and "ffmpeg" in argv[0] and "-y" in argv:
                hit.set()
                release.wait(timeout=5)
                target = Path(argv[-1])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"rendered")

        fake_run.side_effect = block_the_render

        def render_call_count():
            return len([c for c in fake_run.ffmpeg_calls
                       if "-y" in [str(a) for a in c.argv]])

        first = export_job_env.start_export("vid1")
        assert first["started"] is True
        assert hit.wait(timeout=2), "the render never started"
        assert render_call_count() == 1

        second = export_job_env.start_export("vid1")
        assert second["started"] is False
        assert second["reason_code"] == "already_running"
        assert render_call_count() == 1, "a second render must not have been spawned"

        release.set()
        assert _wait_until(lambda: export_job_env.read_status()["state"] in ("done", "failed"))
        assert render_call_count() == 1


# ── staleness, three independent ways ─────────────────────────────────────────

class TestStaleness:
    def test_a_foreign_boot_id_is_treated_as_stale_and_a_fresh_export_can_start(
        self, export_job_env, tmp_motion_db
    ):
        """Simulates a status file surviving a server restart: a DIFFERENT
        process's boot id, even with a live (if unrelated) thread handle."""
        tmp_motion_db.proposal("vid1")
        t, ev = _spinning_thread()
        export_job_env._LIVE = t
        try:
            export_job_env._write_raw({
                "job_id": "old-job", "video_id": "vid1", "state": "rendering",
                "progress": 0.2, "started_at": export_job_env._now_iso(),
                "boot_id": "some-other-processs-boot-id",
            }, export_job_env._job_path())

            status = export_job_env.read_status()
            assert status["state"] == "failed"
            assert status["error"]

            result = export_job_env.start_export("vid1")
            assert result["started"] is True
            assert _wait_until(
                lambda: export_job_env.read_status()["state"] in ("done", "failed"))
        finally:
            ev.set()
            t.join(timeout=2)

    def test_a_dead_thread_is_treated_as_stale_and_a_fresh_export_can_start(
        self, export_job_env, tmp_motion_db
    ):
        """No live thread at all — as if the thread died without reaching its
        own except/finally (should be unreachable in practice, but read_status
        must not trust a status file that claims otherwise forever)."""
        tmp_motion_db.proposal("vid1")
        export_job_env._LIVE = None
        export_job_env._write_raw({
            "job_id": "old-job", "video_id": "vid1", "state": "rendering",
            "progress": 0.3, "started_at": export_job_env._now_iso(),
            "boot_id": export_job_env.BOOT_ID,
        }, export_job_env._job_path())

        status = export_job_env.read_status()
        assert status["state"] == "failed"

        result = export_job_env.start_export("vid1")
        assert result["started"] is True
        assert _wait_until(lambda: export_job_env.read_status()["state"] in ("done", "failed"))

    def test_a_job_past_the_wall_clock_ceiling_is_treated_as_stale(
        self, export_job_env, tmp_motion_db
    ):
        """The one case where boot id agrees AND the thread is genuinely
        alive — only the wall-clock ceiling can explain the downgrade here."""
        tmp_motion_db.proposal("vid1")
        t, ev = _spinning_thread()
        export_job_env._LIVE = t
        try:
            ancient = (datetime.now(timezone.utc)
                      - timedelta(seconds=export_job_env.MAX_JOB_SECONDS + 60)).isoformat()
            export_job_env._write_raw({
                "job_id": "old-job", "video_id": "vid1", "state": "rendering",
                "progress": 0.1, "started_at": ancient, "boot_id": export_job_env.BOOT_ID,
            }, export_job_env._job_path())

            status = export_job_env.read_status()
            assert status["state"] == "failed"

            result = export_job_env.start_export("vid1")
            assert result["started"] is True
            assert _wait_until(
                lambda: export_job_env.read_status()["state"] in ("done", "failed"))
        finally:
            ev.set()
            t.join(timeout=2)


# ── is_exporting() ─────────────────────────────────────────────────────────────

class TestIsExporting:
    def test_idle_is_not_exporting(self, export_job_env, tmp_motion_db):
        assert export_job_env.is_exporting() is False
        assert export_job_env.is_exporting("any-video") is False

    def test_a_running_job_reports_exporting_globally_and_for_its_own_video(
        self, export_job_env, tmp_motion_db, monkeypatch
    ):
        import motion_review

        tmp_motion_db.proposal("vid1")
        release = threading.Event()
        started = threading.Event()

        def fake_export(video_id, regions=None, cut_segments=None, progress_cb=None):
            started.set()
            release.wait(timeout=5)
            return {"ok": True}

        monkeypatch.setattr(motion_review, "export_to_photos", fake_export)

        export_job_env.start_export("vid1")
        assert started.wait(timeout=2)

        assert export_job_env.is_exporting() is True
        assert export_job_env.is_exporting("vid1") is True
        assert export_job_env.is_exporting("some-other-video") is False

        release.set()
        assert _wait_until(lambda: export_job_env.read_status()["state"] == "done")
        assert export_job_env.is_exporting() is False
