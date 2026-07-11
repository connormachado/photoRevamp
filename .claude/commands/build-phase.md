---
description: Run one phase: plan (Opus), implement (subagent), verify (subagent), pause for human check.
---
Given the phase description in $ARGUMENTS:
1. Enter plan mode: produce a concrete plan (files touched, functions changed, how
   this phase will be verified). Wait for my approval.
2. Once approved, dispatch the `implementer` subagent with the approved plan.
3. Once implementer reports done, dispatch the `verifier` subagent.
4. If verifier reports FAIL, feed the failure back to implementer and retry (max 3 attempts).
5. When verifier PASSes (or after 3 failed attempts), dispatch the
   `change-summarizer` subagent to write `CHANGES_PENDING_REVIEW.md` (a
   plain-language, file-by-file summary of the uncommitted changes).
6. Then STOP and print:
   "Phase ready for your check. Run `make start`, look at [specific thing],
   read CHANGES_PENDING_REVIEW.md for what changed, then reply 'continue' or
   describe what's wrong."
Never run git commit or git push at any point.
