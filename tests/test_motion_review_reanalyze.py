"""
On-demand re-analysis — motion_review.reanalyze() / POST /motion-review/analyze
=================================================================================
Backs the right-tool-rail "Analyze Motion" button: re-run video_motion's
dead-time detection against a video already in the queue and overwrite its
proposal in place.

`video_motion.process_video` is stubbed everywhere here, the same way
`test_video_upload.py` stubs it. `tmp_motion_db` only redirects `motion_review.py`'s
own path constants (PROPOSALS_DIR etc.) — it does NOT touch `video_motion.py`'s
separate, identically-named MOTION_DIR constant, so a real `process_video` call
would escape the sandbox and write into the live `photo_db/motion_review/` tree.
"""

import json

import pytest

import motion_review as mr


def _stub_process_video(monkeypatch, tmp_motion_db, calls):
    """Install a fake video_motion.process_video that records (video_arg, owned)
    and rewrites the proposal the way the real one would (fresh `created`,
    same video_id, `owned` set to whatever was passed in)."""

    def fake(video_arg, config, owned=False):
        calls.append((video_arg, owned))
        prop_path = next(tmp_motion_db.proposals.glob("*.json"))
        prop = json.loads(prop_path.read_text())
        prop["owned"] = owned
        prop["created"] = "2099-01-01T00:00:00+00:00"
        prop_path.write_text(json.dumps(prop))

    monkeypatch.setattr(mr.video_motion, "process_video", fake)
    monkeypatch.setattr(mr.video_motion, "load_config", lambda: {"stub_config": True})


class TestReanalyze:
    def test_reruns_analysis_against_the_known_source_path(
        self, tmp_motion_db, monkeypatch, tmp_path
    ):
        source = tmp_path / "clip.mov"
        source.write_bytes(b"x" * 100)
        tmp_motion_db.proposal("vid1", source_path=str(source))
        calls = []
        _stub_process_video(monkeypatch, tmp_motion_db, calls)

        entry = mr.reanalyze("vid1")

        assert len(calls) == 1
        assert calls[0][0] == str(source)
        assert entry["video_id"] == "vid1"

    def test_source_under_uploads_dir_with_explicit_owned_true_stays_owned(
        self, tmp_motion_db, monkeypatch
    ):
        uploads = tmp_motion_db.root / "uploads"
        uploads.mkdir(parents=True)
        source = uploads / "vid1.mov"
        source.write_bytes(b"x" * 100)
        tmp_motion_db.proposal("vid1", source_path=str(source), owned=True)
        calls = []
        _stub_process_video(monkeypatch, tmp_motion_db, calls)

        mr.reanalyze("vid1")

        assert calls[0][1] is True

    def test_legacy_proposal_under_uploads_dir_with_no_owned_key_infers_owned_true(
        self, tmp_motion_db, monkeypatch
    ):
        """A proposal written before the `owned` field existed carries no such
        key at all. queue_removal._owned_source infers ownership from uploads/
        containment in that case — reanalyze must preserve that inference
        rather than defaulting to owned=False and stripping delete-eligibility
        from an app-owned working copy."""
        uploads = tmp_motion_db.root / "uploads"
        uploads.mkdir(parents=True)
        source = uploads / "vid1.mov"
        source.write_bytes(b"x" * 100)
        # tmp_motion_db.proposal() doesn't set "owned" unless overridden — this
        # IS the legacy shape (the field genuinely absent from the JSON).
        tmp_motion_db.proposal("vid1", source_path=str(source))
        calls = []
        _stub_process_video(monkeypatch, tmp_motion_db, calls)

        mr.reanalyze("vid1")

        assert calls[0][1] is True

    def test_source_outside_uploads_dir_is_never_owned_regardless_of_flag(
        self, tmp_motion_db, monkeypatch, tmp_path
    ):
        """A Photos original (or any hand-fed path outside uploads/) must never
        be treated as ours to delete, even if a proposal claims owned: true."""
        source = tmp_path / "clip.mov"
        source.write_bytes(b"x" * 100)
        tmp_motion_db.proposal("vid1", source_path=str(source), owned=True)
        calls = []
        _stub_process_video(monkeypatch, tmp_motion_db, calls)

        mr.reanalyze("vid1")

        assert calls[0][1] is False

    def test_source_no_longer_on_disk_raises_file_not_found(self, tmp_motion_db, tmp_path):
        tmp_motion_db.proposal("vid1", source_path=str(tmp_path / "gone.mov"))
        with pytest.raises(FileNotFoundError):
            mr.reanalyze("vid1")

    def test_unknown_video_id_raises_file_not_found(self, tmp_motion_db):
        with pytest.raises(FileNotFoundError):
            mr.reanalyze("never-seen")


class TestReanalyzeRoute:
    def test_returns_the_fresh_queue_entry(self, client, tmp_motion_db, monkeypatch, tmp_path):
        source = tmp_path / "clip.mov"
        source.write_bytes(b"x" * 100)
        tmp_motion_db.proposal("vid1", source_path=str(source))
        _stub_process_video(monkeypatch, tmp_motion_db, [])

        resp = client.post("/motion-review/analyze", json={"video_id": "vid1"})

        assert resp.status_code == 200
        assert resp.get_json()["video_id"] == "vid1"

    def test_unknown_video_id_is_a_404(self, client, tmp_motion_db):
        resp = client.post("/motion-review/analyze", json={"video_id": "never-seen"})
        assert resp.status_code == 404

    def test_a_missing_video_id_is_a_400(self, client, tmp_motion_db):
        assert client.post("/motion-review/analyze", json={}).status_code == 400

    def test_refuses_while_an_export_is_in_progress_for_this_video(
        self, client, tmp_motion_db, monkeypatch, tmp_path
    ):
        source = tmp_path / "clip.mov"
        source.write_bytes(b"x" * 100)
        tmp_motion_db.proposal("vid1", source_path=str(source))
        import export_job
        monkeypatch.setattr(export_job, "is_exporting", lambda video_id=None: True)

        resp = client.post("/motion-review/analyze", json={"video_id": "vid1"})

        assert resp.status_code == 409
