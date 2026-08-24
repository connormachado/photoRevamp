"""
Chip store — the saved-object registry for junk-cull filters
=============================================================
A "chip" is a saved photo-selection filter: the quick-filter ticks under the
search bar. This module owns their definitions; `chip_resolve.py` owns the one
path that turns a chip into photos, and `chip_stats.py` owns their run counters.

The chip list used to live only in the frontend (`SearchChips.jsx`'s `CHIPS`
array), which is why `dismissed.py` validates a category by SHAPE rather than
membership — it had no list to check against. A chip `id` IS the dismissal
ledger's category key, so `_ID_RE` is imported from `dismissed` rather than
re-declared: two regexes that could drift would silently orphan dismissals.

On disk (`photo_db/chips.json`):
    {"schema_version": 1, "chips": [ {...}, ... ]}

`load()`/`list_chips()`/`get()` NEVER write to disk, even on a missing or
corrupt file — same rule as config_store.py, and for the same reason recorded
in backend/CLAUDE.md: these modules are imported during pytest collection,
before any fixture has redirected the path, so a write-on-read would drop a
real file into the live, gitignored photo_db/. Only the mutators and the
explicit, server.py-startup-only `ensure_seeded()` touch disk.

Logic only — routes live in server.py.
"""

import json
import os
import tempfile
import threading
from pathlib import Path

from dismissed import _CATEGORY_RE as _ID_RE
from utils import DEFAULT_DB_PATH

CHIPS_PATH = DEFAULT_DB_PATH / "chips.json"
SCHEMA_VERSION = 1

# Upper bound on a chip's result_size, mirroring server.MAX_RESULTS. Declared
# here rather than imported because server.py imports this module, not the
# other way round; test_chips.py pins the two together.
MAX_RESULT_SIZE = 500
DEFAULT_RESULT_SIZE = 24

# Every engine `resolve()` can dispatch on. Only "semantic" exists: all six
# builtin ticks are CLIP text queries — there is no pixel-statistic or
# metadata-rule selection code anywhere in the photo path, so "blurry" and
# "dark" are prompts, not measurements. Adding an engine means adding an
# implementation in chip_resolve.ENGINES and a validator in QUERY_VALIDATORS
# below too.
ENGINES = ("semantic",)

# The six ticks as they behaved before the chip store existed, verbatim from
# SearchChips.jsx. These are the equivalence contract: a change here changes
# what a builtin selects, so tests/test_chips.py pins them against independent
# literals.
BUILTIN_CHIPS = [
    {
        "id": "accidental",
        "label": "Accidental photo",
        "emoji": "📷",
        "builtin": True,
        "enabled": True,
        "order": 0,
        "engine": "semantic",
        "query": {"prompts": ["accidental photo"], "negatives": []},
        "result_size": DEFAULT_RESULT_SIZE,
    },
    {
        "id": "dark",
        "label": "Dark or underexposed",
        "emoji": "🌑",
        "builtin": True,
        "enabled": True,
        "order": 1,
        "engine": "semantic",
        "query": {"prompts": ["dark or underexposed photo"], "negatives": []},
        "result_size": DEFAULT_RESULT_SIZE,
    },
    {
        "id": "blurry",
        "label": "Blurry or out of focus",
        "emoji": "💨",
        "builtin": True,
        "enabled": True,
        "order": 2,
        "engine": "semantic",
        "query": {"prompts": ["blurry or out of focus photo"], "negatives": []},
        "result_size": DEFAULT_RESULT_SIZE,
    },
    {
        "id": "screenshot",
        "label": "Screenshot or document",
        "emoji": "📄",
        "builtin": True,
        "enabled": True,
        "order": 3,
        "engine": "semantic",
        "query": {"prompts": ["screenshot or document"], "negatives": []},
        "result_size": DEFAULT_RESULT_SIZE,
    },
    {
        "id": "receipt",
        "label": "Receipt or invoice",
        "emoji": "🧾",
        "builtin": True,
        "enabled": True,
        "order": 4,
        "engine": "semantic",
        "query": {"prompts": ["receipt or invoice"], "negatives": []},
        "result_size": DEFAULT_RESULT_SIZE,
    },
    {
        "id": "duplicate",
        "label": "Duplicate scene",
        "emoji": "🔁",
        "builtin": True,
        "enabled": True,
        "order": 5,
        "engine": "semantic",
        "query": {"prompts": ["duplicate scene"], "negatives": []},
        "result_size": DEFAULT_RESULT_SIZE,
    },
]

# Reentrant: the mutators call load() while already holding the lock, same
# reason config_store._LOCK and motion_review._LEDGER_LOCK are RLocks.
_LOCK = threading.RLock()


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON to path atomically (temp file + os.replace). Mirrors
    dismissed._atomic_write_json / config_store._atomic_write_json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ── Validation ────────────────────────────────────────────────────────────────

def validate(chip) -> dict:
    """Return a normalised copy of `chip`, or raise ValueError naming the field.

    Defaults are filled in for every optional field, so a validated chip is
    always complete — callers downstream (resolve, the route, the frontend)
    never have to handle a missing key.
    """
    if not isinstance(chip, dict):
        raise ValueError(f"chip must be an object, got {type(chip).__name__}")

    chip_id = chip.get("id")
    if not isinstance(chip_id, str) or not _ID_RE.match(chip_id):
        raise ValueError(
            f"invalid chip id: {chip_id!r} (must match {_ID_RE.pattern}) — "
            "the id is also the dismissal ledger key"
        )

    label = chip.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"chip {chip_id!r}: label must be a non-blank string")
    if len(label) > 80:
        raise ValueError(f"chip {chip_id!r}: label must be 80 characters or fewer")

    emoji = chip.get("emoji", "")
    if not isinstance(emoji, str) or len(emoji) > 8:
        raise ValueError(f"chip {chip_id!r}: emoji must be a string of 8 characters or fewer")

    engine = chip.get("engine")
    if engine not in ENGINES:
        raise ValueError(
            f"chip {chip_id!r}: unknown engine {engine!r} (known: {', '.join(ENGINES)})"
        )

    result_size = chip.get("result_size", DEFAULT_RESULT_SIZE)
    # bool is an int subclass; a `True` here is a bug, not a size of 1.
    if not isinstance(result_size, int) or isinstance(result_size, bool):
        raise ValueError(f"chip {chip_id!r}: result_size must be an integer")
    if not 1 <= result_size <= MAX_RESULT_SIZE:
        raise ValueError(
            f"chip {chip_id!r}: result_size must be between 1 and {MAX_RESULT_SIZE}"
        )

    order = chip.get("order", 0)
    if not isinstance(order, int) or isinstance(order, bool) or order < 0:
        raise ValueError(f"chip {chip_id!r}: order must be an integer >= 0")

    return {
        "id": chip_id,
        "label": label,
        "emoji": emoji,
        "builtin": bool(chip.get("builtin", False)),
        "enabled": bool(chip.get("enabled", True)),
        "order": order,
        "engine": engine,
        "query": _validate_query(chip_id, engine, chip.get("query")),
        "result_size": result_size,
    }


def _validate_semantic_query(chip_id: str, query: dict) -> dict:
    """Semantic engine's query payload: single prompt only, no negatives yet.

    Split out of _validate_query so a new engine is a pure QUERY_VALIDATORS
    registration below — no edit to this function or to the dispatcher.
    """
    prompts = query.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise ValueError(f"chip {chip_id!r}: query.prompts must be a non-empty list")
    for prompt in prompts:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"chip {chip_id!r}: every prompt must be a non-blank string")
    negatives = query.get("negatives", [])
    if not isinstance(negatives, list):
        raise ValueError(f"chip {chip_id!r}: query.negatives must be a list")

    # The shape is a list so the schema can grow without a version bump,
    # but the v1 semantic engine implements single-prompt only. Multi-prompt
    # fusion and negative prompts have no defined semantics yet; accepting
    # them here would silently select photos by an untested rule.
    if len(prompts) != 1:
        raise ValueError(
            f"chip {chip_id!r}: multiple prompts are not supported in "
            f"schema_version {SCHEMA_VERSION} (got {len(prompts)})"
        )
    if negatives:
        raise ValueError(
            f"chip {chip_id!r}: negative prompts are not supported in "
            f"schema_version {SCHEMA_VERSION}"
        )
    return {"prompts": list(prompts), "negatives": []}


# Every engine _validate_query can dispatch a payload check to. Mirrors
# chip_resolve.ENGINES's shape: adding an engine's write-time validation is a
# dict entry here, not a growing if/elif. tests/test_chip_migration.py pins
# this set against chips.ENGINES and chip_resolve.ENGINES so the three can't
# drift apart.
QUERY_VALIDATORS = {"semantic": _validate_semantic_query}


def _validate_query(chip_id: str, engine: str, query) -> dict:
    """Dispatch to the engine's query validator.

    The isinstance check stays here, not in each validator: it's a generic
    "query is an object" contract, not a semantic-engine-specific rule.
    """
    if not isinstance(query, dict):
        raise ValueError(f"chip {chip_id!r}: query must be an object")

    validator = QUERY_VALIDATORS.get(engine)
    if validator is None:
        # Unreachable while chips.ENGINES == QUERY_VALIDATORS.keys() — validate()
        # checks engine membership in ENGINES first — but a new engine added
        # there without a validator here must fail loudly with this ValueError,
        # not a KeyError: load() only catches ValueError per-entry, and
        # ensure_seeded() calls load() at server startup, so a KeyError here
        # would take the whole server down instead of just dropping one chip.
        raise ValueError(f"chip {chip_id!r}: no query validator for engine {engine!r}")
    return validator(chip_id, query)


# ── Reading ───────────────────────────────────────────────────────────────────

def load() -> dict:
    """The whole store as {"schema_version": int, "chips": [...]}.

    Never writes, never raises: a missing or corrupt file reads back as an
    empty store. Invalid individual chips are skipped rather than poisoning
    the whole read — one hand-edited chip shouldn't blank the tick row.
    """
    with _LOCK:
        raw = None
        if CHIPS_PATH.exists():
            try:
                with open(CHIPS_PATH) as f:
                    raw = json.load(f)
            except (OSError, ValueError):
                # ValueError covers JSONDecodeError and UnicodeDecodeError.
                raw = None
        if not isinstance(raw, dict):
            return {"schema_version": SCHEMA_VERSION, "chips": []}

        # A scalar here (hand-edited `"chips": 5`) must degrade to an empty
        # store like every other corrupt shape — not raise. ensure_seeded()
        # runs at server startup, so a TypeError out of this function takes
        # the whole server down rather than showing an empty tick row.
        entries = raw.get("chips")
        if not isinstance(entries, list):
            entries = []

        chips = []
        for entry in entries:
            try:
                chips.append(validate(entry))
            except ValueError:
                continue
        chips.sort(key=lambda c: (c["order"], c["id"]))
        version = raw.get("schema_version")
        if not isinstance(version, int):
            version = SCHEMA_VERSION
        return {"schema_version": version, "chips": chips}


def list_chips(enabled_only: bool = False) -> list[dict]:
    """Every chip, sorted by (order, id). `enabled_only` is what the tick row
    asks for; the editor will want the full list."""
    chips = load()["chips"]
    if enabled_only:
        chips = [c for c in chips if c["enabled"]]
    return chips


def get(chip_id: str) -> dict | None:
    """One chip by id, or None. Does not raise on an unknown id — callers
    turn that into a 404."""
    for chip in load()["chips"]:
        if chip["id"] == chip_id:
            return chip
    return None


def known_ids() -> set[str]:
    """Every id in the store, plus every builtin id. The union matters for the
    dismissal migration: a builtin that hasn't been seeded to disk yet is still
    a chip id, and its dismissals must not be treated as orphans."""
    return {c["id"] for c in load()["chips"]} | {c["id"] for c in BUILTIN_CHIPS}


# ── Writing ───────────────────────────────────────────────────────────────────

def _save(chips: list[dict]) -> None:
    _atomic_write_json(
        CHIPS_PATH,
        {
            "schema_version": SCHEMA_VERSION,
            "chips": sorted(chips, key=lambda c: (c["order"], c["id"])),
        },
    )


def upsert(chip: dict) -> dict:
    """Add a chip, or replace an existing one with the same id.

    A chip that already exists keeps its `builtin` flag regardless of what the
    caller passes — builtin-ness is a property of where a chip came from, not
    something an edit can grant or revoke.
    """
    with _LOCK:
        validated = validate(chip)
        chips = load()["chips"]
        existing = next((c for c in chips if c["id"] == validated["id"]), None)
        if existing is not None:
            validated["builtin"] = existing["builtin"]
            chips = [c for c in chips if c["id"] != validated["id"]]
        chips.append(validated)
        _save(chips)
        return validated


def update(chip_id: str, **fields) -> dict:
    """Merge `fields` into an existing chip. Raises KeyError if it doesn't
    exist, ValueError if the result would be invalid.

    `id` and `builtin` are not editable: an id change would orphan the chip's
    dismissals, which is the single most damaging thing this module can do.
    """
    with _LOCK:
        current = get(chip_id)
        if current is None:
            raise KeyError(f"no such chip: {chip_id!r}")
        for locked in ("id", "builtin"):
            if locked in fields and fields[locked] != current[locked]:
                raise ValueError(
                    f"chip {chip_id!r}: {locked} cannot be changed"
                    + (" — it is the dismissal ledger key" if locked == "id" else "")
                )
        merged = {**current, **fields, "id": chip_id, "builtin": current["builtin"]}
        return upsert(merged)


def delete(chip_id: str) -> None:
    """Remove a user chip. Raises KeyError if unknown, ValueError if builtin.

    A builtin is permanent by design — `reset()` is the way back to its
    defaults. Deleting one would strand its dismissals with no chip to key them
    to and no way to get the tick back without a code change.
    """
    with _LOCK:
        current = get(chip_id)
        if current is None:
            raise KeyError(f"no such chip: {chip_id!r}")
        if current["builtin"]:
            raise ValueError(
                f"chip {chip_id!r} is builtin and cannot be deleted (use reset() "
                "to restore its defaults, or set enabled=False to hide it)"
            )
        _save([c for c in load()["chips"] if c["id"] != chip_id])


def reset(chip_id: str) -> dict:
    """Restore a builtin chip to its BUILTIN_CHIPS definition."""
    with _LOCK:
        default = next((c for c in BUILTIN_CHIPS if c["id"] == chip_id), None)
        if default is None:
            raise KeyError(f"no builtin chip: {chip_id!r}")
        return upsert(dict(default))


def ensure_seeded() -> dict:
    """Create chips.json from BUILTIN_CHIPS if it doesn't exist, and add any
    builtin missing from an existing file. Idempotent.

    Called ONCE from server.py's startup — never from a module import — so
    importing this module stays side-effect-free (see the module docstring).
    Restores a builtin that was removed by a hand-edit, but never overwrites
    one the user has customised.
    """
    with _LOCK:
        chips = load()["chips"]
        have = {c["id"] for c in chips}
        added = [validate(dict(c)) for c in BUILTIN_CHIPS if c["id"] not in have]
        if added or not CHIPS_PATH.exists():
            _save(chips + added)
        return load()
