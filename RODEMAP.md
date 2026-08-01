# 📸 photo memory — roadmap

A personal photo search + curation tool built on CLIP embeddings + ChromaDB.
The goal: make 50,000 photos actually useful, searchable, and worth keeping.

Long-term this is a private, intelligent **home base for media** — not a replacement
for specialized editors. Editing is *assistive* (the tool proposes, you approve),
never a manual timeline you have to operate yourself.

> Status doc, not a wish list. `✅` means it exists on `main` and runs. Everything
> else is unbuilt — don't assume a file exists because it's described here.
> For how the built parts are wired, see `CLAUDE.md`.

---

## current stack

| layer | tech |
|---|---|
| embeddings | CLIP ViT-B/32 via `open_clip` |
| vector DB | ChromaDB (local, no account needed) |
| layout | UMAP (2-D projection) + sklearn Agglomerative clustering |
| video | ffmpeg via `imageio-ffmpeg`, pixel-diff motion detection |
| backend | Flask + Python, port 5001 |
| frontend | React + Vite, port 5173 |
| device | Apple Silicon Mac (torch device = `mps`) |

Library is ~49.6k photos indexed. Indexing targets the Photos **derivatives** cache
so it stays compatible with iCloud "Optimize Storage"; `apple_uuid` is stored in
ChromaDB metadata so AppleScript operations can find the real asset.

---

## repo structure

The principle holds: **each backend feature is its own file**, **each frontend view is
its own component**, and `server.py` stays thin — it just wires routes to the right
module. That way a feature can be worked on without touching unrelated code.

For the current actual tree, see `CLAUDE.md` — it's kept in sync with the repo.
Files named in this doc that don't appear there (`duplicates.py`, `clustering.py`,
`DuplicateReview.jsx`, `TimelineView.jsx`, `MoodBoard.jsx`) are **planned, not built**.

---

## features

### ✅ shipped

- **Natural language search** — type "golden hour sunset" and find every matching photo
  across the library. CLIP puts text and images in the same coordinate space, so no
  tagging required.
- **Image-drop search** — drop a photo in, get visually similar shots back.
- **Junk Hunt** — the "find my worst photos" idea, shipped. Six preset junk queries
  (accidental, dark, blurry, screenshot, receipt, duplicate scene) fire in parallel and
  merge into one deduped cull queue.
- **Search chips** — one-tap versions of those same six queries for normal searching.
- **Per-filter photo dismissal** — "not this one, not here." Hide a photo from one
  junk-cull chip without touching the photo or the delete counter; persists to
  `photo_db/dismissed.json`, scoped per category so hiding from "blurry" doesn't affect
  "dark". The control lives in the photo detail modal, above "Show in Photos".
- **Show in Photos** — reveals the exact asset in Photos.app. Note this ended up
  *different from the original "reveal in Finder" plan*: photos are indexed from the
  derivatives cache, whose paths Photos.app doesn't recognize, so it reveals by
  `apple_uuid` via AppleScript `spotlight media item id` instead of `open -R`.
  ⚠️ Intermittently activates Photos.app without landing on the exact photo — unfixed.
- **Delete counter + bulk add pad + reclaimed total** — tracks how many photos you've
  culled, persisted to `stats.json`. The number pad logs a batch at once ("I just cleared
  23 in Photos"). The card also shows one reclaimed-space headline summing exact photo
  sizes, estimated bulk culls and Climb Cutter's trims, with the breakdown on hover.
  This is a *counter*, not a delete-list export — see below for that.
- **Sync / prune** — drops ChromaDB rows whose files no longer exist on disk, so photos
  deleted in Photos.app stop showing up in search.
- **Update Library button** — in-app trigger for incremental indexing, so routine catch-up
  no longer needs the terminal. `embed_job.py` spawns `embed_photos.py` as a background
  subprocess (keeping CLIP out of the Flask process), writes progress to
  `photo_db/embed_status.json`, and refuses to start a second run while one is going. The
  UI polls `/api/embed/status` for a live done/total bar. Add-only — never wipes or
  re-embeds existing photos.
- **Results count toggle** — 12 / 24 / 48 / All.
- **HEIC → JPEG on the fly** — Chrome refuses to render HEIC natively; the `/full`
  endpoint converts in memory.

#### verified build log

Moved out of `CLAUDE.md` so it isn't re-read every session. Each entry records *how* the
work was proven. Everything below predates the test suite (added Aug 2026) and rests on a
manual run; `make test` now covers the pure logic and the route surface, but not the
render pipeline.

- ✅ Repo structure + CLAUDE.md / RODEMAP.md
- ✅ `server.py` refactored into thin routes + `backend/` modules
- ✅ HEIC → JPEG on-the-fly conversion in the `/full` endpoint
- ✅ "Show in Photos" button + `/reveal` AppleScript endpoint (reveals by `apple_uuid`)
- ✅ Results count toggle (12 / 24 / 48 / All)
- ✅ Delete counter (`stats.py`, `StatsContext`, `DeleteCounter.jsx`) with local persistence
- ✅ Bulk-delete number pad (`BulkAddPad.jsx`)
- ✅ Search prompt chips (`SearchChips.jsx`)
- ✅ Junk Hunt mode (parallel chip queries, deduped results)
- ✅ Graph View Phase 1 — `compute_layout.py` (UMAP + Agglomerative, full + incremental)
- ✅ Graph View Phase 2 — `graph_view.py` + `/api/graph-view`
- ✅ Graph View Phase 3 — `GraphView.jsx` canvas render (works; cosmetically unpolished)
- ✅ Climb Cutter Phase 1 — `video_motion.py` pixel-diff detection + ffmpeg cut/timelapse
- ✅ Climb Cutter Phase 2 + 2.5a/b — review room: queue, hover scrub, arrow-key stepping,
  draggable cut boundaries, verdicts persisted, reclaimed-bytes savings ledger
- ✅ Climb Cutter edit-boundary framework — `edit_boundaries.py` + `boundaryTypes.js`
  registry, regions as the source of truth, type picker toolbar + add (`c`) / remove
  (`delete`), type-agnostic export via `build_plan` → `render_plan`. The drop-only
  render output is byte-identical to before the refactor (md5-checked).
- ✅ Climb Cutter "speed" boundary type — green draggable region carrying a typeable
  magnitude, a 🐇/🐢 direction toggle and −/+ 0.5 steppers (`SpeedBlock.jsx` via the
  new `renderOverlay` slot). rabbit N = N× faster, turtle N = N× slower; magnitude
  clamps to [1, 20] and 1 is a no-op that stays on the fast render path. Audio is
  kept and time-stretched with the existing `atempo` chain. Cut and speed compose.
  Render-verified frame-by-frame against a burned-in counter, and on a real iPhone
  `.MOV` (upright, date + GPS intact, stereo audio).
- ✅ Preview parity for the Trimmed panel — `regions.buildPlan` mirrors
  `edit_boundaries.build_plan` via a per-type `toPieces` hook, and `SegmentVideo`
  varies `playbackRate` per piece, so the panel plays what the export will produce
  instead of only knowing how to skip cuts. `regions.outputDuration` is now derived
  from the same plan, so the header and the panel can't drift apart again.
  Browser-verified: rate flips 1 → 2 at the region boundary and back, turtle gives
  0.5, cuts still skip in the same pass.
- ✅ Climb Cutter export to Photos — `export_video.py` + `POST /motion-review/export`,
  equal-sized red/green domes. Smoke-tested end to end on one real
  clip: 59.18s → 32.93s, 176 MB → 37 MB, landed in Photos upright at the original's
  date (Feb 11 2026 9:31 PM) with GPS preserved and the original untouched. (The
  header save icon no longer triggers this — it saves a draft instead.)
- ✅ Climb Cutter draft-save — header save icon persists in-progress edits
  (`POST /motion-review/draft`, `drafts/<video_id>.json`), separate from the green
  dome's export/approve. Browser-verified end-to-end (real navigation away and back,
  not just a queue re-fetch): a saved boundary survived on both an unreviewed video
  and one already approved/exported — the latter caught a precedence bug (drafts were
  being ignored post-approval) and a frontend state-sync bug (draft saves weren't
  folded back into `videos`), both fixed. Also fixed along the way: the AppleScript
  date-setter's field-mutation order could silently roll into the wrong month, and
  GPS location was never actually being applied on export — see `backend/CLAUDE.md`
  for both.
- ✅ Climb Cutter file-picker ingest — `＋ Add video` in the queue header uploads clips
  from the macOS panel (`video_upload.py` + `POST /motion-review/upload`), replacing the
  CLI-only `video_motion.py --video …` path. Uploads are keyed by CONTENT hash, so
  re-picking a clip (even renamed) reuses the existing entry instead of adding a second
  few-hundred-MB copy. Synchronous: analysis runs ~0.35x realtime. Verified against a
  sandboxed `motion_review/` with Flask's test client on a 176 MB / 59s clip — first
  upload 14.8s `queued`, re-pick 1.1s `already_queued`, renamed re-pick likewise, a
  `.txt` rejected per-file without sinking the batch, and the queue row rendering
  correctly. Date + GPS confirmed intact on a real pick from the Photos section, and
  the route now reports `has_date`/`has_gps` on every upload so a stripped copy can't
  fail silently at export time.
- ✅ Preview-proxy concurrency fix — first-open of a video fired three concurrent
  transcodes into one temp path, publishing a corrupt proxy that then cached forever
  (the clip never played again). Serialised per video_id; reproduced deterministically
  before the fix and verified after, both in-process and with three parallel GETs
  against the live server. See `backend/CLAUDE.md`.
- ✅ Unified reclaimed-bytes total on the main page — `reclaimed_bytes` is now the derived
  sum of a `{photos_exact, photos_estimated, climb_cutter}` breakdown, so Climb Cutter's
  absolute-set mirror can no longer clobber photo bytes. Exact sizes come from Photos via
  a separate best-effort `size of media item id` call on `/reveal` (measured 1.8–6.4 MB
  originals against 60–90 KB derivatives — the Chroma `size_kb` route would have been
  ~50× low); count-only culls use the tunable `stats.AVG_PHOTO_BYTES`. Legacy files
  migrate on read by seeding the old scalar into `climb_cutter`. Verified the migration
  leaves the headline unchanged, that a reject→approve cycle doesn't disturb photo bytes,
  and the AppleScript lookup live. Shipped with a follow-up fix: `+` was passing React's
  click event as `exactBytes` and 500ing every write — see the root `CLAUDE.md` bullet.
  Browser-verified after: `+` moved 20 → 21 and 34 MB → 38 MB and survived a reload.
- ✅ **Test harness + `test-author` agent + security hardening** (Aug 2026). Went from zero
  tests to 542 pytest + 134 Vitest, `make test`. pytest with boundary-mocking fixtures in
  `tests/conftest.py`; Vitest 4 + RTL 16 (pinned by React 19 / Vite 8) with a global fetch
  stub, because anything under `StatsProvider` fetches on mount and retries 20×.
  **The Step-0 audit found live vulnerabilities, so hardening had to land first:** `/full`
  and `/thumbnail` accepted any absolute path (`?path=/Users/…/.ssh/id_rsa` returned the
  key), a `../` `video_id` made `save_draft` an arbitrary file overwrite and `_clear_draft`
  an arbitrary delete, and `CORS(app)` + no auth made all of it reachable from any page the
  user browsed. Fixed via `backend/safe_paths.py`, scoped CORS, `debug` off by default, and
  4xx-not-500 input coercion. Verified with teeth, not just green: removing
  `resolve_within_roots` turned 17 tests red with `/full` serving the secret file at 200;
  removing `safe_id_component` let a sentinel outside the tree get overwritten; removing the
  AppleScript quote-strip turned all 7 breakout tests red. Notably the status-code
  assertions alone stayed green for the write bug — only the filesystem assertions caught
  it, which is why those exist. `/write-tests <path>` runs the `test-author` subagent on any
  module; proven on `video_upload.py` (107 tests, 2 new bugs found).
- ✅ **Source-immutability guard + teeth re-audit** (Aug 2026). Closed the three open gaps
  from the test-suite review. `tests/test_source_immutability.py` (21 tests, 542 → 563)
  makes "the original is never deleted or modified" a *tested* property instead of a
  by-construction one, ahead of the reject-rework and purge features that would break it.
  Behavioral throughout: real functions against a real tmp source, fingerprinted by
  size + mtime_ns + md5, with a broadest test that never names the source — it asserts the
  only files that vanish across export → decision → proxy live under an app-owned root, so
  it keeps guarding files that don't exist yet. Because `conftest` blocks real
  subprocesses, a filesystem snapshot can't see an `ffmpeg -y … <source>`; the argv half
  covers that (no writing invocation names the source as an output). Every guarantee was
  re-proved by fault injection and the code restored to a byte-identical hash: weakening
  `resolve_within_roots` → 13 red, gutting `safe_id_component` → 5 red, unescaping the
  AppleScript uuid → 3 red, deleting the source after export → 6 red, appending to it in
  `record_decision` → 9 red. **No pre-existing test caught either source-touching fault.**
  One decorative-test finding, now recorded in `backend/CLAUDE.md`: the parametrized
  `*_refuses_a_traversing_id` route sweeps stay green with the guard removed. Bind host
  audited and clean — `app.run` passes no `host=`, so it is Werkzeug's `127.0.0.1` default,
  and no `--host` flag or Makefile override exists.

- ✅ **Per-filter photo dismissal** (Aug 2026). New `backend/dismissed.py` ledger (atomic
  write, per-category, in-memory cached — deliberately diverges from `stats.py`'s
  read-every-call style since this is on the search hot path) plus `search.search_text`'s
  `exclude_ids` over-fetch (`n + len(exclude_ids)`, capped at `OVERFETCH_CAP`) and three
  `/filters/*` routes. `SearchChips.jsx` chips gained a stable `id` (the ledger key,
  separate from the freely-rewordable `query`). 613 pytest + 135 Vitest green, lint/build
  clean. Live-verified end to end against the real server and library: dismissing backfills
  the grid without moving the delete counter, survives a server restart, stays isolated per
  category (dismissing under "blurry" left "dark" unaffected), Undo restores it, and a
  Junk Hunt dismissal removes a photo from every chip that surfaced it via `_sources`
  provenance. Follow-up in the same session: moved the hide control from a per-tile hover
  icon to the photo detail modal (above "Show in Photos", same button styling), auto-closing
  the modal on success since the photo it was showing no longer belongs in that view.

**Known defects (documented as `xfail(strict)`, awaiting a decision — don't silence them):**
- `build_plan` **drops footage** under an unrecognised region type: `get_type` returns None
  so no Pieces are emitted, but the cursor still advances past the span. The frontend
  reaches the same outcome and documents it, so the two halves agree — the open question is
  whether "I don't recognise this" should lose the user's footage at all. Only reachable
  from unsanitised regions (a draft written by a newer build); `sanitize_regions` strips
  unknown types on normal API traffic.
- **ffconcat quoting**: `export_video.py` and `video_motion.py` write `file '{path}'` lines,
  so a source path containing `'` or a newline can inject demuxer directives. Not reachable
  from the web app (`secure_filename` + extension allowlist strip both) but is from the CLI
  ingest path.
- `_safe_name` **loses the extension on a non-ASCII filename** — `日本語.mp4` parks as a file
  named `mp4`, and the queue row's `source_name` reads `mp4`. Cosmetic (ffmpeg probes by
  content) but the docstring promises otherwise.
- `_safe_name` **never caps length**, so a ~300-char filename fails the upload with a bare
  `OSError [Errno 63]` and no queue row for a perfectly valid video.

---

## 🗺 track: graph view

A spatial map of the library instead of a grid — photos placed by what they actually
look like, so you can see clusters rather than scroll a list.

**Decisions locked:**
- UMAP is the stable, cacheable map. It gets fit once and reused; coordinates are
  written back to ChromaDB metadata.
- d3-force is for *local* nudging at max zoom only — never fit globally, never written
  back to ChromaDB. The map must stay stable between sessions.

| phase | what | status |
|---|---|---|
| 1 | `compute_layout.py` — UMAP fit + cluster labels, full + incremental modes | ✅ built |
| 2 | `graph_view.py` + `/api/graph-view` | ✅ built |
| 3 | `GraphView.jsx` — canvas render, circular thumbs, cluster-colored rings, click-through | ✅ built, unpolished |
| 4 | zoom / pan / level-of-detail | ❌ not started |
| 5 | local overlap nudge (d3-force at max zoom) | ❌ not started |

**Known issues to fix before polishing anything:**
- The UMAP reducer was fit on a **2,000-photo sample** and never refit. All ~49.6k photos
  have coordinates (projected onto that reducer incrementally), but the map's *structure*
  comes from a 2k subset. This is the most likely reason the layout looks wrong. Refit on
  the full library first.
- Graph View currently plots only the **top 50 search results**, not the library. Making it
  an actual map is its own piece of work, separate from Phases 4/5.
- Clustering is Agglomerative at fixed k (broad=12, fine=60). Fixed k on a 2k fit is a
  guess; worth revisiting once the refit lands.

---

## 🧗 track: climb cutter

Assistive video trimming. The tool watches a video, proposes cuts where nothing is
happening, and you approve or reject — you never scrub a timeline by hand.

| phase | what | status |
|---|---|---|
| 1 | `video_motion.py` — pixel-diff motion detection, ffmpeg cut + timelapse export | ✅ built |
| 2 | `motion_review.py` + review room UI — queue, before/after, approve/reject | ✅ built |
| 2.5a/b | hover scrub, arrow-key stepping, draggable cut boundaries, edits persisted on approve | ✅ built |
| 3 | `motion_stats.py` — aggregate stats over decisions | ❌ not built |

**Calibration notes (learned the hard way):**
- Portrait video needs explicit handling — don't assume landscape.
- For bouldering footage the pixel-diff threshold wants to be **~3**, not the default 10.

**Already there, easy to miss:** per-decision logging exists (`decisions.jsonl` with cut
segments and saved-bytes estimates, plus per-video verdicts in `reviews/`), and there's a
reclaimed-bytes savings ledger that's idempotent across re-reviews. Phase 3 is the
*aggregator* on top of that, not the logging itself.

**Deferred ideas** (revisit in an editing phase): `c` to add a cut, `Delete` to remove one;
tiered fallbacks for robust playback.

---

## 🔜 next up — camera roll cleanup

#### duplicate & near-duplicate finder
Photos with a CLIP similarity above ~0.97 are almost certainly the same shot — burst mode,
accidental double-taps, the same photo sent over iMessage. Surface these in a side-by-side
keep/delete UI so you can blow through them fast. *(`duplicates.py` + `DuplicateReview.jsx` —
neither exists yet.)*

#### bulk select + export delete list
Select a batch in the UI, export their paths to a `.txt`. Review in Finder, then delete
manually. Safer than in-app deletion — one last look before anything goes away.
*(Not built. The existing bulk pad only increments the counter.)*

#### more like this
Click any photo → find every visually similar shot across the library. The image-search
endpoint already does the hard part; this turns it into a dedicated flow with pagination.

---

## 🎬 track: video understanding

**Sequencing decision: extend semantic understanding to video *before* building any
further editing UI.** This is the gate — editing features that don't understand content
are the manual-timeline trap.

Right now videos are indexed only as their static derivative stills — `embed_photos.py`
has no video handling at all. Real video search means sampling frames, embedding them,
and making a clip findable by what happens inside it ("the send", "the fall", "someone
laughing") rather than by its thumbnail.

High priority, wanted soon, larger effort than it looks.

---

## 🗓 later features

#### face clustering
Group every photo by person without naming anyone upfront. Face detection first, then
cluster by identity. End result: a folder per person, built automatically.

#### timeline clustering + event detection
A 3+ hour gap between photos is probably a new event. Auto-detect events ("Italy trip",
"Jake's birthday", "random Tuesday") from EXIF timestamps alone. No manual albums.

#### mood & aesthetic board
"Show me all my low-light shots." "Every photo with a blue palette." CLIP encodes mood and
color naturally — these are text queries with different framing, surfaced as a dedicated
mode with preset filters.

#### timeline anomaly detection
Find photos that are weirdly out of place — a 2019 photo inside a 2023 cluster, a
screenshot buried in vacation photos. Good for catching junk that slipped through.

---

## 🚀 big swings (later)

#### trip memory creation
Cluster photos by date range into trips. Score each for quality (sharpness, composition,
faces). Pick the best 20–30 per trip. Generate a small auto-album that plays over music.

Long-term: an agent that edits the trip — cuts, transitions, music sync — from the
narrative arc of the photos. You go on a trip, come home, and an edit is waiting.

---

## ❌ explicitly not doing

- **A full manual video timeline editor.** No Premiere/Final Cut competitor, no clip-dragging
  NLE. Rejected deliberately in favor of assistive, understanding-driven editing plus
  frictionless export. Don't resurrect this — if a feature request starts to look like
  "let the user manually arrange clips," it's the wrong direction.

---

## workflow for adding a new feature

1. **Backend first** — new file in `backend/` with the core logic, add a route to `server.py`
2. **Test the endpoint** — curl it to confirm the response shape
3. **Frontend component** — new component in `src/components/`, wire it to the endpoint
4. **Drop it into App.jsx** — add a nav entry or new view

Multi-phase features go one phase at a time via `/build-phase`: plan → implement → verify →
pause for human check. "Verify" means `make test` **plus** running it — the suite covers
pure logic and the route surface, not the ffmpeg render path or anything visual.
`/write-tests <path>` generates a suite for a module you're about to change.

**Never commit or push** — that's Connor's, manually. Enforced by hooks in `.githooks/`.
