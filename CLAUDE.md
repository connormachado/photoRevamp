# CLAUDE.md — photo memory

> This file is auto-loaded by Claude Code at the start of every session. Keep it current. For the full feature list and long-term plans, see `RODEMAP.md`.

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

| layer | tech |
|---|---|
| embeddings | CLIP ViT-B/32 via `open_clip` |
| vector DB | ChromaDB (local persistent folder, `photo_db/`) |
| backend | Flask + Python, port 5001 |
| frontend | React + Vite, port 5173 |
| device | Apple Silicon Mac (torch device = `mps`) |
| video | pip-bundled ffmpeg via `imageio_ffmpeg` — **not** a system binary |

**There is no `ffmpeg` and no `ffprobe` on PATH.** Every module that touches video
resolves the binary with `imageio_ffmpeg.get_ffmpeg_exe()` (it has libx264 built in).
Because `ffprobe` is not bundled at all, video metadata is scraped by parsing
`ffmpeg -i` **stderr** — see `video_motion.probe` and `export_video.read_source_metadata`.
Don't write code that shells out to a bare `ffmpeg`/`ffprobe`, and don't add a system
ffmpeg dependency to fix a bug; the bundled binary is the convention.

There is **no test suite** anywhere in the repo. `backend/test-videos/` holds manual
fixtures, not tests. Verification is `npm run build` + importing the backend modules;
every "validated" claim in this file rests on a manual run.

## API surface (the actual routes — check here before inventing one)

| route | method | purpose |
|---|---|---|
| `/stats` | GET | `{total, deleted, reclaimed_bytes}` — see wiring notes below |
| `/stats/increment` | POST | bump delete counter by `{delta: ±1}` |
| `/search/text` | POST | `{query, n}` → CLIP text search |
| `/search/image` | POST | `{image_b64, n}` → visual similarity search |
| `/api/graph-view` | GET | `?query&n` → search results with UMAP `x`/`y` + cluster ids |
| `/thumbnail` | GET | `?path&size` → resized JPEG |
| `/full` | GET | `?path` → original; HEIC converted to JPEG in memory |
| `/reveal` | POST | `{id}` → spotlight the photo in Apple Photos via `apple_uuid` |
| `/cleanup` | POST | prune ChromaDB rows whose files are gone from disk |
| `/motion-review/queue` | GET | videos awaiting/having review |
| `/motion-review/source` | GET | `?id` → browser-playable h264 copy (Range-enabled) |
| `/motion-review/timelapse` | GET | `?id` → baked timelapse of removed sections |
| `/motion-review/savings` | GET | running reclaimed-bytes pool |
| `/motion-review/decision` | POST | `{video_id, verdict, regions?}` (legacy `cut_segments?` still accepted) |
| `/motion-review/export` | POST | `{video_id, regions?}` → render + import into Photos + reveal, then record the approval |

There is no `/photo` and no `/open-in-photos` — those names are stale; use `/full`
and `/reveal`.

## Conventions

- **`server.py` stays thin.** It handles routing only. All logic lives in its own module. One route per feature.
- **One feature per backend file.** New cleanup/clustering logic gets its own module, not appended to an existing one.
- **One component per feature on the frontend.** Components go in `photo-search/src/components/`.
- **Everything works offline.** No external API calls beyond the one-time CLIP download.
- **Apple Silicon / MPS.** Handle the torch device explicitly; don't assume CUDA or CPU.
- **Stable IDs.** Photos are keyed by `file_id()` (MD5 of file path) so indexing stays incremental and resumable.
- **No git operations.** Do not commit, push, or otherwise write to git history.

## Non-obvious wiring

- **`GET /stats` is overloaded.** It returns one merged payload, `{total, deleted, reclaimed_bytes}`: `total` is the live `collection.count()` (header "X photos indexed"), the other two come from `stats.py` reading `stats.json`. Don't repurpose `/stats` for just one of them — both the header and `DeleteCounter` read from it. The delete counter is bumped via `POST /stats/increment` with `{delta: ±1}`.
- **`reclaimed_bytes` in `stats.json` is a mirror, not the ledger.** The authoritative per-video record is `photo_db/motion_review/savings.json`; `motion_review._apply_savings` writes there and then mirrors the total into `stats.json`. The review room reads `GET /motion-review/savings` directly, so nothing on the frontend currently consumes the `/stats` copy — keep them in sync anyway.
- **Edit boundaries live in a two-file registry, and regions are the source of truth.** Every kind of timeline edit is declared once per side, keyed by the same type id string: `backend/edit_boundaries.py` (default params + the apply-on-export hook) and `photo-search/src/components/motion-review/boundaryTypes.js` (label, icon, colour, how it renders). Adding a type = one entry in each; nothing in `CutTimeline`, `motion_review.py` or `export_video.py` branches on a specific type. The wire/disk shape is a **region** — `{id, type, start, end, params}` in seconds — and `cut_segments` / `keep_segments` / `trimmed_duration` are now *derived* from it by running `build_plan`, kept only so the savings ledger and the preview panels keep their old shape. Reviews written before the registry carry only `cut_segments` and upgrade to cut regions on read.
- **`render_plan` picks its ffmpeg strategy from the plan.** If every piece is a straight copy (speed 1, no filters) it uses the concat demuxer with inpoint/outpoint — the drop-only path, verified byte-identical to the pre-registry renderer. Any piece needing a transform switches the whole render to one `-filter_complex` graph (per-piece `trim`/`setpts` + `atrim`/`asetpts`/`atempo`, then `concat`), because the concat demuxer cannot vary playback rate per entry. `render_segments` is now a thin wrapper over it.
- **Video re-encode invariants — three ways to silently corrupt an export.** All three were hit and fixed once; don't reintroduce them. (1) **Rotation needs no flag.** On re-encode ffmpeg autorotates, baking the source's display matrix into the pixels — a 1920x1080 iPhone source carrying a -90° matrix comes out a true 1080x1920 portrait file. Adding `-metadata:s:v:0 rotate=` is a no-op in ffmpeg 7 and would double-rotate already-upright footage if it ever started working. (2) **iPhone `.MOV` carries streams this build cannot decode** — a 4-channel `apac` spatial-audio track plus several `mebx` data tracks. Map explicitly (`-map 0:v:0 -map 0:a:0?`); letting ffmpeg auto-map fails the encode outright. (3) **Date and GPS must be re-stamped**, they do not survive a re-encode on their own: `-metadata creation_time=` (prefer the source's `com.apple.quicktime.creationdate`, which is local wall-clock with offset — the timestamp Photos files by) and the ISO-6709 location string.
- **Saving a clip IS approving it — there is no separate approve button.** The green dome in the review room and the floppy icon in the ReviewStage header both fire `POST /motion-review/export`, which renders → imports into Photos → reveals → *then* records the approval. That order matters: a failed export leaves no phantom approval in the ledger. Reject stays bookkeeping-only via `/motion-review/decision`. **The original is never deleted or modified** — the export is a new asset beside it, and deleting the original is always a manual user decision. Consequently `savings.json` is a *projection* ("if you deleted these originals you'd reclaim X"), not a record of bytes actually freed.
- **Two independent mechanisms date an exported clip, and both are kept.** The `creation_time` container tag written during the render, and `set date of media item id ...` via AppleScript after import. The second was expected to be read-only but is settable on current macOS (verified); they agree. Keep both — the container tag needs no automation permission and survives an AppleScript vocabulary change.
- **Chip queries are the single source of truth.** The six junk-cull chips live in the exported `CHIPS` array in `SearchChips.jsx`. Junk Hunt re-imports `CHIPS` and fires all of them in parallel — edit the list in one place. Each chip is `{emoji, label, query}` with the emoji as its own field; only `query` goes to CLIP.
- **"Show in Photos" auto-bumps the delete counter.** On a successful `/reveal`, `OpenInPhotosButton` calls `incrementDeleteCount()` (an optimistic "about to delete" proxy). If the counter drifts up unexpectedly, this is why. The shared `incrementDeleteCount`/`decrementDeleteCount` come from `StatsContext`, which wraps `App`'s returned tree.
- **`/reveal` takes a `file_id`, but reveals by `apple_uuid`.** Photos are indexed from the derivatives cache, whose paths Photos.app doesn't know. The route looks the row up by `id`, pulls `apple_uuid` from metadata, and hands that to `cleanup.reveal_in_photos`, which runs `spotlight media item id` via AppleScript. Never try to reveal by filename or path.
- **The UMAP reducer was fit on a 2,000-photo sample** (`photo_db/models/layout_meta.json`, `count_at_fit: 2000`, Jul 3) and has not been refit. All ~49.6k photos carry `x`/`y` because `compute_layout.py incremental` projects new rows onto that existing reducer. The map's *structure* therefore comes from a 2k subset — the likely reason the layout looks off. A full refit is `compute_layout.py` full-fit mode.
- **Cluster labels are Agglomerative, not density-based.** `compute_layout.py` uses `sklearn.cluster.AgglomerativeClustering` at fixed k (`broad_k=12`, `fine_k=60`), writing `cluster_id_broad`/`cluster_id_fine` back to ChromaDB. There is no HDBSCAN in this project.

## Working with the user

- Comfortable with Python; newer to React — explain React concepts clearly when they come up.
- Analogies help when introducing something new.

## Status / next steps

See `RODEMAP.md` for the full list. Current state:

Library is ~49.6k photos indexed. Repo is consolidated on a single `main` branch
(in sync with `origin/main`), no worktrees, no stashes — one source of truth.

**Completed:**
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
  (`delete`), type-agnostic export via `build_plan` → `render_plan`. "cut" is the only
  registered type so far; the render output is byte-identical to before the refactor.
- ✅ Climb Cutter export to Photos — `export_video.py` + `POST /motion-review/export`,
  equal-sized red/green domes, header save icon. Smoke-tested end to end on one real
  clip: 59.18s → 32.93s, 176 MB → 37 MB, landed in Photos upright at the original's
  date (Feb 11 2026 9:31 PM) with GPS preserved and the original untouched.

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
- ⚠️ `npm run lint` reports ~12 errors (`react-hooks/set-state-in-effect`,
  `react-refresh/only-export-components`). The build is unaffected.

**Immediate next:**
1. Climb Cutter "speed" boundary type — one entry in `edit_boundaries.py` (hook returns a
   `Piece` with `speed=K`) + one in `boundaryTypes.js`. The `-filter_complex` render path it
   needs already exists and is smoke-tested; note that a sped-up piece currently lands at a
   multiplied frame rate, so its hook should pin `fps` in the piece's `vf`.
2. Expand Climb Cutter with further features (current build focus).
3. Graph View polish — refit UMAP on the full library, then Phase 3 cosmetics.
4. Real video semantic search (wanted soon, larger effort).