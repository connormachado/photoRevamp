# Unified reclaimed-bytes prompt

Do NOT commit or push.

Step 0 — Inspect and report the EXACT current schema before changing anything:
- stats.json — every field. Specifically: is there already a reclaimed_bytes field, and does the Climb Cutter savings (savings.json / set_reclaimed_bytes / GET /motion-review/savings) already mirror into it? I need to avoid double-counting. (Note: savings.json is a PROJECTION — "if you deleted these originals you'd reclaim X" — not bytes actually freed. Decide whether the main-page number should include projected Climb Cutter savings or only real deletions, and flag your recommendation.)
- StatsContext.jsx — what it exposes to the UI and how it's consumed.
- BulkAddPad.jsx — does the bulk pad currently bump only the delete COUNT, or also bytes?
- The individual photo-delete path — does it already query the real file size (via apple_uuid + AppleScript) before deleting? (Per our notes, exact bytes are only cleanly available at delete time.)
Report the current state, then propose a minimal-change plan and pause.

Implementation (match the existing schema; do not duplicate persistence logic):

1. Define AVG_PHOTO_BYTES as a single named, tunable constant (default 3,670,016 = 3.5 MB). Comment it clearly so I can change it.

2. Reclaimed-bytes accounting, consolidated so the main page shows ONE total made of:
   - photo deletions, exact — when a delete has a known uuid, use the real file size (keep/extend existing logic).
   - photo deletions, estimated — bulk-pad deletions of N photos add N * AVG_PHOTO_BYTES.
   - Climb Cutter savings — fold in the existing savings ledger WITHOUT counting it twice (if it already mirrors into stats.json, read that; don't re-add). Respect the projected-vs-actual decision from Step 0.
   If helpful, store a small breakdown ({photos_exact, photos_estimated, climb_cutter}) so the total is auditable, but the headline is the sum.

3. Surface the total on the MAIN page via StatsContext (same source of truth the Climb Cutter page uses — no second store). Format human-readable (e.g. "4.2 GB reclaimed"). Add a subtle note/tooltip that bulk deletions are estimated.

Verification:
- Build + lint clean; stats.json stays valid JSON.
- Manual smoke: log a bulk deletion of N on the pad -> main-page total rises by ~N * 3.5 MB. Delete one known photo -> total rises by its real size. Confirm Climb Cutter savings appear in the same total and are NOT double-counted.

Save this prompt to prompts/unified-reclaimed-bytes-prompt.md.

---

## Decisions taken during planning

- **Climb Cutter savings ARE included** in the main-page headline. They were already in
  `reclaimed_bytes` and already shown in the review room, so excluding them would be a
  regression — and the photo side is equally unverified (`/reveal` only spotlights; nothing
  confirms a delete, and the bulk pad is self-reported). The whole number is a projection;
  the breakdown plus a hover tooltip keep that honest.
- **Exact bytes come from AppleScript** `size of media item id`, in its own osascript call
  (not folded into the spotlight script, which has a known intermittent failure and must
  stay byte-identical). Chroma's `size_kb` was rejected: it measures derivatives at
  ~60–90 KB versus 1.8–6.4 MB originals.
- **`−` subtracts the average**, draining `photos_estimated` first and spilling into
  `photos_exact` only once estimated hits 0. No per-photo ledger. Consequence: undoing an
  exact delete removes one average, not the real size.
