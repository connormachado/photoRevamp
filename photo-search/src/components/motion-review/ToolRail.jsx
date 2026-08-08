import { useState } from "react";

const ACCENT = "#2dd4bf";
const BTN = 52; // matches HeaderSaveButton's 52×52 chrome
const GAP = 10;

// 2 columns of 52px buttons + 1 gap + comfortable side padding — deliberately
// NOT CollapsiblePanel's 280px default, which is sized for the queue list.
export const TOOL_RAIL_WIDTH = BTN * 2 + GAP + 28;

function ToolButton({ icon, label, onClick, disabled, busy }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={disabled && !busy ? `${label} — coming soon` : label}
      style={{
        width: BTN,
        height: BTN,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 10,
        border: `1px solid ${ACCENT}55`,
        background: disabled
          ? "rgba(255,255,255,0.03)"
          : hover ? "rgba(45,212,191,0.16)" : "rgba(45,212,191,0.08)",
        color: disabled ? "#3f6f66" : ACCENT,
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
            borderTop: `2px solid ${ACCENT}`,
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
}

/**
 * The right tool rail: a 2×2 grid of buttons matching HeaderSaveButton's
 * chrome. Rotate/Crop/Filters are permanently disabled stubs with clean hooks
 * for future prompts — no handlers, no backend calls. Analyze Motion is the
 * one working action: it re-runs dead-time detection for the current video
 * on demand and repopulates the timeline with fresh suggested cuts.
 */
export default function ToolRail({ onAnalyzeMotion, analyzing, analyzeError, disabled }) {
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
    </div>
  );
}
