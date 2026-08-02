# Prompt: export as a non-blocking background job (progress endpoint + polling UI)

Planned 2026-08-02. Original prompt below, followed by the Step 0 findings and the
decisions taken before implementation. The full implementation plan lives in
`TEMP_PROMPT_31.md`.

---

## Original prompt

Do NOT commit or push.
Goal: run the export/render as a NON-BLOCKING background job with a progress endpoint the UI polls, so a multi-minute re-encode of a big video doesn't freeze or time out the request. Reuse the existing re-embed background-job pattern.

Step 0 — Inspect and report:
- How export is triggered today (approve -> export_video.py render): is it synchronous inside the request? Where would it block on a long file?
- The existing re-embed background-job implementation (the "Re-embed library" button): its job runner, status/progress endpoint, polling UI, and the concurrency guard that prevents duplicate indexer processes. This is the pattern to REUSE.
- How the UI currently waits on export today.
Report, propose a plan, and PAUSE.

Implementation:
1. Wrap export in a background job (reuse the re-embed job pattern — same runner/status mechanism). Kick off returns immediately with a job id.
2. A GET progress/status endpoint the UI polls; show progress; on completion, do the import-to-Photos + reveal + savings-log (savings credited on COMPLETION, not on kickoff).
3. A concurrency guard: can't launch a second export on the same video while one is running (guards against the known zombie-process failure mode).
4. Export still creates a NEW asset; original untouched; the never-touch-original guard must stay green.

Pause points: before changing the approve -> export flow (core pipeline) — propose the change and pause.

Verification:
- Build + lint clean; never-touch-original guard stays green.
- Export a large video: UI shows progress and stays responsive, no timeout; on completion the asset lands in Photos and savings are logged.
- A second export on the same video is refused while one is in flight.

Tests (conditional — job-state + concurrency logic): run /write-tests on the job-state / concurrency-guard code.

Save this prompt to prompts/export-background-job-prompt.md.

---

## Step 0 findings

**Export is fully synchronous inside the Flask request.** `POST /motion-review/export`
(`server.py:476`) makes one blocking call to `motion_review.export_to_photos`
(`motion_review.py:567`) → `export_video.export_and_import` (`export_video.py:555`) →
`render_plan`. The block is `subprocess.run(cmd, capture_output=True)` at
`export_video.py:203` running `libx264 -preset medium -crf 18` — a full re-encode of every
kept frame, not a stream copy. Five more blocking `subprocess.run` calls surround it: an
`ffmpeg -i` metadata scrape, and four `osascript` calls for the Photos import, date, GPS and
reveal. There are zero progress or callback hooks anywhere on that path.

**The UI does not really wait — it hopes.** `MotionReviewApp.runExport`
(`MotionReviewApp.jsx:101`) is a bare `await fetch` with no `AbortController` and no timeout;
nothing in `photo-search/src/` uses either. Feedback is a boolean `exporting` that disables the
domes and prints a static "rendering & importing — this takes a few seconds…"
(`VerdictButtons.jsx:295`). If the browser gives up before ffmpeg finishes, the catch shows
"Could not reach the backend" while the export is in fact succeeding server-side.

**The re-embed pattern** (`backend/embed_job.py`): a detached `subprocess.Popen` of
`embed_photos.py` with `start_new_session=True`; state in an atomic merge-written JSON file
(`photo_db/embed_status.json`) with `idle→scanning→running→done|failed`; a concurrency guard
requiring *both* a running state and `os.kill(pid, 0)` liveness, with stale states downgraded
to `failed` so the button can't wedge permanently; routes `POST /api/embed/start` (409 on
already-running) and `GET /api/embed/status`; `EmbedButton.jsx` polling on a 2s `setInterval`
keyed to a `busy` flag with `clearInterval` cleanup and a one-shot mount fetch. Progress is
written by the child through a `progress_cb`, not scraped from stdout. There are no tests for
any of it.

## Decisions taken before implementation

1. **A thread, not a detached subprocess** — departing from `embed_job.py`. It shells out
   because CLIP would hold the GIL in the request thread; export's heavy work is already
   out-of-process in ffmpeg. A child process would also inherit none of conftest's
   `isolate_stats` / `tmp_motion_db` monkeypatches (it would write the real `stats.json`),
   would put the `savings.json` / `stats.json` read-modify-writes out of reach of a lock, and
   would orphan a minutes-long encode past Ctrl-C.
2. **Stale detection by boot id, not pid.** macOS recycles pids within hours; a reused pid
   would 409 that video forever with no UI escape hatch.
3. **Global one-at-a-time**, stricter than the brief's per-video guard — keeps the machine
   responsive against concurrent preview transcodes and gives the status file one writer.
4. **Reject and Remove return 409 while an export is in flight.** Non-blocking export removes
   the UI interlock that currently makes this unreachable; left open, a Reject landing
   mid-render races `_apply_savings` and can leave a review reading both `verdict: "reject"`
   and `exported_at`, and a Remove can unlink the source out from under a running ffmpeg.
5. **Real percent from ffmpeg `-progress pipe:1`, gated behind an opt-in `progress_cb`** so
   every existing call site and test keeps today's exact argv and `subprocess.run`. Byte
   identity of the render is to be verified empirically by md5-comparing two real renders,
   not argued from the fact that reporting flags look inert.

Flagged, not fixed here: an *import* failure is already reported to the user as success
(`export_and_import` returns `imported: {success: False}` inside the payload rather than
raising, and the UI only branches on `revealed.success`) — visible today, invisible behind a
poll.
