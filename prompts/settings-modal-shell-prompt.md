Do NOT commit or push.
Goal: a three-bar (hamburger) settings button that opens a modal with a tab framework, shipped with each future tab stubbed so Prompts 13–19 each drop into a registered slot.

Step 0 — Inspect and report:
- Where the app's top-level chrome/header lives (App.jsx) and where a settings button belongs.
- The existing EXIF modal (or any dialog/overlay pattern) — reuse its overlay/close/focus handling rather than inventing a new modal.
- What icon set the app uses (so the hamburger matches).
- Any existing settings/config UI at all (likely none).
Report, propose a plan, and PAUSE.

Implementation:
1. A hamburger (three-bar) icon button in the header, styled to match existing icons. Click opens the settings modal.
2. Modal: reuse the existing modal/overlay pattern (single-increment-path). Layout = a left tab list + a right content pane.
3. Register tabs from a simple config array [{id, label, component}] so every future tab is a one-line drop-in. Ship with each of these tabs present but STUBBED ("coming soon" placeholder): Storage, Theme, Photos Library, Export Defaults, Motion Detection, About, Shortcuts.
4. Close on Esc and on click-outside; basic focus handling; the page behind must not scroll while open.

Pause points: none irreversible.

Verification:
- Hamburger opens the modal; tabs switch; Esc and click-outside close it; page behind doesn't scroll.
- Build + lint clean.

Save this prompt to prompts/settings-modal-shell-prompt.md.
