import { useState, useEffect, useCallback } from "react";
import VideoQueue from "./VideoQueue";
import ReviewStage from "./ReviewStage";
import VerdictButtons from "./VerdictButtons";
import { formatBytes } from "./format";

const API = "http://localhost:5001";
const ACCENT = "#2dd4bf";

/**
 * The Climb Cutter "Motion Review" room — a full-screen takeover, decoupled from
 * the photo-search UI (own folder, own backend routes) so it could be split into
 * a standalone app later. Nothing here is destructive: it only records verdicts.
 */
export default function MotionReviewApp({ onExit }) {
  const [videos, setVideos] = useState([]);
  const [selectedVideoId, setSelectedVideoId] = useState(null);
  const [savedBytes, setSavedBytes] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadQueue = useCallback(async () => {
    try {
      const [qRes, sRes] = await Promise.all([
        fetch(`${API}/motion-review/queue`),
        fetch(`${API}/motion-review/savings`),
      ]);
      const q = await qRes.json();
      const s = await sRes.json();
      const vids = q.videos || [];
      setVideos(vids);
      setSavedBytes(s.total_bytes || 0);
      setSelectedVideoId((prev) => prev || (vids[0] && vids[0].video_id) || null);
    } catch {
      setError("Could not reach the backend at " + API);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadQueue(); }, [loadQueue]);

  const selected = videos.find((v) => v.video_id === selectedVideoId) || null;

  // After a verdict: badge the video, update the reclaimed pool, then jump to
  // the next unreviewed video.
  const handleDecided = useCallback((data) => {
    if (typeof data.savings_total_bytes === "number") setSavedBytes(data.savings_total_bytes);
    setVideos((prev) => {
      const next = prev.map((v) =>
        v.video_id === selectedVideoId ? { ...v, verdict: data.verdict } : v
      );
      const nextUnreviewed = next.find((v) => v.verdict == null);
      if (nextUnreviewed) setSelectedVideoId(nextUnreviewed.video_id);
      return next;
    });
  }, [selectedVideoId]);

  return (
    <div style={{ position: "fixed", inset: 0, background: "#0a0a0a", display: "flex", flexDirection: "column", zIndex: 100 }}>
      {/* Distinct top bar — signals "different room" */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "14px 24px",
        borderBottom: `1px solid ${ACCENT}22`,
        background: "linear-gradient(90deg, rgba(45,212,191,0.06), transparent)",
      }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <span style={{ fontSize: 18, fontWeight: 800, color: ACCENT, letterSpacing: "-0.3px" }}>
            🎬 Climb Cutter
          </span>
          <span style={{ fontSize: 13, color: "#666" }}>Motion Review</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          {/* Global reclaimed-data pool */}
          <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
            <span style={{ fontSize: 20, fontWeight: 800, color: "#22c55e", fontFamily: "monospace" }}>
              {formatBytes(savedBytes)}
            </span>
            <span style={{ fontSize: 12, color: "#4b7a5c" }}>reclaimed</span>
          </div>
          <button
            onClick={onExit}
            style={{
              padding: "7px 14px",
              borderRadius: 7,
              border: "1px solid #2a2a2a",
              background: "transparent",
              color: "#888",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            ✕ Exit to search
          </button>
        </div>
      </div>

      {/* Body: left rail (queue + verdict) + review stage */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {loading ? (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#555" }}>
            Loading queue…
          </div>
        ) : error ? (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#f87171" }}>
            {error}
          </div>
        ) : videos.length === 0 ? (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#555", textAlign: "center" }}>
            No processed videos yet. Run <code style={{ color: "#888", margin: "0 6px" }}>video_motion.py --video …</code> first.
          </div>
        ) : (
          <>
            {/* Left rail: queue on top, big red/green verdict beneath */}
            <div style={{
              width: 280,
              flexShrink: 0,
              borderRight: `1px solid ${ACCENT}22`,
              display: "flex",
              flexDirection: "column",
              background: "#0a2e29",
            }}>
              <VideoQueue videos={videos} selectedVideoId={selectedVideoId} onSelect={setSelectedVideoId} />
              {selected && (
                <div style={{ borderTop: `1px solid ${ACCENT}22`, background: "#082521" }}>
                  <VerdictButtons
                    key={selectedVideoId}
                    videoId={selectedVideoId}
                    currentVerdict={selected.verdict}
                    onDecided={handleDecided}
                  />
                </div>
              )}
            </div>
            <ReviewStage key={selectedVideoId} video={selected} />
          </>
        )}
      </div>
    </div>
  );
}
