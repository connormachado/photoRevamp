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

Library is 56,612 photos indexed. Indexing targets the Photos **derivatives** cache
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

- ✅ **Reject/remove with teeth** (Aug 2026). Reject used to leave the row in the queue
  forever wearing a badge; there was no dequeue path in the repo at all, and an upload's
  few-hundred-MB working copy was never freed. New `backend/queue_removal.py` +
  `POST /motion-review/remove`, fired by a confirm popover on the red dome *after* the
  existing `/decision` call — two requests on purpose, so the verdict outlives the entry.
  The whole design turns on provenance, which the proposal schema didn't record: uploads
  and hand-fed CLI paths were indistinguishable, and `apple_uuid` is filename-stem garbage
  (`IMG_8883.mov` → `"IMG"`). `process_video` now takes `owned=`, set True by the upload
  route alone, and `_owned_source` demands both that flag **and** containment inside
  `uploads/` via `resolve_within_roots` — so the flag can never authorise a delete on its
  own, and a symlink out of `uploads/` resolves before comparing and fails. Legacy
  proposals with no flag infer it from the path; verified against the live queue, where the
  two real uploads read `owned: true` and the Photos-library original reads `false`.
  Freed bytes are *reported*, not credited to `stats.json`: the `climb_cutter` slot is an
  absolute set of `sum(savings.per_video)`, so an accumulator there would be wiped by the
  next verdict — and an upload copy is a duplicate of a file still in Photos, so counting
  it beside real photo culls would overstate the headline. Verified by 30 new tests plus a
  sandboxed run of the real routes against a real 176 MB clip: upload → reject → remove
  freed 280 MB (copy + derivatives) and left `reviews/` and every prior `decisions.jsonl`
  line intact; the same round-trip on an external source freed 104 MB of derivatives and
  left the source byte- and mtime-identical. `tests/test_source_immutability.py` grew
  removal into the lifecycle it sweeps rather than being loosened — its `_app_owned`
  boundary already described exactly this rule.

- ✅ **Settings modal shell** (Aug 2026). Hamburger (`☰`) button in the header next to the
  title opens `components/settings/SettingsModal.jsx` — left tab list + right content
  pane, tabs registered in `settings/tabs.js` as `{id, label, component}` so a real tab is
  a one-line swap of its file. All seven tabs (Storage, Theme, Photos Library, Export
  Defaults, Motion Detection, About, Shortcuts) ship as `StubTab` placeholders — no
  behavior yet, this is scaffolding for later prompts to drop into. Overlay/card chrome
  reuses `App.jsx`'s photo-detail Modal; Esc + outside-click + cleanup reuses the
  `BulkAddPad`/`VerdictButtons` listener pattern rather than that Modal's (which lacks
  Esc and scroll-lock). Body-scroll-lock is new. Build + lint clean (lint shows only the
  12 pre-existing errors). **Not click-verified in a real browser** — no headless-browser
  driver (`chromium-cli`/Playwright) was available in the session sandbox; verified via a
  headless-Chrome `--dump-dom` confirming a clean initial mount with no console errors,
  plus code-level review against the two reused patterns above. Manual click-through in
  the actual app is still outstanding.

- ✅ **"Remove from queue" — savings-preserving dequeue** (Aug 2026). A per-video action
  distinct from Reject, for videos already exported: frees the owned working copy via
  `queue_removal.remove_from_queue` without ever calling `/motion-review/decision`, so
  the video's reclaimed-bytes credit is kept rather than retracted. Turned out to need
  no backend changes — `remove_from_queue` already never touched `savings.json`; only
  Reject's preceding `/decision` call did. Frontend: a ghost button in `VerdictButtons.jsx`
  gated on `exportedAt`, its own confirm popover (mutually exclusive with Reject's), wired
  to a new `removeOnly` handler in `MotionReviewApp.jsx`. 653 pytest + 168 Vitest green,
  build/lint clean. New tests exercise the real `record_decision` + `remove_from_queue`
  combo directly (export → remove keeps credit; reject still retracts; one video's
  removal doesn't disturb another's credit) plus a mutation-tested `VerdictButtons`
  suite confirming the two callbacks (`onRemoveOnly` vs `onRejectAndRemove`) can't be
  swapped without failing loudly.

- ✅ **HTTP Range support confirmed on `/motion-review/source`** (Aug 2026). Investigated
  per a prompt asking to add 206/`Content-Range` streaming so the review-stage `<video>`
  can scrub a multi-GB proxy without downloading it whole. Needed no code change — Flask
  3.1's `send_file(conditional=True)` default already handles it (live-verified: 206 +
  correct `Content-Range` + a byte-exact partial body, via Werkzeug's seek-based
  `_RangeWrapper`, not a full in-memory read), and the traversal guard is untouched by a
  `Range` header. Added `TestSourceRangeSupport` (`tests/test_route_security.py`) as the
  regression net that didn't exist before.

- ✅ **Non-blocking video export** (Aug 2026). `POST /motion-review/export` no longer blocks
  the Flask request thread on the ffmpeg re-encode — it now returns 202 immediately with a
  job id, and a new `backend/export_job.py` runs render → import → reveal on a background
  **thread** (not a subprocess, unlike `embed_job.py` — export's heavy work already
  releases the GIL via `subprocess`/`osascript`, and a thread keeps it inside the test
  suite's in-process monkeypatches and lets it share `motion_review._LEDGER_LOCK` with
  `/decision`). New `GET /motion-review/export/status` is polled every second by
  `MotionReviewApp.jsx`, which shows a real phase + percent (`VerdictButtons.jsx`) instead
  of a static "a few seconds" string. Staleness is judged by boot id (not pid, unlike
  `embed_job`, since macOS pid reuse could otherwise wedge the guard forever) plus a dead
  in-process thread handle and a wall-clock ceiling — any one downgrades a stuck job to
  `failed` and unblocks a fresh export. One export globally at a time; `/decision` and
  `/remove` now 409 while one is in flight, closing a race non-blocking export would
  otherwise open (a reject landing mid-render could otherwise fight `_apply_savings`, or a
  remove could unlink a source out from under a running ffmpeg). `export_video.render_plan`
  gained an opt-in `progress_cb` (real ffmpeg `-progress pipe:1` percent, scaled to the
  plan's OUTPUT duration so a sped-up region can't read over 100%) — passing none
  reproduces the exact pre-existing argv, verified both by a byte-for-byte argv pin in
  `tests/test_export_args.py` and by rendering one real clip twice (with and without the
  callback) and md5-comparing the outputs, which matched. 683 pytest (+26: 16 in the new
  `tests/test_export_job.py`, 6 in a new `TestProgressReporting` class, 4 route-level 409
  checks in `tests/test_input_validation.py`) + 168 Vitest green,
  build clean, lint at 13/12 pre-existing errors (one new
  `react-hooks/set-state-in-effect` on the completion effect — same category the codebase
  already tolerates elsewhere in this file, not a new kind of finding). Not yet
  live-verified against a real multi-minute export in the browser (kickoff/poll/guard logic
  is tested; the actual UI progress bar against Photos.app is a human follow-up).

- ✅ **Review-stage timeline zoom + pan** (Aug 2026). `CutTimeline` gained a `pixelsPerSecond`
  zoom (min = fit-the-whole-clip, max = capped so the viewport never shows less than ~3s at
  once — a user-requested ceiling, not a per-frame pixel budget), anchored on the playhead so
  the frame under it never jumps, plus native-scroll panning, a click/drag-to-jump overview
  strip that's always visible (not just once actually panned), an adaptive timestamp ruler
  (`niceTickInterval` re-densifies its labels as you zoom), and a zoom-multiplier readout
  ("5.0x"). New pure module `timelineScale.js` (38 Vitest tests) holds the one time<->pixel
  mapping + zoom bounds; regions were already stored as timestamps in seconds, so no
  migration was needed — the "pixels vs timestamps" fork this feature was gated on resolved
  clean on inspection. One real bug found and fixed mid-build: a negative CSS margin, used to
  visually close a padding gap above the zoom row, caused the (invisible but still
  hit-testable) scroll viewport to sit on top of the slider/±buttons and silently eat every
  click — fixed by moving the hover tooltip's spillover to below the track instead of
  splitting reserved space above/below (see the motion-review `CLAUDE.md` for the durable
  version of this gotcha). Build/lint clean, 206 Vitest green. Not yet live-verified in a
  running browser — no dev server/library was available in-session.

- ✅ **ffconcat injection fix** (Aug 2026). All three `file '{path}'` writers
  (`export_video._concat_demuxer_cmd`, `video_motion.make_trimmed_clip`,
  `make_cuts_timelapse`) now go through new `backend/ffconcat.py` instead of a bare
  f-string. Refuses paths containing `\n`/`\r`/`\x00` (no ffconcat representation exists);
  for `'` or `\`, stages a deterministic same-content symlink under a safe name in the
  render's own temp dir and writes that path instead of escaping — sidesteps needing a
  correct reading of ffmpeg's quoting rules entirely. Flipped the `xfail(strict)` on
  `test_a_quote_in_the_source_path_cannot_inject_a_directive` to a real pass; added 3 more
  tests (CLI-reachable writer, newline refusal, alias reuse across pieces). Verified by
  fault injection (reverted the fix, confirmed the test went RED, restored, confirmed
  green) and empirically by rendering real clips named `cl'ip.mov` / `back\slash.mov`
  through `export_video.render_plan` and playing both back clean. `make test` (687 passed,
  4 xfailed — unchanged) and `npm run lint` (10 pre-existing errors, unchanged) both green.

- ✅ **Fixed-viewport, no-scroll review-stage layout** (Aug 2026). The editor no longer
  scrolls at any supported window size — the previous `overflowY:"auto"` column let the
  scrub/timeline bar fall below the fold on portrait footage, because each panel frame
  capped itself at a viewport-relative `maxHeight:"64vh"` (`SyncedPanels.jsx`) with no
  awareness of the ~400px of fixed chrome around it. `ReviewStage.jsx`'s root is now a
  non-scrolling flex column (header / middle band / scrub band), the scrub band is pinned
  with `flexShrink:0` so it always wins the layout, and the panel frames dropped the `vh`
  cap for `flex:"0 1 auto"` + `minHeight:0` so they shrink to whatever height the middle
  band actually has — landscape clips clip exactly as before, portrait clips letterbox
  instead of overflowing. Also added an empty, zero-width tool-rail slot in the middle
  band as the seam for a future right-side tool rail (nothing renders there yet). The
  scrub band deliberately spans only under the panels/tool-rail, not the full window
  (i.e. not under the 280px queue rail) — spanning it would mean lifting `playhead`/
  `seekTarget` out of `ReviewStage` into `MotionReviewApp`, which the load-bearing
  playhead/seekTarget split documented in `motion-review/CLAUDE.md` argues against; out
  of scope for a layout fix. Build/lint clean (10 pre-existing errors, unchanged), 687
  pytest + 206 Vitest green. Live-verified in a running browser: no page- or stage-level
  scroll at normal window size, and — since the actual trigger is aspect ratio rather
  than raw window size (the OS window couldn't be resized smaller in the sandboxed
  browser session) — also verified under a forced portrait (9:19.5) aspect ratio, which
  is the exact height-bound case that used to overflow (`stage.scrollHeight ===
  stage.clientHeight` held). Also click-verified: playhead placement, ←/→ frame stepping,
  `c` to add a boundary + Delete to remove it, and the timeline's own zoom control all
  still work unchanged.

- ✅ **Collapsible left queue rail** (Aug 2026). New reusable `CollapsiblePanel.jsx`
  (`dock="left"|"right"`, curved pull-tab with a flipping chevron, smooth width-collapse)
  wraps the video queue in `MotionReviewApp.jsx`, freeing ~280px for the middle band on
  click. The empty tool-rail slot the layout work above left on the right is the intended
  future `dock="right"` consumer — not wired up yet. The tab sits FLUSH against the panel's
  real border (not straddling it), with the outline split into a fill-only closed path plus
  a stroke-only open arc so no second line renders next to the panel's own border; the
  chevron points into the panel when open, out toward the content when collapsed. Build/lint
  clean (10 pre-existing errors, unchanged), 687 pytest + 206 Vitest green. Live-verified in
  a running browser: collapse/expand round-trip, chevron flip, no-scroll contract still
  holds collapsed or expanded.

- ✅ **Right tool rail + on-demand Analyze Motion** (Aug 2026). New `CollapsiblePanel(dock="right")`
  in `ReviewStage.jsx`'s reserved seam, proving the component genuinely dock-agnostic (built
  dock="left"-only for the queue rail). 2×2 grid (`ToolRail.jsx`) matching `HeaderSaveButton`'s
  52×52 chrome: Rotate/Crop/Filters are permanently disabled stubs (clean hooks, no handlers);
  Analyze Motion re-runs `video_motion.process_video` on demand via new
  `motion_review.reanalyze()` + `POST /motion-review/analyze` — previously the pass only ever
  ran once, synchronously, at upload/ingest time. Kept synchronous (no `export_job.py`-style
  background thread/poll) since a single video's re-run is the same cost class as the existing
  upload-time analysis. `reanalyze()` derives the `owned` flag via
  `queue_removal._owned_source` rather than a naive `prop.get("owned", False)` read, so a
  legacy proposal with no `owned` key at all doesn't get silently stripped of delete-eligibility
  on re-analysis. `list_queue()` was split into a reusable `_queue_entry()`/`get_queue_entry()`
  so the single-video re-read shares the exact same merge logic rather than drifting from it.
  723 pytest (+10 new in `tests/test_motion_review_reanalyze.py`, `/analyze` added to the
  input-validation route sweeps) + build/lint clean (10 pre-existing errors, unchanged).
  Live-verified end to end against the real server on a real 1:46 clip: spinner during the
  real ~25s ffmpeg pass, timeline repopulated with fresh suggested cuts (2→3 cuts, duration
  1:39.8→0:16.3) on completion, disabled stubs fire no requests, both rails collapse
  independently with no scroll introduced.

- ✅ **Collapsible "Removed · timelapse" panel** (Aug 2026). `SyncedPanels.jsx`'s
  three-panel row is now Original / Removed·timelapse (collapsible) / Trimmed result —
  Trimmed stays permanently visible, Removed collapses. Deliberately did NOT reuse
  `CollapsiblePanel.jsx`: that component assumes a fixed-pixel sidebar docked at a true
  screen edge, but each synced panel is a `flex:1` peer sized by the loaded video's
  `aspectRatio`, with no stable pixel width to hand a dock animation. Collapsing instead
  unmounts the panel (and its `SegmentVideo`) entirely rather than shrinking it to a
  strip; a small `<>` button takes its exact flex slot to re-expand, so the two
  remaining `flex:1` panels reflow to fill the row on their own — same mechanism as
  removing any flex sibling. The collapsible panel is kept in the visual middle on
  purpose so collapsing it grows both neighbors evenly. Session-only state, no
  persistence (matches `CollapsiblePanel`'s existing precedent — no UI-prefs mechanism
  exists elsewhere in the app). Build/lint clean (10 pre-existing errors, unchanged);
  frontend-only, no new tests. No screenshot tool was available in-session
  (no chromium-cli/Playwright), so this was verified through Connor's own live use in
  the browser across several rounds of feedback (button placement, panel order, then a
  correction that it was the wrong panel collapsing) rather than an automated check.

- ✅ **Storage tab — working-copy usage + guarded bulk purge** (Aug 2026). First real
  Settings tab (`StorageTab.jsx` replaces its `StubTab`). New `backend/storage.py`:
  `get_usage()` sums real bytes/count straight off `uploads/` on disk (ground truth,
  not derived from queue rows — verified to match `du -sh` exactly), and
  `purge_all_working_copies()` is deliberately *not* new deletion logic — it loops
  `motion_review.list_queue()` filtered to `owned: true` and calls the existing
  `queue_removal.remove_from_queue` per video (skipping, not failing, any video
  `export_job.is_exporting`), so it inherits that function's ownership proof and its
  `savings.json`-preserving behavior for free. Two routes: `GET /motion-review/storage`,
  `POST /motion-review/storage/purge`. UI: usage line, a purge button behind a
  `VerdictButtons`-style confirm popover, and — per a follow-up request — two *separate*
  reclaimed-bytes lines (Climb Cutter vs. Photos) rather than one merged figure, since a
  merged total was dominated by GB-sized video trims next to KB/MB-sized photo culls.
  That split (`photosReclaimedBytes = reclaimedBreakdown.photos_exact +
  photos_estimated`) was added to `StatsContext` as a single derived value and also
  swapped into the main-page `DeleteCounter`, which previously showed the same misleading
  merged total (was reading "1.7 GB", ~94% of which was Climb Cutter, not photos) — it
  now shows the photos-only slice and its hover tooltip dropped the `climb_cutter` line
  accordingly. 7 new pytest cases (`tests/test_storage.py`, reusing `tmp_motion_db`); full
  suite 742 pytest + 206 Vitest green, build clean. Lint went 10→11 pre-existing errors —
  the one new one is the same `react-hooks/set-state-in-effect` "fetch on mount" pattern
  `MotionReviewApp.jsx` already trips twice, not a new class of problem. Live-verified
  against the real server/library: `/motion-review/storage` matched real disk usage
  (1,105,151,359 bytes / 3 videos against a `du -sh` of 1.0G), and a live discrepancy
  question (4 videos in queue vs. 3 counted) turned out to be the ownership guard working
  as designed — the 4th video's source lives outside `uploads/` (a test fixture path),
  so `owned: false` correctly excludes it from both the usage figure and the purge. The
  purge button itself was never actually clicked in-session (would have deleted real
  working copies); confirm/cancel UI was reviewed but not click-tested live — no
  browser-automation driver was available (no `chromium-cli`/Playwright, and Chrome
  AppleScript control hung on what looks like an unresolved macOS Automation permission
  prompt).

- ✅ **Clear all boundaries** (Aug 2026). One control in the right tool rail
  (`ToolRail.jsx`) drops every edit boundary — any type, current or future — from the
  selected video at once. `clearAllRegions()` (`regions.js`) is a one-line `() => []`:
  the region list is flat and never partitioned by type, so wiping it is type-agnostic
  by construction, stronger than an explicit per-type registry loop. Goes through the
  same `onRegionsChange` path as removing one boundary or "↺ reset to proposed" — no
  separate state reset. Confirm-gated (no undo stack exists yet). Per Connor's follow-up,
  the control stays permanently visible in the rail and just dims (matching the
  Rotate/Crop/Filters stub styling) at zero boundaries rather than hiding. Clearing
  writes nothing to disk — like any other unsaved edit, Save Draft is still required
  afterward to persist it. `ToolButton` gained a `tint` prop (default the rail's teal) so
  this control can render in red at the same 52×52 chrome as the other four buttons,
  which are visually unchanged. Live-verified against the real dev server: added 14 real
  cuts + 1 speed region, cleared, confirmed a clean empty-state render (no crash, no
  phantom markers), Esc/outside-click cancel leave state untouched, dimmed icon is a
  no-op. Build/lint clean, 742 pytest + 206 Vitest green.

- ✅ **Shared config store + Photos Library settings tab** (Aug 2026). New
  `backend/config_store.py` — the one general, persisted key/value settings store
  (`photo_db/config.json`, schema-versioned), built as the foundation for two follow-on
  prompts rather than shaped around one setting. Seeded with `library_root`;
  `safe_paths.ALLOWED_ROOTS`, `embed_job.PHOTOS_ROOT`, and
  `video_motion.DEFAULT_PHOTOS_LIBRARY`/`ORIGINALS_ROOT` — three independently
  hardcoded copies of the same Photos-library path — now all resolve through it, one
  source of truth. Second real Settings tab (`PhotosLibraryTab.jsx` replaces its
  `StubTab`, joining `StorageTab`): read-only root display + a Validate button checking
  for `resources/derivatives` + `originals`, no editable field or file picker by design.
  Two new routes, `GET/POST /settings/photos-library[/validate]`. `/write-tests` on the
  new module (87 tests) surfaced two real bugs against its own "never raises" contract —
  a non-UTF-8 config file and a non-string/empty `library_root` could each crash or
  misdirect the app at import time — both fixed rather than left as documented xfails
  (see `backend/CLAUDE.md`). Full suite 915 pytest + 206 Vitest green (was 828), build/
  lint clean (one more `react-hooks/set-state-in-effect` instance, same pre-existing
  pattern as `StorageTab.jsx`). Live-verified: pointed the config at a bogus path and
  confirmed all three consumers followed it (not the hardcoded literal) in a fresh
  process, `validate` correctly distinguished the real library from the bogus one, and
  deleting `config.json` entirely still imported cleanly with no file recreated by a
  bare read.

- ✅ **Date backfill: `date_taken` from Apple's Photos.sqlite** (Aug 2026). Groundwork
  for the planned "Time tide" temporal search/filter feature, which needs a real
  per-photo date to range-query over. New `backend/photo_dates.py` joins ChromaDB's
  `apple_uuid` against
  `ZASSET.ZUUID`/`ZDATECREATED` (read-only, immutable) and converts Core Data's epoch
  (seconds since 2001, not 1970) to a Unix int — the one canonical `date_taken` shape,
  now written by exactly two callers: `embed_photos.py` at index time and
  `backend/backfill_dates.py`, a rerunnable/idempotent catch-up script for the 56,606
  rows indexed before this existed (mirrors `backfill_uuids.py`'s chunked-update
  shape). `utils.extract_metadata()` no longer writes `date_taken` at all — it used to
  write an EXIF string that only ever populated 6 rows and type-diverged from
  everything else. `graph_view.py`'s payload now carries the field. `/write-tests`
  surfaced two real bugs, both fixed rather than left `xfail` (unlike the config-store
  entry above, which left its two bugs live for a review pass — Connor's call this
  time was fix-in-place, since one was exactly the "silently wrong" class this
  migration exists to prevent): an unescaped sqlite URI (`Path.as_uri()` now, not an
  f-string) and a falsy `apple_uuid` that could theoretically inherit an unrelated
  asset's date from a stray empty-string `ZUUID`. See `backend/CLAUDE.md` for both.
  Full suite 1001 pytest + 206 Vitest green. Live-verified, not just tested: backfilled
  all 56,612 then-indexed rows (0 misses), spot-checked 5 random photos against
  Photos.app's own `date of media item id` via AppleScript (exact match to the
  second), confirmed idempotent on a second `--write` run, then ran a real
  `embed_photos.py` pass that picked up 161 previously-unindexed photos — all 161
  landed with a correctly-typed date at index time with zero misses, and a follow-up
  `backfill_dates.py --write` against the grown 56,773-row library reported 0 written
  / 0 misses, confirming the indexer and the backfill agree.

**Known defects (documented as `xfail(strict)`, awaiting a decision — don't silence them):**
- `build_plan` **drops footage** under an unrecognised region type: `get_type` returns None
  so no Pieces are emitted, but the cursor still advances past the span. The frontend
  reaches the same outcome and documents it, so the two halves agree — the open question is
  whether "I don't recognise this" should lose the user's footage at all. Only reachable
  from unsanitised regions (a draft written by a newer build); `sanitize_regions` strips
  unknown types on normal API traffic.
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
- ✅ **Fixed 2026-08-15 — full-library UMAP refit.** The reducer had been fit on a
  **2,000-photo sample** (Jul 3) and never refit; ~46k photos were merely projected onto it
  via `.transform()`, so the map's structure came from a 3.5% subset. Now fit on all 56,612
  (81 s, 2.3 GB peak, `random_state=42` pinned). Measured on the same photos and queries
  either way: mean concept-separation ratio 16.2 → 38.8, and "screenshot" results went from
  smeared across 9 fine clusters (23% in the largest) to 5 (65%). Global geometry improved
  only modestly by comparison (separation ratio +8.6%, silhouette +2.9%), and the largest
  fine cluster grew from 4.1% to 5.2% of the library — the win is at the concept level, not
  in the aggregate statistics. Clustering had to switch to a kNN connectivity graph to run
  at all; see `backend/CLAUDE.md`. Backups and a coordinate snapshot from before the refit
  are in `photo_db/` with `LAYOUT_RESTORE_20260815T231454Z.md`.
- Graph View currently plots only the **top 50 search results**, not the library. Making it
  an actual map is its own piece of work, separate from Phases 4/5.
- Clustering is Agglomerative (Ward) at fixed k (broad=12, fine=60), now kNN-constrained at
  `CONNECTIVITY_K=15`. The refit has landed, so revisiting k is live work rather than
  blocked: both the cluster counts and the connectivity k are unexamined guesses. If fine
  clusters ever look stringy or chained, raise `CONNECTIVITY_K` — that artifact is a
  symptom of too sparse a graph, not of a bad layout.

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
