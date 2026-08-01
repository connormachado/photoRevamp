# Test harness + reusable test-author agent — originating prompt

Saved verbatim as the spec this work was built from. See the plan that came out of it and
`tests/`, `.claude/agents/test-author.md`, `.claude/commands/write-tests.md` for the result.

---

Do NOT commit or push.

Goal: go from zero tests to (a) a working test harness and (b) a reusable "test-author"
agent that can generate a suite for any module I point it at. This is groundwork for
eventually sharing the app publicly, so include security-sensitive cases. Do this in
PHASES — don't try to test everything at once.

**Step 0 — Inspect and report:**
- Python side: is pytest installed? Any existing tests anywhere? Which backend modules are
  PURE LOGIC (easy to test) vs. heavy on subprocess/AppleScript/ChromaDB/filesystem side
  effects (need mocking)? Name good first targets — likely candidates: byte accounting,
  dismissed.json logic, cut-segment/boundary math, the `ffmpeg -i` stderr metadata parser,
  segment-render arg-building.
- Frontend side: is a test runner configured? (Vitest is the Vite-native choice.)
- The file-serving routes (/thumbnail, /full, /reveal) and any route that passes a filename
  into the ffmpeg binary or AppleScript — these are the security-sensitive surfaces.
- Report, propose a phased plan, and pause.

**Phase A — Harness:**
- Python: add pytest (+ pytest-mock) to a requirements-dev file, a tests/ dir, and a
  conftest.py with fixtures that mock the external boundaries (the imageio_ffmpeg
  subprocess, osascript, the ChromaDB client, filesystem via tmp_path). Add a Makefile
  target `make test`.
- Frontend: add Vitest + React Testing Library, a test script in package.json, and one
  smoke test proving the runner works.

**Phase B — First real suites** (pick 2-3 pure-logic modules from Step 0):
- Assert INTENDED BEHAVIOR and edge cases: empty input, boundary values, malformed ffmpeg
  stderr, over-fetch trimming in the dismissal logic, byte-total math including the bulk
  estimate. Do NOT write tests that just mirror the current implementation line-for-line.
  If a test reveals a real bug, FLAG it — don't silently write the test to match buggy
  output.

**Phase C — Security-sensitive tests** (the "robust enough to ship" part):
- Path traversal: /thumbnail, /full, /reveal must reject uuids/paths that escape the
  library root (e.g. `../../etc/passwd`). Write tests proving traversal is blocked. If it
  ISN'T currently blocked, STOP and tell me — that's a real vuln to fix first.
- Argument/command injection: filenames or user input flowing into ffmpeg args or
  AppleScript strings must be escaped/validated (a quote in a filename breaking out of an
  AppleScript string is a genuine risk). Test that a hostile filename can't break out.
- Basic input validation on POST routes: missing/garbage fields return a 4xx, not a 500 or
  a crash.

**Phase D — The reusable test-author agent:**
- Add `.claude/agents/test-author.md` — a subagent (read + write-to-tests-only +
  bash-to-run-tests) whose job: given a target module, read it, infer its intended
  behavior, and generate a pytest (or Vitest) suite covering happy path + edges + relevant
  security cases, mocking external boundaries. Its instructions MUST say: test
  behavior/spec, not the implementation; never weaken or delete a failing test just to make
  it pass — surface suspected bugs to me instead.
- Add `.claude/commands/write-tests.md` — a slash command `/write-tests <path>` that runs
  the test-author on that file, runs the resulting suite, reports pass/fail, and pauses for
  my review before anything is kept.

**Reality check to state back to me:** LLM-generated tests encode whatever behavior they're
told to expect, so I still review them, and they don't replace a proper security audit (a
dedicated hardening pass can be its own later prompt).

**Verification:**
- `make test` runs and passes; the Vitest script runs and passes.
- The security tests actually FAIL if I deliberately introduce a traversal/injection (prove
  they have teeth).
- `/write-tests <some module>` generates a runnable suite and reports pass/fail.

Save this prompt to `prompts/test-harness-and-agent-prompt.md`.

---

## Step 0 answers (recorded, since they changed the plan)

Two premises in the brief turned out to be wrong, and one turned out to be worse than
feared:

- **`dismissed.json` does not exist** — zero hits for "dismiss" anywhere in the repo. The
  savings-ledger idempotence logic in `motion_review._apply_savings` was substituted as the
  equivalent-shaped target.
- **`/reveal` is not path-based.** It takes a ChromaDB row id and resolves `apple_uuid`
  server-side (`server.py:184-206`) — the one file route that already did the right thing.
- **Path traversal was NOT blocked**, so per the brief's own instruction the work stopped
  and a hardening phase (C0) was inserted ahead of the security tests. `/full` and
  `/thumbnail` took a raw `?path=` straight to `send_file` with no validation, and
  `CORS(app)` with no arguments made that reachable from any web page the user visits.
