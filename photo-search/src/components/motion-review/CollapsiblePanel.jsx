import { useState } from "react";

const ACCENT = "#2dd4bf";
const DURATION = "240ms cubic-bezier(0.4,0,0.2,1)";
const TAB_SIZE = 16; // how far the curved tab bulges out from the panel's own border

// Chevron base art points right; the tab curve bulges toward the panel's
// inner edge (the side facing screen center). Both are built for dock="left"
// and mirrored horizontally for dock="right" via CSS transform, so there is
// only ever one path to maintain.
function Chevron({ flipped }) {
  return (
    <svg
      width={9}
      height={9}
      viewBox="0 0 24 24"
      fill="none"
      stroke={ACCENT}
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{
        position: "relative",
        zIndex: 1,
        transform: flipped ? "rotate(180deg)" : "none",
        transition: `transform ${DURATION}`,
      }}
    >
      <polyline points="9 5 16 12 9 19" />
    </svg>
  );
}

function PullTab({ dock, open, width, hover, onMouseEnter, onMouseLeave, onClick, label }) {
  const mirror = dock === "right";
  // Chevron base points right. Open (panel out) reads as "pointing into the
  // panel" — left for dock="left" — and collapsed reads as "pointing away,
  // out into the content" — right for dock="left". (dock === "left") === open
  // covers all four cases.
  const flipped = (dock === "left") === open;
  // The outer wrapper's own width is shrink-to-fit around the animating
  // content div, so only ONE of the wrapper's two edges is stable (the one
  // pinned by layout — the panel's OUTER edge); the other moves as the
  // content collapses. Anchoring the tab to the moving edge with a fixed
  // CSS offset (e.g. right:0 + translateX for dock="left") put half the tab
  // in negative space once that edge reached 0, clipping it and the chevron
  // off the edge of the screen. Anchoring to the STABLE edge with an offset
  // computed from `open` avoids that, and sitting FLUSH (not straddled) at
  // that edge in both states — rather than centering the tab ON the boundary
  // — is what lets the tab's own flat edge coincide with the panel's real
  // border line instead of sitting a few px inside or outside it.
  const offset = open ? width : 0;
  return (
    <button
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      title={label}
      aria-label={label}
      style={{
        position: "absolute",
        top: "50%",
        [dock === "left" ? "left" : "right"]: offset,
        transform: "translateY(-50%)",
        transition: `${dock === "left" ? "left" : "right"} ${DURATION}`,
        width: TAB_SIZE,
        height: 56,
        padding: 0,
        border: "none",
        background: "none",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1,
      }}
    >
      <svg
        width={TAB_SIZE}
        height={56}
        viewBox={`0 0 ${TAB_SIZE} 56`}
        style={{
          position: "absolute",
          inset: 0,
          zIndex: 0,
          transform: mirror ? "scaleX(-1)" : "none",
        }}
      >
        {/* Two paths sharing one curve, not one closed+stroked shape: the tab
            sits flush against the panel's own border line (see `offset`
            above), so a stroke along the CLOSING straight edge (x=0, top to
            bottom) would draw a second vertical line right next to the
            panel's real border — a visible seam. The fill (closed, no
            stroke) still covers that edge for background-color matching;
            only the curved arc (open path, no "Z") gets an outline, so the
            curve appears to grow directly out of the panel's border instead
            of out of a redundant line of its own. A quadratic curve's actual
            peak is only HALF its control point's offset from the flat edge,
            so the control point is put at 2×TAB_SIZE to make the curve
            itself reach TAB_SIZE — using TAB_SIZE as the control point (the
            visually obvious choice) only pulls the curve out to TAB_SIZE/2,
            leaving the chevron, centered on the full button width, with its
            point outside the fill. */}
        <path
          d={`M0,0 Q${TAB_SIZE * 2},28 0,56 Z`}
          fill={hover ? "rgba(45,212,191,0.16)" : "#0a2e29"}
          style={{ transition: "fill 0.12s" }}
        />
        <path
          d={`M0,0 Q${TAB_SIZE * 2},28 0,56`}
          fill="none"
          stroke={`${ACCENT}55`}
          strokeWidth="1"
        />
      </svg>
      <Chevron flipped={flipped} />
    </button>
  );
}

/**
 * Reusable collapsible side panel — a dock-agnostic width-collapse with a
 * curved pull-tab sitting flush against the panel's own border, bulging out
 * into the neighboring content. Collapsing shrinks the panel to ~0 width (the
 * tab keeps peeking, since it's anchored to a non-animating outer wrapper,
 * not the collapsing content). No persistence: the app has no UI-prefs
 * mechanism anywhere else, so this is plain state.
 */
export default function CollapsiblePanel({ dock, children, defaultOpen = true, width = 280, onToggle }) {
  const [open, setOpen] = useState(defaultOpen);
  const [hover, setHover] = useState(false);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    onToggle?.(next);
  };

  return (
    <div style={{ position: "relative", flexShrink: 0, height: "100%" }}>
      <div
        style={{
          width: open ? width : 0,
          height: "100%",
          overflow: "hidden",
          transition: `width ${DURATION}`,
          display: "flex",
          justifyContent: dock === "right" ? "flex-end" : "flex-start",
        }}
      >
        <div style={{ width, height: "100%", flexShrink: 0 }}>{children}</div>
      </div>
      <PullTab
        dock={dock}
        open={open}
        width={width}
        hover={hover}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onClick={toggle}
        label={open ? "Collapse panel" : "Expand panel"}
      />
    </div>
  );
}
