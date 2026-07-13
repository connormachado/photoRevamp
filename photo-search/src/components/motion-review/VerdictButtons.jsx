import { useState } from "react";

const API = "http://localhost:5001";

/**
 * The per-video verdict, styled as a big comic/silo REJECT button with a small
 * green approve circle beneath it. Lives at the bottom of the left rail.
 * Posts the decision (audit log + resumable state + savings pool) then calls
 * onDecided so the room can badge the queue and advance to the next video.
 */
export default function VerdictButtons({ videoId, currentVerdict, onDecided }) {
  const [saving, setSaving] = useState(null); // "reject" | "approve" | null
  const [pressed, setPressed] = useState(false);

  async function decide(verdict) {
    setSaving(verdict);
    try {
      const res = await fetch(`${API}/motion-review/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: videoId, verdict }),
      });
      const data = await res.json();
      if (res.ok) onDecided(data);
    } catch {
      /* leave UI as-is on failure */
    } finally {
      setSaving(null);
    }
  }

  const isReject = currentVerdict === "reject";
  const isApprove = currentVerdict === "approve";

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 18, padding: "22px 0 26px" }}>
      {/* BIG red silo button */}
      <button
        onClick={() => decide("reject")}
        onMouseDown={() => setPressed(true)}
        onMouseUp={() => setPressed(false)}
        onMouseLeave={() => setPressed(false)}
        disabled={saving !== null}
        title="Reject — throw away the proposed cuts"
        style={{
          width: 150,
          height: 150,
          borderRadius: "50%",
          border: "none",
          cursor: saving ? "default" : "pointer",
          color: "#fff",
          fontSize: 19,
          fontWeight: 900,
          letterSpacing: "0.06em",
          // domed, backlit "big red button" look
          background: "radial-gradient(circle at 38% 30%, #ff6b6b 0%, #ef4444 42%, #b91c1c 100%)",
          boxShadow: pressed
            ? "inset 0 6px 14px rgba(0,0,0,0.5), 0 0 0 10px rgba(239,68,68,0.10)"
            : "0 12px 26px rgba(239,68,68,0.45), inset 0 3px 6px rgba(255,255,255,0.35), inset 0 -8px 16px rgba(0,0,0,0.35), 0 0 0 10px rgba(239,68,68,0.12)",
          outline: isReject ? "3px solid #fecaca" : "none",
          outlineOffset: 4,
          transform: pressed ? "translateY(2px) scale(0.98)" : "none",
          transition: "all 0.08s",
          opacity: saving === "approve" ? 0.5 : 1,
        }}
      >
        {saving === "reject" ? "…" : "REJECT"}
      </button>

      {/* small green approve circle */}
      <button
        onClick={() => decide("approve")}
        disabled={saving !== null}
        title="Approve — accept the trim"
        style={{
          width: 62,
          height: 62,
          borderRadius: "50%",
          border: "none",
          cursor: saving ? "default" : "pointer",
          color: "#fff",
          fontSize: 22,
          fontWeight: 800,
          background: "radial-gradient(circle at 38% 30%, #4ade80 0%, #22c55e 45%, #15803d 100%)",
          boxShadow: "0 6px 14px rgba(34,197,94,0.4), inset 0 2px 4px rgba(255,255,255,0.4), inset 0 -5px 10px rgba(0,0,0,0.3)",
          outline: isApprove ? "3px solid #bbf7d0" : "none",
          outlineOffset: 3,
          opacity: saving === "reject" ? 0.5 : 1,
          transition: "all 0.08s",
        }}
      >
        {saving === "approve" ? "…" : "✓"}
      </button>

      {currentVerdict && (
        <span style={{ color: isApprove ? "#22c55e" : "#f87171", fontSize: 11, fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase" }}>
          {isApprove ? "approved" : "rejected"}
        </span>
      )}
    </div>
  );
}
