import { useState, useRef, useEffect, forwardRef } from "react";

const ACCENT = "#2dd4bf";
const BTN = 52; // matches HeaderSaveButton's 52×52 chrome
const GAP = 10;

// 2 columns of 52px buttons + 1 gap + comfortable side padding — deliberately
// NOT CollapsiblePanel's 280px default, which is sized for the queue list.
export const TOOL_RAIL_WIDTH = BTN * 2 + GAP + 28;

// `tint` defaults to the rail's teal ACCENT; Clear-all passes a red tint so
// it keeps reading as destructive-ish despite matching the grid's chrome.
const ToolButton = forwardRef(function ToolButton(
  { icon, label, onClick, disabled, busy, tint = ACCENT, title }, ref
) {
  const [hover, setHover] = useState(false);
  return (
    <button
      ref={ref}
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={title || (disabled && !busy ? `${label} — coming soon` : label)}
      style={{
        width: BTN,
        height: BTN,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 10,
        border: `1px solid ${tint}55`,
        background: disabled
          ? "rgba(255,255,255,0.03)"
          : hover ? `${tint}29` : `${tint}14`,
        color: disabled ? "#3f6f66" : tint,
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.45 : 1,
        transition: "background 0.12s",
        fontSize: 20,
      }}
    >
      {busy ? (
        <div
          style={{
            width: 16,
            height: 16,
            borderRadius: "50%",
            border: "2px solid #ffffff33",
            borderTop: `2px solid ${tint}`,
            // @keyframes spin is defined globally in App.jsx, which always
            // mounts before MotionReviewApp — no local keyframe needed.
            animation: "spin 0.8s linear infinite",
          }}
        />
      ) : (
        icon
      )}
    </button>
  );
});

function ClearAllControl({ regionCount, onClearAll }) {
  const [confirming, setConfirming] = useState(false);
  const [anchor, setAnchor] = useState(null);
  const wrapRef = useRef(null);
  const btnRef = useRef(null);

  // Same Esc + outside-click pattern as VerdictButtons/BulkAddPad.
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

  const empty = regionCount === 0;

  const ghostBtn = {
    padding: "5px 10px",
    borderRadius: 6,
    border: "1px solid #2a2a2a",
    background: "transparent",
    color: "#999",
    fontSize: 11,
    fontWeight: 600,
    cursor: "pointer",
  };

  // ToolRail's CollapsiblePanel wrapper clips both axes to animate its width
  // (see CollapsiblePanel.jsx), and the rail itself is narrower than a
  // comfortably-readable popover. `position: fixed`, anchored off the
  // button's own rect, escapes that clip (no ancestor here sets a
  // transform/filter, so fixed still resolves against the viewport) while
  // staying a real DOM descendant of wrapRef for the outside-click check.
  const openConfirm = () => {
    const rect = btnRef.current.getBoundingClientRect();
    setAnchor({
      right: window.innerWidth - rect.right,
      bottom: window.innerHeight - rect.top + 6,
    });
    setConfirming(true);
  };

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      {confirming && anchor && (
        <div style={{
          position: "fixed",
          right: anchor.right,
          bottom: anchor.bottom,
          width: 200,
          zIndex: 110,
          padding: 12,
          borderRadius: 10,
          border: "1px solid #2a2a2a",
          background: "rgba(20,20,20,0.98)",
          backdropFilter: "blur(8px)",
          boxShadow: "0 8px 32px rgba(0,0,0,0.55)",
          textAlign: "left",
        }}>
          <div style={{ color: "#e5e5e5", fontSize: 11.5, fontWeight: 600, lineHeight: 1.4 }}>
            Remove all {regionCount} boundar{regionCount === 1 ? "y" : "ies"} from this clip?
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
            <button onClick={() => setConfirming(false)} style={{ ...ghostBtn, flex: 1 }}>
              Cancel
            </button>
            <button
              onClick={() => { setConfirming(false); onClearAll(); }}
              style={{
                ...ghostBtn,
                flex: 1,
                border: "1px solid rgba(248,113,113,0.4)",
                background: "rgba(248,113,113,0.08)",
                color: "#f87171",
                fontWeight: 700,
              }}
            >
              Clear
            </button>
          </div>
        </div>
      )}
      <ToolButton
        ref={btnRef}
        icon="🗑"
        label="Clear all boundaries"
        title={empty ? "No boundaries to clear" : "Clear all boundaries"}
        tint="#f87171"
        disabled={empty}
        onClick={() => (confirming ? setConfirming(false) : openConfirm())}
      />
    </div>
  );
}

/**
 * The right tool rail: a 2×2 grid of buttons matching HeaderSaveButton's
 * chrome. Rotate/Crop/Filters are permanently disabled stubs with clean hooks
 * for future prompts — no handlers, no backend calls. Analyze Motion is the
 * one working action: it re-runs dead-time detection for the current video
 * on demand and repopulates the timeline with fresh suggested cuts.
 */
export default function ToolRail({
  onAnalyzeMotion, analyzing, analyzeError, disabled, regionCount = 0, onClearAll,
}) {
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        paddingTop: 16,
        gap: 12,
        borderLeft: `1px solid ${ACCENT}22`,
        background: "#0a2e29",
      }}
    >
      <div style={{ display: "grid", gridTemplateColumns: `repeat(2, ${BTN}px)`, gap: GAP }}>
        <ToolButton icon="↻" label="Rotate" disabled />
        <ToolButton icon="⬚" label="Crop" disabled />
        <ToolButton icon="🎚" label="Filters" disabled />
        <ToolButton
          icon="📈"
          label="Analyze Motion"
          onClick={onAnalyzeMotion}
          disabled={disabled || analyzing}
          busy={analyzing}
        />
      </div>
      {analyzeError && (
        <div style={{ fontSize: 10, color: "#f87171", textAlign: "center", padding: "0 8px" }}>
          {analyzeError}
        </div>
      )}
      <ClearAllControl regionCount={regionCount} onClearAll={onClearAll} />
    </div>
  );
}
