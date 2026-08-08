# Close the ffconcat injection hole

## Context

The concat demuxer's list file is a mini script, not a data file. All three writers in
this repo build its `file` directive with a bare f-string:

```
lf.write(f"file '{src.resolve()}'\n")
```

A `'` in the source path closes the quote early, so the remainder of the path is parsed as
ffconcat directives. A newline is worse: `concatdec.c` parses line-by-line, so a newline
in a path starts a brand-new directive with no possible escape.

`tests/test_command_safety.py::TestFfconcatQuoting::test_a_quote_in_the_source_path_cannot_inject_a_directive`
already encodes this attack and is marked `xfail(strict)` — deliberately deferred to a
hardening pass. This is that pass. The code changes; the test does not.

Not reachable from the web app (uploads go through `secure_filename` + an extension
allowlist in `video_upload.py`). It is reachable from the CLI ingest path, where
`video_motion.process_video` takes an arbitrary path — which is why the fix covers
`video_motion.py`, not just the export path the test touches.

## The design constraint that picked the approach

ffconcat uses ffmpeg-utils quoting (`av_get_token`): inside single quotes everything is
literal and a `'` cannot appear; the canonical escape is shell-style `'\''`. But that
emits `file '/tmp/cl'\''ip.mov'`, whose interior contains `'` — which the existing test's
`assert "'" not in quoted[1:-1]` rejects. The test, read literally, demands the stronger
property: no quote character is ever written into `list.txt`.

So instead of escaping, we never emit an unsafe path: when a resolved source path
contains a character we won't quote, stage a symlink under a safe name inside the render's
own `mkdtemp` and write that path. Normal paths are written verbatim, byte-identical to
today. This also means correctness does not depend on my reading of ffmpeg's escaping rules.

---

## Changes

### 1. New `backend/ffconcat.py` — the single quoting authority

Dependency-free (no numpy/ffmpeg imports), so both `export_video` and `video_motion` can
import it with no cycle. Per CLAUDE.md's "one feature per backend file". Contents:

- `class UnsafeConcatPathError(ValueError)` — a path that cannot be represented at all.
- `REFUSE = ("\n", "\r", "\x00")` — no ffconcat representation exists. Raise.
- `ALIAS = ("'", "\\")` — representable in principle, but we decline to emit them. Stage a
  symlink instead. (`\` is included belt-and-braces: it costs nothing and removes any
  dependence on whether backslash is literal inside single quotes.)
- `concat_path(src: Path, stage_dir: Path) -> str` — resolve, refuse REFUSE chars, return
  the path string unchanged when clean, else return the staged alias path.
- `file_line(src, stage_dir) -> str` — returns `f"file '{concat_path(...)}'\n"`.

Alias staging details:

- Name is deterministic per source so N pieces of one video reuse one symlink:
  `f"src_{md5(str(resolved).encode()).hexdigest()[:8]}{suffix}"`, where `suffix` is
  `src.suffix` only if it matches `^\.[A-Za-z0-9]+$`, else empty. (ffmpeg probes content;
  the extension is cosmetic.)
- `os.symlink(resolved, alias)`, tolerating `FileExistsError` idempotently.
- Final guard: if the produced alias string itself contains anything in REFUSE or
  ALIAS, raise rather than emit. `stage_dir` is always an `mkdtemp` (or pytest `tmp_path`)
  so this never fires — but it fails loud instead of silently writing an unsafe line.

The symlink is created by us, inside our own temp dir, pointing at an already-resolved
path — it is not the attacker-controlled-symlink shape that `queue_removal._owned_source`
and `safe_paths.resolve_within_roots` exist to refuse. Worth a comment saying so, since this
repo otherwise treats symlinks as a threat.

### 2. Route all three writers through it

| File | Function | Change |
|---|---|---|
| `backend/export_video.py:346` | `_concat_demuxer_cmd` | `lf.write(ffconcat.file_line(src, tmp_dir))` |
| `backend/video_motion.py:344` | `make_trimmed_clip` | same; drop the hoisted `abs_src` local |
| `backend/video_motion.py:418` | `make_cuts_timelapse` | same (its paths are our own temps, so this is a no-op in practice — routed for consistency) |

Leave `make_cuts_timelapse`'s missing ffconcat version 1.0 header alone; out of scope.

A refusal propagates out of `render_plan` → `export_job`, which already catches broad
`Exception` (`backend/export_job.py:253`, `:315`) and marks the job failed. No new
error plumbing needed.

### 3. Flip the xfail

Delete only the `@pytest.mark.xfail(...)` decorator block at
`tests/test_command_safety.py:314-327`. The test body is untouched.

### 4. Additive tests (new, not replacements)

In `TestFfconcatQuoting`:

- The CLI-reachable writer: `video_motion.make_trimmed_clip` with a `cl'ip.mov` source
  (needs `fake_run`, since conftest makes unmocked `subprocess.run` raise) — assert no
  quote reaches `list.txt`.
- Newline refusal: a path containing `\n` raises `UnsafeConcatPathError` rather than
  emitting a line.
- Alias reuse: two pieces of one hostile source produce one symlink and two `file` lines
  pointing at it.

### 5. Save the prompt

Write the task prompt verbatim to `prompts/ffconcat-quoting-fix-prompt.md`, matching the
style of the existing files there.

---

## Verification

1. Empirical ffmpeg check (do this first — it validates the premise). In a scratch dir,
   create real clips named `cl'ip.mov` and `back\slash.mov`, render each through
   `export_video.render_plan`, and confirm both produce a playable output. This is the only
   proof that the alias actually feeds ffmpeg the right file; it is not a unit test and does
   not go in `tests/`.
2. `make test` green, with
   `test_a_quote_in_the_source_path_cannot_inject_a_directive` reported as PASSED, not
   xfailed. Confirm with `.venv/bin/python3 -m pytest tests/test_command_safety.py -v -rX`
   (`-rX` prints xpasses; the run should show none for this test). Note there is no
   `-m "not slow"` filter in `pytest.ini`, so this slow-marked module does run.
3. Normal path unchanged — `TestConcatDemuxerCmd` (all three cases, `tests/test_export_args.py:307`)
   and `TestFfconcatQuoting::test_a_normal_path_is_quoted` still pass untouched. The plain
   render path's argv must stay byte-identical; `tests/test_export_args.py::TestProgressReporting`
   pins that.
4. Prove teeth. Temporarily restore `lf.write(f"file '{src.resolve()}'\n")` in
   `_concat_demuxer_cmd`, confirm the formerly-xfail test goes RED, then restore the fix
   and confirm green again.
5. `npm run lint` — no frontend files change, so the 10 pre-existing errors should be
   exactly unchanged.

No commits, no pushes at any point.
