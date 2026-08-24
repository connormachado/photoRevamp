"""
Chip migration — seed builtins, re-key dismissals
==================================================
A rerunnable one-shot, in the style of backfill_dates.py / backfill_uuids.py.
Run it directly:

    .venv/bin/python3 backend/chip_migration.py            # report only
    .venv/bin/python3 backend/chip_migration.py --apply    # write

Two jobs:

1. Seed `photo_db/chips.json` from `chips.BUILTIN_CHIPS`, so the six ticks that
   used to live in SearchChips.jsx become saved objects with their verbatim
   parameters. Results are unchanged by construction — the prompts, order and
   result sizes are copied, not re-derived.

2. Re-key `photo_db/dismissed.json` from its old category keys to chip ids.

**The mapping is the identity mapping, and that is a finding, not an
assumption.** The old category string already WAS the chip id: the frontend
passed `chip.id` as `category` on every dismissal. `OLD_KEY_TO_CHIP_ID` is
written out explicitly anyway, because it is the place a future id rename has
to be recorded — without it, renaming a chip silently orphans every "keep this
one" decision the user ever made under the old name.

Safety properties, all three tested in tests/test_chip_migration.py:
  - **Idempotent** — a second run finds every key already a chip id and writes
    nothing new.
  - **Never drops an entry.** A key with no mapping and no matching chip is
    KEPT verbatim and reported as unrecognised. The total id count is asserted
    equal before and after; a mismatch aborts before anything is written.
  - **Backed up first.** The original file is copied to
    `dismissed.backup.<UTC>.json` before any rewrite.

This touches JSON ledgers only. No photo, video or original is read or written.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import chips
import dismissed

# Old dismissed.json category key -> chip id. Identity today (see the module
# docstring); a future rename adds a real pair here rather than orphaning the
# old key's dismissals.
OLD_KEY_TO_CHIP_ID = {
    "accidental": "accidental",
    "dark": "dark",
    "blurry": "blurry",
    "screenshot": "screenshot",
    "receipt": "receipt",
    "duplicate": "duplicate",
}


def _utc_stamp() -> str:
    """`%Y%m%dT%H%M%SZ`, matching photo_db/chroma_backup_<stamp>.sqlite3."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_dismissed(stamp: str | None = None) -> Path | None:
    """Copy dismissed.json beside itself as dismissed.backup.<UTC>.json.

    Returns the backup path, or None if there was nothing to back up.
    """
    source = dismissed.DISMISSED_PATH
    if not source.exists():
        return None
    target = source.with_name(f"dismissed.backup.{stamp or _utc_stamp()}.json")
    shutil.copy2(source, target)
    return target


def plan_dismissal_migration() -> dict:
    """Work out what re-keying would do, without touching disk.

    Returns {"before", "after", "renames", "unrecognised", "total_before",
    "total_after", "changed"}.
    """
    try:
        with open(dismissed.DISMISSED_PATH) as f:
            raw = json.load(f)
    except (FileNotFoundError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    before = {k: list(v) for k, v in raw.items() if isinstance(v, list)}
    known = chips.known_ids()

    after: dict[str, list[str]] = {}
    renames: list[tuple[str, str]] = []
    unrecognised: list[str] = []

    for old_key, ids in before.items():
        new_key = OLD_KEY_TO_CHIP_ID.get(old_key, old_key)
        if new_key != old_key:
            renames.append((old_key, new_key))
        elif new_key not in known:
            # No mapping and no chip by that name. Keep it verbatim: a chip
            # that hasn't been created yet, or one whose rename nobody recorded
            # here, must not cost the user their decisions.
            unrecognised.append(old_key)
        # Merge rather than overwrite, in case two old keys map to one chip.
        merged = set(after.get(new_key, [])) | set(ids)
        after[new_key] = sorted(merged)

    return {
        "before": before,
        "after": after,
        "renames": renames,
        "unrecognised": unrecognised,
        "total_before": sum(len(set(v)) for v in before.values()),
        "total_after": sum(len(v) for v in after.values()),
        "changed": {k: sorted(set(v)) for k, v in before.items()} != after,
    }


def migrate_dismissals(apply: bool = False) -> dict:
    """Re-key dismissed.json to chip ids. Idempotent; never drops an entry.

    Aborts before writing if the id count would change — that can only happen
    if two old keys merge into one chip id, and losing a dismissal silently is
    worse than failing loudly.
    """
    plan = plan_dismissal_migration()

    if plan["total_after"] < plan["total_before"]:
        raise RuntimeError(
            "refusing to migrate: id count would drop from "
            f"{plan['total_before']} to {plan['total_after']}"
        )

    plan["applied"] = False
    if apply:
        # Back up UNCONDITIONALLY, before deciding whether a rewrite is even
        # needed. The identity mapping means today's run rewrites nothing, but
        # a backup that only appears on the runs that change something is a
        # backup you can't rely on — and the cost is one 200-byte copy.
        backup = backup_dismissed()
        plan["backup"] = str(backup) if backup else None

        if plan["changed"]:
            if backup is None:
                raise RuntimeError("refusing to migrate: dismissed.json has no backup")
            dismissed._atomic_write_json(
                dismissed.DISMISSED_PATH,
                {k: sorted(v) for k, v in plan["after"].items() if v},
            )
            dismissed.reload()
            plan["applied"] = True

    return plan


def migrate(apply: bool = False) -> dict:
    """Seed chips, then re-key dismissals. The whole migration."""
    seeded = chips.ensure_seeded() if apply else chips.load()
    return {
        "chips": seeded["chips"],
        "dismissals": migrate_dismissals(apply=apply),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (default is a dry-run report)")
    args = parser.parse_args()

    result = migrate(apply=args.apply)
    plan = result["dismissals"]

    print(f"chips.json: {len(result['chips'])} chips "
          f"({'seeded' if args.apply else 'dry run — not written'})")
    for chip in result["chips"]:
        prompt = chip["query"]["prompts"][0]
        print(f"  {chip['order']}  {chip['id']:11s} {chip['emoji']} "
              f"{chip['label']:24s} -> {prompt!r}  n={chip['result_size']}")

    print(f"\ndismissed.json: {plan['total_before']} ids across "
          f"{len(plan['before'])} categories")
    for key, ids in sorted(plan["before"].items()):
        target = OLD_KEY_TO_CHIP_ID.get(key, key)
        arrow = "==" if target == key else "->"
        print(f"  {key:11s} {arrow} {target:11s}  {len(ids)} ids")
    if plan["renames"]:
        print(f"  renames: {plan['renames']}")
    if plan["unrecognised"]:
        print(f"  unrecognised (KEPT, not dropped): {plan['unrecognised']}")
    print(f"  total after: {plan['total_after']} "
          f"({'changed' if plan['changed'] else 'no change needed'})")
    if plan.get("backup"):
        print(f"  backup: {plan['backup']}")

    if plan["total_after"] != plan["total_before"]:
        print("\nWARNING: id count changed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
