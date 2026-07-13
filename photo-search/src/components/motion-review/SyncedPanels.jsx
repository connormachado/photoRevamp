import { useState } from "react";
import SegmentVideo from "./SegmentVideo";

const API = "http://localhost:5001";
const ACCENT = "#2dd4bf";

// A labeled, bordered frame shared by all three panels so they read as a set.
// Background is transparent so the teal stage shows through any letterbox.
function Panel({ label, accent, aspect, children }) {
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
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
        maxHeight: "64vh",
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
 * `onTime` reports the currently-playing time up so the timeline playhead moves.
 */
export default function SyncedPanels({ video, onTime }) {
  const src = video.source_exists ? `${API}/motion-review/source?id=${video.video_id}` : null;
  const cuts = video.cut_segments || [];
  const keeps = video.keep_segments || [];

  // Start from the probe as a hint, then refine to the real measured aspect.
  const probeAspect = video.width && video.height ? video.width / video.height : 16 / 9;
  const [aspect, setAspect] = useState(probeAspect);

  function onLoadedMetadata(e) {
    const v = e.target;
    if (v.videoWidth && v.videoHeight) setAspect(v.videoWidth / v.videoHeight);
  }

  return (
    <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
      <Panel label="Original" accent="#818cf8" aspect={aspect}>
        {src
          ? <video
              src={src}
              controls
              preload="metadata"
              onLoadedMetadata={onLoadedMetadata}
              onTimeUpdate={(e) => onTime && onTime(e.target.currentTime)}
              style={plainVideoStyle}
            />
          : <Placeholder text="Source video not available on disk" />}
      </Panel>

      <Panel label="Removed · timelapse" accent="#f87171" aspect={aspect}>
        {src && cuts.length
          ? <SegmentVideo src={src} segments={cuts} rate={4} onTime={onTime} />
          : <Placeholder text={src ? "No sections removed" : "Source unavailable"} />}
      </Panel>

      <Panel label="Trimmed result" accent={ACCENT} aspect={aspect}>
        {src && keeps.length
          ? <SegmentVideo src={src} segments={keeps} rate={1} onTime={onTime} />
          : <Placeholder text="Source unavailable" />}
      </Panel>
    </div>
  );
}
