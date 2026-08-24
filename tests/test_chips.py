"""
Chip store — the saved-object registry for junk-cull filters
=============================================================
These tests assert the contract stated in `chips.py`'s docstrings, not the
current arithmetic. The load-bearing ones, in rough order of how much damage
they prevent:

* **A chip `id` IS the dismissal ledger's category key.** So `update()` refuses
  to change an id, `delete()` refuses to remove a builtin, `upsert()` will not
  let an edit grant or revoke builtin-ness, and `validate()` rejects anything
  outside the shared `dismissed._CATEGORY_RE` charset. An accepted-but-wrong id
  orphans a user's dismissals silently, which the module calls "the single most
  damaging thing" it can do.
* **Reads never write.** `load()`/`list_chips()`/`get()`/`known_ids()` must
  survive a missing, corrupt, wrong-shaped or wrong-encoded file without
  creating or repairing it — these modules are imported during pytest
  collection, before any fixture has redirected the path.
* **A bad chip is skipped, not fatal.** One hand-edited entry must not blank the
  whole tick row.
* **schema_version 1 is single-prompt, no negatives, on purpose.** The wire
  shape is a list so it can grow, but the semantic engine implements one prompt;
  accepting more would select photos by an untested rule.
* **The six builtins are pinned against independent literals** (see
  `BUILTIN_PIN`) so a drift in what a migrated tick selects turns the suite red.

`chips.CHIPS_PATH` is redirected into tmp_path for every test by the autouse
`isolate_chips` fixture in conftest.py; tests that assert on the file itself
request it by name for readability.

Slow: chips imports utils (via dismissed), which pulls torch in at module scope.
"""

import json
import os
import threading
from pathlib import Path

import pytest

import chips

pytestmark = pytest.mark.slow


# ── helpers ───────────────────────────────────────────────────────────────────

def make_chip(**overrides):
    """A minimal chip that validate() accepts, with fields overridable."""
    chip = {
        "id": "custom",
        "label": "Custom filter",
        "engine": "semantic",
        "query": {"prompts": ["a red door"]},
    }
    chip.update(overrides)
    return chip


def write_store(path: Path, chip_list, schema_version=1, **extra):
    """Hand-write a chips.json, the way a user editing the file would."""
    payload = {"schema_version": schema_version, "chips": list(chip_list), **extra}
    path.write_text(json.dumps(payload))
    return payload


def ids_on_disk(path: Path):
    return [c["id"] for c in json.loads(path.read_text())["chips"]]


# The six builtins as they behaved BEFORE the chip store existed, transcribed
# from SearchChips.jsx's old CHIPS array. These literals are written out here on
# purpose and are NOT read from `chips.BUILTIN_CHIPS`: the whole point of this
# pin is that editing that constant makes the suite go red, so that a change to
# what a migrated tick selects has to be a deliberate, reviewed act. If you are
# here because this test failed, the question is "did we mean to change what
# this tick selects?" — not "how do I update the expected value?".
BUILTIN_PIN = [
    # (order, id, emoji, label, prompt)
    (0, "accidental", "📷", "Accidental photo", "accidental photo"),
    (1, "dark", "🌑", "Dark or underexposed", "dark or underexposed photo"),
    (2, "blurry", "💨", "Blurry or out of focus", "blurry or out of focus photo"),
    (3, "screenshot", "📄", "Screenshot or document", "screenshot or document"),
    (4, "receipt", "🧾", "Receipt or invoice", "receipt or invoice"),
    (5, "duplicate", "🔁", "Duplicate scene", "duplicate scene"),
]

CHIP_KEYS = {
    "id", "label", "emoji", "builtin", "enabled", "order", "engine",
    "query", "result_size",
}


# ── validate(): rejection ─────────────────────────────────────────────────────

class TestValidateRejectsBadIds:
    """The id is the dismissal ledger key, so its charset is the strictest
    thing in the module — a near-miss id silently orphans dismissals."""

    @pytest.mark.parametrize("bad_id", [
        pytest.param("Bad Id", id="spaces-and-capitals"),
        pytest.param("../x", id="path-traversal"),
        pytest.param("", id="empty"),
        pytest.param("UPPER", id="uppercase"),
        pytest.param("dot.id", id="dot"),
        pytest.param("sp ace", id="space"),
        pytest.param("slash/id", id="slash"),
        pytest.param("emoji🙂", id="non-ascii"),
        pytest.param("a" * 41, id="41-chars"),
    ])
    def test_an_id_outside_the_charset_is_refused(self, bad_id):
        with pytest.raises(ValueError, match="invalid chip id"):
            chips.validate(make_chip(id=bad_id))

    @pytest.mark.parametrize("bad_id", [None, 5, ["dark"], {"id": "dark"}])
    def test_a_non_string_id_is_refused_rather_than_coerced(self, bad_id):
        with pytest.raises(ValueError, match="invalid chip id"):
            chips.validate(make_chip(id=bad_id))

    def test_a_missing_id_is_refused(self):
        chip = make_chip()
        del chip["id"]
        with pytest.raises(ValueError, match="invalid chip id"):
            chips.validate(chip)

    @pytest.mark.parametrize("good_id", ["a", "0", "a-b_c", "a" * 40, "blurry"])
    def test_the_documented_charset_is_accepted(self, good_id):
        assert chips.validate(make_chip(id=good_id))["id"] == good_id

    # Regression pin for a fixed defect. `_CATEGORY_RE` used to be anchored
    # with `$`, and in Python `$` also matches just before a trailing newline —
    # so "dark\n" passed a charset check that lists no newline, producing an id
    # that renders identically to "dark" in the tick row but is a DIFFERENT
    # dismissal ledger key, silently splitting a chip's dismissals in two.
    # Fixed by anchoring with `\Z` in dismissed.py, which closes the same hole
    # in `dismissed._validate_category`. Don't loosen the anchor.
    def test_an_id_with_a_trailing_newline_is_refused(self):
        with pytest.raises(ValueError, match="invalid chip id"):
            chips.validate(make_chip(id="dark\n"))


class TestValidateRejectsBadLabels:
    @pytest.mark.parametrize("bad_label", [
        pytest.param("", id="empty"),
        pytest.param("   ", id="spaces-only"),
        pytest.param("\t\n", id="whitespace-only"),
    ])
    def test_a_blank_label_is_refused(self, bad_label):
        with pytest.raises(ValueError, match="label must be a non-blank string"):
            chips.validate(make_chip(label=bad_label))

    @pytest.mark.parametrize("bad_label", [None, 42, ["Label"]])
    def test_a_non_string_label_is_refused(self, bad_label):
        with pytest.raises(ValueError, match="label must be a non-blank string"):
            chips.validate(make_chip(label=bad_label))

    def test_a_label_of_exactly_eighty_characters_is_accepted(self):
        label = "x" * 80
        assert chips.validate(make_chip(label=label))["label"] == label

    def test_a_label_over_eighty_characters_is_refused(self):
        with pytest.raises(ValueError, match="80 characters or fewer"):
            chips.validate(make_chip(label="x" * 81))


class TestValidateRejectsBadEmoji:
    def test_a_non_string_emoji_is_refused(self):
        with pytest.raises(ValueError, match="emoji"):
            chips.validate(make_chip(emoji=None))

    def test_an_emoji_of_exactly_eight_characters_is_accepted(self):
        assert chips.validate(make_chip(emoji="x" * 8))["emoji"] == "x" * 8

    def test_an_over_long_emoji_is_refused(self):
        with pytest.raises(ValueError, match="emoji"):
            chips.validate(make_chip(emoji="x" * 9))


class TestValidateRejectsBadEngines:
    @pytest.mark.parametrize("bad_engine", [
        pytest.param("laplacian", id="plausible-but-unimplemented"),
        pytest.param("SEMANTIC", id="wrong-case"),
        pytest.param("", id="empty"),
        pytest.param(None, id="missing"),
        pytest.param(1, id="non-string"),
    ])
    def test_an_unknown_engine_is_refused(self, bad_engine):
        # An engine with no chip_resolve implementation must never reach disk;
        # resolve() would raise on it at search time instead.
        with pytest.raises(ValueError, match="unknown engine"):
            chips.validate(make_chip(engine=bad_engine))

    def test_every_declared_engine_validates(self):
        # ENGINES is the dispatch list; a name in it that validate() rejects
        # would be a chip type nothing could ever save.
        for engine in chips.ENGINES:
            assert chips.validate(make_chip(engine=engine))["engine"] == engine


class TestValidateRejectsBadResultSize:
    @pytest.mark.parametrize("size", [0, -1, -100])
    def test_a_result_size_below_one_is_refused(self, size):
        with pytest.raises(ValueError, match="result_size must be between"):
            chips.validate(make_chip(result_size=size))

    def test_a_result_size_above_the_ceiling_is_refused(self):
        with pytest.raises(ValueError, match="result_size must be between"):
            chips.validate(make_chip(result_size=chips.MAX_RESULT_SIZE + 1))

    @pytest.mark.parametrize("size", [1, 24, 499, 500])
    def test_the_inclusive_bounds_are_accepted(self, size):
        assert chips.validate(make_chip(result_size=size))["result_size"] == size

    @pytest.mark.parametrize("size", [
        pytest.param(True, id="bool-true"),
        pytest.param(False, id="bool-false"),
    ])
    def test_a_bool_result_size_is_refused_rather_than_read_as_one_or_zero(self, size):
        # bool is an int subclass; True must not silently mean "1 result".
        with pytest.raises(ValueError, match="result_size must be an integer"):
            chips.validate(make_chip(result_size=size))

    @pytest.mark.parametrize("size", ["24", 24.0, None, [24]])
    def test_a_non_integer_result_size_is_refused(self, size):
        with pytest.raises(ValueError, match="result_size must be an integer"):
            chips.validate(make_chip(result_size=size))


class TestValidateRejectsBadOrder:
    @pytest.mark.parametrize("order", [-1, -99])
    def test_a_negative_order_is_refused(self, order):
        with pytest.raises(ValueError, match="order must be an integer"):
            chips.validate(make_chip(order=order))

    @pytest.mark.parametrize("order", ["3", 3.0, None, True])
    def test_a_non_integer_order_is_refused(self, order):
        with pytest.raises(ValueError, match="order must be an integer"):
            chips.validate(make_chip(order=order))

    def test_zero_is_a_valid_order(self):
        assert chips.validate(make_chip(order=0))["order"] == 0


class TestValidateRejectsBadQueries:
    @pytest.mark.parametrize("query", [
        pytest.param(None, id="missing"),
        pytest.param(["a red door"], id="bare-list"),
        pytest.param("a red door", id="bare-string"),
        pytest.param(42, id="number"),
    ])
    def test_a_query_that_is_not_an_object_is_refused(self, query):
        with pytest.raises(ValueError, match="query must be an object"):
            chips.validate(make_chip(query=query))

    @pytest.mark.parametrize("prompts", [
        pytest.param([], id="empty-list"),
        pytest.param(None, id="missing"),
        pytest.param("a red door", id="bare-string"),
        pytest.param({"0": "a red door"}, id="object"),
    ])
    def test_prompts_must_be_a_non_empty_list(self, prompts):
        with pytest.raises(ValueError, match="prompts must be a non-empty list"):
            chips.validate(make_chip(query={"prompts": prompts}))

    @pytest.mark.parametrize("prompt", [
        pytest.param("", id="empty-string"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param(None, id="null"),
        pytest.param(7, id="number"),
    ])
    def test_every_prompt_must_be_a_non_blank_string(self, prompt):
        with pytest.raises(ValueError, match="every prompt must be a non-blank string"):
            chips.validate(make_chip(query={"prompts": [prompt]}))

    def test_a_non_list_negatives_is_refused(self):
        with pytest.raises(ValueError, match="negatives must be a list"):
            chips.validate(make_chip(query={"prompts": ["x"], "negatives": "y"}))


class TestSchemaVersionOneIsSinglePromptOnly:
    """Multi-prompt fusion and negative prompts are REFUSED on purpose: the v1
    semantic engine implements one prompt, so accepting either would select
    photos by a rule nothing implements or tests."""

    @pytest.mark.parametrize("prompts", [
        pytest.param(["a", "b"], id="two"),
        pytest.param(["a", "b", "c"], id="three"),
    ])
    def test_multiple_prompts_are_refused(self, prompts):
        with pytest.raises(ValueError, match="multiple prompts are not supported"):
            chips.validate(make_chip(query={"prompts": prompts}))

    def test_the_multi_prompt_error_names_the_schema_version(self):
        # The refusal is a version statement, not a permanent rule — the message
        # has to say which version, so a future v2 knows what to lift.
        with pytest.raises(ValueError) as exc:
            chips.validate(make_chip(query={"prompts": ["a", "b"]}))
        assert f"schema_version {chips.SCHEMA_VERSION}" in str(exc.value)

    def test_a_negative_prompt_is_refused(self):
        with pytest.raises(ValueError, match="negative prompts are not supported"):
            chips.validate(make_chip(query={"prompts": ["a"], "negatives": ["b"]}))

    def test_the_negatives_error_names_the_schema_version(self):
        with pytest.raises(ValueError) as exc:
            chips.validate(make_chip(query={"prompts": ["a"], "negatives": ["b"]}))
        assert f"schema_version {chips.SCHEMA_VERSION}" in str(exc.value)

    def test_an_empty_negatives_list_is_fine(self):
        chip = chips.validate(make_chip(query={"prompts": ["a"], "negatives": []}))
        assert chip["query"]["negatives"] == []

    def test_a_multi_prompt_chip_cannot_be_written_to_disk_either(self, isolate_chips):
        # The refusal has to hold at the store boundary, not just in validate() —
        # otherwise a bad chip lands on disk and load() silently drops it later.
        with pytest.raises(ValueError):
            chips.upsert(make_chip(query={"prompts": ["a", "b"]}))
        assert not isolate_chips.chips.exists()


# ── validate(): normalisation ─────────────────────────────────────────────────

class TestValidateFillsDefaults:
    """A validated chip is always complete, so nothing downstream (resolve, the
    route, the frontend) has to handle a missing key."""

    def test_a_minimal_chip_comes_back_with_every_field(self):
        assert set(chips.validate(make_chip())) == CHIP_KEYS

    def test_the_documented_defaults_are_filled_in(self):
        chip = chips.validate(make_chip())
        assert chip["emoji"] == ""
        assert chip["builtin"] is False
        assert chip["enabled"] is True
        assert chip["order"] == 0
        assert chip["result_size"] == 24
        assert chip["query"]["negatives"] == []

    def test_supplied_values_win_over_the_defaults(self):
        chip = chips.validate(make_chip(
            emoji="🔥", builtin=True, enabled=False, order=7, result_size=100,
        ))
        assert chip["emoji"] == "🔥"
        assert chip["builtin"] is True
        assert chip["enabled"] is False
        assert chip["order"] == 7
        assert chip["result_size"] == 100

    @pytest.mark.parametrize("truthy", [1, "yes", [0]])
    def test_builtin_and_enabled_are_coerced_to_real_bools(self, truthy):
        # They are serialised to JSON and compared with `is` downstream, so a
        # truthy string must not survive as a string.
        chip = chips.validate(make_chip(builtin=truthy, enabled=truthy))
        assert chip["builtin"] is True and chip["enabled"] is True

    def test_unknown_fields_are_dropped_rather_than_stored(self):
        chip = chips.validate(make_chip(sneaky="payload", schema_version=99))
        assert set(chip) == CHIP_KEYS

    def test_the_caller_dict_is_not_mutated(self):
        original = make_chip()
        before = json.dumps(original, sort_keys=True)
        chips.validate(original)
        assert json.dumps(original, sort_keys=True) == before

    def test_the_prompts_list_is_copied_not_aliased(self):
        prompts = ["a red door"]
        chip = chips.validate(make_chip(query={"prompts": prompts}))
        prompts.append("something else")
        assert chip["query"]["prompts"] == ["a red door"]

    @pytest.mark.parametrize("not_a_chip", [None, [], "chip", 42, ("id",)])
    def test_a_non_object_chip_is_refused(self, not_a_chip):
        with pytest.raises(ValueError, match="chip must be an object"):
            chips.validate(not_a_chip)

    def test_validate_is_idempotent(self):
        once = chips.validate(make_chip())
        assert chips.validate(once) == once


# ── load(): reads never write ─────────────────────────────────────────────────

EMPTY_STORE = {"schema_version": 1, "chips": []}

CORRUPT_BODIES = [
    pytest.param("{not json", id="truncated-garbage"),
    pytest.param("", id="empty-file"),
    pytest.param("   \n", id="whitespace-only"),
    pytest.param('["accidental", "dark"]', id="json-list-not-object"),
    pytest.param('"just a string"', id="json-string"),
    pytest.param("null", id="json-null"),
    pytest.param("17", id="json-number"),
]


class TestReadsNeverWrite:
    """load()/list_chips()/get()/known_ids() are called during import and
    collection, before any fixture has redirected CHIPS_PATH — a write-on-read
    would drop a real file into the live photo_db/."""

    def test_a_missing_file_is_not_created_by_any_read(self, isolate_chips):
        chips.load()
        chips.list_chips()
        chips.list_chips(enabled_only=True)
        chips.get("dark")
        chips.known_ids()
        assert not isolate_chips.chips.exists()

    def test_reading_does_not_create_the_parent_directory_either(
        self, tmp_path, monkeypatch
    ):
        nested = tmp_path / "never" / "made" / "chips.json"
        monkeypatch.setattr(chips, "CHIPS_PATH", nested)
        chips.load()
        chips.known_ids()
        assert not nested.parent.exists()

    def test_a_missing_file_reads_back_as_an_empty_store(self):
        assert chips.load() == EMPTY_STORE

    @pytest.mark.parametrize("body", CORRUPT_BODIES)
    def test_a_corrupt_file_reads_back_as_an_empty_store(self, isolate_chips, body):
        isolate_chips.chips.write_text(body)
        assert chips.load() == EMPTY_STORE

    @pytest.mark.parametrize("body", CORRUPT_BODIES)
    def test_a_corrupt_file_is_never_repaired_or_overwritten_by_a_read(
        self, isolate_chips, body
    ):
        isolate_chips.chips.write_text(body)
        chips.load()
        chips.list_chips()
        chips.known_ids()
        assert isolate_chips.chips.read_text() == body

    def test_a_file_in_the_wrong_encoding_falls_back_like_any_corrupt_file(
        self, isolate_chips
    ):
        # UnicodeDecodeError is a ValueError subclass, which is what the
        # comment in load() is relying on — a latin-1 blob must not escape.
        isolate_chips.chips.write_bytes(b'{"schema_version": 1, "chips": [\xff\xfe]}')
        assert chips.load() == EMPTY_STORE

    def test_a_valid_file_is_not_rewritten_by_a_read(self, isolate_chips):
        body = json.dumps(
            {"schema_version": 1, "chips": [chips.validate(make_chip())]},
            indent=4,
        )
        isolate_chips.chips.write_text(body)
        chips.load()
        chips.list_chips()
        chips.get("custom")
        assert isolate_chips.chips.read_text() == body

    def test_a_missing_chips_key_reads_back_as_no_chips(self, isolate_chips):
        isolate_chips.chips.write_text(json.dumps({"schema_version": 1}))
        assert chips.load()["chips"] == []

    @pytest.mark.parametrize("chips_value", [
        pytest.param(None, id="null"),
        pytest.param("dark", id="string"),
        pytest.param({"dark": {}}, id="object"),
    ])
    def test_a_wrong_shaped_chips_value_degrades_to_no_chips(
        self, isolate_chips, chips_value
    ):
        isolate_chips.chips.write_text(
            json.dumps({"schema_version": 1, "chips": chips_value})
        )
        assert chips.load()["chips"] == []

    # Regression pin for a fixed defect. `for entry in raw.get("chips") or []`
    # iterated whatever was there, so a scalar under the "chips" key raised
    # TypeError instead of degrading — breaking load()'s "never raises" promise.
    # It was reachable by hand-editing chips.json and then starting the server:
    # ensure_seeded() calls load() at startup, so the TypeError propagated out
    # of server start and GET /chips would 500 rather than show an empty tick
    # row. Fixed with an isinstance(entries, list) guard.
    @pytest.mark.parametrize("chips_value", [
        pytest.param(5, id="int"),
        pytest.param(17.5, id="float"),
        pytest.param(True, id="bool"),
    ])
    def test_a_scalar_chips_value_degrades_instead_of_raising(
        self, isolate_chips, chips_value
    ):
        isolate_chips.chips.write_text(
            json.dumps({"schema_version": 1, "chips": chips_value})
        )
        assert chips.load()["chips"] == []


class TestLoadSkipsBadChipsIndividually:
    """One hand-edited chip must not blank the tick row."""

    def test_a_valid_chip_survives_an_invalid_neighbour(self, isolate_chips):
        good = chips.validate(make_chip(id="keeper", order=1))
        write_store(isolate_chips.chips, [{"id": "Bad Id", "label": "x"}, good])
        assert [c["id"] for c in chips.load()["chips"]] == ["keeper"]

    @pytest.mark.parametrize("bad", [
        pytest.param({"id": "no-label", "engine": "semantic",
                      "query": {"prompts": ["x"]}}, id="missing-label"),
        pytest.param({"id": "bad-engine", "label": "L", "engine": "vibes",
                      "query": {"prompts": ["x"]}}, id="unknown-engine"),
        pytest.param({"id": "two-prompts", "label": "L", "engine": "semantic",
                      "query": {"prompts": ["a", "b"]}}, id="multi-prompt"),
        pytest.param("not even an object", id="non-object"),
        pytest.param(None, id="null-entry"),
    ])
    def test_each_kind_of_bad_entry_is_dropped_without_raising(
        self, isolate_chips, bad
    ):
        good = chips.validate(make_chip(id="keeper"))
        write_store(isolate_chips.chips, [bad, good])
        assert [c["id"] for c in chips.load()["chips"]] == ["keeper"]

    def test_a_store_of_only_bad_chips_reads_back_empty_rather_than_raising(
        self, isolate_chips
    ):
        write_store(isolate_chips.chips, [{"id": "Bad Id"}, {"nope": True}])
        assert chips.load()["chips"] == []

    def test_a_bad_chip_is_not_deleted_from_disk_by_a_read(self, isolate_chips):
        write_store(isolate_chips.chips, [{"id": "Bad Id", "label": "x"}])
        chips.load()
        assert ids_on_disk(isolate_chips.chips) == ["Bad Id"]


# ── ordering and filtering ────────────────────────────────────────────────────

class TestOrdering:
    """The tick row renders in list order, so the sort is the display order."""

    def test_chips_come_back_sorted_by_order_then_id(self, isolate_chips):
        entries = [
            chips.validate(make_chip(id="zeta", order=2)),
            chips.validate(make_chip(id="beta", order=1)),
            chips.validate(make_chip(id="alpha", order=1)),
            chips.validate(make_chip(id="gamma", order=0)),
        ]
        write_store(isolate_chips.chips, entries)
        assert [c["id"] for c in chips.load()["chips"]] == [
            "gamma", "alpha", "beta", "zeta",
        ]

    def test_ties_on_order_are_broken_by_id_so_the_row_is_stable(self, isolate_chips):
        entries = [chips.validate(make_chip(id=i, order=0)) for i in ("c", "a", "b")]
        write_store(isolate_chips.chips, entries)
        assert [c["id"] for c in chips.list_chips()] == ["a", "b", "c"]

    def test_list_chips_matches_load(self, isolate_chips):
        write_store(isolate_chips.chips, [chips.validate(make_chip())])
        assert chips.list_chips() == chips.load()["chips"]

    def test_the_written_file_is_sorted_too_so_it_stays_hand_readable(self):
        chips.upsert(make_chip(id="zeta", order=9))
        chips.upsert(make_chip(id="alpha", order=0))
        assert [c["id"] for c in chips.list_chips()] == ["alpha", "zeta"]

    def test_a_write_does_not_disturb_the_order_of_the_others(self):
        for i, cid in enumerate(("a", "b", "c")):
            chips.upsert(make_chip(id=cid, order=i))
        chips.upsert(make_chip(id="d", order=1))
        assert [c["id"] for c in chips.list_chips()] == ["a", "b", "d", "c"]


class TestEnabledOnly:
    @pytest.fixture(autouse=True)
    def _two_chips(self):
        chips.upsert(make_chip(id="on", order=0, enabled=True))
        chips.upsert(make_chip(id="off", order=1, enabled=False))

    def test_enabled_only_omits_disabled_chips(self):
        assert [c["id"] for c in chips.list_chips(enabled_only=True)] == ["on"]

    def test_the_default_includes_disabled_chips(self):
        # The editor needs to see a disabled chip in order to re-enable it.
        assert [c["id"] for c in chips.list_chips()] == ["on", "off"]

    def test_a_disabled_chip_is_still_on_disk(self, isolate_chips):
        assert "off" in ids_on_disk(isolate_chips.chips)

    def test_a_disabled_chip_is_still_reachable_by_id(self):
        assert chips.get("off")["enabled"] is False


# ── get() / known_ids() ───────────────────────────────────────────────────────

class TestGet:
    def test_an_existing_chip_comes_back_whole(self):
        chips.upsert(make_chip(id="dogs", label="Dog photos"))
        assert set(chips.get("dogs")) == CHIP_KEYS

    def test_an_unknown_id_returns_none_rather_than_raising(self):
        # The route turns None into a 404; a raise would be a 500.
        assert chips.get("nope") is None

    def test_an_unknown_id_returns_none_on_an_empty_store(self):
        assert chips.get("dark") is None

    @pytest.mark.parametrize("bad_id", ["", "../dark", "Bad Id"])
    def test_a_malformed_id_returns_none_rather_than_raising(self, bad_id):
        assert chips.get(bad_id) is None

    def test_mutating_a_returned_chip_cannot_poison_the_store(self):
        chips.upsert(make_chip(id="dogs", label="Dog photos"))
        got = chips.get("dogs")
        got["label"] = "Cat photos"
        got["query"]["prompts"][0] = "cats"
        assert chips.get("dogs")["label"] == "Dog photos"
        assert chips.get("dogs")["query"]["prompts"] == ["a red door"]


class TestKnownIds:
    """The union matters for the dismissal migration: a builtin that has not
    been seeded to disk yet is still a chip id, and its dismissals are not
    orphans."""

    def test_builtins_count_even_with_no_file_on_disk(self, isolate_chips):
        assert chips.known_ids() == {c[1] for c in BUILTIN_PIN}
        assert not isolate_chips.chips.exists()

    def test_a_user_chip_is_included_alongside_the_builtins(self):
        chips.upsert(make_chip(id="dogs"))
        assert chips.known_ids() == {c[1] for c in BUILTIN_PIN} | {"dogs"}

    def test_a_builtin_deleted_from_the_file_by_hand_still_counts(
        self, isolate_chips
    ):
        chips.ensure_seeded()
        remaining = [c for c in chips.list_chips() if c["id"] != "dark"]
        write_store(isolate_chips.chips, remaining)
        assert "dark" in chips.known_ids()

    def test_an_invalid_stored_chip_does_not_count_as_known(self, isolate_chips):
        write_store(isolate_chips.chips, [{"id": "Bad Id", "label": "x"}])
        assert "Bad Id" not in chips.known_ids()

    def test_it_returns_a_set(self):
        assert isinstance(chips.known_ids(), set)


# ── upsert() ──────────────────────────────────────────────────────────────────

class TestUpsert:
    def test_a_new_chip_lands_on_disk(self, isolate_chips):
        chips.upsert(make_chip(id="dogs"))
        assert ids_on_disk(isolate_chips.chips) == ["dogs"]

    def test_it_returns_the_normalised_chip_a_later_read_sees(self):
        returned = chips.upsert(make_chip(id="dogs"))
        assert returned == chips.get("dogs")

    def test_upserting_the_same_id_replaces_rather_than_duplicates(
        self, isolate_chips
    ):
        chips.upsert(make_chip(id="dogs", label="Dogs"))
        chips.upsert(make_chip(id="dogs", label="Good dogs"))
        assert ids_on_disk(isolate_chips.chips) == ["dogs"]
        assert chips.get("dogs")["label"] == "Good dogs"

    def test_it_leaves_the_other_chips_alone(self):
        chips.upsert(make_chip(id="dogs"))
        chips.upsert(make_chip(id="cats"))
        chips.upsert(make_chip(id="dogs", label="Good dogs"))
        assert {c["id"] for c in chips.list_chips()} == {"dogs", "cats"}

    def test_it_writes_the_current_schema_version(self, isolate_chips):
        chips.upsert(make_chip())
        assert json.loads(isolate_chips.chips.read_text())["schema_version"] == (
            chips.SCHEMA_VERSION
        )

    def test_an_invalid_chip_never_reaches_disk(self, isolate_chips):
        with pytest.raises(ValueError):
            chips.upsert(make_chip(id="Bad Id"))
        assert not isolate_chips.chips.exists()

    def test_an_invalid_chip_does_not_disturb_an_existing_store(self, isolate_chips):
        chips.upsert(make_chip(id="dogs"))
        before = isolate_chips.chips.read_text()
        with pytest.raises(ValueError):
            chips.upsert(make_chip(id="dogs", label=""))
        assert isolate_chips.chips.read_text() == before

    def test_it_creates_the_parent_directory_when_missing(self, tmp_path, monkeypatch):
        nested = tmp_path / "made" / "on" / "demand" / "chips.json"
        monkeypatch.setattr(chips, "CHIPS_PATH", nested)
        chips.upsert(make_chip())
        assert nested.exists()

    def test_an_emoji_round_trips_through_disk(self):
        chips.upsert(make_chip(emoji="🧾"))
        assert chips.get("custom")["emoji"] == "🧾"


class TestBuiltinnessIsNotGrantableByAnEdit:
    """builtin is a property of where a chip came from, not of the last write."""

    def test_upserting_over_a_builtin_cannot_revoke_its_builtin_flag(self):
        chips.ensure_seeded()
        returned = chips.upsert(make_chip(id="dark", label="My darks", builtin=False))
        assert returned["builtin"] is True
        assert chips.get("dark")["builtin"] is True

    def test_a_revoke_attempt_leaves_the_builtin_undeletable(self):
        # The flag is what protects the dismissal key, so the protection has to
        # survive the write, not just the returned dict.
        chips.ensure_seeded()
        chips.upsert(make_chip(id="dark", label="My darks", builtin=False))
        with pytest.raises(ValueError):
            chips.delete("dark")

    def test_upserting_over_a_user_chip_cannot_grant_builtin(self):
        chips.upsert(make_chip(id="dogs"))
        returned = chips.upsert(make_chip(id="dogs", label="Dogs!", builtin=True))
        assert returned["builtin"] is False
        assert chips.get("dogs")["builtin"] is False

    def test_a_grant_attempt_leaves_the_user_chip_deletable(self):
        chips.upsert(make_chip(id="dogs"))
        chips.upsert(make_chip(id="dogs", builtin=True))
        chips.delete("dogs")
        assert chips.get("dogs") is None

    def test_the_other_fields_of_the_edit_still_apply(self):
        chips.ensure_seeded()
        chips.upsert(make_chip(id="dark", label="My darks", builtin=False, order=3))
        stored = chips.get("dark")
        assert stored["label"] == "My darks"
        assert stored["order"] == 3


# ── update() ──────────────────────────────────────────────────────────────────

class TestUpdate:
    def test_a_named_field_is_merged_in(self):
        chips.upsert(make_chip(id="dogs", label="Dogs"))
        assert chips.update("dogs", label="Good dogs")["label"] == "Good dogs"

    def test_the_untouched_fields_survive(self):
        chips.upsert(make_chip(id="dogs", label="Dogs", emoji="🐕", order=4))
        updated = chips.update("dogs", result_size=100)
        assert updated["label"] == "Dogs"
        assert updated["emoji"] == "🐕"
        assert updated["order"] == 4
        assert updated["query"]["prompts"] == ["a red door"]

    def test_the_change_is_persisted_not_just_returned(self):
        chips.upsert(make_chip(id="dogs"))
        chips.update("dogs", enabled=False)
        assert chips.get("dogs")["enabled"] is False

    def test_an_unknown_chip_raises_key_error(self):
        with pytest.raises(KeyError):
            chips.update("nope", label="x")

    def test_an_unknown_chip_writes_nothing(self, isolate_chips):
        with pytest.raises(KeyError):
            chips.update("nope", label="x")
        assert not isolate_chips.chips.exists()

    def test_an_invalid_merge_result_raises_and_changes_nothing(self, isolate_chips):
        chips.upsert(make_chip(id="dogs", label="Dogs"))
        before = isolate_chips.chips.read_text()
        with pytest.raises(ValueError):
            chips.update("dogs", result_size=99999)
        assert isolate_chips.chips.read_text() == before

    def test_an_unknown_field_is_dropped_rather_than_stored(self):
        chips.upsert(make_chip(id="dogs"))
        assert set(chips.update("dogs", sneaky="payload")) == CHIP_KEYS


class TestUpdateRefusesToRenameAChip:
    """An id change orphans the chip's dismissals — the single most damaging
    thing this module can do, and there is no undo for it."""

    def test_changing_the_id_raises(self):
        chips.upsert(make_chip(id="dogs"))
        with pytest.raises(ValueError, match="id cannot be changed"):
            chips.update("dogs", id="cats")

    def test_the_refusal_explains_why(self):
        chips.upsert(make_chip(id="dogs"))
        with pytest.raises(ValueError, match="dismissal ledger key"):
            chips.update("dogs", id="cats")

    def test_a_refused_rename_leaves_both_ids_as_they_were(self):
        chips.upsert(make_chip(id="dogs"))
        with pytest.raises(ValueError):
            chips.update("dogs", id="cats")
        assert chips.get("dogs") is not None
        assert chips.get("cats") is None

    def test_a_rename_smuggled_in_beside_a_legitimate_edit_is_still_refused(self):
        chips.upsert(make_chip(id="dogs", label="Dogs"))
        with pytest.raises(ValueError):
            chips.update("dogs", label="Good dogs", id="cats")
        assert chips.get("dogs")["label"] == "Dogs"

    def test_passing_the_same_id_is_a_harmless_no_op(self):
        chips.upsert(make_chip(id="dogs"))
        assert chips.update("dogs", id="dogs", label="Good dogs")["label"] == (
            "Good dogs"
        )

    def test_changing_builtin_raises(self):
        chips.ensure_seeded()
        with pytest.raises(ValueError, match="builtin cannot be changed"):
            chips.update("dark", builtin=False)

    def test_granting_builtin_to_a_user_chip_raises(self):
        chips.upsert(make_chip(id="dogs"))
        with pytest.raises(ValueError, match="builtin cannot be changed"):
            chips.update("dogs", builtin=True)

    def test_passing_the_unchanged_builtin_flag_is_a_no_op(self):
        chips.ensure_seeded()
        assert chips.update("dark", builtin=True, order=9)["order"] == 9

    def test_a_builtin_can_still_be_hidden_by_disabling_it(self):
        # The documented escape hatch: enabled=False instead of delete.
        chips.ensure_seeded()
        chips.update("dark", enabled=False)
        assert [c["id"] for c in chips.list_chips(enabled_only=True)] != []
        assert "dark" not in [c["id"] for c in chips.list_chips(enabled_only=True)]


# ── delete() ──────────────────────────────────────────────────────────────────

class TestDelete:
    def test_a_user_chip_is_removed(self, isolate_chips):
        chips.upsert(make_chip(id="dogs"))
        chips.delete("dogs")
        assert chips.get("dogs") is None
        assert ids_on_disk(isolate_chips.chips) == []

    def test_deleting_one_chip_leaves_the_others(self):
        chips.upsert(make_chip(id="dogs"))
        chips.upsert(make_chip(id="cats"))
        chips.delete("dogs")
        assert [c["id"] for c in chips.list_chips()] == ["cats"]

    def test_an_unknown_id_raises_key_error(self):
        with pytest.raises(KeyError):
            chips.delete("nope")

    def test_an_unknown_id_writes_nothing(self, isolate_chips):
        with pytest.raises(KeyError):
            chips.delete("nope")
        assert not isolate_chips.chips.exists()

    def test_deleting_twice_raises_the_second_time(self):
        chips.upsert(make_chip(id="dogs"))
        chips.delete("dogs")
        with pytest.raises(KeyError):
            chips.delete("dogs")

    @pytest.mark.parametrize("builtin_id", [c[1] for c in BUILTIN_PIN])
    def test_no_builtin_can_be_deleted(self, builtin_id):
        # Deleting one strands its dismissals with no chip to key them to, and
        # nothing short of a code change brings the tick back.
        chips.ensure_seeded()
        with pytest.raises(ValueError, match="builtin"):
            chips.delete(builtin_id)

    def test_a_refused_builtin_delete_leaves_the_chip_on_disk(self, isolate_chips):
        chips.ensure_seeded()
        with pytest.raises(ValueError):
            chips.delete("dark")
        assert "dark" in ids_on_disk(isolate_chips.chips)

    def test_the_refusal_points_at_the_alternatives(self):
        chips.ensure_seeded()
        with pytest.raises(ValueError, match="reset|enabled"):
            chips.delete("dark")


# ── reset() ───────────────────────────────────────────────────────────────────

class TestReset:
    """reset() is the way back from an edited builtin, since delete() is not."""

    def test_an_edited_builtin_comes_back_to_its_shipped_definition(self):
        chips.ensure_seeded()
        chips.update("dark", label="My darks", result_size=100, enabled=False,
                     query={"prompts": ["something else"]})
        restored = chips.reset("dark")
        # Compared against the independent literals, not BUILTIN_CHIPS.
        assert restored["label"] == "Dark or underexposed"
        assert restored["emoji"] == "🌑"
        assert restored["result_size"] == 24
        assert restored["enabled"] is True
        assert restored["order"] == 1
        assert restored["query"]["prompts"] == ["dark or underexposed photo"]

    def test_the_restored_chip_is_persisted(self):
        chips.ensure_seeded()
        chips.update("dark", label="My darks")
        chips.reset("dark")
        assert chips.get("dark")["label"] == "Dark or underexposed"

    def test_the_restored_chip_is_still_builtin(self):
        chips.ensure_seeded()
        chips.reset("dark")
        assert chips.get("dark")["builtin"] is True

    def test_resetting_a_builtin_missing_from_disk_recreates_it(self, isolate_chips):
        chips.ensure_seeded()
        write_store(
            isolate_chips.chips,
            [c for c in chips.list_chips() if c["id"] != "dark"],
        )
        assert chips.reset("dark")["id"] == "dark"
        assert chips.get("dark") is not None

    def test_it_works_on_a_completely_empty_store(self, isolate_chips):
        assert chips.reset("dark")["label"] == "Dark or underexposed"
        assert ids_on_disk(isolate_chips.chips) == ["dark"]

    def test_it_leaves_the_other_chips_alone(self):
        chips.ensure_seeded()
        chips.upsert(make_chip(id="dogs", label="Dogs"))
        chips.update("blurry", label="Fuzzy")
        chips.reset("dark")
        assert chips.get("dogs")["label"] == "Dogs"
        assert chips.get("blurry")["label"] == "Fuzzy"

    def test_resetting_a_user_chip_raises_key_error(self):
        chips.upsert(make_chip(id="dogs", label="Dogs"))
        with pytest.raises(KeyError):
            chips.reset("dogs")
        assert chips.get("dogs")["label"] == "Dogs"

    def test_resetting_an_unknown_id_raises_key_error(self):
        with pytest.raises(KeyError):
            chips.reset("nope")

    def test_reset_is_idempotent(self):
        chips.ensure_seeded()
        chips.update("dark", label="My darks")
        assert chips.reset("dark") == chips.reset("dark")


# ── ensure_seeded() ───────────────────────────────────────────────────────────

class TestEnsureSeeded:
    def test_it_creates_the_file_with_every_builtin(self, isolate_chips):
        chips.ensure_seeded()
        assert ids_on_disk(isolate_chips.chips) == [c[1] for c in BUILTIN_PIN]

    def test_it_returns_what_a_later_read_sees(self):
        assert chips.ensure_seeded() == chips.load()

    def test_it_is_idempotent_on_disk(self, isolate_chips):
        chips.ensure_seeded()
        first = isolate_chips.chips.read_text()
        chips.ensure_seeded()
        chips.ensure_seeded()
        assert isolate_chips.chips.read_text() == first

    def test_a_complete_file_is_not_rewritten_at_all(self, isolate_chips):
        # Byte-identity, not just equal chip lists: a needless rewrite would
        # drop hand-added keys and churn the file on every server start.
        chips.ensure_seeded()
        data = json.loads(isolate_chips.chips.read_text())
        data["hand_added_note"] = "keep me"
        isolate_chips.chips.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        before = isolate_chips.chips.read_text()
        chips.ensure_seeded()
        assert isolate_chips.chips.read_text() == before

    def test_a_builtin_deleted_by_hand_is_restored(self, isolate_chips):
        chips.ensure_seeded()
        write_store(
            isolate_chips.chips,
            [c for c in chips.list_chips() if c["id"] != "receipt"],
        )
        chips.ensure_seeded()
        assert "receipt" in ids_on_disk(isolate_chips.chips)

    def test_a_restored_builtin_comes_back_with_its_shipped_definition(
        self, isolate_chips
    ):
        chips.ensure_seeded()
        write_store(
            isolate_chips.chips,
            [c for c in chips.list_chips() if c["id"] != "receipt"],
        )
        chips.ensure_seeded()
        assert chips.get("receipt")["label"] == "Receipt or invoice"
        assert chips.get("receipt")["query"]["prompts"] == ["receipt or invoice"]

    def test_a_customised_builtin_is_never_overwritten(self):
        chips.ensure_seeded()
        chips.update("dark", label="My darks", result_size=100)
        chips.ensure_seeded()
        stored = chips.get("dark")
        assert stored["label"] == "My darks"
        assert stored["result_size"] == 100

    def test_a_disabled_builtin_stays_disabled(self):
        # Otherwise every server restart would un-hide a tick the user turned off.
        chips.ensure_seeded()
        chips.update("dark", enabled=False)
        chips.ensure_seeded()
        assert chips.get("dark")["enabled"] is False

    def test_user_chips_survive_seeding(self, isolate_chips):
        chips.upsert(make_chip(id="dogs", label="Dogs"))
        chips.ensure_seeded()
        assert "dogs" in ids_on_disk(isolate_chips.chips)
        assert chips.get("dogs")["label"] == "Dogs"

    def test_seeding_over_a_corrupt_file_still_yields_the_builtins(
        self, isolate_chips
    ):
        isolate_chips.chips.write_text("{not json")
        chips.ensure_seeded()
        assert ids_on_disk(isolate_chips.chips) == [c[1] for c in BUILTIN_PIN]

    def test_every_seeded_builtin_is_marked_builtin(self):
        chips.ensure_seeded()
        assert all(c["builtin"] is True for c in chips.list_chips())

    def test_seeding_writes_chips_that_validate(self):
        # A seeded chip that load() would skip would make the tick row empty on
        # a fresh install and re-seed on every start.
        chips.ensure_seeded()
        for chip in chips.list_chips():
            assert chips.validate(chip) == chip


# ── the drift pin ─────────────────────────────────────────────────────────────

class TestBuiltinChipsHaveNotDrifted:
    """The equivalence contract with the pre-chip-store frontend ticks.

    Every expected value here is a literal written into this test file, NOT read
    from `chips.BUILTIN_CHIPS` — the whole point is that editing that constant
    turns this red. A failure here means "what this tick selects has changed";
    decide whether that was intended before updating the literal.
    """

    def test_there_are_exactly_six_builtins(self):
        assert len(chips.BUILTIN_CHIPS) == 6

    def test_the_ids_and_their_display_order_are_unchanged(self):
        chips.ensure_seeded()
        assert [c["id"] for c in chips.list_chips()] == [
            "accidental", "dark", "blurry", "screenshot", "receipt", "duplicate",
        ]

    @pytest.mark.parametrize(
        "order,chip_id,emoji,label,prompt", BUILTIN_PIN,
        ids=[c[1] for c in BUILTIN_PIN],
    )
    def test_a_builtin_selects_exactly_what_it_always_did(
        self, order, chip_id, emoji, label, prompt
    ):
        chips.ensure_seeded()
        chip = chips.get(chip_id)
        assert chip is not None, f"builtin {chip_id!r} is missing"
        assert chip["order"] == order
        assert chip["emoji"] == emoji
        assert chip["label"] == label
        assert chip["query"]["prompts"] == [prompt]
        assert chip["query"]["negatives"] == []
        assert chip["engine"] == "semantic"
        assert chip["result_size"] == 24
        assert chip["builtin"] is True
        assert chip["enabled"] is True

    def test_no_extra_builtin_has_appeared(self):
        chips.ensure_seeded()
        assert {c["id"] for c in chips.list_chips()} == {c[1] for c in BUILTIN_PIN}


# ── the ceiling shared with the route ─────────────────────────────────────────

class TestResultSizeCeilingMatchesTheRoute:
    def test_max_result_size_equals_the_servers_max_results(self):
        # chips.py declares MAX_RESULT_SIZE separately because server.py imports
        # chips, not the reverse — this test is the only thing holding the two
        # numbers together. server is imported lazily: importing it builds the
        # app and registers routes, but the CLIP/Chroma load lives in
        # load_everything(), which only runs under __main__.
        import server

        assert chips.MAX_RESULT_SIZE == server.MAX_RESULTS

    def test_a_chip_may_ask_for_the_full_route_maximum(self):
        import server

        assert chips.validate(make_chip(result_size=server.MAX_RESULTS))[
            "result_size"
        ] == server.MAX_RESULTS

    def test_the_default_result_size_is_within_the_ceiling(self):
        assert 1 <= chips.DEFAULT_RESULT_SIZE <= chips.MAX_RESULT_SIZE

    def test_the_id_charset_is_the_dismissal_ledgers_own_regex(self):
        # Not a copy: two regexes that could drift would orphan dismissals.
        import dismissed

        assert chips._ID_RE is dismissed._CATEGORY_RE


# ── atomic writes ─────────────────────────────────────────────────────────────

class TestAtomicWrite:
    def test_a_successful_write_leaves_no_temp_file(self, isolate_chips):
        chips.upsert(make_chip())
        chips.update("custom", label="Renamed")
        chips.delete("custom")
        chips.ensure_seeded()
        assert list(isolate_chips.chips.parent.glob("*.tmp")) == []

    def test_an_interrupted_write_leaves_the_previous_store_intact(
        self, isolate_chips, monkeypatch
    ):
        chips.upsert(make_chip(id="dogs", label="Dogs"))
        original = isolate_chips.chips.read_text()

        real_replace = os.replace
        monkeypatch.setattr(
            os, "replace",
            lambda *a, **k: (_ for _ in ()).throw(OSError("simulated crash")),
        )
        with pytest.raises(OSError):
            chips.upsert(make_chip(id="cats"))
        monkeypatch.setattr(os, "replace", real_replace)

        assert isolate_chips.chips.read_text() == original
        assert list(isolate_chips.chips.parent.glob("*.tmp")) == []
        assert chips.get("cats") is None

    def test_an_interrupted_first_write_creates_no_file_at_all(
        self, isolate_chips, monkeypatch
    ):
        real_replace = os.replace
        monkeypatch.setattr(
            os, "replace",
            lambda *a, **k: (_ for _ in ()).throw(OSError("simulated crash")),
        )
        with pytest.raises(OSError):
            chips.ensure_seeded()
        monkeypatch.setattr(os, "replace", real_replace)

        assert not isolate_chips.chips.exists()
        assert list(isolate_chips.chips.parent.glob("*.tmp")) == []

    def test_the_file_stays_hand_editable(self, isolate_chips):
        # Indented JSON with real emoji, not \u escapes — this file is meant to
        # be read and edited by a human.
        chips.ensure_seeded()
        text = isolate_chips.chips.read_text()
        assert "\n" in text
        assert "🌑" in text


# ── concurrency ───────────────────────────────────────────────────────────────

class TestLocking:
    def test_concurrent_writers_do_not_lose_each_others_chips(self):
        # Every mutator is read-modify-write, so without the lock the last
        # writer would clobber the chips added while it was working.
        def writer(n):
            chips.upsert(make_chip(id=f"chip-{n}", order=n))

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert {c["id"] for c in chips.list_chips()} == {
            f"chip-{n}" for n in range(12)
        }

    def test_a_mutator_can_run_while_the_lock_is_already_held(self):
        # _LOCK is reentrant on purpose: update() calls get() and upsert(),
        # both of which take it again.
        chips.upsert(make_chip(id="dogs"))
        with chips._LOCK:
            chips.update("dogs", label="Good dogs")
        assert chips.get("dogs")["label"] == "Good dogs"


# ── the on-disk envelope ──────────────────────────────────────────────────────

class TestSchemaVersionOnDisk:
    def test_a_written_store_declares_the_current_schema_version(self, isolate_chips):
        chips.ensure_seeded()
        assert json.loads(isolate_chips.chips.read_text())["schema_version"] == 1

    def test_a_missing_schema_version_reads_back_as_the_current_one(
        self, isolate_chips
    ):
        isolate_chips.chips.write_text(json.dumps({"chips": []}))
        assert chips.load()["schema_version"] == chips.SCHEMA_VERSION

    @pytest.mark.parametrize("version", ["1", None, 1.0, "one"])
    def test_a_non_integer_schema_version_falls_back_rather_than_raising(
        self, isolate_chips, version
    ):
        write_store(isolate_chips.chips, [], schema_version=version)
        assert chips.load()["schema_version"] == chips.SCHEMA_VERSION

    def test_a_future_schema_version_is_reported_rather_than_silently_rewritten(
        self, isolate_chips
    ):
        # load() reports what it found; a downgrade decision belongs to the
        # caller, not to a read.
        write_store(isolate_chips.chips, [], schema_version=99)
        assert chips.load()["schema_version"] == 99
