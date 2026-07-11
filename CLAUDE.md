# CLAUDE.md — photo memory

> This file is auto-loaded by Claude Code at the start of every session. Keep it current. For the full feature list and long-term plans, see `RODEMAP.md`.

## Critical rules

- **Do not commit or push anything to GitHub** under any circumstances. Do not run `git commit`, `git push`, or any git command that writes to history. The user handles all commits manually.


## Build Workflow for New Features
- Never run `git commit` or `git push` under any circumstances, at any point in any phase.
- Multi-phase features are built one phase at a time via /build-phase.
- Each phase: plan → implement (subagent) → verify (subagent) → pause for human check.

## What this is

**photo memory** is a fully local photo search and curation tool. It embeds a camera roll (50k+ photos) with CLIP and stores the vectors in ChromaDB, then lets you search the whole library by natural language ("golden hour sunset", "birthday with friends") or by dropping in a photo to find visually similar ones. Everything runs on the user's machine — no cloud accounts, no API keys, no external calls except the one-time CLIP model download. The longer-term goal is camera roll cleanup: duplicates, blurry/junk detection, bulk delete lists, trip albums, and eventually a video edit agent.

## Stack

| layer | tech |
|---|---|
| embeddings | CLIP ViT-B/32 via `open_clip` |
| vector DB | ChromaDB (local persistent folder, `photo_db/`) |
| backend | Flask + Python, port 5001 |
| frontend | React + Vite, port 5173 |
| device | Apple Silicon Mac (torch device = `mps`) |

## Repo structure
```
photoApp/

├── CLAUDE.md

├── RODEMAP.md

├── stats.json           # delete counter persistence — GITIGNORED

├── backend/

│   ├── server.py        # routes ONLY — imports logic from the modules below

│   ├── search.py        # text search, image search, embed_text, embed_image

│   ├── stats.py         # delete counter read/write

│   ├── duplicates.py    # near-duplicate detection

│   ├── clustering.py    # timeline clustering, event + face clustering

│   ├── cleanup.py       # reveal in Finder, export delete list

│   ├── utils.py         # shared helpers: load_model, extract_metadata, file_id

│   └── embed_photos.py  # indexing pipeline (standalone, incremental, resumable)

├── photo_db/            # ChromaDB data — GITIGNORED

├── test_photos/         # 96-photo test set

└── photo-search/        # Vite/React frontend

└── src/

├── App.jsx

├── context/

│   └── StatsContext.jsx   # global delete counter state

├── components/            # one component per feature

└── hooks/
```

## Conventions

- **`server.py` stays thin.** It handles routing only. All logic lives in its own module. One route per feature.
- **One feature per backend file.** New cleanup/clustering logic gets its own module, not appended to an existing one.
- **One component per feature on the frontend.** Components go in `photo-search/src/components/`.
- **Everything works offline.** No external API calls beyond the one-time CLIP download.
- **Apple Silicon / MPS.** Handle the torch device explicitly; don't assume CUDA or CPU.
- **Stable IDs.** Photos are keyed by `file_id()` (MD5 of file path) so indexing stays incremental and resumable.
- **No git operations.** Do not commit, push, or otherwise write to git history.

## Non-obvious wiring

- **`GET /stats` is overloaded.** It returns one merged payload, `{total, deleted}`: `total` is the live `collection.count()` (header "X photos indexed"), `deleted` comes from `stats.py` reading `stats.json`. Don't repurpose `/stats` for just one of them — both the header and `DeleteCounter` read from it. The delete counter is bumped via `POST /stats/increment` with `{delta: ±1}`.
- **Chip queries are the single source of truth.** The six junk-cull chips live in the exported `CHIPS` array in `SearchChips.jsx`. Junk Hunt re-imports `CHIPS` and fires all of them in parallel — edit the list in one place. Each chip carries a display `label` (with emoji) separate from the `query` sent to CLIP; never send the emoji to CLIP.
- **"Show in Photos" auto-bumps the delete counter.** On a successful `/reveal`, `OpenInPhotosButton` calls `incrementDeleteCount()` (an optimistic "about to delete" proxy). If the counter drifts up unexpectedly, this is why. The shared `incrementDeleteCount`/`decrementDeleteCount` come from `StatsContext`, which wraps `App`'s returned tree.

## Working with the user

- Comfortable with Python; newer to React — explain React concepts clearly when they come up.
- Analogies help when introducing something new.

## Status / next steps

See `RODEMAP.md` for the full list. Current state:

**Completed:**
- ✅ Repo structure + CLAUDE.md / RODEMAP.md
- ✅ `server.py` refactored into thin routes + `backend/` modules (`utils.py`, `search.py`)
- ✅ HEIC → JPEG on-the-fly conversion in `/photo` endpoint
- ✅ "Open in Photos" button + `/open-in-photos` AppleScript endpoint
- ✅ Results count toggle (12 / 24 / 48 / All)
- ✅ Delete counter (`stats.py`, `StatsContext`, `DeleteCounter.jsx`) with local persistence
- ✅ Search prompt chips (`SearchChips.jsx`)
- ✅ Junk Hunt mode (`JunkHunt` button, parallel queries, deduped results)

**Immediate next:**
1. Duplicate finder (`duplicates.py`, cosine sim > 0.97, `DuplicateReview.jsx`)