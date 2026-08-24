"""
Video upload → Climb Cutter queue
=================================
`video_upload` is the only HTTP ingest path into Climb Cutter, and the only
place a browser-supplied *filename* becomes a real path on disk. Three contracts
carry the weight, and these tests assert those rather than the arithmetic:

* **Content, not name, identifies a clip** (module docstring + `backend/CLAUDE.md`:
  "An upload's path IS its identity… The content hash (not the filename) is the
  dedupe key"). Re-picking the same footage must land on the same path → same
  `video_id` → reuse of the existing proposal, never a second few-hundred-MB copy.
* **The extension allowlist is the real gate** — `<input accept="video/*">` is a
  hint the picker can ignore — and it must gate *before* anything touches disk.
* **`save_and_process` never raises**: the route maps a whole selection through
  it, so one bad file must not sink the rest.

Slow: `video_upload` imports `utils` (torch) transitively via export_video /
video_motion.
"""

import hashlib
import json
import os
from io import BytesIO
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

pytestmark = pytest.mark.slow


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def uploads(tmp_path, monkeypatch):
    """Repoint the upload path constants into tmp_path and stub the two heavy
    boundaries (`video_motion.process_video`, `export_video.read_source_metadata`).

    The `process_video` stub writes a proposal file the way the real one does,
    because the "already queued" branch is keyed on that file existing — a stub
    that skipped it would make the dedupe tests vacuous.

    Neither `uploads/` nor `uploads/.incoming/` is pre-created: tests that assert
    "a rejected file never touched disk" need those directories to be absent
    unless the module itself made them.
    """
    import video_upload as vu
    from utils import file_id

    root = tmp_path / "motion_review"
    uploads_dir = root / "uploads"
    incoming_dir = uploads_dir / ".incoming"
    proposals_dir = root / "proposals"
    proposals_dir.mkdir(parents=True)

    monkeypatch.setattr(vu, "MOTION_DIR", root)
    monkeypatch.setattr(vu, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(vu, "INCOMING_DIR", incoming_dir)
    monkeypatch.setattr(vu, "PROPOSALS_DIR", proposals_dir)

    class Env:
        module = vu
        motion_root = root
        uploads = uploads_dir
        incoming = incoming_dir
        proposals = proposals_dir
        real_read_source_metadata = staticmethod(vu.export_video.read_source_metadata)

        def __init__(self):
            self.analysed = []          # (path_arg, config) per process_video call
            self.owned_flags = []       # the `owned` each call declared
            self.metadata = {"date": None, "date_utc": None, "gps": None,
                             "rotation": None}
            self.analysis_error = None

        def stored_files(self):
            """Every parked upload — the hash dirs only, never the staging dir."""
            if not self.uploads.exists():
                return []
            return sorted(
                p for p in self.uploads.rglob("*")
                if p.is_file() and self.incoming not in p.parents
            )

        def staged_files(self):
            if not self.incoming.exists():
                return []
            return sorted(p for p in self.incoming.iterdir() if p.is_file())

    env = Env()

    def fake_process_video(video_arg, config, owned=False):
        env.analysed.append((video_arg, config))
        env.owned_flags.append(owned)
        if env.analysis_error is not None:
            raise env.analysis_error
        video_id = file_id(Path(video_arg))
        prop = {"video_id": video_id, "source_path": str(Path(video_arg).resolve())}
        (proposals_dir / f"{video_id}.json").write_text(json.dumps(prop))
        return prop

    monkeypatch.setattr(vu.video_motion, "process_video", fake_process_video)
    monkeypatch.setattr(vu.video_motion, "load_config", lambda: {"stub_config": True})
    monkeypatch.setattr(vu.export_video, "read_source_metadata",
                        lambda path: dict(env.metadata))
    return env


def pick(filename, data=b"\x00fake video bytes\x00"):
    """One file as the browser hands it over: a werkzeug FileStorage."""
    return FileStorage(stream=BytesIO(data), filename=filename)


class ExplodingUpload:
    """A FileStorage whose .save() fails — a full disk, a vanished temp file."""

    def __init__(self, filename, exc):
        self.filename = filename
        self._exc = exc

    def save(self, dst):
        raise self._exc


# ── _content_hash ─────────────────────────────────────────────────────────────

class TestContentHash:
    """The dedupe key. It must depend on the BYTES and nothing else."""

    def test_identical_bytes_at_different_paths_hash_the_same(self, tmp_path):
        a, b = tmp_path / "a.mov", tmp_path / "sub/b.MOV"
        b.parent.mkdir()
        a.write_bytes(b"same footage")
        b.write_bytes(b"same footage")
        import video_upload as vu

        assert vu._content_hash(a) == vu._content_hash(b)

    def test_rewriting_a_file_in_place_changes_its_hash(self, tmp_path):
        """The other half of "content, not path": same path, new bytes, new key.
        Stated in the docstring — deliberately not `utils.file_id`, which hashes
        the path and so could not tell these two apart."""
        import video_upload as vu

        f = tmp_path / "clip.mov"
        f.write_bytes(b"take one")
        first = vu._content_hash(f)
        f.write_bytes(b"take two")

        assert vu._content_hash(f) != first

    def test_differing_bytes_hash_differently(self, tmp_path):
        import video_upload as vu

        a, b = tmp_path / "a.mov", tmp_path / "b.mov"
        a.write_bytes(b"footage A")
        b.write_bytes(b"footage B")
        assert vu._content_hash(a) != vu._content_hash(b)

    @pytest.mark.parametrize("size", [0, 1, 1 << 20, (1 << 20) + 1, (1 << 21) + 7])
    def test_chunked_reading_matches_a_whole_file_digest(self, tmp_path, size):
        """Sizes straddle HASH_CHUNK: the 1 MiB loop must not change the digest,
        and must not stop after the first chunk."""
        import video_upload as vu

        data = bytes((i * 37 + 11) % 256 for i in range(size))
        f = tmp_path / "clip.mov"
        f.write_bytes(data)
        expected = hashlib.md5(data).hexdigest()[: vu.HASH_PREFIX_LEN]
        assert vu._content_hash(f) == expected

    def test_a_change_beyond_the_first_chunk_is_noticed(self, tmp_path):
        """Guards against hashing only a prefix — two multi-GB climbs share their
        first megabyte far more often than their last."""
        import video_upload as vu

        base = bytearray(b"\x00" * ((1 << 20) + 4096))
        a, b = tmp_path / "a.mov", tmp_path / "b.mov"
        a.write_bytes(bytes(base))
        base[-1] = 0xFF
        b.write_bytes(bytes(base))
        assert vu._content_hash(a) != vu._content_hash(b)

    def test_the_hash_is_a_bare_token_fit_for_a_directory_name(self, tmp_path):
        import video_upload as vu

        f = tmp_path / "clip.mov"
        f.write_bytes(b"\xff\xfe/../..\x00 weird bytes")
        h = vu._content_hash(f)
        assert len(h) == vu.HASH_PREFIX_LEN
        assert all(c in "0123456789abcdef" for c in h)
        assert Path(h).name == h        # no separator can appear in it

    def test_staging_dir_can_never_be_mistaken_for_a_hash_dir(self):
        """The `.incoming` comment says it is hidden from the hash dirs. It is
        also inside UPLOADS_DIR on purpose, so `os.replace` stays same-filesystem
        (i.e. atomic)."""
        import video_upload as vu

        assert vu.INCOMING_DIR.parent == vu.UPLOADS_DIR
        assert vu.INCOMING_DIR.name.startswith(".")   # never valid hex


# ── _safe_name ────────────────────────────────────────────────────────────────

HOSTILE_NAMES = [
    "../../../etc/passwd.mov",
    "/etc/passwd.mov",
    "..\\..\\windows\\evil.mov",
    "%2e%2e%2fescape.mov",
    "....//....//up.mov",
    "clip\x00.mov",
    "..",
    ".",
    "sub/dir/clip.mov",
]


class TestSafeName:
    """The browser filename is attacker-controlled; the result is a real path."""

    def test_an_ordinary_name_survives_intact(self):
        import video_upload as vu

        assert vu._safe_name("IMG_1234.MOV") == "IMG_1234.MOV"

    @pytest.mark.parametrize("hostile", HOSTILE_NAMES)
    def test_the_result_is_always_a_single_path_component(self, hostile):
        import video_upload as vu

        name = vu._safe_name(hostile)
        assert name, "an empty name would make the destination a directory"
        assert Path(name).name == name
        assert os.sep not in name and "/" not in name and "\\" not in name
        assert ".." not in name
        assert "\x00" not in name

    @pytest.mark.parametrize("hostile", HOSTILE_NAMES)
    def test_joining_the_result_onto_a_root_cannot_escape_it(self, tmp_path, hostile):
        """The property that actually matters — containment of the destination."""
        import video_upload as vu

        dest = (tmp_path / vu._safe_name(hostile)).resolve()
        assert dest.parent == tmp_path.resolve()

    @pytest.mark.parametrize("blank", ["", None, "日本語", "😀😀😀", "..", "._ "])
    def test_an_unusable_name_falls_back_to_a_usable_video_name(self, blank):
        """Documented in the docstring: an empty result would make the
        destination the hash DIRECTORY rather than a file inside it."""
        import video_upload as vu

        name = vu._safe_name(blank)
        assert Path(name).stem
        assert Path(name).suffix in vu.VIDEO_EXTS

    @pytest.mark.parametrize("name", ["日本語.mp4", "😀😀😀.mov"])
    def test_a_non_ascii_stem_still_keeps_its_extension(self, name):
        import video_upload as vu

        assert Path(vu._safe_name(name)).suffix == Path(name).suffix


# Names that are over the byte limit by one route or another. `secure_filename`
# NFKD-normalises before its ASCII strip, so which multi-byte input survives is
# not obvious: "é" and fullwidth "Ａ" decompose to real ASCII letters and leave a
# 300-character stem that then has to be byte-capped, while Japanese and emoji
# strip to nothing and fall through to the placeholder instead. Both routes have
# to land inside NAME_MAX.
OVERLONG_NAMES = [
    "a" * 300 + ".mov",                 # plain ASCII
    "é" * 300 + ".mp4",                 # 600 source bytes → 300 ASCII "e"s
    "Ａ" * 300 + ".mkv",                 # 900 source bytes → 300 ASCII "A"s
    "あ" * 200 + ".mp4",                 # 600 source bytes → nothing
    "😀" * 100 + ".mov",                 # 400 source bytes → nothing
    "../" * 100 + "b" * 300 + ".m4v",   # traversing AND overlong at once
]

# The module's allowlist, copied so parametrize can run at collection time without
# paying video_upload's torch import. Kept honest by
# TestSafeNameExtensionSurvival::test_the_copied_extension_list_matches_the_module.
ALLOWED_EXTS = [".avi", ".m4v", ".mkv", ".mov", ".mp4"]


class TestSafeNameLength:
    """APFS's NAME_MAX is 255 *bytes* per path component, and this is the only
    place a browser-supplied name becomes one. A name over the limit is a failed
    upload (OSError out of `os.replace`), not a cosmetic problem — so the cap is
    the contract, and it is counted in bytes rather than characters."""

    @pytest.mark.parametrize("name", OVERLONG_NAMES)
    def test_no_name_can_exceed_the_filesystem_limit(self, name):
        import video_upload as vu

        assert len(vu._safe_name(name).encode("utf-8")) <= vu.APFS_NAME_MAX

    @pytest.mark.parametrize("name", ["あ" * 200 + ".mp4", "é" * 300 + ".mp4",
                                      "Ａ" * 300 + ".mp4", "😀" * 100 + ".mp4"])
    def test_a_long_multibyte_name_is_capped_in_bytes_and_keeps_its_extension(self, name):
        """Character count is not byte count: 200 "あ" is 600 bytes on disk. Whether
        the stem transliterates into ASCII or vanishes entirely, the result has to
        fit the limit and still be a `.mp4` file."""
        import video_upload as vu

        result = vu._safe_name(name)
        assert len(result.encode("utf-8")) <= vu.APFS_NAME_MAX
        assert Path(result).suffix == ".mp4"
        assert Path(result).stem, "a bare extension would be a hidden, stem-less file"

    def test_a_long_ascii_name_is_truncated_rather_than_given_up_on(self):
        """The cap must reclaim bytes from the stem, not fall back to the
        placeholder — the original basename is the queue's `source_name`, so an
        over-long pick should still be recognisable to the user."""
        import video_upload as vu

        result = vu._safe_name("a" * 300 + ".mov")
        stem = Path(result).stem

        assert len(result.encode("utf-8")) <= vu.APFS_NAME_MAX
        assert Path(result).suffix == ".mov"
        assert set(stem) == {"a"}, "the kept stem must be a prefix of the original"
        assert len(stem) > 200, f"truncated to {len(stem)} chars — that is a stub, not a name"

    @pytest.mark.parametrize("over_budget_by", [-1, 0, 1])
    def test_the_cap_only_bites_on_a_stem_over_the_budget(self, over_budget_by):
        """Off-by-one around the exact limit. The budget is defined by the module's
        own constant, so this cannot drift from the implementation the way a
        hardcoded 251 would."""
        import video_upload as vu

        budget = vu.APFS_NAME_MAX - len(".mov".encode("utf-8"))
        stem = "a" * (budget + over_budget_by)

        result = vu._safe_name(stem + ".mov")

        assert Path(result).suffix == ".mov"
        if over_budget_by <= 0:
            assert result == stem + ".mov", "a stem that fits must not be shortened"
        else:
            assert len(Path(result).stem.encode("utf-8")) == budget

    @pytest.mark.parametrize("name", HOSTILE_NAMES + OVERLONG_NAMES + [
        "clip.mov", "IMG_1234.MOV", "clip.movie", "noextension", "clip.", "",
    ])
    def test_every_result_is_one_usable_component_inside_the_limit(self, name):
        """The union of the two properties this function exists for, swept over
        everything: whatever comes in, what comes out is a single, non-empty,
        video-extensioned path component the filesystem will accept."""
        import video_upload as vu

        result = vu._safe_name(name)
        assert Path(result).name == result
        assert len(result.encode("utf-8")) <= vu.APFS_NAME_MAX
        assert Path(result).stem
        assert Path(result).suffix.lower() in vu.VIDEO_EXTS


class TestSafeNameExtensionSurvival:
    """The extension must survive every route through the function. It is derived
    from the ORIGINAL name and reattached after sanitisation precisely because
    sanitising "stem.ext" in one pass let a stem that transliterates to nothing
    take the separating dot with it, leaving a bare "mp4" — no dot, no stem, and a
    file ffmpeg would then have to demux by content."""

    def test_the_copied_extension_list_matches_the_module(self):
        import video_upload as vu

        assert set(ALLOWED_EXTS) == vu.VIDEO_EXTS

    @pytest.mark.parametrize("ext", ALLOWED_EXTS)
    def test_a_recognised_extension_survives_a_normal_stem_verbatim(self, ext):
        import video_upload as vu

        assert vu._safe_name(f"IMG_1234{ext}") == f"IMG_1234{ext}"

    @pytest.mark.parametrize("name", ["IMG_1234.MOV", "clip.Mp4", "clip.MKV"])
    def test_a_recognised_extension_keeps_its_original_case(self, name):
        """The allowlist is matched case-insensitively (the iPhone hands over
        `.MOV`), so the case the user gave is carried through rather than
        normalised — the parked name is what the queue shows as `source_name`."""
        import video_upload as vu

        assert Path(vu._safe_name(name)).suffix == Path(name).suffix

    @pytest.mark.parametrize("name", ["日本語.mp4", "😀😀.mkv", "...mkv", "  .avi",
                                      "___.m4v", "🏔🏔.m4v"])
    def test_a_stem_that_sanitises_away_keeps_the_extension_and_gains_a_stem(self, name):
        """The regression that motivated the rewrite: "日本語.mp4" must not become the
        stem-less "mp4"."""
        import video_upload as vu

        result = vu._safe_name(name)
        ext = Path(name).suffix

        assert Path(result).suffix == ext
        assert Path(result).stem
        assert result != ext.lstrip("."), "the extension was swallowed as the stem"
        assert result == f"video{ext}"      # the module's placeholder, spelled out

    @pytest.mark.parametrize("name", ["clip.movie", "clip", "clip.", "clip.txt",
                                      "clip.mov.zip", "noextension", "", None])
    def test_an_unrecognised_or_missing_extension_falls_back_to_mov(self, name):
        """`save_and_process`'s allowlist rejects these before they ever get here,
        so this is `_safe_name`'s own belt and braces for its other caller
        (`_settle_path`) — the destination must still be a video-named file."""
        import video_upload as vu

        result = vu._safe_name(name)
        assert Path(result).suffix == ".mov"
        assert Path(result).stem

    @pytest.mark.parametrize("ext", ALLOWED_EXTS)
    def test_truncation_never_eats_into_the_extension(self, ext):
        """Documented: the cap truncates the stem only. Trimming the tail instead
        would strip the extension off exactly the longest names."""
        import video_upload as vu

        result = vu._safe_name("a" * 400 + ext)

        assert Path(result).suffix == ext
        assert len(result.encode("utf-8")) <= vu.APFS_NAME_MAX

    @pytest.mark.parametrize("name", ["IMG_1234.MOV", "clip.mp4", "clip.mKv",
                                      "../../etc/passwd.mov", "a" * 300 + ".avi"])
    def test_anything_the_upload_gate_admits_keeps_a_video_extension(self, name):
        """Coherence between the two halves: `save_and_process` gates on
        `suffix.lower() in VIDEO_EXTS`, so any name it admits must come out of
        `_safe_name` still carrying an extension from the same allowlist. (Every
        name here is ordinary ASCII case variation — "clip.mKv" is a plain
        capital K, not the confusable below, which gets its own test.)"""
        import video_upload as vu

        assert Path(name).suffix.lower() in vu.VIDEO_EXTS, "not a gate-admitted name"
        assert Path(vu._safe_name(name)).suffix.lower() in vu.VIDEO_EXTS

    def test_a_non_ascii_confusable_extension_is_canonicalised_not_preserved(self):
        """`str.lower()` folds some non-ASCII codepoints onto plain ASCII letters
        — U+212A KELVIN SIGN -> "k" is one — so a name can pass the
        `suffix.lower() in VIDEO_EXTS` gate while its extension is NOT actually
        ASCII. Preserving the original characters here would park the file under
        an extension that is a different codepoint from every real ".mkv", which
        nothing else in this app would ever match against. The gate matched via
        the lowercased form, so that lowercased form — not the confusable
        original — is what must reach the filesystem."""
        import video_upload as vu

        kelvin_ext_name = "clip.mKv"      # real KELVIN SIGN, not ASCII "K"
        assert kelvin_ext_name.lower().endswith(".mkv")
        assert not kelvin_ext_name.isascii()

        result = vu._safe_name(kelvin_ext_name)

        assert result.isascii(), "a confusable character leaked into the stored name"
        assert Path(result).suffix == ".mkv"


class TestSafeNameStability:
    """Same name in, same name out — and the same name twice is not this
    function's problem to solve."""

    @pytest.mark.parametrize("name", HOSTILE_NAMES + OVERLONG_NAMES +
                             ["IMG_1234.MOV", "日本語.mp4"])
    def test_re_sanitising_an_already_safe_name_is_a_no_op(self, name):
        """Reachable: the user re-picks the parked copy out of `uploads/`, so the
        browser hands back a name this function itself produced. It must not drift
        a little further on each pass."""
        import video_upload as vu

        once = vu._safe_name(name)
        assert vu._safe_name(once) == once

    @pytest.mark.parametrize("name", ["IMG_1234.MOV", "a" * 300 + ".mov", "日本語.mp4"])
    def test_the_same_input_always_produces_the_same_output(self, name):
        """No randomness, no counter, no uuid: the parked path IS the video's
        identity (md5-of-absolute-path), so a name that varied between calls would
        make a re-pick a different video."""
        import video_upload as vu

        assert vu._safe_name(name) == vu._safe_name(name)

    def test_two_overlong_names_truncating_alike_are_not_disambiguated_here(self):
        """EXPECTED, not a bug: real collision avoidance happens one layer up, in
        `_settle_path`, which keys the destination DIRECTORY by content hash — two
        different clips can never share a directory whatever they are called, and
        two identical clips are meant to share a path. `_safe_name` therefore has
        no disambiguating to do, and this test exists so a later reader does not
        mistake it for the thing that keeps names unique."""
        import video_upload as vu

        assert vu._safe_name("a" * 300 + ".mov") == \
            vu._safe_name("a" * 400 + "_shot_two.mov")

    def test_two_overlong_names_that_truncate_alike_still_both_queue(self, uploads):
        """That claim, proven through the caller rather than left as prose: two
        different clips whose names truncate to the SAME basename must still park
        as two files, two video_ids and two queue rows, neither overwriting the
        other. This is the property that makes `_safe_name`'s lack of
        disambiguation safe, and it is what would break first if the hash
        directory ever stopped being content-derived."""
        vu = uploads.module
        name_a, name_b = "a" * 300 + "_take_one.mov", "a" * 300 + "_take_two.mov"
        assert vu._safe_name(name_a) == vu._safe_name(name_b), "not a collision case"

        first = vu.save_and_process(pick(name_a, b"climb one"))
        second = vu.save_and_process(pick(name_b, b"climb two"))

        assert [first["status"], second["status"]] == ["queued", "queued"]
        assert first["video_id"] != second["video_id"]
        stored = uploads.stored_files()
        assert len(stored) == 2
        assert {p.name for p in stored} == {vu._safe_name(name_a)}   # same basename…
        assert stored[0].parent != stored[1].parent                  # …different dirs
        assert {p.read_bytes() for p in stored} == {b"climb one", b"climb two"}


# ── _settle_path ──────────────────────────────────────────────────────────────

class TestSettlePath:
    """`uploads/<content-hash>/<name>` — content-derived, and final before analysis."""

    def _stage(self, env, data=b"footage", name="staged.tmp"):
        env.incoming.mkdir(parents=True, exist_ok=True)
        staged = env.incoming / name
        staged.write_bytes(data)
        return staged

    def test_the_file_lands_under_uploads_with_its_bytes_intact(self, uploads):
        vu = uploads.module
        staged = self._stage(uploads, b"real footage")

        dest = vu._settle_path(staged, "IMG_0001.MOV")

        assert dest.is_absolute()          # so it can never be read as an ffmpeg flag
        assert dest.resolve().is_relative_to(uploads.uploads.resolve())
        assert dest.read_bytes() == b"real footage"
        assert dest.name == "IMG_0001.MOV"

    def test_the_directory_is_named_for_the_content(self, uploads):
        vu = uploads.module
        staged = self._stage(uploads, b"real footage")
        expected = hashlib.md5(b"real footage").hexdigest()[: vu.HASH_PREFIX_LEN]

        dest = vu._settle_path(staged, "clip.mov")

        assert dest.parent.name == expected

    @pytest.mark.parametrize("hostile", HOSTILE_NAMES)
    def test_a_hostile_filename_cannot_escape_the_uploads_tree(self, uploads, hostile):
        vu = uploads.module
        staged = self._stage(uploads, b"payload")

        dest = vu._settle_path(staged, hostile)

        assert dest.resolve().parent.parent == uploads.uploads.resolve()
        assert uploads.stored_files() == [dest]

    def test_the_staged_copy_is_gone_afterwards(self, uploads):
        vu = uploads.module
        staged = self._stage(uploads, b"footage")

        vu._settle_path(staged, "clip.mov")

        assert not staged.exists()

    def test_re_settling_the_same_content_reuses_the_first_copy(self, uploads):
        """The documented rule: same bytes under a different name is the same clip,
        so the second copy is thrown away rather than parked beside it."""
        vu = uploads.module
        first = vu._settle_path(self._stage(uploads, b"same"), "IMG_0001.MOV")
        second = vu._settle_path(self._stage(uploads, b"same"), "renamed_later.mp4")

        assert second == first
        assert len(uploads.stored_files()) == 1

    def test_the_discarded_duplicate_leaves_nothing_staged(self, uploads):
        vu = uploads.module
        vu._settle_path(self._stage(uploads, b"same"), "a.mov")
        vu._settle_path(self._stage(uploads, b"same", name="b.tmp"), "b.mov")

        assert uploads.staged_files() == []

    def test_settling_is_idempotent_in_the_path_it_returns(self, uploads):
        vu = uploads.module
        paths = {
            vu._settle_path(self._stage(uploads, b"same", name=f"s{i}.tmp"), "x.mov")
            for i in range(3)
        }
        assert len(paths) == 1

    def test_different_content_under_one_name_gets_separate_homes(self, uploads):
        vu = uploads.module
        a = vu._settle_path(self._stage(uploads, b"climb one"), "IMG_0001.MOV")
        b = vu._settle_path(self._stage(uploads, b"climb two"), "IMG_0001.MOV")

        assert a.parent != b.parent
        assert len(uploads.stored_files()) == 2

    def test_a_one_byte_difference_is_a_different_clip(self, uploads):
        vu = uploads.module
        a = vu._settle_path(self._stage(uploads, b"footage\x00"), "a.mov")
        b = vu._settle_path(self._stage(uploads, b"footage\x01"), "a.mov")

        assert a != b

    def test_an_occupied_directory_resolves_to_one_stable_choice(self, uploads):
        """Two files can share a hash dir (a concurrent double-pick under different
        names). Whichever is chosen, the choice must not flip between calls — the
        path is the video's identity."""
        vu = uploads.module
        first = vu._settle_path(self._stage(uploads, b"same"), "b_second.mov")
        (first.parent / "a_first.mov").write_bytes(b"same")

        again = vu._settle_path(self._stage(uploads, b"same"), "c_third.mov")
        once_more = vu._settle_path(self._stage(uploads, b"same"), "d_fourth.mov")

        assert again == once_more


# ── save_and_process: the extension gate ──────────────────────────────────────

class TestExtensionAllowlist:
    """`accept="video/*"` is only a hint — this is the real gate."""

    @pytest.mark.parametrize("ext", sorted({".mov", ".mp4", ".m4v", ".avi", ".mkv"}))
    def test_every_allowed_extension_is_accepted(self, uploads, ext):
        result = uploads.module.save_and_process(pick(f"clip{ext}"))
        assert result["status"] == "queued", result["error"]

    @pytest.mark.parametrize("name", ["IMG_1234.MOV", "clip.MP4", "clip.MkV"])
    def test_the_check_is_case_insensitive(self, uploads, name):
        """iPhone hands over `.MOV`; rejecting it would break the main use case."""
        assert uploads.module.save_and_process(pick(name))["status"] == "queued"

    @pytest.mark.parametrize("name", [
        "notes.txt", "payload.exe", "script.sh", "photo.jpg", "clip.movie",
        "clip.mov.txt", "archive.mov.zip", "noextension", "", ".mov.",
    ])
    def test_a_non_video_is_refused(self, uploads, name):
        result = uploads.module.save_and_process(pick(name))
        assert result["status"] == "error"
        assert result["video_id"] is None
        assert result["error"]

    def test_a_missing_filename_is_refused_rather_than_crashing(self, uploads):
        result = uploads.module.save_and_process(pick(None))
        assert result["status"] == "error"

    def test_a_refused_upload_never_reaches_the_disk(self, uploads):
        """The gate is before the write — a status-only assertion would pass even
        if the bytes had already been parked."""
        uploads.module.save_and_process(pick("payload.exe", b"MZ\x90\x00"))

        assert uploads.stored_files() == []
        assert not uploads.incoming.exists()

    def test_a_refused_upload_is_never_analysed(self, uploads):
        uploads.module.save_and_process(pick("payload.exe"))
        assert uploads.analysed == []

    def test_the_error_names_the_offending_extension_and_the_allowed_set(self, uploads):
        result = uploads.module.save_and_process(pick("payload.exe"))
        assert ".exe" in result["error"]
        for ext in uploads.module.VIDEO_EXTS:
            assert ext in result["error"]

    @pytest.mark.parametrize("hostile,escapes_to", [
        ("../pwned.mov", "motion_root"),      # uploads/../pwned.mov
        ("../../pwned.mov", "tmp_root"),      # uploads/../../pwned.mov
    ])
    def test_a_hostile_name_with_a_video_extension_cannot_write_outside_uploads(
        self, uploads, tmp_path, hostile, escapes_to
    ):
        """Passing the extension gate is not the same as being trusted. Asserted on
        the filesystem: a "queued" status would not by itself prove containment."""
        result = uploads.module.save_and_process(pick(hostile, b"payload"))

        assert result["status"] == "queued"
        [stored] = uploads.stored_files()
        assert stored.resolve().is_relative_to(uploads.uploads.resolve())
        outside = {"motion_root": uploads.motion_root, "tmp_root": tmp_path}[escapes_to]
        assert not (outside / "pwned.mov").exists()


# ── save_and_process: the happy path ──────────────────────────────────────────

class TestQueueing:
    def test_a_good_upload_is_parked_analysed_and_reported_as_queued(self, uploads):
        result = uploads.module.save_and_process(pick("IMG_0001.MOV", b"climb bytes"))

        assert result["status"] == "queued"
        assert result["error"] is None
        assert result["filename"] == "IMG_0001.MOV"
        [stored] = uploads.stored_files()
        assert stored.read_bytes() == b"climb bytes"
        assert len(uploads.analysed) == 1

    def test_the_reported_video_id_is_the_one_analysis_wrote_a_proposal_under(
        self, uploads
    ):
        """`video_id` is md5-of-absolute-path, computed independently here and in
        video_motion. If the two ever disagreed the UI would poll a queue row that
        does not exist."""
        result = uploads.module.save_and_process(pick("IMG_0001.MOV"))

        assert (uploads.proposals / f"{result['video_id']}.json").exists()

    def test_analysis_is_handed_the_settled_path_and_the_loaded_config(self, uploads):
        """The path must be final BEFORE analysis: video_id is md5-of-path, so
        analysing a staged path then moving the file would orphan the proposal."""
        uploads.module.save_and_process(pick("IMG_0001.MOV"))

        [(path_arg, config)] = uploads.analysed
        [stored] = uploads.stored_files()
        assert Path(path_arg).resolve() == stored.resolve()
        assert config == {"stub_config": True}

    def test_an_upload_is_declared_as_a_copy_the_app_owns(self, uploads):
        """This route is the ONLY thing that may set owned=True — the flag is
        what later lets queue_removal delete the file to reclaim disk. A
        proposal that merely references a file the user already had must not
        inherit it."""
        uploads.module.save_and_process(pick("IMG_0001.MOV"))

        assert uploads.owned_flags == [True]

    def test_the_staging_file_does_not_survive_a_success(self, uploads):
        uploads.module.save_and_process(pick("IMG_0001.MOV"))
        assert uploads.staged_files() == []

    def test_metadata_presence_is_surfaced_not_enforced(self, uploads):
        """A clip whose QuickTime tags were stripped must still queue — the flags
        exist so the UI can warn, not to reject the upload."""
        uploads.metadata = {"date": None, "gps": None}
        stripped = uploads.module.save_and_process(pick("stripped.mov", b"a"))

        uploads.metadata = {"date": "2026-02-11T21:31:00-0500", "gps": "+43.6-72.2/"}
        tagged = uploads.module.save_and_process(pick("tagged.mov", b"b"))

        assert stripped["status"] == "queued"
        assert (stripped["has_date"], stripped["has_gps"]) == (False, False)
        assert (tagged["has_date"], tagged["has_gps"]) == (True, True)

    def test_the_result_always_carries_the_documented_keys(self, uploads):
        expected = {"filename", "video_id", "status", "has_date", "has_gps", "error"}
        assert set(uploads.module.save_and_process(pick("a.mov"))) == expected
        assert set(uploads.module.save_and_process(pick("a.txt"))) == expected

    def test_a_very_long_filename_still_queues(self, uploads):
        result = uploads.module.save_and_process(pick("a" * 300 + ".mov"))
        assert result["status"] == "queued", result["error"]


# ── save_and_process: dedupe ──────────────────────────────────────────────────

class TestDedupe:
    """`backend/CLAUDE.md`: the content hash, not the filename, is the dedupe key."""

    def test_the_same_clip_picked_twice_reuses_the_existing_queue_row(self, uploads):
        first = uploads.module.save_and_process(pick("IMG_0001.MOV", b"one climb"))
        second = uploads.module.save_and_process(pick("IMG_0001.MOV", b"one climb"))

        assert second["status"] == "already_queued"
        assert second["video_id"] == first["video_id"]
        assert len(uploads.analysed) == 1        # not re-analysed

    def test_a_renamed_re_pick_is_still_the_same_clip(self, uploads):
        """Explicit in the docstring: matching on the name would put a second
        few-hundred-MB copy on disk just because the file got renamed."""
        first = uploads.module.save_and_process(pick("IMG_0001.MOV", b"one climb"))
        second = uploads.module.save_and_process(pick("bouldering_v2.mp4", b"one climb"))

        assert second["video_id"] == first["video_id"]
        assert second["status"] == "already_queued"
        assert len(uploads.stored_files()) == 1

    def test_two_different_clips_sharing_a_name_both_queue(self, uploads):
        a = uploads.module.save_and_process(pick("IMG_0001.MOV", b"climb one"))
        b = uploads.module.save_and_process(pick("IMG_0001.MOV", b"climb two"))

        assert a["video_id"] != b["video_id"]
        assert {a["status"], b["status"]} == {"queued"}
        assert len(uploads.stored_files()) == 2

    def test_a_re_pick_after_the_proposal_was_removed_is_analysed_again(self, uploads):
        """Dedupe keys on the proposal, not on the parked file: deleting the queue
        row and re-picking must re-analyse, and must not park a second copy."""
        first = uploads.module.save_and_process(pick("IMG_0001.MOV", b"one climb"))
        (uploads.proposals / f"{first['video_id']}.json").unlink()

        second = uploads.module.save_and_process(pick("IMG_0001.MOV", b"one climb"))

        assert second["status"] == "queued"
        assert len(uploads.analysed) == 2
        assert len(uploads.stored_files()) == 1

    def test_a_duplicate_re_pick_still_reports_its_metadata(self, uploads):
        uploads.metadata = {"date": "2026-02-11T21:31:00-0500", "gps": None}
        uploads.module.save_and_process(pick("IMG_0001.MOV", b"one climb"))
        second = uploads.module.save_and_process(pick("IMG_0001.MOV", b"one climb"))

        assert second["has_date"] is True
        assert second["has_gps"] is False


# ── save_and_process: failure isolation ───────────────────────────────────────

class TestNeverRaises:
    """"Never raises: the route processes a whole selection and one bad file must
    not sink the rest." Every failure must come back as a result dict."""

    def test_a_failed_write_becomes_an_error_result(self, uploads):
        result = uploads.module.save_and_process(
            ExplodingUpload("IMG_0001.MOV", OSError(28, "No space left on device"))
        )
        assert result["status"] == "error"
        assert result["error"]
        assert uploads.stored_files() == []

    def test_a_failed_write_leaves_nothing_staged(self, uploads):
        uploads.module.save_and_process(
            ExplodingUpload("IMG_0001.MOV", OSError(28, "No space left on device"))
        )
        assert uploads.staged_files() == []

    def test_a_failed_metadata_probe_becomes_an_error_result(self, uploads, monkeypatch):
        def boom(path):
            raise RuntimeError("ffmpeg vanished")

        monkeypatch.setattr(uploads.module.export_video, "read_source_metadata", boom)
        result = uploads.module.save_and_process(pick("IMG_0001.MOV"))

        assert result["status"] == "error"
        assert "ffmpeg vanished" in result["error"]

    def test_a_failed_analysis_becomes_an_error_result(self, uploads):
        uploads.analysis_error = RuntimeError("Conversion failed!")
        result = uploads.module.save_and_process(pick("IMG_0001.MOV"))

        assert result["status"] == "error"
        assert "Conversion failed!" in result["error"]
        assert uploads.staged_files() == []

    def test_a_retry_after_a_failed_analysis_re_analyses(self, uploads):
        """The parked copy survives a failed analysis, so the retry must not be
        mistaken for an already-queued clip."""
        uploads.analysis_error = RuntimeError("Conversion failed!")
        uploads.module.save_and_process(pick("IMG_0001.MOV", b"one climb"))

        uploads.analysis_error = None
        retry = uploads.module.save_and_process(pick("IMG_0001.MOV", b"one climb"))

        assert retry["status"] == "queued"
        assert len(uploads.stored_files()) == 1

    def test_one_bad_file_in_a_selection_does_not_stop_the_others(self, uploads):
        picks = [pick("notes.txt"), pick("good.mov", b"one"),
                 ExplodingUpload("bad.mov", OSError("nope")), pick("also.mp4", b"two")]
        results = [uploads.module.save_and_process(f) for f in picks]

        assert [r["status"] for r in results] == ["error", "queued", "error", "queued"]

    def test_a_zero_byte_upload_does_not_crash(self, uploads):
        result = uploads.module.save_and_process(pick("empty.mov", b""))
        assert result["status"] in {"queued", "error"}
        assert uploads.staged_files() == []


# ── the ffmpeg boundary ───────────────────────────────────────────────────────

class TestMetadataProbeBoundary:
    """The one subprocess this module reaches, via export_video.read_source_metadata."""

    @pytest.fixture(autouse=True)
    def real_probe(self, uploads, monkeypatch, fake_run):
        monkeypatch.setattr(uploads.module.export_video, "read_source_metadata",
                            uploads.real_read_source_metadata)
        fake_run.install("export_video")
        return fake_run

    def test_a_real_iphone_clip_reports_both_date_and_gps(self, uploads, real_probe,
                                                          ffmpeg_stderr):
        real_probe.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        result = uploads.module.save_and_process(pick("IMG_0001.MOV"))

        assert (result["has_date"], result["has_gps"]) == (True, True)

    @pytest.mark.parametrize("blob", ["malformed_empty", "malformed_truncated",
                                      "malformed_garbage"])
    def test_unreadable_output_reports_no_metadata_but_still_queues(
        self, uploads, real_probe, ffmpeg_stderr, blob
    ):
        real_probe.set_response(stderr=ffmpeg_stderr[blob])
        result = uploads.module.save_and_process(pick("IMG_0001.MOV"))

        assert result["status"] == "queued"
        assert (result["has_date"], result["has_gps"]) == (False, False)

    def test_the_probe_gets_the_settled_path_as_a_list_argument(self, uploads,
                                                               real_probe, ffmpeg_stderr):
        """A list argv with no shell, and the path in the slot after `-i` — so a
        name that survived sanitisation as `-something` still reads as a filename."""
        real_probe.set_response(stderr=ffmpeg_stderr["iphone_mov"])
        uploads.module.save_and_process(pick("-rf.mov"))

        [call] = real_probe.ffmpeg_calls
        assert call.is_argv_list
        assert not call.uses_shell
        [stored] = uploads.stored_files()
        assert call.flag_value("-i") == str(stored)


# ── the route wrapper ─────────────────────────────────────────────────────────

class TestUploadRoute:
    """server.py maps save_and_process over the selection; malformed input must
    come back 4xx, never 500."""

    def test_no_files_is_a_400(self, client, uploads):
        resp = client.post("/motion-review/upload", data={},
                           content_type="multipart/form-data")
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_a_non_video_selection_is_reported_per_file_not_as_a_500(
        self, client, uploads
    ):
        resp = client.post(
            "/motion-review/upload",
            data={"files": (BytesIO(b"MZ\x90\x00"), "payload.exe")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["queued"] == 0
        assert body["results"][0]["status"] == "error"
        assert uploads.stored_files() == []

    def test_a_mixed_selection_counts_only_what_queued(self, client, uploads):
        resp = client.post(
            "/motion-review/upload",
            data={"files": [(BytesIO(b"one"), "a.mov"),
                            (BytesIO(b"nope"), "b.txt"),
                            (BytesIO(b"two"), "c.mp4")]},
            content_type="multipart/form-data",
        )
        body = resp.get_json()
        assert body["queued"] == 2
        assert len(body["results"]) == 3
