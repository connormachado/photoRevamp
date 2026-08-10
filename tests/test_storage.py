"""
Climb Cutter storage: usage summary + bulk purge
=================================================
`get_usage` must match actual bytes on disk under uploads/, and
`purge_all_working_copies` must only ever touch OWNED entries via the same
keep-savings remove path `queue_removal` already uses — never the savings
ledger, never a non-owned/external file.
"""

import json

import pytest


UPLOAD_BYTES = b"WORKING-COPY-" + bytes(range(256)) * 4
EXTERNAL_BYTES = b"USERS-OWN-FILE-" + bytes(range(256)) * 4


@pytest.fixture
def st(tmp_motion_db):
    import storage

    return storage


def _make_owned(tmp_motion_db, video_id, content=UPLOAD_BYTES):
    src_dir = tmp_motion_db.module.MOTION_DIR / "uploads" / f"hash-{video_id}"
    src_dir.mkdir(parents=True)
    src = src_dir / f"{video_id}.mov"
    src.write_bytes(content)
    tmp_motion_db.proposal(video_id=video_id, source_path=str(src), owned=True)
    return src


def _make_external(tmp_motion_db, tmp_path, video_id, content=EXTERNAL_BYTES):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    src = library / f"{video_id}.mov"
    src.write_bytes(content)
    tmp_motion_db.proposal(video_id=video_id, source_path=str(src), owned=False)
    return src


class TestGetUsage:
    def test_empty_uploads_dir_is_zero(self, st):
        usage = st.get_usage()
        assert usage == {"total_bytes": 0, "count": 0}

    def test_sums_real_bytes_on_disk(self, st, tmp_motion_db):
        _make_owned(tmp_motion_db, "a", UPLOAD_BYTES)
        _make_owned(tmp_motion_db, "b", UPLOAD_BYTES * 2)
        usage = st.get_usage()
        assert usage["count"] == 2
        assert usage["total_bytes"] == len(UPLOAD_BYTES) + len(UPLOAD_BYTES) * 2

    def test_excludes_the_incoming_staging_dir(self, st, tmp_motion_db):
        incoming = tmp_motion_db.module.MOTION_DIR / "uploads" / ".incoming"
        incoming.mkdir(parents=True)
        (incoming / "staged.tmp").write_bytes(b"not-a-working-copy-yet" * 10)
        assert st.get_usage() == {"total_bytes": 0, "count": 0}


class TestPurgeAllWorkingCopies:
    def test_deletes_owned_entries_and_frees_disk(self, st, tmp_motion_db):
        src = _make_owned(tmp_motion_db, "owned1")
        result = st.purge_all_working_copies()
        assert result["purged"] == 1
        assert result["skipped"] == 0
        assert result["freed_bytes"] >= len(UPLOAD_BYTES)
        assert not src.exists()
        assert not (tmp_motion_db.proposals / "owned1.json").exists()

    def test_leaves_a_non_owned_original_untouched(self, st, tmp_motion_db, tmp_path):
        src = _make_external(tmp_motion_db, tmp_path, "ext1")
        result = st.purge_all_working_copies()
        assert result["purged"] == 0
        assert src.exists()
        assert (tmp_motion_db.proposals / "ext1.json").exists()

    def test_preserves_the_savings_ledger(self, st, tmp_motion_db):
        _make_owned(tmp_motion_db, "owned1")
        tmp_motion_db.savings.write_text(json.dumps({
            "total_bytes": 5000,
            "per_video": {"owned1": 5000},
        }))
        st.purge_all_working_copies()
        savings = json.loads(tmp_motion_db.savings.read_text())
        assert savings == {"total_bytes": 5000, "per_video": {"owned1": 5000}}

    def test_skips_a_video_currently_exporting(self, st, tmp_motion_db, monkeypatch):
        import export_job

        src = _make_owned(tmp_motion_db, "exporting1")
        monkeypatch.setattr(
            export_job, "is_exporting", lambda video_id=None: video_id == "exporting1"
        )
        result = st.purge_all_working_copies()
        assert result["purged"] == 0
        assert result["skipped"] == 1
        assert src.exists()
