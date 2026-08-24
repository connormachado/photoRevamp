"""
Chip resolution — the ONE path that turns a chip into photos
=============================================================
Every chip selection in the app goes through `resolve()`. It dispatches on the
chip's `engine`; each engine implementation owns its own selection mechanism.

This is deliberately the only such path. Before the chip store, the tick row
and Junk Hunt each built their own `/search/text` call with a `category`
parameter, and the dismissal filtering lived inline in the route — two callers
constructing the same query by hand, which is how they drift. The old
`/search/text` category branch was MOVED here, not copied: it no longer exists
in server.py.

Adding an engine means an entry in `ENGINES` here AND in `chips.ENGINES`
(which is what validation checks against) — the two are held together by
tests/test_chips.py.

Logic only — routes live in server.py.
"""

import chip_stats
import dismissed
import search


def _resolve_semantic(chip, n, collection, model, tokenizer, device) -> list[dict]:
    """CLIP text query, top-N, minus this chip's dismissals.

    All six builtin ticks are this engine — "blurry" and "dark" are prompts
    describing what such a photo looks like, not pixel measurements. There is
    no similarity threshold anywhere: `search_text` returns the closest `n`,
    and `score` is display-only.

    The dismissal exclusion keeps the over-fetch + post-filter behaviour it had
    in the route: `search_text` fetches `n + len(exclude_ids)` (capped at
    `search.OVERFETCH_CAP`) and filters afterward, the minimum over-fetch that
    still guarantees a full page. The ledger is keyed by chip id, which is the
    same string the old `category` parameter carried.
    """
    exclude_ids = set(dismissed.get_dismissed(chip["id"]))
    return search.search_text(
        chip["query"]["prompts"][0], n, collection, model, tokenizer, device,
        exclude_ids=exclude_ids,
    )


ENGINES = {
    "semantic": _resolve_semantic,
}


def resolve(chip, collection, model, tokenizer, device, n=None) -> list[dict]:
    """Select the photos for `chip`. The single entry point.

    `n` overrides the chip's `result_size` when given — the chip's value is a
    default, not a cap. That is what lets the tick row honour the UI's 24/48
    count toggle and Junk Hunt ask for 48, exactly as they did before the
    store existed.
    """
    if n is None:
        n = chip["result_size"]
    engine = ENGINES.get(chip["engine"])
    if engine is None:
        # chips.validate() rejects an unknown engine on write, so reaching this
        # means chips.ENGINES gained a member without an implementation here.
        raise ValueError(f"no implementation for engine {chip['engine']!r}")
    results = engine(chip, n, collection, model, tokenizer, device)
    chip_stats.record_run(chip["id"], len(results))
    return results
