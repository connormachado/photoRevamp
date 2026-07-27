import { useRef, useEffect, useState } from "react";
import { indexForTime, ratesKey, segmentsKey } from "./segments";

/**
 * A <video> that only plays back a given list of {start, end, speed?} pieces.
 *
 * Both the "Removed" panel (plays only the cut segments) and the "Trimmed"
 * panel (plays only the keep segments) are the SAME source video driven by this
 * one controller — no re-encoding, and it live-updates if the segment list
 * changes (the seam Phase 2.5's draggable editor extends).
 *
 * Native controls are intentionally OFF: seeking between segments made the
 * browser flash its control bar on every skip. Instead it's click-to-play with
 * a minimal ▶ overlay, and it reports currentTime up so the timeline playhead
 * can show the skip.
 *
 * `seekTo` is a PLACED playhead — `{t, seq}`, bumped only when the user puts it
 * somewhere — so all three panels park on the same frame and you can start
 * playback from there on any of them. It is deliberately NOT the live playhead:
 * feeding playback position back down here made the two idle panels seek ~4x a
 * second, and a seek is the most expensive thing you can ask a video decoder to
 * do, so the panel that was actually playing stuttered. `onStop` closes that
 * loop instead — when a panel is paused it reports where it stopped, and the
 * parent syncs the others once.
 *
 * A piece's optional `speed` multiplies the panel's `rate`, so the Trimmed panel
 * previews speed regions at their real rate (regions.buildPlan supplies them)
 * while the Removed panel keeps its flat 4× timelapse.
 */
export default function SegmentVideo({ src, segments, rate = 1, onTime, seekTo = null, onStop }) {
  const ref = useRef(null);
  const idxRef = useRef(0); // which segment we're currently inside
  const [playing, setPlaying] = useState(false);
  // Set just before the automatic end-of-list pause, so that pause doesn't get
  // reported as "the user stopped here" and drag every panel to the last frame.
  const autoPauseRef = useRef(false);

  // The segment list is read through a ref inside effects so those effects can
  // depend on its CONTENT rather than the array's identity.
  const segsRef = useRef(segments);
  const appliedSeekRef = useRef(null);
  const key = segmentsKey(segments);
  const rateKey = ratesKey(segments);

  // Declared first so it lands before the effects below on every commit, leaving
  // them a current list to read. (Assigning during render is what a ref is not
  // for, and the lint rules reject it.)
  useEffect(() => { segsRef.current = segments; });

  // Reset progress whenever the source or the segment CONTENTS change.
  //
  // Keyed on `key`, never on the `segments` array itself: the parent rebuilds
  // that array on every render, and playback triggers parent renders (onTime →
  // setPlayhead). Depending on identity therefore reset playback several times a
  // second, which looked like the panel looping the same ~0.1s of footage.
  useEffect(() => {
    idxRef.current = 0;
    const v = ref.current;
    const segs = segsRef.current;
    if (v && segs.length) v.currentTime = segs[0].start;
  }, [src, key]);

  // Follow a PLACED playhead. Keyed on `seq`, not on the time: re-placing the
  // playhead where it already was is still a real request to come back here,
  // and playback has usually moved this panel away since. Skipped while THIS
  // panel is playing — dragging it backwards mid-play is never what you meant.
  useEffect(() => {
    if (!seekTo || appliedSeekRef.current === seekTo.seq) return;
    appliedSeekRef.current = seekTo.seq;
    const v = ref.current;
    const segs = segsRef.current;
    if (!v || !segs.length || !v.paused) return;
    const i = indexForTime(segs, seekTo.t);
    idxRef.current = i;
    // Park just INSIDE the piece. Landing exactly on `end` — which happens
    // whenever the playhead is past this panel's last piece — instantly trips
    // the end-of-list check in onTimeUpdate below and wraps playback back to
    // zero, which looked like the panel ignoring the scrub entirely.
    const seg = segs[i];
    const safeEnd = Math.max(seg.start, seg.end - 0.05);
    v.currentTime = Math.min(Math.max(seekTo.t, seg.start), safeEnd);
  }, [seekTo]);

  // The panel's own `rate` (the Removed panel's 4× timelapse) MULTIPLIED by this
  // piece's own speed, so a speed region previews at its real rate.
  //
  // The clamp is not cosmetic: browsers cap playbackRate at 16 (and stop playing
  // audio well before that), while a speed magnitude goes to 20. A 20× region
  // therefore previews at 16× but still EXPORTS at a true 20× — the preview is
  // the approximation here, not the render.
  function applyRate(i = idxRef.current) {
    const v = ref.current;
    const segs = segsRef.current;
    if (!v) return;
    const speed = (segs[i] && segs[i].speed) || 1;
    v.playbackRate = Math.min(16, Math.max(0.0625, rate * speed));
  }

  // Re-apply the rate when only the SPEEDS changed, leaving the playback
  // position alone — otherwise stepping a magnitude with −/+ would jump the
  // panel back to the start on every click.
  useEffect(() => { applyRate(); }, [rateKey, rate]); // eslint-disable-line react-hooks/exhaustive-deps

  function onLoadedMetadata() {
    const v = ref.current;
    if (v && segments.length) v.currentTime = segments[0].start;
    applyRate();
  }

  function onPlay() {
    const v = ref.current;
    if (!v || !segments.length) return;
    applyRate();
    const seg = segments[idxRef.current];
    if (!seg || v.currentTime < seg.start - 0.05 || v.currentTime >= seg.end) {
      idxRef.current = 0;
      v.currentTime = segments[0].start;
    }
  }

  function onTimeUpdate() {
    const v = ref.current;
    if (!v || !segments.length) return;
    // Report whether we're actually PLAYING: `timeupdate` also fires for the
    // programmatic seeks above (and the segment-change reset), and the timeline
    // must not treat those as "the playhead moved".
    if (onTime) onTime(v.currentTime, !v.paused);
    const seg = segments[idxRef.current];
    if (!seg) return;
    if (v.currentTime >= seg.end - 0.02) {
      if (idxRef.current < segments.length - 1) {
        idxRef.current += 1;
        v.currentTime = segments[idxRef.current].start; // skip the gap
        applyRate(idxRef.current);                      // this piece's own speed
      } else {
        autoPauseRef.current = true;
        v.pause();
        idxRef.current = 0;
        v.currentTime = segments[0].start;
      }
    }
  }

  // A pause the USER caused reports where this panel stopped, so the parent can
  // bring the other panels here — the one-shot replacement for the old
  // every-frame sync. The automatic rewind at the end of the list is not that.
  function onPauseEvent() {
    setPlaying(false);
    if (autoPauseRef.current) { autoPauseRef.current = false; return; }
    const v = ref.current;
    if (v && onStop) onStop(v.currentTime);
  }

  function toggle() {
    const v = ref.current;
    if (!v) return;
    // play() rejects if the element is torn down (or re-sourced) before it
    // starts — switching videos mid-play threw an uncaught AbortError.
    if (v.paused) { const p = v.play(); if (p) p.catch(() => {}); } else v.pause();
  }

  return (
    <div
      onClick={toggle}
      style={{ position: "relative", width: "100%", height: "100%", cursor: "pointer" }}
    >
      <video
        ref={ref}
        src={src}
        preload="metadata"
        onLoadedMetadata={onLoadedMetadata}
        onPlay={() => { onPlay(); setPlaying(true); }}
        onPause={onPauseEvent}
        onTimeUpdate={onTimeUpdate}
        style={{ width: "100%", height: "100%", objectFit: "contain", background: "#000", display: "block" }}
      />
      {!playing && (
        <div style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          pointerEvents: "none",
        }}>
          <div style={{
            width: 54,
            height: 54,
            borderRadius: "50%",
            background: "rgba(0,0,0,0.5)",
            border: "1px solid rgba(255,255,255,0.35)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#fff",
            fontSize: 20,
            paddingLeft: 4,
          }}>▶</div>
        </div>
      )}
    </div>
  );
}
