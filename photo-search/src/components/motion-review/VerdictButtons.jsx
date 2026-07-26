import { useState } from "react";
import SaveIcon from "./SaveIcon";

const API = "http://localhost:5001";

/**
 * The per-video decision pair, at the bottom of the left rail: a red REJECT dome
 * and an equal-sized green SAVE dome, side by side.
 *
 * The two are NOT symmetric in what they do. Reject is bookkeeping — it posts to
 * /motion-review/decision and records that the proposed cuts were thrown away.
 * Save is the real action: it renders the kept footage, imports it into Photos
 * at the original's date, and reveals it there. Saving *is* approving, so there
 * is no separate approve button.
 *
 * The render/import round-trip takes several seconds, so the green dome owns a
 * visible working state; the actual fetch lives in MotionReviewApp because the
 * header save icon fires the same export.
 */
export default function VerdictButtons({
  videoId,
  currentVerdict,
  exportedAt,
  onDecided,
  onExport,
  exporting,
  exportResult,
}) {
  const [rejecting, setRejecting] = useState(false);
  const [pressed, setPressed] = useState(null); // "reject" | "save" | null

  async function reject() {
    setRejecting(true);
    try {
      const res = await fetch(`${API}/motion-review/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: videoId, verdict: "reject" }),
      });
      const data = await res.json();
      if (res.ok) onDecided(data);
    } catch {
      /* leave UI as-is on failure */
    } finally {
      setRejecting(false);
    }
  }

  const busy = rejecting || exporting;
  const isReject = currentVerdict === "reject";
  const isSaved = Boolean(exportedAt);

  // Both domes are the same size. At 118px a pair plus the gap fits the 280px
  // rail with room for its padding.
  const DOME = 118;

  const domeShadow = (rgb, isPressed) =>
    isPressed
      ? `inset 0 6px 14px rgba(0,0,0,0.5), 0 0 0 8px rgba(${rgb},0.10)`
      : `0 10px 22px rgba(${rgb},0.45), inset 0 3px 6px rgba(255,255,255,0.35), ` +
        `inset 0 -8px 16px rgba(0,0,0,0.35), 0 0 0 8px rgba(${rgb},0.12)`;

  return (
    <div style={{ padding: "20px 12px 22px" }}>
      <div style={{ display: "flex", justifyContent: "center", gap: 16 }}>
        {/* REJECT — discard the proposed cuts */}
        <button
          onClick={reject}
          onMouseDown={() => setPressed("reject")}
          onMouseUp={() => setPressed(null)}
          onMouseLeave={() => setPressed(null)}
          disabled={busy}
          title="Reject — throw away the proposed cuts"
          style={{
            width: DOME,
            height: DOME,
            borderRadius: "50%",
            border: "none",
            cursor: busy ? "default" : "pointer",
            color: "#fff",
            fontSize: 15,
            fontWeight: 900,
            letterSpacing: "0.06em",
            background: "radial-gradient(circle at 38% 30%, #ff6b6b 0%, #ef4444 42%, #b91c1c 100%)",
            boxShadow: domeShadow("239,68,68", pressed === "reject"),
            outline: isReject ? "3px solid #fecaca" : "none",
            outlineOffset: 4,
            transform: pressed === "reject" ? "translateY(2px) scale(0.98)" : "none",
            transition: "all 0.08s",
            opacity: exporting ? 0.5 : 1,
          }}
        >
          {rejecting ? "…" : "REJECT"}
        </button>

        {/* SAVE — render + import into Photos + reveal */}
        <button
          onClick={onExport}
          onMouseDown={() => setPressed("save")}
          onMouseUp={() => setPressed(null)}
          onMouseLeave={() => setPressed(null)}
          disabled={busy}
          title="Save — export the trimmed clip into Photos at the original's date"
          style={{
            width: DOME,
            height: DOME,
            borderRadius: "50%",
            border: "none",
            cursor: busy ? "default" : "pointer",
            color: "#fff",
            fontSize: 15,
            fontWeight: 900,
            letterSpacing: "0.06em",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 5,
            background: "radial-gradient(circle at 38% 30%, #4ade80 0%, #22c55e 45%, #15803d 100%)",
            boxShadow: domeShadow("34,197,94", pressed === "save"),
            outline: isSaved ? "3px solid #bbf7d0" : "none",
            outlineOffset: 4,
            transform: pressed === "save" ? "translateY(2px) scale(0.98)" : "none",
            transition: "all 0.08s",
            opacity: rejecting ? 0.5 : 1,
          }}
        >
          {exporting ? (
            <span style={{ fontSize: 13, fontWeight: 700 }}>saving…</span>
          ) : (
            <>
              <SaveIcon size={26} color="#fff" />
              <span>SAVE</span>
            </>
          )}
        </button>
      </div>

      {/* Status line: export outcome wins, otherwise the recorded verdict. */}
      <div style={{ marginTop: 14, minHeight: 30, textAlign: "center", padding: "0 4px" }}>
        {exporting ? (
          <span style={{ color: "#5eead4", fontSize: 11, lineHeight: 1.5 }}>
            rendering &amp; importing — this takes a few seconds…
          </span>
        ) : exportResult ? (
          <span style={{
            color: exportResult.ok ? "#22c55e" : "#f87171",
            fontSize: 11,
            fontWeight: 600,
            lineHeight: 1.5,
          }}>
            {exportResult.message}
          </span>
        ) : isReject ? (
          <span style={{ color: "#f87171", fontSize: 11, fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase" }}>
            rejected
          </span>
        ) : isSaved ? (
          <span style={{ color: "#22c55e", fontSize: 11, fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase" }}>
            saved to photos
          </span>
        ) : null}
      </div>
    </div>
  );
}
