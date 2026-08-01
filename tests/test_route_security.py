"""
Path traversal on the file-serving routes
=========================================
Two request-shaped inputs become filesystem paths, and before the hardening pass
both did it verbatim:

* `/thumbnail?path=` and `/full?path=` handed the raw string to `Path()` and
  `send_file` — so `?path=/Users/you/.ssh/id_rsa` returned the key. No `../`
  needed; an absolute path was accepted outright.
* `video_id` was concatenated into `<dir>/<video_id>.json`. proposals/, reviews/
  and drafts/ are SIBLINGS, so a `../`-laden id made the guard-read and the write
  resolve to the same file outside the tree — turning `save_draft` into an
  arbitrary overwrite of any valid-JSON file, and `_clear_draft` into an
  arbitrary unlink.

None of that was theoretical. The app ships CORS and no authentication, so any
page the user visits while the server runs can call these routes.

These tests are the regression net for that fix, so several assert on the
FILESYSTEM rather than the status code: a route that returns 403 while still
having written the file would pass a status-only test.
"""

import json

import pytest

pytestmark = pytest.mark.slow


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A fake photo library, with the containment roots pointed at it."""
    import safe_paths
    import server

    root = tmp_path / "library"
    (root / "photos").mkdir(parents=True)

    # A real JPEG so the happy path can actually be served.
    from PIL import Image

    photo = root / "photos" / "real.jpg"
    Image.new("RGB", (64, 48), "red").save(photo)

    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("SUPER-SECRET-PRIVATE-KEY")

    monkeypatch.setattr(safe_paths, "ALLOWED_ROOTS", [root])
    monkeypatch.setattr(server.safe_paths, "ALLOWED_ROOTS", [root])

    class Library:
        pass

    lib = Library()
    lib.root = root
    lib.photo = photo
    lib.secret = secret
    return lib


# ── /full and /thumbnail ──────────────────────────────────────────────────────

class TestFileServingTraversal:
    @pytest.mark.parametrize("route", ["/full", "/thumbnail"])
    def test_an_absolute_path_outside_the_library_is_refused(self, client, library, route):
        """The original hole needed no `../` at all."""
        resp = client.get(f"{route}?path={library.secret}")
        assert resp.status_code == 403
        assert b"SUPER-SECRET" not in resp.data

    @pytest.mark.parametrize("route", ["/full", "/thumbnail"])
    def test_a_relative_escape_is_refused(self, client, library, route):
        resp = client.get(f"{route}?path={library.root}/photos/../../outside/secret.txt")
        assert resp.status_code == 403
        assert b"SUPER-SECRET" not in resp.data

    @pytest.mark.parametrize("route", ["/full", "/thumbnail"])
    def test_a_classic_etc_passwd_traversal_is_refused(self, client, library, route):
        resp = client.get(f"{route}?path=../../../../../../etc/passwd")
        assert resp.status_code == 403
        assert b"root:" not in resp.data

    @pytest.mark.parametrize("route", ["/full", "/thumbnail"])
    def test_a_url_encoded_traversal_is_refused(self, client, library, route):
        """Werkzeug decodes %2e%2e%2f before the view sees it, so the guard must
        run on the decoded value — which it does, but pin it."""
        resp = client.get(f"{route}?path=%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd")
        assert resp.status_code == 403

    @pytest.mark.parametrize("route", ["/full", "/thumbnail"])
    def test_a_symlink_pointing_out_of_the_library_is_refused(self, client, library, route):
        """The reason the check resolves before comparing: a bare prefix test on
        the unresolved path would accept this."""
        link = library.root / "photos" / "escape.jpg"
        link.symlink_to(library.secret)

        resp = client.get(f"{route}?path={link}")
        assert resp.status_code == 403
        assert b"SUPER-SECRET" not in resp.data

    @pytest.mark.parametrize("route", ["/full", "/thumbnail"])
    def test_a_home_relative_path_is_refused(self, client, library, route):
        resp = client.get(f"{route}?path=~/.ssh/id_rsa")
        assert resp.status_code == 403

    @pytest.mark.parametrize("route", ["/full", "/thumbnail"])
    def test_an_empty_path_is_refused(self, client, library, route):
        resp = client.get(route)
        assert resp.status_code == 403

    @pytest.mark.parametrize("route", ["/full", "/thumbnail"])
    def test_a_null_byte_is_refused(self, client, library, route):
        """A NUL truncates the path at the C layer — a classic guard bypass."""
        resp = client.get(f"{route}?path={library.root}/photos/real.jpg\x00.txt")
        assert resp.status_code == 403

    def test_refusal_does_not_reveal_whether_the_file_exists(self, client, library):
        """Same 403 either way, so the route can't be used to probe the disk."""
        existing = client.get(f"/full?path={library.secret}")
        missing = client.get("/full?path=/outside/definitely-not-here.txt")
        assert existing.status_code == missing.status_code == 403


class TestFileServingStillWorks:
    """The guard has to let real traffic through — an over-tight fix is also a bug."""

    def test_a_real_photo_inside_the_library_is_served(self, client, library):
        resp = client.get(f"/full?path={library.photo}")
        assert resp.status_code == 200
        assert len(resp.data) > 0

    def test_a_thumbnail_of_a_real_photo_is_served(self, client, library):
        resp = client.get(f"/thumbnail?path={library.photo}")
        assert resp.status_code == 200
        assert resp.mimetype == "image/jpeg"

    def test_a_missing_file_inside_the_library_is_a_404_not_a_403(self, client, library):
        """Distinguishes "not allowed" from "not there" for legitimate callers."""
        resp = client.get(f"/full?path={library.root}/photos/gone.jpg")
        assert resp.status_code == 404

    def test_a_path_with_dot_segments_that_stays_inside_is_allowed(self, client, library):
        resp = client.get(f"/full?path={library.root}/photos/../photos/real.jpg")
        assert resp.status_code == 200


# ── video_id traversal ────────────────────────────────────────────────────────

TRAVERSING_IDS = [
    "../../../../../../tmp/evil",
    "..%2f..%2fevil",
    "../evil",
    "/etc/hosts",
    "..",
    ".",
    "sub/dir",
    "back\\slash",
]


@pytest.fixture
def sentinel(tmp_path):
    """A valid-JSON file outside the motion-review tree, to be attacked."""
    path = tmp_path / "precious.json"
    path.write_text(json.dumps({"important": "user data"}))
    return path


class TestVideoIdTraversal:
    @pytest.mark.parametrize("video_id", TRAVERSING_IDS)
    def test_draft_refuses_a_traversing_id(self, client, tmp_motion_db, video_id):
        resp = client.post("/motion-review/draft",
                           json={"video_id": video_id, "regions": []})
        assert 400 <= resp.status_code < 500, "traversal must not 500 or succeed"

    def test_draft_writes_nothing_outside_the_motion_tree(
        self, client, tmp_motion_db, sentinel, tmp_path
    ):
        """The real payload: proposals/ and drafts/ are siblings, so the guard
        read and the write land on the SAME file outside the tree."""
        target = str(sentinel)[: -len(".json")]
        escape = "../" * 12 + target.lstrip("/")

        client.post("/motion-review/draft", json={"video_id": escape, "regions": []})

        assert json.loads(sentinel.read_text()) == {"important": "user data"}

    @pytest.mark.parametrize("video_id", TRAVERSING_IDS)
    def test_decision_refuses_a_traversing_id(self, client, tmp_motion_db, video_id):
        resp = client.post("/motion-review/decision",
                           json={"video_id": video_id, "verdict": "approve"})
        assert 400 <= resp.status_code < 500

    def test_decision_writes_no_review_outside_the_motion_tree(
        self, client, tmp_motion_db, sentinel
    ):
        target = str(sentinel)[: -len(".json")]
        escape = "../" * 12 + target.lstrip("/")

        client.post("/motion-review/decision",
                    json={"video_id": escape, "verdict": "approve"})

        assert json.loads(sentinel.read_text()) == {"important": "user data"}

    def test_export_does_not_unlink_a_file_outside_the_motion_tree(
        self, client, tmp_motion_db, sentinel
    ):
        """`_clear_draft` calls .unlink() on a path built from the id — the same
        primitive, pointed at deletion.

        Note this test alone is weak: without a matching proposal the route bails
        out with a 404 long before reaching `_clear_draft`, so it would stay green
        even with the guard removed. The unit test below is the one with teeth.
        """
        target = str(sentinel)[: -len(".json")]
        escape = "../" * 12 + target.lstrip("/")

        resp = client.post("/motion-review/export", json={"video_id": escape})

        assert sentinel.exists(), "export deleted a file outside the motion-review tree"
        assert 400 <= resp.status_code < 500

    def test_clear_draft_refuses_to_unlink_outside_the_drafts_dir(
        self, tmp_motion_db, sentinel
    ):
        """The deletion primitive, exercised directly.

        `_clear_draft` swallows FileNotFoundError, so a traversing id used to
        reach straight through to `.unlink()` on any path the user can write.
        Called at the unit level because the route in front of it bails out
        earlier for unrelated reasons — testing only through the route would give
        a green result that proves nothing.
        """
        import safe_paths

        target = str(sentinel)[: -len(".json")]
        escape = "../" * 12 + target.lstrip("/")

        with pytest.raises(safe_paths.UnsafePathError):
            tmp_motion_db.module._clear_draft(escape)

        assert sentinel.exists(), "a traversing video_id deleted a file outside drafts/"

    def test_save_draft_refuses_to_write_outside_the_drafts_dir(
        self, tmp_motion_db, sentinel
    ):
        """The write primitive at the unit level, for the same reason."""
        import safe_paths

        target = str(sentinel)[: -len(".json")]
        escape = "../" * 12 + target.lstrip("/")

        with pytest.raises(safe_paths.UnsafePathError):
            tmp_motion_db.module.save_draft(escape, [])

        assert json.loads(sentinel.read_text()) == {"important": "user data"}

    @pytest.mark.parametrize("video_id", TRAVERSING_IDS)
    def test_source_refuses_a_traversing_id(self, client, tmp_motion_db, video_id):
        resp = client.get(f"/motion-review/source?id={video_id}")
        assert 400 <= resp.status_code < 500

    @pytest.mark.parametrize("video_id", TRAVERSING_IDS)
    def test_timelapse_refuses_a_traversing_id(self, client, tmp_motion_db, video_id):
        resp = client.get(f"/motion-review/timelapse?id={video_id}")
        assert 400 <= resp.status_code < 500

    def test_a_leading_dash_id_is_refused(self, client, tmp_motion_db):
        """Never reaches a shell, but it becomes an ffmpeg output filename."""
        resp = client.post("/motion-review/draft",
                           json={"video_id": "-rf", "regions": []})
        assert 400 <= resp.status_code < 500


class TestVideoIdStillWorks:
    def test_a_normal_md5_id_is_accepted(self, client, tmp_motion_db, tmp_path):
        """Real ids are md5 hexdigests; the guard must not reject them."""
        video_id = "a3f5c9e1b7d24680a3f5c9e1b7d24680"
        source = tmp_path / "clip.mov"
        source.write_bytes(b"x" * 1000)
        tmp_motion_db.proposal(video_id, source_path=str(source))

        resp = client.post("/motion-review/draft",
                           json={"video_id": video_id, "regions": []})
        assert resp.status_code == 200
        assert (tmp_motion_db.drafts / f"{video_id}.json").exists()

    def test_an_id_with_dots_and_dashes_inside_is_accepted(self, client, tmp_motion_db,
                                                           tmp_path):
        """Only path SYNTAX is rejected, not every unusual character."""
        video_id = "clip.2026-02-11_v2"
        source = tmp_path / "clip.mov"
        source.write_bytes(b"x" * 1000)
        tmp_motion_db.proposal(video_id, source_path=str(source))

        resp = client.post("/motion-review/draft",
                           json={"video_id": video_id, "regions": []})
        assert resp.status_code == 200


# ── CORS ──────────────────────────────────────────────────────────────────────

class TestCorsScope:
    """`CORS(app)` with no arguments made every route readable from any origin.

    With no authentication anywhere in this app, that is what turned the
    arbitrary-read above into a drive-by: a page on any site could fetch
    localhost:5001 and read the response body. Binding to 127.0.0.1 does not
    help — the browser is already inside that boundary.
    """

    def test_a_hostile_origin_is_not_granted_access(self, client):
        resp = client.get("/stats", headers={"Origin": "https://evil.example"})
        assert resp.headers.get("Access-Control-Allow-Origin") != "*"
        assert resp.headers.get("Access-Control-Allow-Origin") != "https://evil.example"

    def test_the_vite_dev_server_is_still_allowed(self, client):
        resp = client.get("/stats", headers={"Origin": "http://localhost:5173"})
        assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"

    def test_a_hostile_origin_cannot_preflight_a_post(self, client):
        resp = client.open("/motion-review/draft", method="OPTIONS", headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        })
        assert resp.headers.get("Access-Control-Allow-Origin") not in (
            "*", "https://evil.example"
        )
