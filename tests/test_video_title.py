"""
Editable video title
=====================
A user-typed title becomes an export filename and, since Photos names an
imported asset after the file on disk, a Photos asset name — so it has to
survive contact with a filesystem path, an ffmpeg argv, and an AppleScript
string unescaped. Three layers, each pinned here:

1. `safe_paths.sanitize_title_component` — a WHITELIST (unlike
   `safe_id_component`'s denylist), because a title is free text a person
   typed, not an opaque token.
2. `motion_review.get_title` / `set_title` / `_unique_export_name` — where a
   title is persisted and how two same-titled exports are kept from
   clobbering each other.
3. `POST /motion-review/title` — the route, which re-sanitizes server-side
   regardless of what the client already cleaned up.
"""

import pytest

pytestmark = pytest.mark.slow   # motion_review / safe_paths import utils -> torch


# ── sanitize_title_component ────────────────────────────────────────────────

class TestSanitizeTitleComponent:
    def test_a_clean_title_passes_through_unchanged(self):
        import safe_paths

        assert safe_paths.sanitize_title_component("Best Send 2024") == "Best Send 2024"

    @pytest.mark.parametrize("raw,expected", [
        ("my/climb", "myclimb"),
        ('best"send', "bestsend"),
        ("a\\b", "ab"),
        ("crux:move", "cruxmove"),
        ("run\x00away", "runaway"),
        ("😀party🎉", "party"),
        ("café run", "caf run"),   # only the non-ASCII letter is dropped
    ])
    def test_disallowed_characters_are_dropped_silently_not_replaced(self, raw, expected):
        import safe_paths

        # Dropped, never replaced with a placeholder like "-" — confirmed with
        # Connor: a stray character disappears rather than leaving a scar.
        assert safe_paths.sanitize_title_component(raw) == expected

    def test_whitespace_is_collapsed_and_trimmed(self):
        import safe_paths

        assert safe_paths.sanitize_title_component("  best   send  ") == "best send"

    def test_leading_and_trailing_dots_are_stripped_but_interior_ones_survive(self):
        import safe_paths

        assert safe_paths.sanitize_title_component("..v1.2.mov..") == "v1.2.mov"

    def test_a_title_that_is_only_disallowed_characters_sanitizes_to_empty(self):
        import safe_paths

        assert safe_paths.sanitize_title_component('/\\:"*?<>|') == ""

    def test_none_and_empty_and_non_string_are_all_empty(self):
        import safe_paths

        assert safe_paths.sanitize_title_component("") == ""
        assert safe_paths.sanitize_title_component(None) == ""
        assert safe_paths.sanitize_title_component(123) == ""

    def test_pure_dots_are_rejected_as_empty(self):
        import safe_paths

        assert safe_paths.sanitize_title_component(".") == ""
        assert safe_paths.sanitize_title_component("..") == ""

    @pytest.mark.parametrize("reserved", ["CON", "con", "NUL", "com1", "LPT9"])
    def test_reserved_device_names_sanitize_to_empty(self, reserved):
        import safe_paths

        assert safe_paths.sanitize_title_component(reserved) == ""

    def test_length_is_capped(self):
        import safe_paths

        result = safe_paths.sanitize_title_component("x" * 500)
        assert len(result) <= safe_paths.MAX_TITLE_LENGTH

    def test_a_length_cap_landing_mid_word_still_has_no_trailing_whitespace(self):
        import safe_paths

        raw = ("word " * 40)  # cap lands inside trailing spaces
        result = safe_paths.sanitize_title_component(raw)
        assert result == result.rstrip()


# ── storage: get_title / set_title ──────────────────────────────────────────

class TestTitleStorage:
    def test_a_video_with_no_title_returns_empty(self, tmp_motion_db):
        import motion_review

        tmp_motion_db.proposal("vid1")
        assert motion_review.get_title("vid1") == ""

    def test_set_then_get_round_trips(self, tmp_motion_db):
        import motion_review

        tmp_motion_db.proposal("vid1")
        motion_review.set_title("vid1", "Best Send")
        assert motion_review.get_title("vid1") == "Best Send"

    def test_setting_an_empty_title_clears_it(self, tmp_motion_db):
        import motion_review

        tmp_motion_db.proposal("vid1")
        motion_review.set_title("vid1", "Best Send")
        motion_review.set_title("vid1", "")
        assert motion_review.get_title("vid1") == ""

    def test_a_video_with_no_proposal_raises(self, tmp_motion_db):
        import motion_review

        with pytest.raises(FileNotFoundError):
            motion_review.set_title("no-such-video", "Best Send")

    def test_a_traversing_video_id_is_rejected(self, tmp_motion_db):
        import motion_review
        import safe_paths

        with pytest.raises(safe_paths.UnsafePathError):
            motion_review.set_title("../../etc/passwd", "Best Send")

    def test_the_title_appears_in_the_queue_entry(self, tmp_motion_db):
        import motion_review

        tmp_motion_db.proposal("vid1")
        motion_review.set_title("vid1", "Best Send")
        entries = motion_review.list_queue()
        assert entries[0]["title"] == "Best Send"

    def test_an_unset_title_is_an_empty_string_not_missing_in_the_queue_entry(self, tmp_motion_db):
        import motion_review

        tmp_motion_db.proposal("vid1")
        entries = motion_review.list_queue()
        assert entries[0]["title"] == ""


# ── _unique_export_name ─────────────────────────────────────────────────────

class TestUniqueExportName:
    def test_a_fresh_stem_gets_no_suffix(self, tmp_path, monkeypatch):
        import export_video
        import motion_review

        monkeypatch.setattr(export_video, "EXPORTS_DIR", tmp_path / "exports")
        assert motion_review._unique_export_name("Best Send") == "Best Send_trimmed.mp4"

    def test_a_taken_name_gets_a_counter_suffix(self, tmp_path, monkeypatch):
        import export_video
        import motion_review

        exports = tmp_path / "exports"
        exports.mkdir()
        monkeypatch.setattr(export_video, "EXPORTS_DIR", exports)
        (exports / "Best Send_trimmed.mp4").write_bytes(b"x")

        assert motion_review._unique_export_name("Best Send") == "Best Send-2_trimmed.mp4"

    def test_several_taken_names_advance_the_counter(self, tmp_path, monkeypatch):
        import export_video
        import motion_review

        exports = tmp_path / "exports"
        exports.mkdir()
        monkeypatch.setattr(export_video, "EXPORTS_DIR", exports)
        (exports / "Best Send_trimmed.mp4").write_bytes(b"x")
        (exports / "Best Send-2_trimmed.mp4").write_bytes(b"x")

        assert motion_review._unique_export_name("Best Send") == "Best Send-3_trimmed.mp4"


# ── the route ────────────────────────────────────────────────────────────────

class TestTitleRoute:
    def test_a_missing_video_id_is_a_client_error(self, client):
        res = client.post("/motion-review/title", json={"title": "Best Send"})
        assert res.status_code == 400

    def test_an_unknown_video_is_a_404(self, client, tmp_motion_db):
        res = client.post("/motion-review/title",
                           json={"video_id": "no-such-video", "title": "Best Send"})
        assert res.status_code == 404

    def test_a_clean_title_round_trips_through_the_route(self, client, tmp_motion_db):
        tmp_motion_db.proposal("vid1")
        res = client.post("/motion-review/title",
                           json={"video_id": "vid1", "title": "Best Send"})
        assert res.status_code == 200
        assert res.get_json()["title"] == "Best Send"

    def test_a_hostile_title_is_sanitized_before_being_stored_or_echoed(self, client, tmp_motion_db):
        tmp_motion_db.proposal("vid1")
        hostile = 'my/climb: "best"?\n; rm -rf ~'
        res = client.post("/motion-review/title", json={"video_id": "vid1", "title": hostile})
        assert res.status_code == 200
        sanitized = res.get_json()["title"]
        for bad in ('"', "/", "\\", ":", ";", "\n"):
            assert bad not in sanitized

    def test_the_route_never_trusts_a_pre_sanitized_client_value(self, client, tmp_motion_db):
        """Even a title claiming to already be clean is re-run through the
        sanitizer server-side — the route must not skip re-sanitizing just
        because the incoming string already looks safe."""
        tmp_motion_db.proposal("vid1")
        res = client.post("/motion-review/title",
                           json={"video_id": "vid1", "title": "already/clean\""})
        assert '"' not in res.get_json()["title"]
        assert "/" not in res.get_json()["title"]
