# File-picker → queue (Climb Cutter upload)

Do NOT commit or push.

Step 0 — Inspect and report:
- How the Climb Cutter queue ingests a video today (VideoQueue component + its backend route): Apple UUID, file path, or both? What's the smallest change to also accept an uploaded file?
- Whether video_motion.py can run against an arbitrary file path (it's documented to accept a direct path) — confirm, since an upload lands as a temp file, not a Photos asset.
- Any existing Flask upload handling / max-content-length config.
Report, propose plan, pause.

Implementation (browser file picker → upload → temp file → queue):

1. Frontend: an "Add video" button in VideoQueue that triggers a hidden <input type="file" accept="video/*"> (allow multiple if easy). On change, POST the selected file(s) to the backend as multipart/form-data. Note: the browser will NOT give a real path — you must send the bytes.

2. Backend route (e.g. POST /motion-review/upload) that saves each uploaded file to a temp dir (e.g. photo_db/motion_review/uploads/) and enqueues it BY PATH (video_motion.py already accepts a direct file path). Return the queued count.

3. Metadata check: a file picked from the Photos section of the macOS panel is a copy — verify it still carries QuickTime creation_time + GPS, because the export flow reads those from the file. If macOS ever hands a stripped copy, flag it; for a NEW exported asset it's the source file's metadata we stamp, so confirm on ONE real pick.

4. Large uploads: climbing videos can be hundreds of MB (the export smoke test source was 176 MB). It's localhost so it's fast, but set Flask's MAX_CONTENT_LENGTH high enough that it doesn't reject the upload.

5. No apple_uuid comes back from this path — that's fine. The Climb Cutter export creates a NEW asset and reads date/GPS from the file itself; it never needed the original's uuid.

Verification:
- Build + lint clean.
- Manual smoke: click "Add video," pick a clip from the Photos section of the macOS file panel, confirm it uploads, lands in the queue, and opens in the review stage. Then run an export and confirm the result still lands in Photos upright at the right date.

Save this prompt to prompts/file-picker-queue-prompt.md.
