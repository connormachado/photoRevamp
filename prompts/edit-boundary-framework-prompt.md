# Prompt 1 — Edit-Boundary Framework (Climb Cutter)

Do NOT commit or push.

Step 0 — Inspect and report before writing anything:
- CutTimeline.jsx (and ReviewStage / SegmentVideo / SyncedPanels / MotionReviewApp) — exactly how a cut boundary is represented in state, rendered, dragged, added, and removed today. What is the data shape? Where does the segment/boundary list live?
- backend: how the kept/cut segments become the exported video today (render_segments in export_video.py) — where in that pipeline a per-region transform would hook in.
Report the current model, then propose the generalized design and pause.

Design to implement (confirm with me first):
1. A boundary-TYPE registry — ONE declarative place where each edit-boundary type is defined by: id (e.g. "cut"), display color, icon, label, default value/params, how it renders on the timeline, and an "apply-on-export" hook describing what it does to the video during render. Adding a new type later = add one entry + its hook, nothing else.
2. Migrate the EXISTING cut boundary into the registry as the first registered type. Its behavior, color, and interactions must be identical to today — this step is a no-op for the user.
3. A UI affordance to choose WHICH type of boundary to add (a small type picker / toolbar) and to remove a boundary. With only "cut" registered so far, this looks ~like today plus a type selector that currently has one option.
4. The export pipeline iterates the timeline's boundaries/segments and calls each type's apply-on-export hook, so export is type-agnostic. Cut's hook = drop the segment (current behavior). Future types plug in without touching the pipeline.

Do NOT add the speed type here — that's Prompt 2. This prompt only lands the framework + migrates cut onto it.

Pause point: after Step 0, before refactoring, confirm the data-model change with me — this touches core review-room state.

Verification:
- Build + lint clean.
- Manual smoke: the cut workflow behaves EXACTLY as before — add/drag/remove cut boundaries, approve/export a video, confirm identical output. The type picker shows (with just "cut" for now).

Save this prompt to prompts/edit-boundary-framework-prompt.md.
