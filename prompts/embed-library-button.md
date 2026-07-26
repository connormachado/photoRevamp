# Prompt: Re-embed Library button (background job + progress UI)

Built 2026-07-25. Original prompt below, followed by the Step 1 decisions.

---

## Original prompt

# Claude Code — Build a "Re-embed Library" button (background job + progress UI)

## Context
photoApp: local-first photo search (CLIP + ChromaDB + Flask :5001 + React/Vite :5173),
repo at ~/Desktop/photoApp, single branch `main`, clean tree. I want a button in the web UI
that triggers embedding my photo library — running the SAME logic as backend/embed_photos.py —
so routine catch-up indexing never requires the terminal. Embedding is long-running, so this
MUST be a non-blocking background job with a progress indicator, not a synchronous request.

Heads-up: CLAUDE.md is known to be OUT OF DATE (it references files/routes that don't exist).
Trust the actual code you read, not CLAUDE.md.

## Step 0 — Inspect before writing anything; report back what you find
- Read backend/embed_photos.py fully and tell me: (a) is the embedding logic callable as a
  function, or only runnable as a __main__ script? (b) is it genuinely incremental/resumable —
  does it skip photos already in ChromaDB? (c) does it expose or print any progress/count, and
  how does it determine "total photos to process"? (d) what args/flags does it accept?
- Read server.py: note the existing thin-route pattern and how backend logic modules are imported.
- Note how the frontend talks to the backend today (base URL/port, any existing polling) and
  where stats live in the UI (StatsContext.jsx / header) — the button should sit near existing controls.
- Confirm whether an embed process could already be running.

## Step 1 — Ask me, then pause for a plan (no code yet)
1. Scope: default = safe INCREMENTAL catch-up only (add new, skip existing — NEVER wipe or
   re-embed existing photos). Confirm. Also: expose a separate, clearly-labeled "full re-embed"
   behind a confirm dialog, or leave full-refit terminal-only for now?
2. Job model: my strong preference is a SUBPROCESS (keeps heavy CLIP/MPS work out of the Flask
   process so the server keeps answering status polls), with progress written to a small status
   file (e.g. photo_db/embed_status.json: {state, done, total, started_at, error}). Confirm or
   propose better based on what embed_photos.py actually supports.
3. Where the button + progress bar should live in the UI.
Pause for my go-ahead before building.

## Step 2 — Backend
- Add thin routes: POST /api/embed/start (launches the incremental embed as a background job)
  and GET /api/embed/status (returns current status JSON for polling).
- REUSE embed_photos.py's logic — do NOT reimplement embedding. If importable, call it; if
  script-only, shell out via subprocess. embed_photos.py stays the single source of embedding truth.
- CONCURRENCY GUARD: if a job is already running, /start must REFUSE (return "already running")
  rather than spawn a second process. This is the whole point — one canonical embed, never two
  stomping on ChromaDB.
- Progress: total = (photos on disk matching the derivative filter) − (already in ChromaDB),
  updating done/total as it goes. If clean progress is hard to extract, a coarse
  running/done/failed state is acceptable for v1 — tell me which you did.
- Use the existing ChromaDB chunked-pagination pattern for any bulk count queries (SQLite var limit).
- On failure, capture the error into the status file so the UI can display it.

## Step 3 — Frontend
- Add a "Re-embed library" button near the existing stats/header controls. On click → POST
  /api/embed/start, then poll GET /api/embed/status every ~2s.
- Show progress (done/total + bar if total known; spinner + "indexing…" if not). Disable the
  button while running. On completion show success + the new photo count; on error show the message.
- I'm newer to React — keep state simple (small hook or local state + setInterval cleared on
  unmount) and add a short comment explaining the polling loop.

## Step 4 — Verify (report what you OBSERVED, not just "no errors")
- Start servers, click the button, confirm: a job starts, status polls update, a SECOND click
  while running is safely refused, and the count reflects newly-added photos when done.
- Confirm the button does NOT trigger a destructive full re-embed.

## Step 5 — Lock it in
- Save this prompt + my Step 1 answers to prompts/embed-library-button.md.
- Add a one-line note to ROADMAP.md that the in-app embed trigger exists. (Leave CLAUDE.md's
  broader cleanup to a separate pass — just don't add to its drift.)

## Hard constraints
- No git commit, no git push, ever.
- Incremental/idempotent by default — never wipe or re-embed existing photos without an explicit,
  separate, confirmed action.
- Don't touch photo_db/models/ or trigger UMAP --full-refit.
- Don't modify ChromaDB schema/metadata fields without asking.
- Stop at every pause point; don't self-approve past them.

---

## Step 1 answers (Connor's decisions)

1. **Scope — incremental only.** No full-re-embed UI at all. A destructive rebuild stays
   terminal-only (`rm -rf photo_db`, per `build_flow.txt`). Smallest surface area; no
   library-destroying action reachable from the browser.
2. **Progress — subprocess + status file, exact progress.** Accepted one small additive change
   to `embed_photos.py`: an optional `progress_cb` parameter plus a `--status-file` flag.
   Default behavior when neither is passed is unchanged, so `embed_photos.py` stays the single
   source of embedding truth and the CLI works exactly as before.
3. **Photos root — the derivatives dir**, `~/Pictures/Photos Library.photoslibrary/resources/derivatives`.
   Note `build_flow.txt:11` points at `originals/`, which is stale: the hardcoded file filter in
   `index_photos` (`.jpeg` + `_5005_c` in stem) matches zero files there.
4. **Placement — beside Sync Library** in the header control row (`App.jsx`), matching
   `SyncButton.jsx`'s inline message pattern.
5. **Follow-up decision (b):** on a run completing, the server re-opens its ChromaDB collection
   (`reload_collection()`), so new photos become searchable and the header count updates without
   a backend restart. Chroma holds its HNSW index per-process, so without this the parent would
   keep serving the index it read at startup.

## What got built

| file | change |
|---|---|
| `backend/embed_job.py` | new — status file read/write (atomic), PID liveness check, subprocess spawn, concurrency guard |
| `backend/embed_photos.py` | additive — `progress_cb` param, `--status-file` flag, `run_with_status()` wrapper |
| `backend/server.py` | `POST /api/embed/start`, `GET /api/embed/status`, `reload_collection()` |
| `photo-search/src/components/EmbedButton.jsx` | new — button + 2s polling loop + progress bar |
| `photo-search/src/App.jsx` | mounts `EmbedButton`, `handleEmbedFinished` refreshes the header count |

## Notes for future sessions

- Status file is `photo_db/embed_status.json`; subprocess stdout/stderr goes to
  `photo_db/embed_log.txt`. Both are gitignored via `photo_db/`.
- States: `idle → scanning → running → done | failed`.
- The guard is two-layer: status says running **and** the PID is alive. A stale `running` from a
  killed process is downgraded to `failed` so the button can't wedge permanently.
- The child is spawned with `start_new_session=True`, so Ctrl-C on `make start` stops the servers
  without killing an embed mid-write.
- `sys.executable` is used to spawn, which inherits the arm64 venv → MPS rather than Rosetta.
