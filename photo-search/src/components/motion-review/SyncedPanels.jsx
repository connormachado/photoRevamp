import { useState } from "react";
import SegmentVideo from "./SegmentVideo";

const API = "http://localhost:5001";
const ACCENT = "#2dd4bf";

// A labeled, bordered frame shared by all three panels so they read as a set.
// Background is transparent so the teal stage shows through any letterbox.
function Panel({ label, accent, aspect, children }) {
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0 }}>
      <div style={{
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        color: accent,
        marginBottom: 8,
      }}>
        {label}
      </div>
      <div style={{
        background: "transparent",
        border: `1px solid ${accent}55`,
        borderRadius: 10,
        overflow: "hidden",
        aspectRatio: aspect,
        flex: "0 1 auto",
        minHeight: 0,
        margin: "0 auto",
        width: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}>
        {children}
      </div>
    </div>
  );
}

function Placeholder({ text }) {
  return <div style={{ color: "#5eead4aa", fontSize: 13, padding: 20, textAlign: "center" }}>{text}</div>;
}

const plainVideoStyle = { width: "100%", height: "100%", objectFit: "contain", background: "transparent", display: "block" };

/**
 * Three side-by-side panels, all fed by the SAME source video:
 *   Original — plays straight through (keeps native controls; it never skips)
 *   Removed  — plays only the cut segments (sped up, timelapse feel)
 *   Trimmed  — plays only the keep segments (skips the cuts)
 *
 * The panel aspect ratio is MEASURED from the loaded video (videoWidth/Height),
 * which the browser reports post-rotation — so portrait iPhone .MOV files (that
 * are stored landscape + a rotation flag) fill the frame instead of shrinking.
 * `onTime(t, playing)` reports the currently-playing time up so the timeline
 * playhead moves. The second argument matters: `timeupdate` also fires for
 * programmatic seeks (scrub previews, segment resets), and the timeline ignores
 * those — only real playback is allowed to move the playhead.
 *
 * `seekTo` is the PLACED playhead flowing the other way — `{t, seq}`, bumped
 * only when the playhead is put somewhere — so all three panels park on the same
 * frame and playback can start from there on any of them. The Original is seeked
 * by the parent through `videoRef`; the two segment panels take it as a prop and
 * map it onto their own segment list. `onStop` reports where a segment panel was
 * paused, which is when the set re-syncs; see SegmentVideo for why that isn't
 * done continuously.
 */
export default function SyncedPanels({ video, onTime, videoRef, cuts: cutsProp, keeps: keepsProp, seekTo = null, onStop }) {
  const src = video.source_exists ? `${API}/motion-review/source?id=${video.video_id}` : null;
  const cuts = cutsProp || video.cut_segments || [];
  const keeps = keepsProp || video.keep_segments || [];

  // Start from the probe as a hint, then refine to the real measured aspect.
  const probeAspect = video.width && video.height ? video.width / video.height : 16 / 9;
  const [aspect, setAspect] = useState(probeAspect);

  function onLoadedMetadata(e) {
    const v = e.target;
    if (v.videoWidth && v.videoHeight) setAspect(v.videoWidth / v.videoHeight);
  }

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 16, alignItems: "stretch" }}>
      <Panel label="Original" accent="#818cf8" aspect={aspect}>
        {src
          ? <video
              ref={videoRef}
              src={src}
              controls
              preload="metadata"
              onLoadedMetadata={onLoadedMetadata}
              onTimeUpdate={(e) => onTime && onTime(e.target.currentTime, !e.target.paused)}
              style={plainVideoStyle}
            />
          : <Placeholder text="Source video not available on disk" />}
      </Panel>

      <Panel label="Removed · timelapse" accent="#f87171" aspect={aspect}>
        {src && cuts.length
          ? <SegmentVideo src={src} segments={cuts} rate={4} onTime={onTime} seekTo={seekTo} onStop={onStop} />
          : <Placeholder text={src ? "No sections removed" : "Source unavailable"} />}
      </Panel>

      <Panel label="Trimmed result" accent={ACCENT} aspect={aspect}>
        {src && keeps.length
          ? <SegmentVideo src={src} segments={keeps} rate={1} onTime={onTime} seekTo={seekTo} onStop={onStop} />
          : <Placeholder text="Source unavailable" />}
      </Panel>
    </div>
  );
}
