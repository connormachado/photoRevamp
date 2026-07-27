// Edit-boundary registry — display half.
//
// ONE declarative place where each kind of timeline edit is defined for the UI:
// id, label, icon, colour, default params, and how it renders on the timeline.
// A type has two optional render slots: `renderBlock(region, ctx)` draws inside
// the timeline's clipped, rounded track, and `renderOverlay(region, ctx)` draws
// in the unclipped layer above it — that is where interactive chrome belongs,
// since a region is often only a few pixels wide.
// `toPieces(region)` is the display-side mirror of the backend's
// `apply_on_export`: the source spans this region becomes in the finished video,
// each optionally carrying a `speed`. It is what lets the preview panels play
// what the export will actually produce — see regions.buildPlan. Keep it in
// agreement with the backend hook or the preview and the render will disagree.
// Adding a new edit type later = add one entry here (plus its export hook in the
// backend registry) and nothing else — CutTimeline, the toolbar and ReviewStage
// all read from this map and never branch on a specific type.
//
// Mirror file
// -----------
// The export half lives in `backend/edit_boundaries.py` — default params and the
// apply-on-export hook that decides what ffmpeg actually does with the span. The
// two files are keyed by the same TYPE ID STRINGS; that id is the contract
// between them. Keep `defaultParams` in sync across both.
//
// The data model
// --------------
// A *region* is the first-class entity, in seconds:
//
//     { id: "r-8f2a", type: "cut", start: 5.25, end: 9.0, params: {} }
//
// Regions are sorted by start and never overlap. Time NOT covered by a region is
// untouched footage. See ./regions.js for the helpers.

import { createElement } from "react";
import SpeedBlock from "./SpeedBlock";

const fmt = (s) => {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1).padStart(4, "0");
  return `${m}:${sec}`;
};

// ── speed helpers ────────────────────────────────────────────────────────────
// Mirrors _effective_speed / SPEED_* in backend/edit_boundaries.py. Magnitude is
// always unsigned; the direction toggle is what makes it faster or slower, so
// stepping down floors at 1.0 (a no-op) rather than crossing into "slower".
export const SPEED_MIN_MAGNITUDE = 1;
export const SPEED_MAX_MAGNITUDE = 20;
export const SPEED_STEP = 0.5;

export function clampMagnitude(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return 2;
  return Math.min(SPEED_MAX_MAGNITUDE, Math.max(SPEED_MIN_MAGNITUDE, v));
}

export function effectiveSpeed(params) {
  const mag = clampMagnitude((params || {}).magnitude);
  return (params || {}).direction === "down" ? 1 / mag : mag;
}

// "2×" / "2.5×" — trailing .0 trimmed so the common case reads clean.
const fmtMag = (n) => `${Number(n.toFixed(2))}×`;

export const BOUNDARY_TYPES = {
  cut: {
    id: "cut",
    label: "Cut",
    icon: "✂",
    color: "#f87171",                    // drag handles, footer text, chip accent
    fill: "rgba(248,113,113,0.35)",      // the block on the timeline
    glyphColor: "#fecaca",
    defaultParams: {},
    minWidthFrames: 2,                   // a region can't get thinner than this
    defaultLengthSeconds: 1.5,           // width of a freshly added region
    glyph: () => "✂",
    describe: (r) => `cut ${fmt(r.start)} → ${fmt(r.end)}`,
    // Seconds this region contributes to the exported video — mirrors the
    // backend hook (cut drops its span, so nothing).
    outputDuration: () => 0,
    // Mirrors _cut_apply: the span is dropped, so it becomes no pieces at all.
    toPieces: () => [],
    removesFootage: true,
    // Optional `renderBlock(region, ctx)` escape hatch: omitted here, so cut uses
    // CutTimeline's default block driven by fill/glyph/glyphColor. Types that
    // need custom chrome (a gradient) override it. See `renderOverlay` on
    // "speed" for chrome that has to escape the block's clip instead.
  },
  speed: {
    id: "speed",
    label: "Speed",
    icon: "⏩",
    color: "#4ade80",                    // green — the counterpart to cut's red
    fill: "rgba(74,222,128,0.30)",
    glyphColor: "#bbf7d0",
    defaultParams: { direction: "up", magnitude: 2 },
    minWidthFrames: 2,
    defaultLengthSeconds: 3,             // wider than a cut so the cluster has a perch
    // The overlay carries the readout, so the in-bar block is just the green
    // fill + selection ring from CutTimeline's default path.
    glyph: () => "",
    describe: (r) => `speed ${r.params?.direction === "down" ? "🐢" : "🐇"} `
      + `${fmtMag(clampMagnitude(r.params?.magnitude))} ${fmt(r.start)} → ${fmt(r.end)}`,
    // Mirrors _speed_output_duration: the span survives, retimed.
    outputDuration: (r) => (r.end - r.start) / effectiveSpeed(r.params),
    // Mirrors _speed_apply: the span survives whole as one retimed piece.
    toPieces: (r) => [{ start: r.start, end: r.end, speed: effectiveSpeed(r.params) }],
    removesFootage: false,
    // `renderOverlay(region, ctx)` — the second registry render slot. Unlike
    // renderBlock it is drawn in CutTimeline's UNCLIPPED layer, so interactive
    // chrome stays reachable on a region only a few pixels wide. createElement
    // rather than JSX because this file is .js and Vite's esbuild does not
    // transform JSX outside .jsx.
    renderOverlay: (region, ctx) =>
      createElement(SpeedBlock, { key: region.id, region, ctx }),
  },
  // Next entry goes here. Nothing else needs to change.
};

export const TYPE_LIST = Object.values(BOUNDARY_TYPES);
export const DEFAULT_TYPE_ID = "cut";

// Unknown ids fall back to the default so a stale/foreign region still renders
// instead of blowing up the timeline.
export function getType(id) {
  return BOUNDARY_TYPES[id] || BOUNDARY_TYPES[DEFAULT_TYPE_ID];
}
