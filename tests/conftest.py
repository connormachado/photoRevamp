"""
Shared test fixtures
====================
Each fixture here mocks exactly one *external boundary*. The rule for this suite:
tests exercise our logic, never the outside world. Nothing in `tests/` may run
real ffmpeg, talk to Photos.app, open the live ChromaDB, or write anywhere
outside `tmp_path`.

`backend/` is on sys.path via `pythonpath` in pytest.ini, so backend modules are
imported by their flat names (`import stats`) exactly as server.py sees them.

Import cost note: `utils.py` pulls in torch at module scope, and almost every
backend module imports `utils`. Only `edit_boundaries` and `stats` are cheap
(~0.03s); everything else costs ~2s the first time. That is why fixtures import
their target module lazily, inside the fixture body, rather than at the top of
this file — collecting a run that only touches edit_boundaries shouldn't pay for
torch.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ffmpeg_samples import (  # noqa: E402
    ALL_MALFORMED,
    IPHONE_MOV,
    NO_FPS_ONLY_TBR,
    SILENT_MP4,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Resolve the bundled ffmpeg binary once, here at collection time.
# `imageio_ffmpeg.get_ffmpeg_exe()` validates the binary by actually running
# `ffmpeg -version`, and export_video / video_motion / motion_review all call it
# at module scope — so importing any of them inside a test would trip the
# no-real-subprocess guard below. The lookup is lru_cached, so warming it before
# the guard exists costs one spawn for the whole session and never recurs.
import imageio_ffmpeg  # noqa: E402

imageio_ffmpeg.get_ffmpeg_exe()


# ── Filesystem safety net ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolate_stats(tmp_path, monkeypatch):
    """Point `stats.STATS_PATH` at tmp_path for EVERY test, no opt-in.

    This is autouse on purpose. `motion_review` imports `stats as stats_store`
    and `_apply_savings` mirrors its total into it, so any test that records a
    verdict would otherwise silently overwrite the real repo-root `stats.json` —
    i.e. corrupt the user's live delete counter. Making it impossible to forget
    is worth more than the negligible cost of the monkeypatch.
    """
    import stats

    path = tmp_path / "stats.json"
    monkeypatch.setattr(stats, "STATS_PATH", path)
    return path


@pytest.fixture(autouse=True)
def isolate_dismissed(tmp_path, monkeypatch):
    """Point `dismissed.DISMISSED_PATH` at tmp_path for EVERY test, no opt-in.

    Same rationale as `isolate_stats`: without this, any test that dismisses a
    photo would read/write the real repo's `photo_db/dismissed.json`. Also
    resets the module's in-memory cache before and after, since `dismissed.py`
    (unlike stats.py) caches in memory rather than re-reading on every call.
    """
    import dismissed

    path = tmp_path / "dismissed.json"
    monkeypatch.setattr(dismissed, "DISMISSED_PATH", path)
    dismissed.reload()
    yield path
    dismissed.reload()


@pytest.fixture(autouse=True)
def isolate_config_store(tmp_path, monkeypatch):
    """Point `config_store.CONFIG_PATH` at tmp_path for EVERY test, no opt-in.

    Same rationale as `isolate_stats`/`isolate_dismissed`: without this, a
    test calling `config_store.set(...)` would write into the real repo's
    `photo_db/config.json`. This fixture's protection window starts after
    collection, which is fine only because `config_store.load()`/`get()`
    never write — see config_store.py's module docstring.
    """
    import config_store

    path = tmp_path / "config.json"
    monkeypatch.setattr(config_store, "CONFIG_PATH", path)
    return path


@pytest.fixture
def seeded_stats(isolate_stats):
    """Write a stats.json with known contents and return its path.

    Takes a dict via `seeded_stats.write({...})` so tests can set up legacy or
    partial files for the migration paths.
    """
    class Seeder:
        path = isolate_stats

        def write(self, data: dict) -> Path:
            self.path.write_text(json.dumps(data))
            return self.path

    return Seeder()


# ── subprocess boundary (ffmpeg + osascript) ──────────────────────────────────

class RecordedCall:
    """One captured `subprocess.run` invocation."""

    def __init__(self, args, kwargs):
        self.args = args
        self.kwargs = kwargs

    @property
    def argv(self):
        """The command as passed. A list means no shell is involved."""
        return self.args[0] if self.args else self.kwargs.get("args")

    @property
    def is_argv_list(self) -> bool:
        return isinstance(self.argv, (list, tuple))

    @property
    def uses_shell(self) -> bool:
        return bool(self.kwargs.get("shell", False))

    @property
    def program(self) -> str:
        return str(self.argv[0]) if self.is_argv_list and self.argv else str(self.argv)

    def flag_value(self, flag: str):
        """Value following `flag` in the argv, or None. E.g. flag_value('-i')."""
        if not self.is_argv_list:
            return None
        argv = [str(a) for a in self.argv]
        for i, tok in enumerate(argv):
            if tok == flag and i + 1 < len(argv):
                return argv[i + 1]
        return None

    def __repr__(self):
        return f"<RecordedCall {self.argv!r} shell={self.uses_shell}>"


class FakeRun:
    """Stand-in for `subprocess.run` that records calls and replays canned output.

    Replicates the one behaviour tests actually depend on beyond recording:
    `check=True` plus a non-zero return code raises `CalledProcessError`, which
    is how `cleanup.reveal_in_photos` and friends detect AppleScript failure.
    """

    def __init__(self):
        self.calls: list[RecordedCall] = []
        self._default = {"stdout": "", "stderr": "", "returncode": 0}
        self._queue: list[dict] = []
        # Optional hook called with each RecordedCall. Needed for code that
        # checks its own output file afterwards — render_plan raises unless the
        # target exists and is non-empty, so a test must fake the file too, not
        # just the exit code.
        self.side_effect = None
        # Scriptable stdout lines for a faked `Popen(...).stdout` iteration —
        # see FakePopen / install_popen(). None (the default) means no
        # progress lines at all, which export_video's parser must tolerate.
        self.popen_stdout: list[str] | None = None

    def set_response(self, stdout="", stderr="", returncode=0):
        self._default = {"stdout": stdout, "stderr": stderr, "returncode": returncode}

    def queue_response(self, stdout="", stderr="", returncode=0):
        """Queue a response for the next call; falls back to the default once drained."""
        self._queue.append({"stdout": stdout, "stderr": stderr, "returncode": returncode})

    def __call__(self, *args, **kwargs):
        call = RecordedCall(args, kwargs)
        self.calls.append(call)
        if self.side_effect is not None:
            self.side_effect(call)
        resp = self._queue.pop(0) if self._queue else self._default

        stdout, stderr = resp["stdout"], resp["stderr"]
        if not kwargs.get("text", False) and not kwargs.get("universal_newlines", False):
            stdout = stdout.encode() if isinstance(stdout, str) else stdout
            stderr = stderr.encode() if isinstance(stderr, str) else stderr

        completed = subprocess.CompletedProcess(
            args=call.argv, returncode=resp["returncode"], stdout=stdout, stderr=stderr
        )
        if kwargs.get("check") and resp["returncode"] != 0:
            raise subprocess.CalledProcessError(
                resp["returncode"], call.argv, output=stdout, stderr=stderr
            )
        return completed

    # ── query helpers ────────────────────────────────────────────────────────
    @property
    def last(self) -> RecordedCall:
        assert self.calls, "no subprocess.run calls were recorded"
        return self.calls[-1]

    def calls_to(self, program_substr: str) -> list[RecordedCall]:
        return [c for c in self.calls if program_substr in c.program]

    @property
    def osascript_calls(self) -> list[RecordedCall]:
        return self.calls_to("osascript")

    @property
    def ffmpeg_calls(self) -> list[RecordedCall]:
        return self.calls_to("ffmpeg")

    def scripts(self) -> list[str]:
        """Every AppleScript source string handed to `osascript -e`."""
        return [s for c in self.osascript_calls if (s := c.flag_value("-e")) is not None]


class FakePopen:
    """Stand-in for `subprocess.Popen`, sharing FakeRun's call list and canned
    responses so `ffmpeg_calls`, `calls_to`, `flag_value` and the immutability
    suite's argv sweep all keep working against Popen calls exactly as they do
    against `.run()` calls — they all read the same `fake.calls` list.

    The one real call site in this codebase (`export_video._run_encode_with_
    progress`) pipes stdout (to read `-progress` lines) and hands stderr a
    REAL file handle, never `subprocess.PIPE` — so a genuine ffmpeg process
    writes its diagnostic text straight into that fd. This fake mirrors that:
    it writes the canned stderr into the handle synchronously (there is no
    concurrent child process to race), so code that reads the file back after
    the real process would have exited sees the same content either way.
    """

    def __init__(self, fake: "FakeRun", *args, **kwargs):
        self._fake = fake
        call = RecordedCall(args, kwargs)
        fake.calls.append(call)
        if fake.side_effect is not None:
            # Runs BEFORE this call "returns" to the caller, same moment a
            # test can block a background thread mid-render by having the
            # side effect wait on an Event — the call is already recorded by
            # this point, so a concurrent poll sees it.
            fake.side_effect(call)

        resp = fake._queue.pop(0) if fake._queue else fake._default
        self.returncode = resp["returncode"]
        self.pid = 424242

        lines = fake.popen_stdout if fake.popen_stdout is not None else []
        self.stdout = iter(list(lines))
        self.stderr = None  # this codebase never reads Popen.stderr directly

        stderr_target = kwargs.get("stderr")
        if stderr_target is not None and hasattr(stderr_target, "write"):
            stderr_target.write(resp["stderr"])
            stderr_target.flush()

    def wait(self, timeout=None):
        return self.returncode


class RealSubprocessBlocked(RuntimeError):
    """Raised when a test tries to spawn an actual process."""


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    """Make spawning a real process impossible unless a test opts in.

    Backend modules do `import subprocess` and call `subprocess.run(...)`, which
    resolves through the one shared stdlib module object — there is no per-module
    copy to patch. That cuts both ways: a single patch here covers every backend
    module at once, and conversely a test that forgets to mock would otherwise
    launch real ffmpeg against the user's library or pop open Photos.app.

    So the default is a loud failure. `fake_run` overrides this.
    """
    def blocked(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        raise RealSubprocessBlocked(
            f"test tried to spawn a real process: {cmd!r}\n"
            "Request the `fake_run` fixture and call fake_run.install()."
        )

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, blocked, raising=True)


@pytest.fixture
def fake_run(monkeypatch):
    """A FakeRun, plus `install()` to put it in front of `subprocess.run`.

    `install()` takes optional module names purely as documentation of what the
    test expects to intercept — the patch itself is process-wide, because every
    backend module shares the one stdlib `subprocess` module object.
    """
    fake = FakeRun()

    def install(*_modules_for_readability):
        monkeypatch.setattr(subprocess, "run", fake, raising=True)
        return fake

    def install_popen(*_modules_for_readability):
        """Opt-in, separate from `.install()`: patches `subprocess.Popen` too.

        Only the export-progress path uses Popen at all
        (`export_video._run_encode_with_progress`, reached when a caller
        passes `progress_cb`), so most tests never need this — `.install()`
        alone still covers every `subprocess.run` call, unchanged.
        """
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda *a, **kw: FakePopen(fake, *a, **kw), raising=True,
        )
        return fake

    fake.install = install
    fake.install_popen = install_popen
    return fake


@pytest.fixture
def ffmpeg_stderr():
    """Canned `ffmpeg -i` stderr blobs, keyed by scenario.

    `iphone_mov` is real captured output — see tests/ffmpeg_samples.py.
    """
    return {
        "iphone_mov": IPHONE_MOV,
        "silent_mp4": SILENT_MP4,
        "no_fps_only_tbr": NO_FPS_ONLY_TBR,
        **{f"malformed_{k}": v for k, v in ALL_MALFORMED.items()},
    }


# ── ChromaDB boundary ─────────────────────────────────────────────────────────

class FakeCollection:
    """Dict-backed stand-in for a Chroma collection.

    Mirrors the response *shapes* the real client returns, which differ between
    `get` (flat lists) and `query` (lists-of-lists, one inner list per query
    embedding) — code that unpacks them is sensitive to that difference.
    """

    def __init__(self, rows: dict[str, dict] | None = None):
        self.rows = dict(rows or {})
        self.deleted: list[str] = []

    def add_row(self, row_id: str, **metadata):
        self.rows[row_id] = metadata
        return row_id

    def count(self) -> int:
        return len(self.rows)

    def get(self, ids=None, include=None, **_):
        ids = [i for i in (ids or list(self.rows)) if i in self.rows]
        return {
            "ids": ids,
            "metadatas": [self.rows[i] for i in ids],
            "documents": [None] * len(ids),
        }

    def query(self, n_results=10, **_):
        ids = list(self.rows)[:n_results]
        return {
            "ids": [ids],
            "metadatas": [[self.rows[i] for i in ids]],
            "distances": [[0.1 * (n + 1) for n in range(len(ids))]],
            "documents": [[None] * len(ids)],
        }

    def delete(self, ids=None, **_):
        for i in ids or []:
            self.rows.pop(i, None)
            self.deleted.append(i)


@pytest.fixture
def fake_chroma():
    """An empty FakeCollection. Populate with `.add_row(id, path=..., ...)`."""
    return FakeCollection()


# ── motion_review state directory ─────────────────────────────────────────────

@pytest.fixture
def tmp_motion_db(tmp_path, monkeypatch):
    """Repoint every motion_review path constant into tmp_path.

    Returns an object exposing the redirected dirs plus a `proposal()` helper,
    because most motion_review entry points refuse to act on a video that has no
    proposal on disk.
    """
    import motion_review as mr

    root = tmp_path / "motion_review"
    dirs = {
        "MOTION_DIR": root,
        "PROPOSALS_DIR": root / "proposals",
        "REVIEWS_DIR": root / "reviews",
        "DRAFTS_DIR": root / "drafts",
        "PREVIEW_DIR": root / "preview",
    }
    for attr, path in dirs.items():
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(mr, attr, path)
    monkeypatch.setattr(mr, "DECISIONS_LOG", root / "decisions.jsonl")
    monkeypatch.setattr(mr, "SAVINGS_PATH", root / "savings.json")
    monkeypatch.setattr(mr, "TITLES_PATH", root / "titles.json")

    class MotionDB:
        module = mr
        root = dirs["MOTION_DIR"]
        proposals = dirs["PROPOSALS_DIR"]
        reviews = dirs["REVIEWS_DIR"]
        drafts = dirs["DRAFTS_DIR"]
        preview = dirs["PREVIEW_DIR"]
        savings = root / "savings.json"
        decisions = root / "decisions.jsonl"
        titles = root / "titles.json"

        def proposal(self, video_id="vid1", **overrides):
            """Write a minimal valid proposal and return it."""
            prop = {
                "video_id": video_id,
                "source_path": str(tmp_path / f"{video_id}.mov"),
                "original_duration": 60.0,
                "trimmed_duration": 40.0,
                "cut_segments": [{"start": 40.0, "end": 60.0}],
                "regions": [],
            }
            prop.update(overrides)
            (self.proposals / f"{video_id}.json").write_text(json.dumps(prop))
            return prop

    return MotionDB()


# ── Flask boundary ────────────────────────────────────────────────────────────

@pytest.fixture
def client(fake_chroma, monkeypatch):
    """Flask test client with the CLIP model and Chroma collection stubbed.

    Importing `server` is safe — it builds the app and registers routes, but the
    350MB CLIP load and the Chroma connection happen in `load_everything()`,
    which only runs under `__main__`.

    PROPAGATE_EXCEPTIONS is forced off so an unhandled error surfaces as a real
    500 *response* rather than raising into the test. The input-validation suite
    asserts on status codes, and would be untestable if exceptions escaped.
    """
    import search
    import server

    monkeypatch.setattr(server, "collection", fake_chroma)
    monkeypatch.setattr(server, "model", object())
    monkeypatch.setattr(server, "tokenizer", object())
    monkeypatch.setattr(server, "preprocess", object())
    monkeypatch.setattr(server, "device", "cpu")

    # The CLIP model itself is out of scope for route tests, and the stubs above
    # are not callable — so the search functions are replaced with recorders.
    # This is also what lets a test assert on the arguments a route passed down
    # (e.g. that `n` was clamped before reaching Chroma).
    calls = {"text": [], "image": []}

    def fake_search_text(query, n, *args, **kwargs):
        calls["text"].append({"query": query, "n": n})
        return []

    def fake_search_image(img, n, *args, **kwargs):
        calls["image"].append({"n": n})
        return []

    monkeypatch.setattr(search, "search_text", fake_search_text)
    monkeypatch.setattr(search, "search_image", fake_search_image)

    server.app.config["TESTING"] = False
    server.app.config["PROPAGATE_EXCEPTIONS"] = False
    with server.app.test_client() as c:
        c.chroma = fake_chroma
        c.server = server
        c.search_calls = calls
        yield c
