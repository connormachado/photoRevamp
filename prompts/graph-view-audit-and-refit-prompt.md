# Graph View audit + full UMAP refit

## Original request (verbatim)

Do NOT commit or push. Phases 0 and 1 are READ-ONLY / additive. Do NOT run the refit until I explicitly approve it.
Goal: (a) produce a written, accurate inventory of what Graph View is today, (b) answer one blocking question for a follow-on feature (is a per-photo DATE available?), and (c) after my approval, refit the UMAP reducer on the FULL library with a backup and a before/after quality comparison — because the current layout was fit on ~2,000 of ~49,605 photos and the remainder were projected in via .transform(), which is the root cause of the poor layout.

Step 0 — Inspect and report (READ-ONLY; change nothing):
- Consult `docs/CODEBASE_MAP.md` FIRST for where the relevant code lives (routes, modules, state owners, the nav guide) instead of re-exploring — it's navigation; CLAUDE.md stays the authority on gotchas/contracts. If the map is MISSING, HALT and tell me to run `/cartographer` myself — NEVER run `/cartographer` yourself (it's a ~200k in-session burn). If the map is present but seems to contradict the code, trust the code and flag the drift so I can regenerate.
- Also open `backend/CLAUDE.md` explicitly (it does NOT auto-load from the frontend) and read the UMAP / AgglomerativeClustering caveats.
- INVENTORY. Read backend/compute_layout.py, backend/graph_view.py, and photo-search/src/components/GraphView.jsx (or wherever it lives) and report, as a plain table I can keep:
    - What the GET /api/graph-view response contains PER PHOTO. Exact field names. Specifically: uuid, x, y, broad cluster id, fine cluster id, thumbnail reference — and CRITICALLY, is there any DATE / timestamp field? THIS IS A BLOCKING QUESTION for the next feature.
    - If NO date is present: where could one come from cheaply — ChromaDB metadata, EXIF on the derivative, the derivative filename, or Apple Photos? Report the cheapest reliable source and roughly what a one-time backfill would cost. Do NOT build the backfill in this prompt.
    - How many photos currently have coordinates vs. how many are in ChromaDB. Report the gap if any.
    - What the frontend actually renders today and what it does NOT: confirm whether zoom/pan, level-of-detail, and overlap resolution exist (I believe they do not — no wheel/pan/transform handlers, no d3 in package.json). Confirm the canvas is a fixed size.
    - Whether cluster IDs (broad/fine) are PERSISTED or REFERENCED anywhere outside compute_layout.py — saved queries, favorites, any JSON ledger. This matters because a refit REASSIGNS cluster ids: cluster 7 before is not cluster 7 after. If anything stores a raw cluster id, flag it as a migration hazard.
    - What exactly `--full-refit` touches (which files under photo_db/models/, which records get rewritten), whether UMAP's random_state is PINNED, and your best estimate of runtime + peak RAM for ~49,605 x 512 vectors on Apple Silicon CPU.
Report all of the above, propose the phased plan, and PAUSE. I want the inventory in hand before anything is written.

Phase 1 — Backup and reproducibility (do this BEFORE the refit; safe and additive):
1. Copy photo_db/models/ to a timestamped sibling backup directory. Verify the copy is complete and report its size.
2. Export the CURRENT coordinates + cluster ids for every photo to a timestamped JSON snapshot under photo_db/. This is the rollback artifact — if the new layout is somehow worse, we can restore both the model and the coords.
3. Write a short, explicit RESTORE procedure into the snapshot's sibling README (or a comment) so a future session can roll back without re-deriving it.
4. PIN UMAP's random_state to a fixed constant if it isn't already, and say what value you used. Rationale: an unpinned reducer produces a different map on every refit, which destroys spatial memory — the whole point of a map is that things stay where I learned they are.

Phase 2 — The refit (DO NOT RUN UNTIL I APPROVE; pause here and ask):
5. Run compute_layout.py --full-refit: fit the reducer once on the FULL set of embeddings, then write coordinates and re-cluster (broad_k=12, fine_k=60 unless we agree otherwise).
6. PROVE it improved rather than merely changed. Report, before vs after: the coordinate bounding box, the mean and median nearest-neighbour distance, the distribution of fine-cluster sizes (min/median/max), and the size of the largest cluster as a share of the library. A layout fit on 2k and projected should look measurably more collapsed than a full refit — if the numbers DON'T improve, say so plainly rather than declaring success.
7. Report whether any cluster-id migration hazard from Step 0 actually needs handling.

Phase 3 — Verify (after the refit):
8. Spot-check semantics: pick 3 obviously-distinct concepts (e.g. climbing, food, screenshots), fetch a handful of each via the existing text search, and confirm each group's coordinates are tightly grouped and the three groups are separated. Report actual numbers.
9. Confirm the frontend still renders, hit-testing still works, and search-results-at-UMAP-coords still land sensibly.
10. Confirm day-to-day incremental indexing still uses .transform() against the NEW model (fit once on the full library, project new photos thereafter) — do not make every new photo trigger a refit.

Pause points: before running the refit (Phase 2) — it rewrites all ~49,605 coordinates and overwrites the persisted model. Before overwriting anything under photo_db/models/ confirm the Phase 1 backup exists. Never delete the backup or the coord snapshot in this prompt.

Verification:
- The inventory table is written and accurate; the DATE question is answered definitively.
- Backup + coord snapshot exist and a restore procedure is documented.
- Before/after layout metrics are reported side by side, with an honest verdict on whether it improved.
- Build + lint clean; the Graph View page still loads and renders.

Tests (conditional — this touches coordinate-writing and clustering logic): run /write-tests on the layout-writing path — assert that a full refit writes a coordinate for every embedded photo, that no photo is silently dropped, that random_state is pinned so two runs on the same input produce identical coordinates, and that the projection path (.transform()) is used for incremental additions rather than a refit. Mock UMAP where a real fit would be too slow.

Save this prompt to prompts/graph-view-audit-and-refit-prompt.md.

---

## Amendments (issued at plan approval, folded in before Phase 1 began)

These were added after the audit surfaced the `pdist` blocker. Recorded here so the on-disk
record matches what was actually run, not just the original request.

**AMENDMENT 1 — Diagnose the 8,308, don't just assume the refit fixes them.**
Phase 3 step 14 verifies those photos now have coordinates, but nothing anywhere asks WHY
they lack them. Move that question into Phase 1 as a read-only investigation and report the
CAUSE before the refit runs. If they're simply photos that no incremental run ever covered,
the refit fixes them and step 14 passes. But if they're being excluded by a filter — media
type, a path pattern, videos, an unreadable derivative — then full_fit won't give them
coordinates either, and I'd be finding that out at the end of a 30-minute job instead of
before it. Report the cause and say explicitly whether you expect the count to go to zero.

**AMENDMENT 2 — The targeted rollback is incomplete; fix it or document it.**
The snapshot captures 4 fields for the 48,304 rows that currently have a layout. After the
refit, all 56,612 rows will carry coordinates. Replaying the snapshot therefore restores
48,304 rows and leaves 8,308 rows holding NEW coordinates the snapshot has no entry for —
so "targeted rollback" would not actually return the DB to its prior state. Either (a) have
the targeted-rollback procedure explicitly clear those 4 metadata keys for any id present
post-refit but absent from the snapshot, or (b) state plainly in LAYOUT_RESTORE_<UTC>.md
that targeted rollback is APPROXIMATE and the full chroma_backup restore is the only true
revert. Either is fine — pick one and make the restore doc honest about which.

**AMENDMENT 3 — Record the connectivity change in backend/CLAUDE.md, and fix the stale count.**
You're right that "Ward / Agglomerative" stays accurate, but "kNN-constrained" is a new
load-bearing fact: clusters are now spatially contiguous in the 2-D map rather than free-form
in that space. That's a semantic change, and layout_meta.json is not where a future session
will look for it — the gotcha file is. Add a short note to backend/CLAUDE.md covering: that
connectivity is now passed, WHY (the dense pdist path allocates ~11.9 GiB at this library
size and must stay unreachable), what it means for cluster shape, and the connectivity_k
value. While you're in there, CLAUDE.md's "~49.6k photos indexed" figure is stale — the real
count is 56,612 vectors with 48,304 laid out. Correct it. Show me both diffs before writing;
CLAUDE.md edits always get my eyes.

**AMENDMENT 4 — The benchmark extrapolation is super-linear. Say so.**
UMAP is not linear in n: NN-descent is roughly n log n and the optimization epochs scale with
n as well. A --limit 8000 timing extrapolated linearly to 56,612 will UNDERESTIMATE. Report
the benchmark as a LOWER BOUND, not a forecast, and give me a rough super-linear extrapolation
alongside the raw number so I know what I'm actually committing to. Also note whether the
running server's memory footprint distorted the peak-RSS reading during the benchmark.

**AMENDMENT 5 (non-blocking, just write it down) — n_neighbors=15 is an unexamined knob.**
The connectivity graph's k shapes the resulting clusters. If the fine clusters come out
stringy or chained — a known artifact of Ward with sparse connectivity — that is a signal to
RAISE k, not evidence that the refit failed. Put that one line in the Phase 2 report so a
disappointing first look doesn't get misread as a bad refit. Do not tune it pre-emptively.

**Unchanged and still in force:**
- No git operations at any point. No commit, no push. I commit manually.
- HARD STOP after Phase 1. Do not start the refit. Report backup sizes, the snapshot row
  count, the 8,308 cause, and the benchmark, then wait for a second explicit approval.
- Apple's Photos library and photo_db/models/ are not written to before the Phase 1 backups
  are verified to exist.
