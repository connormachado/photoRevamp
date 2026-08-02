# Reject/Remove rework (Climb Cutter queue)

Do NOT commit or push.

Goal: give reject/remove teeth — a confirm dialog, then drop the video from the queue AND delete its working copy to reclaim disk, but ONLY when that copy is one we created (a temp upload). Never delete a real Photos original that a queue entry only references.

Step 0 — Inspect and report:
- How queue entries are stored today. Does each entry record its SOURCE — a disposable copy WE created (the Prompt 3 upload path, living in e.g. photo_db/motion_review/uploads/) vs. an EXTERNAL file we don't own (hand-fed path/uuid, and the future Prompt 5 finder-by-path)?
- What "reject" does now (per notes: ~nothing).
- Any review/decision history tied to a video (decisions.jsonl / reviews/) that removal should or shouldn't touch.
Report, propose a plan, and PAUSE.

Implementation:
1. Ensure every queue entry carries an ownership flag: owned=true when we created a disposable copy (upload route), owned=false when it references an external file. If not tracked yet, set it at ingest (upload → owned=true; external add → owned=false). Extend the existing enqueue — don't fork it.
2. Reject/Remove → a small confirm popup: "Remove this video from the queue? This deletes the working copy but never your original." with Cancel / Remove.
3. On confirm: drop the queue entry. If owned=true, delete the working-copy file to reclaim space. If owned=false, delete NOTHING on disk — just drop the entry. Never delete a file we didn't create.
4. Leave decisions.jsonl / audit history intact (removal is cleanup, not a history rewrite) unless Step 0 surfaces a reason otherwise — flag it if so.
5. If the reclaimed-bytes substrate already exists, credit the freed bytes; otherwise just free the file.

Pause points: before deleting ANY file, re-check the ownership flag. A non-owned path is never touched.

Verification:
- Build + lint clean.
- Upload a video → Reject → confirm dialog → entry gone AND its temp copy deleted from the uploads dir.
- Add an external path/uuid entry → Reject → entry gone, the file on disk UNTOUCHED.
- Cancel leaves everything as-is.

Save this prompt to prompts/reject-remove-rework-prompt.md.
