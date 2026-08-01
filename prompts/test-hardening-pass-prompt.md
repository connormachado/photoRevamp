# Test hardening pass prompt

Do NOT commit or push. Do NOT weaken, delete, or loosen any existing test to make something pass — if code is actually unsafe, STOP and report it instead of adjusting the test.

Goal: close the three genuinely-open gaps from the test-suite review before this app is distributed for local use: (1) add a behavioral test that proves destructive operations never touch the real original, (2) prove the existing injection/traversal guards actually have teeth, (3) verify the server binds to localhost only. This is a focused hardening pass, not a coverage expansion.

--- PART 1: The never-touch-original guard (highest priority) ---
The review found that "never modify/delete the original" (design decision B) is currently safe only by construction — no test guards it. Since the reject-rework and purge features are next, this guard must exist first.

Step 0 — report: which code paths could touch a source file (export/render, reject/remove, any cleanup), and how the tests currently mock the filesystem. PAUSE.

Then add tests that, with the filesystem mocked/tmp-backed:
- Run an export/render of a queued video and assert the SOURCE file still exists and is byte-for-byte unchanged afterward.
- Run the reject/decision path and assert no source/original file is deleted or modified.
- Assert that the only files ever deleted are ones the app created itself (staged/working copies), never a path the app merely references.
These must be BEHAVIORAL (call the real function, assert on the filesystem result) — not a check that mirrors the implementation.

--- PART 2: Prove the guards have teeth ---
For each guarantee below, temporarily break the PRODUCT code, run the suite, confirm the specific test goes RED, then RESTORE the code exactly. Report a table: [guarantee | fault injected | did the right test go red? | code restored?].
- Path traversal on /full, /thumbnail, and the video_id routes (weaken safe_paths so an escape is allowed → the traversal tests must fail).
- AppleScript breakout (let one hostile value through unescaped → the quote-count invariant test must fail).
- The never-touch-original guard you just added in Part 1 (make a delete path touch the source → your new test must fail).
If ANY guarantee does not go red when broken, that test is decorative — flag it explicitly.

--- PART 3: Bind address (one-line check, high consequence) ---
Report the exact host the Flask app binds to (app.run host= / the start command / Makefile). If it is 0.0.0.0 or anything other than 127.0.0.1/localhost, FLAG it clearly and explain that on shared wifi other machines could reach the app — do NOT change it without my say-so, just report and recommend.

Verification: make test still green (minus any intentional, restored teeth breaks); the Part 1 guard tests exist and pass; the Part 2 table shows every guarantee went red when broken; Part 3 reports the actual bind host.

Save this prompt to prompts/test-hardening-pass-prompt.md.
