import { useRef, useEffect, useState } from "react";

/**
 * A <video> that only plays back a given list of {start, end} segments.
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
 */
export default function SegmentVideo({ src, segments, rate = 1, onTime }) {
  const ref = useRef(null);
  const idxRef = useRef(0); // which segment we're currently inside
  const [playing, setPlaying] = useState(false);

  // Reset progress whenever the source or the segment set changes.
  useEffect(() => {
    idxRef.current = 0;
    const v = ref.current;
    if (v && segments.length) v.currentTime = segments[0].start;
  }, [src, segments]);

  function applyRate() {
    if (ref.current) ref.current.playbackRate = rate;
  }

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
      } else {
        v.pause();
        idxRef.current = 0;
        v.currentTime = segments[0].start;
      }
    }
  }

  function toggle() {
    const v = ref.current;
    if (!v) return;
    if (v.paused) v.play(); else v.pause();
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
        onPause={() => setPlaying(false)}
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
