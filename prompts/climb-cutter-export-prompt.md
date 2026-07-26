# Climb Cutter — Export to Photos (Prompt 1)

Do NOT commit or push. Pause before writing to the Photos library.

Step 0 — Inspect and report before writing anything:
- backend/video_motion.py — how segments are represented, and whether any code already RENDERS a trimmed video file (vs. just logging cut segments). List the 14 functions and flag any existing ffmpeg/opencv render.
- The /motion-review backend routes and the approve flow — what happens today when I approve? (decisions.jsonl, reviews/, savings.json.)
- The motion-review frontend components (VideoQueue, ReviewStage, CutTimeline, VerdictButtons, etc.) — where an "Export to Photos" button would naturally live.
- How the source video is located (Apple UUID vs direct path) and where the original's creation date/EXIF is available (ChromaDB metadata? ffprobe on the source?).
- Confirm ffmpeg is installed and on PATH. If not, stop and tell me.

Then propose your plan and pause.

Implementation:

1. Create backend/export_video.py — a shared, reusable export helper (Prompt 2 will also use it). It should expose roughly:
   - render_segments(source_path, kept_segments) -> rendered_path
       Concatenate the approved/kept segments into one file using ffmpeg. Output to photo_db/motion_review/exports/. Re-encode to a Photos-friendly format (default: H.264 MP4, -crf 18; note HEVC/.mov as an option in a comment).
   - import_to_photos(video_path, original_date, gps=None) -> imported_item_ref
       Import the file into Apple Photos via AppleScript, set its date to match original_date so Photos files it next to the original, and preserve GPS if available.
   - reveal_in_photos(imported_item_ref)
       Activate Photos and spotlight/reveal the imported item.
   Wrap AppleScript calls the same way cleanup.py already does its reveal-by-uuid.

   IMPORTANT AppleScript caveat: setting an imported item's date is finicky. Try the media item `date` property first; if that vocabulary isn't settable, fall back to stamping the file's QuickTime `creation_time` via ffmpeg BEFORE import (Photos reads that on import). Verify whichever path you pick actually lands the clip at the right timestamp on one real test.

2. Wire a new backend route (e.g. POST /motion-review/export) that: locates the source by uuid/path, reads the original's date, calls render_segments -> import_to_photos -> reveal_in_photos, records the export in the existing decision/savings ledger (do NOT invent a new ledger), and returns success + the reveal result. It must NOT delete or modify the original.

3. Add an "Export to Photos" button to the motion-review UI as a DISTINCT action — do not silently change what the existing approve button does. On click it calls the route and shows a clear result ("Saved to Photos and revealed — original left untouched; delete it yourself once you've checked it").

Pause point: before the FIRST real import, confirm with me and test on exactly ONE video.

Verification:
- npm run build passes; npm run lint no worse than baseline (~12 known).
- backend/export_video.py imports cleanly.
- Manual smoke: run one export end-to-end; confirm (a) a new clip appears in Photos, (b) at the original's date, (c) Photos opens revealing it, (d) the original still exists.

Save this prompt to prompts/climb-cutter-export-prompt.md.

---

## Revisions (agreed during the build — these supersede the above)

1. **Approve IS save.** Item 3 above said to add Export as a DISTINCT action and
   not to change the approve button. Connor clarified that approve never meant
   anything other than "save this out," so there is **no third button**: the green
   dome renders, imports to Photos, reveals, and records the approval, all in one
   click. Reject is unchanged (bookkeeping only, still posts to `/decision`).
2. **Verdict button styling.** Red and green are now equal-sized domes (118px)
   sitting side by side, replacing the big-red-with-small-green-underneath stack.
   118px rather than 150px because a 150px pair overflows the 280px left rail.
3. **Second save affordance.** A canonical floppy-disk save button sits in the
   ReviewStage header, to the left of the video title, sized (52px) to span both
   the title line and the stats line beneath it. It fires the same export.
4. **The `video_id` hash is off the screen.** The MD5-of-path line under the title
   was internal plumbing. Replaced with `[X MB] : [placeholder]`, where the size
   comes from the existing `source_size_bytes` and the placeholder is statically
   set to *"your video is ~x times that of y"* to hold the spot for a future
   dynamically-populated comparison.
5. **Ledger.** `export_to_photos` writes through the existing `record_decision`
   plus one `{"action": "export"}` line in `decisions.jsonl` and export fields on
   `reviews/<id>.json`. `savings.json` is still credited through the normal
   approve path — it has always been a projection ("if you delete these originals
   you'd reclaim X"), not a record of bytes actually freed.

## Findings worth keeping

- **There is no system ffmpeg or ffprobe on PATH.** The project uses the
  pip-bundled `imageio_ffmpeg` binary (has libx264). Since ffprobe doesn't ship
  with it, metadata is scraped from `ffmpeg -i` stderr, as `video_motion.probe`
  already did.
- **Rotation needs no flag.** On re-encode ffmpeg autorotates: a 1920x1080 source
  with a -90° display matrix comes out a true 1080x1920 portrait file. An explicit
  `-metadata:s:v:0 rotate=` is a no-op in ffmpeg 7 and would be actively harmful
  if it ever started working.
- **iPhone .MOV has undecodable streams.** A 4-channel `apac` spatial-audio track
  and several `mebx` data tracks. Must `-map 0:v:0 -map 0:a:0?` explicitly;
  auto-mapping fails the encode outright.
- **Date + GPS survive the render**, verified by round-tripping a real clip:
  `creation_time` and `location` (ISO-6709) both land in the output.
