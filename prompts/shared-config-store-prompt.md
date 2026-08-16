Do NOT commit or push.
Goal: establish ONE shared, persisted config store for app settings, with the Photos library root as its first entry, and refactor the hardcoded library-root references to read from it. This exists so Prompts 16 and 17 have a store to extend instead of each inventing one. The UI is deliberately minimal: display the current root and validate it — NOT an editable path picker.

Step 0 — Inspect and report:
- Consult `docs/CODEBASE_MAP.md` FIRST for where the relevant code lives (routes, modules, state owners, the nav guide) instead of re-exploring — it's navigation; CLAUDE.md stays the authority on gotchas/contracts. If the map is MISSING, HALT and tell me to run `/cartographer` myself — NEVER run `/cartographer` yourself (it's a ~200k in-session burn). If the map is present but seems to contradict the code, trust the code and flag the drift so I can regenerate.
- Where the library root is referenced or hardcoded TODAY. Enumerate every site (photo indexing anchors at resources/derivatives/masters/ — locate that base path, and check whether video originals resolve from a different anchor). I want the full list before any refactor, because a missed site means two sources of truth.
- Whether a config store already exists in any form (settings.json / config.py / a module-level constants file / localStorage on the frontend). If one exists, EXTEND it — do not create a second.
- The existing JSON-ledger conventions (stats.json, savings.json, dismissed.json, kept_videos.json) and their atomic-write pattern, so this matches rather than inventing a new one.
Report the full list of hardcoded sites, propose the store's shape, and PAUSE.

Implementation:
1. Create the shared config store (propose photo_db/config.json unless an existing one is found) using the repo's atomic-write pattern, with an explicit schema-version field so 16 and 17 can extend it safely later. Design it as a general key/value settings store — this prompt's job is the FOUNDATION, so do not shape it narrowly around one path.
2. Seed it with library_root, defaulting to the currently-detected location so behaviour is unchanged on first run. A missing or empty config must fall back to today's detected value, never crash.
3. Refactor every hardcoded library-root site found in Step 0 to read from the store. This is the actually valuable half of the prompt — after it, there is one source of truth.
4. Photos Library settings tab UI, minimal by design: display the resolved root as READ-ONLY text, plus a "Validate" button that checks the path exists and looks like a Photos library (contains the expected originals/derivatives structure) and reports clearly either way. NO editable field, NO file picker, NO reindex warnings — those were cut deliberately. Add a one-line note in the tab saying the root can be changed by editing the config file directly, so the escape hatch is discoverable.
5. Do NOT auto-reindex anything, ever, from this prompt.

Pause points: before the refactor in step 3 — show me the list of sites you're changing. Nothing here deletes or moves a file.

Verification:
- Build + lint clean; config.json is valid JSON and survives a restart.
- With the config present, indexing and any library scan resolve through it — prove it by pointing the config at a deliberately wrong path in a scratch run and confirming the code follows the config rather than a hardcoded string. Restore it afterward.
- Validate reports success on the real library and failure on a bogus path.
- Deleting config.json entirely still starts cleanly, falling back to the detected default.

Tests (conditional — config resolution and fallback are real logic): run /write-tests on the config store — assert atomic write survives an interrupted write, that a missing file falls back to the detected default rather than raising, that an unknown extra key is preserved on write (so 16/17 can't clobber each other), and that schema-version is written. Skip UI tests.

Save this prompt to prompts/shared-config-store-prompt.md.
