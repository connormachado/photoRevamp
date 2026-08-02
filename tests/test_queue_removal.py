"""
Queue removal
=============
`queue_removal` is the first thing in this app that deletes a file on purpose,
so most of what is tested here is the *refusal*: which paths it must leave
alone, and the fact that neither half of the ownership check can authorise a
delete on its own.

The uploads dir is derived from `motion_review.MOTION_DIR` (so it follows the
`tmp_motion_db` redirect) rather than imported from `video_upload`, which is a
duplicated fact — `test_the_uploads_dir_matches_the_upload_routes` is what keeps
the two from drifting apart.
"""

import hashlib
import json

import pytest


UPLOAD_BYTES = b"UPLOADED-WORKING-COPY-" + bytes(range(256)) * 8
EXTERNAL_BYTES = b"THE-USERS-OWN-FILE-" + bytes(range(256)) * 8


@pytest.fixture
def qr(tmp_motion_db):
    """The module under test, with motion_review already redirected to tmp."""
    import queue_removal

    return queue_removal


def _md5(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


@pytest.fixture
def upload_entry(tmp_motion_db, qr):
    """A queue entry whose source is a copy the app made under uploads/."""
    src_dir = qr._uploads_dir() / "abc123def456"
    src_dir.mkdir(parents=True)
    src = src_dir / "IMG_9999.mov"
    src.write_bytes(UPLOAD_BYTES)
    tmp_motion_db.proposal(video_id="upl", source_path=str(src), owned=True)
    return src


@pytest.fixture
def external_entry(tmp_motion_db, tmp_path):
    """A queue entry that only REFERENCES a file the user already had."""
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    src = library / "IMG_ORIGINAL.mov"
    src.write_bytes(EXTERNAL_BYTES)
    tmp_motion_db.proposal(video_id="ext", source_path=str(src), owned=False)
    return src


# ── the ownership decision ────────────────────────────────────────────────────

class TestOwnedSource:
    def test_an_upload_with_the_flag_is_ours(self, qr, tmp_motion_db, upload_entry):
        prop = json.loads((tmp_motion_db.proposals / "upl.json").read_text())
        assert qr._owned_source(prop) == upload_entry.resolve()

    def test_an_external_file_is_not_ours(self, qr, tmp_motion_db, external_entry):
        prop = json.loads((tmp_motion_db.proposals / "ext.json").read_text())
        assert qr._owned_source(prop) is None

    def test_a_legacy_proposal_under_uploads_is_inferred_as_ours(self, qr, tmp_motion_db):
        """The 9 proposals written before the flag existed carry no `owned` key.
        Sitting under uploads/ is the only way a file got there."""
        src_dir = qr._uploads_dir() / "legacyhash"
        src_dir.mkdir(parents=True)
        src = src_dir / "OLD.mov"
        src.write_bytes(UPLOAD_BYTES)
        prop = tmp_motion_db.proposal(video_id="legacy", source_path=str(src))
        assert "owned" not in prop
        assert qr._owned_source(prop) == src.resolve()

    def test_a_legacy_proposal_outside_uploads_is_not_ours(self, qr, tmp_motion_db, tmp_path):
        src = tmp_path / "somewhere.mov"
        src.write_bytes(EXTERNAL_BYTES)
        prop = tmp_motion_db.proposal(video_id="legacy2", source_path=str(src))
        assert qr._owned_source(prop) is None

    def test_the_flag_alone_cannot_authorise_a_delete(self, qr, tmp_motion_db, tmp_path):
        """A proposal claiming `owned: true` about a path outside uploads/ is
        either corrupt or hostile. Containment is the enforcement, not the flag."""
        src = tmp_path / "library" / "NOT_OURS.mov"
        src.parent.mkdir(exist_ok=True)
        src.write_bytes(EXTERNAL_BYTES)
        prop = tmp_motion_db.proposal(video_id="liar", source_path=str(src), owned=True)
        assert qr._owned_source(prop) is None

    def test_an_explicit_false_is_honoured_even_under_uploads(self, qr, tmp_motion_db):
        src_dir = qr._uploads_dir() / "hash2"
        src_dir.mkdir(parents=True)
        src = src_dir / "declined.mov"
        src.write_bytes(UPLOAD_BYTES)
        prop = tmp_motion_db.proposal(video_id="no", source_path=str(src), owned=False)
        assert qr._owned_source(prop) is None

    def test_a_symlink_out_of_uploads_is_not_followed(self, qr, tmp_motion_db, tmp_path):
        """resolve_within_roots resolves BEFORE comparing, so a link parked in
        uploads/ cannot smuggle an external path past the containment check."""
        target = tmp_path / "library" / "REAL_ORIGINAL.mov"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(EXTERNAL_BYTES)
        src_dir = qr._uploads_dir() / "hash3"
        src_dir.mkdir(parents=True)
        link = src_dir / "looks_like_ours.mov"
        link.symlink_to(target)

        prop = tmp_motion_db.proposal(video_id="link", source_path=str(link), owned=True)
        assert qr._owned_source(prop) is None

    def test_a_missing_source_path_is_not_ours(self, qr, tmp_motion_db):
        prop = tmp_motion_db.proposal(video_id="nopath", source_path="", owned=True)
        assert qr._owned_source(prop) is None

    def test_the_uploads_dir_matches_the_upload_routes(self, qr):
        """Guards the one fact this module duplicates rather than imports."""
        import video_upload

        import motion_review as mr
        assert (mr.DEFAULT_DB_PATH / "motion_review" / "uploads").resolve() == \
            video_upload.UPLOADS_DIR.resolve()


# ── removal ───────────────────────────────────────────────────────────────────

class TestRemoveFromQueue:
    def test_an_owned_upload_is_deleted_and_its_bytes_reported(
        self, qr, tmp_motion_db, upload_entry
    ):
        result = qr.remove_from_queue("upl")

        assert not upload_entry.exists(), "the working copy survived"
        assert result["deleted_source"] is True
        assert result["freed_bytes"] >= len(UPLOAD_BYTES)
        assert result["source_name"] == "IMG_9999.mov"

    def test_the_hash_dir_goes_with_its_only_file(self, qr, upload_entry):
        qr.remove_from_queue("upl")
        assert not upload_entry.parent.exists()

    def test_an_external_source_is_left_byte_identical(
        self, qr, tmp_motion_db, external_entry
    ):
        before = _md5(external_entry)
        stat_before = external_entry.stat()

        result = qr.remove_from_queue("ext")

        assert external_entry.exists(), "removal deleted a file the app did not create"
        assert _md5(external_entry) == before
        assert external_entry.stat().st_mtime_ns == stat_before.st_mtime_ns
        assert result["deleted_source"] is False

    def test_the_row_disappears_from_the_queue(self, qr, tmp_motion_db, upload_entry):
        assert any(v["video_id"] == "upl" for v in tmp_motion_db.module.list_queue())

        qr.remove_from_queue("upl")

        assert not (tmp_motion_db.proposals / "upl.json").exists()
        assert all(v["video_id"] != "upl" for v in tmp_motion_db.module.list_queue())

    def test_the_row_disappears_for_an_external_entry_too(
        self, qr, tmp_motion_db, external_entry
    ):
        qr.remove_from_queue("ext")
        assert all(v["video_id"] != "ext" for v in tmp_motion_db.module.list_queue())

    def test_derivatives_and_the_draft_go(self, qr, tmp_motion_db, upload_entry):
        trimmed = tmp_motion_db.root / "clips" / "upl_trimmed.mkv"
        timelapse = tmp_motion_db.root / "cuts" / "upl_removed.mp4"
        for p in (trimmed, timelapse):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"derived bytes")
        tmp_motion_db.proposal(
            video_id="upl",
            source_path=str(upload_entry),
            owned=True,
            artifacts={"trimmed": str(trimmed), "timelapse": str(timelapse)},
        )
        proxy = tmp_motion_db.preview / f"upl{tmp_motion_db.module.PREVIEW_SUFFIX}"
        proxy.write_bytes(b"proxy bytes")
        legacy = tmp_motion_db.preview / f"upl{tmp_motion_db.module.PREVIEW_LEGACY_SUFFIXES[0]}"
        legacy.write_bytes(b"old proxy bytes")
        draft = tmp_motion_db.drafts / "upl.json"
        draft.write_text('{"video_id": "upl", "regions": []}')

        qr.remove_from_queue("upl")

        for p in (trimmed, timelapse, proxy, legacy, draft):
            assert not p.exists(), f"{p.name} survived the removal"

    def test_derivatives_go_for_an_external_entry_too(
        self, qr, tmp_motion_db, external_entry
    ):
        """They are app-created whoever owns the source, and re-analysis makes
        them again — orphaning them once the row is gone is pure waste."""
        proxy = tmp_motion_db.preview / f"ext{tmp_motion_db.module.PREVIEW_SUFFIX}"
        proxy.write_bytes(b"proxy bytes")

        qr.remove_from_queue("ext")

        assert not proxy.exists()

    def test_an_artifact_path_outside_the_motion_dir_is_refused(
        self, qr, tmp_motion_db, tmp_path, upload_entry
    ):
        """A proposal is just JSON on disk; its artifact paths are not a mandate
        to unlink anything anywhere."""
        outsider = tmp_path / "library" / "precious.mov"
        outsider.parent.mkdir(exist_ok=True)
        outsider.write_bytes(EXTERNAL_BYTES)
        tmp_motion_db.proposal(
            video_id="upl",
            source_path=str(upload_entry),
            owned=True,
            artifacts={"trimmed": str(outsider), "timelapse": None},
        )

        qr.remove_from_queue("upl")

        assert outsider.exists()

    def test_the_freed_total_counts_every_file_it_deleted(
        self, qr, tmp_motion_db, upload_entry
    ):
        proxy = tmp_motion_db.preview / f"upl{tmp_motion_db.module.PREVIEW_SUFFIX}"
        proxy.write_bytes(b"x" * 500)

        result = qr.remove_from_queue("upl")

        prop_size = len(json.dumps({}))  # the proposal counts too; just bound it
        assert result["freed_bytes"] >= len(UPLOAD_BYTES) + 500 + prop_size


# ── history ───────────────────────────────────────────────────────────────────

class TestHistorySurvives:
    def test_the_review_file_is_kept(self, qr, tmp_motion_db, upload_entry):
        review = tmp_motion_db.reviews / "upl.json"
        review.write_text('{"video_id": "upl", "verdict": "reject"}')

        qr.remove_from_queue("upl")

        assert review.exists()
        assert json.loads(review.read_text())["verdict"] == "reject"

    def test_existing_decision_lines_are_untouched(self, qr, tmp_motion_db, upload_entry):
        tmp_motion_db.decisions.write_text(
            '{"video_id": "upl", "verdict": "reject"}\n'
            '{"video_id": "other", "verdict": "approve"}\n'
        )

        qr.remove_from_queue("upl")

        lines = [json.loads(l) for l in tmp_motion_db.decisions.read_text().splitlines()]
        assert lines[0] == {"video_id": "upl", "verdict": "reject"}
        assert lines[1] == {"video_id": "other", "verdict": "approve"}

    def test_a_remove_line_is_appended(self, qr, tmp_motion_db, upload_entry):
        qr.remove_from_queue("upl")

        last = json.loads(tmp_motion_db.decisions.read_text().splitlines()[-1])
        assert last["action"] == "remove"
        assert last["video_id"] == "upl"
        assert last["deleted_source"] is True
        assert last["freed_bytes"] > 0

    def test_the_savings_ledger_is_not_touched(self, qr, tmp_motion_db, upload_entry):
        """Removal is cleanup. The reject that precedes it has already popped
        the video via _apply_savings; doing it again here would be a second,
        untracked writer of the same ledger."""
        tmp_motion_db.savings.write_text('{"total_bytes": 99, "per_video": {"upl": 99}}')

        qr.remove_from_queue("upl")

        assert json.loads(tmp_motion_db.savings.read_text()) == {
            "total_bytes": 99, "per_video": {"upl": 99}
        }


# ── interaction with the savings ledger ──────────────────────────────────────

class TestRemoveFromQueueKeepsSavingsCredit:
    """`record_decision`'s reject path retracts a video's savings credit
    (`_apply_savings`); `remove_from_queue` never does. These tests exercise
    both real entry points together, not just the ledger in isolation, so a
    change that accidentally routed Remove through record_decision (or
    dropped reject's retraction) would show up here."""

    def test_removing_an_exported_video_leaves_its_credit_in_place(
        self, qr, tmp_motion_db, upload_entry
    ):
        mr = tmp_motion_db.module
        decision = mr.record_decision("upl", "approve")
        assert decision["savings_total_bytes"] > 0

        qr.remove_from_queue("upl")

        assert mr.get_savings()["total_bytes"] == decision["savings_total_bytes"]
        assert mr.get_savings()["per_video"] == {"upl": decision["video_saved_bytes"]}

    def test_rejecting_still_retracts_before_a_remove_that_follows_it(
        self, qr, tmp_motion_db, upload_entry
    ):
        """The pre-existing Reject button's flow (decision:reject, then
        remove) must keep retracting — Remove's new ledger-preserving
        behavior must not leak into it."""
        mr = tmp_motion_db.module
        mr.record_decision("upl", "approve")

        rejected = mr.record_decision("upl", "reject")
        assert rejected["savings_total_bytes"] == 0

        qr.remove_from_queue("upl")

        assert mr.get_savings() == {"total_bytes": 0, "per_video": {}}

    def test_removing_one_exported_video_does_not_disturb_another_videos_credit(
        self, qr, tmp_motion_db, upload_entry
    ):
        """Removing "upl" frees its file but is not a reject, so ITS credit
        stays too — this only proves the OTHER video's credit is untouched."""
        mr = tmp_motion_db.module
        src2_dir = qr._uploads_dir() / "otherhash"
        src2_dir.mkdir(parents=True)
        src2 = src2_dir / "IMG_OTHER.mov"
        src2.write_bytes(UPLOAD_BYTES)
        tmp_motion_db.proposal(video_id="upl2", source_path=str(src2), owned=True)

        mine = mr.record_decision("upl", "approve")
        other = mr.record_decision("upl2", "approve")

        qr.remove_from_queue("upl")

        assert mr.get_savings()["per_video"] == {
            "upl": mine["video_saved_bytes"],
            "upl2": other["video_saved_bytes"],
        }


# ── bad input ─────────────────────────────────────────────────────────────────

class TestBadInput:
    def test_an_unknown_video_id_raises(self, qr, tmp_motion_db):
        with pytest.raises(FileNotFoundError):
            qr.remove_from_queue("nosuchvideo")

    @pytest.mark.parametrize("bad_id", ["../escape", "a/b", "..", "", "-flag", "a\\b"])
    def test_a_traversing_id_is_refused(self, qr, tmp_motion_db, bad_id):
        with pytest.raises(ValueError):
            qr.remove_from_queue(bad_id)

    def test_a_traversing_id_deletes_nothing(self, qr, tmp_motion_db, tmp_path):
        """The id is interpolated into four sibling dirs' filenames, so a `../`
        that got through would unlink outside the tree."""
        sentinel = tmp_path / "sentinel.json"
        sentinel.write_text('{"keep": "me"}')

        with pytest.raises(ValueError):
            qr.remove_from_queue("../../sentinel")

        assert sentinel.exists()
