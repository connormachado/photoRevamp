# Editable video title → safe export filename

Do NOT commit or push.
Goal: let me click the current video title in the editor and rename it inline; the title I set becomes the exported file's name, so sanitize it to be filesystem-safe (and safe to pass into ffmpeg/AppleScript) without silently mangling it.

## Step 0 — Inspect and report

- Consult `docs/CODEBASE_MAP.md` FIRST for where the relevant code lives (routes, modules, state owners, the nav guide) instead of re-exploring — it's navigation; CLAUDE.md stays the authority on gotchas/contracts. If the map is MISSING, HALT and tell me to run `/cartographer` myself — NEVER run `/cartographer` yourself (it's a ~200k in-session burn). If the map is present but seems to contradict the code, trust the code and flag the drift so I can regenerate.
- Where the title is shown today and what it defaults to (source filename? uuid?).
- How export_video.py names the exported file / how the imported Photos asset gets its name — i.e. where the title needs to flow to.
- Where to store the title on the queue entry so it persists through to export.

Report, propose a plan, and PAUSE.

## Implementation

1. Make the title inline-editable: click to edit, Enter/blur commits, Esc cancels.
2. Sanitize on commit to filesystem-safe: whitelist letters/digits/space/dash/underscore/interior-period; strip or replace anything else; forbid path separators (/ \), colon, control chars, leading dots, and reserved-ish names; collapse whitespace; cap length (~120 chars). Show the sanitized result so I see what actually stuck.
3. Store the sanitized title on the queue entry so it flows to export. On export, use it as the output filename (and the Photos asset name if that's how naming works). Handle collisions (append a short counter/timestamp) so two same-titled exports don't clobber.
4. Never let the raw title reach an ffmpeg arg or AppleScript string unescaped — pass via safe arg lists / proper quoting (a quote or space breaking out is a real risk).

Pause points: nothing irreversible, but confirm the sanitizer rules before wiring the title into the export filename.

## Verification

- Build + lint clean.
- Rename a video with a messy string (e.g. my/climb: "best"? ) → the field shows a clean safe name → export produces a file with that name, lands in Photos, original untouched.
- A hostile title cannot break the ffmpeg/AppleScript call.
