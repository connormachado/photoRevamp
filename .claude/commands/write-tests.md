---
description: Generate a test suite for one file via the test-author subagent, run it, report pass/fail, and pause for review.
---
Generate tests for the file given in $ARGUMENTS.

1. **Check the target.** If $ARGUMENTS is empty, ask which file. If the path
   doesn't exist, say so and stop — don't guess at a similar name.

2. **Dispatch the `test-author` subagent** with the target path. Tell it to
   follow its own instructions exactly, and remind it of the two rules that
   matter most: test the intended behavior rather than the implementation, and
   never weaken or delete a failing test to reach green — surface suspected bugs
   instead.

3. **Run the suite yourself** once test-author reports back — don't take its word
   for the result:
   - `.py` → `arch -arm64 .venv/bin/python3 -m pytest <test file> -v`
   - `.js` / `.jsx` → `cd photo-search && npx vitest run <test file>`

4. **Run `make test`** to confirm the new file didn't break anything else.

5. **Report to Connor:**
   - Which file was tested and where the tests were written.
   - Pass / xfail / fail counts, from YOUR run.
   - **Any suspected bugs**, quoted from test-author's report, each with the
     xfail'd test that documents it. Lead with these — they are the most valuable
     output of the run.
   - Any oddities (doc comments contradicting behavior, drifted mirror files).
   - What was deliberately left uncovered, and why.
   - Anywhere test-author had to guess the intended behavior.

6. **Then STOP.** Print:

   > Tests written but NOT reviewed. Read `<test file>` before trusting it —
   > generated tests encode whatever behavior I inferred, which may not be what
   > you meant. Reply "keep" to leave them, or tell me what to change.

   Do not move on to another file, do not "clean up" the suite, and do not treat
   a green run as approval.

Never run `git commit` or `git push` at any point.
