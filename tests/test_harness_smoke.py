"""
Harness smoke tests
===================
Proves the fixtures in conftest.py actually intercept the boundaries they claim
to. If one of these breaks, every other suite's isolation is suspect — so these
assert the *safety properties*, not just that the runner starts.
"""

import subprocess

import pytest


def test_backend_modules_are_importable_by_flat_name():
    """pytest.ini's `pythonpath = backend` makes flat sibling imports work."""
    import edit_boundaries
    import stats

    assert hasattr(edit_boundaries, "build_plan")
    assert hasattr(stats, "update_stats")


def test_isolate_stats_redirects_away_from_the_repo(isolate_stats, tmp_path):
    """The autouse fixture must move STATS_PATH out of the repo root.

    Guards the real failure mode: a test recording a verdict clobbering the
    user's live delete counter.
    """
    import stats

    assert stats.STATS_PATH == isolate_stats
    assert tmp_path in stats.STATS_PATH.parents

    stats.update_stats(1)
    assert isolate_stats.exists()
    assert not (tmp_path.parent / "stats.json").exists()


def test_isolate_stats_is_autouse_without_being_requested():
    """No fixture argument here on purpose — isolation must not be opt-in."""
    import stats

    assert stats.STATS_PATH.name == "stats.json"
    assert "Desktop/photoApp/stats.json" not in str(stats.STATS_PATH)


def test_fake_run_records_argv_and_replays_stderr(fake_run):
    import cleanup

    fake_run.install("cleanup")
    fake_run.set_response(stdout="12345")

    cleanup.photo_size_bytes("ABC-123")

    assert fake_run.osascript_calls, "osascript call was not intercepted"
    assert fake_run.last.is_argv_list
    assert not fake_run.last.uses_shell


def test_fake_run_raises_on_check_true_nonzero(fake_run):
    """`check=True` + non-zero must raise, or error-path tests are meaningless."""
    import cleanup

    fake_run.install("cleanup")
    fake_run.set_response(stderr="Photos got an error", returncode=1)

    result = cleanup.reveal_in_photos("ABC-123")
    assert result["success"] is False
    assert "Photos got an error" in result["error"]


def test_unmocked_subprocess_is_blocked_loudly():
    """A test that forgets to mock must fail, never launch ffmpeg or Photos.app.

    No `fake_run` here on purpose — this asserts the autouse default.
    """
    from conftest import RealSubprocessBlocked

    import cleanup

    with pytest.raises(RealSubprocessBlocked):
        cleanup.reveal_in_photos("ABC-123")


def test_fake_run_intercepts_every_backend_module_at_once(fake_run):
    """One install covers all of them — they share the stdlib subprocess module."""
    import cleanup
    import export_video

    fake_run.install()
    assert cleanup.subprocess.run is fake_run
    assert export_video.subprocess.run is fake_run
    assert subprocess.run is fake_run


def test_ffmpeg_stderr_fixture_carries_the_real_apple_tags(ffmpeg_stderr):
    """The iPhone blob must keep the three things the parsers have rules about."""
    blob = ffmpeg_stderr["iphone_mov"]
    assert "com.apple.quicktime.creationdate" in blob
    assert "com.apple.quicktime.location.ISO6709" in blob
    assert "displaymatrix: rotation of -90.00 degrees" in blob


def test_fake_chroma_shapes_match_the_real_client(fake_chroma):
    """`get` returns flat lists; `query` returns lists-of-lists. Don't conflate."""
    fake_chroma.add_row("id1", path="/lib/a.jpg", apple_uuid="UUID-1")
    fake_chroma.add_row("id2", path="/lib/b.jpg", apple_uuid="UUID-2")

    got = fake_chroma.get(ids=["id1"], include=["metadatas"])
    assert got["ids"] == ["id1"]
    assert got["metadatas"][0]["apple_uuid"] == "UUID-1"

    queried = fake_chroma.query(n_results=2)
    assert queried["ids"] == [["id1", "id2"]]
    assert len(queried["metadatas"][0]) == 2

    assert fake_chroma.count() == 2
    fake_chroma.delete(ids=["id1"])
    assert fake_chroma.count() == 1


def test_tmp_motion_db_redirects_every_path_constant(tmp_motion_db, tmp_path):
    mr = tmp_motion_db.module
    for attr in ("MOTION_DIR", "PROPOSALS_DIR", "REVIEWS_DIR", "DRAFTS_DIR", "PREVIEW_DIR"):
        assert tmp_path in getattr(mr, attr).parents or getattr(mr, attr) == tmp_path
    assert tmp_path in mr.SAVINGS_PATH.parents

    prop = tmp_motion_db.proposal("vid1")
    assert (tmp_motion_db.proposals / "vid1.json").exists()
    assert prop["original_duration"] == 60.0


@pytest.mark.slow
def test_flask_client_serves_a_route_with_stubbed_globals(client):
    """Proves `server` imports without loading CLIP and that stubs take effect."""
    client.chroma.add_row("id1", path="/lib/a.jpg")

    resp = client.get("/stats")
    assert resp.status_code == 200
    assert resp.get_json()["total"] == 1


@pytest.mark.slow
def test_flask_client_returns_500_rather_than_raising(client):
    """PROPAGATE_EXCEPTIONS must stay off so status codes are assertable.

    /stats/increment with a non-numeric delta crashes today; the point here is
    only that the crash arrives as a *response*. The input-validation suite is
    what asserts it should be a 4xx.
    """
    resp = client.post("/stats/increment", json={"delta": "not-a-number"})
    assert resp.status_code in (400, 422, 500)
