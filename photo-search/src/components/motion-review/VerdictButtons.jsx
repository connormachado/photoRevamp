import { useState, useRef, useEffect } from "react";
import SaveIcon from "./SaveIcon";
import { formatBytes } from "./format";

/**
 * The per-video decision pair, at the bottom of the left rail: a red REJECT dome
 * and an equal-sized green SAVE dome, side by side.
 *
 * The two are NOT symmetric in what they do. Reject now has teeth: it records
 * the verdict AND drops the row, freeing the working copy when that copy is one
 * the app made (see queue_removal._owned_source — a referenced Photos original
 * is never touched). Because it destroys something, it is the only action here
 * behind a confirm step. Save is the real other action: it renders the kept
 * footage, imports it into Photos at the original's date, and reveals it there.
 * Saving *is* approving, so there is no separate approve button.
 *
 * Both round-trips live in MotionReviewApp — the header save icon fires the same
 * export, and a removal has to do list surgery this component can't see.
 */
export default function VerdictButtons({
  videoId,
  currentVerdict,
  exportedAt,
  owned,
  sourceSizeBytes,
  onRejectAndRemove,
  onExport,
  exporting,
  exportResult,
}) {
  const [pressed, setPressed] = useState(null); // "reject" | "save" | null
  const [confirming, setConfirming] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const wrapRef = useRef(null);

  // Close the confirm on outside-click and on Escape, like BulkAddPad.
  useEffect(() => {
    if (!confirming) return;
    function onDown(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setConfirming(false);
    }
    function onKey(e) {
      if (e.key === "Escape") setConfirming(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [confirming]);

  async function confirmRemove() {
    setConfirming(false);
    setRejecting(true);
    try {
      await onRejectAndRemove(videoId);
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

  const ghostBtn = {
    padding: "9px 14px",
    borderRadius: 8,
    border: "1px solid #2a2a2a",
    background: "transparent",
    color: "#999",
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
  };

  return (
    <div ref={wrapRef} style={{ padding: "20px 12px 22px", position: "relative" }}>
      {confirming && (
        // Sits above MotionReviewApp's own zIndex:100 root, which is its
        // stacking context — a lower value here would slide under the stage.
        <div style={{
          position: "absolute",
          bottom: "100%",
          left: 10,
          right: 10,
          marginBottom: 6,
          zIndex: 110,
          padding: 14,
          borderRadius: 12,
          border: "1px solid #2a2a2a",
          background: "rgba(20,20,20,0.98)",
          backdropFilter: "blur(8px)",
          boxShadow: "0 8px 32px rgba(0,0,0,0.55)",
          textAlign: "left",
        }}>
          <div style={{ color: "#e5e5e5", fontSize: 12.5, fontWeight: 600, lineHeight: 1.45 }}>
            Remove this video from the queue? This deletes the working copy but
            never your original.
          </div>
          <div style={{ marginTop: 7, color: "#7a7a7a", fontSize: 11, lineHeight: 1.45 }}>
            {owned
              ? `Frees about ${formatBytes(sourceSizeBytes)} — the copy this app made when you uploaded it.`
              : "No working copy to delete — this entry only references a file on your Mac, which stays put."}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button onClick={() => setConfirming(false)} style={{ ...ghostBtn, flex: 1 }}>
              Cancel
            </button>
            <button
              onClick={confirmRemove}
              style={{
                ...ghostBtn,
                flex: 1,
                border: "1px solid rgba(248,113,113,0.4)",
                background: "rgba(248,113,113,0.08)",
                color: "#f87171",
                fontWeight: 700,
              }}
            >
              Remove
            </button>
          </div>
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "center", gap: 16 }}>
        {/* REJECT — record the verdict, then drop the row and free our copy */}
        <button
          onClick={() => setConfirming((v) => !v)}
          onMouseDown={() => setPressed("reject")}
          onMouseUp={() => setPressed(null)}
          onMouseLeave={() => setPressed(null)}
          disabled={busy}
          title="Reject — record the verdict and remove this video from the queue"
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
            outline: isReject || confirming ? "3px solid #fecaca" : "none",
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
