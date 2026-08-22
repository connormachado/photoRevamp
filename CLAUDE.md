# CLAUDE.md — photo memory

> Auto-loaded at the start of every session. Feature list, shipped log, known gaps and
> roadmap live in `RODEMAP.md` (not auto-loaded).

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
- **Date-stamp every number.** Any count, size, or measurement that survives in this file
  gets an explicit "as of `<date>`". Undated facts rot *silently* — "~49.6k photos" sat
  here for weeks telling every session something false. A dated one rots *visibly*: the
  reader can see how old it is and go re-measure. Verify a claim against the code before
  writing it, and re-verify rather than edit around it when updating.
- **Prefer a command to a number.** If the number is the point, name the command that
  produces it — and confirm that command actually returns what you claim before naming it.
- **Don't delete a list that a rule depends on.** A "no NEW category" gate is unenforceable
  without a recorded list of known ones. Regenerate and re-stamp the list; deleting it makes
  the rule decorative.
- **A reminder to a human is not documentation.** If the rule is "remember to run X",
  the fix is a mechanism (a test, a hook, a Makefile target) — build that instead.
- **Put it in the narrowest file that still loads when it matters.** Guidance about one
  folder belongs in that folder's `CLAUDE.md` (`backend/`,
  `photo-search/src/components/motion-review/`), which loads only when a session touches
  files there. Only genuinely repo-wide rules belong in this file.
- **Progress logs, shipped-feature lists, roadmap items, and inventories of what doesn't
  exist yet go in `RODEMAP.md`**, not here. A "what I just built" entry is never a reason
  to grow this file.
- **Prefer replacing over appending.** When behaviour changes, rewrite the stale line;
  don't stack a correction on top of it.
- **Structure long entries.** A multi-hundred-token unbroken paragraph gets skimmed, which
  hides the load-bearing clause inside it. Break anything long into labelled sub-bullets.
- **Budget is 4,600 tokens, not a line count** (lines are a bad proxy — these files are
  long bullets). Headroom is deliberately thin, so the next gotcha worth recording forces a
  trade against something stale. **That is the forcing function; don't quietly raise it** —
  move something to `RODEMAP.md` or a subdirectory file instead. **Measure, never eyeball**
  (discovers the memory files, so a new nested one is counted; `cl100k_base` is a proxy for
  tracking growth, not Claude's exact tokenizer):
  ```
  find . -name CLAUDE.md -not -path '*/node_modules/*' -not -path '*/.venv/*' | sort | xargs .venv/bin/python3 -c "import tiktoken,sys;e=tiktoken.get_encoding('cl100k_base');t=[(len(e.encode(open(f).read())),f) for f in sys.argv[1:]];[print(f'{n:6d} tok  {f}') for n,f in t];print(f'{sum(n for n,_ in t):6d} tok  TOTAL')"
  ```

## Critical rules

- **Do not commit or push anything to GitHub** under any circumstances. Do not run `git
  commit`, `git push`, or any git command that writes to history. Connor commits manually.
- This is enforced by `.githooks/` — **an agent's commit or push is refused by design.
  That is intended; do not work around it.** The hooks explain themselves when they fire.
  They are activated per-clone by `git config core.hooksPath .githooks`, which is *manual*:
  a fresh clone has no block at all until someone runs it.
- **After a build phase or long task, run `/summarize-changes --writeFile`** — it dispatches
  the `change-summarizer` subagent to write the gitignored `CHANGES_PENDING_REVIEW.md` for
  Connor to read before committing. `/build-phase` does this automatically as its last step;
  **a prompt run outside `/build-phase` will not**, so do it explicitly there.

## What this is

A fully local photo search and curation tool: a 50k+ camera roll embedded with CLIP into
ChromaDB, searchable by natural language or by example image. Nothing leaves the machine
except the one-time CLIP download. Longer-term goal is camera-roll cleanup and a video
edit agent.

## Stack

Read `requirements.txt` and `photo-search/package.json` for the dependency list. The
things they don't tell you:

**Video goes through pip-bundled ffmpeg (`imageio_ffmpeg`), not a system binary.
There is no `ffmpeg` and no `ffprobe` on PATH.** Every module that touches video
resolves the binary with `imageio_ffmpeg.get_ffmpeg_exe()` (it has libx264 built in).
Because `ffprobe` is not bundled at all, video metadata is scraped by parsing
`ffmpeg -i` **stderr** — see `video_motion.probe` and `export_video.read_source_metadata`.
Don't write code that shells out to a bare `ffmpeg`/`ffprobe`, and don't add a system
ffmpeg dependency to fix a bug; the bundled binary is the convention.

**`make test` runs everything** — pytest (`tests/`) + Vitest (`photo-search`). Two
`tests/conftest.py` behaviours that will bite otherwise: an autouse fixture makes any
**unmocked `subprocess.run` raise** (request `fake_run`, don't work around it), and
`stats.STATS_PATH` is autouse-redirected to tmp so no test can clobber the live delete
counter. Reuse the boundary fixtures (`fake_run`, `ffmpeg_stderr`, `fake_chroma`,
`tmp_motion_db`, `client`) instead of rolling new mocks. `/write-tests <path>` generates
a suite via the `test-author` subagent. Coverage is pure logic + the route surface only —
**not** the ffmpeg render path, which is still verified by running it.

**The `xfail(strict)` markers are not all the same kind of thing.** Some mark an open
design question; others mark a known, fixable DEFECT that was flagged rather than fixed.
**Read the marker's `reason` string before touching one** — it says which kind it is and
what the correct behaviour would be. `strict=True` means the suite goes RED the moment one
starts passing, so a fix and its marker have to land together. Don't trust a count written
down anywhere, including here — `pytest -m xfail --collect-only` is the truth. (Verified
2026-08-22: it catches decorator markers *and* `pytest.param(marks=…)` markers, expanding
every parametrized instance. It does **not** catch an imperative `pytest.xfail()` called
inside a test body; the suite currently contains none, so grep for `pytest.xfail(` if you
need certainty.)

**`npm run lint` has pre-existing errors; gate on no NEW category, never on the count.**
Known categories as of 2026-08-22: `react-hooks/set-state-in-effect`, `no-unused-vars`,
`react-refresh/only-export-components`, `react-hooks/immutability` — 12 errors, 0 warnings.
A category outside that list is the thing worth flagging; the count itself drifts and a
check that cries wolf gets ignored. The build is unaffected either way.

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

- **Videos are indexed only as their static derivative stills.** `embed_photos.py` has no video handling, so a video matches on one frame's worth of content — which is why video results can look arbitrary. Real video semantic understanding does not exist yet.
- **`GET /stats` is overloaded.** One merged payload: `total` is the live `collection.count()` (header "X photos indexed"), `deleted` / `reclaimed_bytes` / `reclaimed_breakdown` come from `stats.py`, and `avg_photo_bytes` is config echoed for the UI (never persisted). Don't repurpose `/stats` for just one of them — the header, `DeleteCounter`, and Settings > Storage all read from it. Bumps go through `POST /stats/increment` with `{delta, exact_bytes?}`; the byte accounting itself is documented in `backend/CLAUDE.md`. **The main page's reclaimed figure is photos-only, not the merged total** — Climb Cutter's GB-sized video trims used to drown out photo-cull savings on the header (it once read "1.7 GB", ~94% of which was video). `StatsContext` derives `photosReclaimedBytes` as the one place that sum lives; `DeleteCounter` and `StorageTab` both read it rather than each computing it. The Climb Cutter slice has its own home: `reclaimedBreakdown.climb_cutter` on the Settings > Storage tab.
- **`incrementDeleteCount(exactBytes)` takes an argument, so never hand it straight to `onClick`.** React passes the click event as the first arg, which the server then tried to `int()` — a silent 500 on every `+` press, while `−` (arity 0) kept working, so the counter drifted down and looked like a persistence bug. Wrap it: `onClick={() => incrementDeleteCount()}`. `StatsContext.bump` now ignores any non-finite size, and a failed write rolls the optimistic count back rather than showing a number that vanishes on reload.
- **Edit boundaries live in a two-file registry, and regions are the source of truth.** Every kind of timeline edit is declared once per side, keyed by the same type id string: `backend/edit_boundaries.py` (default params + the apply-on-export hook) and `photo-search/src/components/motion-review/boundaryTypes.js` (label, icon, colour, how it renders). Adding a type = one entry in each; nothing in `CutTimeline`, `motion_review.py` or `export_video.py` branches on a specific type. A type has two optional render slots: `renderBlock` draws inside the timeline's clipped rounded track, `renderOverlay` draws in the unclipped layer above it — interactive chrome belongs in the overlay, because a region is often only ~25px wide on screen while its controls are ~110px. The wire/disk shape is a **region** — `{id, type, start, end, params}` in seconds — and `cut_segments` / `keep_segments` / `trimmed_duration` are now *derived* from it by running `build_plan`, kept only so the savings ledger and the preview panels keep their old shape. Reviews written before the registry carry only `cut_segments` and upgrade to cut regions on read.

- **Climb Cutter's verdict lifecycle: separate actions that must never be conflated.**
  Save, Reject, and "Remove from queue" are three different things with three different
  effects on the ledger, and the original file is never one of the things that changes.

  - **Saving a clip IS approving it — there is no separate approve button.** The green dome
    in the review room fires `POST /motion-review/export`, which kicks off a background job
    (`backend/export_job.py`) that renders → imports into Photos → reveals → *then* records
    the approval, **in that order**: a failed export leaves no phantom approval in the
    ledger. The route 202s immediately with a job id and `MotionReviewApp.jsx` polls
    `GET /motion-review/export/status` for progress; see `backend/CLAUDE.md` for why it's a
    thread rather than `embed_job.py`'s subprocess.
  - **Export outlives the request that started it, so two routes 409 while one is in
    flight** for that video: `POST /motion-review/decision` and `POST /motion-review/remove`.
    Otherwise a reject could race the export's own `record_decision` call, or a remove could
    unlink the source file out from under a running ffmpeg.
  - **Reject is two operations, and they compose rather than merge.**
    `POST /motion-review/decision` is bookkeeping-only; `POST /motion-review/remove`
    (`backend/queue_removal.py`) is the destructive half that drops the row and frees disk.
    The confirm popup fires both, in that order, so the verdict outlives the entry it
    described.
  - **The original is never deleted or modified.** The export is a new asset beside it, and
    deleting the original is always a manual user decision. Removal may delete exactly one
    class of source: a working copy the app itself made under `motion_review/uploads/`,
    proven by *two* independent checks in `queue_removal._owned_source` (the proposal's
    `owned` flag, set only by the upload route, **and** the path resolving inside
    `uploads/`). Neither alone authorises an unlink — a proposal claiming `owned: true`
    about a path outside `uploads/`, or a symlink parked in `uploads/`, deletes nothing.
    This is enforced, not intended: `tests/test_source_immutability.py` fails if any path
    deletes or rewrites a file the app did not create, and removal is in the lifecycle it
    sweeps. **A purge feature WILL trip it — that failure is the guard working**, so change
    the product's promise deliberately rather than loosening the test.
  - **`savings.json` is therefore a projection, not a record** — "if you deleted these
    originals you'd reclaim X", not bytes actually freed.
  - **The floppy icon in the ReviewStage header is a separate action.** It saves the current
    in-progress edit as a draft (`POST /motion-review/draft`) so reopening the app resumes
    the same unfinished edit; it does not export or approve anything. See
    `motion_review.save_draft`/`_get_draft`/`_clear_draft` and the `drafts/<video_id>.json`
    files — a resume point, never routed through `record_decision`, so it never touches
    `decisions.jsonl` or `savings.json`. A draft is cleared once its video is actually
    exported (the export supersedes it) but survives a reject.
  - **"Remove from queue" is a third, separate action from Reject.** It only shows once a
    video is exported (`exportedAt` truthy) and its own confirm popup (`VerdictButtons.jsx`)
    fires `POST /motion-review/remove` *alone* — it deliberately skips
    `POST /motion-review/decision`, which is the only route that retracts a video's savings
    credit (`record_decision` → `_apply_savings`). `queue_removal.remove_from_queue` itself
    never touches `savings.json` either way; Reject's retraction happens entirely on the
    `/decision` side, before its own `/remove` call. The Settings > Storage bulk "purge
    working copies" feature (`backend/storage.py`) does exactly this — loops
    `remove_from_queue` over every `owned` queue entry and never calls `record_decision`,
    for the same reason.

- **Chip queries are the single source of truth.** The junk-cull chips live in the exported `CHIPS` array in `SearchChips.jsx`. Junk Hunt re-imports `CHIPS` and fires all of them in parallel — edit the list in one place. Each chip is `{id, emoji, label, query}`; only `query` goes to CLIP. `id` is the persisted dismissal-ledger key (`photo_db/dismissed.json`) — renaming one orphans its dismissals, rewording `query` is free.
- **"Show in Photos" auto-bumps the delete counter; "Hide from this filter" must never copy that pattern.** On a successful `/reveal`, `OpenInPhotosButton` calls `incrementDeleteCount()` (an optimistic "about to delete" proxy) via `StatsContext`. The two live side by side in the photo detail modal but are opposites: dismissing a photo from a filter (`POST /filters/dismiss`, `backend/dismissed.py`) only ever writes `dismissed.json` — no `/reveal`, no stats write, no touch to the photo. It's a display filter, not a delete signal.
- **For a new modal, copy `BulkAddPad`'s close pattern, not `App.jsx`'s photo-detail `Modal`.** The latter is older and only closes on outside-click — no Esc, no scroll-lock. `BulkAddPad`/`VerdictButtons`' `useEffect` (Esc + outside-click listeners added on open, removed on close) is the canonical one; `SettingsModal` follows it.

### Wiring that lives closer to the code

These load automatically in the folder they describe — read them there, don't copy them up:

- `backend/CLAUDE.md` — ffmpeg render/export invariants, path/id validation, the stats and
  savings ledgers, `/reveal`, dates, and the UMAP/clustering caveats.
- `photo-search/src/components/motion-review/CLAUDE.md` — preview playback and timeline UI.
- `docs/CODEBASE_MAP.md` — **not in the repo.** A local, gitignored, regenerable navigation
  map built by the Cartographer plugin; a fresh clone has no `docs/`. Run `/cartographer`
  to build it, and again when it's stale. Never treat its absence as a bug.

## Working with the user

- Comfortable with Python; newer to React — explain React concepts clearly when they come up.
- Analogies help when introducing something new.

## Status

As of 2026-08-22: 56,773 photos in ChromaDB, every row carrying `date_taken` (int, Unix
seconds UTC); 56,612 carry Graph View layout coords. Re-measure with `collection.count()`
rather than trusting this line. Repo is a single `main` branch in sync with `origin/main`
— no worktrees, no stashes.

`RODEMAP.md` holds current status, the shipped-work log, the verified list of **known gaps
(features that do not exist yet — check there before assuming one does)**, and next steps.
