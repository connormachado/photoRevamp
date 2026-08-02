"""
Route input validation
======================
The rule: garbage in a request produces a 4xx, never a 500.

This is not only tidiness. Before the hardening pass most routes called `int(...)`
or `.get(...)` straight on unvalidated input, so `{"delta": "x"}` raised out of
the view — and with `debug=True` (the old default) Flask answered with a full
traceback: absolute paths, source lines, local variable names, shipped to the
caller. Every 500 on a user-supplied value was an information leak as well as a
crash.

`n` gets its own attention because it flows into `collection.query(n_results=)`.
Unbounded, it is a one-request way to make the server walk the entire index.
"""

import base64
import time

import pytest

pytestmark = pytest.mark.slow


def assert_client_error(resp, route: str):
    assert 400 <= resp.status_code < 500, (
        f"{route} answered {resp.status_code}; malformed input must be a 4xx, "
        f"not a server error. Body: {resp.data[:200]!r}"
    )


# ── /stats/increment ──────────────────────────────────────────────────────────

class TestStatsIncrement:
    @pytest.mark.parametrize("body", [
        {"delta": "not-a-number"},
        {"delta": []},
        {"delta": {}},
        {"delta": None},
        {"delta": "1; DROP TABLE"},
    ])
    def test_a_non_numeric_delta_is_a_client_error(self, client, body):
        assert_client_error(client.post("/stats/increment", json=body),
                            "/stats/increment")

    def test_a_missing_delta_defaults_to_a_no_op(self, client):
        resp = client.post("/stats/increment", json={})
        assert resp.status_code == 200
        assert resp.get_json()["deleted"] == 0

    def test_a_non_object_body_does_not_crash(self, client):
        """`request.get_json()` happily returns a str for `"hello"`, and the
        route then called `.get` on it."""
        for body in ['"hello"', "[1,2,3]", "42", "null"]:
            resp = client.post("/stats/increment", data=body,
                               content_type="application/json")
            assert resp.status_code < 500, f"body {body!r} produced a server error"

    def test_malformed_json_does_not_crash(self, client):
        resp = client.post("/stats/increment", data="{not json",
                           content_type="application/json")
        assert resp.status_code < 500

    def test_no_body_at_all_does_not_crash(self, client):
        assert client.post("/stats/increment").status_code < 500

    def test_a_garbage_exact_bytes_still_lets_the_count_bump(self, client):
        """A malformed size must not cost the caller their count — documented
        behaviour, and the reason this one falls back instead of 400ing."""
        resp = client.post("/stats/increment",
                           json={"delta": 1, "exact_bytes": "huge"})
        assert resp.status_code == 200
        assert resp.get_json()["deleted"] == 1


# ── /search/text ──────────────────────────────────────────────────────────────

class TestSearchText:
    def test_a_bare_json_string_body_is_a_client_error(self, client):
        resp = client.post("/search/text", data='"hello"',
                           content_type="application/json")
        assert_client_error(resp, "/search/text")

    @pytest.mark.parametrize("body", [{}, {"query": ""}, {"query": "   "},
                                      {"query": None}])
    def test_an_empty_query_is_rejected(self, client, body):
        resp = client.post("/search/text", json=body)
        assert resp.status_code == 400

    def test_a_non_string_query_does_not_crash(self, client):
        """`.strip()` on an int used to raise straight out of the view."""
        for value in [5, [], {}, True]:
            resp = client.post("/search/text", json={"query": value})
            assert resp.status_code < 500, f"query={value!r} produced a server error"

    @pytest.mark.parametrize("n", ["x", [], {}, None])
    def test_a_non_numeric_n_is_a_client_error(self, client, n):
        assert_client_error(client.post("/search/text", json={"query": "cat", "n": n}),
                            "/search/text")

    def test_an_enormous_n_is_clamped_before_it_reaches_chroma(self, client):
        """Unbounded, this walks the whole index on a single request."""
        import server

        resp = client.post("/search/text", json={"query": "cat", "n": 100_000_000})
        assert resp.status_code == 200
        assert client.search_calls["text"][-1]["n"] <= server.MAX_RESULTS

    def test_a_negative_n_is_floored(self, client):
        resp = client.post("/search/text", json={"query": "cat", "n": -5})
        assert resp.status_code == 200
        assert client.search_calls["text"][-1]["n"] >= 1

    def test_a_normal_search_still_works(self, client):
        """The clamping must not break the common case."""
        resp = client.post("/search/text", json={"query": "golden hour sunset"})
        assert resp.status_code == 200
        assert client.search_calls["text"][-1]["query"] == "golden hour sunset"


# ── /search/image ─────────────────────────────────────────────────────────────

class TestSearchImage:
    def test_missing_image_data_is_rejected(self, client):
        assert client.post("/search/image", json={}).status_code == 400

    @pytest.mark.parametrize("payload", [
        "this is not base64!!!",
        "####",
        "YWJjZA",          # valid base64 chars, but not an image
    ])
    def test_undecodable_input_is_a_client_error_not_a_crash(self, client, payload):
        assert_client_error(client.post("/search/image", json={"image_b64": payload}),
                            "/search/image")

    def test_a_non_image_binary_is_a_client_error(self, client):
        payload = base64.b64encode(b"\x00\x01\x02 definitely not an image").decode()
        assert_client_error(client.post("/search/image", json={"image_b64": payload}),
                            "/search/image")

    def test_a_non_string_image_field_does_not_crash(self, client):
        for value in [123, [], {"a": 1}]:
            resp = client.post("/search/image", json={"image_b64": value})
            assert resp.status_code < 500

    def test_a_decompression_bomb_is_refused_rather_than_expanded(self, client):
        """A tiny payload that decodes to gigabytes of pixels. PIL raises
        DecompressionBombError; the route must turn that into a 400."""
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (1, 1)).save(buf, format="PNG")
        header = bytearray(buf.getvalue())
        # Rewrite the IHDR width/height to 60000 x 60000.
        header[16:24] = (60000).to_bytes(4, "big") + (60000).to_bytes(4, "big")

        resp = client.post("/search/image",
                           json={"image_b64": base64.b64encode(bytes(header)).decode()})
        assert_client_error(resp, "/search/image")


# ── query-string routes ───────────────────────────────────────────────────────

class TestQueryStringParams:
    @pytest.mark.parametrize("value", ["abc", "", "1.5", "1e999", "[]"])
    def test_a_non_numeric_thumbnail_size_is_a_client_error(self, client, value):
        assert_client_error(client.get(f"/thumbnail?path=/x.jpg&size={value}"),
                            "/thumbnail")

    @pytest.mark.parametrize("value", ["abc", "", "1.5"])
    def test_a_non_numeric_graph_view_n_is_a_client_error(self, client, value):
        assert_client_error(client.get(f"/api/graph-view?query=cat&n={value}"),
                            "/api/graph-view")

    def test_an_empty_graph_view_query_is_rejected(self, client):
        assert client.get("/api/graph-view?query=").status_code == 400


# ── motion-review routes ──────────────────────────────────────────────────────

class TestMotionReviewValidation:
    def test_a_missing_video_id_is_rejected(self, client, tmp_motion_db):
        for route in ["/motion-review/decision", "/motion-review/export",
                      "/motion-review/draft"]:
            assert client.post(route, json={}).status_code == 400, route

    @pytest.mark.parametrize("verdict", ["maybe", "", "APPROVE", 5, None, []])
    def test_an_unknown_verdict_is_a_client_error(self, client, tmp_motion_db,
                                                  tmp_path, verdict):
        source = tmp_path / "clip.mov"
        source.write_bytes(b"x" * 100)
        tmp_motion_db.proposal("vid1", source_path=str(source))

        resp = client.post("/motion-review/decision",
                           json={"video_id": "vid1", "verdict": verdict})
        assert_client_error(resp, "/motion-review/decision")

    @pytest.mark.parametrize("regions", [5, "cut", {"start": 1}, True])
    def test_a_non_list_regions_field_does_not_crash(self, client, tmp_motion_db,
                                                     tmp_path, regions):
        """`sanitize_regions` used to iterate whatever it was handed; a bare int
        raised TypeError out of the route."""
        source = tmp_path / "clip.mov"
        source.write_bytes(b"x" * 100)
        tmp_motion_db.proposal("vid1", source_path=str(source))

        resp = client.post("/motion-review/draft",
                           json={"video_id": "vid1", "regions": regions})
        assert resp.status_code < 500, f"regions={regions!r} produced a server error"

    def test_malformed_region_entries_are_ignored_rather_than_fatal(
        self, client, tmp_motion_db, tmp_path
    ):
        source = tmp_path / "clip.mov"
        source.write_bytes(b"x" * 100)
        tmp_motion_db.proposal("vid1", source_path=str(source))

        resp = client.post("/motion-review/draft", json={
            "video_id": "vid1",
            "regions": [None, "junk", 42, {"no": "bounds"},
                        {"type": "cut", "start": "a", "end": "b"}],
        })
        assert resp.status_code == 200
        assert resp.get_json()["regions"] == []

    def test_an_unknown_video_is_a_404(self, client, tmp_motion_db):
        resp = client.post("/motion-review/draft",
                           json={"video_id": "never-seen", "regions": []})
        assert resp.status_code == 404

    def test_a_missing_source_id_is_rejected(self, client, tmp_motion_db):
        assert client.get("/motion-review/source").status_code == 400


# ── the general property ──────────────────────────────────────────────────────

class TestNoRouteReturnsFiveHundred:
    """A sweep, so a newly added route inherits the rule without a bespoke test."""

    GET_ROUTES = ["/stats", "/motion-review/queue", "/motion-review/savings",
                  "/motion-review/export/status",
                  "/api/embed/status", "/filters/dismissed"]
    POST_ROUTES = ["/stats/increment", "/search/text", "/search/image",
                   "/motion-review/decision", "/motion-review/draft",
                   "/motion-review/export", "/motion-review/remove",
                   "/filters/dismiss", "/filters/restore"]

    JUNK_BODIES = [
        {}, {"unexpected": "field"}, {"video_id": None}, {"query": None},
        {"n": "x"}, {"delta": "x"}, {"regions": "not-a-list"},
    ]

    @pytest.mark.parametrize("route", GET_ROUTES)
    def test_get_routes_survive_junk_query_strings(self, client, tmp_motion_db, route):
        resp = client.get(f"{route}?n=abc&size=xyz&id=&path=&query=")
        assert resp.status_code < 500, f"{route} -> {resp.status_code}"

    @pytest.mark.parametrize("route", POST_ROUTES)
    def test_post_routes_survive_junk_bodies(self, client, tmp_motion_db, route):
        for body in self.JUNK_BODIES:
            resp = client.post(route, json=body)
            assert resp.status_code < 500, f"{route} with {body!r} -> {resp.status_code}"

    @pytest.mark.parametrize("route", POST_ROUTES)
    def test_post_routes_survive_a_non_object_body(self, client, tmp_motion_db, route):
        for raw in ['"str"', "[1,2]", "42", "null", "{bad json"]:
            resp = client.post(route, data=raw, content_type="application/json")
            assert resp.status_code < 500, f"{route} with {raw!r} -> {resp.status_code}"

    @pytest.mark.parametrize("route", POST_ROUTES)
    def test_post_routes_survive_no_body_and_no_content_type(self, client,
                                                             tmp_motion_db, route):
        resp = client.post(route)
        assert resp.status_code < 500, f"{route} -> {resp.status_code}"


# ── export concurrency guard, at the route level ──────────────────────────────
# export_job.py itself has its own unit-level suite (tests/test_export_job.py);
# these confirm the 409s actually reach an HTTP client through server.py, since
# that wiring (the `export_job.is_exporting(...)` guard added to /decision and
# /remove, plus /export's own kickoff-refusal branch) lives entirely in the
# route layer this file otherwise covers.

class TestExportConcurrencyGuard:
    def _block_an_export(self, tmp_motion_db, monkeypatch, video_id="vid1"):
        """Start a real background job whose export_to_photos is stubbed to
        block on an Event, and return that Event so the caller can release it.
        Blocks on `started` so the caller never races the guard against a job
        that hasn't actually begun yet."""
        import threading

        import export_job
        import motion_review

        tmp_motion_db.proposal(video_id)
        started = threading.Event()
        release = threading.Event()

        def fake_export(vid, regions=None, cut_segments=None, progress_cb=None):
            started.set()
            release.wait(timeout=5)
            return {"ok": True}

        monkeypatch.setattr(motion_review, "export_to_photos", fake_export)
        result = export_job.start_export(video_id)
        assert result["started"] is True
        assert started.wait(timeout=2), "the background job never started"
        return release

    def _finish(self, release):
        """Release the blocked job and wait for it to reach a terminal state,
        so it can never leak a live thread into a later test."""
        import export_job

        release.set()
        deadline = time.time() + 5
        while time.time() < deadline:
            if export_job.read_status().get("state") in ("done", "failed"):
                return
            time.sleep(0.02)

    def test_decision_is_refused_while_an_export_is_in_flight(
        self, client, tmp_motion_db, monkeypatch
    ):
        release = self._block_an_export(tmp_motion_db, monkeypatch)
        try:
            resp = client.post("/motion-review/decision",
                               json={"video_id": "vid1", "verdict": "approve"})
            assert resp.status_code == 409
        finally:
            self._finish(release)

    def test_remove_is_refused_while_an_export_is_in_flight(
        self, client, tmp_motion_db, monkeypatch
    ):
        release = self._block_an_export(tmp_motion_db, monkeypatch)
        try:
            resp = client.post("/motion-review/remove", json={"video_id": "vid1"})
            assert resp.status_code == 409
        finally:
            self._finish(release)

    def test_a_second_export_kickoff_is_refused_while_one_is_in_flight(
        self, client, tmp_motion_db, monkeypatch
    ):
        release = self._block_an_export(tmp_motion_db, monkeypatch)
        try:
            resp = client.post("/motion-review/export", json={"video_id": "vid1"})
            assert resp.status_code == 409
        finally:
            self._finish(release)

    def test_the_guard_is_scoped_to_the_video_actually_exporting(
        self, client, tmp_motion_db, monkeypatch
    ):
        """A DIFFERENT video's /decision must not be caught by another
        video's in-flight export — the guard checks video_id, not "is
        anything at all exporting"."""
        release = self._block_an_export(tmp_motion_db, monkeypatch, video_id="vid1")
        try:
            tmp_motion_db.proposal("vid2")
            resp = client.post("/motion-review/decision",
                               json={"video_id": "vid2", "verdict": "reject"})
            assert resp.status_code != 409
        finally:
            self._finish(release)
