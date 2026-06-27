# CLAUDE.md — photo memory

> This file is auto-loaded by Claude Code at the start of every session. Keep it current. For the full feature list and long-term plans, see `ROADMAP.md`.

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

The backend is mid-migration into a `backend/` folder. **Target** layout:

```
photoApp/
├── CLAUDE.md
├── ROADMAP.md
├── backend/
│   ├── server.py        # routes ONLY — imports logic from the modules below
│   ├── search.py        # text search, image search, embed_text, embed_image
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
        ├── components/  # one component per feature
        └── hooks/
```

## Conventions

- **`server.py` stays thin.** It handles routing only. All logic lives in its own module. One route per feature.
- **One feature per backend file.** New cleanup/clustering logic gets its own module, not appended to an existing one.
- **One component per feature on the frontend.** Components go in `photo-search/src/components/`.
- **Everything works offline.** No external API calls beyond the one-time CLIP download.
- **Apple Silicon / MPS.** Handle the torch device explicitly; don't assume CUDA or CPU.
- **Stable IDs.** Photos are keyed by `file_id()` (MD5 of file path) so indexing stays incremental and resumable.

## Working with the user

- Comfortable with Python; newer to React — explain React concepts clearly when they come up.
- Analogies help when introducing something new.

## Status / next steps

See `ROADMAP.md` for the full list. Immediate order of work:
1. ✅ This file
2. Refactor `server.py` into thin routes + the `backend/` modules above (start with `utils.py`: `load_model`, `extract_metadata`, `file_id`).
3. First cleanup features: **Reveal in Finder** (`/reveal` endpoint + modal button) and **duplicate finder** (`duplicates.py`, cosine sim > 0.97, `DuplicateReview.jsx`).