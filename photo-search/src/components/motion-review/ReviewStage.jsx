import { useState, useRef, useEffect, useCallback } from "react";
import SyncedPanels from "./SyncedPanels";
import CutTimeline from "./CutTimeline";
import { fmtDur, formatBytes } from "./format";
import { complementSegments, sumDurations, segmentsEqual } from "./segments";

const ACCENT = "#2dd4bf";

// Before → after + how much was removed (green = savings vibe).
function DurationSummary({ original, trimmed, savedBytes }) {
  const removedPct = original > 0 ? Math.round((1 - trimmed / original) * 100) : 0;
  const cell = (label, value, color) => (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: 11, color: "#666", textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color, fontFamily: "monospace" }}>{value}</div>
    </div>
  );
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 24 }}>
      {cell("Original", fmtDur(original), "#e5e5e5")}
      <div style={{ fontSize: 24, color: "#444" }}>→</div>
      {cell("Trimmed", fmtDur(trimmed), ACCENT)}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 4, marginLeft: 8 }}>
        <div style={{
          padding: "4px 12px",
          borderRadius: 20,
          background: "rgba(34,197,94,0.12)",
          border: "1px solid rgba(34,197,94,0.4)",
          color: "#22c55e",
          fontSize: 14,
          fontWeight: 700,
        }}>
          −{removedPct}% shorter
        </div>
        {savedBytes > 0 && (
          <span style={{ color: "#22c55e", fontSize: 12, fontWeight: 600 }}>
            ≈ {formatBytes(savedBytes)} saved for this cut
          </span>
        )}
      </div>
    </div>
  );
}

export default function ReviewStage({ video, cuts, onCutsChange }) {
  const [playhead, setPlayhead] = useState(0);
  const originalRef = useRef(null);      // the Original panel's <video>, for seeking
  const playheadRef = useRef(0);         // mirror of playhead for the keydown closure

  const duration = video ? video.original_duration || 0 : 0;
  const fps = video && video.fps ? video.fps : 30;

  // Live-derived from the (editable) cut list, so drags update instantly.
  const keeps = complementSegments(cuts || [], duration);
  const trimmedDuration = Math.max(0, duration - sumDurations(cuts || []));
  const savedBytes = duration > 0 && video && video.source_size_bytes
    ? Math.round(video.source_size_bytes * (1 - trimmedDuration / duration))
    : 0;
  const isEdited = video ? !segmentsEqual(cuts, video.proposed_cut_segments || video.cut_segments) : false;

  // Playback updates the playhead; scrub/step both update it AND move the video.
  const setPlay = useCallback((t) => {
    playheadRef.current = t;
    setPlayhead(t);
  }, []);

  // Seek the Original panel to time t and park the playhead there (renders that
  // exact frame because the video is paused first).
  const seek = useCallback((t) => {
    if (!duration) return;
    const clamped = Math.max(0, Math.min(duration, t));
    const v = originalRef.current;
    if (v) {
      v.pause();
      v.currentTime = clamped;
    }
    setPlay(clamped);
  }, [duration, setPlay]);

  // Arrow keys step frames: ←/→ = ∓1 frame, Shift+←/→ = ∓10 frames.
  useEffect(() => {
    if (!video || !video.source_exists) return;
    function onKey(e) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault(); // don't scroll the page
      const frames = e.shiftKey ? 10 : 1;
      const step = frames / fps;
      seek(playheadRef.current + (e.key === "ArrowRight" ? step : -step));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [video, fps, seek]);

  if (!video) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#555", background: "#0d3d37" }}>
        Select a video from the queue to review.
      </div>
    );
  }

  return (
    <div style={{ flex: 1, padding: "24px 32px", overflowY: "auto", background: "#0d3d37" }}>
      {/* Header: filename + before/after + savings */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20, gap: 16, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 600, color: "#e5e5e5" }}>{video.source_name}</div>
          <div style={{ fontSize: 12, color: "#666", fontFamily: "monospace" }}>{video.video_id}</div>
        </div>
        <DurationSummary
          original={duration}
          trimmed={trimmedDuration}
          savedBytes={savedBytes}
        />
      </div>

      {/* Three synced panels — the Original doubles as the scrub viewer */}
      <SyncedPanels video={video} onTime={setPlay} videoRef={originalRef} cuts={cuts} keeps={keeps} />

      {/* Scrub + edit timeline + frame readout */}
      <div style={{ marginTop: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
          <span style={{ fontSize: 11, color: "#5eead4aa", letterSpacing: "0.05em" }}>
            hover to scrub · ←/→ step 1 frame · shift+←/→ step 10 · drag red edges to trim
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {isEdited && (
              <button
                onClick={() => onCutsChange(video.proposed_cut_segments || [])}
                style={{
                  padding: "3px 10px",
                  borderRadius: 6,
                  border: "1px solid #f59e0b66",
                  background: "rgba(245,158,11,0.1)",
                  color: "#f59e0b",
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                ↺ reset to proposed
              </button>
            )}
            <span style={{ fontSize: 12, color: "#e5e5e5", fontFamily: "monospace" }}>
              {fmtDur(playhead)} · frame {Math.round(playhead * fps)}
            </span>
          </div>
        </div>
        <CutTimeline
          duration={duration}
          cutSegments={cuts}
          fps={fps}
          playhead={playhead}
          onSeek={seek}
          onCutsChange={onCutsChange}
        />
      </div>
    </div>
  );
}
