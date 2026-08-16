Do NOT commit or push. Apple's Photos library is READ-ONLY in this prompt — open it immutable, never write to it, never let Photos be running against a database you have open for writing.
Goal: populate a per-photo date in ChromaDB metadata by joining Apple's Photos.sqlite on apple_uuid, so temporal features (starting with Time tide) have a real date to work from. Today ChromaDB's date_taken is empty on 56,606 of 56,612 rows because the app indexes derivatives with stripped EXIF.

Step 0 — Inspect and report:
- Consult `docs/CODEBASE_MAP.md` FIRST for where the relevant code lives (routes, modules, state owners, the nav guide) instead of re-exploring — it's navigation; CLAUDE.md stays the authority on gotchas/contracts. If the map is MISSING, HALT and tell me to run `/cartographer` myself — NEVER run `/cartographer` yourself (it's a ~200k in-session burn). If the map is present but seems to contradict the code, trust the code and flag the drift so I can regenerate.
- Read `backend/backfill_uuids.py` in full. This prompt is deliberately its sibling: same shape, same chunked-update pattern, same 5,000-row chunk size. MIRROR it rather than inventing a new script structure.
- Confirm the existing metadata key situation: is the key literally `date_taken`, is it empty-string or absent on the 56,606, and are there 6 rows with a REAL value? Report what those 6 contain — if they hold a usable date in a different format, decide explicitly whether to overwrite or preserve them, and say which.
- Locate the Photos library database path. It must come from the shared config store if Prompt 15 has shipped; if not, resolve it the same way the rest of the app anchors into the library and leave a clean seam. Do NOT hardcode a path with a username in it.
- Confirm on the live data before writing anything: the join `ZASSET.ZUUID` ↔ ChromaDB `apple_uuid` coverage, and that `ZDATECREATED` is non-null across the assets you'll use. Prompt 37 measured 100% / non-null — verify it still holds rather than trusting the earlier number.
- Report the storage format decision and recommend one: an ISO-8601 string is human-readable in the DB and sorts lexicographically; a Unix timestamp is cheaper to range-filter. Chroma metadata supports numbers, and Time tide will do range lookups over ~56k rows. Recommend, and PAUSE for my pick.
Report, propose the plan, and PAUSE.

Implementation:
1. Open Photos.sqlite READ-ONLY and IMMUTABLE (a `file:…?immutable=1` URI), exactly as the audit did. Never open it writable. If Photos.app holding the database causes a lock error even read-only, report it rather than working around it.
2. Read the mapping: ZUUID → ZDATECREATED for every asset. Convert the Core Data epoch by adding 978307200 (seconds since 2001-01-01, NOT 1970) — getting this wrong silently shifts the entire library 31 years and every downstream feature inherits the error.
3. Store the date in the chosen format on each ChromaDB row, keyed by apple_uuid. Write via chunked `collection.update()` at 5,000 rows per chunk (the repo's documented bulk limit), preserving every other metadata key — use the same `{**meta, …}` spread the layout write-back uses, so nothing already on the row is dropped.
4. Handle the misses explicitly rather than silently: any Chroma row whose apple_uuid finds no ZASSET match, and any asset with a null date, gets counted and REPORTED. If the count is not zero, stop and tell me the number before I assume the backfill is complete.
5. Make it RESUMABLE and IDEMPOTENT, mirroring the incremental spirit of the rest of the pipeline: re-running must not duplicate work or corrupt existing values, and it must pick up rows added since the last run. This is the same script that gets re-run after future indexing passes, so it can't be a one-shot.
6. Expose the date in the graph-view payload (`backend/graph_view.py` builds the per-photo dict) so Prompt 38 can consume it. One field, no other response changes.
7. Sanity-report the result: the min and max date across the library, and a histogram by year. Prompt 37 measured a range of 1999-12-31 → 2026-07-25 — if the result disagrees wildly, something is wrong with the epoch conversion and I want to see it before trusting the data.

Pause points: before the first write to ChromaDB metadata (56,612 rows) — confirm the format choice and show me the conversion verified against a few known photos. Back up `photo_db/chroma.sqlite3` first using SQLite's online backup from a read-only connection (NOT `cp` — the server may be holding the collection open and a plain copy can catch a torn write); `make stop` first if the server is running. Apple's Photos library is never written to.

Verification:
- Build + lint clean; the app still starts and search still works (metadata writes must not disturb embeddings).
- Spot-check at least 5 photos against what Apple Photos itself shows for them — the dates must match, not merely exist.
- Reported coverage: how many rows got a date, how many missed, and why. Zero unexplained misses.
- Min/max and the per-year histogram look plausible for a real camera roll (no 1970 cluster — that's the epoch bug's signature).
- Re-run the script: it completes, changes nothing, and reports zero new writes.
- The graph-view payload now carries the date field.

Tests (conditional — epoch conversion and chunking are exactly the logic worth guarding): run /write-tests on the converter and the backfill loop — assert the Core Data epoch conversion against known fixed values (including one that would land in 1970 if the offset were omitted, so the test fails loudly on that bug), that chunking never exceeds 5,000, that a row with no match is counted rather than written with a null, that existing metadata keys survive the update, and that a second run is a no-op. Mock both databases; do NOT open the real Photos library in tests.

Save this prompt to prompts/date-backfill-prompt.md.
