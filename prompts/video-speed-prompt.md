# Prompt — Climb Cutter "speed" boundary type

Do NOT commit or push. Pause before writing to the Photos library.

Step 0 — Inspect:
- The boundary-type registry + apply-on-export hook from Prompt 1.
- export_video.py (shipped) — how it invokes the imageio_ffmpeg binary and stamps metadata (there is NO system ffmpeg; reuse this).

Register a new "speed" boundary type with this EXACT UI spec. It must feel like the existing editable cut boundary — draggable on the timeline, same interaction model:
- Color: greenish (vs. the cut boundary's red).
- A number inside the boundary that I can type into — just the magnitude (e.g. "2", "2.5"), with NO leading +/- sign.
- A tappable RABBIT/TURTLE toggle icon that sets DIRECTION: rabbit = speed up, turtle = slow down. Tapping the icon swaps to the other (rabbit → turtle → rabbit). This icon replaces the old +/- sign — direction is now chosen by tapping the animal, not inferred from a sign.
- The number element's height = 75% of the boundary's height.
- A "-" step button and a "+" step button flanking the icon (one on each side); each click changes the MAGNITUDE by 0.5. (The step buttons only change the number; direction is the toggle's job.)
- Default when a speed boundary is added: rabbit (speed up), magnitude 2 (i.e. 2× faster).

SEMANTICS — confirm with me before finalizing (my proposed default): rabbit at magnitude N renders that region at N× speed (setpts=PTS/N); turtle at magnitude N renders it at 1/N speed (setpts=PTS*N), i.e. N times slower. Direction comes only from the toggle, so the step buttons never flip it — they just move the magnitude by 0.5. One thing to confirm: the magnitude floor when stepping down. My default is to clamp at 1.0 (magnitude 1 = normal speed / no-op in either direction), so to actually go slower you tap the turtle rather than driving the number below 1. Tell me if you'd rather allow sub-1 magnitudes instead.

Export hook (implement the "speed" type's apply-on-export from the framework):
- For a speed region, apply setpts to just that portion via the imageio_ffmpeg binary, then concatenate it with the other (cut, sped, or normal) segments. Reuse export_video.py's metadata / rotation / stream-mapping handling.
- Audio: default drop (-an) for speed-ups; if keeping audio, use atempo with chaining outside its safe range.
- Cut and speed must COMPOSE — a single timeline can carry both, and the export respects all of them.

Pause point: confirm the semantics above and test on ONE video before any real import.

Verification:
- Build + lint clean.
- Manual smoke: add a green speed boundary defaulting to rabbit + magnitude 2; type a value; step the magnitude with the +/- buttons; tap the icon to toggle rabbit↔turtle; drag it on the timeline. Export and confirm that region's speed changed in the output while other regions are untouched, the clip lands in Photos upright at the original timestamp, and the original is untouched.

Save this prompt to prompts/video-speed-prompt.md.

---

## Answers given during planning

1. **Semantics** — confirmed the proposed default. rabbit N → `setpts=PTS/N`
   (N× faster); turtle N → `setpts=PTS*N` (N× slower). Step buttons move the
   magnitude by 0.5 and never flip direction. Magnitude clamps to **[1.0, 20.0]**;
   1.0 is a no-op in either direction.
2. **Audio** — overrode the `-an` default: audio is **kept and time-stretched**
   via the `atempo` chain already implemented in `export_video._atempo_chain`
   (correct for both directions, chains outside atempo's 0.5–2.0 range). `-an`
   would have silenced the whole clip, not just the sped region, since ffmpeg
   cannot mute one region without inserting silence.
3. **Control placement** — the `[number] [−][🐇][+]` cluster floats over the
   region, centered and always visible, in an unclipped layer, so it may spill
   past the edges of a narrow region rather than being clipped away.
