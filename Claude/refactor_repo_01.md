We're refactoring this repo. The goal is two things: (1) reorganize Python files
into a backend/ folder, and (2) split the monolithic server.py into thin routes +
per-feature logic modules. Read CLAUDE.md first for project context and conventions.

Before you change anything:
- Run `git status` and make sure the tree is clean. If it isn't, stop and tell me.
- Make a commit of the current state so we can roll back ("checkpoint before refactor").

Target structure (only create the files that have real content now — do NOT create
empty stubs for duplicates.py, clustering.py, or cleanup.py yet; those come later):

  photoApp/
  ├── CLAUDE.md
  ├── ROADMAP.md
  ├── backend/
  │   ├── server.py        # routes ONLY
  │   ├── search.py        # text search, image search, embed_text, embed_image
  │   ├── utils.py         # load_model, extract_metadata, file_id
  │   └── embed_photos.py  # moved as-is
  ├── photo_db/            # do NOT move, do NOT touch
  ├── test_photos/         # do NOT move
  └── photo-search/        # frontend, leave alone

Do it in this order, and pause after each step so I can confirm:

STEP 1 — utils.py
Move load_model(), extract_metadata(), and file_id() out of server.py and into
backend/utils.py. Everything else depends on these, so do them first. Keep the exact
same logic — file_id() must stay MD5-of-file-path so existing ChromaDB IDs still match,
and load_model() must keep using the mps torch device.

STEP 2 — search.py
Move the text-search and image-search logic (and embed_text / embed_image) into
backend/search.py. Import what it needs from utils.

STEP 3 — thin server.py
Move server.py into backend/ and rewrite it so it ONLY defines routes and calls into
search.py / utils.py. Same endpoints, same paths, same request/response shapes as before:
/search/text, /search/image, /thumbnail, /full, /stats. Behavior must be identical.

STEP 4 — move embed_photos.py into backend/ unchanged.

CRITICAL — watch the paths:
server.py and embed_photos.py probably reference photo_db/ and test_photos/ with paths
relative to the repo root. Moving them into backend/ will break those relative paths.
Fix this by anchoring those paths to the repo root (e.g. resolve from the file's location
up one level) rather than the current working directory, so the app works no matter where
it's launched from.

When done:
- Make sure photo_db/ is still gitignored.
- Start the server (note the new launch command, e.g. `python backend/server.py`) and hit
  /stats to confirm it still reads the existing index without re-embedding.
- Give me the new run commands for both backend and frontend.
- Do NOT re-index or modify anything in photo_db/.