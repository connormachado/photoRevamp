# Prompt — persistent per-filter photo dismissal

Do NOT commit or push.

The six junk-cull chips are the main culling surface, but they're stateless: a
photo CLIP thinks is "blurry" that I've decided to keep comes back at the top
of the blurry tick every single time I open it. There's no way to say "not
this one, not here" short of deleting the photo.

Goal: a per-category hide list that survives restart, never touches the photo
itself, and backfills the grid so a dismissed photo's slot is taken by a fresh
result. Per-category by design — hiding a photo from "blurry" must leave it
visible under "dark".

## Resulting design

- Each chip in `SearchChips.jsx` gets a stable `id` (the ledger key); `query`
  stays the CLIP string and is free to reword.
- `backend/dismissed.py` is a new atomic-write JSON ledger at
  `photo_db/dismissed.json`, shape `{category: [file_id, ...]}`, with an
  in-memory cache so the search hot path does zero disk reads.
- `search.search_text` grows an optional `exclude_ids` set: over-fetches by
  exactly `len(exclude_ids)` (capped), filters, then trims back to `n` — the
  minimum over-fetch that still guarantees a full page.
- `POST /search/text` accepts an optional `category`; when present and valid
  it's filtered through the matching dismissed-ids ledger. A typed search
  (no category) is never filtered.
- Three new routes: `POST /filters/dismiss`, `POST /filters/restore`,
  `GET /filters/dismissed`.
- Frontend: a `HideFromFilterButton` (eye-slash) appears on hover over a tile,
  only when a category is known (chip view or Junk Hunt). Clicking it removes
  the photo from the grid, persists the dismissal, and shows an undo toast.
  Junk Hunt tracks which chip(s) surfaced each photo so a Junk Hunt dismiss
  hides it from every contributing category, not just one.
- Dismissing never touches the photo itself — no `/reveal`, no delete-counter
  bump, no filesystem write outside `dismissed.json`. Purely a display filter.

## Verification

- `make test` green, including new `tests/test_dismissed.py` (atomic write,
  per-category isolation, corrupt/missing ledger, invalid category rejected,
  over-fetch math) and an updated `SearchChips.test.jsx`.
- `npm run lint` (no new errors beyond the pre-existing ~12) and
  `npm run build` clean.
- Manual smoke: dismiss a photo from a chip, confirm it's gone and the grid
  backfills; restart the server and confirm it's still gone; confirm the same
  photo still shows under a different chip; undo from the toast and confirm
  it returns; dismiss from Junk Hunt and confirm it leaves every contributing
  tick; confirm the photo still exists in Photos.app and the delete counter
  doesn't move.

## Not doing

- No commits or pushes at any point.
- No manage-dismissed panel — undo toast only (`GET /filters/dismissed`
  covers debugging).
- No filtering of typed or image searches; dismissal is scoped to the ticks.
