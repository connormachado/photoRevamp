# Prompt — split header save icon into "save draft"

Do NOT commit or push.

I need to fix the function of two separate buttons because I made a mistake earlier.
I said I only wanted two button functions on the UI, meaning the green circle button
and the save icon button both perform the same function, but I lowkey want to make
the save icon button save the settings of the edits that we are performing on the
footage, so that next time I boot up the website, I can get back into editing the
same video. Diagnose what to change and then I'll approve and we'll edit it.

Resulting design: the green dome in the review room's left rail keeps exporting to
Photos (unchanged). The floppy-disk icon in the `ReviewStage` header instead saves
the current in-progress region edits as a resumable draft via a new
`POST /motion-review/draft` endpoint, stored separately from the decision/export
ledger (`drafts/<video_id>.json`, never touching `decisions.jsonl` or
`savings.json`). Reopening the app resumes the draft for any video that hasn't been
exported yet; a draft is cleared once its video is actually exported, but survives a
reject.

Verification:
- `python3 -c "import motion_review; import server"` (via the project `.venv`) and
  `npm run build` under `photo-search/` both clean.
- Still needed: a live browser pass — edit regions, click the header save icon,
  reload, confirm the same edits resume; export and confirm the draft file is
  cleared; reject and confirm the draft survives.

Save this prompt to prompts/header-save-draft-prompt.md.
