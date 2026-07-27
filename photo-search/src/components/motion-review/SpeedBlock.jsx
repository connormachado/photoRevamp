import { useState } from "react";
import {
  SPEED_MAX_MAGNITUDE, SPEED_MIN_MAGNITUDE, SPEED_STEP, clampMagnitude,
} from "./boundaryTypes";

/**
 * The interactive chrome for a "speed" region: a typeable magnitude, a
 * rabbit/turtle direction toggle, and −/+ step buttons flanking it.
 *
 *     [ 2.0 ] [−] [🐇] [+]
 *
 * Rendered through the registry's `renderOverlay` slot rather than
 * `renderBlock`, because block content lives inside CutTimeline's rounded
 * `overflow: hidden` container and a default speed region is only ~25px wide on
 * screen — clipped to the block, none of this would be reachable. The overlay
 * layer is unclipped, so the cluster centres on the region and is allowed to
 * spill past its edges.
 *
 * DIRECTION IS NOT A SIGN. The magnitude is always an unsigned number; whether
 * it means faster or slower comes only from the animal. So the step buttons can
 * never flip direction — they just walk the number by 0.5, clamped at 1.0 (a
 * no-op in either direction) so you tap the turtle to go slower instead of
 * driving the number below 1.
 */
export default function SpeedBlock({ region, ctx }) {
  const { pct, onParamsChange, onSelectRegion, selected, barHeight = 44 } = ctx;
  const params = region.params || {};
  const magnitude = clampMagnitude(params.magnitude);
  const isUp = params.direction !== "down";

  // The input is a controlled *string* so half-typed values ("2." , "") survive
  // a keystroke; the committed number is only ever the clamped parse.
  //
  // Re-syncing the draft is done during render rather than in an effect (React's
  // "adjusting state when a prop changes" pattern) — an effect here would be a
  // cascading render, which the lint rules correctly reject. The second guard
  // means only a magnitude the draft did NOT produce snaps the box: pressing +
  // or hitting the clamp rewrites it, typing "2." does not.
  const [draft, setDraft] = useState(String(magnitude));
  const [seenMagnitude, setSeenMagnitude] = useState(magnitude);
  if (magnitude !== seenMagnitude) {
    setSeenMagnitude(magnitude);
    if (parseFloat(draft) !== magnitude) setDraft(String(magnitude));
  }

  const setParams = (patch) => onParamsChange && onParamsChange(region.id, patch);
  const stop = (e) => e.stopPropagation();

  // 75% of the timeline bar's height, per spec.
  const numberHeight = Math.round(barHeight * 0.75);

  function commitDraft(text) {
    const n = parseFloat(text);
    if (Number.isFinite(n)) setParams({ magnitude: clampMagnitude(n) });
    else setDraft(String(magnitude));         // unparseable — snap back
  }

  const stepBtn = (label, delta, title) => (
    <button
      title={title}
      onMouseDown={stop}
      onClick={(e) => {
        stop(e);
        setParams({ magnitude: clampMagnitude(magnitude + delta) });
      }}
      style={{
        width: 20,
        height: numberHeight,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 0,
        borderRadius: 5,
        border: "1px solid #4ade8055",
        background: "rgba(10,31,28,0.85)",
        color: "#bbf7d0",
        fontSize: 13,
        fontWeight: 700,
        lineHeight: 1,
        cursor: "pointer",
      }}
    >
      {label}
    </button>
  );

  return (
    <div
      onMouseDown={(e) => {
        stop(e);
        onSelectRegion && onSelectRegion(region.id);
      }}
      style={{
        position: "absolute",
        top: "50%",
        left: pct((region.start + region.end) / 2),
        transform: "translate(-50%, -50%)",
        display: "flex",
        alignItems: "center",
        gap: 3,
        padding: "0 3px",
        borderRadius: 7,
        background: "rgba(10,31,28,0.72)",
        border: `1px solid ${selected ? "#bbf7d0" : "#4ade8066"}`,
        whiteSpace: "nowrap",
        zIndex: 3,
      }}
    >
      <input
        value={draft}
        onMouseDown={stop}
        onChange={(e) => {
          setDraft(e.target.value);
          commitDraft(e.target.value);
        }}
        onBlur={() => commitDraft(draft)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { commitDraft(draft); e.currentTarget.blur(); }
        }}
        title={`Magnitude (${SPEED_MIN_MAGNITUDE}–${SPEED_MAX_MAGNITUDE}) — the animal decides faster or slower`}
        style={{
          width: 42,
          height: numberHeight,
          boxSizing: "border-box",
          textAlign: "center",
          borderRadius: 5,
          border: "1px solid #4ade8055",
          background: "rgba(10,31,28,0.9)",
          color: "#4ade80",
          fontFamily: "monospace",
          fontSize: 13,
          fontWeight: 700,
          padding: "0 2px",
        }}
      />
      {stepBtn("−", -SPEED_STEP, `−${SPEED_STEP} (floor ${SPEED_MIN_MAGNITUDE}×)`)}
      <button
        title={isUp ? "Speeding up — tap for slow motion" : "Slowing down — tap to speed up"}
        onMouseDown={stop}
        onClick={(e) => {
          stop(e);
          setParams({ direction: isUp ? "down" : "up" });
        }}
        style={{
          width: 26,
          height: numberHeight,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 0,
          borderRadius: 5,
          border: "1px solid #4ade8055",
          background: "rgba(74,222,128,0.16)",
          fontSize: 14,
          lineHeight: 1,
          cursor: "pointer",
        }}
      >
        {isUp ? "🐇" : "🐢"}
      </button>
      {stepBtn("+", SPEED_STEP, `+${SPEED_STEP} (cap ${SPEED_MAX_MAGNITUDE}×)`)}
    </div>
  );
}
