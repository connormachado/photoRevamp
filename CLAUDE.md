# CLAUDE.md — photo memory

> This file is auto-loaded by Claude Code at the start of every session. For the full
> feature list and long-term plans, see `RODEMAP.md`.

### Rules for editing this file (agents: read before you add a line)

This file is read into context on **every** session, so every line here is a recurring
cost. Default to **not** adding to it.

- **Only add what a session cannot derive from the codebase.** If `ls`, a grep, the
  package manifest, or `--help` would answer it, leave it out. Specifically: no
  directory layouts, no dependency/stack lists, no route or API tables, no copied
  signatures or schemas, no repo tours, no generic best practices.
- **Do add:** gotchas and failure contracts ("X looks safe but does Y"), design
  rationale the code can't explain, conventions that *differ* from the tool's default,
  safety-critical prohibitions, and non-guessable commands.
- **Put it in the narrowest file that still loads when it matters.** Guidance about one
  folder belongs in that folder's `CLAUDE.md` (`backend/`,
  `photo-search/src/components/motion-review/`), which loads only when a session touches
  files there. Only genuinely repo-wide rules belong in this file.
- **Progress logs, shipped-feature lists, and roadmap items go in `RODEMAP.md`**, not
  here. A "what I just built" entry is never a reason to grow this file.
- **Prefer replacing over appending.** When behaviour changes, rewrite the stale line;
  don't stack a correction on top of it.
- Keep this file under ~250 lines. If an addition pushes past that, something else
  should be moving out to `RODEMAP.md` or a subdirectory file.

## Critical rules

- **Do not commit or push anything to GitHub** under any circumstances. Do not run `git commit`, `git push`, or any git command that writes to history. The user handles all commits manually.

### Enforced mechanically: agent-commit block (don't fight it)

This rule is no longer just prose — it's enforced by git hooks so a drifting session can't quietly commit. **If you (an agent) try to `git commit` or `git push`, it will be refused. This is intended. Do not try to work around it.**

- **Hooks:** `.githooks/pre-commit` and `.githooks/pre-push` (tracked in the repo; activated via `git config core.hooksPath .githooks`, which is set locally per clone).
- **How they detect an agent:** Claude Code auto-exports env markers (`CLAUDECODE=1`, `AI_AGENT=claude-code_...`, `CLAUDE_CODE_ENTRYPOINT`) into every Bash subprocess it spawns. The hooks block when any of these is present and fail loudly with: `🚫 Blocked: commits must be made manually by Connor, not by an agent.` Connor's own Terminal/iTerm never carries these vars, so his manual commits pass untouched — no setup required.
- **Connor's manual bypass:** if Connor ever needs to commit/push from inside a session (e.g. via the `!` prefix), prefix the command with `CLAUDE_COMMIT_OK=1` (e.g. `CLAUDE_COMMIT_OK=1 git commit ...`). This override is for Connor only; agents must not use it.
- **After a build phase / long task:** the `change-summarizer` subagent (global, `~/.claude/agents/change-summarizer.md`) writes `CHANGES_PENDING_REVIEW.md` — a plain-language, file-by-file summary of the uncommitted changes for Connor to read before he commits. It's gitignored and never committed. `/build-phase` runs it automatically as its final step.


## Build Workflow for New Features
- Never run `git commit` or `git push` under any circumstances, at any point in any phase.
- Multi-phase features are built one phase at a time via /build-phase.
- Each phase: plan → implement (subagent) → verify (subagent) → pause for human check.

## What this is

**photo memory** is a fully local photo search and curation tool. It embeds a camera roll (50k+ photos) with CLIP and stores the vectors in ChromaDB, then lets you search the whole library by natural language ("golden hour sunset", "birthday with friends") or by dropping in a photo to find visually similar ones. Everything runs on the user's machine — no cloud accounts, no API keys, no external calls except the one-time CLIP model download. The longer-term goal is camera roll cleanup: duplicates, blurry/junk detection, bulk delete lists, trip albums, and eventually a video edit agent.

## Stack

Read `requirements.txt` and `photo-search/package.json` for the dependency list. The
two things they don't tell you:

**Video goes through pip-bundled ffmpeg (`imageio_ffmpeg`), not a system binary.
There is no `ffmpeg` and no `ffprobe` on PATH.** Every module that touches video
resolves the binary with `imageio_ffmpeg.get_ffmpeg_exe()` (it has libx264 built in).
Because `ffprobe` is not bundled at all, video metadata is scraped by parsing
`ffmpeg -i` **stderr** — see `video_motion.probe` and `export_video.read_source_metadata`.
Don't write code that shells out to a bare `ffmpeg`/`ffprobe`, and don't add a system
ffmpeg dependency to fix a bug; the bundled binary is the convention.

**`make test` runs everything** — pytest (`tests/`) + Vitest (`photo-search`). Run pytest
through `.venv/bin/python3`, never a bare `pytest`: the venv is 3.12 and only it has
torch. Two `tests/conftest.py` behaviours that will bite otherwise — an autouse fixture
makes any **unmocked `subprocess.run` raise** (request `fake_run`, don't work around it),
and `stats.STATS_PATH` is autouse-redirected to tmp so no test can clobber the live
delete counter. Reuse the boundary fixtures (`fake_run`, `ffmpeg_stderr`, `fake_chroma`,
`tmp_motion_db`, `client`) instead of rolling new mocks. `/write-tests <path>` generates
a suite via the `test-author` subagent. Coverage is pure logic + the route surface only —
**not** the ffmpeg render path, which is still verified by running it. Four tests are
`xfail(strict)` on purpose; they mark open decisions, so don't "fix" them into passing.

## API surface

The live list is the `@app.route` decorators in `backend/server.py` — read them there
rather than trusting a copy. The one thing grep won't tell you: there is no `/photo`
and no `/open-in-photos`. Those names are stale; use `/full` and `/reveal`.

## Conventions

- **Start a task by reading `docs/CODEBASE_MAP.md`** (build it with `/cartographer` if
  absent) to find where code lives — routes, modules, state owners — *before* dispatching
  explore agents. It's navigation only: this file remains the authority on gotchas and
  contracts, so if they disagree, CLAUDE.md wins and the map is stale — regenerate it.
- **`server.py` stays thin.** It handles routing only. All logic lives in its own module. One route per feature.
- **One feature per backend file.** New cleanup/clustering logic gets its own module, not appended to an existing one.
- **One component per feature on the frontend.** Components go in `photo-search/src/components/`.
- **Everything works offline.** No external API calls beyond the one-time CLIP download.
- **Apple Silicon / MPS.** Handle the torch device explicitly; don't assume CUDA or CPU.
- **Stable IDs.** Photos are keyed by `file_id()` (MD5 of file path) so indexing stays incremental and resumable.
- **Any request-derived string that becomes a path goes through `backend/safe_paths.py` first** — `resolve_within_roots` for a filesystem path, `safe_id_component` for an id interpolated into a filename. New routes included; see `backend/CLAUDE.md` for why.
- **No git operations.** Do not commit, push, or otherwise write to git history.

## Non-obvious wiring

- **`GET /stats` is overloaded.** One merged payload: `total` is the live `collection.count()` (header "X photos indexed"), `deleted` / `reclaimed_bytes` / `reclaimed_breakdown` come from `stats.py`, and `avg_photo_bytes` is config echoed for the UI (never persisted). Don't repurpose `/stats` for just one of them — the header, `DeleteCounter`, and Settings > Storage all read from it. Bumps go through `POST /stats/increment` with `{delta, exact_bytes?}`; the byte accounting itself is documented in `backend/CLAUDE.md`. **The main page's reclaimed figure is photos-only, not the merged total** — `reclaimed_bytes` sums `photos_exact + photos_estimated + climb_cutter`, and Climb Cutter's GB-sized video trims used to drown out photo-cull savings on the header (it once read "1.7 GB", ~94% of which was video). `StatsContext` derives `photosReclaimedBytes` (`photos_exact + photos_estimated`) as the one place that sum lives; `DeleteCounter` and `StorageTab` both read it rather than each computing it. The Climb Cutter slice has its own home now: `reclaimedBreakdown.climb_cutter` on the Settings > Storage tab.
- **`incrementDeleteCount(exactBytes)` takes an argument, so never hand it straight to `onClick`.** React passes the click event as the first arg, which the server then tried to `int()` — a silent 500 on every `+` press, while `−` (arity 0) kept working, so the counter drifted down and looked like a persistence bug. Wrap it: `onClick={() => incrementDeleteCount()}`. `StatsContext.bump` now ignores any non-finite size, and a failed write rolls the optimistic count back rather than showing a number that vanishes on reload.
- **Edit boundaries live in a two-file registry, and regions are the source of truth.** Every kind of timeline edit is declared once per side, keyed by the same type id string: `backend/edit_boundaries.py` (default params + the apply-on-export hook) and `photo-search/src/components/motion-review/boundaryTypes.js` (label, icon, colour, how it renders). Adding a type = one entry in each; nothing in `CutTimeline`, `motion_review.py` or `export_video.py` branches on a specific type. A type has two optional render slots: `renderBlock` draws inside the timeline's clipped rounded track, `renderOverlay` draws in the unclipped layer above it — interactive chrome belongs in the overlay, because a region is often only ~25px wide on screen while its controls are ~110px. The wire/disk shape is a **region** — `{id, type, start, end, params}` in seconds — and `cut_segments` / `keep_segments` / `trimmed_duration` are now *derived* from it by running `build_plan`, kept only so the savings ledger and the preview panels keep their old shape. Reviews written before the registry carry only `cut_segments` and upgrade to cut regions on read.
- **Saving a clip IS approving it — there is no separate approve button.** The green dome in the review room fires `POST /motion-review/export`, which kicks off a background job (`backend/export_job.py`) that renders → imports into Photos → reveals → *then* records the approval, in that order, unchanged from before it went async: a failed export still leaves no phantom approval in the ledger. **The route itself is now non-blocking** — it 202s immediately with a job id, and `MotionReviewApp.jsx` polls `GET /motion-review/export/status` once a second for real ffmpeg-driven progress instead of a static "a few seconds" message; see `backend/CLAUDE.md` for why this is a thread rather than `embed_job.py`'s subprocess. **Because export can now outlive the request that started it, `POST /motion-review/decision` and `POST /motion-review/remove` both 409 while one is in flight for that video** — otherwise a reject could race the export's own `record_decision` call, or a remove could unlink the source file out from under a running ffmpeg. **Reject is now two operations, and they compose rather than merge.** `POST /motion-review/decision` is still bookkeeping-only; `POST /motion-review/remove` (`backend/queue_removal.py`) is the destructive half that drops the row and frees disk. The confirm popup fires both, in that order, so the verdict outlives the entry it described. **The original is never deleted or modified** — the export is a new asset beside it, and deleting the original is always a manual user decision. Removal is allowed to delete exactly one class of source: a working copy the app itself made under `motion_review/uploads/`, proven by *two* independent checks in `queue_removal._owned_source` (the proposal's `owned` flag, set only by the upload route, **and** the path resolving inside `uploads/`). Neither alone authorises an unlink — a proposal claiming `owned: true` about a path outside `uploads/`, or a symlink parked in `uploads/`, deletes nothing. This is enforced, not intended: `tests/test_source_immutability.py` fails if any path deletes or rewrites a file the app did not create, and removal is in the lifecycle it sweeps. A purge feature WILL trip it — that failure is the guard working, so change the product's promise deliberately rather than loosening the test. Consequently `savings.json` is a *projection* ("if you deleted these originals you'd reclaim X"), not a record of bytes actually freed. **The floppy icon in the ReviewStage header is a separate action** — it saves the current in-progress edit as a draft (`POST /motion-review/draft`) so reopening the app resumes the same unfinished edit; it does not export or approve anything. See `motion_review.save_draft`/`_get_draft`/`_clear_draft` and the `drafts/<video_id>.json` files — a resume point, never routed through `record_decision`, so it never touches `decisions.jsonl` or `savings.json`. A draft is cleared once its video is actually exported (the export supersedes it) but survives a reject. **"Remove from queue" is a third, separate action from Reject, and the two must never be conflated.** It only shows once a video is exported (`exportedAt` truthy) and its own confirm popup (`VerdictButtons.jsx`) fires `POST /motion-review/remove` *alone* — it deliberately skips `POST /motion-review/decision`, which is the only route that retracts a video's savings credit (`record_decision` → `_apply_savings`). `queue_removal.remove_from_queue` itself never touches `savings.json` either way; Reject's retraction happens entirely on the `/decision` side, before its own `/remove` call. The Settings > Storage bulk "purge working copies" feature (`backend/storage.py`) does exactly this — loops `remove_from_queue` over every `owned` queue entry and never calls `record_decision`, for the same reason.
- **Chip queries are the single source of truth.** The six junk-cull chips live in the exported `CHIPS` array in `SearchChips.jsx`. Junk Hunt re-imports `CHIPS` and fires all of them in parallel — edit the list in one place. Each chip is `{id, emoji, label, query}`; only `query` goes to CLIP. `id` is the persisted dismissal-ledger key (`photo_db/dismissed.json`) — renaming one orphans its dismissals, rewording `query` is free.
- **"Show in Photos" auto-bumps the delete counter; "Hide from this filter" must never copy that pattern.** On a successful `/reveal`, `OpenInPhotosButton` calls `incrementDeleteCount()` (an optimistic "about to delete" proxy) via `StatsContext`. The two live side by side in the photo detail modal but are opposites: dismissing a photo from a filter (`POST /filters/dismiss`, `backend/dismissed.py`) only ever writes `dismissed.json` — no `/reveal`, no stats write, no touch to the photo. It's a display filter, not a delete signal.
- **For a new modal, copy `BulkAddPad`'s close pattern, not `App.jsx`'s photo-detail `Modal`.** The latter is older and only closes on outside-click — no Esc, no scroll-lock. `BulkAddPad`/`VerdictButtons`' `useEffect` (Esc + outside-click listeners added on open, removed on close) is the canonical one; `SettingsModal` follows it.

### Wiring that lives closer to the code

These load automatically when you work in the folder they describe — don't copy them
back up here:

- `backend/CLAUDE.md` — the ffmpeg render/export invariants (rotation, stream mapping,
  date + GPS re-stamping), `render_plan`'s two strategies, the preview-proxy cache-buster,
  the savings-ledger mirror, `/reveal`'s id indirection, and the UMAP/clustering caveats.
- `photo-search/src/components/motion-review/CLAUDE.md` — preview playback: why seeks are
  the expensive operation, the contiguous-piece boundary rule, and how the panels
  approximate speed.
- `docs/CODEBASE_MAP.md` — **not in the repo.** It's a local, gitignored, regenerable
  navigation map (route table, module-by-module purposes, on-disk state layouts, sequence
  diagrams for indexing / search / the Climb Cutter lifecycle) built by the Cartographer
  plugin. A fresh clone has no `docs/`; run `/cartographer` to build it, and again whenever
  it's gone stale. Never treat its absence as a bug.

## Working with the user

- Comfortable with Python; newer to React — explain React concepts clearly when they come up.
- Analogies help when introducing something new.

## Status / next steps

See `RODEMAP.md` for the full list. Current state:

Library is 56,612 vectors indexed in ChromaDB, all of which carry a Graph View layout
(`x`/`y`/cluster ids) as of the 2026-08-15 full UMAP refit. Repo is consolidated on a
single `main` branch (in sync with `origin/main`), no worktrees, no stashes — one
source of truth.

The shipped-work log with its verification detail lives in `RODEMAP.md`
under **features → ✅ shipped → verified build log**.

**Known gaps (verified absent, don't assume these exist):**
- ❌ Graph View Phase 4 (zoom / LOD) and Phase 5 (overlap nudge) — not started. `GraphView.jsx`
  is a fixed 920×600 canvas with no zoom, pan, or collision pass, and there is no `d3`
  dependency in `package.json`.
- ❌ Graph View renders only the top 50 search results at their UMAP coords — it is not yet a
  whole-library map.
- ❌ Duplicate finder (`duplicates.py`, `DuplicateReview.jsx`) — never built.
- ❌ Timeline / event / face clustering (`clustering.py`) — never built.
- ❌ Real video semantic understanding — `embed_photos.py` has no video handling at all;
  videos are indexed only as their static derivative stills.
- ❌ `motion_stats.py` aggregator — though decision logging itself *does* exist
  (`photo_db/motion_review/decisions.jsonl` + per-video `reviews/`).
- ⚠️ "Show in Photos" intermittently activates Photos.app without landing on the exact photo.
  Cause unconfirmed; the split activate-then-spotlight `osascript` calls are a suspect.
- ⚠️ `npm run lint` reports 11 errors (`react-hooks/set-state-in-effect`,
  `react-refresh/only-export-components`). The build is unaffected.
- ⚠️ Settings modal (`photo-search/src/components/settings/`) is UI shell only — six of
  seven tabs are still `StubTab` placeholders. `StorageTab` (working-copy usage +
  guarded bulk purge) is the first real one.

**Immediate next:**
1. Speed boundaries are render-verified and preview-verified but **not yet exported to
   Photos end to end** — the first real `POST /motion-review/export` with a speed
   region is still pending.
2. Expand Climb Cutter with further features (current build focus).
3. Graph View polish — full-library UMAP refit is done (2026-08-15); Phase 3 cosmetics
   and the Phase 4/5 zoom + overlap work remain. Overlap is now the most visible gap:
   tighter concept clusters mean results stack more, not less.
4. Real video semantic search (wanted soon, larger effort).