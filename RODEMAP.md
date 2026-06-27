# 📸 photo memory — roadmap

A personal photo search + curation tool built on CLIP embeddings + ChromaDB.
The goal: make 50,000 photos actually useful, searchable, and worth keeping.

---

## current stack

| layer | tech |
|---|---|
| embeddings | CLIP ViT-B/32 via `open_clip` |
| vector DB | ChromaDB (local, no account needed) |
| backend | Flask + Python |
| frontend | React + Vite |

---

## repo structure (recommended)

Keep features modular so Claude Code can drop in new ones without touching unrelated code.

```
photoApp/
├── photo_db/               # ChromaDB data (gitignore this)
├── test_photos/            # Small test set
│
├── backend/
│   ├── embed_photos.py     # Indexing pipeline
│   ├── server.py           # Flask API (routes only, thin layer)
│   ├── search.py           # Search logic (text, image, similar)
│   ├── duplicates.py       # Duplicate detection logic
│   ├── clustering.py       # Timeline + face + event clustering
│   ├── cleanup.py          # Deletion list export, reveal in Finder
│   └── utils.py            # Shared helpers (EXIF, file ID, etc.)
│
├── photo-search/           # Vite/React frontend
│   └── src/
│       ├── App.jsx
│       ├── components/
│       │   ├── SearchBar.jsx
│       │   ├── PhotoGrid.jsx
│       │   ├── PhotoModal.jsx
│       │   ├── DuplicateReview.jsx   # side-by-side keep/delete UI
│       │   ├── TimelineView.jsx
│       │   └── MoodBoard.jsx
│       └── hooks/
│           ├── useSearch.js
│           └── useSelection.js       # bulk select state
│
└── ROADMAP.md
```

The key principle: **each backend feature is its own file**, and **each frontend view is its own component**. `server.py` stays thin — it just wires routes to the right module. That way Claude Code can work on `duplicates.py` without ever touching `search.py`.

---

## features

### ✅ shipped
- **Natural language search** — type "golden hour sunset" and find every matching photo across your entire library. CLIP translates text and images into the same coordinate space, so no tagging required.

---

### 🔜 next up — camera roll cleanup

#### duplicate & near-duplicate finder
Photos with a CLIP similarity score above ~0.97 are almost certainly the same shot — burst mode, accidental double-taps, the same photo sent over iMessage. Surface these in a side-by-side keep/delete UI so you can blow through them fast.

#### reveal in Finder
Every photo already has its full file path in the metadata. A "Show in Finder" button runs `open -R /path/to/photo.jpg` on macOS and highlights the exact file — so you can delete it yourself without the app needing write access to your library.

#### bulk select + export delete list
Select a batch of photos in the UI, export their paths to a `.txt` file. Review in Finder, then delete manually. Safer than in-app deletion — gives you one last look before anything goes away.

#### "find my worst photos"
Blurry shots, accidental black screens, photos of the floor. These cluster together in embedding space. Searchable via text ("blurry dark accidental") or by flagging statistical outliers — embeddings that land far from any meaningful cluster.

#### more like this
Click any photo → find every visually similar shot across your whole library. Already partially working via the image search endpoint; this turns it into a dedicated "more of these" flow with pagination.

---

### 🗓 summer features

#### face clustering
Group every photo by person without needing to name anyone upfront. Uses a face detection model on top of CLIP — first pass finds faces, second pass clusters them by identity. End result: a folder per person, built automatically.

#### timeline clustering + event detection
If there's a gap of 3+ hours between photos, that's probably a new event. Group your library into auto-detected events ("Italy trip", "Jake's birthday", "random Tuesday") based purely on EXIF timestamps. No manual albums needed.

#### mood & aesthetic board
"Show me all my low-light shots." "Find every photo with a blue/red/green palette." CLIP encodes mood and color naturally — these are just text queries with a slightly different framing, surfaced as a dedicated UI mode with preset filters.

#### timeline anomaly detection
Find photos that are weirdly out of place — a photo from 2019 sitting in a 2023 event cluster, a screenshot buried in vacation photos, a misfiled duplicate. Useful for finding junk that slipped through and for auditing your library structure.

---

### 🚀 big swings (later)

#### trip memory creation
Cluster photos by date range into trips. Score each photo for quality (sharpness, composition, faces). Pick the best 20–30 per trip. Generate a small auto-album that plays over music. 

Long-term: an agent that actually edits the trip — cuts, transitions, music sync — based on the narrative arc of the photos. Think: you go on a trip, come home, and an edit is waiting for you.

---

## workflow for adding a new feature

1. **Backend first** — add a new file in `backend/` with the core logic, add a route to `server.py`
2. **Test the endpoint** — curl it or use Postman to confirm the response shape
3. **Frontend component** — add a new component in `src/components/`, wire it to the endpoint
4. **Drop it into App.jsx** — add a nav entry or new view

Each feature is self-contained. Claude Code can take a feature from this doc, implement it end-to-end, and nothing else breaks.