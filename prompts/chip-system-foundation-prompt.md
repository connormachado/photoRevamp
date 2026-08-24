# Chip system foundation

Do NOT commit or push.

Goal: make a "chip" a first-class saved object — one schema, one store, one query path — and MIGRATE the existing hardcoded filter ticks into it so there is exactly ONE selection system in the app. This prompt must produce NO user-visible change: every existing tick must return identical results afterward. No new chips, no new UI.

Step 0 — Inspect and report (this is the highest-value part of this prompt; do not rush it). Follow CLAUDE.md's map-consult rule first. Then:

- THE EXISTING TICKS. Find every quick-filter tick (blurry / dark / accidental / duplicates / any others) and report, per tick: where it's defined (frontend array? backend route? both?), and EXACTLY how it selects photos. For each, classify the mechanism: a CLIP text query, a pixel statistic (blur variance, luminance), a metadata rule, or something else. Report the actual thresholds/parameters each one uses today, verbatim — those become the migrated chip's defaults, and without them written down the migration can't preserve behaviour.
- DISMISSALS. Report the exact shape of photo_db/dismissed.json, how it is keyed per-category, and how the over-fetch + post-filter query flow works. THIS IS A MIGRATION HAZARD: if categories are keyed by a string that changes when it becomes a chip id, existing "keep this one" decisions get orphaned. Propose an explicit old-key -> chip-id mapping.
- QUERY SEMANTICS. Do the existing ticks use a similarity THRESHOLD, a top-N, or a hard rule? Report per tick. (My stated preference for user chips is top-N, because a cosine threshold is unintuitive and can return zero results and look broken — but do not change existing tick behaviour in this prompt.)
- METADATA AVAILABILITY. This gates a future rot engine, so report it now: what per-photo metadata is actually available without re-reading files — is camera make/model, pixel dimensions, or any EXIF stored in ChromaDB metadata, or would a metadata rule need to read the derivative/original per photo? Report the real cost of a metadata-rule engine over the full library. Do NOT build that engine here.
- CONVENTIONS. Where the existing JSON ledgers live (stats.json, savings.json, dismissed.json, kept_videos.json) and their atomic-write pattern, so the chip store matches rather than inventing a new convention.

Report all of the above, then propose the concrete chip SCHEMA (field by field, with types and defaults) and the migration plan. PAUSE. I want to approve the schema before a line of it is written.

## Implementation

Phase 1 — schema + store:

1. Create the chip store (propose photo_db/chips.json unless a better home exists) with the project's existing atomic-write pattern and an explicit schema-version field so future migrations are possible.
2. Chip fields, at minimum: id (stable, never reused), label, builtin (bool), engine (an enum — start with only the engines the existing ticks actually need), a query payload appropriate to the engine (e.g. prompts[] and negatives[] for semantic; params{} for pixel/rule), a result-size setting, enabled, and display order.
3. Keep per-chip STATS in a SEPARATE sibling file (e.g. chip_stats.json), not inside the chip record. Rationale: editing a chip must never rewrite or risk losing its accumulated stats, and a stats update must never touch chip definitions. If you disagree, say why before implementing.
4. Built-in chips are marked builtin: their params are editable and resettable to default, but they cannot be deleted.

Phase 2 — ONE query path:

5. Introduce a single resolve function: given a chip, return the selected photos. It dispatches on engine. The existing tick selection logic MOVES into engine implementations — move it, do not copy it, and delete the old path once the new one is proven. Two code paths that both select photos is the exact failure this prompt exists to prevent.
6. The resolve path keeps the existing over-fetch + post-filter dismissal behaviour, now keyed by chip id.

Phase 3 — migration:

7. Seed the existing ticks as builtin chips using their verbatim current parameters, so results are unchanged.
8. Migrate dismissed.json keys to chip ids using the mapping approved in Step 0. The migration must be idempotent (running twice changes nothing) and must NEVER drop a dismissal entry. Back up the original file before rewriting it.

Phase 4 — frontend:

9. The tick row reads its chips from the store instead of a hardcoded list, preserving current labels and order exactly. No styling change, no new controls. If this reveals that ordering or labels were implicit in code, make them explicit data.

Pause points: before writing the approved schema (Step 0). Before rewriting dismissed.json (show me the key mapping and confirm the backup exists). Do not touch or delete any photo, video, or original anywhere in this prompt — this is metadata and selection logic only.

## Verification

- Build + lint clean; chips.json and chip_stats.json are valid JSON; dismissed.json is backed up and still valid.
- EQUIVALENCE PROOF (the real test): for each existing tick, capture the result list BEFORE the change and AFTER, and show they are identical. If any list differs, that is a regression, not an improvement — report it rather than accepting it.
- An existing dismissal still hides the same photo from the same tick after migration.
- There is exactly ONE code path that selects photos for a chip; the old per-tick paths are gone.

## Tests

Conditional — schema, migration, and dispatch logic all qualify: run /write-tests on the chip store and migration — assert schema validation rejects a malformed chip, that the dismissed-key migration is idempotent and loses nothing, that resolve() dispatches to the right engine and honours the result-size setting, that a builtin chip cannot be deleted, and that editing a chip does not mutate its stats. Include a test that would FAIL if a migrated tick's parameters drifted from the recorded defaults.

Save this prompt to prompts/chip-system-foundation-prompt.md.
