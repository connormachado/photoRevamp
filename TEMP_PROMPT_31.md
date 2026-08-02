# Non-blocking video export (background job + polled progress)

## Context

**The problem.** `POST /motion-review/export` (`backend/server.py:476`) is fully synchronous.
It calls `motion_review.export_to_photos` (`motion_review.py:567`), which blocks on
`export_video.export_and_import` → `render_plan`'s `subprocess.run(ffmpeg …)` at
`export_video.py:203` — a full `libx264 -preset medium -crf 18` re-encode. On a long
climbing clip that is minutes. The browser side is a bare `await fetch` with no
`AbortController` and no timeout (`MotionReviewApp.jsx:101-146`); the only feedback is a
static "rendering & importing — this takes a few seconds…" string in `VerdictButtons.jsx`.
A long export therefore looks like a hang, and if the browser gives up first the user is
told "Could not reach the backend" while the export is in fact succeeding server-side.

**The outcome we want.** Kick-off returns immediately with a job id; the UI polls a status
endpoint and shows real progress; the import-to-Photos + reveal + savings-log happen on
completion; a second export cannot be launched while one is in flight; the original video
is still never touched.

**The pattern being reused.** `backend/embed_job.py` + `photo-search/src/components/EmbedButton.jsx`
— atomic merge-write JSON status file, `state` machine, stale-job downgrade, `{started,
reason_code, reason, status}` kickoff shape, `409` on already-running, and a `setInterval`
effect keyed on a `busy` boolean with `clearInterval` cleanup.

**Three decisions already made with Connor:**
1. **Global one-at-a-time.** Stricter than the written requirement (which only asked for a
   per-video guard) — one export anywhere at a time. Keeps the machine responsive against
   preview transcodes, and gives the status file exactly one writer.
2. **Reject and Remove are refused (409) while an export is in flight.** Non-blocking export
   removes the UI interlock (`busy = rejecting || removingOnly || exporting`,
   `VerdictButtons.jsx:90`) that currently makes this unreachable. Left open, a Reject landing
   mid-render races `_apply_savings`' read-modify-write and can leave `reviews/<id>.json`
   reading `verdict: "reject"` *and* `exported_at`; a Remove can unlink the source out from
   under a running ffmpeg, or unlink the proposal so the post-render `record_decision` raises
   after the clip is already in Photos.
3. **Real percent from ffmpeg, behind an opt-in callback.** The flags are added *only* when a
   caller passes `progress_cb`, so every existing call site and test keeps today's exact argv
   and today's `subprocess.run`.

---

## Design decisions and why

### A thread, not a detached subprocess (departs from `embed_job.py`)

`embed_job` shells out for a reason its own docstring states: `index_photos` would "load a
second copy of CLIP onto the MPS device *in the request thread*." That is real GIL-holding
Python work. Export is the opposite — every slow second is already spent inside
`subprocess.run(ffmpeg)` or `osascript`, both of which release the GIL. Flask's `app.run()`
defaults `threaded=True`, so polls are served concurrently either way. A second interpreter
would buy nothing and cost four things:

- **The test suite would lose the export path.** `tests/conftest.py`'s `isolate_stats` and
  `tmp_motion_db` are `monkeypatch.setattr` on module objects *in this interpreter*. A child
  process inherits none of them and would write the real `stats.json` and the real
  `photo_db/motion_review/`.
- **The ledger writers would move out of reach of a lock.** `_apply_savings`
  (`motion_review.py:457`) is a read-modify-write of `savings.json` and then of the repo-root
  `stats.json` *shared with the photo-cull counter*. In-process that is one `threading.RLock`;
  cross-process it is `fcntl.flock` on four files.
- **`start_new_session=True` would orphan the encode.** Ctrl-C in `make start` would leave a
  detached job to finish minutes later, import into Photos and write the ledger with no
  server running.
- **osascript / TCC.** Photos automation from a detached grandchild is an untested variable
  under an already-flaky boundary (`backend/CLAUDE.md` records the spotlight script's
  "unconfirmed intermittent failure").

Trade accepted: an export does not survive a server restart. Correct for a local single-user
tool, and the failure mode is the invariant we already promise — the ledger is written only
after render+import succeed, so a killed export leaves no phantom approval.

### Stale detection by boot id, not by pid

`embed_job._pid_alive` uses `os.kill(pid, 0)`. macOS recycles pids within hours; a reused pid
would wedge the guard permanently, and there is no UI escape hatch. Instead: `BOOT_ID =
uuid4().hex` at module import, stamped into every write. On read, a non-terminal state whose
`boot_id` differs from the current one is *unconditionally* stale — a previous server process
cannot own this process's thread. Same-boot staleness is checked exactly, by asking whether
the registered `Thread` is alive. Plus a wall-clock ceiling so a wedged-but-alive thread can't
block forever.

### The sequence stays one plain synchronous callable

`motion_review.export_to_photos` keeps its body, its ordering invariant, and its signature
apart from one new optional kwarg. The job module only *calls* it. This is what keeps
`tests/test_source_immutability.py` real: that suite drives `export_to_photos` directly and
its assertions (`assert outputs, "no render was recorded — this test proved nothing"`,
`assert vanished, "no file was deleted at all"`) only have teeth because the render happens
in-process under `fake_run`. **Those guard assertions must not be edited during this work.**

---

## Implementation

### Step 0 — save the prompt

Write the task prompt verbatim to `prompts/export-background-job-prompt.md`, matching the
existing files in `prompts/`.

### Step 1 — `backend/export_video.py`: opt-in progress

- `render_plan(source_path, plan, out_name=None, metadata=None, progress_cb=None)`.
  `progress_cb` is `(frac: float) -> None`.
- Only when `progress_cb` is truthy, append `["-progress", "pipe:1", "-nostats"]` to `cmd`
  after the existing shared encoder tail and before `cmd.append(str(encode_target))`.
  With no callback the argv is byte-for-byte what it is today.
- New module-private `_run_encode_with_progress(cmd, total_seconds, cb, tmp_dir)`:
  - `subprocess.Popen(cmd, stdout=PIPE, stderr=<file in tmp_dir>, text=True)`.
    **stderr goes to a file, not a pipe** — reading stdout to EOF while stderr fills its
    64 KiB pipe buffer deadlocks. `tmp_dir` already exists and is already `rmtree`'d in the
    existing `finally`.
  - Iterate `proc.stdout`, parse `out_time_us=` (fall back to `out_time=HH:MM:SS.ffffff`),
    `frac = clamp(secs / total_seconds, 0, 1)`. Invoke `cb` only when `int(frac * 100)`
    changes, so a 2-minute encode writes ~100 status files rather than ~240.
  - `proc.wait()`, read the stderr tail, return a `CompletedProcess`-shaped object so the
    caller's existing `if proc.returncode != 0 …: raise RuntimeError(f"ffmpeg render failed:
    {(proc.stderr or '')[-600:]}")` branch is unchanged.
  - No parseable progress must not be an error — some builds emit `out_time_ms` only.
- Denominator: `sum(p.output_duration for p in pieces)` off the already-normalized pieces
  (`Piece.output_duration` divides by speed, so a 2× region can't report 200%). This is the
  same number as `trimmed_duration`.
- The filtered path's second pass, `_strip_display_matrix`, gets **no flags and no changes** —
  it is a sub-second stream copy and it is the fix for the rotation invariant. It simply sits
  at render-100%.
- `export_and_import(…, progress_cb=None)` where the callback is `(phase, frac|None)`:
  `("rendering", f)` forwarded from `render_plan`, then `("importing", None)` and
  `("revealing", None)` before their respective calls. `export_video` owns the phase
  vocabulary; it has no opinion about overall percent.

### Step 2 — `tests/conftest.py`: `FakePopen`

`_no_real_subprocess` (conftest:215) blocks `Popen`; `fake_run` (conftest:238) fakes only
`run`. Add `fake_run.install_popen()` — a `FakePopen` that records a `RecordedCall` into the
**same** `fake.calls` list (so `ffmpeg_calls`, `calls_to`, `flag_value` and the immutability
suite's argv sweep keep working), exposes `.stdout` iterating a scriptable
`fake.popen_stdout` line list, `.stderr`, `.wait()`, `.returncode`, `.pid`, and honours
`fake.side_effect` so the existing "ffmpeg creates the file it was told to write" hooks still
fire. Separate opt-in method, not folded into `install()`, so existing tests are untouched.

### Step 3 — `backend/motion_review.py`: one kwarg, one lock

- `export_to_photos(video_id, regions=None, cut_segments=None, progress_cb=None)` — forwards
  `progress_cb` into the `export_video.export_and_import(...)` call at line 609. **Nothing
  else in the function moves.** Requirement "savings credited on completion" is satisfied by
  the existing ordering: `record_decision` already runs only after render+import return.
- Add a module-level `_LEDGER_LOCK = threading.RLock()` and hold it across `record_decision`'s
  body (which reaches `_apply_savings` → `savings.json` → `stats_store.set_climb_cutter_bytes`
  → `stats.json`) and across `export_to_photos`' step-4 read-modify-write of
  `reviews/<id>.json`. Reentrant because `record_decision` calls `_apply_savings` internally.

Re-run `tests/test_source_immutability.py` after this step, before anything asynchronous
exists.

### Step 4 — `backend/export_job.py` (new module)

State file: **one** file, `photo_db/motion_review/export_job.json`, holding the single
current-or-most-recent job. One writer by construction, given the global one-at-a-time guard.

```python
def _job_path():  # a function, NOT a module constant — see below
    return motion_review.MOTION_DIR / "export_job.json"
```

It must be a function so it follows `tmp_motion_db`'s redirect of `motion_review.MOTION_DIR`.
Exact precedent and rationale: `queue_removal._uploads_dir()`, documented in `backend/CLAUDE.md`.

Schema (`IDLE_JOB` merged over on read, like `embed_job.IDLE_STATUS`):

```jsonc
{
  "job_id":   null,          // uuid4().hex[:12] — the UI's once-per-run latch
  "video_id": null,          // echoed so the UI never misattributes a poll
  "state":    "idle",        // idle | queued | rendering | importing | revealing | done | failed
  "progress": 0.0,           // overall 0..1, monotonic
  "started_at": null, "finished_at": null,
  "error":    null,
  "boot_id":  null,
  "result":   null           // the full export_to_photos payload, verbatim, on success
}
```

`result` is load-bearing: it is exactly what `POST /motion-review/export` used to return,
which is what lets the frontend's completion hand-off move from the fetch response to the poll
with no reshaping.

Module internals:

- `BOOT_ID = uuid4().hex`, `_LOCK = threading.Lock()`, `_LIVE: threading.Thread | None`.
- `write_status(updates)` — merge + tmp-file + `os.replace`, copied from `embed_job.write_status`.
- `read_status()` — merge over `IDLE_JOB`, then under `_LOCK` downgrade a non-terminal state to
  `failed` when `boot_id != BOOT_ID`, or `_LIVE` is None/not alive, or `started_at` is older
  than the wall-clock ceiling. Error text: "The export stopped before it finished (did the
  server restart?)".
- `is_exporting(video_id=None)` — the predicate `/decision` and `/remove` consult. Returns the
  in-flight job's `video_id` or None.
- `start_export(video_id, regions=None, cut_segments=None) -> {started, reason_code, reason, status}`:
  1. `safe_paths.safe_id_component(video_id)` — raises `UnsafePathError` (a `ValueError`).
  2. **Synchronously** confirm the proposal exists (`FileNotFoundError` if not). This is not
     cosmetic: `tests/test_route_security.py` and `tests/test_input_validation.py` assert a
     traversing id and an unknown video are client errors on this route, and after this change
     a 202 followed by an exception in a worker thread would satisfy neither. `backend/CLAUDE.md`
     already warns that the traversal route sweeps stay green when the guard is gutted.
  3. Under `_LOCK`: `read_status()`; if non-terminal →
     `{"started": False, "reason_code": "already_running", …}`.
  4. Still under `_LOCK`: build the `Thread(daemon=True)`, assign `_LIVE`, write the `queued`
     status with the new `job_id`/`boot_id`/`video_id`, then `t.start()`. Holding the lock
     across all four closes the TOCTOU window; `read_status` takes the same lock, so no poll
     can see `queued` with a not-yet-started thread and call it stale.
  5. Wrap the spawn in `try/except` — an exception there must write `failed`, never leave
     `queued` forever.
- Thread body: call `motion_review.export_to_photos(..., progress_cb=_on_progress)`; on
  success write `{state: done, progress: 1.0, finished_at, result}`; on any exception write
  `{state: failed, finished_at, error: f"{type(e).__name__}: {e}"}`; in a `finally`, clear
  `_LIVE` under `_LOCK`. **Terminal status is written before `_LIVE` is cleared** — the stale
  check only fires on non-terminal states, so no poll can catch a done job looking dead.
- `_on_progress(phase, frac)` owns the overall-percent band table, so `export_video` doesn't
  have to: `rendering → 0.90 × frac`, `importing → 0.90`, `revealing → 0.97`, `done → 1.0`.
- Import `motion_review` at module scope (fine — `server.py` imports both). `motion_review`
  must **not** import `export_job`; `server.py` and `queue_removal` consult it instead.

### Step 5 — `backend/server.py` (routing only, stays thin)

Keep the URL `POST /motion-review/export` — smallest diff, one URL in the frontend, and the
three existing route sweeps stay pointed at a live route instead of a corpse.

```python
result = export_job.start_export(video_id, data.get("regions"), data.get("cut_segments"))
```
with `ValueError → 400`, `FileNotFoundError → 404`, `already_running → 409`, success →
`202` with the kickoff payload (which carries `job_id`). The `except RuntimeError → 500`
clause is dropped: a render failure now lands in the status file, which is the point.

New `GET /motion-review/export/status` — no arguments, returns `export_job.read_status()`.
Add it to `tests/test_input_validation.py`'s `GET_ROUTES` sweep so it inherits the
never-500 rule.

`POST /motion-review/decision` and `POST /motion-review/remove` gain a guard at the top:
if `export_job.is_exporting()` matches this `video_id`, return `409` with a plain message.

### Step 6 — frontend

`MotionReviewApp.jsx`:
- Replace the `exporting` boolean with `exportJob` (the whole status object) plus
  `handledJobRef` latching on `job_id` — the client-side equivalent of `server.py:354`'s
  `_embed_reloaded_for`.
- `runExport` keeps its guard (add a `useRef` in-flight latch alongside the state — two clicks
  in one tick both read the same closed-over `false`) and its POST, but stops awaiting the
  work: on `202` store `data.status`; on `409` show "An export is already running" and store
  the returned status; on other errors show `data.error`. No `finally { setExporting(false) }` —
  the poll owns the lifecycle now.
- Mount effect: one-shot `GET /motion-review/export/status`, so a page reload mid-render
  resumes the bar instead of showing an idle green SAVE. Same rationale as
  `EmbedButton.jsx:18-23`.
- Poll effect keyed on `busy` (`state` non-terminal), `POLL_MS = 1000` rather than embed's
  2000 — a bar that steps every two seconds reads as broken. `clearInterval` in the cleanup,
  exactly as `EmbedButton.jsx:30-41`.
- Completion effect, latched on `job_id` so it fires once no matter how many polls arrive:
  on `done`, set the result message and call `loadQueue()` + `refreshStats()`. **`loadQueue()`
  replaces the whole `setVideos(prev => prev.map(...))` fold and the `savings_total_bytes`
  branch** (a net deletion): the hand-fold keys on a closed-over `selectedVideoId` and would
  write the wrong row if the user switched videos mid-export, whereas `/queue` and `/savings`
  are the authority and already carry every field being folded. On `failed`, show
  `status.error`.
- Attribution: anything derived from the job checks `exportJob.video_id === selectedVideoId`
  before rendering per-clip chrome, so a poll for the exporting clip never decorates the one
  currently on screen.
- Pass the per-video `exporting` (i.e. `job is live && job.video_id === thisVideo`) into
  `VerdictButtons`, not a global "something is exporting" — otherwise
  `VerdictButtons.test.jsx`'s "does not offer the confirm while an export is running" tests
  keep passing while testing the wrong thing.

`VerdictButtons.jsx` — changes confined to the status-line block plus one new prop:
- Phase text: `starting…` / `rendering… {pct}%` / `importing into Photos…` / `revealing…`.
- The `EmbedButton` progress bar underneath: a 90×4px track with a `width: {pct}%` fill,
  `transition: width 0.3s`, in the room's `#5eead4` accent. `pct` computed client-side.
- **An import failure must render as a failure.** Today `export_and_import` returns
  `imported: {success: False, error}` *inside* the payload rather than raising, so
  `export_to_photos` still credits savings and `runExport` still says "Saved to Photos…"
  because it only branches on `revealed.success`. Behind a poll the user would never notice.
  Surface `result.imported.success` and say so plainly. (Whether crediting should *also* be
  gated on `imported.success` is a product change — flagging it, not doing it here.)
- Reject / Remove disabled with an explanatory line while this clip is exporting, matching the
  new 409.

### Step 7 — docs

One bullet in `backend/CLAUDE.md`: why the export job is a thread while the embed job is a
subprocess, and that the `-progress` flags are gated behind `progress_cb` so the default argv
is unchanged. Amend the "Saving a clip IS approving it" bullet in `/CLAUDE.md` to note the
export is now asynchronous, that the ledger ordering is unchanged, and that Reject/Remove are
refused during an export. Add the shipped entry to `RODEMAP.md`.

---

## Tests

Run `/write-tests backend/export_job.py` for the job-state and concurrency-guard suite
(the conditional the task specifies). It should cover:

1. `start_export` returns before a blocked stub `export_to_photos` is released — the actual
   non-blocking claim.
2. `read_status` walks `queued → rendering → done`; `result` carries the payload verbatim.
3. **Savings credited only on completion** — with the job blocked inside the render, assert
   `get_savings()["per_video"]` is empty and `decisions.jsonl` has no `"action": "export"`
   line; release; join; assert both appear.
4. A second `start_export` while one is in flight → `{"started": False, "reason_code":
   "already_running"}`, and only one render is recorded by `fake_run`. Assert on the render
   count, not just the return value.
5. Staleness, three ways — foreign `boot_id`, dead thread, past the wall-clock ceiling — each
   reads as `failed` **and** permits a new `start_export`. This is the test that stops the
   guard wedging forever.
6. Failure injection: the stub raises → `state: failed`, error non-empty, and **nothing**
   written to `savings.json` or `decisions.jsonl`.
7. A traversing `video_id` raises before any thread is created and writes no file.

Plus, outside the generated suite:
- `tests/test_export_args.py` — new `TestProgressReporting`. The load-bearing one is a
  **byte-for-byte pin**: build the concat argv with and without `progress_cb` and assert the
  only delta is exactly `["-progress", "pipe:1", "-nostats"]`. That makes the `backend/CLAUDE.md`
  rule mechanical rather than aspirational. Also: default argv contains no `-progress`;
  `_strip_display_matrix`'s argv is identical either way; scripted `out_time_us=` lines produce
  monotonic fractions in `[0,1]`; unparseable stdout still completes; a 2× speed region does
  not report 200%.
- `tests/test_input_validation.py` — `/motion-review/export/status` into `GET_ROUTES`;
  `POST /motion-review/export` returns 409 while in flight; `/decision` and `/remove` return
  409 while in flight.
- `VerdictButtons.test.jsx` — the phase/percent line and bar render from the job prop.

---

## Verification

1. **`make test`** — full pytest + Vitest. `tests/test_source_immutability.py` must be green
   **and non-vacuous**: confirm the `assert outputs, "no render was recorded — this test proved
   nothing"`, `assert vanished, "no file was deleted at all"` and `assert not draft.exists()`
   lines are still present and still reached. That suite going quietly green is the specific
   failure mode to watch for in this change.
2. **`cd photo-search && npm run build`** clean, and `npm run lint` shows **no new** errors
   (~12 pre-existing `react-hooks/set-state-in-effect` / `react-refresh/only-export-components`
   are the baseline).
3. **Byte-identity of the render, empirically.** Render one real plan twice against the same
   source — once through `render_plan(...)` and once through `render_plan(..., progress_cb=…)`
   — and `md5` both outputs. They must match. This is the check that discharges the
   `backend/CLAUDE.md` byte-for-byte invariant; reasoning that reporting flags are inert is not
   sufficient, since that invariant was established empirically in the first place.
4. **Live: a large video.** `make start`, open the review room, export a multi-minute clip.
   Expect: the POST returns instantly with a `job_id`; the bar advances; the room stays
   responsive (scrub the timeline, switch clips); no browser timeout; on completion the asset
   is in Photos at the original's date, the original is untouched, and the reclaimed total
   moves on both the review room and the main page's `DeleteCounter`.
5. **Concurrency refusal.** While that export runs, click SAVE again → refused with a clear
   message, exactly one asset in Photos. Then confirm Reject and Remove are refused too, and
   become available again once the job reaches `done`.
6. **Wedge recovery.** With an export running, kill and restart `server.py`; the status must
   read `failed` (not a permanent spinner) and a fresh export must start.
7. **Failure path.** Point a proposal at a corrupt source so ffmpeg fails; the job must land
   `failed` with the stderr tail, and `savings.json` / `decisions.jsonl` must be untouched.

No `git commit`, no `git push` at any point.

---

## Critical files

| File | Change |
|---|---|
| `prompts/export-background-job-prompt.md` | new — the task prompt, verbatim |
| `backend/export_job.py` | **new** — boot-id job state, global guard, thread runner, band table |
| `backend/export_video.py` | opt-in `progress_cb` on `render_plan`/`export_and_import`; `_run_encode_with_progress` |
| `backend/motion_review.py` | `progress_cb` pass-through in `export_to_photos`; `_LEDGER_LOCK` |
| `backend/server.py` | export route → kickoff (202/409); new status route; 409 on `/decision` + `/remove` |
| `tests/conftest.py` | `fake_run.install_popen()` + `FakePopen` |
| `tests/test_export_job.py` | **new** — via `/write-tests` |
| `tests/test_export_args.py` | `TestProgressReporting`, incl. the byte-for-byte argv pin |
| `photo-search/src/components/motion-review/MotionReviewApp.jsx` | job state, kickoff, mount fetch, poll, latched completion |
| `photo-search/src/components/motion-review/VerdictButtons.jsx` | phase label, progress bar, import-failure message |

## Deliberately out of scope

- Polling for a video you have navigated away from. The job completes correctly regardless of
  the browser; the mount fetch and `loadQueue()` pick the result up.
- Making `render_plan` write to a per-job temp then `os.replace` into `out_path`. A killed
  ffmpeg on the plain path leaves a partial file at the canonical name — a real but
  **pre-existing** bug, not one this change introduces. Worth its own fix.
- Gating savings credit on `imported.success` (a product change).
- `reveal_in_photos` steals focus via `tell application "Photos" to activate`. Today the user
  is watching a spinner; with a background job Photos will jump to the front mid-edit. Keeping
  reveal in the job as specified, but flagging the behaviour.
