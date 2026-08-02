Do NOT commit or push.
Goal: serve video with HTTP Range support (206 Partial Content) so the review-stage player can seek/scrub anywhere in a multi-GB file without downloading everything before that point.

Step 0 — Inspect and report:
- Which route serves the actual video bytes to the review-stage <video> player (e.g. /full or a motion-review video route) and how it returns the file today (send_file? a full read into memory?).
- Whether Range is already honored — does the response send `Accept-Ranges: bytes` and return 206 to a `Range:` request? (Werkzeug's send_file(conditional=True) / send_from_directory generally does this natively.)
- Confirm the traversal guard on that route (the shipped teeth-tested one) so Range work doesn't weaken it.
Report, propose a plan, and PAUSE.

Implementation:
1. Make the video route honor Range requests: return 206 with correct Content-Range + Accept-Ranges: bytes, streaming the requested byte window WITHOUT reading the whole file into memory. Prefer enabling Werkzeug's built-in conditional/range handling over hand-rolling it.
2. Confirm the frontend <video> uses it (native players do automatic range seeking once the server supports it — usually no frontend change needed; verify).
3. Do NOT weaken the path-traversal guard on this route.

Pause points: none irreversible.

Verification:
- Build + lint clean; the shipped traversal tests stay green.
- `curl -H "Range: bytes=1000000-1100000"` on the route returns 206 with Content-Range (not 200 + whole file).
- In the app, scrub to near the end of a large video — it seeks quickly instead of stalling on a full download.
