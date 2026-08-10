Do NOT commit or push.
Goal: show how much local disk my currently-stored working copies use, offer a confirmed "purge all working copies" bulk-clean, and surface the running reclaimed-bytes total if that substrate exists — never touching real originals.

Step 0 — Inspect and report:
- Consult `docs/CODEBASE_MAP.md` FIRST for where the relevant code lives (routes, modules, state owners, the nav guide) instead of re-exploring — it's navigation; CLAUDE.md stays the authority on gotchas/contracts. If the map is MISSING, HALT and tell me to run `/cartographer` myself — NEVER run `/cartographer` yourself (it's a ~200k in-session burn). If the map is present but seems to contradict the code, trust the code and flag the drift so I can regenerate.
- Where working copies live (the Prompt 3 uploads dir, e.g. photo_db/motion_review/uploads/) and how to sum their size.
- Whether queue entries carry the Prompt 10 "owned" flag yet (purge must only target owned copies).
- Whether the reclaimed-bytes substrate (stats.json / savings.json / set_reclaimed_bytes) exists (Prompt 1 may not be built — treat the reclaimed total as "show if available").
Report, propose a plan, and PAUSE.

Implementation:
1. Backend: an endpoint returning current working-copy usage (total bytes + count, optional per-video breakdown).
2. Storage tab UI: "Working copies stored: X.X GB (N videos)"; and "Reclaimed so far: Y.Y GB" only if the substrate exists.
3. "Purge all working copies" button behind a confirm ("This removes those videos from the queue and deletes their stored copies. Your originals and your reclaimed-space total are never touched."). On confirm, reuse Prompt 28's keep-savings remove (delete every OWNED working copy + drop those entries, but PRESERVE the savings ledger — purging to free space must never wipe earned savings). Never delete a non-owned/original file.

Pause points: confirm before purge; originals are never deleted.

Verification:
- Build + lint clean.
- The usage number matches the actual on-disk size of the uploads dir.
- Purge (after confirm) frees that space and clears the owned entries; any external/referenced files are untouched.

Save this prompt to prompts/settings-storage-tab-prompt.md.
